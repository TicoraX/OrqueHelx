"""Contexto del proyecto, con checkpoints: retomar desde cualquier lado.

Un grafo de LangGraph cuyo estado **es** el traspaso del proyecto. Cada corrida
sondea el sistema real, deriva qué está bloqueando y escribe `ESTADO.md`.

Por qué LangGraph y no un JSON suelto: el checkpointer guarda **historial por
thread**, no solo el último valor. Se puede ver cómo se movió el proyecto y
retomar desde un punto anterior. Un JSON solo tiene el presente.

Por qué **no** es un motor: ORQUESTER ya tiene uno prestado (ARQUITECTURA.md
§1) y adoptar un segundo contradiría la decisión central. Esto no ejecuta
flujos de usuario: modela el contexto del proyecto y nada más.

    uv run --python 3.11 --with langgraph --with langgraph-checkpoint-sqlite \\
      python contexto/estado.py            # sondea, escribe ESTADO.md
    ... python contexto/estado.py historial   # los checkpoints guardados
"""
import json, os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

RAIZ = Path(__file__).resolve().parent.parent
CHECKPOINTS = RAIZ / "contexto" / "checkpoints.sqlite"

# Un thread POR CORRIDA, no uno fijo. Con un thread unico, reinvocar un grafo
# que ya llego a END devuelve el estado cacheado sin volver a sondear: el
# traspaso quedaba congelado en la primera corrida y mentia en silencio.
# El historial se arma recorriendo los threads, no los checkpoints de uno.
THREAD_PREFIJO = "orquester"


def _reemplazar(_viejo, nuevo):
    """Reductor: el sondeo nuevo reemplaza al anterior, no lo acumula."""
    return nuevo


class Contexto(TypedDict, total=False):
    fase: str
    sistema: Annotated[dict, _reemplazar]
    bloqueos: Annotated[list, _reemplazar]
    siguiente: Annotated[list, _reemplazar]
    nota: str


# --- Lo que NO se sondea: decisiones tomadas. Son el contexto que un agente
# nuevo no puede deducir del código, y el que más caro sale re-litigar.
DECISIONES = [
    ("Motor prestado, sin fork", "§1", "Hermes se pinea por versión; el clon está "
     "en .gitignore y su working tree se verifica limpio."),
    ("El kanban es el único scheduler", "§12", "ORQUESTER ejecuta nodos externos "
     "pero no programa nada: es worker, no motor."),
    ("El dispatcher externo va en Python", "§12", "Usa kanban_db como librería. "
     "Reescribir el protocolo de claim en TS es el riesgo que §10 documenta."),
    ("El runtime del nodo va en el assignee", "§12", "`orquester-external:<rt>`. "
     "NO en `skills` (es contexto, no selector) ni en `metadata` (no existe al crear)."),
    ("Dos bases, dos dueños", "§10.2", "Postgres: diseño y gobierno. "
     "kanban SQLite: ejecución. No es duplicación."),
    ("Las versiones de grafo son inmutables", "§10.2", "Editar crea la siguiente. "
     "Una corrida vieja tiene que explicarse con el grafo que se ejecutó."),
    ("Nada de proveedores external_process", "§11", "Con `copilot` todo agente "
     "hijo se cuelga para siempre en `Initializing agent...`."),

    # --- Studio: decisiones tomadas construyendo el producto ---
    ("Plantillas y grafos son carpetas distintas", "TUTORIAL §5b",
     "`plantillas/` va en el repo y es de solo lectura; usar una la COPIA a "
     "`ui/grafos/`. Si fueran el mismo lugar, un `git pull` que mejore una "
     "plantilla pisaría el trabajo hecho encima."),
    ("Lo que una plantilla requiere se DERIVA de sus nodos", "TUTORIAL §5b",
     "No se declara aparte: una lista escrita a mano se desincroniza el primer "
     "día que alguien cambia un ejecutor."),
    ("Una nota no puede tener dependencias", "compiler/compile.py",
     "El compilador RECHAZA la arista en vez de ignorarla: `a → nota → b` se "
     "vería conectado en el lienzo y `b` arrancaría sin esperar a `a`. Un error "
     "visible es mejor que un DAG que miente."),
    ("Las reglas del flujo van en el `body`, no en el título", "compiler/compile.py",
     "`build_worker_context` entrega el body junto al goal, y así el título "
     "sigue siendo legible en el lienzo y en el kanban."),
    ("El esfuerzo no necesitó campo nuevo", "§10.5",
     "`reasoning_effort` ya existe en la card de Hermes, igual que "
     "`model_override`. Un campo, dos consumidores: su dispatcher y el nuestro."),
    ("Los niveles de esfuerzo se validan en el compilador", "§10.5",
     "No son iguales en los tres CLIs (`max` no existe en agy). Pedir un nivel "
     "y que corra en el default es pagar por trabajo que no se pidió."),
    ("Un tope de gasto NUNCA se simula", "§10.5",
     "Solo se pasa al CLI que lo entiende (claude-code). En los demás corta el "
     "dispatcher ENTRE nodos: lo que ya arrancó termina. Un tope que se cree "
     "puesto y no lo está es peor que no tener tope."),
    ("El Studio no persiste conversaciones", "§10.4",
     "La sesión del chat la guarda el CLI; por la API viaja solo el id. Una "
     "sesión por runtime: el id de claude no significa nada para opencode."),
    ("El chat pasa por `loop.run_chat`", "§10.4",
     "Concede las herramientas del carril y hereda la denylist: un chat es un "
     "agente con shell igual que un nodo."),
    ("El acomodo del grafo se calcula en Python", "compiler/disposicion.py",
     "El orden por capas (Kahn) ya existe en el compilador. Reimplementarlo en "
     "JS sería el mismo algoritmo dos veces y la segunda copia se desincroniza."),
    ("El nombre de archivo va por lista blanca", "ui/server.py",
     "Un `board: '../../x'` escribía .json fuera de ui/grafos y leía cualquier "
     ".json del disco. Explotado antes de arreglarlo; hay un test que lo intenta."),
    ("La suite de Python NO verifica la UI", "tests/",
     "Ningún test toca `ui/index.html`: los cambios de interfaz se verifican con "
     "Playwright en el navegador. Y `test_api_rbac` sale con código 0 cuando se "
     "omite por falta de Postgres, así que un runner que mire el exit code "
     "cuenta un test omitido como pasado."),
]


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True,
                          cwd=RAIZ).stdout.strip()


