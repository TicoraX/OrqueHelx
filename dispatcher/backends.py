"""`run_backend`: invocar un agente externo y devolver el contrato.

Implementa ARQUITECTURA.md SS12. Cada backend es una entrada de tabla
`(argv, parser)` — el mismo subprocess con distinto argv y distinto parser.
No hay una clase por backend porque no hace falta.

Las reglas de abajo salieron de la fase de verificacion, no del gusto:
  - el schema va en archivo, nunca inline (el escapado inline se rompe)
  - nunca juzgar por exit code (`agy -p` falla con EXIT=0)
  - el goal nombra el backend (unica diferencia observada entre que se
    invoque el binario y que no)
  - denylist de flags de bypass de permisos
"""
import json, os, re, shutil, subprocess, tempfile
from pathlib import Path

# SS5: jsonschema es dependencia DURA, y por eso se importa aca arriba. Estaba
# adentro de `_validar`, y cuando faltaba el ModuleNotFoundError salia envuelto
# en un BackendError con el nombre del CLI adelante: "opencode:
# ModuleNotFoundError". No matchea `_PERMANENTES`, asi que el nodo se
# reintentaba dos veces culpando a un agente que habia respondido bien.
# Una dependencia ausente tiene que romper una vez, al arrancar, y decir la verdad.
try:
    import jsonschema
except ImportError:                       # pragma: no cover - solo sin la dep
    raise SystemExit(
        "falta `jsonschema`, que es dependencia dura (SS5): sin ella la "
        "validacion del contrato se saltea y los guardrails quedan inertes.\n"
        "  uv run --python 3.11 --with jsonschema --with pyyaml python <lo que ibas a correr>")

# El AgentAdapterOutput de SS5.
CONTRATO = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failure"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
}

# Turnos de agente que se le conceden a `claude -p`. Arranco en 5 y una
# revision de codigo real murio con `error_max_turns` leyendo el diff: 5 alcanza
# para contar archivos, no para trabajar. El techo existe igual, porque un
# agente sin limite de turnos es una factura sin limite.
MAX_TURNS = 30

# SS4.1: un worker de Hermes escalo solo a --dangerously-skip-permissions.
# El nuestro no puede. Se compara sobre el argv ya construido.
FLAGS_PROHIBIDOS = {
    "--dangerously-skip-permissions",
    "--yolo",
    "--no-sandbox",
    "--disable-permissions",
}


class BackendError(RuntimeError):
    """El backend no devolvio algo que cumpla el contrato.

    `permanente` dice si reintentar tiene sentido. Va ACA y no en una lista de
    substrings del lado del dispatcher: esa lista ya se desincronizo una vez
    (el catch paso de FileNotFoundError a OSError, el mensaje cambio, y un
    workspace inexistente se reintentaba dos veces como transitorio), y volvia
    a pasar con cualquier excepcion nueva del parser. Quien LEVANTA el error
    sabe si se arregla solo; quien lee el texto, no.
    """
    permanente = False


class ErrorPermanente(BackendError):
    """No se arregla reintentando: falta un binario, un flag prohibido, un
    runtime que no existe. Reintentar solo gasta tiempo y cuota."""
    permanente = True


def _texto_de_jsonl(stdout: str) -> str:
    """OpenCode emite JSONL de eventos: la respuesta son los `text` concatenados."""
    partes = []
    for linea in stdout.splitlines():
        linea = linea.strip()
        if not linea.startswith("{"):
            continue
        try:
            ev = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "text":
            continue
        # El texto vive en `part.text`, no en la raiz del evento. Verificado
        # contra la salida real de opencode v1.18.18.
        txt = (ev.get("part") or {}).get("text")
        if isinstance(txt, str):
            partes.append(txt)
    return "".join(partes)


