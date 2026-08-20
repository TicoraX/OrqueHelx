"""Studio de ORQUESTER: servidor local, sin dependencias.

`http.server` de la stdlib y un HTML de un archivo. Sin npm, sin build, sin
framework: la UI es un canvas SVG y unas pocas llamadas fetch. Cuando el
producto necesite RBAC, multiusuario y persistencia en Postgres, esto se
reemplaza por el backend NestJS de SS7 — hasta entonces es andamiaje que nadie
pidio.

    uv run --python 3.11 --with jsonschema python ui/server.py
    -> http://127.0.0.1:8765
"""
import hmac, json, os, re, secrets, subprocess, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "dispatcher", "compiler", "mcp_exporter"):
    sys.path.insert(0, str(RAIZ / sub))

import hermes_cli.kanban_db as k
import compile as compilador
import disposicion
import loop as dispatcher
import exportar as mcp
import capacidades

HTML = Path(__file__).parent / "index.html"
GRAFOS = RAIZ / "ui" / "grafos"
GRAFOS.mkdir(exist_ok=True)
SNAPSHOTS = GRAFOS / "snapshots"
SNAPSHOTS.mkdir(exist_ok=True)

# Plantillas: versionadas en el repo y de SOLO LECTURA desde el Studio. Usar
# una la copia a `ui/grafos/`. Si fueran el mismo lugar, editar una plantilla y
# despues hacer `git pull` te pisaria el trabajo: por eso dos carpetas con
# dueños distintos.
PLANTILLAS = RAIZ / "plantillas"


def _ruta_segura(nombre: str, carpeta: Path, que: str = "nombre") -> Path:
    """La ruta `<carpeta>/<nombre>.json`, garantizada dentro de `carpeta`.

    El nombre lo elige quien manda el pedido y terminaba en un `Path` sin
    filtrar: con `board: "../../x"` se escribia un .json en cualquier lado del
    disco, y se leia cualquier .json existente. Verificado explotandolo.

    Filtrar solo `..` NO alcanza, y por eso esto vive en un lugar solo: una
    ruta ABSOLUTA no tiene `..` y se escapa igual, porque unir una carpeta con
    una ruta absoluta devuelve la absoluta y descarta la carpeta. El snapshot
    se guardaba con ese unico filtro y escribia donde le pidieran; tambien
    verificado explotandolo.
    """
    limpio = (nombre or "").strip()
    if not limpio or set(limpio) <= {"."}:
        raise ValueError(f"{que} vacio")
    if ".." in limpio or not re.fullmatch(r"[\w .-]+", limpio, re.UNICODE):
        raise ValueError(f"{que} invalido: {nombre!r} "
                         "(solo letras, numeros, guiones y puntos)")
    f = (carpeta / f"{limpio}.json").resolve()
    # Cinturon y tiradores: aunque la validacion de arriba se afloje, el
    # archivo TIENE que quedar dentro de la carpeta que se pidio.
    if f.parent != carpeta.resolve():
        raise ValueError(f"{que} invalido: {nombre!r}")
    return f


def _archivo(nombre: str) -> Path:
    """La ruta del grafo `nombre`, garantizada dentro de GRAFOS."""
    return _ruta_segura(nombre, GRAFOS, "nombre de grafo")


