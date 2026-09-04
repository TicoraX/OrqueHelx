"""Lecciones por repositorio, del lado del servidor (L2, docs/PLAN-2026-09-01-
lecciones-por-repositorio.md): guardar/leer el archivo del workspace del nodo,
y que guardar dos veces seguidas no apile sellos de fecha.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_lecciones.py
"""
import sys, tempfile, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "compiler", "dispatcher", "ui"):
    sys.path.insert(0, str(RAIZ / sub))
import compile as c
import hermes_cli.kanban_db as k
import server as srv

BOARD = f"lecc-{int(time.time() * 1000) % 10_000_000}"
ws = Path(tempfile.mkdtemp())
g = {"board": BOARD, "aristas": [],
     "nodos": [{"id": "n1", "titulo": "nodo", "runtime": "opencode", "workspace": str(ws)}]}
task_id = c.compilar(g, board=BOARD)["n1"]

# --- Sin archivo todavia: workspace=True, texto vacio -----------------------
l0 = srv._lecciones_nodo(BOARD, task_id)
assert l0 == {"ok": True, "workspace": True, "texto": ""}, l0
print("1. workspace con nodo pero sin archivo de lecciones todavia: texto vacio, OK")

# --- Guardar y releer ---------------------------------------------------------
r1 = srv._guardar_lecciones_nodo(BOARD, task_id, "no uses tabs en este repo")
assert r1 == {"ok": True}, r1
l1 = srv._lecciones_nodo(BOARD, task_id)
assert "no uses tabs" in l1["texto"], l1
assert l1["texto"].startswith("<!-- actualizado "), l1["texto"]
print("2. guardar y releer trae el texto con un sello de fecha adelante: OK")

# --- Guardar de nuevo (el panel manda el texto CON el sello que acaba de leer,
# tal como haria el usuario editando el textarea) -- no se apila otro sello ---
r2 = srv._guardar_lecciones_nodo(BOARD, task_id, l1["texto"] + "\ny tampoco imports circulares")
l2 = srv._lecciones_nodo(BOARD, task_id)
assert l2["texto"].count("actualizado") == 1, \
    f"se apilaron sellos de fecha: {l2['texto']!r}"
assert "tampoco imports circulares" in l2["texto"], l2["texto"]
assert "no uses tabs" in l2["texto"], l2["texto"]
print("3. guardar de nuevo (con el sello viejo incluido, como manda el panel) no lo apila: OK")

print("\nOK: lecciones por repositorio, guardado y lectura del lado del servidor.")
