"""Studio de ORQUESTER: servidor local, sin dependencias.

`http.server` de la stdlib y un HTML de un archivo. Sin npm, sin build, sin
framework: la UI es un canvas SVG y unas pocas llamadas fetch. Cuando el
producto necesite RBAC, multiusuario y persistencia en Postgres, esto se
reemplaza por el backend NestJS de SS7 — hasta entonces es andamiaje que nadie
pidio.

    uv run --python 3.11 --with jsonschema python ui/server.py
    -> http://127.0.0.1:8765
"""
import html, hmac, json, os, re, secrets, shutil, subprocess, sys, threading, time, traceback
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
import corrida
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

# Alta y baja de `_corriendo` bajo lock. `ThreadingHTTPServer` atiende cada
# pedido en su propio hilo: entre el "¿ya hay uno corriendo?" y el alta no habia
# nada, asi que dos clics seguidos en Ejecutar --o el modo App y `/api/correr` a
# la vez-- pasaban los dos y arrancaban dos dispatchers sobre el mismo board.
_LOCK_ARRANQUE = threading.Lock()


def _parar_board(board: str) -> dict:
    """Interrumpir inmediatamente y en su totalidad la corrida del board."""
    board = corrida.slug(board)
    vivo = board in _corriendo
    corrida.pedir_parada(board)
    # 1. Matar subprocesos de agentes externos en ejecución
    try:
        # Con el board: sin el, parar una corrida mataba tambien los agentes de
        # cualquier otro board corriendo en paralelo.
        dispatcher.matar_procesos_activos(board)
    except Exception:
        pass
    # 2. Matar proceso de Hermes si sigue en vuelo
    corrida.matar_hermes(board)
    # 3. Marcar tareas en estado 'running' como blocked
    n_bloqueadas = 0
    conn = None
    try:
        conn = _conn(board)
        for t in k.list_tasks(conn, status="running"):
            try:
                k.block_task(conn, t.id, reason="Detenido por el usuario", kind="capability")
                n_bloqueadas += 1
            except Exception:
                pass
    except Exception:
        pass
    finally:
        # Una conexion por clic en Parar, y ninguna se cerraba.
        if conn is not None:
            conn.close()
    return {
        "ok": vivo,
        "parado": vivo,
        "motivo": "" if vivo else "no hay nada corriendo en este board",
        "tareas_bloqueadas": n_bloqueadas
    }


def _parar_nodo(board: str, task_id: str) -> dict:
    """Detener inmediatamente un nodo especifico en ejecucion."""
    board = corrida.slug(board)
    muerto = False
    try:
        muerto = dispatcher.matar_proceso_task(board, task_id)
    except Exception:
        pass
    bloqueado = False
    conn = None
    try:
        conn = _conn(board)
        t = k.get_task(conn, task_id)
        if t and t.status in ("running", "ready", "todo"):
            k.block_task(conn, task_id, reason="Detenido por el usuario", kind="capability")
            bloqueado = True
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    # `ok` refleja si se hizo ALGO, igual que en `_parar_board`. Decia `True`
    # fijo: sobre un task_id inexistente --o vacio, que es lo que llega si el
    # cuerpo no lo trae-- el Studio anunciaba "nodo detenido" sin haber tocado
    # nada. Un boton que siempre dice que si no informa, decora.
    return {
        "ok": muerto or bloqueado,
        "board": board,
        "task_id": task_id,
        "proceso_matado": muerto,
        "tarea_bloqueada": bloqueado,
        "motivo": "" if (muerto or bloqueado) else
                  (f"no existe la card {task_id}" if task_id else "falta el task_id"),
    }


def _aprobar_gate(board: str, task_id: str, resultado: str = None) -> dict:
    """Aprobar un nodo Gate, completando su tarea para desbloquear a los hijos."""
    conn = None
    try:
        conn = _conn(board)
        t = k.get_task(conn, task_id)
        if not t:
            return {"ok": False, "error": f"no existe la tarea '{task_id}'"}
        res = (resultado or "").strip() or "Aprobado por el usuario"
        k.complete_task(conn, task_id, summary=res, result=res,
                        metadata={"orquester_status": "success", "claimer": "human"})
        return {"ok": True, "board": board, "task_id": task_id, "resultado": res}
    except Exception as e:
        return {"ok": False, "error": f"no se pudo aprobar el gate: {e}"}
    finally:
        if conn is not None:
            conn.close()

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


def _catalogo_skills() -> dict:
    """Listar las skills disponibles en los directorios del entorno local."""
    carpetas = [
        Path.home() / ".gemini" / "config" / "skills",
        RAIZ.parent / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".codex" / "skills",
    ]
    encontradas = {}
    for base in carpetas:
        if not base.is_dir():
            continue
        try:
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.is_file():
                    continue
                nombre = d.name
                if nombre in encontradas:
                    continue
                desc = ""
                try:
                    txt = skill_md.read_text(encoding="utf-8", errors="ignore")
                    if txt.startswith("---"):
                        partes = txt.split("---", 2)
                        if len(partes) >= 3:
                            for linea in partes[1].splitlines():
                                if linea.strip().startswith("description:"):
                                    desc = linea.partition(":")[2].strip().strip('"').strip("'")
                                    break
                except Exception:
                    pass
                encontradas[nombre] = {
                    "nombre": nombre,
                    "descripcion": desc or "Habilidad de agente local",
                    "ruta": str(skill_md),
                }
        except Exception:
            continue
    return {"skills": sorted(encontradas.values(), key=lambda x: x["nombre"]), "total": len(encontradas)}


