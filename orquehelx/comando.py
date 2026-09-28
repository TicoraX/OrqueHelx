"""Comando ``/ohx``: rutas configuradas y consumo medido de cada suscripcion.

La medicion se hace al pedirla (refresco manual, sin temporizadores). Un proveedor sin medidor
muestra "sin dato": nunca se estima un porcentaje.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from . import cuota
from .rutas import SIN_RUTAS, Ruta

USO = "uso: /ohx rutas | /ohx cuota"


def _hora(fecha) -> str:
    return fecha.astimezone().strftime("%Y-%m-%d %H:%M")


def _rutas(rutas: dict[str, Ruta]) -> str:
    return "\n".join(f"{r.nombre}: {r.proveedor}" + (f" ({r.modelo})" if r.modelo else "") for r in rutas.values())


def _cuota_de(ruta: Ruta, ventanas: list | None, error: Exception | None) -> list[str]:
    lineas = []
    ag = cuota.consultar(ruta.proveedor, desde=time.time() - 24 * 3600)
    vigente = ag is not None and (ag.reinicio is None or ag.reinicio > datetime.now(timezone.utc))
    if vigente:
        cuando = _hora(ag.reinicio) if ag.reinicio else "sin dato"
        lineas.append(f"{ruta.nombre}: sin cuota desde {time.strftime('%H:%M', time.localtime(ag.visto))}"
                      f" (reinicio: {cuando})")
    if error is not None:
        return [*lineas, f"{ruta.nombre}: no se pudo medir ({error})"]
    if ventanas is None:
        return [*lineas, f"{ruta.nombre}: sin dato (el proveedor no informa consumo)"]
    if not ventanas:
        return [*lineas, f"{ruta.nombre}: sin dato (la medición no devolvió ventanas)"]
    lineas.append(f"{ruta.nombre}:")
    for etiqueta, usado, reinicio in ventanas:
        # Truncar, no redondear: 99.96 no puede mostrarse como 100 (agotado).
        valor = "sin dato" if usado is None else f"{math.floor(usado * 10) / 10:g} %"
        lineas.append(f"  {etiqueta}: {valor}" + (f", reinicia {_hora(reinicio)}" if reinicio else ""))
    return lineas


def ejecutar(rutas: dict[str, Ruta], argumentos: str) -> str:
    sub = (argumentos or "").strip().lower()
    if sub in ("rutas", "cuota") and not rutas:
        return SIN_RUTAS
    if sub == "rutas":
        return _rutas(rutas)
    if sub == "cuota":
        # Una medicion por proveedor: varias rutas pueden compartir la misma suscripcion.
        mediciones = {p: cuota.medir_seguro(p) for p in dict.fromkeys(r.proveedor for r in rutas.values())}
        return "\n".join(linea for ruta in rutas.values() for linea in _cuota_de(ruta, *mediciones[ruta.proveedor]))
    return USO