def _upstream() -> str:
    """El remoto que sigue esta rama, o `origin/master` si no sigue a ninguno.

    Una rama recien creada no tiene upstream, y ahi `origin/master` es la
    comparacion correcta: todo lo que tenga encima esta, efectivamente, sin
    pushear a ningun lado.
    """
    return _git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                "@{upstream}") or "origin/master"


def sondear(_: Contexto) -> Contexto:
    """Mirar el sistema real. Nada de esto se escribe a mano: se mide."""
    pin = dict(l.split("=", 1) for l in
               (RAIZ / "HERMES_PIN").read_text(encoding="utf-8").splitlines()
               if "=" in l and not l.startswith("#"))
    arq = (RAIZ / "ARQUITECTURA.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+) afirmaciones, (\d+) pendiente", arq)
    clon = _git("-C", str(RAIZ / "hermes-agent"), "rev-parse", "HEAD")

    return {"sistema": {
        "commit": _git("rev-parse", "--short", "HEAD"),
        "mensaje": _git("log", "-1", "--format=%s"),
        "remoto": _git("remote", "get-url", "origin") or "(sin remoto)",
        # `ESTADO.md` se excluye: lo acaba de escribir este mismo grafo, asi que
        # sin esto el traspaso siempre se reporta sucio por su propia causa.
        "arbol_limpio": not [l for l in _git("status", "--porcelain").splitlines()
                             if l and "ESTADO.md" not in l],
        # Contra el upstream de ESTA rama, no contra `origin/master` fijo. Con
        # master fijo, en una rama de trabajo esto mide "sin MERGEAR" y reporta
        # un bloqueo que no existe: catorce commits pusheados y en su PR se
        # anunciaban como catorce sin pushear.
        "sin_pushear": len([x for x in _git("log", "--oneline",
                                            f"{_upstream()}..HEAD").splitlines() if x]),
        "sin_mergear": len([x for x in _git("log", "--oneline",
                                            "origin/master..HEAD").splitlines() if x]),
        "afirmaciones": int(m.group(1)) if m else 0,
        "pendientes_10": int(m.group(2)) if m else 0,
        "tests": sorted(p.name for p in (RAIZ / "tests").glob("test_*.py")),
        "pin_hermes": pin.get("commit", "?")[:12],
        "clon_en_el_pin": clon.startswith(pin.get("commit", "x")[:12]),
        "cli_hermes": pin.get("cli", "?"),
        "binarios": {n: bool(shutil.which(n))
                     for n in ("claude", "opencode", "agy", "hermes", "node", "docker")},
        "postgres": _postgres_arriba(),
    }}


