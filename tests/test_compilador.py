"""Check del compilador (ARQUITECTURA.md SS4). Sin LLM: solo grafo -> kanban.

Valida con `capacidades=False`: lo que se prueba aca es la ESTRUCTURA del
grafo, que vale en cualquier maquina. El preflight de binarios instalados es
de esta maquina y se prueba aparte, en `test_capacidades.py`.

    uv run --python 3.11 python ..\\tests\\test_compilador.py
"""
import sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "dispatcher"))

import compile as c
import hermes_cli.kanban_db as k

def falla(grafo, fragmento):
    try:
        c.validar(grafo, capacidades=False)
    except c.ErrorDeGrafo as e:
        assert fragmento in str(e), f"esperaba {fragmento!r} en {e!r}"
        return
    raise SystemExit(f"FALLA: acepto un grafo invalido ({fragmento})")

N = lambda i, rt="hermes": {"id": i, "titulo": f"tarea {i}", "runtime": rt}

# --- Validacion: todo lo que se rechaza sin tocar la base ---
falla({"nodos": []}, "no tiene nodos")
falla({"nodos": [{"id": "a", "titulo": "x", "runtime": "gpt5"}]}, "desconocido")
falla({"nodos": [{"id": "a", "titulo": "  "}]}, "falta 'titulo'")
falla({"nodos": [N("a"), N("a")]}, "ids repetidos")
falla({"nodos": [N("a")], "aristas": [["a", "z"]]}, "nodo inexistente")
falla({"nodos": [N("a")], "aristas": [["a", "a"]]}, "de si mismo")
falla({"nodos": [N("a"), N("b")], "aristas": [["a", "b"], ["b", "a"]]}, "ciclo")
NOTA = lambda i: {"id": i, "titulo": "esto explica el flujo", "tipo": "nota"}
falla({"nodos": [NOTA("n1")]}, "todo notas")
falla({"nodos": [N("a"), NOTA("n1")], "aristas": [["a", "n1"]]}, "no puede tener dependencias")
falla({"nodos": [N("a"), NOTA("n1")], "aristas": [["n1", "a"]]}, "no puede tener dependencias")
# Una nota con un runtime inventado NO es un error: no se ejecuta.
c.validar({"nodos": [N("a"), NOTA("n1")]}, capacidades=False)
# Validacion de presupuesto por nodo
falla({"nodos": [{"id": "a", "titulo": "t", "presupuesto_usd": "-5"}]}, "invalido")
falla({"nodos": [{"id": "a", "titulo": "t", "presupuesto_usd": "abc"}]}, "invalido")
# `True` y `inf` pasaban: `float(True)` es 1.0 (un tope de US$1 que nadie
# escribio) y `inf` cumple `> 0` pero no se alcanza nunca, que es un tope que
# se cree puesto y no lo esta.
falla({"nodos": [{"id": "a", "titulo": "t", "presupuesto_usd": True}]}, "invalido")
falla({"nodos": [{"id": "a", "titulo": "t", "presupuesto_usd": float("inf")}]}, "invalido")
falla({"nodos": [{"id": "a", "titulo": "t", "presupuesto_usd": float("nan")}]}, "invalido")
c.validar({"nodos": [{"id": "a", "titulo": "t", "presupuesto_usd": "1.50"}]}, capacidades=False)
# Validacion de herramientas por nodo
falla({"nodos": [{"id": "a", "titulo": "t", "herramientas": ["Inexistente"]}]}, "invalidas")
falla({"nodos": [{"id": "a", "titulo": "t", "herramientas": "Bash"}]}, "invalidas")
c.validar({"nodos": [{"id": "a", "titulo": "t", "herramientas": ["Read", "Bash"]}]}, capacidades=False)
print("1. validacion rechaza: vacio, runtime malo, sin titulo, ids repetidos,")
print("   arista colgada, auto-enlace, ciclo, presupuesto y herramientas invalidas: OK")

# --- El ciclo se nombra entero, no un solo enlace ---
try:
    c.validar({"nodos": [N("a"), N("b"), N("c")],
               "aristas": [["a", "b"], ["b", "c"], ["c", "a"]]}, capacidades=False)
except c.ErrorDeGrafo as e:
    assert "'a', 'b', 'c'" in str(e).replace('"', "'"), e
print("2. el error de ciclo nombra los tres nodos trabados: OK")

