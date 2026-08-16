"""Contabilidad de consumo por nodo y por flujo.

Los fixtures son las formas de salida REALES de cada CLI, capturadas
ejecutandolos, no inventadas. Si un CLI cambia su formato, este test falla
antes de que el panel muestre ceros en silencio — que es la peor forma de
fallar para algo que se mira para decidir.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_consumo.py
"""
import json, sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "ui"))

import backends as b
import loop
import hermes_cli.kanban_db as k

# --- Fixtures: salidas reales, recortadas ---
OPENCODE = "\n".join([
    json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
    json.dumps({"type": "text", "part": {"type": "text", "text": '{"status":"success","summary":"7"}'}}),
    json.dumps({"type": "step_finish", "part": {
        "tokens": {"total": 23291, "input": 21369, "output": 2,
                   "reasoning": 0, "cache": {"write": 0, "read": 1920}},
        "cost": 0.009304215}}),
])
CLAUDE = json.dumps({
    "is_error": False, "num_turns": 2, "duration_ms": 2987,
    "total_cost_usd": 0.24467450000000002, "subtype": "success",
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 23626,
              "cache_read_input_tokens": 12909, "output_tokens": 78},
    "structured_output": {"status": "success", "summary": "7"},
})
AGY = json.dumps({
    "conversation_id": "b62", "status": "SUCCESS", "num_turns": 1,
    "duration_seconds": 6.5213993,
    "usage": {"input_tokens": 28451, "output_tokens": 175, "thinking_tokens": 151,
              "cache_read_tokens": 0, "total_tokens": 28626},
    "structured_output": {"status": "success", "summary": "7"},
})

u = b._uso_opencode(OPENCODE)
assert (u["entrada"], u["salida"], u["total"]) == (21369, 2, 23291), u
assert u["cache_lectura"] == 1920 and abs(u["costo_usd"] - 0.009304215) < 1e-9, u
print(f"1. opencode: {u['total']} tokens, ${u['costo_usd']:.6f}: OK")

u = b._uso_claude(CLAUDE)
assert (u["entrada"], u["salida"], u["cache_lectura"]) == (2, 78, 12909), u
assert u["total"] == 2 + 78 + 12909 + 23626, u
assert u["costo_usd"] == 0.24467450000000002 and u["turnos"] == 2, u
assert u["duracion_s"] == 3.0, u
print(f"2. claude: {u['total']} tokens, ${u['costo_usd']:.4f}, {u['turnos']} turnos: OK")

u = b._uso_agy(AGY)
assert (u["entrada"], u["salida"], u["total"]) == (28451, 175, 28626), u
assert u["turnos"] == 1 and u["duracion_s"] == 6.5, u
# agy corre por suscripcion: no informa medidor. None NO es lo mismo que 0.
assert u["costo_usd"] is None, "agy no informa costo; None no es cero"
print(f"3. antigravity: {u['total']} tokens, costo=None (suscripcion): OK")

# --- Una salida rota no debe tirar el nodo, solo dar consumo vacio ---
for fn in (b._uso_claude, b._uso_agy):
    v = fn("esto no es json")
    assert v["total"] == 0 and v["costo_usd"] is None, v
assert b._uso_opencode("basura\nmas basura")["total"] == 0
print("4. salida ilegible -> consumo en cero, sin excepcion: OK")

# --- run_backend adjunta el consumo sin ensuciar el contrato ---
SALIDA = json.dumps({"status": "success", "summary": "listo"})
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", f"print({SALIDA!r})"],
                          b._primer_objeto)
b.USO["opencode"] = lambda out: dict(b._uso_vacio("opencode"), total=99, costo_usd=0.5)
r = b.run_backend("opencode", "x", timeout=60)
assert r["status"] == "success" and r["uso"]["total"] == 99, r
print("5. run_backend adjunta `uso` despues de validar el contrato: OK")

# --- Agregacion por flujo, sobre runs y no sobre tasks ---
db = Path(tempfile.mkdtemp()) / "consumo.db"
k.init_db(db_path=db)
conn = k.connect(db_path=db)
_connect = k.connect
k.connect = lambda **kw: _connect(db_path=db)

import server                                  # importa despues del parche

# Un nodo que falla una vez y a la segunda anda: dos intentos, dos consumos.
intentos = {"n": 0}
def _flaky(g, e):
    intentos["n"] += 1
    return (["python", "-c", "print('sin contrato')"] if intentos["n"] == 1
            else ["python", "-c", f"print({SALIDA!r})"])
b.BACKENDS["opencode"] = (_flaky, b._primer_objeto)
b.USO["opencode"] = lambda out: dict(b._uso_vacio("opencode"), entrada=10, total=10,
                                     costo_usd=0.25)

t = k.create_task(conn, title="nodo con reintento", assignee=loop.carril("opencode"))
loop.ejecutar_una(conn, t, timeout=60)          # falla -> bloquea
loop.reintentar(conn)
loop.ejecutar_una(conn, t, timeout=60)          # ahora cierra

runs = k.list_runs(conn, t)
con_uso = [u for u in ((r.metadata or {}).get("uso") for r in runs) if u]
print(f"6. intentos={len(runs)}, con consumo registrado={len(con_uso)}")
assert len(runs) == 2, f"deberia haber 2 intentos: {len(runs)}"
assert len(con_uso) == 1, ("solo el intento que cerro registra consumo; "
                           "el que fallo no llego a completar")
assert con_uso[0]["total"] == 10 and con_uso[0]["costo_usd"] == 0.25

# `_consumo` agrega sobre la misma base temporal, porque `k.connect` esta parcheado.
agg = server._consumo("cualquiera")
print(f"7. agregado del flujo: {agg['total']['total']} tokens, "
      f"${agg['total']['costo_usd']:.2f}, {agg['total']['intentos']} intento(s)")
assert agg["total"]["total"] == 10 and agg["total"]["costo_usd"] == 0.25
assert agg["por_nodo"][t]["runtime"] == "opencode"

# Un backend sin medidor no debe contarse como costo cero: se cuenta aparte.
b.USO["opencode"] = lambda out: dict(b._uso_vacio("opencode"), total=5, costo_usd=None)
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", f"print({SALIDA!r})"],
                          b._primer_objeto)
t2 = k.create_task(conn, title="nodo por suscripcion", assignee=loop.carril("opencode"))
loop.ejecutar_una(conn, t2, timeout=60)
agg = server._consumo("cualquiera")
print(f"8. con costo={agg['total']['con_costo']}, sin costo={agg['total']['sin_costo']}")
assert agg["total"]["con_costo"] == 1 and agg["total"]["sin_costo"] == 1
assert agg["total"]["costo_usd"] == 0.25, "un nodo sin medidor no suma ni resta al costo"
assert agg["por_nodo"][t2]["costo_usd"] is None, "None no es 0"

print("\nOK: el consumo se extrae, se persiste y se agrega.")