def _guardar_plantilla(nombre: str, descripcion: str, grafo: dict,
                       pisar: bool = False) -> dict:
    """Promociona un grafo válido a plantilla reutilizable en el catálogo.

    `pisar` no es una opcion nueva: es el MISMO contrato que ya tiene
    `/api/plantilla` para no reemplazar un grafo guardado (409 y el flag).
    Sin el, promocionar con un nombre repetido reemplazaba en silencio una
    plantilla que viene en el repo.
    """
    f = _ruta_segura(nombre, PLANTILLAS, "nombre de plantilla")
    if f.exists() and not pisar:
        raise FileExistsError(f"ya existe la plantilla '{f.stem}'")
    compilador.validar(grafo, capacidades=False)

    data = {
        "descripcion": descripcion.strip() or f"Plantilla {f.stem}",
        "board": grafo.get("board") or f.stem,
        "nodos": grafo.get("nodos") or [],
        "aristas": grafo.get("aristas") or [],
    }
    if grafo.get("reglas"):
        data["reglas"] = grafo["reglas"]
    if grafo.get("presupuesto_usd"):
        data["presupuesto_usd"] = grafo["presupuesto_usd"]

    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "nombre": f.stem, "plantilla": data}


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
            lineas.append(f'        {nid}["[Nota] {titulo}"]:::nota')
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
    # Las dos condiciones, igual que arriba: un candidato instalado pero fuera
    # de `BACKENDS` no lo sabe correr nadie, y el fallo aparecia recien en
    # `run_chat`, culpando al agente de algo que era ruteo.
    return next((c for c in ("opencode", "claude-code", "antigravity")
                 if c in t and t[c]["disponible"] and c in dispatcher.BACKENDS), None)


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
    """Consumo del board: por nodo, por runtime y agregado.

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
    por_runtime = {}
    for t in k.list_tasks(conn):
        acum = None
        for r in k.list_runs(conn, t.id):
            u = (r.metadata or {}).get("uso")
            if not u:
                continue
            rt = u.get("runtime") or (t.assignee.split(":")[1] if ":" in (t.assignee or "") else (t.assignee or "hermes"))
            if rt not in por_runtime:
                por_runtime[rt] = {"entrada": 0, "salida": 0, "total": 0, "cache_lectura": 0,
                                   "costo_usd": 0.0, "intentos": 0, "con_costo": 0, "sin_costo": 0}
            acum = acum or {"entrada": 0, "salida": 0, "total": 0,
                            "cache_lectura": 0, "costo_usd": None,
                            "runtime": rt, "intentos": 0}
            for campo in ("entrada", "salida", "total", "cache_lectura"):
                val = u.get(campo) or 0
                acum[campo] += val
                total[campo] += val
                por_runtime[rt][campo] += val
            acum["intentos"] += 1
            total["intentos"] += 1
            por_runtime[rt]["intentos"] += 1
            if u.get("costo_usd") is not None:
                cost = u["costo_usd"]
                acum["costo_usd"] = (acum["costo_usd"] or 0) + cost
                total["costo_usd"] += cost
                total["con_costo"] += 1
                por_runtime[rt]["costo_usd"] += cost
                por_runtime[rt]["con_costo"] += 1
            else:
                # Sin costo NO es cero: es un backend que corre por suscripcion
                # y no informa medidor. Contarlo como 0 mentiria el promedio.
                total["sin_costo"] += 1
                por_runtime[rt]["sin_costo"] += 1
        if acum:
            por_nodo[t.id] = acum
    return {"total": total, "por_nodo": por_nodo, "por_runtime": por_runtime, "tope_usd": tope,
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


def _grafo_desnudo(g: dict) -> dict:
    """El grafo sin nada que el agente no necesite ver.

    Las coordenadas son del lienzo y el agente no las decide; mandarlas es pagar
    tokens por ruido y darle la chance de devolverlas cambiadas.
    """
    return {
        "board": g.get("board") or "",
        "reglas": g.get("reglas") or "",
        "nodos": [{c: v for c, v in n.items() if c not in ("x", "y")}
                  for n in (g.get("nodos") or [])],
        "aristas": g.get("aristas") or [],
    }


def _acomodar(obj: dict, previas: dict) -> None:
    """Ubicar los nodos: los que ya estaban se quedan donde el usuario los dejo.

    Re-acomodar todo en cada refinamiento tira el arreglo manual del lienzo, que
    es justo lo primero que uno hace despues de generar. El boton `Ordenar`
    sigue ahi para un re-layout completo, a pedido.
    """
    pos = disposicion.ordenar(obj)
    for n in obj.get("nodos", []):
        viejo = previas.get(n.get("id"))
        if viejo:
            # Lo que el agente devolvio MANDA (por eso va segundo): si cambio el
            # titulo o el runtime, ese es el cambio pedido. Pero lo que no
            # nombro se conserva, y ahi entran el modelo, el presupuesto, las
            # herramientas y el workspace, que el agente ni sabe que existen.
            n.update({c: v for c, v in viejo.items() if c not in n})
            n["x"], n["y"] = viejo.get("x", 100), viejo.get("y", 100)
        else:
            p = pos.get(n.get("id")) or {"x": n.get("x", 100), "y": n.get("y", 100)}
            n["x"], n["y"] = p["x"], p["y"]

    # Un nodo nuevo puede caer sobre uno viejo que se movio a mano, porque la
    # topologia no sabe donde lo puso el usuario. Se lo baja hasta que no pise.
    # ponytail: O(n^2) sobre un lienzo de decenas de nodos; con cientos haria
    # falta un indice espacial.
    for n in obj.get("nodos", []):
        if n.get("id") in previas:
            continue
        for _ in range(50):
            if not any(o is not n
                       and abs(o["x"] - n["x"]) < disposicion.ANCHO
                       and abs(o["y"] - n["y"]) < disposicion.ALTO
                       for o in obj["nodos"]):
                break
            n["y"] += disposicion.ALTO + 24


def _generar_grafo(descripcion: str, runtime: str = "claude-code", dry_run: bool = False,
                   actual: dict = None, sesion: str = None) -> dict:
    """Diseñar un grafo DAG con un agente, o REFINAR uno que ya existe.

    Con `actual`, el pedido es un cambio sobre ese grafo y no un diseño desde
    cero: sin esto, "agregale un nodo de tests" obligaba a describir el flujo
    entero otra vez y pisaba el lienzo.

    Se pide el grafo COMPLETO ya modificado, no un diff: un parche necesitaria
    un formato y un aplicador propios, y el grafo entero ya pasa por `validar`,
    que rechaza ids repetidos, aristas colgadas y ciclos. La `sesion` la guarda
    el CLI, igual que el chat (SS10.4): por acá viaja solo el id, así que dos
    refinamientos seguidos son una conversación y no dos desconocidos.
    """
    desc = (descripcion or "").strip()
    if not desc:
        raise ValueError("la descripcion del flujo no puede estar vacia")
    if actual is not None and not (actual.get("nodos") or []):
        actual = None                      # un grafo vacio no es nada que refinar

    slug_base = re.sub(r'[^a-zA-Z0-9_-]', '-', desc.lower()[:25]).strip("-") or "flujo-ia"
    slug = (actual or {}).get("board") or f"ia-{slug_base}"
    # El nodo ENTERO, no solo sus coordenadas: al refinar, el agente devuelve
    # `id`/`titulo`/`runtime` (que es lo que le pide el formato) y omite todo lo
    # demas. Sin conservar el nodo viejo, un refinamiento borraba en silencio el
    # modelo, el esfuerzo, el presupuesto, las herramientas y el workspace que
    # el usuario habia configurado nodo por nodo.
    previas = {n["id"]: n for n in ((actual or {}).get("nodos") or []) if n.get("id")}

    def _degradado(motivo: str) -> dict:
        """Sin agente no se inventa un diseño. Refinando se devuelve el grafo
        TAL CUAL estaba: pisarlo con una plantilla de tres nodos por no poder
        hablar con nadie seria perder el trabajo del usuario."""
        if actual is not None:
            return {"grafo": actual, "degradado": True, "sesion": sesion,
                    "motivo": f"el grafo quedo sin cambios: {motivo}"}
        g = {
            "board": slug,
            "reglas": "No modificar archivos fuera del alcance. Responder en formato estructurado.",
            "nodos": [
                {"id": "analisis", "titulo": f"Analizar requerimiento: {desc}", "runtime": "claude-code"},
                {"id": "ejecucion", "titulo": f"Ejecutar y validar: {desc}", "runtime": "opencode"},
                {"id": "sintesis", "titulo": "Sintetizar resultados y emitir veredicto final", "runtime": "antigravity"},
            ],
            "aristas": [["analisis", "ejecucion"], ["ejecucion", "sintesis"]],
        }
        compilador.validar(g, capacidades=False)
        _acomodar(g, {})
        return {"grafo": g, "degradado": True, "motivo": motivo, "sesion": sesion}

    if dry_run:
        return _degradado("dry_run: no se invoco ningun agente")

    rt_elegido = _runtime_para_chatear(runtime)
    if not rt_elegido:
        return _degradado("no hay ningun ejecutor instalado en esta maquina")

    ESTRUCTURA = (
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
        "1. Los identificadores de nodos deben ser alfanumericos cortos (ej. n1, n2, auditor, tester).\n"
        "2. Debe ser un DAG aciclico valido (sin ciclos, sin auto-referencias).\n"
        "3. Distribui el trabajo entre los runtimes disponibles (claude-code, opencode, antigravity, hermes).\n"
    )
    if actual is not None:
        prompt = (
            "Sos el arquitecto de flujos de ORQUESTER. Este es el grafo DAG actual:\n\n"
            f"{json.dumps(_grafo_desnudo(actual), ensure_ascii=False, indent=2)}\n\n"
            f"Cambio pedido: {desc}\n\n"
            "Devolve el grafo COMPLETO ya modificado, UNICAMENTE como objeto JSON "
            "(sin explicaciones ni prosa alrededor).\n"
            + ESTRUCTURA +
            "4. CONSERVA el `id` exacto de los nodos que no cambian: es lo que "
            "permite no re-dibujar el lienzo entero.\n"
            "5. No toques los nodos que el cambio pedido no menciona.\n"
        )
    else:
        prompt = (
            "Sos el arquitecto de flujos de ORQUESTER. Diseña un grafo DAG de tareas "
            "optimo para el siguiente requerimiento:\n\n"
            f"Requerimiento: {desc}\n\n"
            "Responde UNICAMENTE con un objeto JSON valido (sin explicaciones, sin "
            "comentarios, sin markdown de envoltorio excepto ```json si es necesario).\n"
            + ESTRUCTURA +
            "4. Incluye entre 2 y 5 nodos segun la complejidad requerida.\n"
        )

    try:
        # `texto`, no `respuesta`: es la clave que devuelve `chat_backend`.
        # Con la clave mal, `resp` quedaba vacia, `json.loads("")` tiraba, y el
        # `except` de abajo devolvia SIEMPRE la plantilla de tres nodos. La
        # feature "generar grafo con IA" nunca invoco a un agente de verdad.
        r = dispatcher.run_chat(rt_elegido, prompt, sesion=sesion, timeout=120)
        resp = (r.get("texto") or "").strip()
        if "```json" in resp:
            resp = resp.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in resp:
            resp = resp.split("```", 1)[1].split("```", 1)[0].strip()

        obj = json.loads(resp)
        if not isinstance(obj, dict) or "nodos" not in obj or "aristas" not in obj:
            raise ValueError("Estructura de grafo incompleta")
        obj["board"] = obj.get("board") or slug
        # Antes de la mezcla: rechaza lo que devolvio el agente con un mensaje
        # que habla de lo que el agente hizo mal (ids repetidos, aristas
        # colgadas, un ciclo).
        compilador.validar(obj, capacidades=False)
        _acomodar(obj, previas)
        # Y despues: la mezcla puede devolverle a un nodo un campo del grafo
        # viejo que ya no es valido. Si el agente le cambio el runtime de
        # `opencode` a `antigravity`, el `esfuerzo: max` que traia deja de
        # existir. Sin esta segunda pasada se colaba y fallaba recien al
        # compilar, lejos de donde se causo.
        compilador.validar(obj, capacidades=False)
        return {"grafo": obj, "degradado": False, "motivo": "",
                # Si el CLI no informa sesion se conserva la que habia: perder el
                # id a mitad de la charla arranca una conversacion nueva sin
                # avisar, y el agente se olvida del grafo del que venimos hablando.
                "sesion": r.get("sesion") or sesion}
    except Exception as e:
        return _degradado(f"{rt_elegido} no devolvio un grafo usable: "
                          f"{type(e).__name__}: {str(e)[:200]}")


def _reintentar_nodo(board: str, task_id: str) -> dict:
    """Reintentar un nodo bloqueado desbloqueándolo en kanban_db.

    Se mira el estado ANTES de tocar nada. `unblock_task` no valida de donde
    viene: sobre una card `done` la manda a `ready` y el nodo se vuelve a
    ejecutar pisando su propio entregable, y sobre una `running` le saca la
    card al dispatcher que la tiene reclamada. Los unicos estados de los que
    se puede volver son los que este boton dice atender.
    """
    conn = _conn(board)
    t = k.get_task(conn, task_id)
    if not t:
        raise ValueError(f"no existe la card {task_id}")
    # `failed` no existe en `VALID_STATUSES`: un fallo terminal en Hermes
    # termina en `triage`, y uno del dispatcher nuestro en `blocked`.
    if t.status not in ("blocked", "triage", "scheduled"):
        raise ValueError(f"la card {task_id} esta en '{t.status}': "
                         "solo se reintenta lo bloqueado")
    k.unblock_task(conn, task_id)
    return {"ok": True, "task_id": task_id, "estado_previo": t.status}


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


def _diff_grafos(g1: dict, g2: dict) -> dict:
    """Calcula la diferencia estructural entre dos grafos DAG."""
    nodos1 = {n["id"]: n for n in g1.get("nodos", []) if not compilador.es_nota(n)}
    nodos2 = {n["id"]: n for n in g2.get("nodos", []) if not compilador.es_nota(n)}

    ids1 = set(nodos1.keys())
    ids2 = set(nodos2.keys())

    agregados = sorted(list(ids2 - ids1))
    eliminados = sorted(list(ids1 - ids2))
    comunes = sorted(list(ids1 & ids2))

    modificados = []
    for nid in comunes:
        n1 = nodos1[nid]
        n2 = nodos2[nid]
        cambios = {}
        for campo in ("titulo", "runtime", "modelo", "esfuerzo", "presupuesto_usd", "workspace"):
            v1 = n1.get(campo)
            v2 = n2.get(campo)
            if v1 != v2:
                cambios[campo] = {"antes": v1, "despues": v2}

        h1 = sorted(list(n1.get("herramientas") or []))
        h2 = sorted(list(n2.get("herramientas") or []))
        if h1 != h2:
            cambios["herramientas"] = {"antes": h1, "despues": h2}

        if cambios:
            modificados.append({
                "id": nid,
                "cambios": cambios,
            })

    def _aristas_set(g):
        return {tuple(a[:2]) for a in g.get("aristas", []) if len(a) >= 2}

    ar1 = _aristas_set(g1)
    ar2 = _aristas_set(g2)

    aristas_agregadas = [list(a) for a in sorted(list(ar2 - ar1))]
    aristas_eliminadas = [list(a) for a in sorted(list(ar1 - ar2))]

    partes_resumen = []
    if agregados:
        partes_resumen.append(f"+{len(agregados)} nodo(s)")
    if eliminados:
        partes_resumen.append(f"-{len(eliminados)} nodo(s)")
    if modificados:
        partes_resumen.append(f"{len(modificados)} nodo(s) modificado(s)")
    if aristas_agregadas:
        partes_resumen.append(f"+{len(aristas_agregadas)} arista(s)")
    if aristas_eliminadas:
        partes_resumen.append(f"-{len(aristas_eliminadas)} arista(s)")

    return {
        "ok": True,
        "identicos": not (agregados or eliminados or modificados or aristas_agregadas or aristas_eliminadas),
        "resumen": ", ".join(partes_resumen) if partes_resumen else "Sin cambios",
        "nodos_agregados": agregados,
        "nodos_eliminados": eliminados,
        "nodos_modificados": modificados,
        "aristas_agregadas": aristas_agregadas,
        "aristas_eliminadas": aristas_eliminadas,
    }


def _diff_snapshots(snap_id: str, grafo_actual: dict = None, compare_id: str = None) -> dict:
    """Comparar un snapshot con el grafo actual o con otro snapshot."""
    snap1 = _restaurar_snapshot(snap_id)["snapshot"]
    g1 = snap1.get("grafo", {})
    if compare_id:
        snap2 = _restaurar_snapshot(compare_id)["snapshot"]
        g2 = snap2.get("grafo", {})
        desc_comparado = snap2.get("descripcion", compare_id)
    else:
        g2 = grafo_actual or {}
        desc_comparado = "Lienzo actual"

    diff = _diff_grafos(g1, g2)
    diff["base"] = {"id": snap_id, "descripcion": snap1.get("descripcion", snap_id)}
    diff["comparado"] = {"id": compare_id, "descripcion": desc_comparado}
    return diff


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
import corrida
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
    corrida.correr(board, log=corrida.imprimir)
    
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


def _generar_reporte_corrida(board: str, formato: str = "markdown") -> dict:
    """Generar informe completo de auditoría y ejecución de un board en Markdown o HTML."""
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

        runs = k.list_runs(conn, t.id)
        ultimo_run = runs[-1] if runs else None
        resumen = (ultimo_run.summary if ultimo_run and ultimo_run.summary else "").strip()

        if t.result or resumen:
            salida_nodo = (t.result or "").strip()
            cerca = "`" * max(3, max((len(m) for m in re.findall(r"`+", salida_nodo)),
                                     default=0) + 1)
            sum_line = f"\n> **Resumen**: {resumen}\n" if resumen else ""
            res_block = f"\n{cerca}\n{salida_nodo}\n{cerca}\n" if salida_nodo else ""
            secciones_entregables.append(
                f"### Nodo `{tid}`: {titulo}\n- **Estado**: `{st}` | **Runtime**: `{rt}`"
                f"{sum_line}{res_block}")

    if formato == "html":
        cards = []
        for t in tasks:
            tid = t.id
            titulo = t.title or "(sin titulo)"
            st = t.status or "todo"
            bg_st = "#2ea043" if st == "done" else ("#d73a49" if st in ("blocked", "triage") else "#6a737d")
            res_txt = html.escape(t.result or "(sin entregable)")
            runs_t = k.list_runs(conn, t.id)
            sum_t = (runs_t[-1].summary if runs_t and runs_t[-1].summary else "").strip()
            cards.append(f"""
            <div style="background:#1e222b; border:1px solid #333a47; border-radius:8px; padding:16px; margin-bottom:16px;">
              <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #333a47; padding-bottom:8px; margin-bottom:12px;">
                <div><h3 style="margin:0; font-size:15px; color:#f0f3f6;">{html.escape(titulo)}</h3><span style="font-size:11px; color:#8b949e;">ID: {tid} · Asignado a: {t.assignee}</span></div>
                <span style="background:{bg_st}; color:#fff; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:bold;">{st.upper()}</span>
              </div>
              {f'<div style="margin-bottom:8px; font-size:12.5px; color:#c9d1d9;"><b>Resumen:</b> {html.escape(sum_t)}</div>' if sum_t else ''}
              {f'<pre style="background:#161920; border:1px solid #2a313d; border-radius:6px; padding:10px; font-size:12px; white-space:pre-wrap; color:#f0f3f6; overflow-x:auto;">{res_txt}</pre>' if t.result else ''}
            </div>
            """)
        html_out = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>Reporte Ejecutivo — {html.escape(slug)}</title>
<style>body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: #0f1217; color: #c9d1d9; line-height: 1.6; padding: 32px; }} .container {{ max-width: 900px; margin: 0 auto; }}</style>
</head>
<body><div class="container">
  <h1 style="color:#f0f3f6; margin-bottom:4px;">Reporte Ejecutivo: {html.escape(slug)}</h1>
  <div style="color:#8b949e; font-size:12px; margin-bottom:20px;">Generado el {fecha_str} por ORQUESTER Studio</div>
  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px; margin-bottom:24px;">
    <div style="background:#161920; border:1px solid #2a313d; border-radius:8px; padding:12px; text-align:center;"><div style="font-size:11px; color:#8b949e;">PROGRESO</div><div style="font-size:18px; font-weight:bold; color:#58a6ff;">{res.get('progreso_pct', 0)}%</div></div>
    <div style="background:#161920; border:1px solid #2a313d; border-radius:8px; padding:12px; text-align:center;"><div style="font-size:11px; color:#8b949e;">COMPLETADAS</div><div style="font-size:18px; font-weight:bold; color:#2ea043;">{res.get('terminados', 0)}/{res.get('total', 0)}</div></div>
    <div style="background:#161920; border:1px solid #2a313d; border-radius:8px; padding:12px; text-align:center;"><div style="font-size:11px; color:#8b949e;">FALLIDAS</div><div style="font-size:18px; font-weight:bold; color:#d73a49;">{res.get('fallidos', 0)}</div></div>
    <div style="background:#161920; border:1px solid #2a313d; border-radius:8px; padding:12px; text-align:center;"><div style="font-size:11px; color:#8b949e;">CONSUMO TOTAL</div><div style="font-size:18px; font-weight:bold; color:#58a6ff;">US$ {tot.get('costo_usd', 0.0):.4f}</div></div>
  </div>
  <h2 style="color:#f0f3f6; font-size:16px;">Entregables</h2>
  {''.join(cards) if cards else '<p style="color:#8b949e;">No hay tareas registradas.</p>'}
</div></body></html>"""
        return {"ok": True, "board": slug, "formato": "html", "reporte": html_out}

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
        "formato": "markdown",
        "reporte": "\n".join(md),
    }


