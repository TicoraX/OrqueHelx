"""Rutas: el nombre corto con que el modelo elige en que suscripcion corre un subagente.

Se leen de ``plugins.entries.orquehelx.settings.rutas`` en el config.yaml de Hermes::

    rutas:
      claude:   {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}
      agy:      {provider: antigravity-subscription-directsdk, model: flash}
      opencode: {acp: [opencode, acp]}        # cualquier CLI que hable ACP

Una ruta ``acp`` no necesita plugin propio: el plugin registra un proveedor ``ohx-<ruta>`` que
lanza ese comando (ver ``proveedores.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CLAVE_CONFIG = "plugins.entries.orquehelx.settings.rutas"
SIN_RUTAS = f"no hay rutas configuradas: define {CLAVE_CONFIG} en config.yaml"
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
        raise ErrorDeRuta(f"ruta {nombre!r}: el nombre debe ser minúsculas, dígitos o '-' (máx. 32)")
    if not isinstance(valor, dict):
        raise ErrorDeRuta(f"ruta {nombre!r}: se esperaba un mapa con 'provider' o 'acp'")
    sobrantes = set(valor) - _CAMPOS
    if sobrantes:
        raise ErrorDeRuta(f"ruta {nombre!r}: campos desconocidos {sorted(sobrantes)}")
    modelo = valor.get("model")
    if modelo is not None and (not isinstance(modelo, str) or not modelo.strip()):
        raise ErrorDeRuta(f"ruta {nombre!r}: 'model' debe ser texto no vacío")
    proveedor, acp = valor.get("provider"), valor.get("acp")
    if (proveedor is None) == (acp is None):
        raise ErrorDeRuta(f"ruta {nombre!r}: define exactamente uno de 'provider' o 'acp'")
    if acp is not None:
        if not isinstance(acp, list) or not acp or not all(isinstance(p, str) and p.strip() for p in acp):
            raise ErrorDeRuta(f"ruta {nombre!r}: 'acp' debe ser una lista no vacía de textos, p. ej. [opencode, acp]")
        return Ruta(nombre, f"ohx-{nombre}", modelo, tuple(acp))
    if not isinstance(proveedor, str) or not proveedor.strip():
        raise ErrorDeRuta(f"ruta {nombre!r}: 'provider' debe ser texto no vacío")
    return Ruta(nombre, proveedor.strip(), modelo)


def cargar(config: object) -> dict[str, Ruta]:
    """Valida la config completa; falla en voz alta en la primera ruta inutilizable."""
    if not isinstance(config, dict) or not config:
        raise ErrorDeRuta(SIN_RUTAS)
    return {str(nombre): _ruta(str(nombre), valor) for nombre, valor in config.items()}


def credenciales(ruta: Ruta) -> dict:
    """``credentials_cfg`` para ``delegate_task``: la ruta fija el proveedor y no hay fallback (D2)."""
    cfg = {"provider": ruta.proveedor, "fallback_providers": []}
    if ruta.modelo:
        cfg["model"] = ruta.modelo
    return cfg
