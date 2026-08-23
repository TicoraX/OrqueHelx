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
import math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hermes-agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dispatcher"))
import hermes_cli.kanban_db as k
from backends import BACKENDS
from capacidades import faltantes
from backends import ESFUERZO
from loop import carril

RUNTIMES = set(BACKENDS) | {"hermes"}

# Sin literales con escapes: este archivo se edita desde varias herramientas
# y un "\n" se convirtio en un salto de linea real mas de una vez.
SEPARADOR = chr(10) * 2
ENCABEZADO_REGLAS = "## Reglas del flujo" + chr(10)


def es_nota(nodo: dict) -> bool:
    """Un bloque de explicacion en el lienzo. NO se ejecuta ni se compila.

    Existe porque un grafo de diez nodos sin una linea de contexto es
    ilegible dentro de una semana, y meter la explicacion en el goal de un
    nodo se la manda al agente como si fuera parte del trabajo.
    """
    return nodo.get("tipo") == "nota"


def es_gate(nodo: dict) -> bool:
    """Un punto de aprobacion humana (Human-in-the-loop)."""
    return nodo.get("tipo") in ("gate", "aprobacion")


class ErrorDeGrafo(ValueError):
    """El grafo no es compilable. El mensaje va tal cual al canvas."""


def _assignee(nodo: dict) -> str:
    if es_gate(nodo):
        return "human"
    rt = nodo.get("runtime", "hermes")
    if rt not in RUNTIMES:
        raise ErrorDeGrafo(
            f"nodo '{nodo['id']}': runtime '{rt}' desconocido. "
            f"Validos: {sorted(RUNTIMES)}"
        )
    # `hermes` va a un perfil real y lo ejecuta el dispatcher de Hermes.
    # El resto va al carril externo y lo ejecuta el de ORQUESTER (SS12).
    return nodo.get("perfil", "default") if rt == "hermes" else carril(rt)