# --- Mapeo de runtime a assignee ---
assert c._assignee(N("a", "hermes")) == "default"
assert c._assignee({"id": "a", "titulo": "t", "runtime": "hermes", "perfil": "otro"}) == "otro"
assert c._assignee(N("a", "opencode")) == "orquester-external:opencode"
assert c._assignee(N("a", "antigravity")) == "orquester-external:antigravity"
print("3. runtime -> assignee (perfil Hermes vs carril externo): OK")

# --- Orden topologico: padres antes que hijos ---
rombo = {"nodos": [N("d"), N("b"), N("a"), N("c")],          # a proposito desordenado
         "aristas": [["a", "b"], ["a", "c"], ["b", "d"], ["c", "d"]]}
orden = [n["id"] for n in c._orden_topologico(rombo)]
assert orden.index("a") < orden.index("b") < orden.index("d")
assert orden.index("a") < orden.index("c") < orden.index("d")
print(f"4. orden topologico del rombo: {orden}: OK")

# --- Compilacion real contra un kanban de verdad ---
db = Path(tempfile.mkdtemp()) / "compilado.db"
k.init_db(db_path=db)
conn = k.connect(db_path=db)

# Se compila a mano contra la conexion temporal: `compilar()` resuelve el board
# por nombre y aca queremos aislarlo del kanban del usuario.
ids = {}
aristas = rombo["aristas"]
for nodo in c._orden_topologico(rombo):
    ids[nodo["id"]] = k.create_task(
        conn, title=nodo["titulo"], assignee=c._assignee(nodo),
        parents=[ids[p] for p, h in aristas if h == nodo["id"]],
    )
st = lambda i: k.get_task(conn, ids[i]).status
print(f"5. compilado: {ids}")
assert st("a") == "ready", f"la raiz deberia estar ready, esta {st('a')}"
assert st("b") == st("c") == st("d") == "todo", "el resto espera a sus padres"

k.complete_task(conn, ids["a"], summary="hecho")
assert st("b") == st("c") == "ready" and st("d") == "todo"
print("6. el DAG compilado se comporta como el rombo: OK")

# --- Un grafo mixto: un nodo Hermes y uno externo ---
mixto = {"nodos": [N("nativo", "hermes"), N("externo", "opencode")],
         "aristas": [["nativo", "externo"]]}
c.validar(mixto, capacidades=False)
ids2 = {}
for nodo in c._orden_topologico(mixto):
    ids2[nodo["id"]] = k.create_task(
        conn, title=nodo["titulo"], assignee=c._assignee(nodo),
        parents=[ids2[p] for p, h in mixto["aristas"] if h == nodo["id"]],
    )
assert k.get_task(conn, ids2["nativo"]).assignee == "default"
assert k.get_task(conn, ids2["externo"]).assignee == "orquester-external:opencode"
print("7. grafo mixto: cada nodo cae en el carril de su ejecutor: OK")


# --- Notas y reglas ---
db = Path(tempfile.mkdtemp()) / "notas.db"
k.init_db(db_path=db)
_connect = k.connect
k.connect = lambda **kw: _connect(db_path=db)
k.create_board = lambda *a, **kw: None

g = {"board": "notas", "reglas": "No instales dependencias. Respondé en español.",
     "nodos": [N("a"), N("b"), NOTA("n1")], "aristas": [["a", "b"]]}
ids = c.compilar(g, board="notas")
assert set(ids) == {"a", "b"}, f"la nota se compilo como card: {ids}"

conn = k.connect(board="notas")
for tid in ids.values():
    body = k.get_task(conn, tid).body or ""
    assert "No instales dependencias" in body, f"las reglas no llegaron al nodo: {body!r}"
    assert "Reglas del flujo" in body
# El goal sigue limpio: las reglas van en el body, no pegadas al titulo.
assert "No instales" not in k.get_task(conn, ids["a"]).title
print("8. las notas no se compilan y las reglas llegan a TODOS los nodos: OK")

# Sin reglas, el body no se inventa.
g2 = {"board": "sinreglas", "nodos": [N("z")], "aristas": []}
ids2 = c.compilar(g2, board="notas")
assert k.get_task(conn, ids2["z"]).body in (None, ""), "invento un body sin reglas"
print("9. sin reglas, el body queda vacio: OK")

print("\nOK: el compilador traduce el grafo al kanban.")
