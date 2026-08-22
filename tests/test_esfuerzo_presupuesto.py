"""Esfuerzo por nodo y tope de gasto por flujo. Sin LLM: procesos falsos.

Dos perillas que cuestan plata si fallan en silencio:
  - pedir `max` y que corra en el default es perder el trabajo que se pago;
  - un fan-out ancho con esfuerzo alto es una factura sin techo.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_esfuerzo_presupuesto.py
"""
import sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

import backends as b
import capacidades
import compile as c
import loop
import hermes_cli.kanban_db as k

# --- 1. El nivel se valida contra el backend, no contra una lista global ---
for rt, validos in b.ESFUERZO.items():
    assert capacidades.DECLARADO[rt]["esfuerzos"] == list(validos[1]), \
        f"la tabla de capacidades y backends no coinciden en {rt}"
print("1. capacidades y backends declaran los MISMOS niveles: OK")

try:
    b._flags_extra("antigravity", "max")
    raise SystemExit("FALLA: agy no llega a 'max'")
except b.BackendError as e:
    assert "no acepta esfuerzo 'max'" in str(e), e
assert b._flags_extra("claude-code", "max") == ["--effort", "max"]
assert b._flags_extra("opencode", "high") == ["--variant", "high"]
assert b._flags_extra("antigravity", "high") == ["--effort", "high"]
print("2. cada CLI valida el nivel con SU lista y usa SU flag: OK")

# --- 3. El tope de gasto solo se manda a quien lo entiende ---
assert b._flags_extra("claude-code", None, 1.5) == ["--max-budget-usd", "1.5000"]
assert b._flags_extra("opencode", None, 1.5) == [], \
    "no se puede simular un tope que el CLI no tiene"
assert b._flags_extra("claude-code", None, -3) == ["--max-budget-usd", "0.0000"]
print("3. el tope va solo al CLI con soporte nativo, y nunca negativo: OK")

# --- 4. El compilador rechaza un nivel imposible ANTES de crear cards ---
g = {"board": "esf", "nodos": [{"id": "a", "titulo": "x", "runtime": "antigravity",
                                "esfuerzo": "max"}], "aristas": []}
try:
    c.validar(g, capacidades=False)
    raise SystemExit("FALLA: compilo un esfuerzo que el CLI rechaza")
except c.ErrorDeGrafo as e:
    assert "no acepta esfuerzo 'max'" in str(e), e
print("4. el compilador rechaza el nivel imposible antes de tocar la base: OK")

# --- 5. El esfuerzo llega a la card como `reasoning_effort` ---
db = Path(tempfile.mkdtemp()) / "esf.db"
k.init_db(db_path=db)
_connect = k.connect
k.connect = lambda **kw: _connect(db_path=db)
k.create_board = lambda *a, **kw: None

g = {"board": "esf", "nodos": [
        {"id": "a", "titulo": "x", "runtime": "claude-code", "esfuerzo": "xhigh"},
        {"id": "b", "titulo": "y", "runtime": "opencode"}],
     "aristas": []}
ids = c.compilar(g, board="esf")
conn = k.connect(board="esf")
assert k.get_task(conn, ids["a"]).reasoning_effort == "xhigh"
assert k.get_task(conn, ids["b"]).reasoning_effort is None, "invento un esfuerzo"
print("5. el esfuerzo viaja en `reasoning_effort` de la card: OK")

# --- 6. El dispatcher lee la card y lo pasa al CLI ---
visto = {}
_run = b.run_backend
def espia(runtime, goal, **kw):
    visto.update(kw)
    return {"status": "success", "summary": "ok", "uso": {"costo_usd": 0.5}}
loop.run_backend = espia
loop.ejecutar_una(conn, ids["a"], timeout=30)
assert visto["esfuerzo"] == "xhigh", visto
print("6. el dispatcher pasa el esfuerzo de la card al backend: OK")

# --- 7. El gasto se suma sobre los intentos ---
gasto = loop.gasto_usd(conn)
assert gasto == 0.5, f"gasto mal sumado: {gasto}"
print(f"7. el gasto del board se suma sobre los runs (US$ {gasto}): OK")

# --- 8. Con el tope alcanzado no se arranca ningun nodo nuevo ---
# El corte es ENTRE nodos: lo que ya arranco termina. Lo que NO puede pasar es
# que el tick siguiente lance mas trabajo.
visto.clear()
hechas = loop.tick(conn, timeout=30, tope_usd=0.4)
assert hechas == [], f"arranco nodos con el tope pasado: {hechas}"
assert not visto, "llamo al backend con el presupuesto agotado"
print("8. con el tope superado, el tick no arranca nada: OK")

# --- 9. Con presupuesto de sobra, se le pasa el RESTO al CLI que lo entiende ---
hechas = loop.tick(conn, timeout=30, tope_usd=2.0)
assert hechas, "no arranco con presupuesto disponible"
assert abs(visto["presupuesto"] - 1.5) < 1e-9, \
    f"le paso el tope entero en vez del resto: {visto.get('presupuesto')}"
print("9. al nodo se le pasa lo que QUEDA del tope, no el tope entero: OK")

# --- 10. Presupuesto individual por nodo acota el presupuesto efectivo ---
g_nodo = {"board": "esf-nodo", "nodos": [
    {"id": "n1", "titulo": "t1", "runtime": "claude-code", "presupuesto_usd": "0.75"}],
    "aristas": []}
ids_nodo = c.compilar(g_nodo, board="esf-nodo")
visto.clear()
loop.ejecutar_una(conn, ids_nodo["n1"], timeout=30, presupuesto=2.0)
assert abs(visto["presupuesto"] - 0.75) < 1e-9, f"no aplico el tope por nodo: {visto}"
print("10. el presupuesto individual por nodo acota el tope del backend: OK")

loop.run_backend = _run
print("\nOK: el esfuerzo se elige por nodo y el gasto tiene techo.")
