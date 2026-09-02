"""Terminal en vivo (T1, docs/PLAN-2026-09-01-terminal-en-vivo.md): archivo +
polling en vez de threads lectores (Premisa 2 del plan). Nada de esto invoca
un CLI de agente real -- un proceso Python de control alcanza para probar el
mecanismo de redireccion a archivo.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_terminal_vivo.py
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "compiler", "dispatcher", "ui"):
    sys.path.insert(0, str(RAIZ / sub))
import backends as b
import loop
import hermes_cli.kanban_db as k
import compile as c
import server as srv

TMP = Path(tempfile.mkdtemp())

# --- T1.1: _correr con archivo_terminal escribe progresivo -----------------
ARCHIVO = TMP / "vivo.log"
PROGRESIVO = ("import sys, time\n"
              "for i in range(4):\n"
              "    print(f'linea {i}')\n"
              "    sys.stdout.flush()\n"
              "    time.sleep(0.3)\n")
resultado = {}


def _lanzar():
    resultado["proc"] = b._correr("_prueba", ["python", "-c", PROGRESIVO],
                                  timeout=10, archivo_terminal=ARCHIVO)


hilo = threading.Thread(target=_lanzar)
hilo.start()
time.sleep(0.5)                     # a mitad de la corrida (4 x 0.3s = 1.2s)
parcial = ARCHIVO.read_text(encoding="utf-8")
hilo.join()
assert "linea 0" in parcial and "linea 3" not in parcial, \
    f"a mitad de la corrida deberia tener contenido parcial, no todo: {parcial!r}"
assert resultado["proc"].stdout.count("linea") == 4, resultado["proc"].stdout
print("1. _correr con archivo_terminal escribe progresivo, no todo al final: OK")

# --- T1.1: timeout deja la salida parcial en el mensaje --------------------
LENTO = "import sys, time\nfor i in range(3):\n    print(f'l{i}'); sys.stdout.flush(); time.sleep(1)\n"
try:
    b._correr("_prueba", ["python", "-c", LENTO], timeout=0.5,
              archivo_terminal=TMP / "timeout.log")
    raise SystemExit("FALLA: no exploto el timeout")
except b.BackendError as e:
    assert "parcial" in str(e).lower(), str(e)
print("2. timeout con archivo_terminal deja la salida parcial en el mensaje: OK")

# --- T1.2: run_backend reenvia archivo_terminal a _correr -------------------
CONTRATO_OK = json.dumps({"status": "success", "summary": "ok"})
SCRIPT = (f"import sys, time\n"
          f"print('arrancando'); sys.stdout.flush(); time.sleep(0.2)\n"
          f"print({CONTRATO_OK!r})\n")
b.BACKENDS["_prueba2"] = (lambda g, s: ["python", "-c", SCRIPT], b._primer_objeto)
try:
    archivo = TMP / "run_backend.log"
    salida = b.run_backend("_prueba2", "meta", timeout=10, archivo_terminal=archivo)
    assert salida["summary"] == "ok", salida
    assert "arrancando" in archivo.read_text(encoding="utf-8")
finally:
    del b.BACKENDS["_prueba2"]
print("3. run_backend reenvia archivo_terminal a _correr: OK")

# Sin pasar el parametro, sigue andando igual que antes (compat con run_chat).
b.BACKENDS["_prueba3"] = (lambda g, s: ["python", "-c", SCRIPT], b._primer_objeto)
try:
    salida = b.run_backend("_prueba3", "meta", timeout=10)
    assert salida["summary"] == "ok", salida
finally:
    del b.BACKENDS["_prueba3"]
print("4. sin archivo_terminal (default None), run_backend sigue igual que antes: OK")

# --- T1.2: loop._ruta_terminal arma la ruta y crea la carpeta --------------
dbfile = Path(tempfile.mkdtemp()) / "prueba.db"
k.init_db(db_path=dbfile)
ruta = loop._ruta_terminal(str(dbfile), "t_123")
assert ruta == dbfile.parent / "workspaces" / "t_123" / "terminal.log", ruta
assert ruta.parent.is_dir(), "no creo la carpeta"
print("5. _ruta_terminal arma <db>/workspaces/<task_id>/terminal.log y crea la carpeta: OK")

# --- T1.3: endpoint de polling, con solapamiento y filtro por chunk --------
BOARD = f"term-{int(time.time() * 1000) % 10_000_000}"
g = {"board": BOARD, "aristas": [],
     "nodos": [{"id": "n1", "titulo": "nodo", "runtime": "opencode"}]}
task_id = c.compilar(g, board=BOARD)["n1"]
conn = k.connect(board=BOARD)
db = loop._archivo_de(conn)
ruta_log = Path(db).parent / "workspaces" / task_id / "terminal.log"
ruta_log.parent.mkdir(parents=True, exist_ok=True)

# Sin archivo todavia: texto vacio, no explota.
sin_archivo = srv._terminal_nodo(BOARD, task_id, 0)
assert sin_archivo == {"texto": "", "offset_nuevo": 0}, sin_archivo
print("6. sin archivo de terminal todavia, el poll devuelve vacio sin explotar: OK")

# Una credencial de mentira, larga (>12 chars, el piso de `_valores_sensibles`).
os.environ["FAKE_TEST_API_KEY"] = "sk-super-secreta-de-mentira-larga"
CREDENCIAL = os.environ["FAKE_TEST_API_KEY"]

ruta_log.write_text("arrancando el nodo\n" + "x" * 300, encoding="utf-8")
r1 = srv._terminal_nodo(BOARD, task_id, 0)
assert "arrancando" in r1["texto"], r1
# Retiene los ultimos _SOLAPE_TERMINAL bytes: no llega a mandar el archivo
# entero aunque ya este escrito.
assert r1["offset_nuevo"] < ruta_log.stat().st_size, \
    "no retuvo nada en el borde vivo del archivo"
print("7. el poll retiene los ultimos bytes (borde vivo), no manda el archivo entero: OK")

# La credencial cae EXACTO en el borde entre lo ya mandado (r1) y lo nuevo:
# se escribe de forma que la mitad quede del lado retenido de r1.
with open(ruta_log, "a", encoding="utf-8") as fh:
    fh.write(CREDENCIAL)
    fh.write("y" * 300)               # empuja la credencial lejos del borde vivo
r2 = srv._terminal_nodo(BOARD, task_id, r1["offset_nuevo"])
assert CREDENCIAL not in r2["texto"], "la credencial partida en el borde se colo sin tapar"
assert "[credencial del entorno tapada]" in r2["texto"], r2["texto"][:120]
print("8. una credencial partida justo en el borde de dos lecturas queda tapada igual: OK")

# `final=True`: el nodo ya termino, se manda TODO lo que quedaba retenido.
r3 = srv._terminal_nodo(BOARD, task_id, r2["offset_nuevo"], final=True)
assert r3["offset_nuevo"] == ruta_log.stat().st_size, \
    "final=True deberia vaciar todo lo retenido, no dejar nada pendiente"
print("9. final=True vacia todo lo que habia quedado retenido: OK")

print("\nOK: el mecanismo de archivo+polling de la terminal en vivo funciona.")
