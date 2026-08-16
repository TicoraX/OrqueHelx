"""DAG rombo HETEROGENEO sobre el board real `orquester-mixto`.

    A  claude-code
   / \
  B   C     B=opencode   C=hermes nativo
   \ /
    D  antigravity-cli

Este script SOLO arma el grafo y verifica el estado inicial. La ejecucion la
hace el dispatcher (`hermes kanban --board orquester-mixto dispatch`).

Lo que se prueba con workers reales (ver test_dag_heterogeneo.md):
  1. fan-out: B y C corren en paralelo tras cerrar A
  2. join: D no arranca hasta que B y C cerraron
  3. handoff de contexto entre backends distintos

Correr desde hermes-agent/:
    uv run --python 3.11 python ..\\tests\\test_dag_heterogeneo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hermes-agent"))
import hermes_cli.kanban_db as k

BOARD = "orquester-mixto"
conn = k.connect(board=BOARD)

st = lambda t: k.get_task(conn, t).status

# Goals inequivocos, autocontenidos, sin depender de archivos preexistentes.
# El workspace scratch se borra en complete_task: nada de entregables en disco.
GOAL_A = ("Escribe un plan de tres pasos para agregar logging a un modulo "
          "Python. Devuelve solo los tres pasos numerados.")
GOAL_B = ("Escribe una funcion Python `setup_logger(name)` que devuelva un "
          "logging.Logger configurado a nivel INFO. Devuelve solo el codigo.")
GOAL_C = ("Escribe un parrafo de documentacion que explique como usar "
          "`setup_logger`. Devuelve solo el parrafo.")
GOAL_D = ("Revisa el codigo y la documentacion que recibiste en tu contexto y "
          "responde si son consistentes entre si. Devuelve un veredicto de "
          "una linea.")

# initial_status NO se pasa: no acepta "ready" y la logica de padres decide.
A = k.create_task(conn, title=GOAL_A, assignee="default",
                  skills=["claude-code"])
B = k.create_task(conn, title=GOAL_B, assignee="default",
                  skills=["opencode"], parents=[A])
C = k.create_task(conn, title=GOAL_C, assignee="default",
                  parents=[A])                      # sin skills: hermes nativo
D = k.create_task(conn, title=GOAL_D, assignee="default",
                  skills=["antigravity-cli"], parents=[B, C])

print(f"Board: {BOARD}")
print(f"A (claude-code)     = {A}")
print(f"B (opencode)        = {B}")
print(f"C (hermes nativo)   = {C}")
print(f"D (antigravity-cli) = {D}")
print(f"Estado inicial: A={st(A)} B={st(B)} C={st(C)} D={st(D)}")

assert st(A) == "ready", f"A sin padres deberia estar ready, esta {st(A)}"
assert st(B) == "todo", f"B deberia esperar a A, esta {st(B)}"
assert st(C) == "todo", f"C deberia esperar a A, esta {st(C)}"
assert st(D) == "todo", f"D deberia esperar a B y C, esta {st(D)}"

print("\nASSERTS OK: A liberado, B/C/D bloqueados.")
print(f"IDS={A},{B},{C},{D}")