def validar(grafo: dict, *, capacidades: bool = True) -> None:
    """Todo lo que se puede rechazar sin tocar la base de datos.

    Se valida ANTES de crear nada: una compilacion a medias deja cards
    huerfanas en el board que despues hay que limpiar a mano.

    Dos cosas distintas, y por eso `capacidades` se puede apagar:
      - la **estructura** del grafo (ids, aristas, ciclos) es propiedad del
        grafo y vale igual en cualquier maquina;
      - las **capacidades** son de ESTA maquina. Un grafo estructuralmente
        sano es invalido para correr aca si falta un binario, pero sigue
        siendo un grafo valido para guardar, versionar o mandar a otro lado.
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
        if not es_nota(n):
            _assignee(n)                  # valida el runtime

    if all(es_nota(n) for n in nodos):
        raise ErrorDeGrafo("el grafo es todo notas: no hay nada que ejecutar")

    por_id = {n["id"]: n for n in nodos}
    conocidos = set(ids)
    for arista in grafo.get("aristas") or []:
        if len(arista) != 2:
            raise ErrorDeGrafo(f"arista mal formada: {arista!r}")
        for extremo in arista:
            if extremo not in conocidos:
                raise ErrorDeGrafo(f"arista {arista!r} apunta a un nodo inexistente: '{extremo}'")
        if arista[0] == arista[1]:
            raise ErrorDeGrafo(f"nodo '{arista[0]}': no puede depender de si mismo")
        # Una nota no puede estar en una cadena de dependencias. Se rechaza en
        # vez de ignorarla en silencio: `a -> nota -> b` se veria conectado en
        # el lienzo y al compilar b arrancaria sin esperar a a.
        for extremo in arista:
            if es_nota(por_id[extremo]):
                raise ErrorDeGrafo(
                    f"nodo '{extremo}' es una nota y no puede tener dependencias: "
                    "las notas explican, no se ejecutan")

    _orden_topologico(grafo)              # detecta ciclos con un mensaje util

    # Preflight de capacidades: si un runtime del grafo no esta instalado, se
    # dice ACA y no a los 600s de timeout en medio de una corrida. El agente (o
    # la persona) sabe lo que le falta antes de empezar.
    # El nivel de esfuerzo NO es el mismo en todos: `claude` llega a `max`,
    # `agy` corta en `high`. Un nivel que el CLI no acepta lo haria fallar
    # recien al invocarlo, con la card ya creada y el flujo a medio correr.
    for n in nodos:
        esf = (n.get("esfuerzo") or "").strip()
        rt = n.get("runtime", "hermes")
        if not es_nota(n) and n.get("presupuesto_usd") is not None:
            crudo = n["presupuesto_usd"]
            try:
                # `bool` aparte: `float(True)` es 1.0 y pasaria como un tope de
                # US$ 1 que nadie escribio. `math.isfinite` aparte: `inf` pasa
                # el `> 0` y da un tope que no puede alcanzarse nunca, o sea un
                # tope que se cree puesto y no lo esta (SS10.5).
                if isinstance(crudo, bool):
                    raise ValueError()
                val = float(crudo)
                if not math.isfinite(val) or val <= 0:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ErrorDeGrafo(
                    f"nodo '{n['id']}': presupuesto '{crudo}' invalido "
                    "(debe ser un numero finito > 0)")
        if not es_nota(n) and n.get("herramientas") is not None:
            herr = n["herramientas"]
            validas = {"Read", "Grep", "Glob", "Bash", "Write"}
            # `isinstance(h, str)` primero: `h not in validas` con una lista
            # adentro tira TypeError (unhashable) en vez de ErrorDeGrafo, y eso
            # sale del compilador como un 500 en lugar del mensaje al canvas.
            if not isinstance(herr, (list, tuple, set)) or                     any(not isinstance(h, str) or h not in validas for h in herr):
                raise ErrorDeGrafo(
                    f"nodo '{n['id']}': herramientas '{herr}' invalidas. "
                    f"Validas: {sorted(validas)}")
        if not esf or es_nota(n) or rt not in ESFUERZO:
            continue
        if esf not in ESFUERZO[rt][1]:
            raise ErrorDeGrafo(
                f"nodo '{n['id']}': '{rt}' no acepta esfuerzo '{esf}'. "
                f"Validos: {list(ESFUERZO[rt][1])}")

    ausentes = faltantes({n.get("runtime", "hermes") for n in nodos
                          if not es_nota(n) and not es_gate(n)}) if capacidades else []
    if ausentes:
        raise ErrorDeGrafo(
            f"estos ejecutores no estan disponibles en esta maquina: {ausentes}. "
            f"Instalalos o cambiá el runtime de esos nodos."
        )


def _orden_topologico(grafo: dict) -> list[dict]:
    """Padres antes que hijos: `create_task(parents=...)` exige que ya existan.

    Kahn. El kanban tambien rechaza ciclos, pero su error nombra un solo enlace;
    aca podemos nombrar el conjunto de nodos que quedaron trabados, que es lo
    que el canvas necesita para pintarlos en rojo.
    """
    por_id = {n["id"]: n for n in grafo["nodos"] if not es_nota(n)}
    padres = {i: set() for i in por_id}
    hijos = {i: set() for i in por_id}
    for p, h in grafo.get("aristas") or []:
        if p not in por_id or h not in por_id:
            continue                      # arista de/hacia una nota
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
    # Reglas del flujo: valen para TODOS los nodos y van en el `body`, que
    # `build_worker_context` le entrega al worker junto al goal. En el body y
    # no pegadas al titulo para que el goal siga siendo legible en el lienzo y
    # en el kanban.
    reglas = (grafo.get("reglas") or "").strip()
    ids = {}
    for nodo in _orden_topologico(grafo):
        parents = [ids[p] for p, h in aristas if h == nodo["id"]]
        try:
            ids[nodo["id"]] = k.create_task(
                conn,
                title=nodo["titulo"],
                body=SEPARADOR.join(x for x in
                                  (nodo.get("cuerpo"),
                                   ENCABEZADO_REGLAS + reglas if reglas else None)
                                  if x) or None,
                assignee=_assignee(nodo),
                parents=parents,
                board=board,
                # `workspace` fija donde corre el nodo. Sin el, el scratch de
                # Hermes arranca vacio y el agente no ve el repo.
                **({"workspace_kind": "dir", "workspace_path": nodo["workspace"]}
                   if nodo.get("workspace") else {}),
                # Modelo por nodo. En un nodo `hermes` lo usa el dispatcher de
                # Hermes; en uno externo lo lee nuestro dispatcher y lo pasa
                # como `--model`. Mismo campo, dos consumidores.
                **({"model_override": nodo["modelo"]} if nodo.get("modelo") else {}),
                # `reasoning_effort` ya es un campo de la card en Hermes: en un
                # nodo `hermes` lo aplica su dispatcher, en uno externo lo lee
                # el nuestro y lo pasa como --effort/--variant. Un campo, dos
                # consumidores, igual que el modelo.
                **({"reasoning_effort": nodo["esfuerzo"]} if nodo.get("esfuerzo") else {}),
                # `skills` SOLO en un nodo `claude-code`, que es el unico
                # runtime que aplica los permisos (`--allowedTools` en
                # `backends.run_backend`). En un nodo `hermes` este campo
                # significa otra cosa --carga de contexto para un worker
                # nativo, `loop.py` lo explica-- y meterle `["Read","Bash"]` le
                # pedia a Hermes que cargara skills con esos nombres en una
                # card que SI va a lanzar.
                #
                # ponytail: en `opencode` y `agy` las herramientas del nodo
                # siguen sin aplicarse; sus CLIs no tienen un flag equivalente.
                # Que no viajen es mejor que viajar a un campo que hace otra
                # cosa, pero el hueco sigue: el Studio deja marcarlas y no
                # rigen. Cerrarlo es rechazarlas en `validar`, y eso invalida
                # grafos ya guardados: va aparte.
                **({"skills": list(nodo["herramientas"])}
                   if nodo.get("herramientas") and nodo.get("runtime") == "claude-code"
                   else {}),
                **({"provider_override": nodo["proveedor"]} if nodo.get("proveedor") else {}),
                **({"tenant": f"budget:{nodo['presupuesto_usd']}"} if nodo.get("presupuesto_usd") else {}),
            )
        except ValueError as e:
            # SS4: traducir el error del kanban a algo que el canvas pueda pintar.
            raise ErrorDeGrafo(f"nodo '{nodo['id']}': {e}") from e
    return ids


def analizar(grafo: dict) -> list[dict]:
    """Linter estático de grafos DAG: detecta antipatrones, aristas redundantes y riesgos."""
    hallazgos = []
    nodos = [n for n in (grafo.get("nodos") or []) if not es_nota(n)]
    if not nodos:
        return hallazgos

    ids = {n["id"] for n in nodos}
    aristas = [(p, h) for p, h in (grafo.get("aristas") or []) if p in ids and h in ids]

    padres = {i: set() for i in ids}
    hijos = {i: set() for i in ids}
    for p, h in aristas:
        padres[h].add(p)
        hijos[p].add(h)

    # 1. Nodos aislados en flujos multipaso
    if len(nodos) > 1:
        for n in nodos:
            nid = n["id"]
            if not padres[nid] and not hijos[nid]:
                hallazgos.append({
                    "tipo": "aviso",
                    "codigo": "nodo_aislado",
                    "nodo": nid,
                    "mensaje": f"El nodo '{nid}' está aislado: no tiene dependencias ni dependientes.",
                })

    # 2. Fan-in alto (más de 4 dependencias directas)
    for nid, ps in padres.items():
        if len(ps) > 4:
            hallazgos.append({
                "tipo": "aviso",
                "codigo": "fan_in_alto",
                "nodo": nid,
                "mensaje": f"El nodo '{nid}' espera {len(ps)} dependencias directas concurrentes.",
            })

    # 3. Redundancia transitiva en aristas
    def _alcanzable_indirecto(origen, destino, visitados=None):
        if visitados is None:
            visitados = set()
        visitados.add(origen)
        for inter in hijos.get(origen, set()):
            if inter == destino:
                continue
            if inter not in visitados:
                if destino in hijos.get(inter, set()) or _alcanzable_indirecto(inter, destino, visitados):
                    return True
        return False

    for p, h in aristas:
        if _alcanzable_indirecto(p, h):
            hallazgos.append({
                "tipo": "optimizacion",
                "codigo": "arista_redundante",
                "arista": [p, h],
                "mensaje": f"La arista '{p} -> {h}' es redundante porque existe un camino indirecto alternativo.",
            })

    # 4. Esfuerzo alto sin tope individual
    for n in nodos:
        nid = n["id"]
        esf = n.get("esfuerzo")
        pres = n.get("presupuesto_usd")
        if esf in ("high", "xhigh", "max", "ultra") and not pres:
            hallazgos.append({
                "tipo": "consejo",
                "codigo": "esfuerzo_sin_tope",
                "nodo": nid,
                "mensaje": f"El nodo '{nid}' usa esfuerzo '{esf}' sin tope de gasto individual configurado.",
            })

    # 5. Runtimes no instalados en el entorno
    try:
        ausentes = faltantes({n.get("runtime", "hermes") for n in nodos})
        for n in nodos:
            rt = n.get("runtime", "hermes")
            if rt in ausentes:
                hallazgos.append({
                    "tipo": "advertencia",
                    "codigo": "runtime_no_disponible",
                    "nodo": n["id"],
                    "mensaje": f"El runtime '{rt}' del nodo '{n['id']}' no está instalado en este entorno.",
                })
    except Exception:
        pass

    return hallazgos


def trazabilidad(grafo: dict, nodo_id: str) -> dict:
    """Calcula ancestros, descendientes e impacto de un nodo en el DAG."""
    nodos = [n for n in (grafo.get("nodos") or []) if not es_nota(n)]
    ids = {n["id"] for n in nodos}
    if nodo_id not in ids:
        raise ErrorDeGrafo(f"nodo '{nodo_id}' no encontrado en el grafo")

    aristas = [(p, h) for p, h in (grafo.get("aristas") or []) if p in ids and h in ids]
    padres = {i: set() for i in ids}
    hijos = {i: set() for i in ids}
    for p, h in aristas:
        padres[h].add(p)
        hijos[p].add(h)

    def _recorrer(nid, adyacencias):
        visitados = set()
        pila = list(adyacencias.get(nid, set()))
        while pila:
            if len(visitados) > 10_000:
                raise ValueError("grafo demasiado grande para analizar trazabilidad")
            actual = pila.pop()
            if actual not in visitados:
                visitados.add(actual)
                pila.extend(item for item in adyacencias.get(actual, set()) if item not in visitados)
        return visitados

    ancestros = sorted(list(_recorrer(nodo_id, padres)))
    descendientes = sorted(list(_recorrer(nodo_id, hijos)))
    total_ejecutables = len(nodos)
    impacto_pct = round((len(descendientes) / max(1, total_ejecutables - 1)) * 100, 1) if total_ejecutables > 1 else 0.0

    return {
        "ok": True,
        "nodo": nodo_id,
        "ancestros": ancestros,
        "descendientes": descendientes,
        "padres_directos": sorted(list(padres.get(nodo_id, set()))),
        "hijos_directos": sorted(list(hijos.get(nodo_id, set()))),
        "total_impactados": len(descendientes),
        "impacto_pct": impacto_pct,
    }
