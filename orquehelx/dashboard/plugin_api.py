"""Backend del dashboard de OrqueHelx, montado por Hermes en /api/plugins/orquehelx/.

Hermes importa este archivo por ruta y suelto, asi que no hay imports relativos: usa los modulos del
plugin ya cargados en el proceso (el agente del dashboard corre aca, y asi el registro de agotamientos
es el mismo) y, si no estan, los carga por ruta.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_RAIZ = Path(__file__).resolve().parents[1]
router = APIRouter()


def _modulo(nombre: str):
    cargado = sys.modules.get(f"hermes_plugins.orquehelx.{nombre}")
    if cargado is not None:
        return cargado
    clave = f"_orquehelx_dashboard_{nombre}"
    if clave not in sys.modules:
        spec = importlib.util.spec_from_file_location(clave, _RAIZ / f"{nombre}.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[clave] = modulo
        spec.loader.exec_module(modulo)
    return sys.modules[clave]


def _config_rutas():
    from hermes_cli.config import load_config_readonly
    entrada = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
    return ((entrada.get("orquehelx") or {}).get("settings") or {}).get("rutas")


def _medicion(ventanas, error) -> dict:
    if error is not None:
        return {"estado": "error", "error": str(error), "ventanas": []}
    if ventanas is None:
        return {"estado": "sin_dato", "error": None, "ventanas": []}
    return {"estado": "medida", "error": None, "ventanas": [
        {"etiqueta": etiqueta, "usado": usado, "reinicio": reinicio.isoformat() if reinicio else None}
        for etiqueta, usado, reinicio in ventanas]}


def _vigente(cuota, proveedor: str):
    """El agotamiento del proveedor si todavia no llego su reinicio (sin reinicio conocido, sigue vigente)."""
    ag = cuota.consultar(proveedor, desde=time.time() - 24 * 3600)
    return ag if ag is not None and (ag.reinicio is None or ag.reinicio.timestamp() > time.time()) else None


@router.get("/estado")
def estado() -> dict:
    """Rutas configuradas, cuota medida de cada una (una medicion por proveedor) y agotamientos vigentes."""
    rutas_mod, cuota = _modulo("rutas"), _modulo("cuota")
    try:
        rutas = rutas_mod.cargar(_config_rutas())
    except rutas_mod.ErrorDeConfig as exc:
        return {"rutas": [], "error": str(exc)}
    mediciones = {p: cuota.medir_seguro(p) for p in dict.fromkeys(r.proveedor for r in rutas.values())}
    salida = []
    for r in rutas.values():
        ag = _vigente(cuota, r.proveedor)
        salida.append({
            "nombre": r.nombre, "proveedor": r.proveedor, "modelo": r.modelo, "acp": r.acp is not None,
            "medicion": _medicion(*mediciones[r.proveedor]),
            "agotada": {"desde": ag.visto, "reinicio": ag.reinicio.isoformat() if ag.reinicio else None}
            if ag else None,
        })
    return {"rutas": salida, "error": None}


def _pausas():
    from hermes_constants import get_hermes_home
    pausas = _modulo("pausas")
    return pausas, pausas.abrir(get_hermes_home() / "orquehelx" / "pausas.db")


class NuevaPausa(BaseModel):
    sesion: str = Field(min_length=1, max_length=200)
    proveedor: str = Field(min_length=1, max_length=200)
    modelo: str | None = Field(default=None, max_length=200)


class Resolucion(BaseModel):
    destino: str | None = Field(default=None, max_length=64)


@router.post("/pausas")
def pausar(cuerpo: NuevaPausa) -> dict:
    """Pausa la sesion solo si el proveedor tiene un agotamiento registrado y vigente; si no, no es cuota."""
    ag = _vigente(_modulo("cuota"), cuerpo.proveedor)
    if ag is None:
        return {"pausa": None}
    pausas, con = _pausas()
    try:
        return {"pausa": pausas.crear(con, cuerpo.sesion, cuerpo.proveedor, cuerpo.modelo, ag.reinicio)}
    finally:
        con.close()


@router.get("/pausas")
def pendientes() -> dict:
    pausas, con = _pausas()
    try:
        return {"pausas": pausas.pendientes(con)}
    finally:
        con.close()


@router.post("/pausas/{pausa}/{accion}")
def resolver(pausa: int, accion: Literal["reanudar", "reenviar", "cancelar"], cuerpo: Resolucion) -> dict:
    pausas, con = _pausas()
    try:
        gano = pausas.resolver(con, pausa, accion, cuerpo.destino)
    finally:
        con.close()
    if not gano:
        raise HTTPException(status_code=409, detail="ya se resolvió")
    return {"ok": True}
