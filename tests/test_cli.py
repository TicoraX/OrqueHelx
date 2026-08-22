"""El CLI headless: correr un DAG sin levantar el Studio.

Hasta ahora el bucle de una corrida vivia adentro de `ui/server.py`, y habia
copias en `mcp_exporter` y en `loop.correr`. Este test corre un grafo de punta a
punta SIN servidor HTTP y sin agentes de verdad: `run_backend` se reemplaza por
una funcion que devuelve el contrato, que es exactamente el borde donde termina
lo nuestro y empieza el CLI ajeno.

Lo que se prueba es lo que CI necesita: que el codigo de salida distinga una
corrida buena de una mala. Un CLI que siempre devuelve 0 no sirve de gate.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_cli.py
"""
import gc, json, sqlite3, sys, tempfile, time, traceback
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
import orquester

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


def _cerrar_todo():
    """Windows no borra una SQLite con una conexion abierta, y `compilar` deja
    la suya viva. Se barren las de este proceso antes de borrar el board."""
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.close()
            except Exception:
                pass


try:
    # --- 1. Una corrida completa termina en 0 y cierra los dos nodos ---------
    dispatcher.run_backend = _agente_falso
    codigo = orquester.main(["run", str(ARCHIVO), "--board", BOARD])
    assert codigo == 0, f"una corrida sana tiene que salir 0, salio {codigo}"
    assert llamadas == ["claude-code", "claude-code"], (
        f"no se ejecutaron los dos nodos: {llamadas}")

    conn = k.connect(board=BOARD)
    estados = {t.title: t.status for t in k.list_tasks(conn)}
    assert set(estados.values()) == {"done"}, estados
    # Y el orden lo puso el kanban, no el CLI: B espera a A.
    assert len(estados) == 2, estados
    conn.close()
    print("1. `orquester run` compila y ejecuta el grafo entero, salida 0: OK")

    # --- 2. Un nodo que falla NO devuelve 0 ---------------------------------
    # Es lo unico que hace util al CLI dentro de un workflow: si un DAG que
    # dejo cards bloqueadas saliera 0, el gate de CI pasaria en verde con el
    # flujo roto.
    llamadas.clear()
    dispatcher.run_backend = _agente_roto
    board2 = BOARD + "-roto"
    codigo = orquester.main(["run", str(ARCHIVO), "--board", board2])
    assert codigo == 1, f"un nodo bloqueado tiene que salir 1, salio {codigo}"
    conn = k.connect(board=board2)
    assert any(t.status == "blocked" for t in k.list_tasks(conn)), \
        "el nodo roto tendria que haber quedado bloqueado"
    conn.close()
    print("2. un nodo que falla deja la salida en 1: OK")

    # --- 3. El tope de gasto tiene que ser > 0 -------------------------------
    # `--tope 0` es un tope que no se puede respetar: cualquier gasto ya lo
    # supera. Se rechaza en el parseo, no a mitad de corrida.
    try:
        orquester.main(["run", str(ARCHIVO), "--tope", "0"])
        raise AssertionError("acepto un tope de 0")
    except SystemExit as e:
        assert e.code == 2, f"esperaba error de argparse, salio {e.code}"
    print("3. `--tope 0` se rechaza antes de compilar nada: OK")

    # --- 4. El nombre del board no puede salir de la carpeta -----------------
    # El board termina en el `mkdir(parents=True)` de `k.connect`. El Studio ya
    # filtra esto (`_ruta_segura`); el CLI recibe el nombre del .json, que no
    # siempre lo escribio quien corre el comando. Es la misma proteccion, en el
    # otro lado de la puerta.
    for malo in ("../fuera", "sub/dir", ".."):
        try:
            orquester.main(["run", str(ARCHIVO), "--board", malo])
            raise AssertionError(f"acepto un board invalido: {malo!r}")
        except SystemExit as e:
            assert "board invalido" in str(e.code), f"{malo!r} -> {e.code!r}"
    print("4. un board con '..' o '/' se rechaza: OK")

    # --- 5. El tope de gasto corta la corrida, y eso tampoco sale 0 ---------
    # El docstring del CLI promete salir 1 tambien "si la corrida se corto por
    # tope o timeout", y eso no estaba probado. Con el agente falso gastando
    # 0.01 por nodo, un tope de 0.005 se pasa en el primer nodo.
    llamadas.clear()
    dispatcher.run_backend = _agente_falso
    board3 = BOARD + "-tope"
    codigo = orquester.main(["run", str(ARCHIVO), "--board", board3, "--tope", "0.005"])
    assert codigo == 1, f"cortar por tope no puede salir 0, salio {codigo}"
    assert len(llamadas) == 1, (
        f"el tope tenia que frenar despues del primer nodo, corrieron {llamadas}")
    print("5. el tope de gasto corta la corrida y la salida es 1: OK")

    # --- 6. El slug del board dice lo mismo que el kanban -------------------
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

    print("\nOK: el CLI corre un DAG sin Studio y su codigo de salida informa.")
finally:
    _cerrar_todo()
    for slug in (BOARD, BOARD + "-roto", BOARD + "-tope"):
        try:
            k.remove_board(slug, archive=False)
        except Exception:
            traceback.print_exc()
