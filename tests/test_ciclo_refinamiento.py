"""El ciclo de refinamiento: el linter es el critico y el agente el que corrige.

Un nodo que dice "listo" no prueba nada: lo dice igual si dejo el workspace
roto. Con `--validar`, despues de cada nodo se mide el workspace y, si aparecio
un problema que antes no estaba, la card vuelve a la cola con ese problema como
contexto del reintento.

Se prueba el lazo entero, sin agentes de verdad: un backend falso rompe un
archivo en el primer intento, lee lo que le dice el validador en el segundo, y
lo arregla. Lo que se verifica no es que el nodo termine --eso lo haria igual
sin validador-- sino **que el segundo intento recibio el hallazgo del primero**.
Sin eso, el ciclo es un reintento a ciegas.

    uv run --python 3.11 --with jsonschema --with pyyaml --with pyflakes python tests/test_ciclo_refinamiento.py
"""
import gc, sqlite3, sys, tempfile, time, traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "dispatcher", "compiler"):
    sys.path.insert(0, str(RAIZ / sub))

import hermes_cli.kanban_db as k
import compile as compilador
import loop as dispatcher
import corrida
import validadores

compilador.faltantes = lambda runtimes: []

WS = Path(tempfile.mkdtemp())
# El workspace ya viene con deuda: un import sin usar que pyflakes ve. Si el
# validador comparara contra cero, el primer nodo quedaria bloqueado por algo
# que no escribio.
(WS / "preexistente.py").write_text("import os\n", encoding="utf-8")

BOARD = f"ciclo-{int(time.time() * 1000) % 10_000_000}"
GRAFO = {"board": BOARD, "aristas": [], "nodos": [
    {"id": "a", "titulo": "escribe codigo", "runtime": "claude-code",
     "workspace": str(WS)}]}

contextos = []


def _agente(runtime, ctx, **kw):
    """Rompe la primera vez; la segunda lee lo que le dijeron y arregla."""
    contextos.append(ctx)
    if len(contextos) == 1:
        (WS / "nuevo.py").write_text("def f():\n    return sin_definir\n",
                                     encoding="utf-8")
        return {"status": "success", "summary": "escribi nuevo.py",
                "uso": {"costo_usd": 0.0}}
    (WS / "nuevo.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    return {"status": "success", "summary": "corregido", "uso": {"costo_usd": 0.0}}


def _cerrar_todo():
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.close()
            except Exception:
                pass


boards = [BOARD, BOARD + "-terco"]
try:
    hay, motivo = validadores.disponible("pyflakes")
    assert hay, f"este test necesita pyflakes: {motivo}"

    # --- 1. El validador caza lo que el nodo rompio y lo manda a reintentar --
    k.create_board(BOARD)
    ids = compilador.compilar(GRAFO, board=BOARD)
    dispatcher.run_backend = _agente
    fin = corrida.correr(BOARD, espera=0.1, validador="pyflakes")

    assert len(contextos) == 2, (
        f"el nodo tenia que correr dos veces (romper y corregir), corrio "
        f"{len(contextos)}")
    conn = k.connect(board=BOARD)
    t = k.get_task(conn, ids["a"])
    assert t.status == "done", f"tras corregir tenia que cerrar: {t.status}"
    conn.close()
    assert fin["motivo"] == "sin trabajo", fin
    print("1. el validador bloquea el nodo que rompio y lo deja reintentar: OK")

    # --- 2. Y el reintento SABE que rompio -----------------------------------
    # Esto es el ciclo. Sin esto hay un reintento a ciegas, que es lo que habia.
    segundo = contextos[1]
    assert "sin_definir" in segundo, (
        "el segundo intento no recibio el hallazgo del validador:\n"
        f"{segundo[:400]}")
    assert "REINTENTO" in segundo, "no se le dijo que era un reintento"
    assert segundo.index("sin_definir") < segundo.index(dispatcher._FIN_DATOS), (
        "el hallazgo cayo despues de la marca de fin de datos, o sea del lado "
        "de las instrucciones")
    print("2. el reintento recibe el hallazgo, y como dato: OK")

    # --- 3. Al nodo no se le cobra la deuda que ya estaba --------------------
    assert "preexistente.py" not in segundo, (
        "le paso al agente un problema que el workspace ya tenia:\n" + segundo)
    print("3. no se le cobra al nodo la deuda preexistente del workspace: OK")

    # --- 4. Un nodo que no aprende termina, no gira para siempre -------------
    # El techo es `MAX_INTENTOS`, que ya existia: el ciclo lo hereda en vez de
    # inventar un contador propio. Sin techo, "reintentar con feedback" es un
    # bucle infinito con costo por vuelta.
    WS2 = Path(tempfile.mkdtemp())
    b2 = BOARD + "-terco"
    corridas = []

    def _terco(runtime, ctx, **kw):
        corridas.append(1)
        (WS2 / "roto.py").write_text("def f():\n    return jamas_definido\n",
                                     encoding="utf-8")
        return {"status": "success", "summary": "yo dije que estaba bien",
                "uso": {"costo_usd": 0.0}}

    k.create_board(b2)
    ids2 = compilador.compilar(
        {"board": b2, "aristas": [], "nodos": [
            {"id": "a", "titulo": "no aprende", "runtime": "claude-code",
             "workspace": str(WS2)}]}, board=b2)
    dispatcher.run_backend = _terco
    t0 = time.monotonic()
    fin2 = corrida.correr(b2, espera=0.1, validador="pyflakes")
    tardo = time.monotonic() - t0

    assert len(corridas) == dispatcher.MAX_INTENTOS, (
        f"tenia que intentar {dispatcher.MAX_INTENTOS} veces, intento {len(corridas)}")
    assert fin2["motivo"] == "trabado", f"tenia que cortar, devolvio {fin2}"
    assert tardo < 60, f"tardo {tardo:.0f}s: eso es un bucle, no un ciclo"
    conn = k.connect(board=b2)
    # `triage` tambien vale: Hermes escala solo cuando una card se bloquea una y
    # otra vez (`BLOCK_RECURRENCE_LIMIT`), y ese es el final correcto para algo
    # que ya nadie automatico va a arreglar.
    final = k.get_task(conn, ids2["a"]).status
    assert final in ("blocked", "triage"), final
    conn.close()
    print(f"4. un nodo que no corrige se agota a los {dispatcher.MAX_INTENTOS} "
          f"intentos y la corrida termina: OK")

    # --- 5. Un validador que no existe se dice ANTES de correr nada ---------
    fin3 = corrida.correr(BOARD, espera=0.1, validador="rm -rf /")
    assert fin3["motivo"] == "error" and "desconocido" in fin3.get("error", ""), fin3
    assert fin3["nodos"] == 0, "arranco nodos con un validador invalido"
    print("5. un validador desconocido corta antes de arrancar, sin ejecutarlo: OK")

    print("\nOK: el linter critica, el agente corrige, y el ciclo tiene techo.")
finally:
    _cerrar_todo()
    for slug in boards:
        try:
            k.remove_board(slug, archive=False)
        except Exception:
            traceback.print_exc()
