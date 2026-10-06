"""Comando ``/ohx``: rutas configuradas y consumo medido de cada suscripcion.

La medicion se hace al pedirla (refresco manual, sin temporizadores). Un proveedor sin medidor
muestra "sin dato": nunca se estima un porcentaje.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from . import cuota
from .rutas import CLAVE_CONFIG, Ruta
from .textos import t

# Subcomandos en ingles (v0.2) y sus nombres de v0.1.
_SUB = {"routes": "routes", "rutas": "routes", "quota": "quota", "cuota": "quota"}


def _hora(fecha) -> str:
    return fecha.astimezone().strftime("%Y-%m-%d %H:%M")


def _rutas(rutas: dict[str, Ruta]) -> str:
    return "\n".join(f"{r.nombre}: {r.proveedor}" + (f" ({r.modelo})" if r.modelo else "") for r in rutas.values())


def _cuota_de(ruta: Ruta, ventanas: list | None, error: Exception | None) -> list[str]:
    lineas = []
    ag = cuota.consultar(ruta.proveedor, desde=time.time() - 24 * 3600)
    vigente = ag is not None and (ag.reinicio is None or ag.reinicio > datetime.now(timezone.utc))
    if vigente:
        cuando = _hora(ag.reinicio) if ag.reinicio else t("no_data")
        lineas.append(t("out_since", route=ruta.nombre, since=time.strftime("%H:%M", time.localtime(ag.visto)),
                        reset=cuando))
    if error is not None:
        return [*lineas, t("measure_failed", route=ruta.nombre, error=error)]
    if ventanas is None:
        return [*lineas, t("provider_silent", route=ruta.nombre)]
    if not ventanas:
        return [*lineas, t("no_windows", route=ruta.nombre)]
    lineas.append(f"{ruta.nombre}:")
    for etiqueta, usado, reinicio in ventanas:
        # Truncar, no redondear: 99.96 no puede mostrarse como 100 (agotado).
        valor = t("no_data") if usado is None else f"{math.floor(usado * 10) / 10:g} %"
        lineas.append(f"  {etiqueta}: {valor}" + (t("resets", when=_hora(reinicio)) if reinicio else ""))
    return lineas


def ejecutar(rutas: dict[str, Ruta], argumentos: str) -> str:
    sub = _SUB.get((argumentos or "").strip().lower())
    if sub and not rutas:
        return t("no_routes", key=CLAVE_CONFIG)
    if sub == "routes":
        return _rutas(rutas)
    if sub == "quota":
        # Una medicion por proveedor: varias rutas pueden compartir la misma suscripcion.
        mediciones = {p: cuota.medir_seguro(p) for p in dict.fromkeys(r.proveedor for r in rutas.values())}
        return "\n".join(linea for ruta in rutas.values() for linea in _cuota_de(ruta, *mediciones[ruta.proveedor]))
    return t("ohx_usage")