def _postgres_arriba() -> bool:
    """El compose del plano de control, si docker está."""
    if not shutil.which("docker"):
        return False
    salida = subprocess.run(["docker", "compose", "ps", "--format", "{{.Service}} {{.Status}}"],
                            capture_output=True, text=True, cwd=RAIZ).stdout
    return "db" in salida and "healthy" in salida


def revisar(estado: Contexto) -> Contexto:
    """Derivar qué frena y qué sigue. La lógica vive acá, no en la cabeza de nadie."""
    s = estado["sistema"]
    bloqueos, siguiente = [], []

    if not s["arbol_limpio"]:
        bloqueos.append("hay cambios sin commitear")
    if s["sin_pushear"]:
        bloqueos.append(f"{s['sin_pushear']} commit(s) sin pushear")
    elif s["sin_mergear"]:
        # No es un bloqueo: es una rama de trabajo con su PR abierto.
        siguiente.append(f"{s['sin_mergear']} commit(s) pusheados y sin mergear "
                         f"a master (rama de trabajo)")
    if not s["clon_en_el_pin"]:
        bloqueos.append("el clon de Hermes no está en el commit del pin: "
                        "revalidar la suite o volver al pin")
    faltan = [n for n, hay in s["binarios"].items() if not hay]
    if faltan:
        bloqueos.append(f"binarios ausentes: {', '.join(faltan)} "
                        "(los nodos de ese runtime van a fallar como `capability`)")
    if not s["postgres"]:
        siguiente.append("`docker compose up -d db` si vas a usar multiusuario")

    if s["pendientes_10"]:
        siguiente.append(f"§10 tiene {s['pendientes_10']} afirmación(es) pendiente(s)")
    # OJO: esta lista está escrita a mano dentro de un archivo cuyo docstring
    # dice "Generado, no escrito: un traspaso a mano miente". Ya mintió: durante
    # semanas anunció como pendientes dos cosas que estaban construidas.
    #   - "chat que DISEÑA el grafo": `/api/generar-grafo` existía y llamaba a un
    #     agente, pero leía `res["respuesta"]` y el chat devuelve `res["texto"]`,
    #     así que caía SIEMPRE a una plantilla fija. Estaba roto, no ausente.
    #   - "presupuesto por NODO": `compile.py` ya escribía `tenant=budget:N`,
    #     `loop.ejecutar_una` ya hacía `min(global, nodo)` y
    #     `test_esfuerzo_presupuesto` ya lo cubría. Estaba hecho.
    # Antes de agregar algo acá: buscarlo en el código. Y antes de tacharlo,
    # también.
    siguiente += [
        # La expansión del Studio está cerrada: abrir un grafo guardado, pasarle
        # parámetros, elegir workspace, parar una corrida, zoom/paneo, deshacer,
        # resultado completo y gasto por nodo. Servidor en
        # `tests/test_ui_expansion.py`; lo del lienzo, con Playwright.
        # El Studio está completo para uso local: plantillas, chat, esfuerzo,
        # tope de gasto, auto-layout y atajos. Lo que queda es producto, no UI.
        "que el grafo diseñado por el agente se pueda EDITAR y re-pedir en el "
        "lienzo (generarlo ya anda; hoy cada intento pisa el anterior)",
        "conectar el Studio a la API multiusuario (hoy le habla directo al motor)",
        "medición empírica del cumplimiento del output_schema (diferida a propósito)",
        "tabla de rutas en ui/server.py: 34 endpoints en dos cadenas de `if`",
    ]
    return {"bloqueos": bloqueos, "siguiente": siguiente,
            "fase": "producto en uso" if not bloqueos else "con pendientes"}


