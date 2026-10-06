"""Rutas: el nombre corto con que el modelo elige en que suscripcion corre un subagente.

Se leen de ``plugins.entries.orquehelx.settings.routes`` en el config.yaml de Hermes (``rutas`` en v0.1)::

    routes:
      claude:   {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}
      agy:      {provider: antigravity-subscription-directsdk, model: flash}
      opencode: {acp: [opencode, acp]}        # cualquier CLI que hable ACP

Una ruta ``acp`` no necesita plugin propio: el plugin registra un proveedor ``ohx-<ruta>`` que
lanza ese comando (ver ``proveedores.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .textos import t

CLAVE_CONFIG = "plugins.entries.orquehelx.settings.routes"
_NOMBRE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_CAMPOS = {"provider", "model", "acp"}


class ErrorDeConfig(ValueError):
    """Config del plugin inutilizable; el mensaje nombra la clave y que corregir."""


class ErrorDeRuta(ErrorDeConfig):
    """Config de rutas inutilizable; el mensaje nombra la ruta y que corregir."""


@dataclass(frozen=True)
class Ruta:
    nombre: str
    proveedor: str
    modelo: str | None = None
    acp: tuple[str, ...] | None = None


def _ruta(nombre: str, valor: object) -> Ruta:
    if not _NOMBRE.match(nombre):
        raise ErrorDeRuta(t("route_name", route=nombre))
    if not isinstance(valor, dict):
        raise ErrorDeRuta(t("route_map", route=nombre))
    sobrantes = set(valor) - _CAMPOS
    if sobrantes:
        raise ErrorDeRuta(t("route_fields", route=nombre, fields=sorted(sobrantes)))
    modelo = valor.get("model")
    if modelo is not None and (not isinstance(modelo, str) or not modelo.strip()):
        raise ErrorDeRuta(t("route_model", route=nombre))
    proveedor, acp = valor.get("provider"), valor.get("acp")
    if (proveedor is None) == (acp is None):
        raise ErrorDeRuta(t("route_one_of", route=nombre))
    if acp is not None:
        if not isinstance(acp, list) or not acp or not all(isinstance(p, str) and p.strip() for p in acp):
            raise ErrorDeRuta(t("route_acp", route=nombre))
        return Ruta(nombre, f"ohx-{nombre}", modelo, tuple(acp))
    if not isinstance(proveedor, str) or not proveedor.strip():
        raise ErrorDeRuta(t("route_provider", route=nombre))
    return Ruta(nombre, proveedor.strip(), modelo)


def cargar(config: object) -> dict[str, Ruta]:
    """Valida la config completa; falla en voz alta en la primera ruta inutilizable."""
    if not isinstance(config, dict) or not config:
        raise ErrorDeRuta(t("no_routes", key=CLAVE_CONFIG))
    return {str(nombre): _ruta(str(nombre), valor) for nombre, valor in config.items()}


def ajuste(obtener, clave: str, alias: str):
    """Valor de ``clave`` (ingles, v0.2) o de su ``alias`` en espanol (v0.1). Las dos a la vez es un error:
    no hay forma de saber cual quiso el usuario."""
    nuevo, viejo = obtener(clave), obtener(alias)
    if nuevo is not None and viejo is not None:
        raise ErrorDeConfig(t("both_keys", new=clave, old=alias))
    return viejo if nuevo is None else nuevo


def credenciales(ruta: Ruta) -> dict:
    """``credentials_cfg`` para ``delegate_task``: la ruta fija el proveedor y no hay fallback (D2)."""
    cfg = {"provider": ruta.proveedor, "fallback_providers": []}
    if ruta.modelo:
        cfg["model"] = ruta.modelo
    return cfg
