"""El CLI headless: correr un DAG sin levantar el Studio.

Los cuatro comandos de inspeccion (`doctor`, `plantillas`, `skills`, `validar`)
se prueban llamandolos; `run` --que es para lo que existe el CLI-- se prueba
corriendo un grafo de punta a punta con `run_backend` reemplazado por una
funcion, que es el borde exacto donde termina lo nuestro y empieza el CLI ajeno.

Lo del `run` no es de adorno. Una version anterior de este archivo probaba solo
los cuatro comandos de inspeccion, y con la suite en verde el CLI no arrancaba:
`cmd_run` llamaba a `corrida.correr` sin haber importado `corrida`, o sea un
NameError en cada invocacion. Un test que no toca el camino principal deja
pasar que el camino principal no exista.

Lo que se prueba es lo que CI necesita: que el codigo de salida distinga una
corrida buena de una mala. Un CLI que siempre devuelve 0 no sirve de gate.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_cli.py
"""
import argparse, gc, json, sqlite3, sys, tempfile, time, traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "dispatcher", "compiler"):
    sys.path.insert(0, str(RAIZ / sub))
sys.path.insert(0, str(RAIZ))

import hermes_cli.kanban_db as k
import compile as compilador
import loop as dispatcher
import corrida
from backends import ErrorPermanente

# Por ruta y no por `import orquester`: este proceso ya tiene `hermes-agent` en
# el path, y asi el test no depende del orden de los imports para agarrar el
# archivo que quiere. Es la misma colision que le costo el nombre a `cli.py`.
import importlib.util
_spec = importlib.util.spec_from_file_location("orquester_cli", RAIZ / "orquester.py")
orq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orq)

BOARD = f"cli-test-{int(time.time() * 1000) % 10_000_000}"
GRAFO = {
    "board": BOARD,
    "nodos": [{"id": "a", "titulo": "Nodo A", "runtime": "claude-code"},
              {"id": "b", "titulo": "Nodo B", "runtime": "claude-code"}],
    "aristas": [["a", "b"]],
}
ARCHIVO = Path(tempfile.mkdtemp()) / "grafo.json"
ARCHIVO.write_text(json.dumps(GRAFO), encoding="utf-8")

# El preflight de capacidades mira ESTA maquina, y el test no puede depender de
# que haya un `claude` instalado: lo que se prueba es el CLI, no la deteccion.
compilador.faltantes = lambda runtimes: []

llamadas = []


def _agente_falso(runtime, ctx, **kw):
    llamadas.append(runtime)
    return {"status": "success", "summary": f"hecho por {runtime}",
            "uso": {"costo_usd": 0.01}}


def _agente_roto(runtime, ctx, **kw):
    raise ErrorPermanente("el CLI no esta instalado")


# Un grafo con un nodo de aprobacion humana en el medio.
CON_GATE = {
    "board": BOARD + "-gate",
    "nodos": [{"id": "a", "titulo": "Trabaja", "runtime": "claude-code"},
              {"id": "g", "titulo": "Aprobacion humana", "tipo": "gate"},
              {"id": "b", "titulo": "Despues del gate", "runtime": "claude-code"}],
    "aristas": [["a", "g"], ["g", "b"]],
}
ARCHIVO_GATE = Path(tempfile.mkdtemp()) / "con-gate.json"
ARCHIVO_GATE.write_text(json.dumps(CON_GATE), encoding="utf-8")


def _correr(board, presupuesto=None, salida_json=False, grafo=None,
            esperar_gates=None, aprobar_gates=False):
    return orq.cmd_run(argparse.Namespace(
        grafo=str(grafo or ARCHIVO), board=board, workspace=None,
        ignorar_capacidades=True, presupuesto=presupuesto, json=salida_json,
        esperar_gates=esperar_gates, aprobar_gates=aprobar_gates))


def _cerrar_todo():
    """Windows no borra una SQLite con una conexion abierta, y `compilar` deja
    la suya viva. Se barren las de este proceso antes de borrar el board."""
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.close()
            except Exception:
                pass


boards = [BOARD, BOARD + "-roto", BOARD + "-tope",
          BOARD + "-gate", BOARD + "-gate-auto"]