def _generar_dataset_jsonl(board: str = None) -> dict:
    """Exportar tareas e historial de ejecución en formato JSONL para benchmarking y dataset.

    Los campos salen de `kanban_db.Task`, no de como uno cree que se llaman:
    esto leia `t.description` y `t.summary`, que NO existen (son `body` y el
    summary del run), y `total_usd` en `por_nodo`, que es `costo_usd`. El
    `except` por board se comia el AttributeError y el endpoint devolvia 200
    con cero registros sobre un board lleno de cards `done`.
    """
    boards = [board.strip()] if (board and board.strip()) else [b.get("slug") for b in k.list_boards() if b.get("slug")]
    lineas = []
    total_registros = 0
    omitidos = []

    for b in boards:
        try:
            conn = _conn(b)
            tasks = k.list_tasks(conn)
            consumo = _consumo(b)
            por_nodo = consumo.get("por_nodo", {})
            for t in tasks:
                rt = "hermes"
                if t.assignee and ":" in t.assignee:
                    rt = t.assignee.split(":", 1)[1]
                # `None` y no `0.0`: un backend por suscripcion no informa
                # medidor, y contarlo como gratis corre el promedio de un dataset
                # que existe justo para medir costo. `_consumo` ya distingue los
                # dos casos; el exportador los aplastaba en uno.
                gasto = (por_nodo.get(t.id, {}) or {}).get("costo_usd")
                dur = None
                if t.started_at and t.completed_at:
                    try:
                        dur = round(float(t.completed_at) - float(t.started_at), 2)
                    except (ValueError, TypeError):
                        pass
                # El resumen es del ultimo intento: la card guarda el `result`,
                # y el `summary` vive en el run que lo produjo.
                runs = k.list_runs(conn, t.id)
                resumen = (runs[-1].summary or "").strip() if runs else ""

                registro = {
                    "task_id": t.id,
                    "board": b,
                    "title": t.title,
                    "runtime": rt,
                    "status": t.status,
                    "prompt": t.body or "",
                    "model_override": t.model_override,
                    "reasoning_effort": t.reasoning_effort,
                    "gasto_usd": gasto,
                    "duracion_segundos": dur,
                    "entregable": (t.result or "").strip(),
                    "summary": resumen,
                    "intentos": len(runs),
                    "created_at": t.created_at,
                    "completed_at": t.completed_at,
                }
                lineas.append(json.dumps(registro, ensure_ascii=False))
                total_registros += 1
        except Exception as e:
            # Se DICE cual board no entro. Un `continue` mudo devuelve un
            # dataset incompleto con cara de completo, que es peor que un error.
            omitidos.append({"board": b, "motivo": f"{type(e).__name__}: {e}"})

    return {
        "ok": True,
        "board": board or "todos",
        "total_registros": total_registros,
        "omitidos": omitidos,
        "jsonl": "\n".join(lineas),
    }


