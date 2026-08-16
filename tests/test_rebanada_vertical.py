"""Rebanada vertical del dispatcher externo (ARQUITECTURA.md SS12).

Dos partes:
  A) offline, siempre corre: los parsers contra las formas de salida REALES
     capturadas en la verificacion, mas denylist y contrato.
  B) e2e con `opencode`, solo con --e2e: cadena de dos nodos sobre un board
     real, para ver el handoff de contexto entre nodos externos.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_rebanada_vertical.py
    uv run --python 3.11 --with jsonschema python ..\\tests\\test_rebanada_vertical.py --e2e
"""
import sys, json
from pathlib import Path

sys.path.insert(0, r"A:/Proyectos/orquester/dispatcher")
sys.path.insert(0, r"A:/Proyectos/orquester/hermes-agent")
import backends as b

# ---------------------------------------------------------------- Parte A ---
# Formas de salida reales, no inventadas: agy anida el contrato en
# `structured_output` y deja prosa en `response`; opencode emite JSONL de
# eventos donde la respuesta son los `text` concatenados.
AGY = json.dumps({
    "conversation_id": "c1", "duration_seconds": 11.5,
    "response": "Conte los archivos.\n\n```json\n{\"status\":\"success\"}\n```",
    "structured_output": {"status": "success", "summary": "3 archivos .md"},
    "usage": {"input_tokens": 100},
})
OPENCODE = "\n".join([
    json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
    json.dumps({"type": "tool_use", "part": {"name": "bash"}}),
    # El texto va en `part.text`, no en la raiz: forma real de v1.18.18.
    json.dumps({"type": "text", "part": {"type": "text", "text": '{"status": "suc'}}),
    json.dumps({"type": "text", "part": {"type": "text", "text": 'cess", "summary": "7 archivos"}'}}),
    json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
])
CLAUDE_PLANO = 'Listo.\n{"status":"failure","summary":"no encontre nada"}\nfin.'

_, parse_agy = b.BACKENDS["antigravity"]
_, parse_oc = b.BACKENDS["opencode"]

assert parse_agy(AGY) == {"status": "success", "summary": "3 archivos .md"}
assert parse_oc(OPENCODE) == {"status": "success", "summary": "7 archivos"}
assert b._primer_objeto(CLAUDE_PLANO)["status"] == "failure"
print("A1. parsers: agy/structured_output, opencode/JSONL, objeto embebido en prosa: OK")

# `response` trae prosa con un objeto INCOMPLETO pegado; el parser debe
# preferir structured_output y no quedarse con el primero que encuentre.
assert parse_agy(AGY)["summary"], "no debe caer en el objeto de `response`"

# Anidamiento y llaves dentro de strings: una regex ingenua se rompe aca.
anidado = '{"status":"success","summary":"dice {\\"raro\\"} y }{ suelto","extra":{"a":{"b":1}}}'
assert b._primer_objeto(anidado)["summary"] == 'dice {"raro"} y }{ suelto'
print("A2. scanner balanceado sobre llaves anidadas y dentro de strings: OK")

# El contrato se valida de verdad (SS5: jsonschema es dependencia dura).
try:
    b._validar({"status": "done", "summary": "x"})     # 'done' no esta en el enum
    raise SystemExit("FALLA: acepto un status invalido")
except Exception as e:
    assert "done" in str(e), e
print("A3. contrato rechaza status fuera del enum: OK")

# Denylist de flags de bypass (SS4.1).
b.BACKENDS["_prueba"] = (lambda g, s: ["echo", "--dangerously-skip-permissions"], dict)
try:
    b.run_backend("_prueba", "x")
    raise SystemExit("FALLA: dejo pasar un flag de bypass")
except b.BackendError as e:
    assert "bypass" in str(e), e
finally:
    del b.BACKENDS["_prueba"]
print("A4. denylist bloquea --dangerously-skip-permissions: OK")

# Nunca juzgar por exit code: exit 0 con salida basura debe ser BackendError.
b.BACKENDS["_vacio"] = (lambda g, s: ["python", "-c", "print('sin json')"],
                        b._primer_objeto)
