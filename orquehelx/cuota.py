"""Agotamiento de cuota: se registra solo si es inequivoco o esta medido, nunca por sospecha.

Hermes llama ``al_fallar`` (hook ``api_request_error``) en cada llamada fallida. No cambia como
Hermes clasifica el error: solo anota que proveedor se quedo sin cuota y cuando se reinicia, para
que ``delegar`` le diga al padre que paso en vez de un fallo generico.

    error del proveedor
      |- texto inequivoco (RESOURCE_EXHAUSTED de agy, 429 + "usage limit"/"quota") -> agotado
      |- error generico de claude-subscription -> se mide /usage: ventana >= 100 % -> agotado
      '- lo demas -> no es cuota
    reinicio: el ultimo entre las ventanas agotadas que aplican al modelo, o None si alguna no
    informa hora ("sin dato"; nunca se estima). Una ventana de otro modelo ("Opus week" para una
    ruta haiku) no bloquea la ruta.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Agotamiento:
    proveedor: str
    mensaje: str
    reinicio: datetime | None
    visto: float


Ventana = tuple[str, "float | None", "datetime | None"]  # (etiqueta, usado %, reinicio)


def _ventanas(snap) -> list[Ventana]:
    return [(w.label, w.used_percent, w.reset_at) for w in snap.windows] if snap else []


def _medir_claude() -> list[Ventana]:
    # ponytail: funcion privada de Hermes; el perfil de claude-subscription no implementa
    # fetch_account_usage (reportado upstream). Cambiar a fetch_account_usage cuando lo haga.
    from agent.account_usage import _fetch_anthropic_account_usage
    return _ventanas(_fetch_anthropic_account_usage())


def _medir_codex() -> list[Ventana]:
    from agent.account_usage import fetch_account_usage
    return _ventanas(fetch_account_usage("openai-codex"))


# Prefijo de proveedor -> medidor de ventanas.
MEDIDORES = {"claude-subscription": _medir_claude, "openai-codex": _medir_codex}
_FAMILIAS = ("opus", "sonnet", "haiku")

_lock = threading.Lock()
_vistos: dict[str, Agotamiento] = {}


def _aplica(etiqueta: str, modelo: str) -> bool:
    """Una ventana con nombre de familia ("Opus week") solo cuenta para esa familia de modelo."""
    familias = [f for f in _FAMILIAS if f in etiqueta.lower()]
    return not familias or any(f in modelo.lower() for f in familias)


def _ventana_agotada(proveedor: str, modelo: str) -> tuple[bool | None, datetime | None]:
    """(agotada, reinicio) segun la medicion; (None, None) si no hay medidor o la medicion fallo."""
    medir = next((m for prefijo, m in MEDIDORES.items() if proveedor.startswith(prefijo)), None)
    if medir is None:
        return None, None
    try:
        ventanas = medir()
    except Exception as exc:  # red, token vencido, API cambiada: sin medicion no se afirma nada
        log.warning("orquehelx: no se pudo medir la cuota de %s: %s", proveedor, exc)
        return None, None
    agotadas = [r for etiqueta, usado, r in ventanas if usado is not None and usado >= 100 and _aplica(etiqueta, modelo)]
    if not agotadas:
        return False, None
    # La ruta vuelve cuando reabren TODAS sus ventanas agotadas; una sin hora deja el reinicio sin dato.
    return True, None if None in agotadas else max(agotadas)


def _texto_inequivoco(mensaje: str, status: int | None) -> bool:
    m = mensaje.lower()
    if "resource_exhausted" in m and any(p in m for p in ("individual quota", "quota exceeded", "code 429")):
        return True
    return status == 429 and ("usage limit" in m or "quota" in m)


def al_fallar(provider: str = "", error: object = None, status_code: int | None = None, model: str = "", **_) -> None:
    """Hook ``api_request_error``. Hermes manda el texto en ``error={"type", "message"}``
    (agent/api_request_hooks.py); los valores de retorno se ignoran."""
    proveedor, modelo = (provider or "").strip(), model or ""
    mensaje = str(error.get("message") or "") if isinstance(error, dict) else str(error or "")
    if not proveedor:
        return
    if _texto_inequivoco(mensaje, status_code):
        _, reinicio = _ventana_agotada(proveedor, modelo)
    elif proveedor.startswith("claude-subscription") and "native request failed" in mensaje.lower():
        agotada, reinicio = _ventana_agotada(proveedor, modelo)
        if not agotada:
            return
    else:
        return
    # ponytail: un registro por proveedor, sin id de tarea; si el proveedor esta agotado, la falla de
    # cualquier hijo en ese proveedor en ese lapso es cuota. Por tarea si aparecen falsos positivos.
    with _lock:
        _vistos[proveedor] = Agotamiento(proveedor, mensaje[:300], reinicio, time.time())


def consultar(proveedor: str, desde: float) -> Agotamiento | None:
    """El agotamiento de ``proveedor`` visto desde ``desde`` (epoch), o None."""
    with _lock:
        ag = _vistos.get(proveedor)
    return ag if ag is not None and ag.visto >= desde else None