def _arrancar(board: str, tope_usd: float = None) -> dict:
    # El nombre se valida ACA y no en la ruta: `_arrancar` tiene dos llamadores
    # (`/api/correr` y el modo App), y una guarda puesta en uno solo es como
    # llegamos a la mitad de los bugs de esta rama.
    #
    # NO es por inyeccion de comandos: se probo. `subprocess.run` con lista y
    # sin `shell=True` cita los argumentos incluso para un `.cmd` (Python 3.11.14
    # devolvio `arg=["inocente & echo pwned"]` y no ejecuto nada), y ademas
    # `_resolver_argv` ya resuelve el shim de npm al `.exe` real. Es por el
    # `.json` del grafo, que se escribe con este nombre.
    _ruta_segura(board, GRAFOS, "board")
    # Y ademas por el slug: `_ruta_segura` acepta mayusculas y espacios, y el
    # kanban no. Un `POST /api/correr {"board": "mi board"}` pasaba, arrancaba
    # el hilo, devolvia `{"ok": true}` y moria adentro con un ValueError que
    # nadie leia: el Studio decia que arranco y no habia arrancado nada.
    board = corrida.slug(board)

    # El bucle vive en `dispatcher/corrida.py`, no aca: lo comparten el Studio,
    # el exportador MCP y el CLI. Aca queda lo que es del Studio --el hilo, el
    # registro de lo que corre y el tope que se esta aplicando-- y nada mas.
    def _correr():
        try:
            corrida.correr(board, tope_usd=tope_usd)
        finally:
            _corriendo.pop(board, None)

    # Chequeo y alta bajo el mismo lock: ver `_LOCK_ARRANQUE`.
    h = threading.Thread(target=_correr, daemon=True)
    with _LOCK_ARRANQUE:
        if board in _corriendo and _corriendo[board].is_alive():
            return {"ok": False, "motivo": "ya hay un dispatcher corriendo en este board"}
        corrida.limpiar_parada(board)  # un arranque anterior pudo dejarlo marcado
        _topes[board] = tope_usd       # None = esta corrida va sin tope
        _corriendo[board] = h
    h.start()
    return {"ok": True}