def _slug_yaml(s) -> str:
    """Reducir un valor del grafo a algo que no pueda cerrar el texto generado.

    Los exportadores (CI, mermaid) arman texto pegando ids y runtimes que
    vienen del pedido. Un id con comillas y saltos de linea inyectaba pasos
    propios en el workflow de GitHub Actions; el saneo existia para la clave
    del job y no se usaba en el resto del mismo archivo.
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(s))

# board -> hilo del dispatcher. Un solo dispatcher por board a la vez: dos
# reclamarian las mismas cards y, aunque `claim_task` lo resuelve sin corromper
# nada, es trabajo duplicado sin motivo.
_corriendo: dict[str, threading.Thread] = {}

# Boards a los que se les pidio parar. No se mata nada a mitad de camino: un
# `kill` dejaria la card reclamada y el proceso hijo huerfano. Se deja de
# LEVANTAR trabajo nuevo, y lo que ya arranco termina y se cierra bien.
_parar: set[str] = set()

# board -> tope de gasto con el que se arranco. El Studio tiene que mostrar el
# que se esta APLICANDO, no el que hay tipeado en el campo: editar el campo con
# una corrida en marcha no cambia el tope de esa corrida, y la barra mostraba
# un techo que nadie estaba respetando.
_topes: dict[str, float] = {}

# Token de acceso. Esto ejecuta agentes con shell: sin autenticacion, exponer el
# puerto es entregar una terminal. Se genera uno por arranque salvo que se fije
# `ORQUESTER_TOKEN` (util para dejarlo estable entre reinicios).
TOKEN = os.environ.get("ORQUESTER_TOKEN") or secrets.token_urlsafe(24)

# Nombres aceptados en la cabecera `Host`. Un `http.server` escuchando en
# 127.0.0.1 sin esta comprobacion es vulnerable a DNS rebinding: una pagina
# cualquiera hace que su dominio resuelva a 127.0.0.1 y desde ese momento le
# habla al Studio como same-origin, saltandose el navegador. El token frena lo
# que lo exige, pero `/api/capacidades` va SIN token y publica la ruta en disco
# de cada binario instalado.
# Si `ORQUESTER_HOST` abre el puerto a la red, quien lo abrio sabe por que
# nombres se llega: los declara en `ORQUESTER_HOSTS`, separados por coma.
HOSTS_OK = {"127.0.0.1", "localhost", "::1", ""} | {
    h.strip().lower() for h in (os.environ.get("ORQUESTER_HOSTS") or "").split(",")
    if h.strip()
} | ({os.environ["ORQUESTER_HOST"].lower()} if os.environ.get("ORQUESTER_HOST") else set())


def _query(ruta_con_query: str) -> dict:
    """Los parametros de la URL, decodificados. UNA sola vez, para todos.

    `parse_qsl` y no un `split("=")` a mano: sin decodificar, un grafo llamado
    `mi flujo` (nombre que `_archivo` acepta) llegaba como `mi%20flujo` y era
    inabrible. Y `parse_qsl` y no `parse_qs`, que devuelve listas: un
    `board=["x"]` termina en `k.connect` y revienta adentro.

    Vive en un lugar solo porque habia DOS parsers de query — este y el de
    `_token_ok` — y arreglar uno dejaba el otro igual de roto.
    """
    _, _, query = ruta_con_query.partition("?")
    return dict(parse_qsl(query, keep_blank_values=True))


def _token_ok(handler) -> bool:
    """Comparacion en tiempo constante: un `==` filtra el token por timing."""
    dado = handler.headers.get("X-Orquester-Token") or _query(handler.path).get("token", "")
    # En bytes, no en str: `compare_digest` sobre strings LANZA TypeError si hay
    # un caracter no ASCII, y desde que el query se decodifica un `?token=%C3%B1`
    # llega como `ñ`. Sin esto, cualquiera sin autenticar tira el pedido abajo.
    return hmac.compare_digest(dado.encode("utf-8", "surrogatepass"), TOKEN.encode())


def _catalogo() -> dict:
    """Las plantillas del repo, con lo que hace falta para correr cada una.

    `requiere` se DERIVA de los nodos en vez de declararse: una lista escrita a
    mano se desincroniza del grafo la primera vez que alguien cambia un nodo.
    """
    salida = []
    for f in sorted(PLANTILLAS.glob("*.json")):
        try:
            g = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            salida.append({"nombre": f.stem, "error": f"no se pudo leer: {e}"})
            continue
        nodos = g.get("nodos") or []
        # Las notas no se ejecutan: contarlas como nodos o mirarles el runtime
        # haria que el catalogo pida un binario que nadie va a usar.
        ejecutables = [n for n in nodos if n.get("tipo") != "nota"]
        runtimes = sorted({n.get("runtime", "hermes") for n in ejecutables})
        salida.append({
            "nombre": f.stem,
            "descripcion": g.get("descripcion", ""),
            "nodos": len(ejecutables),
            "notas": len(nodos) - len(ejecutables),
            "reglas": bool((g.get("reglas") or "").strip()),
            "runtimes": runtimes,
            "parametros": mcp.parametros(g),
            # Se avisa ACA lo que falta, no a los 600s de una corrida.
            "faltan": capacidades.faltantes(runtimes),
        })
    return {"plantillas": salida}


def _generar_mermaid(grafo: dict) -> str:
    """Exportar el grafo como diagrama Mermaid con estilos semánticos."""
    nodos = grafo.get("nodos") or []
    aristas = grafo.get("aristas") or []
    board = grafo.get("board") or "flujo"

    # Mismo criterio que en el exportador de CI: el id que viene del grafo se
    # sanea antes de entrar al diagrama, no se pega crudo.
    lineas = ["graph TD", f"    subgraph {_slug_yaml(board)} [{_slug_yaml(board)}]"]
    for n in nodos:
        nid = _slug_yaml(n["id"])
        titulo = (n.get("titulo") or nid).replace('"', "'").replace("\n", " ")
        if len(titulo) > 50:
            titulo = titulo[:47] + "..."
        if n.get("tipo") == "nota":
            lineas.append(f'        {nid}["📝 {titulo}"]:::nota')
        else:
            rt = _slug_yaml(n.get("runtime", "hermes"))
            lineas.append(f'        {nid}["{titulo}<br/><i>({rt})</i>"]:::{rt.replace("-", "_")}')

    for p, h in aristas:
        lineas.append(f"        {_slug_yaml(p)} --> {_slug_yaml(h)}")
    lineas.append("    end")
    lineas.append("    classDef hermes fill:#8e9aab,stroke:#6f7b8c,color:#14161a;")
    lineas.append("    classDef claude_code fill:#d0873f,stroke:#a66629,color:#ffffff;")
    lineas.append("    classDef opencode fill:#4f9c8a,stroke:#347063,color:#ffffff;")
    lineas.append("    classDef antigravity fill:#a297fc,stroke:#7b6ee0,color:#14161a;")
    lineas.append("    classDef nota fill:#232833,stroke:#59616f,color:#e6e8ec,stroke-dasharray: 4 4;")
    return "\n".join(lineas)


def _runtime_para_chatear(runtime: str) -> str | None:
    """El runtime pedido si esta instalado, si no el primero que lo este.

    Lo comparten el optimizador de goals y el generador de grafos: los dos
    necesitan CUALQUIER ejecutor con el que hablar, no uno en particular.
    """
    t = capacidades.tabla()
    if runtime in t and t[runtime]["disponible"] and runtime in dispatcher.BACKENDS:
        return runtime
    return next((c for c in ("opencode", "claude-code", "antigravity")
                 if c in t and t[c]["disponible"]), None)


def _goal_estructurado(goal: str, marcadores: list, reglas: str) -> str:
    """El goal ordenado a mano, sin agente. Es el fallback, no el camino feliz."""
    partes = [f"Objetivo: {goal}"]
    if marcadores:
        partes.append(f"Parametros requeridos: {', '.join('{{' + m + '}}' for m in marcadores)}.")
    partes.append("Restricciones: No inventar archivos ni datos de prueba. Reportar claramente los hallazgos.")
    partes.append("Formato de salida: Estructurado y conciso.")
    if reglas:
        partes.append(f"Reglas: {reglas.strip()}")
    return "\n\n".join(partes)


def _optimizar_goal(goal: str, runtime: str = "claude-code", reglas: str = "",
                    dry_run: bool = False) -> dict:
    """Optimizar un goal in-place aplicando tecnicas de prompting.

    Devuelve `{"optimizado", "degradado", "motivo"}`. `degradado` NO es adorno:
    esta funcion leia `res["respuesta"]` y el chat devuelve `res["texto"]`, asi
    que durante toda su vida devolvio el goal sin tocar y el Studio anuncio
    "Goal optimizado con exito". Un fallback que no se declara es una feature
    muerta que se ve viva.
    """
    goal_limpio = (goal or "").strip()
    if not goal_limpio:
        raise ValueError("el goal no puede estar vacio")

    marcadores = re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", goal_limpio)
    plano = lambda motivo: {"optimizado": _goal_estructurado(goal_limpio, marcadores, reglas),
                            "degradado": True, "motivo": motivo}
    if dry_run:
        return plano("dry_run: no se invoco ningun agente")

    rt_elegido = _runtime_para_chatear(runtime)
    if not rt_elegido:
        return plano("no hay ningun ejecutor instalado en esta maquina")

    prompt = (
        "Eres un optimizador senior de prompts para agentes de IA autonomos (ORQUESTER).\n"
        "Optimiza el siguiente objetivo (goal) de un nodo para que el agente ejecute con maxima precision y sin alucinar.\n"
        "Reglas obligatorias:\n"
        "1. Manten la intencion del usuario pero hazla especifica, directa y accionable.\n"
        "2. Delimita explicitamente el entregable esperado y su formato.\n"
        "3. Incluye restricciones negativas (ej. no inventar datos de prueba, no modificar archivos fuera del alcance).\n"
        "4. Si el goal original contiene variables con {{marcadores}}, DEBES mantenerlas EXACTAMENTE igual.\n"
        "5. Devuelve UNICAMENTE el texto optimizado del goal, sin preambulos, sin comillas envolventes ni explicaciones adicionales.\n\n"
        f"Goal original:\n{goal_limpio}\n"
        + (f"\nReglas globales del flujo:\n{reglas.strip()}\n" if reglas else "")
    )

    try:
        # `texto`, no `respuesta`: es la clave que devuelve `chat_backend`.
        respuesta = (dispatcher.run_chat(rt_elegido, prompt, timeout=90)
                     .get("texto") or "").strip()
    except Exception as e:
        return plano(f"{rt_elegido} fallo: {type(e).__name__}: {str(e)[:200]}")
    if respuesta.startswith("```"):
        lineas = respuesta.splitlines()
        if len(lineas) >= 2 and lineas[-1].startswith("```"):
            respuesta = "\n".join(lineas[1:-1]).strip()
    if not respuesta:
        return plano(f"{rt_elegido} no devolvio texto")
    return {"optimizado": respuesta, "degradado": False, "motivo": ""}


def _conn(board: str):
    """Conexion al board, rechazando el que no existe.

    `k.connect` hace `mkdir(parents=True)` y auto-inicializa el esquema: pedirle
    un board inventado no falla, lo CREA. Por eso los `try/except` que
    envolvian estas llamadas no se disparaban nunca y cada nombre tipeado mal
    dejaba una base nueva en disco, respondiendo 200 sobre un board vacio.
    `board_exists` es la pregunta que si contesta.
    """
    if not k.board_exists(board):
        raise ValueError(f"no existe el board '{board}'")
    return k.connect(board=board)


def _estado(board: str) -> dict:
    """Estado de todas las cards del board, para pintar el canvas."""
    try:
        conn = _conn(board)
    except Exception as e:
        return {"error": str(e), "tareas": {}}
    tareas = {}
    for t in k.list_tasks(conn):
        tareas[t.id] = {
            "titulo": t.title,
            "estado": t.status,
            "assignee": t.assignee,
            "resumen": (getattr(t, "result", None) or "")[:400],
        }
    return {"tareas": tareas, "corriendo": board in _corriendo}


def _traza(board: str, task_id: str) -> dict:
    """Traza de un nodo: sus intentos y sus eventos.

    Es la "observabilidad como grafo" de IDEAS §1: en vez de traducir un log a
    un grafo mental, se lee la traza del nodo en el mismo dibujo donde se
    diseñó el flujo.
    """
    conn = _conn(board)
    t = k.get_task(conn, task_id)
    if t is None:
        return {"error": f"no existe la card {task_id}"}
    # Ni `resultado` ni los summaries se recortan: el resultado de un nodo es
    # el ENTREGABLE del flujo, y leerlo a medias obliga a abrir la base a mano.
    # Los `payload` de los eventos si: traen PID y ruido de reclaim.
    eventos = [
        {"kind": e.kind, "cuando": e.created_at, "run": e.run_id,
         # El payload trae PID, motivo del reclaim, error... util y a veces
         # enorme: se recorta acá y no en el navegador.
         "detalle": json.dumps(e.payload, ensure_ascii=False)[:220] if e.payload else ""}
        for e in k.list_events(conn, task_id)
    ]
    intentos = [
        {"n": i + 1, "outcome": r.outcome, "resumen": r.summary or "",
         "error": getattr(r, "error", None) or "",
         "inicio": r.started_at, "fin": r.ended_at}
        for i, r in enumerate(k.list_runs(conn, task_id))
    ]
    return {
        "titulo": t.title, "estado": t.status, "assignee": t.assignee,
        "block_kind": t.block_kind, "resultado": t.result or "",
        "intentos": intentos, "eventos": eventos,
    }


def _consumo(board: str) -> dict:
    """Consumo del board: por nodo y agregado.

    Se suma sobre los **runs**, no sobre las tasks: un nodo reintentado gasto en
    cada intento, y el total del flujo tiene que reflejarlo.
    """
    try:
        conn = _conn(board)
    except Exception as e:
        return {"error": str(e)}
    # El tope de la corrida, si hubo una. Se devuelve aunque ya haya terminado:
    # despues de correr, lo que importa es contra que techo se gasto.
    tope = _topes.get(board)
    total = {"entrada": 0, "salida": 0, "total": 0, "cache_lectura": 0,
             "costo_usd": 0.0, "intentos": 0, "con_costo": 0, "sin_costo": 0}
    por_nodo = {}
    for t in k.list_tasks(conn):
        acum = None
        for r in k.list_runs(conn, t.id):
            u = (r.metadata or {}).get("uso")
            if not u:
                continue
            acum = acum or {"entrada": 0, "salida": 0, "total": 0,
                            "cache_lectura": 0, "costo_usd": None,
                            "runtime": u.get("runtime"), "intentos": 0}
            for campo in ("entrada", "salida", "total", "cache_lectura"):
                acum[campo] += u.get(campo) or 0
                total[campo] += u.get(campo) or 0
            acum["intentos"] += 1
            total["intentos"] += 1
            if u.get("costo_usd") is not None:
                acum["costo_usd"] = (acum["costo_usd"] or 0) + u["costo_usd"]
                total["costo_usd"] += u["costo_usd"]
                total["con_costo"] += 1
            else:
                # Sin costo NO es cero: es un backend que corre por suscripcion
                # y no informa medidor. Contarlo como 0 mentiria el promedio.
                total["sin_costo"] += 1
        if acum:
            por_nodo[t.id] = acum
    return {"total": total, "por_nodo": por_nodo, "tope_usd": tope,
            "corriendo": board in _corriendo}


def _telemetria(board: str) -> dict:
    """Métricas y telemetría de ejecución en tiempo real para el board."""
    try:
        conn = _conn(board)
        tasks = k.list_tasks(conn)
    except Exception as e:
        return {"board": board, "error": str(e), "corriendo": board in _corriendo,
                "resumen": {"total": 0, "terminados": 0, "fallidos": 0, "activos": 0, "listos": 0, "progreso_pct": 0.0, "estados": {}},
                "consumo": _consumo(board), "nodos": []}

    estados = {}
    nodos_info = []
    consumo_data = _consumo(board)

    for t in tasks:
        st = t.status or "todo"
        estados[st] = estados.get(st, 0) + 1
        runs = k.list_runs(conn, t.id)
        duracion_s = 0.0
        for r in runs:
            if r.started_at and r.ended_at:
                duracion_s += max(0, r.ended_at - r.started_at)
            elif r.started_at:
                duracion_s += max(0, time.time() - r.started_at)

        rt = (t.assignee or "").split(":", 1)[1] if ":" in (t.assignee or "") else (t.assignee or "hermes")
        nodos_info.append({
            "id": t.id,
            "titulo": t.title,
            "assignee": t.assignee,
            "runtime": rt,
            "estado": st,
            "intentos": len(runs),
            "duracion_s": round(duracion_s, 2),
            "resultado": (getattr(t, "result", None) or "")[:200] if getattr(t, "result", None) else "",
        })

    # Los estados salen de `kanban_db.VALID_STATUSES`, no de la intuicion:
    # {triage, todo, scheduled, ready, running, blocked, review, done, archived}.
    # Aca decia `in_progress` y `failed`, que NO existen: `activos` era siempre
    # 0 y `fallidos` nunca contaba `triage`, que es justo donde Hermes escala un
    # nodo que agoto sus desbloqueos. O sea, el fallo TERMINAL no se contaba.
    total = len(tasks)
    terminados = estados.get("done", 0)
    fallidos = estados.get("blocked", 0) + estados.get("triage", 0)
    activos = estados.get("running", 0)
    listos = estados.get("ready", 0)
    progreso = round((terminados / total * 100), 1) if total > 0 else 0.0

    return {
        "board": board,
        "corriendo": board in _corriendo,
        "resumen": {
            "total": total,
            "terminados": terminados,
            "fallidos": fallidos,
            "activos": activos,
            "listos": listos,
            "progreso_pct": progreso,
            "estados": estados,
        },
        "consumo": consumo_data,
        "nodos": nodos_info,
    }


def _historial() -> dict:
    """Listar boards con resumen consolidado de ejecuciones previas."""
    try:
        boards_raw = k.list_boards()
    except Exception:
        boards_raw = []
    salida = []
    for b in boards_raw:
        slug = b.get("slug")
        if not slug:
            continue
        try:
            conn = k.connect(board=slug)
            tasks = k.list_tasks(conn)
            if not tasks:
                continue
            estados = {}
            for t in tasks:
                st = t.status or "todo"
                estados[st] = estados.get(st, 0) + 1
            consumo = _consumo(slug)
            salida.append({
                "slug": slug,
                "nombre": b.get("display_name") or slug,
                "total_nodos": len(tasks),
                "estados": estados,
                "completado": estados.get("done", 0) == len(tasks) and len(tasks) > 0,
                "tiene_fallos": bool(estados.get("blocked", 0) or estados.get("triage", 0)),
                "costo_usd": consumo.get("total", {}).get("costo_usd", 0.0),
                "tokens_total": consumo.get("total", {}).get("total", 0),
                "corriendo": slug in _corriendo,
            })
        except Exception:
            continue
    return {"historial": salida}


def _generar_grafo(descripcion: str, runtime: str = "claude-code", dry_run: bool = False) -> dict:
    """Generar un grafo DAG estructurado y validado a partir de una descripción en lenguaje natural."""
    desc = (descripcion or "").strip()
    if not desc:
        raise ValueError("la descripcion del flujo no puede estar vacia")

    slug_base = re.sub(r'[^a-zA-Z0-9_-]', '-', desc.lower()[:25]).strip("-") or "flujo-ia"
    slug = f"ia-{slug_base}"

    def _plantilla(motivo: str) -> dict:
        """El grafo de ejemplo. Es el fallback, y lo dice: `_degradado` viaja
        hasta la UI para que no anuncie como diseño de un agente algo que
        salio de una plantilla fija de tres nodos."""
        g = {
            "board": slug,
            "reglas": "No modificar archivos fuera del alcance. Responder en formato estructurado.",
            "nodos": [
                {"id": "analisis", "titulo": f"Analizar requerimiento: {desc}", "runtime": "claude-code", "x": 100, "y": 100},
                {"id": "ejecucion", "titulo": f"Ejecutar y validar: {desc}", "runtime": "opencode", "x": 300, "y": 100},
                {"id": "sintesis", "titulo": "Sintetizar resultados y emitir veredicto final", "runtime": "antigravity", "x": 200, "y": 250},
            ],
            "aristas": [["analisis", "ejecucion"], ["ejecucion", "sintesis"]],
            "_degradado": True, "_motivo": motivo,
        }
        compilador.validar(g, capacidades=False)
        pos = disposicion.ordenar(g)
        for n in g["nodos"]:
            if n["id"] in pos:
                n["x"], n["y"] = pos[n["id"]]["x"], pos[n["id"]]["y"]
        return g

    if dry_run:
        return _plantilla("dry_run: no se invoco ningun agente")

    rt_elegido = _runtime_para_chatear(runtime)
    if not rt_elegido:
        return _plantilla("no hay ningun ejecutor instalado en esta maquina")

    prompt = (
        "Eres el arquitecto de flujos de ORQUESTER. Diseña un grafo DAG de tareas óptimo para el siguiente requerimiento:\n\n"
        f"Requerimiento: {desc}\n\n"
        "Debes responder UNICAMENTE con un objeto JSON válido (sin explicaciones, sin comentarios, sin markdown de envoltorio excepto ```json si es necesario).\n"
        "Estructura obligatoria:\n"
        "{\n"
        f'  "board": "{slug}",\n'
        '  "reglas": "Reglas de ejecucion para todos los nodos",\n'
        '  "nodos": [\n'
        '    {"id": "identificador_unico", "titulo": "Prompt detallado del nodo", "runtime": "claude-code|opencode|antigravity|hermes"}\n'
        '  ],\n'
        '  "aristas": [["id_padre", "id_hijo"]]\n'
        "}\n"
        "Reglas:\n"
        "1. Los identificadores de nodos deben ser alfanuméricos cortos (ej. n1, n2, auditor, tester).\n"
        "2. Debe ser un DAG acíclico válido (sin ciclos, sin auto-referencias).\n"
        "3. Distribuye el trabajo entre los runtimes disponibles (claude-code, opencode, antigravity, hermes).\n"
        "4. Incluye entre 2 y 5 nodos según la complejidad requerida.\n"
    )

    try:
        # `texto`, no `respuesta`: es la clave que devuelve `chat_backend`.
        # Con la clave mal, `resp` quedaba vacia, `json.loads("")` tiraba, y el
        # `except` de abajo devolvia SIEMPRE la plantilla de tres nodos. La
        # feature "generar grafo con IA" nunca invoco a un agente de verdad.
        resp = (dispatcher.run_chat(rt_elegido, prompt, timeout=120)
                .get("texto") or "").strip()
        if "```json" in resp:
            resp = resp.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in resp:
            resp = resp.split("```", 1)[1].split("```", 1)[0].strip()

        obj = json.loads(resp)
        if not isinstance(obj, dict) or "nodos" not in obj or "aristas" not in obj:
            raise ValueError("Estructura de grafo incompleta")
        obj["board"] = obj.get("board") or slug
        compilador.validar(obj, capacidades=False)
        pos = disposicion.ordenar(obj)
        for n in obj.get("nodos", []):
            if n.get("id") in pos:
                n["x"] = pos[n["id"]]["x"]
                n["y"] = pos[n["id"]]["y"]
            else:
                n["x"] = n.get("x", 100)
                n["y"] = n.get("y", 100)
        return obj
    except Exception as e:
        return _plantilla(f"{rt_elegido} no devolvio un grafo usable: "
                          f"{type(e).__name__}: {str(e)[:200]}")


def _reintentar_nodo(board: str, task_id: str) -> dict:
    """Reintentar un nodo fallido o bloqueado desbloqueándolo en kanban_db."""
    conn = _conn(board)
    t = k.get_task(conn, task_id)
    if not t:
        raise ValueError(f"no existe la card {task_id}")
    k.unblock_task(conn, task_id)
    return {"ok": True, "task_id": task_id}


def _guardar_snapshot(board: str, grafo: dict, descripcion: str = "") -> dict:
    """Guardar una instantánea inmutable del diseño actual del grafo."""
    ts = int(time.time())
    # El board pasa por el MISMO filtro que un nombre de grafo. Antes solo se
    # miraba `..`, y uno absoluto escribia el .json fuera de `snapshots/`.
    # `limpio` sale ya filtrado, y `ts` es un int: el snap_id es seguro por
    # construccion, sin una segunda validacion que se pueda desincronizar.
    limpio = _ruta_segura(board, SNAPSHOTS, "board").stem
    snap_id = f"{limpio}_{ts}"
    f = SNAPSHOTS / f"{snap_id}.json"
    # Dos snapshots del mismo board en el mismo segundo compartian id y el
    # segundo pisaba al primero. Un snapshot es inmutable: si el nombre ya
    # existe, se desempata en vez de sobreescribir.
    n = 1
    while f.exists():
        n += 1
        snap_id = f"{limpio}_{ts}-{n}"
        f = SNAPSHOTS / f"{snap_id}.json"
    data = {
        "id": snap_id,
        "board": limpio,
        "timestamp": ts,
        "descripcion": descripcion.strip() or f"Snapshot {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}",
        "grafo": grafo,
    }
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "id": snap_id, "snapshot": data}


def _listar_snapshots(board: str = "") -> dict:
    """Listar todas las instantáneas guardadas, filtradas opcionalmente por board."""
    limpio = (board or "").strip()
    salida = []
    for f in sorted(SNAPSHOTS.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if not limpio or d.get("board") == limpio:
                salida.append({
                    "id": d.get("id") or f.stem,
                    "board": d.get("board"),
                    "timestamp": d.get("timestamp"),
                    "descripcion": d.get("descripcion"),
                    "total_nodos": len(d.get("grafo", {}).get("nodos", [])),
                })
        except Exception:
            continue
    return {"snapshots": salida}


def _restaurar_snapshot(snap_id: str) -> dict:
    """Recuperar un snapshot por ID."""
    f = _ruta_segura(snap_id, SNAPSHOTS, "ID de snapshot")
    if not f.is_file():
        raise ValueError(f"No existe el snapshot {f.stem}")
    return {"ok": True, "snapshot": json.loads(f.read_text(encoding="utf-8"))}


def _generar_ci_workflow(grafo: dict) -> str:
    """Generar workflow de GitHub Actions a partir del grafo DAG.

    TODO valor que viene del grafo se sanea antes de entrar al YAML. El `id`
    ya se saneaba para la clave del job (`nid`) pero se usaba CRUDO tres lineas
    mas abajo, en `name:`, en el `echo` y en el `python -c`: un id con comillas
    y saltos de linea inyectaba pasos `- run:` propios en el workflow. Verificado.
    """
    board = _slug_yaml(grafo.get("board") or "orquester-flujo")
    nodos = grafo.get("nodos") or []
    aristas = grafo.get("aristas") or []

    padres_por_nodo = {}
    for p, h in aristas:
        padres_por_nodo.setdefault(h, []).append(p)

    # Un slug por nodo, calculado UNA vez y compartido con `needs`. Dos ids
    # distintos pueden colapsar al mismo slug (`a.b` y `a-b` dan `a_b`), y eso
    # producia dos claves YAML iguales: gana la ultima y un job desaparece sin
    # avisar. El sufijo desempata. Y `needs` sale de la misma tabla, porque
    # re-slugear al padre por separado tenia el mismo riesgo.
    claves, vistos = {}, {}
    for n in nodos:
        base = _slug_yaml(n["id"])
        vistos[base] = vistos.get(base, 0) + 1
        claves[n["id"]] = base if vistos[base] == 1 else f"{base}_{vistos[base]}"

    jobs_yaml = []
    for n in nodos:
        nid = claves[n["id"]]
        # Aplanado a una linea; el escapado del literal lo hace `json.dumps`,
        # que es exactamente el de un escalar YAML entre comillas dobles.
        titulo = " ".join((n.get("titulo") or nid).split())
        rt = _slug_yaml(n.get("runtime", "claude-code"))
        # `if p in claves`: este exportador no llama a `validar`, asi que una
        # arista puede nombrar un nodo que no existe.
        needs = [claves[p] for p in padres_por_nodo.get(n["id"], []) if p in claves]

        job_lines = [
            f"  {nid}:",
            f"    name: {json.dumps(f'{nid}: {titulo[:35]}')}",
            "    runs-on: ubuntu-latest",
        ]
        if needs:
            job_lines.append(f"    needs: [{', '.join(needs)}]")

        job_lines.extend([
            "    steps:",
            "      - name: Checkout repository",
            "        uses: actions/checkout@v4",
            "      - name: Setup Python",
            "        uses: actions/setup-python@v5",
            "        with:",
            "          python-version: '3.11'",
            f"      - name: Ejecutar nodo ({rt})",
            f"        run: |",
            f"          echo \"==> ORQUESTER Nodo {nid} [{rt}]\"",
            # Comentario de shell: el titulo ya viene sin saltos de linea, que
            # es lo unico que podria sacarlo del comentario.
            f"          # Goal: {titulo[:70]}",
            f"          python -c \"print('Paso {nid} completado.')\"",
        ])
        jobs_yaml.append("\n".join(job_lines))

    workflow = [
        f"# Pipeline CI/CD generado automáticamente por ORQUESTER",
        f"# Flujo: {board}",
        f"name: ORQUESTER - {board}",
        "",
        "on:",
        "  push:",
        "    branches: [ main, master ]",
        "  pull_request:",
        "  workflow_dispatch:",
        "",
        "jobs:",
        "\n".join(jobs_yaml) if jobs_yaml else "  noop:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo 'Grafo vacio'",
    ]
    return "\n".join(workflow)


def _generar_script_python(grafo: dict) -> str:
    """Generar un script Python autónomo para ejecutar el flujo sin el Studio.

    El grafo viaja como JSON leido en runtime, NO interpolado en el fuente.
    Pegarlo con un f-string rompia de dos formas: `true`/`null` de JSON no son
    literales de Python (el script moria con `NameError`), y un `board` con
    comillas cerraba el literal y ejecutaba lo que viniera detras. El script se
    descarga y se corre a mano: eso era ejecucion de codigo arbitrario en la
    maquina de quien lo corriera. `json.dumps` escapa toda comilla doble, asi
    que dentro del `r\"\"\"...\"\"\"` no puede aparecer un cierre.
    """
    grafo_json = json.dumps(grafo, indent=2, ensure_ascii=False)
    script = f'''#!/usr/bin/env python3
"""Script autónomo de ejecución de un flujo de ORQUESTER.
Generado automáticamente por ORQUESTER Studio.
"""
import json, sys
from pathlib import Path

# Añadir directorios de ORQUESTER al path
RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

import compile as compilador
import loop as dispatcher
import hermes_cli.kanban_db as k

GRAFO = json.loads(r"""
{grafo_json}
""")

def main():
    board = GRAFO.get("board") or "orquester-script"
    print(f"==> Validando y compilando flujo: {{board}}...")
    compilador.validar(GRAFO, capacidades=True)
    ids = compilador.compilar(GRAFO, board=board)
    print(f"==> {{len(ids)}} tareas creadas en kanban.db (board: {{board}})")
    
    print("==> Iniciando ejecucion con dispatcher...")
    dispatcher.correr(board, hasta_vacio=True)
    
    conn = k.connect(board=board)
    tasks = k.list_tasks(conn)
    completadas = sum(1 for t in tasks if t.status == "done")
    fallidas = sum(1 for t in tasks if t.status in ("blocked", "triage"))
    gasto = dispatcher.gasto_usd(conn)
    print(f"\\n==> Resultado final: {{completadas}}/{{len(tasks)}} completadas, {{fallidas}} fallidas.")
    print(f"==> Consumo medido: US$ {{gasto:.4f}}")

if __name__ == "__main__":
    main()
'''
    return script


def _simular_flujo(grafo: dict) -> dict:
    """Simulación analítica del DAG: paralelismo por capa, camino crítico y runtimes."""
    compilador.validar(grafo, capacidades=False)
    capas = disposicion._capas(grafo)
    por_capa: dict[int, list[dict]] = {}
    for n in grafo.get("nodos", []):
        if compilador.es_nota(n):
            continue
        c = capas.get(n["id"], 0)
        por_capa.setdefault(c, []).append({
            "id": n["id"],
            "titulo": n.get("titulo", n["id"]),
            "runtime": n.get("runtime", "hermes"),
            "esfuerzo": n.get("esfuerzo"),
            "presupuesto_usd": n.get("presupuesto_usd"),
        })

    pasos = []
    max_paralelo = 0
    runtimes_usados = set()
    for c in sorted(por_capa.keys()):
        grupo = por_capa[c]
        max_paralelo = max(max_paralelo, len(grupo))
        for item in grupo:
            runtimes_usados.add(item["runtime"])
        pasos.append({
            "paso": c + 1,
            "nodos": grupo,
            "paralelos": len(grupo),
        })

    return {
        "ok": True,
        "board": grafo.get("board", "orquester"),
        "total_nodos": len([n for n in grafo.get("nodos", []) if not compilador.es_nota(n)]),
        "camino_critico_pasos": len(pasos),
        "paralelismo_maximo": max_paralelo,
        "runtimes": sorted(list(runtimes_usados)),
        "pasos": pasos,
    }


def _generar_reporte_corrida(board: str) -> dict:
    """Generar informe completo de auditoría y ejecución de un board en Markdown."""
    slug = (board or "orquester").strip()
    conn = _conn(slug)
    tasks = k.list_tasks(conn)
    tele = _telemetria(slug)
    res = tele.get("resumen", {})
    consumo = _consumo(slug)
    tot = consumo.get("total", {})
    por_nodo = consumo.get("por_nodo", {})

    fecha_str = time.strftime("%Y-%m-%d %H:%M:%S")
    filas_tabla = []
    secciones_entregables = []

    for t in tasks:
        tid = t.id
        titulo = t.title or "(sin titulo)"
        rt = "hermes"
        if t.assignee and ":" in t.assignee:
            rt = t.assignee.split(":", 1)[1]
        st = t.status or "todo"
        costo_nodo = por_nodo.get(tid, {}).get("costo_usd")
        costo_txt = f"US$ {costo_nodo:.4f}" if costo_nodo is not None else "—"
        tokens_nodo = por_nodo.get(tid, {}).get("total", 0)

        dur_txt = "—"
        if t.started_at and t.completed_at:
            dur_txt = f"{t.completed_at - t.started_at}s"

        filas_tabla.append(f"| `{tid}` | {titulo[:35]} | `{rt}` | `{st}` | {dur_txt} | {tokens_nodo} ({costo_txt}) |")

        if t.result:
            salida_nodo = t.result[:800]
            # Cerca mas larga que la secuencia de backticks mas larga que traiga
            # el resultado: un entregable que contenga ``` cerraba la cerca y el
            # resto del reporte se renderizaba como markdown en vez de como
            # salida del agente.
            cerca = "`" * max(3, max((len(m) for m in re.findall(r"`+", salida_nodo)),
                                     default=0) + 1)
            secciones_entregables.append(
                f"### Nodo `{tid}`: {titulo}\n- **Estado**: `{st}` | **Runtime**: `{rt}`"
                f"\n\n{cerca}\n{salida_nodo}\n{cerca}\n")

    md = [
        f"# Reporte de Auditoría: {slug}",
        f"*Generado automáticamente por ORQUESTER Studio el {fecha_str}*",
        "",
        "## 1. Resumen Ejecutivo",
        f"- **Progreso**: {res.get('progreso_pct', 0)}% ({res.get('terminados', 0)}/{res.get('total', 0)} tareas completadas)",
        f"- **Nodos Fallidos/Bloqueados**: {res.get('fallidos', 0)}",
        f"- **Nodos Activos**: {res.get('activos', 0)}",
        f"- **Consumo Total**: US$ {tot.get('costo_usd', 0.0):.4f} ({tot.get('total', 0):,} tokens en {tot.get('intentos', 0)} intentos)",
        "",
        "## 2. Detalle de Nodos del Flujo",
        "| ID | Título | Runtime | Estado | Duración | Consumo |",
        "|---|---|---|---|---|---|",
        "\n".join(filas_tabla) if filas_tabla else "| — | Sin tareas | — | — | — | — |",
        "",
        "## 3. Entregables y Salidas de Agentes",
        "\n".join(secciones_entregables) if secciones_entregables else "*No hay entregables registrados aún.*",
    ]

    return {
        "ok": True,
        "board": slug,
        "reporte": "\n".join(md),
    }


def _quedan_de_hermes(conn) -> bool:
    """Cards que espera el dispatcher de Hermes, no el nuestro."""
    propios = {dispatcher.carril(rt) for rt in dispatcher.BACKENDS}
    return any(t.status in ("todo", "ready", "running")
               for t in k.list_tasks(conn) if t.assignee not in propios)


def _arrancar(board: str, tope_usd: float = None) -> dict:
    if board in _corriendo and _corriendo[board].is_alive():
        return {"ok": False, "motivo": "ya hay un dispatcher corriendo en este board"}

    # Los nodos `runtime: hermes` los lanza el dispatcher de Hermes, no el
    # nuestro (§12). El exportador MCP ya lo tickeaba solo; el boton Ejecutar
    # no, y obligaba a abrir otra terminal para un flujo mixto. Misma pieza,
    # mismo comportamiento.
    hermes = mcp._hermes_bin()

    def _tick_hermes():
        if not hermes:
            return
        try:
            subprocess.run([hermes, "kanban", "--board", board, "dispatch"],
                           capture_output=True, timeout=180,
                           stdin=subprocess.DEVNULL)
        except Exception:
            traceback.print_exc()          # que no tumbe el loop del carril propio

    _parar.discard(board)          # un arranque anterior pudo dejarlo marcado
    _topes[board] = tope_usd       # None = esta corrida va sin tope

    def _correr():
        try:
            conn = k.connect(board=board)
            while True:
                if board in _parar:
                    return
                if tope_usd is not None and dispatcher.gasto_usd(conn) >= tope_usd:
                    # Se anota como si lo hubieran parado a mano: el Studio ya
                    # sabe mostrar ese estado, y el motivo se ve en el consumo.
                    _parar.add(board)
                    print(f"[{board}] tope de US$ {tope_usd} alcanzado: no se "
                          f"arrancan nodos nuevos")
                    return
                _tick_hermes()
                hechas = dispatcher.tick(conn, board=board, tope_usd=tope_usd)
                sin_trabajo = (not hechas
                               and not dispatcher._queda_trabajo(conn)
                               and not _quedan_de_hermes(conn))
                if sin_trabajo:
                    return
                time.sleep(3)
        except Exception:
            traceback.print_exc()
        finally:
            _corriendo.pop(board, None)
            _parar.discard(board)

    h = threading.Thread(target=_correr, daemon=True)
    _corriendo[board] = h
    h.start()
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def _responder(self, codigo, cuerpo, tipo="application/json"):
        datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _host_ok(self) -> bool:
        """La cabecera `Host` tiene que nombrar a esta maquina (ver HOSTS_OK)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host in HOSTS_OK:
            return True
        self._responder(421, {"error": f"Host '{host}' no atendido aca"})
        return False

    def _autorizado(self) -> bool:
        if _token_ok(self):
            return True
        # 404 y no 401: un 401 confirma que aca hay algo. Ademas sin
        # `WWW-Authenticate` el navegador no muestra un popup inutil.
        self._responder(404, {"error": "no encontrado"})
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        ruta, _, _ = self.path.partition("?")
        # El HTML es una cascara estatica sin datos: se sirve sin token para que
        # recargar la pagina funcione (una navegacion no puede mandar cabeceras,
        # y el token se limpia de la URL a proposito). Todo `/api/*` si exige
        # token: ahi estan los datos y la ejecucion.
        if ruta not in ("/", "/api/capacidades") and not self._autorizado():
            return
        params = _query(self.path)
        # Con `try`, igual que `do_POST`. Sin el, cualquier excepcion cerraba el
        # socket sin respuesta: un board con espacios (`_normalize_board_slug`
        # tira ValueError), un .json corrupto en `ui/grafos/`, `list_boards()`.
        try:
            return self._get(ruta, params)
        except ValueError as e:
            return self._responder(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._responder(500, {"error": f"{type(e).__name__}: {e}"})

    def _get(self, ruta, params):
        if ruta == "/":
            return self._responder(200, HTML.read_bytes(), "text/html; charset=utf-8")
        if ruta == "/api/estado":
            return self._responder(200, _estado(params.get("board", "orquester")))
        if ruta == "/api/capacidades":
            # Sin token: no expone nada del usuario, solo que sabe hacer este
            # motor. Un agente lo consulta antes de armar un grafo.
            return self._responder(200, capacidades.tabla())
        if ruta == "/api/modelos":
            # Con token: lanza un subproceso (`agy models` consulta al
            # proveedor). No va junto a /api/capacidades, que es estatico.
            return self._responder(200, capacidades.modelos(params.get("runtime", "")))
        if ruta == "/api/consumo":
            return self._responder(200, _consumo(params.get("board", "orquester")))
        if ruta == "/api/traza":
            return self._responder(200, _traza(params.get("board", "orquester"),
                                               params.get("task", "")))
        if ruta == "/api/plantillas":
            return self._responder(200, _catalogo())
        if ruta == "/api/grafos":
            return self._responder(200, {"grafos": sorted(p.stem for p in GRAFOS.glob("*.json"))})
        if ruta == "/api/boards":
            return self._responder(200, {"boards": [b.get("slug") for b in k.list_boards() if b.get("slug")]})
        if ruta == "/api/telemetria":
            return self._responder(200, _telemetria(params.get("board", "orquester")))
        if ruta == "/api/historial":
            return self._responder(200, _historial())
        if ruta == "/api/doctor":
            return self._responder(200, capacidades.doctor())
        if ruta == "/api/secretos-status":
            return self._responder(200, capacidades.secretos_status())
        if ruta == "/api/reporte-corrida":
            return self._responder(200, _generar_reporte_corrida(params.get("board", "orquester")))
        if ruta == "/api/snapshots":
            return self._responder(200, _listar_snapshots(params.get("board", "")))
        if ruta == "/api/grafo":
            try:
                f = _archivo(params.get("nombre", ""))
            except ValueError as e:
                return self._responder(400, {"error": str(e)})
            if not f.exists():
                return self._responder(404, {"error": "no existe"})
            return self._responder(200, json.loads(f.read_text(encoding="utf-8")))
        return self._responder(404, {"error": "ruta desconocida"})

    # 8 MB. Esto recibe grafos, no subidas: sin techo, un `Content-Length`
    # enorme se reserva en memoria antes de que nadie mire el contenido.
    MAX_CUERPO = 8 * 1024 * 1024

    def do_POST(self):
        if not self._host_ok():
            return
        if not self._autorizado():
            return
        try:
            # Adentro del `try`, no afuera. Estaban afuera y un cuerpo que no
            # fuera JSON, o un `Content-Length: abc`, tiraban la excepcion en el
            # handler: socket cerrado, sin respuesta, traceback en consola.
            # Verificado con `curl -d 'no-es-json'` (curl exit 52).
            largo = int(self.headers.get("Content-Length") or 0)
            # Negativo antes que el tope: `read(-1)` lee HASTA EOF, o sea que un
            # `Content-Length: -1` cuelga el hilo esperando un cierre que el
            # cliente no tiene por que hacer.
            if largo < 0:
                return self._responder(400, {"error": "Content-Length invalido"})
            if largo > self.MAX_CUERPO:
                return self._responder(413, {"error": f"cuerpo de mas de "
                                                      f"{self.MAX_CUERPO} bytes"})
            cuerpo = json.loads(self.rfile.read(largo) or b"{}")
            if not isinstance(cuerpo, dict):
                return self._responder(400, {"error": "el cuerpo tiene que ser un objeto JSON"})
            if self.path == "/api/validar":
                compilador.validar(cuerpo)
                return self._responder(200, {"ok": True})
            if self.path == "/api/chat":
                # Un turno de conversacion con el ejecutor elegido. La sesion
                # la guarda el CLI: aca solo viaja el id de ida y vuelta, asi
                # que el Studio no persiste ninguna conversacion.
                rt = cuerpo.get("runtime", "")
                if rt not in dispatcher.BACKENDS:
                    return self._responder(400, {"error": f"'{rt}' no puede chatear"})
                if not (cuerpo.get("mensaje") or "").strip():
                    return self._responder(400, {"error": "mensaje vacio"})
                try:
                    r = dispatcher.run_chat(
                        rt, cuerpo["mensaje"],
                        sesion=cuerpo.get("sesion") or None,
                        cwd=cuerpo.get("workspace") or None,
                        modelo=cuerpo.get("modelo") or None,
                        esfuerzo=cuerpo.get("esfuerzo") or None,
                        timeout=int(cuerpo.get("timeout") or 600))
                except Exception as e:
                    return self._responder(400, {"error": str(e)[:1500]})
                return self._responder(200, r)
            if self.path == "/api/ordenar":
                # Solo calcula: no guarda ni compila nada. La UI aplica las
                # coordenadas que recibe.
                compilador.validar(cuerpo, capacidades=False)
                return self._responder(200, {"posiciones": disposicion.ordenar(cuerpo)})
            if self.path == "/api/optimizar-goal":
                goal = cuerpo.get("goal") or ""
                rt = cuerpo.get("runtime") or "claude-code"
                reglas = cuerpo.get("reglas") or ""
                try:
                    res = _optimizar_goal(goal, runtime=rt, reglas=reglas,
                                          dry_run=bool(cuerpo.get("dry_run")))
                    return self._responder(200, {"ok": True, **res})
                except ValueError as e:
                    return self._responder(400, {"error": str(e)})
            if self.path == "/api/exportar-mermaid":
                try:
                    return self._responder(200, {"ok": True, "mermaid": _generar_mermaid(cuerpo)})
                except Exception as e:
                    return self._responder(400, {"error": f"no se pudo generar mermaid: {e}"})
            if self.path == "/api/generar-grafo":
                desc = cuerpo.get("descripcion") or ""
                rt = cuerpo.get("runtime") or "claude-code"
                dry = bool(cuerpo.get("dry_run"))
                try:
                    g = _generar_grafo(desc, runtime=rt, dry_run=dry)
                    return self._responder(200, {"ok": True, "grafo": g})
                except ValueError as e:
                    return self._responder(400, {"error": str(e)})
                except Exception as e:
                    return self._responder(500, {"error": f"error generando grafo: {e}"})
            if self.path == "/api/reintentar-nodo":
                board = cuerpo.get("board") or "orquester"
                tid = cuerpo.get("task_id") or ""
                try:
                    res = _reintentar_nodo(board, tid)
                    return self._responder(200, res)
                except ValueError as e:
                    return self._responder(400, {"error": str(e)})
                except Exception as e:
                    return self._responder(500, {"error": f"error reintentando nodo: {e}"})
            if self.path == "/api/exportar-ci":
                try:
                    return self._responder(200, {"ok": True, "workflow": _generar_ci_workflow(cuerpo)})
                except Exception as e:
                    return self._responder(400, {"error": f"error generando workflow CI: {e}"})
            if self.path == "/api/exportar-python":
                try:
                    return self._responder(200, {"ok": True, "script": _generar_script_python(cuerpo)})
                except Exception as e:
                    return self._responder(400, {"error": f"error generando script python: {e}"})
            if self.path == "/api/simular":
                try:
                    return self._responder(200, _simular_flujo(cuerpo))
                except Exception as e:
                    return self._responder(400, {"error": f"error simulando flujo: {e}"})
            if self.path == "/api/snapshot":
                b = cuerpo.get("board") or "orquester"
                g = cuerpo.get("grafo") or {}
                desc = cuerpo.get("descripcion") or ""
                try:
                    return self._responder(200, _guardar_snapshot(b, g, desc))
                except Exception as e:
                    return self._responder(400, {"error": f"error guardando snapshot: {e}"})
            if self.path == "/api/snapshot/restaurar":
                sid = cuerpo.get("id") or ""
                try:
                    return self._responder(200, _restaurar_snapshot(sid))
                except Exception as e:
                    return self._responder(400, {"error": f"error restaurando snapshot: {e}"})
            if self.path == "/api/reporte-corrida":
                b = cuerpo.get("board") or "orquester"
                try:
                    return self._responder(200, _generar_reporte_corrida(b))
                except Exception as e:
                    return self._responder(400, {"error": f"error generando reporte: {e}"})
            if self.path == "/api/parametros":
                # Los marcadores los detecta el exportador MCP, no una segunda
                # regex en el navegador: si se duplica, se desincroniza y el
                # Studio compila con un `{{marcador}}` que llega literal al disco.
                # `faltan` sale del mismo lugar que los parametros: si la UI
                # los contara por su cuenta, marcaria en ambar uno distinto del
                # que rechaza el compilador.
                todos = mcp.parametros(cuerpo)
                valores = cuerpo.get("valores") or {}
                return self._responder(200, {
                    "parametros": todos,
                    "faltan": [x for x in todos if not str(valores.get(x, "")).strip()],
                })
            if self.path == "/api/compilar":
                # Un grafo con marcadores no se compila crudo: `sustituir` exige
                # que esten todos y falla con el nombre del que falta.
                grafo = (mcp.sustituir(cuerpo, cuerpo.get("valores") or {})
                         if mcp.parametros(cuerpo) else cuerpo)
                ids = compilador.compilar(grafo, board=cuerpo.get("board"))
                return self._responder(200, {"ok": True, "ids": ids})
            if self.path == "/api/mcp":
                nombre = cuerpo.get("board") or "sin-nombre"
                return self._responder(200, {
                    "tool": nombre,
                    "parametros": mcp.parametros(cuerpo),
                    "config": {"mcpServers": {nombre: {
                        "command": "python",
                        "args": [str(RAIZ / "mcp_exporter" / "mcp_server.py"),
                                 str(_archivo(nombre))],
                    }}},
                })
            if self.path == "/api/parar":
                board = cuerpo.get("board", "orquester")
                vivo = board in _corriendo
                _parar.add(board)
                return self._responder(200, {"ok": vivo, "motivo":
                                             "" if vivo else "no hay nada corriendo en este board"})
            if self.path == "/api/correr":
                tope = cuerpo.get("presupuesto_usd")
                try:
                    tope = float(tope) if str(tope or "").strip() else None
                except ValueError:
                    return self._responder(400, {"error": f"presupuesto invalido: {tope!r}"})
                if tope is not None and tope <= 0:
                    return self._responder(400, {"error": "el presupuesto tiene que ser > 0"})
                return self._responder(200, _arrancar(cuerpo.get("board", "orquester"), tope))
            if self.path == "/api/plantilla":
                # Usar una plantilla = copiarla a los grafos propios, con el
                # nombre que elija quien la usa. La plantilla no se toca nunca.
                # Mismo validador que los grafos y los snapshots. Aca la
                # comparacion era `origen.parent != PLANTILLAS` SIN `.resolve()`:
                # hoy no se escapa, pero era el tercer criterio distinto para lo
                # mismo, y el que fallo en `_guardar_snapshot` era uno de esos.
                origen = _ruta_segura(cuerpo.get("plantilla") or "", PLANTILLAS,
                                      "plantilla")
                if not origen.is_file():
                    return self._responder(404, {"error": "no existe esa plantilla"})
                g = json.loads(origen.read_text(encoding="utf-8"))
                g["board"] = cuerpo.get("nombre") or g.get("board") or "sin-nombre"
                destino = _archivo(g["board"])
                if destino.exists() and not cuerpo.get("pisar"):
                    return self._responder(409, {"error": f"ya tenés un grafo llamado "
                                                          f"'{g['board']}'"})
                destino.write_text(json.dumps(g, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
                return self._responder(200, {"ok": True, "grafo": g})
            if self.path == "/api/grafo/borrar":
                f = _archivo(cuerpo.get("board") or "")
                if not f.is_file():
                    return self._responder(404, {"error": "no existe"})
                f.unlink()
                return self._responder(200, {"ok": True})
            if self.path == "/api/grafo":
                _archivo(cuerpo.get("board") or "").write_text(
                    json.dumps(cuerpo, indent=2, ensure_ascii=False), encoding="utf-8")
                return self._responder(200, {"ok": True})
        except compilador.ErrorDeGrafo as e:
            # El mensaje va tal cual al canvas: por eso los errores del
            # compilador nombran el nodo culpable.
            return self._responder(400, {"ok": False, "error": str(e)})
        except ValueError as e:
            # `sustituir` con parametros faltantes. Es culpa del pedido, no del
            # servidor: 400 y el nombre de lo que falta. (ErrorDeGrafo tambien
            # es ValueError, por eso este `except` va despues.)
            return self._responder(400, {"ok": False, "error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._responder(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        return self._responder(404, {"error": "ruta desconocida"})

    def log_message(self, *a):
        pass                                  # sin ruido de acceso en consola


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    # 127.0.0.1 por defecto. `ORQUESTER_HOST=0.0.0.0` lo abre a la red, y el
    # token deja de ser una formalidad: pasa a ser lo unico que separa a
    # cualquiera de una shell en esta maquina.
    host = os.environ.get("ORQUESTER_HOST", "127.0.0.1")
    print(f"Studio en http://{host}:{puerto}/?token={TOKEN}")
    if host != "127.0.0.1":
        print("  AVISO: expuesto a la red. El token es la unica barrera y esto")
        print("  ejecuta agentes con shell. No lo dejes escuchando sin necesidad.")
    if not os.environ.get("ORQUESTER_TOKEN"):
        print("  (token nuevo en cada arranque; fijalo con ORQUESTER_TOKEN)")
    # ThreadingHTTPServer y no HTTPServer: un turno de chat tarda entre 20 y
    # 120 segundos, y con un solo hilo ese pedido congela el Studio entero
    # (el canvas deja de refrescar el estado de la corrida mientras tanto).
    ThreadingHTTPServer((host, puerto), Handler).serve_forever()
