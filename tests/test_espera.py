"""El nodo de espera: el reloj destraba, no una persona ni un agente.

`scheduled` ya existia en `VALID_STATUSES` y no lo usaba nadie nuestro, y el
kanban trae las dos mitades: `schedule_task` aparca la card fuera del alcance de
cualquier dispatcher, y `unblock_task` la devuelve respetando a los padres. Lo
unico que falta es el CUANDO, que su propio docstring delega en "un cron
externo". Ese cron es `corrida.atender_esperas`.

Se prueba con plazos de menos de un segundo: lo que esta en discusion es el
mecanismo --que la card se duerma, que el hijo no arranque mientras duerme, y
que despierte sola-- y no la precision del reloj del sistema.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_espera.py
"""
import gc, sqlite3, sys, time, traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "dispatcher", "compiler"):
    sys.path.insert(0, str(RAIZ / sub))

import hermes_cli.kanban_db as k
import compile as compilador
import loop as dispatcher
import corrida

compilador.faltantes = lambda runtimes: []

BOARD = f"espera-{int(time.time() * 1000) % 10_000_000}"
corridos = []


def _agente(runtime, ctx, **kw):
    corridos.append(runtime)
    return {"status": "success", "summary": "ok", "uso": {"costo_usd": 0.0}}


def _cerrar_todo():
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.close()
            except Exception:
                pass


boards = [BOARD]
try:
    # --- 1. El compilador y el dispatcher hablan del mismo prefijo ----------
    # El `assignee` es el contrato entre las dos piezas, que a proposito no se
    # importan entre si. Si una cambia el prefijo, las esperas dejan de
    # dormirse en silencio: el nodo pasaria a `ready` y nadie lo levantaria.
    assert compilador.PREFIJO_ESPERA == corrida.PREFIJO_ESPERA, (
        compilador.PREFIJO_ESPERA, corrida.PREFIJO_ESPERA)
    print("1. compilador y dispatcher usan el mismo prefijo de espera: OK")

    # --- 2. El plazo se valida antes de crear nada -------------------------
    # Un plazo invalido tiene que doler al compilar, no dejar una card dormida
    # para siempre en un board que nadie mira.
    for malo in (None, 0, -5, "manana", True, float("inf"),
                 compilador.MAX_ESPERA_S + 1):
        g = {"board": "x", "aristas": [], "nodos": [
            {"id": "a", "titulo": "t", "runtime": "claude-code"},
            {"id": "e", "titulo": "espera", "tipo": "espera",
             "esperar_segundos": malo}]}
        try:
            compilador.validar(g, capacidades=False)
            raise AssertionError(f"acepto un plazo invalido: {malo!r}")
        except compilador.ErrorDeGrafo as e:
            assert "esperar_segundos" in str(e), str(e)
    print("2. un plazo invalido se rechaza al validar el grafo: OK")

    # --- 3. El flujo entero: trabajo -> espera -> trabajo -------------------
    k.create_board(BOARD)
    ids = compilador.compilar({"board": BOARD, "nodos": [
        {"id": "antes", "titulo": "antes", "runtime": "claude-code"},
        {"id": "pausa", "titulo": "pausa", "tipo": "espera", "esperar_segundos": 2},
        {"id": "despues", "titulo": "despues", "runtime": "claude-code"}],
        "aristas": [["antes", "pausa"], ["pausa", "despues"]]}, board=BOARD)

    conn = k.connect(board=BOARD)
    assert k.get_task(conn, ids["pausa"]).assignee == "reloj:2", \
        k.get_task(conn, ids["pausa"]).assignee

    dispatcher.run_backend = _agente
    t0 = time.monotonic()
    fin = corrida.correr(BOARD, espera=0.2)
    tardo = time.monotonic() - t0

    estados = {n: k.get_task(conn, t).status for n, t in ids.items()}
    assert estados == {"antes": "done", "pausa": "done", "despues": "done"}, estados
    assert corridos == ["claude-code", "claude-code"], (
        f"la espera no ejecuta nada, tenian que correr dos nodos: {corridos}")
    assert fin["motivo"] == "sin trabajo", fin
    # El plazo se respeto: sin la espera esto cerraba en menos de un segundo.
    assert tardo >= 2, f"la corrida no espero los 2s del nodo, tardo {tardo:.1f}s"
    print(f"3. antes -> espera(2s) -> despues, en {tardo:.1f}s y sin colgarse: OK")

    # --- 4. Mientras duerme, el hijo NO arranca ----------------------------
    # Es lo unico que hace util al nodo: si el hijo pudiera correr igual, la
    # espera seria decorativa.
    orden = [k.list_events(conn, ids["pausa"]), k.list_events(conn, ids["despues"])]
    tipos_pausa = [e.kind if hasattr(e, "kind") else e.get("kind") for e in orden[0]]
    assert "scheduled" in tipos_pausa, (
        f"la card nunca paso por `scheduled`: {tipos_pausa}")
    print("4. la espera pasa por `scheduled` y el hijo espera a que despierte: OK")

    # --- 5. Una espera dormida cuenta como futuro --------------------------
    # `_hay_futuro` decide si la corrida termino. Una card `scheduled` SI va a
    # avanzar sola --la despierta el reloj-- y contarla como muerta cerraba la
    # corrida a los tres segundos dando el flujo por terminado.
    b2 = BOARD + "-dormida"
    boards.append(b2)
    k.create_board(b2)
    ids2 = compilador.compilar({"board": b2, "aristas": [], "nodos": [
        {"id": "e", "titulo": "duerme", "tipo": "espera",
         "esperar_segundos": 3600}]}, board=b2)
    c2 = k.connect(board=b2)
    corrida.atender_esperas(c2)
    assert k.get_task(c2, ids2["e"]).status == "scheduled"
    assert corrida._hay_futuro(c2) is True, (
        "una espera dormida se contaba como flujo terminado")
    c2.close()
    print("5. una espera dormida mantiene viva la corrida: OK")

    conn.close()
    print("\nOK: el nodo de espera duerme, no ejecuta nada, y despierta solo.")
finally:
    _cerrar_todo()
    for slug in boards:
        try:
            k.remove_board(slug, archive=False)
        except Exception:
            traceback.print_exc()
