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
import shutil
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
        "modelo_forma": "nombre del modelo (ej. claude-sonnet-4-6)",
        "listar_modelos": None,
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
        "listar_modelos": None,
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
        "listar_modelos": "hermes model",
        "ojo": "lo ejecuta el dispatcher de Hermes, no el de ORQUESTER (§12)",
    },
}


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