def _primer_objeto(texto: str) -> dict:
    """Extraer el primer objeto JSON balanceado de un texto con prosa alrededor.

    `agy --json-schema` gobierna `structured_output`, no `response`, y
    claude/opencode devuelven el contrato embebido en markdown. Scanner de
    llaves en vez de regex: una regex no balancea anidamiento.
    """
    for i, ch in enumerate(texto):
        if ch != "{":
            continue
        prof, en_str, escape = 0, False, False
        for j in range(i, len(texto)):
            c = texto[j]
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_str = not en_str
            elif not en_str:
                if c == "{":
                    prof += 1
                elif c == "}":
                    prof -= 1
                    if prof == 0:
                        try:
                            obj = json.loads(texto[i:j + 1])
                        except json.JSONDecodeError:
                            break          # falso positivo, seguir buscando
                        if isinstance(obj, dict):
                            return obj
                        break
    raise BackendError("no hay ningun objeto JSON en la salida")


def _parse_envelope(stdout: str, clave: str) -> dict:
    """agy y claude devuelven un objeto con el contrato en una clave conocida."""
    obj = _primer_objeto(stdout)
    # El envelope de claude reporta por que TERMINO. Sin mirarlo, un corte por
    # limite de turnos se reportaba como "la salida no trae structured_output",
    # que manda a buscar el bug en el parser en vez de en el limite.
    if obj.get("subtype", "").startswith("error_") or obj.get("is_error"):
        motivo = obj.get("subtype") or "error"
        detalle = "; ".join(obj.get("errors") or []) or obj.get("terminal_reason", "")
        raise BackendError(f"el agente termino con {motivo}: {detalle[:300]}")
    anidado = obj.get(clave)
    if isinstance(anidado, dict):
        return anidado
    if isinstance(anidado, str):
        return _primer_objeto(anidado)
    # Sin envelope: el objeto de arriba ya puede ser el contrato.
    if "status" in obj and "summary" in obj:
        return obj
    for alterna in ("response", "result", "output"):
        val = obj.get(alterna)
        if isinstance(val, str):
            try:
                return _primer_objeto(val)
            except BackendError:
                continue
    raise BackendError(f"la salida no trae '{clave}' ni el contrato en la raiz")


# (argv, parser). `esquema` es una RUTA, nunca el JSON inline.
BACKENDS = {
    "antigravity": (
        # `--model` toma el slug de la primera columna de `agy models`
        # (ej. `gemini-3.1-pro-high`), no el nombre para mostrar.
        lambda goal, esquema, modelo=None: [
            "agy", "-p", goal,
            "--output-format", "json", "--json-schema", esquema,
            "--print-timeout", "5m",
            *(["--model", modelo] if modelo else []),
        ],
        lambda out: _parse_envelope(out, "structured_output"),
    ),
    "claude-code": (
        # Ojo: claude exige el schema **inline**, no una ruta — al reves que agy.
        # Verificado: con una ruta responde `--json-schema is not valid JSON`.
        # Pasarlo inline es seguro porque el argv va directo a CreateProcess, sin
        # shell de por medio; la regla de "schema en archivo" era contra el
        # escapado de PowerShell, que aca no participa.
        lambda goal, esquema, modelo=None: [
            "claude", "-p", goal,
            "--output-format", "json", "--json-schema", json.dumps(CONTRATO),
            "--max-turns", str(MAX_TURNS),
            *(["--model", modelo] if modelo else []),
        ],
        lambda out: _parse_envelope(out, "structured_output"),
    ),
    "opencode": (
        # opencode no tiene flag de schema: el contrato viaja en el goal.
        # opencode quiere `proveedor/modelo` (ej. `deepseek/deepseek-chat`).
        lambda goal, esquema, modelo=None: [
            "opencode", "run", goal, "--format", "json",
            *(["--model", modelo] if modelo else []),
        ],
        lambda out: _primer_objeto(_texto_de_jsonl(out) or out),
    ),
}


# --- Consumo -----------------------------------------------------------------
# Formas capturadas de cada CLI, no supuestas:
#   opencode  step_finish.part -> {"tokens": {total,input,output,cache:{read,write}},
#                                  "cost": 0.0093}   (varios eventos: se suman)
#   claude    envelope         -> {"total_cost_usd": .., "usage": {input_tokens,
#                                  output_tokens, cache_read_input_tokens, ..}}
#   agy       envelope         -> {"usage": {input_tokens, output_tokens,
#                                  thinking_tokens, cache_read_tokens, total_tokens}}
#
# `agy` NO informa costo, y no es un olvido: corre contra una suscripcion, no
# contra un medidor. `claude` si informa `total_cost_usd`, que es lo que ese
# trabajo **habria costado por API** — la comparacion que le da sentido a
# "corre sobre tus suscripciones" (IDEAS §1).

