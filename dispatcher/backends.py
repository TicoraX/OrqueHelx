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

# El AgentAdapterOutput de SS5.
CONTRATO = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failure"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
}

# SS4.1: un worker de Hermes escalo solo a --dangerously-skip-permissions.
# El nuestro no puede. Se compara sobre el argv ya construido.
# Turnos de agente que se le conceden a `claude -p`. Arranco en 5 y una
# revision de codigo real murio con `error_max_turns` leyendo el diff: 5 alcanza
# para contar archivos, no para trabajar. El techo existe igual, porque un
# agente sin limite de turnos es una factura sin limite.
MAX_TURNS = 30

FLAGS_PROHIBIDOS = {
    "--dangerously-skip-permissions",
    "--yolo",
    "--no-sandbox",
    "--disable-permissions",
}


class BackendError(RuntimeError):
    """El backend no devolvio algo que cumpla el contrato."""


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
        lambda goal, esquema: [
            "agy", "-p", goal,
            "--output-format", "json", "--json-schema", esquema,
            "--print-timeout", "5m",
        ],
        lambda out: _parse_envelope(out, "structured_output"),
    ),
    "claude-code": (
        # Ojo: claude exige el schema **inline**, no una ruta — al reves que agy.
        # Verificado: con una ruta responde `--json-schema is not valid JSON`.
        # Pasarlo inline es seguro porque el argv va directo a CreateProcess, sin
        # shell de por medio; la regla de "schema en archivo" era contra el
        # escapado de PowerShell, que aca no participa.
        lambda goal, esquema: [
            "claude", "-p", goal,
            "--output-format", "json", "--json-schema", json.dumps(CONTRATO),
            "--max-turns", str(MAX_TURNS),
        ],
        lambda out: _parse_envelope(out, "structured_output"),
    ),
    "opencode": (
        # opencode no tiene flag de schema: el contrato viaja en el goal.
        lambda goal, esquema: ["opencode", "run", goal, "--format", "json"],
        lambda out: _primer_objeto(_texto_de_jsonl(out) or out),
    ),
}


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
        raise BackendError(f"no esta en el PATH: {argv[0]}")
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
    # SS5: jsonschema es dependencia dura. Sin el, la validacion se saltea en
    # silencio y los guardrails quedan inertes.
    import jsonschema
    jsonschema.validate(obj, CONTRATO)
    return obj


def run_backend(runtime: str, goal: str, *, timeout: int = 600,
                cwd: str = None, herramientas: list[str] = None) -> dict:
    """Invocar `runtime` con `goal` y devolver un AgentAdapterOutput validado.

    `cwd` es el directorio donde corre el agente: sin esto heredaria el del
    dispatcher, que es una coincidencia y no una decision. Un nodo que revisa
    un repo tiene que correr *en* ese repo.

    `herramientas` son los permisos que el nodo necesita (ej. `["Read","Bash"]`).
    Solo `claude` los toma por linea de comandos (`--allowedTools`); opencode y
    agy gobiernan permisos por su propia config. Se documenta en vez de
    simularlo, porque un permiso que se cree concedido y no lo esta es peor que
    uno ausente.
    """
    if runtime not in BACKENDS:
        raise BackendError(f"runtime desconocido: {runtime}")
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
        argv = construir_argv(goal_final, str(ruta_esquema))
        if herramientas and runtime == "claude-code":
            argv += ["--allowedTools", ",".join(herramientas)]

        prohibidos = FLAGS_PROHIBIDOS.intersection(argv)
        if prohibidos:
            raise BackendError(f"flags de bypass prohibidos: {sorted(prohibidos)}")

        try:
            proc = subprocess.run(
                _resolver_argv(argv), capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                # stdin cerrado, SIEMPRE. Sin esto el CLI hereda el stdin del
                # padre y puede quedarse leyendolo — y cuando el padre es el
                # servidor MCP, ese stdin **es el canal JSON-RPC**: el agente se
                # come los mensajes del protocolo y las dos partes se cuelgan.
                # Verificado: opencode responde en 9s suelto y colgaba >600s
                # lanzado desde el servidor MCP.
                stdin=subprocess.DEVNULL,
                cwd=cwd,
            )
        except OSError as e:
            # OSError y no solo FileNotFoundError: un `cwd` inexistente tira
            # NotADirectoryError, y sin atraparlo se escapaba del pool de hilos
            # y mataba el tick entero por un nodo mal configurado.
            raise BackendError(f"no se pudo lanzar {runtime}: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise BackendError(f"{runtime} excedio {timeout}s") from e

    # Deliberadamente NO se mira proc.returncode: `agy -p` sale 0 aunque falle
    # (SS4.1, verificado). El veredicto sale de parsear la salida.
    try:
        return _validar(parser(proc.stdout))
    except Exception as e:
        # Envolver TODO, no solo BackendError: un parser puede tirar KeyError o
        # ValueError, y el loop solo sabe manejar BackendError. Una excepcion
        # cruda se le escaparia y mataria el dispatcher entero por una card.
        cola = (proc.stderr or proc.stdout or "")[-400:]
        raise BackendError(
            f"{runtime}: {type(e).__name__}: {e}. Ultimos 400 chars: {cola!r}"
        ) from e
