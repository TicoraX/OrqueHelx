"""Compilador: grafo del Studio -> cards del kanban (ARQUITECTURA.md SS4).

No es un motor de workflows, es un mapeo:

    nodo   -> create_task(assignee=<carril o perfil>, parents=[...])
    arista -> el `parents` del hijo
    ciclo  -> lo rechaza el kanban (`_would_cycle`); acá se traduce a un error
              legible para el canvas

Formato del grafo (el que edita la UI):

    {
      "board": "mi-flujo",
      "nodos":   [{"id": "a", "titulo": "...", "runtime": "opencode"},
                  {"id": "b", "titulo": "...", "runtime": "hermes",
                   "perfil": "default"}],
      "aristas": [["a", "b"]]
    }
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hermes-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dispatcher"))
import hermes_cli.kanban_db as k
from backends import BACKENDS
from loop import carril

RUNTIMES = set(BACKENDS) | {"hermes"}


class ErrorDeGrafo(ValueError):
    """El grafo no es compilable. El mensaje va tal cual al canvas."""


def _assignee(nodo: dict) -> str:
    rt = nodo.get("runtime", "hermes")
    if rt not in RUNTIMES:
        raise ErrorDeGrafo(
            f"nodo '{nodo['id']}': runtime '{rt}' desconocido. "
            f"Validos: {sorted(RUNTIMES)}"
        )
    # `hermes` va a un perfil real y lo ejecuta el dispatcher de Hermes.
    # El resto va al carril externo y lo ejecuta el de ORQUESTER (SS12).
    return nodo.get("perfil", "default") if rt == "hermes" else carril(rt)


def validar(grafo: dict) -> None:
    """Todo lo que se puede rechazar sin tocar la base de datos.

    Se valida ANTES de crear nada: una compilacion a medias deja cards
    huerfanas en el board que despues hay que limpiar a mano.
    """
    nodos = grafo.get("nodos") or []
    if not nodos:
        raise ErrorDeGrafo("el grafo no tiene nodos")

    ids = [n.get("id") for n in nodos]
    if any(not i for i in ids):
        raise ErrorDeGrafo("hay nodos sin 'id'")
    repetidos = {i for i in ids if ids.count(i) > 1}
    if repetidos:
        raise ErrorDeGrafo(f"ids repetidos: {sorted(repetidos)}")

    for n in nodos:
        if not (n.get("titulo") or "").strip():
            raise ErrorDeGrafo(f"nodo '{n['id']}': falta 'titulo'")
        _assignee(n)                      # valida el runtime

    conocidos = set(ids)
    for arista in grafo.get("aristas") or []:
        if len(arista) != 2:
            raise ErrorDeGrafo(f"arista mal formada: {arista!r}")
        for extremo in arista:
            if extremo not in conocidos:
                raise ErrorDeGrafo(f"arista {arista!r} apunta a un nodo inexistente: '{extremo}'")
        if arista[0] == arista[1]:
            raise ErrorDeGrafo(f"nodo '{arista[0]}': no puede depender de si mismo")

    _orden_topologico(grafo)              # detecta ciclos con un mensaje util


def _orden_topologico(grafo: dict) -> list[dict]:
    """Padres antes que hijos: `create_task(parents=...)` exige que ya existan.

    Kahn. El kanban tambien rechaza ciclos, pero su error nombra un solo enlace;
    aca podemos nombrar el conjunto de nodos que quedaron trabados, que es lo
    que el canvas necesita para pintarlos en rojo.
    """
    por_id = {n["id"]: n for n in grafo["nodos"]}
    padres = {i: set() for i in por_id}
    hijos = {i: set() for i in por_id}
    for p, h in grafo.get("aristas") or []:
        padres[h].add(p)
        hijos[p].add(h)

    listos = [i for i, ps in padres.items() if not ps]
    orden = []
    while listos:
        i = listos.pop(0)
        orden.append(por_id[i])
        for h in hijos[i]:
            padres[h].discard(i)
            if not padres[h]:
                listos.append(h)

    if len(orden) != len(por_id):
        trabados = sorted(set(por_id) - {n["id"] for n in orden})
        raise ErrorDeGrafo(f"el grafo tiene un ciclo entre: {trabados}")
    return orden


def compilar(grafo: dict, *, board: str = None) -> dict[str, str]:
    """Crear el board y las cards. Devuelve {id_del_nodo: task_id}."""
    validar(grafo)
    board = board or grafo.get("board") or "orquester"
    try:
        k.create_board(board)
    except Exception:
        pass                              # ya existe
    conn = k.connect(board=board)

    aristas = grafo.get("aristas") or []
    ids = {}
    for nodo in _orden_topologico(grafo):
        parents = [ids[p] for p, h in aristas if h == nodo["id"]]
        try:
            ids[nodo["id"]] = k.create_task(
                conn,
                title=nodo["titulo"],
                body=nodo.get("cuerpo"),
                assignee=_assignee(nodo),
                parents=parents,
                board=board,
                # `workspace` fija donde corre el nodo. Sin el, el scratch de
                # Hermes arranca vacio y el agente no ve el repo.
                **({"workspace_kind": "dir", "workspace_path": nodo["workspace"]}
                   if nodo.get("workspace") else {}),
            )
        except ValueError as e:
            # SS4: traducir el error del kanban a algo que el canvas pueda pintar.
            raise ErrorDeGrafo(f"nodo '{nodo['id']}': {e}") from e
    return ids