def _listar_workspaces() -> dict:
    """Inspecciona los workspaces y directorios de trabajo de los boards registrados."""
    boards = [b.get("slug") for b in k.list_boards() if b.get("slug")]
    salida = []
    total_bytes = 0
    total_items = 0

    for b in boards:
        try:
            ws_root = k.workspaces_root(board=b)
            if not ws_root.exists():
                continue
            items = []
            board_bytes = 0
            for p in ws_root.iterdir():
                if p.is_dir():
                    tam = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                    items.append({
                        "id": p.name,
                        "ruta": str(p),
                        "tamano_bytes": tam,
                        "tamano_humano": f"{tam / 1024:.1f} KB" if tam < 1024 * 1024 else f"{tam / (1024 * 1024):.2f} MB",
                    })
                    board_bytes += tam
            if items:
                salida.append({
                    "board": b,
                    "raiz": str(ws_root),
                    "total_workspaces": len(items),
                    "tamano_bytes": board_bytes,
                    "tamano_humano": f"{board_bytes / 1024:.1f} KB" if board_bytes < 1024 * 1024 else f"{board_bytes / (1024 * 1024):.2f} MB",
                    "workspaces": items,
                })
                total_bytes += board_bytes
                total_items += len(items)
        except Exception:
            continue

    return {
        "ok": True,
        "total_boards": len(salida),
        "total_workspaces": total_items,
        "tamano_total_bytes": total_bytes,
        "tamano_total_humano": f"{total_bytes / 1024:.1f} KB" if total_bytes < 1024 * 1024 else f"{total_bytes / (1024 * 1024):.2f} MB",
        "boards": salida,
    }


def _limpiar_workspaces(board: str = None, task_id: str = None) -> dict:
    """Limpia los workspaces scratch de un board o tarea, sin tocar los vivos.

    El scratch de un nodo ES su `cwd`: `workspaces_root(board)/<task_id>`
    (`kanban_db` lo arma asi). Borrarlo mientras el nodo corre le saca el piso
    al agente a mitad del trabajo — y esto se dispara desde un boton que, sin
    argumentos, barre TODOS los boards. Por eso se saltean dos cosas:

      - los boards con un dispatcher nuestro en marcha (`_corriendo`), enteros,
        porque entre listar y borrar puede arrancar un nodo mas;
      - las cards en `running` de los demas boards, que es lo que ejecuta el
        dispatcher de Hermes, que corre fuera de este proceso.

    Lo que se saltea se DEVUELVE: una purga que dice "0 eliminados" sin explicar
    que no toco nada porque habia una corrida parece rota.
    """
    eliminados = 0
    bytes_liberados = 0
    omitidos = []

    boards = [board] if board else [b.get("slug") for b in k.list_boards() if b.get("slug")]
    for b in boards:
        # `is_alive()`, igual que `_arrancar`: `_corriendo` puede tener el hilo
        # de una corrida que ya termino y todavia no se saco del diccionario, y
        # eso no es motivo para negarse a limpiar.
        if b in _corriendo and _corriendo[b].is_alive():
            omitidos.append({"board": b, "motivo": "hay un dispatcher corriendo"})
            continue
        try:
            ws_root = k.workspaces_root(board=b)
            if not ws_root.exists():
                continue
            try:
                activas = {t.id for t in k.list_tasks(k.connect(board=b), status="running")}
            except Exception:
                # Sin poder leer que corre, NO se borra: el default de una
                # operacion destructiva es no hacer nada.
                omitidos.append({"board": b, "motivo": "no se pudo leer que esta corriendo"})
                continue
            for p in list(ws_root.iterdir()):
                if not p.is_dir():
                    continue
                if task_id and p.name != task_id:
                    continue
                if p.name in activas:
                    omitidos.append({"board": b, "task": p.name, "motivo": "la card esta corriendo"})
                    continue
                tam = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p, ignore_errors=True)
                if not p.exists():
                    eliminados += 1
                    bytes_liberados += tam
                else:
                    omitidos.append({"board": b, "task": p.name,
                                     "motivo": "no se pudo borrar (archivo en uso?)"})
        except Exception as e:
            omitidos.append({"board": b, "motivo": f"{type(e).__name__}: {e}"})
            continue

    return {
        "ok": True,
        "workspaces_eliminados": eliminados,
        "bytes_liberados": bytes_liberados,
        "omitidos": omitidos,
        "liberado_humano": f"{bytes_liberados / 1024:.1f} KB" if bytes_liberados < 1024 * 1024 else f"{bytes_liberados / (1024 * 1024):.2f} MB",
    }