def _uso_vacio(runtime: str) -> dict:
    return {"runtime": runtime, "entrada": 0, "salida": 0, "total": 0,
            "cache_lectura": 0, "costo_usd": None, "turnos": None, "duracion_s": None}


def _uso_opencode(stdout: str) -> dict:
    u = _uso_vacio("opencode")
    for linea in stdout.splitlines():
        if not linea.strip().startswith("{"):
            continue
        try:
            parte = (json.loads(linea).get("part") or {})
        except json.JSONDecodeError:
            continue
        tok = parte.get("tokens") or {}
        if not tok and parte.get("cost") is None:
            continue
        u["entrada"] += tok.get("input", 0) or 0
        u["salida"] += tok.get("output", 0) or 0
        u["total"] += tok.get("total", 0) or 0
        u["cache_lectura"] += (tok.get("cache") or {}).get("read", 0) or 0
        if parte.get("cost") is not None:
            u["costo_usd"] = (u["costo_usd"] or 0) + parte["cost"]
    return u


def _uso_claude(stdout: str) -> dict:
    u = _uso_vacio("claude-code")
    try:
        o = _primer_objeto(stdout)
    except BackendError:
        return u
    us = o.get("usage") or {}
    u["entrada"] = us.get("input_tokens", 0) or 0
    u["salida"] = us.get("output_tokens", 0) or 0
    u["cache_lectura"] = us.get("cache_read_input_tokens", 0) or 0
    # claude no da un total: se arma con lo que si informa, cache incluida,
    # porque los tokens cacheados igual se leyeron aunque cuesten menos.
    u["total"] = (u["entrada"] + u["salida"] + u["cache_lectura"]
                  + (us.get("cache_creation_input_tokens", 0) or 0))
    u["costo_usd"] = o.get("total_cost_usd")
    u["turnos"] = o.get("num_turns")
    u["duracion_s"] = round(o["duration_ms"] / 1000, 1) if o.get("duration_ms") else None
    return u


def _uso_agy(stdout: str) -> dict:
    u = _uso_vacio("antigravity")
    try:
        o = _primer_objeto(stdout)
    except BackendError:
        return u
    us = o.get("usage") or {}
    u["entrada"] = us.get("input_tokens", 0) or 0
    u["salida"] = us.get("output_tokens", 0) or 0
    u["total"] = us.get("total_tokens", 0) or 0
    u["cache_lectura"] = us.get("cache_read_tokens", 0) or 0
    u["turnos"] = o.get("num_turns")
    u["duracion_s"] = round(o["duration_seconds"], 1) if o.get("duration_seconds") else None
    return u


# Aparte de BACKENDS a proposito: los tests desempacan `(argv, parser)` y un
# tercer elemento los romperia sin necesidad.
USO = {"opencode": _uso_opencode, "claude-code": _uso_claude, "antigravity": _uso_agy}


