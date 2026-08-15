"""DAG rombo contra el kanban real de Hermes.

    A
   / \
  B   C
   \ /
    D

Verifica que D solo pase a 'ready' cuando B y C esten 'done', y que el
contexto de los padres llegue al hijo. Sin LLM, sin API key: solo el
scheduler y SQLite.
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, r"A:/Proyectos/orquester/hermes-agent")
import hermes_cli.kanban_db as k

db = Path(tempfile.mkdtemp()) / "rombo.db"
k.init_db(db_path=db)
conn = k.connect(db_path=db)

st = lambda t: k.get_task(conn, t).status

# --- Construir el rombo ---
A = k.create_task(conn, title="A: clonar repo")
B = k.create_task(conn, title="B: correr tests", parents=[A])
C = k.create_task(conn, title="C: correr linter", parents=[A])
D = k.create_task(conn, title="D: abrir PR", parents=[B, C])

print(f"Creado: A={A} B={B} C={C} D={D}")
print(f"1. Estado inicial: A={st(A)} B={st(B)} C={st(C)} D={st(D)}")
assert st(A) == "ready", f"A sin padres deberia estar ready, esta {st(A)}"
assert st(B) == st(C) == "todo", "B y C deberian esperar a A"
assert st(D) == "todo", "D deberia esperar a B y C"

# --- Cerrar A: B y C deben liberarse juntos ---
k.complete_task(conn, A, result="repo en /tmp/x", summary="clonado en /tmp/x",
                metadata={"commit": "abc123"})
print(f"2. Tras completar A:  B={st(B)} C={st(C)} D={st(D)}")
assert st(B) == "ready" and st(C) == "ready", "A cerrado libera B y C en paralelo"
assert st(D) == "todo", "D no puede moverse: B y C siguen abiertos"

# --- Cerrar solo B: D NO debe liberarse (el check que importa) ---
k.complete_task(conn, B, result="42 tests ok", summary="42 tests pasaron")
print(f"3. Tras completar solo B: C={st(C)} D={st(D)}")
assert st(D) == "todo", f"FALLO: D se libero con un solo padre cerrado (esta {st(D)})"

# --- Cerrar C: recien ahora D se libera ---
k.complete_task(conn, C, result="0 warnings", summary="linter limpio")
print(f"4. Tras completar C:  D={st(D)}")
assert st(D) == "ready", f"FALLO: D deberia estar ready, esta {st(D)}"

# --- El contexto de los padres llega al hijo ---
ctx = k.build_worker_context(conn, D)
assert "42 tests pasaron" in ctx and "linter limpio" in ctx, \
    f"Los summaries de B y C no llegaron a D:\n{ctx}"
print("5. build_worker_context pasa los summaries de B y C a D: OK")

# --- Ciclo: el kanban NO lo rechaza (justifica el topo-check del compilador) ---
try:
    k.link_tasks(conn, D, A)   # D -> A cierra el ciclo
    ciclo = "ACEPTADO (el kanban no valida ciclos)"
except Exception as e:
    ciclo = f"rechazado: {type(e).__name__}"
print(f"6. Enlace ciclico D->A: {ciclo}")

print("\nTODOS LOS ASSERTS PASARON")
print(f"DB temporal: {db}")