def _analizar_workspace(ruta: str) -> dict:
    """Inspecciona una carpeta para detectar su stack, tests y estado git."""
    if not ruta or not str(ruta).strip():
        raise ValueError("Ruta de workspace vacia")
    p = Path(ruta).resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError(f"La carpeta '{ruta}' no existe o no es un directorio")
    # Sin allowlist de rutas, a proposito. Se probo una (home + raiz del repo) y
    # rechazaba `A:/Proyectos/skills`, que es exactamente el caso de uso: "elegi
    # una carpeta" no puede significar "elegi una carpeta de esta lista". Y no
    # compra nada: `/api/*` ya exige token, y lo siguiente que hace el producto
    # con esa carpeta es correr un agente con shell adentro. Restringir la
    # LECTURA mientras se permite la EJECUCION no es una barrera, es un
    # inconveniente. La barrera real es el token, mas no ejecutar lo que la
    # carpeta diga (ver `_sin_filtros`).

    stack = "Desconocido"
    frameworks = []
    comando_tests = ""

    # Detectar Node.js
    pkg = p / "package.json"
    if pkg.exists():
        stack = "Node.js / TypeScript" if (p / "tsconfig.json").exists() else "Node.js / JavaScript"
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            scripts = data.get("scripts", {})
            if "react" in deps: frameworks.append("React")
            if "next" in deps: frameworks.append("Next.js")
            if "vue" in deps: frameworks.append("Vue")
            if "svelte" in deps: frameworks.append("Svelte")
            if "express" in deps: frameworks.append("Express")
            if "vitest" in deps or "test" in scripts:
                comando_tests = "npm test"
        except Exception:
            pass

    # Detectar Python
    py_markers = [p / "pyproject.toml", p / "requirements.txt", p / "setup.py", p / "Pipfile", p / "uv.lock"]
    tiene_py = any(m.exists() for m in py_markers) or bool(list(p.glob("*.py"))) or bool(list(p.glob("*/*.py")))
    if tiene_py:
        if stack == "Desconocido":
            stack = "Python"
        else:
            stack += " + Python"
        if (p / "tests").exists() or (p / "test").exists() or (p / "pytest.ini").exists():
            comando_tests = comando_tests or "pytest"
        else:
            comando_tests = comando_tests or "python -m unittest"

    # Rust y Go SUMAN, no pisan: un repo con backend en Go y frontend en Node
    # se reportaba como "Go" a secas, y peor, el `comando_tests` que ya se habia
    # elegido quedaba sobreescrito. Un monorepo es el caso normal, no el raro.
    for marcador, nombre, cmd in (("Cargo.toml", "Rust", "cargo test"),
                                  ("go.mod", "Go", "go test ./...")):
        if (p / marcador).exists():
            stack = nombre if stack == "Desconocido" else f"{stack} + {nombre}"
            comando_tests = comando_tests or cmd

    # Detectar Git. Los `-c` no son decoracion: `git` lee el `.git/config` de la
    # carpeta que se le apunta, y `core.fsmonitor` / `core.hooksPath` son
    # comandos que ejecuta el propio git. Este endpoint se dispara con el boton
    # "Abrir", ANTES de que nadie autorice correr un agente, asi que mirar una
    # carpeta descargada no puede ejecutar lo que esa carpeta diga. Un `-c` de
    # la linea de comandos le gana al config del repo.
    SIN_HOOKS = ["-c", "core.fsmonitor=", "-c", "core.hooksPath=",
                 "-c", "core.pager=cat", "-c", "protocol.ext.allow=never"]

    def _git(*args, extra=()) -> str:
        """La salida de un `git` en esta carpeta, o vacio si no se pudo."""
        try:
            r = subprocess.run(["git", *SIN_HOOKS, *extra, *args], cwd=str(p),
                               capture_output=True, text=True, timeout=3,
                               stdin=subprocess.DEVNULL, encoding="utf-8",
                               errors="replace")
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _sin_filtros() -> list:
        """Neutraliza los filtros de contenido que el repo tenga definidos.

        `core.fsmonitor` no era el unico comando que git ejecuta solo: para
        decidir si un archivo esta modificado, `git status` corre el filtro
        `clean` que el `.gitattributes` del repo elija, y el comando de ese
        filtro sale del `.git/config` del repo. Los nombres no se pueden
        adivinar, pero SI se pueden leer: `git config --get-regexp` solo lee, y
        cada driver encontrado se pisa con `cat` (identidad) por linea de
        comandos, que le gana al config del repo.
        """
        salida = []
        for linea in _git("config", "--local", "--name-only", "--get-regexp",
                          r"^filter\..*\.(clean|smudge|process)$").splitlines():
            clave = linea.strip()
            # `cat` (identidad) para clean/smudge, que esperan un comando que
            # copie stdin a stdout; vacio para `process`, que se apaga asi.
            salida += ["-c", f"{clave}=cat" if clave.endswith(("clean", "smudge"))
                       else f"{clave}="]
        return salida

    es_git = _git("rev-parse", "--is-inside-work-tree") == "true"
    git_branch = _git("branch", "--show-current") if es_git else ""
    # El unico de los cuatro que corre filtros de contenido.
    git_cambios = (len(_git("status", "--porcelain", extra=_sin_filtros()).splitlines())
                   if es_git else 0)
    ultimo_commit = _git("log", "-1", "--oneline") if es_git else ""

    return {
        "ok": True,
        "nombre": p.name,
        "ruta": str(p),
        "stack": stack,
        "frameworks": frameworks,
        # Vacio si no se detecto ninguno. Estaba con `or "pytest"`, asi que a un
        # repo de Node sin script de test se le decia al agente que corriera
        # pytest: un comando que no existe ahi, presentado como el correcto.
        "comando_tests": comando_tests,
        "es_git": es_git,
        "git_branch": git_branch or "main",
        "git_cambios_pendientes": git_cambios,
        "ultimo_commit": ultimo_commit,
    }


# El dialogo corre en un proceso APARTE, no en el hilo del handler. Tk es de un
# solo hilo y `ThreadingHTTPServer` atiende cada pedido en el suyo: crear una
# ventana ahi es una forma conocida de dejar el servidor clavado. Un hijo que
# vive tres segundos no puede llevarse nada puesto.
_DIALOGO = """
import tkinter, tkinter.filedialog as fd
r = tkinter.Tk()
r.withdraw()
r.attributes("-topmost", True)   # si no, sale DETRAS del navegador
print(fd.askdirectory(title="Elegi la carpeta del proyecto") or "")
r.destroy()
"""


def _elegir_carpeta(timeout: int = 300) -> dict:
    """Abre el selector de carpetas del sistema y devuelve lo que se eligio.

    El navegador no sirve para esto: ni `showDirectoryPicker()` ni
    `<input webkitdirectory>` entregan la ruta absoluta --dan un handle o rutas
    relativas-- y el motor necesita una ruta absoluta para el `cwd` del agente.
    Como el Studio corre en la misma maquina que el usuario (`HOSTS_OK` son
    127.0.0.1 y localhost), el que puede abrir el dialogo es el servidor.

    Si no hay display --contenedor, sesion sin escritorio, Studio en otra
    maquina-- se dice y listo: el campo de texto de al lado sigue existiendo y
    es el camino que ya funcionaba.
    """
    try:
        r = subprocess.run([sys.executable, "-c", _DIALOGO], capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "cancelado": True,
                "motivo": "el dialogo quedo abierto demasiado tiempo"}
    except Exception as e:
        return {"ok": False, "motivo": f"no se pudo abrir el selector: {e}"}

    if r.returncode != 0:
        # Tipicamente `TclError: no display name`. El motivo del propio Tk es
        # mas util que uno inventado por nosotros.
        detalle = (r.stderr or "").strip().splitlines()[-1:] or ["sin detalle"]
        return {"ok": False,
                "motivo": f"esta maquina no puede abrir un selector de carpetas: {detalle[0]}"}

    ruta = (r.stdout or "").strip()
    if not ruta:
        return {"ok": False, "cancelado": True, "motivo": "no se eligio ninguna carpeta"}
    # Se devuelve YA analizada: elegir y que no pase nada obliga a un segundo
    # clic para lo unico que uno iba a hacer despues.
    return {"ok": True, **_analizar_workspace(ruta)}