def escribir(estado: Contexto) -> Contexto:
    """Renderizar ESTADO.md. Generado, no escrito: un traspaso a mano miente."""
    s = estado["sistema"]
    si = lambda b: "sí" if b else "**no**"
    lineas = [
        "# Estado de ORQUESTER",
        "",
        "> Generado por `contexto/estado.py`. **No editar a mano**: se sobrescribe.",
        "> Un traspaso escrito a mano queda viejo y nadie se entera.",
        "",
        f"**Fase:** {estado['fase']}",
        "",
        "## Dónde está el código",
        "",
        f"- Commit `{s['commit']}` — {s['mensaje']}",
        f"- Remoto: {s['remoto']}",
        f"- Árbol limpio: {si(s['arbol_limpio'])} · sin pushear: {s['sin_pushear']}",
        "",
        "## Runtime prestado",
        "",
        f"- Pin de Hermes: `{s['pin_hermes']}` · el clon está en el pin: {si(s['clon_en_el_pin'])}",
        f"- CLI instalado: {s['cli_hermes']}",
        f"- Binarios: " + ", ".join(f"{n} {'✓' if hay else '✗'}"
                                   for n, hay in s["binarios"].items()),
        f"- Postgres del plano de control: {si(s['postgres'])}",
        "",
        "## Verificación",
        "",
        f"- `ARQUITECTURA.md` §10: **{s['afirmaciones']} afirmaciones**, "
        f"{s['pendientes_10']} pendiente(s)",
        f"- {len(s['tests'])} tests: " + ", ".join(t.replace('test_', '').replace('.py', '')
                                                   for t in s["tests"]),
        "",
        "## Decisiones tomadas (no re-litigar sin motivo nuevo)",
        "",
    ]
    lineas += [f"- **{t}** ({ref}) — {por}" for t, ref, por in DECISIONES]
    lineas += ["", "## Bloqueos", ""]
    lineas += [f"- {b}" for b in estado["bloqueos"]] or ["- ninguno"]
    lineas += ["", "## Qué sigue", ""]
    lineas += [f"- {x}" for x in estado["siguiente"]]
    lineas += [
        "",
        "## Cómo retomar",
        "",
        "```bash",
        "uv run --python 3.11 --with langgraph --with langgraph-checkpoint-sqlite \\",
        "  python contexto/estado.py            # regenerar este archivo",
        "sh tests/linux.sh                      # la suite en Linux",
        "cat TUTORIAL.md                        # cómo se usa el producto",
        "```",
        "",
        "El historial de este contexto está en los checkpoints de LangGraph:",
        "`python contexto/estado.py historial`.",
        "",
    ]
    (RAIZ / "ESTADO.md").write_text("\n".join(lineas), encoding="utf-8")
    return {"nota": f"ESTADO.md escrito ({len(lineas)} lineas)"}


def _grafo(saver):
    g = StateGraph(Contexto)
    g.add_node("sondear", sondear)
    g.add_node("revisar", revisar)
    g.add_node("escribir", escribir)
    g.add_edge(START, "sondear")
    g.add_edge("sondear", "revisar")
    g.add_edge("revisar", "escribir")
    g.add_edge("escribir", END)
    return g.compile(checkpointer=saver)


def main(argv: list[str]) -> None:
    CHECKPOINTS.parent.mkdir(exist_ok=True)
    # `check_same_thread=False`: el saver puede leerse desde otro hilo que el
    # que abrio la conexion, igual que el resto del proyecto con SQLite.
    with sqlite3.connect(CHECKPOINTS, check_same_thread=False) as conn:
        saver = SqliteSaver(conn)
        app = _grafo(saver)

        if argv[1:2] == ["historial"]:
            vistos = set()
            for cp in saver.list(None):                 # todos los threads
                hilo = cp.config["configurable"]["thread_id"]
                v = cp.checkpoint.get("channel_values") or {}
                if hilo in vistos or "sistema" not in v:
                    continue                            # solo el estado final de cada corrida
                vistos.add(hilo)
                s = v["sistema"]
                print(f"{hilo:<28} {s.get('commit','?'):<9} "
                      f"{v.get('fase','?'):<18} bloqueos={len(v.get('bloqueos') or [])}")
            print(f"\n{len(vistos)} corrida(s) guardadas")
            return

        cfg = {"configurable": {"thread_id": f"{THREAD_PREFIJO}-{int(time.time())}"}}
        final = app.invoke({}, cfg)
        s = final["sistema"]
        print(f"fase        : {final['fase']}")
        print(f"commit      : {s['commit']} — {s['mensaje'][:50]}")
        print(f"§10         : {s['afirmaciones']} afirmaciones, {s['pendientes_10']} pendiente(s)")
        print(f"tests       : {len(s['tests'])}")
        print(f"pin de clon : {'en el pin' if s['clon_en_el_pin'] else 'MOVIDO del pin'}")
        for b in final["bloqueos"]:
            print(f"  bloqueo   : {b}")
        print(final["nota"])


if __name__ == "__main__":
    main(sys.argv)