def _resolver_argv(argv: list[str]) -> list[str]:
    """Resolver argv[0] a algo que CreateProcess sepa lanzar.

    En Windows los CLIs de agentes llegan como shims de npm: `opencode` es un
    `.ps1`, no un `.exe`, y subprocess sin shell no lo ejecuta. Se resuelve el
    binario real y se antepone el interprete que corresponda. `shell=True` no
    es opcion: el goal va en el argv y meterlo en una linea de comando es una
    inyeccion esperando a pasar.
    """
    # Orden deliberado: .exe y .cmd primero. `shutil.which` a secas devuelve el
    # `.ps1` cuando PATHEXT lo incluye, y el shim de PowerShell no reenvia bien
    # un goal multilinea — el contexto del padre llega mutilado.
    ruta = next(
        (r for ext in (".exe", ".cmd", ".bat", "")
         if (r := shutil.which(argv[0] + ext))
         and os.path.splitext(r)[1].lower() != ".ps1"),
        None,
    ) or shutil.which(argv[0])
    if ruta is None:
        raise ErrorPermanente(f"no esta en el PATH: {argv[0]}")
    ext = os.path.splitext(ruta)[1].lower()
    if ext in (".cmd", ".bat"):
        # Un .cmd se enruta por cmd.exe, que **parte el argumento en el primer
        # salto de linea**: el goal llega mutilado y los flags posteriores se
        # pierden. Verificado — opencode recibio solo la primera linea y perdio
        # `--format json`. Los shims de npm son una linea que llama a un .exe
        # real; si lo encontramos, lo usamos y los newlines sobreviven.
        # ponytail: heuristica sobre el texto del shim. Si algun dia falla,
        # cae al .cmd y ahi si hay que aplanar el goal a una sola linea.
        try:
            texto = Path(ruta).read_text(encoding="utf-8", errors="replace")
            for cand in re.findall(r'"([^"]+\.exe)"', texto):
                real = cand.replace("%dp0%", str(Path(ruta).parent))
                real = os.path.normpath(real)
                if os.path.isfile(real):
                    return [real, *argv[1:]]
        except OSError:
            pass
        return [ruta, *argv[1:]]
    if ext == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", ruta, *argv[1:]]
    return [ruta, *argv[1:]]


def _validar(obj: dict) -> dict:
    jsonschema.validate(obj, CONTRATO)
    return obj


# --- Esfuerzo y tope de gasto --------------------------------------------
# Los niveles NO son los mismos en los tres CLIs (`claude` llega a `max`, `agy`
# corta en `high`, `opencode` los llama `--variant`), asi que se validan contra
# la lista del backend. Un nivel invalido falla ruidoso en vez de ignorarse:
# pedir `max` y que corra en el default es la peor forma de perder plata.
ESFUERZO = {
    "claude-code": ("--effort", ("low", "medium", "high", "xhigh", "max")),
    "antigravity": ("--effort", ("low", "medium", "high")),
    "opencode": ("--variant", ("minimal", "high", "max")),
}

# Tope de gasto por invocacion. Solo claude lo tiene nativo; en los demas el
# limite lo aplica el dispatcher entre nodos (`loop.tick`), que es mas grueso.
TOPE_GASTO = {"claude-code": "--max-budget-usd"}


def _flags_extra(runtime: str, esfuerzo: str = None,
                 presupuesto: float = None) -> list[str]:
    """Flags de esfuerzo y presupuesto para este runtime, si los soporta."""
    extra = []
    if esfuerzo:
        flag, validos = ESFUERZO.get(runtime, (None, ()))
        if not flag:
            raise ErrorPermanente(f"{runtime} no acepta esfuerzo por invocacion")
        if esfuerzo not in validos:
            raise ErrorPermanente(
                f"{runtime} no acepta esfuerzo '{esfuerzo}'. Validos: {list(validos)}")
        extra += [flag, esfuerzo]
    if presupuesto is not None and TOPE_GASTO.get(runtime):
        # Se manda solo si el CLI lo entiende. En los demas NO se simula: un
        # tope que se cree puesto y no lo esta es peor que no tener tope.
        extra += [TOPE_GASTO[runtime], f"{max(presupuesto, 0):.4f}"]
    return extra


# --- Chat: conversacion con sesion, sin contrato JSON --------------------
# Un nodo del grafo entrega un AgentAdapterOutput; un chat entrega texto y
# tiene que ACORDARSE del turno anterior. Los tres CLIs guardan la sesion y la
# retoman por id. Medido, no supuesto (§10):
#   claude-code : session_id     -> --resume
#   opencode    : sessionID      -> --session
#   antigravity : conversation_id-> --conversation
# En los tres, retomar devuelve el MISMO id, asi que la sesion no se renumera
# a mitad de la conversacion.
CHAT = {
    "claude-code": (
        lambda msg, sesion, modelo: [
            "claude", "-p", msg, "--output-format", "json",
            "--max-turns", str(MAX_TURNS),
            *(["--resume", sesion] if sesion else []),
            *(["--model", modelo] if modelo else []),
        ],
        lambda out: (_primer_objeto(out).get("result") or "",
                     _primer_objeto(out).get("session_id")),
    ),
    "antigravity": (
        lambda msg, sesion, modelo: [
            "agy", "-p", msg, "--output-format", "json", "--print-timeout", "5m",
            *(["--conversation", sesion] if sesion else []),
            *(["--model", modelo] if modelo else []),
        ],
        lambda out: (_primer_objeto(out).get("response") or "",
                     _primer_objeto(out).get("conversation_id")),
    ),
    "opencode": (
        lambda msg, sesion, modelo: [
            "opencode", "run", msg, "--format", "json",
            *(["--session", sesion] if sesion else []),
            *(["--model", modelo] if modelo else []),
        ],
        lambda out: (_texto_de_jsonl(out), _sesion_de_jsonl(out)),
    ),
}