def _orquestar_intencion(cuerpo: dict) -> dict:
    """Una intencion + una carpeta -> un DAG compilado y, si se pide, corriendo.

    Es el unico endpoint del modo App: elige la plantilla, le pone los valores
    del workspace y lo lanza. Todo lo que hace ya existia suelto en el Studio
    (`/api/plantilla`, `/api/compilar`, `/api/correr`); aca se encadena para
    que el usuario no tenga que saber que existen.

    Por eso NO reimplementa ninguno de los tres: sustituye con
    `mcp.sustituir` (el mismo que usa `/api/compilar`, que ademas cubre TODOS
    los campos de texto del nodo y no solo dos), compila con la firma real de
    `compilador.compilar` y arranca con `_arrancar`, que es el que sabe atender
    los nodos `hermes` y respetar el tope. La version anterior llamaba a
    `compilar(..., conexion=..., tope_usd=...)`, parametros que no existen, y a
    `_correr_dispatcher_board`, una funcion que no existe: con `ejecutar=True`
    esto devolvia 400 SIEMPRE, o sea que el modo App nunca ejecuto nada.
    """
    ws = (cuerpo.get("workspace") or "").strip()
    info_ws = _analizar_workspace(ws) if ws else {}
    ruta_ws = info_ws.get("ruta") or ws or str(RAIZ)

    intencion = (cuerpo.get("intencion") or "").lower().strip()
    prompt = (cuerpo.get("prompt") or "").strip()
    ejecutar = bool(cuerpo.get("ejecutar", True))
    try:
        tope_usd = float(cuerpo["tope_usd"]) if str(cuerpo.get("tope_usd") or "").strip() else None
    except (TypeError, ValueError):
        raise ValueError(f"tope invalido: {cuerpo.get('tope_usd')!r}")
    if tope_usd is not None and tope_usd <= 0:
        raise ValueError("el tope de gasto tiene que ser > 0")

    mapa_plantillas = {
        "seguridad": "auditoria-seguridad-cso-strix",
        "web": "desarrollo-web-deliberate",
        "refactor": "refactor-yagni-ponytail",
        "feature": "pipeline-feature-fullstack",
        "diff": "revision-de-repo",
        "explicar": "explicar-un-repo",
        # Las claves son los `id` de las tarjetas del modo App, no una version
        # corta de ellos: `triage` y `documentar` no coincidian con
        # `triage-de-bug` y `documentar-cambios`, y `segunda-opinion` no estaba.
        # Esas tres tarjetas caian al `elif prompt` y en vez de su plantilla el
        # usuario recibia un diseno generico del agente, sin ningun aviso.
        "triage-de-bug": "triage-de-bug",
        "documentar-cambios": "documentar-cambios",
        "segunda-opinion": "segunda-opinion",
    }

    degradado, motivo = False, ""
    nom_pl = mapa_plantillas.get(intencion)
    if nom_pl:
        pl_path = _ruta_segura(nom_pl, PLANTILLAS, "plantilla")
        if not pl_path.is_file():
            raise ValueError(f"no existe la plantilla '{nom_pl}'")
        g = json.loads(pl_path.read_text(encoding="utf-8"))
    elif prompt:
        # Sin `dry_run` fijo: estaba hardcodeado en True, asi que este camino
        # NUNCA le hablaba a un agente y devolvia siempre la misma plantilla de
        # tres nodos con la frase del usuario pegada adentro. Ahora se le pide
        # de verdad, y si no hay con quien hablar se DICE (`degradado`) en vez
        # de presentar el fallback como un diseno.
        res_ia = _generar_grafo(prompt, runtime=cuerpo.get("runtime") or "claude-code",
                                dry_run=bool(cuerpo.get("dry_run")))
        g = res_ia.get("grafo")
        degradado, motivo = bool(res_ia.get("degradado")), res_ia.get("motivo") or ""
        nom_pl = "sintesis-ia"
    else:
        nom_pl = "revision-de-repo"
        g = json.loads((PLANTILLAS / f"{nom_pl}.json").read_text(encoding="utf-8"))

    # Solo los marcadores que el grafo PIDE. `sustituir` falla nombrando el que
    # falte, que es mejor que dejar pasar un `{{modulo}}` literal al cwd de un
    # agente, y mejor que rellenar a ciegas ocho claves que quiza no use.
    disponibles = {
        "repo": ruta_ws,
        "ruta": ruta_ws,
        # Si no se detecto, se le dice al agente que lo averigue el, en vez de
        # mandarle un comando inventado que va a fallar.
        "comando_tests": (info_ws.get("comando_tests")
                          or "(averigualo vos: no se detecto un comando de tests)"),
        "modulo": prompt or info_ws.get("nombre") or "el modulo principal",
        "feature": prompt or "la funcionalidad pedida",
        "pregunta": prompt or f"Explicar la arquitectura de {info_ws.get('nombre') or 'este repositorio'}",
        "sintoma": prompt or "comportamiento inesperado observado",
        "commits": str(cuerpo.get("commits") or 5),
    }
    pedidos = mcp.parametros(g)
    faltan = [p for p in pedidos if p not in disponibles]
    if faltan:
        raise ValueError(f"la plantilla '{nom_pl}' pide parametros que el modo App "
                         f"no sabe completar: {faltan}")
    g = mcp.sustituir(g, {p: disponibles[p] for p in pedidos})

    # Sufijo aleatorio y no `int(time.time()) % 100000`: dos orquestaciones de
    # la misma plantilla en el mismo segundo compartian board, y la segunda
    # compilaba sus cards ADENTRO de la corrida de la primera. Es el mismo bug
    # que ya tenian los ids de snapshot (test 36b).
    board_slug = f"{nom_pl}-{secrets.token_hex(3)}"
    g["board"] = board_slug
    for n in g.get("nodos", []):
        # El workspace es lo que hace que el agente vea el repo del usuario y no
        # el scratch vacio de Hermes (SS4.1). Una plantilla puede no declararlo.
        if not compilador.es_nota(n) and not n.get("workspace"):
            n["workspace"] = ruta_ws

    compilador.validar(g, capacidades=False)
    for nid, xy in disposicion.ordenar(g).items():
        for n in g.get("nodos", []):
            if n["id"] == nid:
                n["x"], n["y"] = xy["x"], xy["y"]

    arranque = None
    if ejecutar:
        # Compilar ANTES de guardar: si el preflight de capacidades rechaza el
        # grafo, el .json ya escrito quedaba de huerfano en `ui/grafos` con un
        # board que no existe en ningun lado.
        compilador.compilar(g, board=board_slug)
        arranque = _arrancar(board_slug, tope_usd)
    _archivo(board_slug).write_text(json.dumps(g, indent=2, ensure_ascii=False),
                                    encoding="utf-8")

    return {
        "ok": True,
        "board": board_slug,
        "plantilla": nom_pl,
        "degradado": degradado,
        "motivo": motivo,
        "workspace_analizado": info_ws,
        "grafo": g,
        "total_nodos": len(g.get("nodos", [])),
        "ejecutando": bool(arranque and arranque.get("ok")),
        "arranque": arranque,
    }