try:
    b.run_backend("_vacio", "x")
    raise SystemExit("FALLA: acepto una salida sin contrato por tener exit 0")
except b.BackendError:
    pass
finally:
    del b.BACKENDS["_vacio"]
print("A5. exit 0 con salida invalida se trata como fallo: OK")

# El sufijo de runtime no debe volver spawneable al carril: si `orquester-
# external:opencode` fuera un perfil valido, el dispatcher de Hermes intentaria
# `hermes -p orquester-external:opencode` y nos robaria la card.
import loop
from hermes_cli.profiles import normalize_profile_name, profile_exists
for rt in b.BACKENDS:
    lane = loop.carril(rt)
    assert normalize_profile_name(lane) == lane, "el assignee debe sobrevivir intacto"
    assert not profile_exists(lane), f"{lane} NO puede ser un perfil Hermes"
assert loop._runtime_de(type("T", (), {"id": "t1", "assignee": "orquester-external:opencode"})) == "opencode"
print("A6. el sub-carril sobrevive la normalizacion y sigue sin ser perfil: OK")

if "--e2e" not in sys.argv:
    print("\nOK parte A. Corre con --e2e para la cadena real sobre el board.")
    raise SystemExit(0)

# ---------------------------------------------------------------- Parte B ---
import hermes_cli.kanban_db as k

BOARD = "orquester-slice"
try:
    k.create_board(BOARD)
except Exception:
    pass                                   # ya existe
conn = k.connect(board=BOARD)

A = k.create_task(conn, title="Responde con el numero 7 y nada mas",
                  assignee=loop.carril("opencode"))
B = k.create_task(conn, title="Repeti el numero que te paso tu padre",
                  assignee=loop.carril("opencode"), parents=[A])
print(f"\nB1. cadena creada: A={A} B={B}")
assert k.get_task(conn, A).status == "ready"
assert k.get_task(conn, B).status == "todo", "B debe esperar a A"

ctx_antes = k.build_worker_context(conn, B)
res_a = loop.ejecutar_una(conn, A, timeout=300)
print(f"B2. A -> {res_a['status']}: {res_a['summary'][:80]}")
assert res_a["status"] == "success", res_a

assert k.get_task(conn, B).status == "ready", "cerrar A debe liberar B"
ctx_despues = k.build_worker_context(conn, B)
assert len(ctx_despues) > len(ctx_antes), "el contexto de B debe crecer con el padre"
assert "Parent task results" in ctx_despues, ctx_despues[-500:]
print("B3. el summary de A llego al contexto de B: OK")

res_b = loop.ejecutar_una(conn, B, timeout=300)
print(f"B4. B -> {res_b['status']}: {res_b['summary'][:80]}")
assert res_b["status"] == "success", res_b
assert k.get_task(conn, B).status == "done"

# --- Un nodo que falla NO debe liberar a sus hijos ---
# Es el bug que destapo el board `mixto-4`: cerrar con `complete_task` marca la
# card 'done' y el kanban promueve al hijo, que arranca sobre el mensaje de
# error creyendo que es el resultado del padre.
P = k.create_task(conn, title="Nodo que va a fallar", assignee=loop.carril("claude-code"))
H = k.create_task(conn, title="Hijo que no debe arrancar",
                  assignee=loop.carril("opencode"), parents=[P])
orig = b.BACKENDS["claude-code"]
b.BACKENDS["claude-code"] = (lambda g, e: ["python", "-c", "print('sin contrato')"],
                             b._primer_objeto)
try:
    res = loop.ejecutar_una(conn, P, timeout=60)
finally:
    b.BACKENDS["claude-code"] = orig
print(f"B5. fallo -> padre={k.get_task(conn, P).status}, hijo={k.get_task(conn, H).status}")
assert res["status"] == "failure", res
assert k.get_task(conn, P).status == "blocked", "un fallo debe bloquear, no cerrar"
assert k.get_task(conn, H).status == "todo", "el hijo NO puede liberarse tras un fallo"

print("\nOK: la rebanada vertical corre punta a punta.")
