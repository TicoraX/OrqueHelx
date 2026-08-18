"""El acomodo automatico del grafo: capas, sin cruces y sin pisarse.

Se prueba la LOGICA, no el dibujo: entra un grafo, salen coordenadas. Lo que
importa es que un hijo nunca quede por encima de un padre (la flecha subiria) y
que dos nodos no se superpongan.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_disposicion.py
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "dispatcher"))

import json
import disposicion as d

N = lambda i: {"id": i, "titulo": f"tarea {i}", "runtime": "hermes", "x": 0, "y": 0}
NOTA = lambda i: {"id": i, "titulo": "explicacion", "tipo": "nota", "x": 0, "y": 0}


def sin_pisarse(grafo, pos):
    """Ningun par de cajas se superpone."""
    cajas = []
    for n in grafo["nodos"]:
        p = pos[n["id"]]
        ancho = d.ANCHO_NOTA if n.get("tipo") == "nota" else d.ANCHO
        cajas.append((n["id"], p["x"], p["y"], p["x"] + ancho, p["y"] + d.ALTO))
    for i, (ia, x0, y0, x1, y1) in enumerate(cajas):
        for ib, a0, b0, a1, b1 in cajas[i + 1:]:
            if x0 < a1 and a0 < x1 and y0 < b1 and b0 < y1:
                raise AssertionError(f"'{ia}' y '{ib}' se pisan")


# --- 1. Cadena: cada uno debajo del anterior ---
g = {"nodos": [N("a"), N("b"), N("c")], "aristas": [["a", "b"], ["b", "c"]]}
pos = d.ordenar(g)
assert pos["a"]["y"] < pos["b"]["y"] < pos["c"]["y"], pos
assert pos["a"]["x"] == pos["b"]["x"] == pos["c"]["x"], "una cadena va en columna"
sin_pisarse(g, pos)
print("1. cadena: en columna y cada hijo mas abajo: OK")

# --- 2. Rombo: los dos del medio en la MISMA capa, el cierre debajo ---
g = {"nodos": [N("a"), N("b"), N("c"), N("d")],
     "aristas": [["a", "b"], ["a", "c"], ["b", "d"], ["c", "d"]]}
pos = d.ordenar(g)
assert pos["b"]["y"] == pos["c"]["y"], "b y c son hermanos: misma fila"
assert pos["b"]["x"] != pos["c"]["x"], "y en columnas distintas"
assert pos["d"]["y"] > pos["b"]["y"], pos
sin_pisarse(g, pos)
print("2. rombo: hermanos en la misma fila, el cierre debajo: OK")

# --- 3. Camino mas largo, no el primero que llega ---
# `d` depende de `a` (capa 0) y de `c` (capa 2). Si se tomara el primer padre
# que aparece, `d` caeria en la capa 1 y la flecha desde `c` iria hacia ARRIBA.
g = {"nodos": [N("a"), N("b"), N("c"), N("d")],
     "aristas": [["a", "b"], ["b", "c"], ["a", "d"], ["c", "d"]]}
pos = d.ordenar(g)
for p, h in g["aristas"]:
    assert pos[p]["y"] < pos[h]["y"], f"la flecha {p}->{h} sube en vez de bajar"
sin_pisarse(g, pos)
print("3. un nodo con padres de capas distintas cae bajo el MAS profundo: OK")

# --- 4. Las notas van a su propia columna ---
g = {"nodos": [N("a"), N("b"), NOTA("n1"), NOTA("n2")], "aristas": [["a", "b"]]}
pos = d.ordenar(g)
assert pos["n1"]["x"] < pos["a"]["x"], "la nota invade la columna de los nodos"
assert pos["n1"]["x"] == pos["n2"]["x"], "las notas comparten columna"
assert pos["n1"]["y"] != pos["n2"]["y"], "dos notas apiladas en el mismo lugar"
sin_pisarse(g, pos)
print("4. las notas tienen su columna y no pisan al grafo: OK")

# --- 5. Idempotente: apretar el boton dos veces no mueve nada ---
# Sin esto, el orden dentro de una capa saldria del recorrido de un dict y los
# nodos saltarian de lugar en cada clic.
g = {"nodos": [N("a"), N("b"), N("c"), N("d")],
     "aristas": [["a", "b"], ["a", "c"], ["a", "d"]]}
p1 = d.ordenar(g)
for n in g["nodos"]:
    n.update(p1[n["id"]])
p2 = d.ordenar(g)
assert p1 == p2, "ordenar dos veces da resultados distintos"
print("5. ordenar es idempotente: OK")

# --- 6. Las plantillas reales quedan bien acomodadas ---
for f in sorted((RAIZ / "plantillas").glob("*.json")):
    g = json.loads(f.read_text(encoding="utf-8"))
    pos = d.ordenar(g)
    assert len(pos) == len(g["nodos"]), f"{f.stem}: faltan coordenadas"
    for p, h in g["aristas"]:
        assert pos[p]["y"] < pos[h]["y"], f"{f.stem}: {p}->{h} sube"
    sin_pisarse(g, pos)
print(f"6. las plantillas del repo se acomodan sin cruces ni superposiciones: OK")

print("\nOK: el grafo se acomoda solo, en capas y sin pisarse.")
