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
import json, os, re, shutil, sqlite3, subprocess, sys
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

RAIZ = Path(__file__).resolve().parent.parent
CHECKPOINTS = RAIZ / "contexto" / "checkpoints.sqlite"
THREAD = "orquester"


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
]


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True,
                          cwd=RAIZ).stdout.strip()


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
        "arbol_limpio": not _git("status", "--porcelain"),
        "sin_pushear": len([x for x in _git("log", "--oneline",
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
    siguiente += [
        # La UI se quedó atrás del motor en tres puntos. No son mejoras: son
        # cosas que el motor sabe hacer y desde el Studio no se pueden.
        "UI: abrir un grafo guardado — Guardar escribe en ui/grafos/ y los "
        "endpoints existen, pero el canvas nunca los usa. Hoy guardás y no podés volver",
        "UI: pedir los valores de los {{parametros}} al Ejecutar — un grafo "
        "parametrizado solo se corre por MCP o por código",
        "UI: elegir el `workspace` por nodo — sin eso el agente no ve tu repo, "
        "y hoy solo se pone editando el JSON",
        "UI (menor): consumo por nodo (ya se calcula, solo se muestra el total), "
        "zoom del lienzo, resultado completo del nodo (la traza recorta a 300)",
        "conectar el Studio a la API multiusuario (hoy le habla directo al motor)",
        "medición empírica del cumplimiento del output_schema (diferida a propósito)",
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
        app = _grafo(SqliteSaver(conn))
        cfg = {"configurable": {"thread_id": THREAD}}

        if argv[1:2] == ["historial"]:
            for i, snap in enumerate(app.get_state_history(cfg)):
                s = snap.values.get("sistema") or {}
                print(f"{i:>3}  {snap.config['configurable'].get('checkpoint_id','?')[:8]}  "
                      f"{s.get('commit','?'):<9} {snap.values.get('fase','?'):<18} "
                      f"bloqueos={len(snap.values.get('bloqueos') or [])}")
            return

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