def _sesion_de_jsonl(stdout: str) -> str | None:
    """El `sessionID` de los eventos de opencode. El primero que aparezca."""
    for linea in stdout.splitlines():
        if not linea.strip().startswith("{"):
            continue
        try:
            ev = json.loads(linea)
        except json.JSONDecodeError:
            continue
        for sitio in (ev, ev.get("part") or {}):
            if isinstance(sitio, dict) and isinstance(sitio.get("sessionID"), str):
                return sitio["sessionID"]
    return None


def chat_backend(runtime: str, mensaje: str, *, sesion: str = None,
                 timeout: int = 600, cwd: str = None,
                 herramientas: list[str] = None, modelo: str = None,
                 esfuerzo: str = None, presupuesto: float = None) -> dict:
    """Un turno de conversacion. Devuelve texto, la sesion y el consumo.

    Comparte con `run_backend` la denylist de flags, el stdin cerrado y el
    `cwd`: es el mismo agente con las mismas barandas, solo que sin contrato.
    """
    if runtime not in CHAT:
        raise ErrorPermanente(f"runtime desconocido para chat: {runtime}")
    construir_argv, parser = CHAT[runtime]
    argv = construir_argv(mensaje, sesion, modelo)
    if herramientas and runtime == "claude-code":
        argv += ["--allowedTools", ",".join(herramientas)]
    argv += _flags_extra(runtime, esfuerzo, presupuesto)

    proc = _correr(runtime, argv, timeout=timeout, cwd=cwd)
    try:
        texto, sesion_nueva = parser(proc.stdout)
    except Exception as e:
        cola = (proc.stderr or proc.stdout or "")[-400:]
        raise BackendError(
            f"{runtime}: {type(e).__name__}: {e}. Ultimos 400 chars: {cola!r}") from e
    if not (texto or "").strip():
        raise BackendError(f"{runtime} no devolvio texto")

    extraer = USO.get(runtime)
    return {"texto": texto,
            # Si el CLI no informa sesion, se conserva la que ya tenia: perder
            # el id a mitad de la charla arrancaria una conversacion nueva sin
            # avisar, y el usuario veria al agente olvidarse de todo.
            "sesion": sesion_nueva or sesion,
            "uso": extraer(proc.stdout) if extraer else _uso_vacio(runtime)}


