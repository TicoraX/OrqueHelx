"""Herramienta ``delegar``: el modelo elige por nombre de ruta en que suscripcion corre el subagente.

``delegate_task`` nativo de Hermes no deja al modelo elegir el proveedor del hijo: usa uno solo,
de config. Aca el modelo nombra una ruta y el hijo corre en ese proveedor, sin fallback (D2).

    modelo --delegar(ruta, objetivo)--> manejar --> delegate_task(credentials_cfg=ruta)
                                                        '--> hijo en el proveedor de la ruta
"""
from __future__ import annotations

import inspect
import json

from .proveedores import registrar_acp
from .rutas import Ruta, cargar, credenciales


def esquema(rutas: dict[str, Ruta]) -> dict:
    opciones = "; ".join(f"{r.nombre} ({r.modelo or r.proveedor})" for r in rutas.values())
    return {
        "name": "delegar",
        "description": (
            "Delega una tarea a un subagente que corre en otra suscripcion y devuelve su resultado. "
            f"Rutas disponibles: {opciones}. Si la ruta no tiene cuota, se informa; no se cambia sola."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ruta": {"type": "string", "enum": list(rutas), "description": "Suscripcion donde corre el subagente."},
                "objetivo": {"type": "string", "description": "Tarea completa y autocontenida para el subagente."},
                "contexto": {"type": "string", "description": "Datos que el subagente necesita y no puede ver."},
            },
            "required": ["ruta", "objetivo"],
        },
    }


def _error(mensaje: str) -> str:
    return json.dumps({"error": mensaje}, ensure_ascii=False)


def manejar(rutas: dict[str, Ruta], args: dict, parent_agent) -> str:
    # Los argumentos vienen del modelo: cualquier tipo es posible y nada debe romper el handler.
    nombre, objetivo = args.get("ruta"), args.get("objetivo")
    ruta = rutas.get(nombre) if isinstance(nombre, str) else None
    if ruta is None:
        return _error(f"ruta desconocida {nombre!r}; rutas validas: {', '.join(rutas)}")
    objetivo = objetivo.strip() if isinstance(objetivo, str) else ""
    if not objetivo:
        return _error("falta 'objetivo': describe la tarea completa para el subagente")
    if parent_agent is None:
        return _error("delegar necesita el agente padre y Hermes no lo paso; actualiza Hermes o reporta el caso")
    contexto = args.get("contexto")
    contexto = contexto.strip() or None if isinstance(contexto, str) else None
    from tools.delegate_tool import delegate_task
    return delegate_task(goal=objetivo, context=contexto, parent_agent=parent_agent,
                         credentials_cfg=credenciales(ruta))


def _hermes_pasa_parent_agent() -> bool:
    """True desde que Hermes reenvia ``parent_agent`` a los handlers del registry (PR #122961)."""
    import model_tools
    return "parent_agent" in inspect.signature(model_tools.handle_function_call).parameters


def registrar(ctx) -> None:
    rutas = cargar(ctx.get_config("rutas"))
    for ruta in rutas.values():
        if ruta.acp:
            registrar_acp(ruta)

    def handler(args, parent_agent=None, **_):
        return manejar(rutas, args, parent_agent)

    definicion = esquema(rutas)
    ctx.register_tool("delegar", "orquehelx", definicion, handler, description=definicion["description"])
    if not _hermes_pasa_parent_agent():
        # ponytail: parche para Hermes sin PR #122961; la tabla inline es la unica via que recibe el
        # agente. Se apaga solo cuando handle_function_call acepta parent_agent.
        from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
        INLINE_TOOL_EXECUTORS["delegar"] = lambda agent, args, _ctx: manejar(rutas, args, agent)
