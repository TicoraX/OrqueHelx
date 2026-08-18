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


if __name__ == "__main__":
    import json
    print(json.dumps(tabla(), indent=2, ensure_ascii=False))