try:
    # --- 1. Los comandos de inspeccion contestan ----------------------------
    for nombre, fn in (("doctor", orq.cmd_doctor), ("plantillas", orq.cmd_plantillas),
                       ("skills", orq.cmd_skills)):
        assert fn(argparse.Namespace()) == 0, f"cmd_{nombre} no devolvio 0"

    _pl = str(RAIZ / "plantillas" / "documentar-cambios.json")
    assert orq.cmd_validar(argparse.Namespace(grafo=_pl, ignorar_capacidades=True)) == 0
    assert orq.cmd_validar(argparse.Namespace(grafo="no-existe-123.json",
                                              ignorar_capacidades=True)) == 1, \
        "validar un archivo inexistente tiene que fallar"
    print("1. doctor, plantillas, skills y validar contestan: OK")

    # --- 2. Una corrida completa termina en 0 y cierra los dos nodos --------
    dispatcher.run_backend = _agente_falso
    codigo = _correr(BOARD)
    assert codigo == 0, f"una corrida sana tiene que salir 0, salio {codigo}"
    assert llamadas == ["claude-code", "claude-code"], (
        f"no se ejecutaron los dos nodos: {llamadas}")

    conn = k.connect(board=BOARD)
    estados = {t.title: t.status for t in k.list_tasks(conn)}
    assert set(estados.values()) == {"done"} and len(estados) == 2, estados
    conn.close()
    print("2. `run` compila y ejecuta el grafo entero, salida 0: OK")

    # --- 3. Un nodo que falla NO devuelve 0 --------------------------------
    # Es lo unico que hace util al CLI dentro de un workflow: si un DAG que
    # dejo cards bloqueadas saliera 0, el gate de CI pasaria en verde con el
    # flujo roto.
    llamadas.clear()
    dispatcher.run_backend = _agente_roto
    codigo = _correr(BOARD + "-roto")
    assert codigo == 1, f"un nodo bloqueado tiene que salir 1, salio {codigo}"
    conn = k.connect(board=BOARD + "-roto")
    assert any(t.status == "blocked" for t in k.list_tasks(conn)), \
        "el nodo roto tendria que haber quedado bloqueado"
    conn.close()
    print("3. un nodo que falla deja la salida en 1: OK")

    # --- 4. Cortar por tope tampoco es exito -------------------------------
    # Los nodos que no llegaron a arrancar quedan en `ready`: ni completados ni
    # fallidos. Con la regla "ok = ninguno fallo", una corrida truncada a la
    # mitad por presupuesto devolvia 0 y CI la daba por buena.
    llamadas.clear()
    dispatcher.run_backend = _agente_falso
    codigo = _correr(BOARD + "-tope", presupuesto="0.005")
    assert codigo == 1, f"cortar por tope no puede salir 0, salio {codigo}"
    assert len(llamadas) == 1, (
        f"el tope tenia que frenar despues del primer nodo, corrieron {llamadas}")
    print("4. el tope de gasto corta la corrida y la salida es 1: OK")

    # --- 5. El nombre del board pasa por la regla del kanban ---------------
    for malo in ("mi board", "../fuera", "sub/dir"):
        assert _correr(malo) == 1, f"acepto un board invalido: {malo!r}"
    print("5. un board con espacios, '..' o '/' se rechaza con codigo 1: OK")

    # --- 6. El slug dice lo mismo que el kanban ----------------------------
    # `corrida.slug` copia la regla de `kanban_db._normalize_board_slug`, que es
    # privada del clon pineado. Si un bump del pin la cambia, tiene que fallar
    # aca y no en produccion.
    for nombre in ("Mi-Board", "mi-board", "board_1", "b"):
        assert corrida.slug(nombre) == k._normalize_board_slug(nombre), nombre
    for malo in ("mi board", "../fuera", "sub/dir", "-empieza-mal", "", "x" * 65):
        nuestro = kanban = None
        try:
            nuestro = corrida.slug(malo)
        except ValueError:
            pass
        try:
            kanban = k._normalize_board_slug(malo)
        except ValueError:
            pass
        assert nuestro == kanban, (
            f"{malo!r}: nosotros {nuestro!r}, el kanban {kanban!r}")
    print("6. `corrida.slug` y el kanban aceptan y rechazan lo mismo: OK")

    # --- 7. Un gate pendiente pausa la corrida, no la cuelga ---------------
    # Un nodo de aprobacion queda en `ready` esperando a una persona, y nada
    # dentro del proceso lo va a mover. Antes de esto el bucle giraba para
    # siempre: 28 vueltas en 6 segundos sobre un flujo que no podia avanzar.
    # Ahora corta con codigo 2, que es "pausado", distinto de 1 = "fallo": el
    # board queda intacto y se reanuda desde el Studio.
    llamadas.clear()
    dispatcher.run_backend = _agente_falso
    b_gate = BOARD + "-gate"
    codigo = _correr(b_gate, grafo=ARCHIVO_GATE)
    assert codigo == 2, f"un gate pendiente tiene que salir 2, salio {codigo}"
    assert llamadas == ["claude-code"], (
        f"el nodo de despues del gate no tenia que correr: {llamadas}")

    conn = k.connect(board=b_gate)
    estados = {t.title: t.status for t in k.list_tasks(conn)}
    assert estados["Aprobacion humana"] == "ready", estados
    assert estados["Despues del gate"] == "todo", estados
    conn.close()
    print("7. un gate pendiente pausa la corrida con codigo 2: OK")

    # --- 8. `--aprobar-gates` sigue, y lo deja asentado como automatico -----
    # El flag existe para un pipeline que de verdad no tiene humano. Lo que no
    # puede pasar es que el registro diga que aprobo una persona: una auditoria
    # a seis meses no tiene como distinguirlo, y el pasado no se reescribe.
    llamadas.clear()
    b_auto = BOARD + "-gate-auto"
    codigo = _correr(b_auto, grafo=ARCHIVO_GATE, aprobar_gates=True)
    assert codigo == 0, f"con --aprobar-gates tenia que salir 0, salio {codigo}"
    assert llamadas == ["claude-code", "claude-code"], (
        f"tenian que correr los dos nodos: {llamadas}")

    conn = k.connect(board=b_auto)
    gate = next(t for t in k.list_tasks(conn) if t.title == "Aprobacion humana")
    assert gate.status == "done", gate.status
    runs = k.list_runs(conn, gate.id)
    meta = (runs[-1].metadata or {}) if runs else {}
    assert meta.get("claimer") == "auto" and meta.get("aprobacion") == "automatica", (
        f"el registro no dice que la aprobacion fue automatica: {meta}")
    assert "automaticamente" in (gate.result or ""), gate.result
    conn.close()
    print("8. `--aprobar-gates` cierra el gate y lo asienta como automatico: OK")

    print("\nOK: el CLI corre un DAG sin Studio y su codigo de salida informa.")
finally:
    _cerrar_todo()
    for slug in boards:
        try:
            k.remove_board(slug, archive=False)
        except Exception:
            traceback.print_exc()