def _correr(runtime: str, argv: list[str], *, timeout: int, cwd: str = None):
    """Lanzar un CLI de agente. Las reglas de invocacion viven ACA, una vez.

    Estaban duplicadas y la duplicacion costo caro: el bug de `stdin` heredado
    se arreglo primero en `run_backend` y aparecio de nuevo, identico, en el
    exportador MCP. Una sola copia o vuelve a pasar.
    """
    prohibidos = FLAGS_PROHIBIDOS.intersection(argv)
    if prohibidos:
        raise ErrorPermanente(f"flags de bypass prohibidos: {sorted(prohibidos)}")
    try:
        return subprocess.run(
            _resolver_argv(argv), capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            # stdin cerrado, SIEMPRE. Sin esto el CLI hereda el stdin del padre
            # y puede quedarse leyendolo — y cuando el padre es el servidor MCP,
            # ese stdin **es el canal JSON-RPC**: el agente se come los mensajes
            # del protocolo y las dos partes se cuelgan. Verificado: opencode
            # responde en 9s suelto y colgaba >600s lanzado desde el servidor.
            stdin=subprocess.DEVNULL,
            cwd=cwd,
        )
    except OSError as e:
        # OSError y no solo FileNotFoundError: un `cwd` inexistente tira
        # NotADirectoryError, y sin atraparlo se escapaba del pool de hilos y
        # mataba el tick entero por un nodo mal configurado.
        raise ErrorPermanente(f"no se pudo lanzar {runtime}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise BackendError(f"{runtime} excedio {timeout}s") from e


def run_backend(runtime: str, goal: str, *, timeout: int = 600,
                cwd: str = None, herramientas: list[str] = None,
                modelo: str = None, esfuerzo: str = None,
                presupuesto: float = None) -> dict:
    """Invocar `runtime` con `goal` y devolver un AgentAdapterOutput validado.

    `cwd` es el directorio donde corre el agente: sin esto heredaria el del
    dispatcher, que es una coincidencia y no una decision. Un nodo que revisa
    un repo tiene que correr *en* ese repo.

    `modelo` es opcional y **por nodo**: los tres CLIs aceptan `--model`, cada
    uno con su forma (slug en agy, `proveedor/modelo` en opencode, nombre en
    claude). Sin modelo, cada CLI usa el suyo por defecto.

    `herramientas` son los permisos que el nodo necesita (ej. `["Read","Bash"]`).
    Solo `claude` los toma por linea de comandos (`--allowedTools`); opencode y
    agy gobiernan permisos por su propia config. Se documenta en vez de
    simularlo, porque un permiso que se cree concedido y no lo esta es peor que
    uno ausente.
    """
    if runtime not in BACKENDS:
        raise ErrorPermanente(f"runtime desconocido: {runtime}")
    construir_argv, parser = BACKENDS[runtime]

    # El goal nombra el backend y lleva el contrato en texto. Para opencode es
    # la unica via; para los otros dos es cinturon ademas de tirantes.
    goal_final = (
        f"[ejecutor: {runtime}] {goal}\n\n"
        f"Responde EXCLUSIVAMENTE con un objeto JSON que cumpla este esquema, "
        f"sin prosa alrededor: {json.dumps(CONTRATO)}"
    )

    with tempfile.TemporaryDirectory() as tmp:
        ruta_esquema = Path(tmp) / "contrato.json"
        ruta_esquema.write_text(json.dumps(CONTRATO), encoding="utf-8")
        # El modelo se pasa solo si lo hay: un builder de dos argumentos —los
        # que usan los tests para sustituir un backend— sigue siendo valido
        # mientras nadie le pida modelo. Si se lo piden, falla ruidoso, que es
        # lo correcto: ese builder no sabe pasarlo.
        argv = (construir_argv(goal_final, str(ruta_esquema), modelo) if modelo
                else construir_argv(goal_final, str(ruta_esquema)))
        if herramientas and runtime == "claude-code":
            argv += ["--allowedTools", ",".join(herramientas)]
        argv += _flags_extra(runtime, esfuerzo, presupuesto)

        proc = _correr(runtime, argv, timeout=timeout, cwd=cwd)

    # Deliberadamente NO se mira proc.returncode: `agy -p` sale 0 aunque falle
    # (SS4.1, verificado). El veredicto sale de parsear la salida.
    try:
        contrato = _validar(parser(proc.stdout))
        # El consumo se adjunta DESPUES de validar, asi el contrato se valida
        # tal cual lo devolvio el agente y no con campos nuestros encima.
        extraer = USO.get(runtime)
        contrato["uso"] = extraer(proc.stdout) if extraer else _uso_vacio(runtime)
        return contrato
    except Exception as e:
        # Envolver TODO, no solo BackendError: un parser puede tirar KeyError o
        # ValueError, y el loop solo sabe manejar BackendError. Una excepcion
        # cruda se le escaparia y mataria el dispatcher entero por una card.
        cola = (proc.stderr or proc.stdout or "")[-400:]
        raise BackendError(
            f"{runtime}: {type(e).__name__}: {e}. Ultimos 400 chars: {cola!r}"
        ) from e
