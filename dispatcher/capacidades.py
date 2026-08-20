"""Qué sabe hacer cada backend, y qué necesita para funcionar.

`ARQUITECTURA.md` §4.1 lo pidió explícitamente: **las skills bundleadas de
Hermes se desactualizan y eso degrada al worker**, no solo a la doc. El worker
no usó `--json-schema` porque su skill decía que no existía. ORQUESTER necesita
su propia tabla, verificada contra el CLI instalado.

Es también la respuesta a "que los agentes sepan lo que requieren": un agente
—o el compilador— consulta esto antes de correr, en vez de descubrir a los 600s
que faltaba un binario.

Los campos NO son opinión: cada uno salió de ejecutar el CLI y mirar la salida.
Están citados en §10.
"""
import shutil, subprocess, time
from pathlib import Path

from backends import BACKENDS, MAX_TURNS, USO

# Lo declarado. Cada linea tiene su evidencia en ARQUITECTURA.md §10.
DECLARADO = {
    "claude-code": {
        "binario": "claude",
        "schema_en_cli": True,
        "schema_como": "json-inline",       # una RUTA falla: "is not valid JSON"
        "salida": "objeto unico con envelope",
        "contrato_en": "structured_output",
        "informa_costo": True,              # total_cost_usd = equivalente API
        "informa_tokens": True,
        "acepta_permisos": True,            # --allowedTools
        "limite_turnos": MAX_TURNS,         # 5 mataba una revision de diff real
        "auth": "suscripcion o API key propia de Claude Code",
        "modelo_por_nodo": True,
        "modelo_flag": "--model",
        "modelo_forma": "alias (opus, sonnet, fable) o nombre completo",
        "listar_modelos": None,      # no tiene subcomando que liste
        "esfuerzo_flag": "--effort",
        "esfuerzos": ["low", "medium", "high", "xhigh", "max"],
        "tope_gasto_flag": "--max-budget-usd",   # el unico con tope nativo
        # Los alias los documenta su propio `--help`, no los inventamos aca.
        "alias_conocidos": ["opus", "sonnet", "fable"],
    },
    "opencode": {
        "binario": "opencode",
        "schema_en_cli": False,             # no hay flag: el contrato va en el goal
        "schema_como": None,
        "salida": "JSONL de eventos",
        "contrato_en": "concatenar part.text de los eventos type=text",
        "informa_costo": True,              # cost por step_finish, se suman
        "informa_tokens": True,
        "acepta_permisos": False,
        "limite_turnos": None,
        "auth": "el proveedor que tenga configurado opencode",
        "modelo_por_nodo": True,
        "modelo_flag": "--model",
        "modelo_forma": "proveedor/modelo (ej. deepseek/deepseek-chat)",
        "listar_modelos": "opencode models",
        # `--variant` es el esfuerzo en opencode, y los niveles dependen del
        # proveedor detras del modelo: los tres de abajo son los que documenta.
        "esfuerzo_flag": "--variant",
        "esfuerzos": ["minimal", "high", "max"],
        "tope_gasto_flag": None,
    },
    "antigravity": {
        "binario": "agy",
        "schema_en_cli": True,
        "schema_como": "ruta-a-archivo",
        "salida": "objeto unico con envelope",
        "contrato_en": "structured_output",  # `response` trae prosa, no sirve
        "informa_costo": False,              # corre por suscripcion, sin medidor
        "informa_tokens": True,
        "acepta_permisos": False,            # se configura en su settings.json
        "limite_turnos": None,
        "auth": "suscripcion de Antigravity",
        "modelo_por_nodo": True,
        "modelo_flag": "--model",
        "modelo_forma": "slug de `agy models` (ej. gemini-3.1-pro-high)",
        "listar_modelos": 'agy models',
        "esfuerzo_flag": "--effort",
        "esfuerzos": ["low", "medium", "high"],
        "tope_gasto_flag": None,
        "ojo": "sale con EXIT=0 aunque falle: juzgar por la salida, no por el codigo",
    },
    "hermes": {
        "binario": "hermes",
        "schema_en_cli": False,
        "schema_como": None,
        "salida": "lo maneja el dispatcher de Hermes",
        "contrato_en": "output_schema de la card",
        "informa_costo": False,
        "informa_tokens": False,
        "acepta_permisos": False,
        "limite_turnos": None,
        "auth": "el proveedor de Hermes (NO uno external_process, §11)",
        "modelo_por_nodo": True,
        "modelo_flag": "-m (lo pasa el dispatcher de Hermes)",
        "modelo_forma": "el que acepte el proveedor de Hermes",
        # `hermes model` NO sirve para listar: abre un selector INTERACTIVO que
        # se queda esperando en stdin. Estaba declarado aca como si listara.
        "listar_modelos": None,
        # Hermes acepta mas niveles que cualquier CLI externo
        # (`hermes_constants.VALID_REASONING_EFFORTS`), y los aplica su propio
        # dispatcher a traves de `reasoning_effort` de la card.
        "esfuerzo_flag": "(reasoning_effort de la card)",
        "esfuerzos": ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
        "tope_gasto_flag": None,
        "ojo": "lo ejecuta el dispatcher de Hermes, no el de ORQUESTER (§12)",
    },
}


# `agy models` tarda unos segundos (consulta al proveedor). Se cachea por
# proceso: los modelos no cambian entre dos clics del Studio.
_CACHE: dict[str, tuple[float, list[str]]] = {}
_TTL = 600