class Handler(BaseHTTPRequestHandler):
    def _responder(self, codigo, cuerpo, tipo="application/json"):
        datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if tipo == "application/json":
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(datos)

    def _host_ok(self) -> bool:
        """La cabecera `Host` tiene que nombrar a esta maquina (ver HOSTS_OK)."""
        crudo = (self.headers.get("Host") or "").strip()
        # Un literal IPv6 viaja entre corchetes y TIENE `:` adentro: cortar por
        # el ultimo `:` convertia `[::1]` en `[:` y el Studio se negaba a
        # atenderse a si mismo en IPv6.
        if crudo.startswith("["):
            cierre = crudo.find("]")
            host = crudo[1:cierre] if cierre != -1 else crudo.lstrip("[")
        else:
            host = crudo.rsplit(":", 1)[0]
        host = host.strip("[]").lower()
        if host in HOSTS_OK:
            return True
        self._responder(421, {"error": f"Host '{host}' no atendido aca"})
        return False

    def _fallo_400(self, prefijo: str, e: Exception):
        """400 con el motivo, y el traceback donde se pueda leer.

        Estas rutas contestaban 400 y tiraban el traceback: un fallo interno
        quedaba indistinguible de un cuerpo mal armado y no dejaba rastro en
        ningun lado. El manejador de afuera si lo imprime antes del 500, y era
        justo la senal que estos `except` de mas adentro se comian. El codigo
        sigue siendo 400 a proposito: lo que entra por aca es el grafo que
        manda el cliente, y el error casi siempre es suyo.

        El traceback va solo para lo INESPERADO: `ValueError` es la convencion
        del repo para "el pedido esta mal" (`_ruta_segura`, `ErrorDeGrafo`) y
        volcar su pila por cada nombre invalido es ruido que tapa justo la
        senal que esto viene a rescatar.
        """
        if not isinstance(e, ValueError):
            traceback.print_exc()
        return self._responder(400, {"error": f"{prefijo}: {e}"})

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

    def _stream_eventos(self, board: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        ultimo_hash = None
        for _ in range(60):
            try:
                est = _estado(board)
                est_raw = json.dumps(est, sort_keys=True)
                if est_raw != ultimo_hash:
                    msg = f"data: {json.dumps(est)}\n\n".encode("utf-8")
                    self.wfile.write(msg)
                    self.wfile.flush()
                    ultimo_hash = est_raw
                time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break                      # el cliente se fue: nada que decirle
            except Exception as e:
                # Las cabeceras ya salieron, asi que un 500 no es opcion. Sin
                # este evento el navegador ve un stream cortado, lo toma por
                # caida y reconecta para siempre: un hilo y una apertura de
                # SQLite por intento, y ningun error visible en la UI.
                try:
                    aviso = json.dumps({"error": str(e)})
                    self.wfile.write(
                        f"event: error\ndata: {aviso}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
                break

    def _get(self, ruta, params):
        if ruta == "/":
            return self._responder(200, HTML.read_bytes(), "text/html; charset=utf-8")
        if ruta == "/api/eventos":
            return self._stream_eventos(params.get("board", "orquester"))
        if ruta == "/api/skills/catalogo":
            return self._responder(200, _catalogo_skills())
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
            return self._responder(200, _generar_reporte_corrida(params.get("board", "orquester"), params.get("formato", "markdown")))
        if ruta == "/api/reporte/descargar":
            board = params.get("board", "orquester")
            formato = params.get("formato", "markdown").lower()
            res = _generar_reporte_corrida(board, formato)
            if not res.get("ok"):
                return self._responder(400, res)
            ext = "html" if formato == "html" else "md"
            mime = "text/html; charset=utf-8" if formato == "html" else "text/markdown; charset=utf-8"
            contenido_bytes = res["reporte"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", f'attachment; filename="reporte-{board}.{ext}"')
            self.send_header("Content-Length", str(len(contenido_bytes)))
            self.end_headers()
            self.wfile.write(contenido_bytes)
            return
        if ruta == "/api/snapshots":
            return self._responder(200, _listar_snapshots(params.get("board", "")))
        if ruta == "/api/workspaces":
            return self._responder(200, _listar_workspaces())
        if ruta == "/api/workspace/analizar":
            return self._responder(200, _analizar_workspace(params.get("ruta", "")))
        if ruta == "/api/exportar-dataset":
            return self._responder(200, _generar_dataset_jsonl(params.get("board")))
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
                    return self._fallo_400("no se pudo generar mermaid", e)
            if self.path == "/api/generar-grafo":
                desc = cuerpo.get("descripcion") or ""
                rt = cuerpo.get("runtime") or "claude-code"
                dry = bool(cuerpo.get("dry_run"))
                try:
                    # Con `actual`, el pedido REFINA ese grafo en vez de diseñar
                    # uno nuevo, y `sesion` encadena los refinamientos.
                    res = _generar_grafo(desc, runtime=rt, dry_run=dry,
                                         actual=cuerpo.get("actual") or None,
                                         sesion=cuerpo.get("sesion") or None)
                    return self._responder(200, {"ok": True, **res})
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
                    return self._fallo_400("error generando workflow CI", e)
            if self.path == "/api/exportar-python":
                try:
                    return self._responder(200, {"ok": True, "script": _generar_script_python(cuerpo)})
                except Exception as e:
                    return self._fallo_400("error generando script python", e)
            if self.path == "/api/simular":
                try:
                    return self._responder(200, _simular_flujo(cuerpo))
                except Exception as e:
                    return self._fallo_400("error simulando flujo", e)
            if self.path == "/api/analizar-grafo":
                try:
                    return self._responder(200, {"ok": True, "hallazgos": compilador.analizar(cuerpo)})
                except Exception as e:
                    return self._fallo_400("error analizando grafo", e)
            if self.path == "/api/trazabilidad-grafo":
                nid = cuerpo.get("nodo") or ""
                g = cuerpo.get("grafo") or {}
                try:
                    return self._responder(200, compilador.trazabilidad(g, nid))
                except Exception as e:
                    return self._fallo_400("error en trazabilidad", e)
            if self.path == "/api/snapshot":
                b = cuerpo.get("board") or "orquester"
                g = cuerpo.get("grafo") or {}
                desc = cuerpo.get("descripcion") or ""
                try:
                    return self._responder(200, _guardar_snapshot(b, g, desc))
                except Exception as e:
                    return self._fallo_400("error guardando snapshot", e)
            if self.path == "/api/snapshot/restaurar":
                sid = cuerpo.get("id") or ""
                try:
                    return self._responder(200, _restaurar_snapshot(sid))
                except Exception as e:
                    return self._fallo_400("error restaurando snapshot", e)
            if self.path == "/api/snapshot/diff":
                sid = cuerpo.get("id") or ""
                cid = cuerpo.get("compare_id") or None
                g = cuerpo.get("grafo_actual") or None
                try:
                    return self._responder(200, _diff_snapshots(sid, grafo_actual=g, compare_id=cid))
                except Exception as e:
                    return self._fallo_400("error comparando snapshots", e)
            if self.path == "/api/reporte-corrida":
                b = cuerpo.get("board") or "orquester"
                try:
                    return self._responder(200, _generar_reporte_corrida(b))
                except Exception as e:
                    return self._fallo_400("error generando reporte", e)
            if self.path == "/api/guardar-plantilla":
                nom = cuerpo.get("nombre") or ""
                desc = cuerpo.get("descripcion") or ""
                g = cuerpo.get("grafo") or {}
                try:
                    return self._responder(200, _guardar_plantilla(
                        nom, desc, g, pisar=bool(cuerpo.get("pisar"))))
                except FileExistsError as e:
                    return self._responder(409, {"error": str(e)})
                except Exception as e:
                    return self._fallo_400("error guardando plantilla", e)
            if self.path == "/api/workspaces/limpiar":
                b = cuerpo.get("board") or None
                tid = cuerpo.get("task_id") or None
                try:
                    return self._responder(200, _limpiar_workspaces(board=b, task_id=tid))
                except Exception as e:
                    return self._fallo_400("error limpiando workspaces", e)
            if self.path == "/api/exportar-dataset":
                b = cuerpo.get("board") or None
                try:
                    return self._responder(200, _generar_dataset_jsonl(b))
                except Exception as e:
                    return self._fallo_400("error exportando dataset", e)
            if self.path == "/api/workspace/elegir":
                try:
                    return self._responder(200, _elegir_carpeta())
                except Exception as e:
                    return self._fallo_400("error abriendo el selector", e)
            if self.path == "/api/workspace/analizar":
                try:
                    return self._responder(200, _analizar_workspace(cuerpo.get("ruta", "")))
                except Exception as e:
                    return self._fallo_400("error analizando workspace", e)
            if self.path == "/api/orquestar-intencion":
                try:
                    return self._responder(200, _orquestar_intencion(cuerpo))
                except Exception as e:
                    return self._fallo_400("error orquestando intencion", e)
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
                res = _parar_board(board)
                return self._responder(200, res)
            if self.path == "/api/nodo/parar":
                board = cuerpo.get("board", "orquester")
                tid = cuerpo.get("task_id") or ""
                return self._responder(200, _parar_nodo(board, tid))
            if self.path == "/api/gate/aprobar":
                board = cuerpo.get("board", "orquester")
                tid = cuerpo.get("task_id") or ""
                resultado = cuerpo.get("resultado") or None
                return self._responder(200, _aprobar_gate(board, tid, resultado))
            if self.path == "/api/reporte/generar":
                board = cuerpo.get("board", "orquester")
                formato = cuerpo.get("formato", "markdown")
                return self._responder(200, _generar_reporte_corrida(board, formato))
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