def modelos(runtime: str) -> dict:
    """Los modelos que acepta un runtime, preguntandoselo al CLI.

    Sale de `listar_modelos`, el campo que esta tabla ya declaraba y que nadie
    usaba: el Studio pedia el modelo como texto libre y habia que saberse el
    slug de memoria.
    """
    d = DECLARADO.get(runtime)
    if not d:
        return {"modelos": [], "fuente": f"runtime desconocido: {runtime}"}
    if d.get("alias_conocidos") and not d.get("listar_modelos"):
        return {"modelos": d["alias_conocidos"], "fuente": "alias que documenta su --help"}
    cmd = d.get("listar_modelos")
    if not cmd or not shutil.which(d["binario"]):
        return {"modelos": [], "fuente": ""}

    hit = _CACHE.get(runtime)
    if hit and time.time() - hit[0] < _TTL:
        return {"modelos": hit[1], "fuente": cmd + " (cache)"}
    try:
        # stdin cerrado por lo mismo de siempre (`backends.run_backend`): si el
        # padre es el servidor MCP, el stdin heredado es el canal JSON-RPC.
        r = subprocess.run(_resolver(cmd), capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return {"modelos": [], "fuente": f"fallo `{cmd}`: {type(e).__name__}"}

    vistos = []
    for linea in (r.stdout or "").splitlines():
        # Primera columna: `agy` devuelve `slug<TAB>etiqueta`, `opencode` solo
        # el slug. Un slug siempre trae `/`, `-`, `.` o `:`; asi se cae solo el
        # banner ("Fetching available models...") sin listarlo como modelo.
        slug = linea.split("	")[0].split()[0] if linea.split() else ""
        if slug and not slug.endswith("...") and any(c in slug for c in "/-.:")                 and slug not in vistos:
            vistos.append(slug)
    _CACHE[runtime] = (time.time(), vistos)
    return {"modelos": vistos, "fuente": cmd}


def _resolver(cmd: str) -> list[str]:
    """El argv del comando de listado, con el binario real detras del shim."""
    from backends import _resolver_argv
    return _resolver_argv(cmd.split())


def tabla() -> dict:
    """Lo declarado, más lo que se puede comprobar ahora mismo en esta máquina."""
    salida = {}
    for rt, d in DECLARADO.items():
        ruta = shutil.which(d["binario"])
        salida[rt] = {
            **d,
            "disponible": bool(ruta),
            "ruta": ruta,
            # Coherencia interna: si esta en BACKENDS, el dispatcher de ORQUESTER
            # lo ejecuta; si no, es de Hermes.
            "lo_ejecuta": "orquester" if rt in BACKENDS else "hermes",
            "extrae_consumo": rt in USO,
        }
    return salida


def faltantes(runtimes) -> list[str]:
    """Los runtimes pedidos que hoy no se pueden ejecutar."""
    t = tabla()
    return sorted({rt for rt in runtimes
                   if rt in t and not t[rt]["disponible"]})


def doctor() -> dict:
    """Diagnóstico integral de salud, runtimes, SQLite y entorno de ORQUESTER."""
    import platform, sqlite3

    sqlite_ver = sqlite3.sqlite_version
    partes = [int(p) for p in sqlite_ver.split(".") if p.isdigit()]
    wal_seguro = tuple(partes[:2]) >= (3, 51) if len(partes) >= 2 else False

    binarios = {}
    todos_bin = {
        "claude": ["claude", "--version"],
        "opencode": ["opencode", "--version"],
        "antigravity": ["agy", "--version"],
        "hermes": ["hermes", "--version"],
        "node": ["node", "--version"],
        "docker": ["docker", "--version"],
    }

    for nombre, argv in todos_bin.items():
        presente = bool(shutil.which(argv[0]))
        version = None
        if presente:
            try:
                p = subprocess.run(argv, capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
                out = (p.stdout or p.stderr or "").strip()
                version = out.splitlines()[0] if out else "disponible"
            except Exception:
                version = "disponible"
        binarios[nombre] = {
            "disponible": presente,
            "version": version,
            "ruta": shutil.which(argv[0]) or None,
        }

    runtimes_disponibles = sum(1 for b in ("claude", "opencode", "antigravity", "hermes") if binarios[b]["disponible"])

    return {
        "ok": runtimes_disponibles > 0,
        "plataforma": {
            "os": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
            "sqlite_version": sqlite_ver,
            "wal_seguro": wal_seguro,
        },
        "binarios": binarios,
        "runtimes_activos": runtimes_disponibles,
    }


def secretos_status() -> dict:
    """Inspección segura de credenciales y API keys configuradas en el entorno."""
    import os

    claves = [
        ("ANTHROPIC_API_KEY", "Anthropic", "Claude Code / Hermes"),
        ("OPENAI_API_KEY", "OpenAI", "OpenCode / Hermes"),
        ("DEEPSEEK_API_KEY", "DeepSeek", "OpenCode / DeepSeek-V3"),
        ("GEMINI_API_KEY", "Google Gemini", "Antigravity"),
        ("GROQ_API_KEY", "Groq", "Hermes Fast Inference"),
        ("OPENROUTER_API_KEY", "OpenRouter", "OpenCode / Multi-Model"),
        ("GITHUB_TOKEN", "GitHub", "MCP / Git Tools"),
        ("GH_TOKEN", "GitHub CLI", "GitHub CLI auth"),
    ]

    salida = []
    for var, proveedor, uso in claves:
        val = os.environ.get(var, "").strip()
        presente = bool(val)
        enmascarado = None
        if presente:
            if len(val) <= 8:
                enmascarado = "***"
            else:
                enmascarado = f"{val[:4]}...{val[-3:]}"
        salida.append({
            "variable": var,
            "proveedor": proveedor,
            "uso": uso,
            "presente": presente,
            "enmascarado": enmascarado,
        })

    return {
        "ok": True,
        "total_configuradas": sum(1 for c in salida if c["presente"]),
        "total_revisadas": len(salida),
        "secretos": salida,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(tabla(), indent=2, ensure_ascii=False))
