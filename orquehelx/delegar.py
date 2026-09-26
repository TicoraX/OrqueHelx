"""Herramienta ``delegar``: el modelo elige por nombre de ruta en que suscripcion corre el subagente.

``delegate_task`` nativo de Hermes no deja al modelo elegir el proveedor del hijo: usa uno solo,
de config. Aca el modelo nombra una ruta y el hijo corre en ese proveedor, sin fallback (D2).

    modelo --delegar(ruta, objetivo)--> manejar --> delegate_task(credentials_cfg=ruta)
                                                        '--> hijo en el proveedor de la ruta
    hijo falla y cuota.consultar(proveedor) lo confirma --> {"estado": "sin_cuota", ...}
                                                            + instruccion segun la politica (D18)
"""
from __future__ import annotations

import json
import time

from . import comando, cuota
from .proveedores import registrar_acp
from .rutas import ErrorDeConfig, Ruta, cargar, credenciales

# D18: que hace el padre cuando un hijo se queda sin cuota. Nunca hay cambio automatico sin permiso.
POLITICAS = {
    "preguntar": ("No reintentes ni cambies de ruta por tu cuenta. Informa al usuario y preguntale si quiere "
                  "enviar la tarea a otra ruta (di que modelo usa cada una) o esperar al reinicio."),
    "padre_decide": ("Puedes reenviar la tarea a otra ruta si su modelo es adecuado para la tarea; "
                     "informa al usuario que ruta elegiste y por que."),
    "esperar": "No hagas nada mas con esta tarea. Informa al usuario y espera su proxima indicacion.",
}
# Atributo en el agente padre con el turno donde una ruta quedo sin cuota (preguntar/esperar).
_BLOQUEO = "_orquehelx_turno_sin_cuota"


def esquema(rutas: dict[str, Ruta]) -> dict:
    opciones = _opciones(rutas)
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


def _sin_cuota(rutas: dict[str, Ruta], ruta: Ruta, ag: cuota.Agotamiento, politica: str) -> str:
    reinicio = ag.reinicio.isoformat() if ag.reinicio else None
    cuando = f"se reinicia {reinicio}" if reinicio else "reinicio: sin dato (el proveedor no lo informa)"
    return json.dumps({
        "estado": "sin_cuota",
        "ruta": ruta.nombre,
        "proveedor": ruta.proveedor,
        "reinicio": reinicio,
        "otras_rutas": [n for n in rutas if n != ruta.nombre],
        "instruccion": f"La ruta {ruta.nombre} se quedo sin cuota ({cuando}). {POLITICAS[politica]}",
    }, ensure_ascii=False)


def _fallo(salida: str) -> bool:
    try:
        resultados = json.loads(salida).get("results") or []
    except (ValueError, AttributeError):
        return False
    return any(r.get("status") != "completed" for r in resultados if isinstance(r, dict))


def manejar(rutas: dict[str, Ruta], args: dict, parent_agent, politica: str = "preguntar") -> str:
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
    turno = getattr(parent_agent, "_current_turn_id", None)
    if politica != "padre_decide" and turno is not None and getattr(parent_agent, _BLOQUEO, None) == turno:
        # D2: tras un sin_cuota, cambiar de ruta requiere que el usuario lo autorice; su respuesta abre otro turno.
        return _error("una ruta se quedo sin cuota en este turno y la politica exige que el usuario autorice el "
                      "siguiente paso: preguntale y espera su respuesta antes de delegar de nuevo")
    contexto = args.get("contexto")
    contexto = contexto.strip() or None if isinstance(contexto, str) else None
    from tools.delegate_tool import delegate_task
    inicio = time.time()
    salida = delegate_task(goal=objetivo, context=contexto, parent_agent=parent_agent,
                           credentials_cfg=credenciales(ruta))
    ag = cuota.consultar(ruta.proveedor, desde=inicio) if _fallo(salida) else None
    if ag is None:
        return salida
    if politica != "padre_decide" and turno is not None:
        setattr(parent_agent, _BLOQUEO, turno)
    return _sin_cuota(rutas, ruta, ag, politica)


# Acciones de control de delegate_task: operan sobre subagentes ya lanzados (tambien los de delegar).
_CONTROL = {"list", "steer", "stop"}


def _opciones(rutas: dict[str, Ruta]) -> str:
    return ", ".join(f"{r.nombre} ({r.modelo or r.proveedor})" for r in rutas.values())


def redirigir(rutas: dict[str, Ruta], tool_name: str = "", args: dict | None = None, **_) -> dict | None:
    """Hook ``pre_tool_call``: el delegate_task nativo corre el hijo en el proveedor del padre, sin rutas
    ni politica de cuota. Lanzar subagentes pasa por ``delegar``; las acciones de control siguen."""
    if tool_name != "delegate_task":
        return None
    accion = str((args or {}).get("action") or "spawn").strip().lower()
    if accion in _CONTROL:
        return None
    return {"action": "block", "message": (
        "Con OrqueHelx los subagentes se lanzan con la herramienta delegar(ruta, objetivo, contexto), que "
        f"elige la suscripcion donde corre cada uno. Rutas: {_opciones(rutas)}. Vuelve a pedirlo con delegar.")}


def seccion_prompt(rutas: dict[str, Ruta]) -> str:
    return (
        "Subagentes (OrqueHelx): para delegar trabajo usa la herramienta delegar(ruta, objetivo, contexto), "
        "no delegate_task. Cada ruta corre en otra suscripcion con su propia cuota; elige la que convenga a "
        f"la tarea. Rutas: {_opciones(rutas)}. Si una ruta responde sin_cuota, sigue su instruccion."
    )


def registrar(ctx) -> None:
    # No importar model_tools aca: su import descubre plugins y, dentro del loader de plugins,
    # se traba contra su lock hasta el timeout de carga (10 s) y Hermes descarta el plugin.
    rutas = cargar(ctx.get_config("rutas"))
    politica = ctx.get_config("politica", "preguntar")
    if not isinstance(politica, str) or politica not in POLITICAS:
        raise ErrorDeConfig(f"politica {politica!r} invalida en plugins.entries.orquehelx.settings.politica; "
                            f"usa una de: {', '.join(POLITICAS)}")
    for ruta in rutas.values():
        if ruta.acp:
            registrar_acp(ruta)

    def handler(args, parent_agent=None, **_):
        return manejar(rutas, args, parent_agent, politica)

    definicion = esquema(rutas)
    ctx.register_tool("delegar", "orquehelx", definicion, handler, description=definicion["description"])
    ctx.register_hook("api_request_error", cuota.al_fallar)
    ctx.register_hook("pre_tool_call", lambda **kw: redirigir(rutas, **kw))
    ctx.register_system_prompt_section("orquehelx.rutas", seccion_prompt(rutas))
    ctx.register_command("ohx", lambda argumentos: comando.ejecutar(rutas, argumentos),
                         description="Rutas de OrqueHelx y consumo medido de cada suscripcion",
                         args_hint="rutas|cuota")
    # ponytail: la tabla inline recibe el agente en cualquier version de Hermes y el executor la consulta
    # antes que al registry. El handler del registry queda para quien despache sin tabla inline (y
    # recibe parent_agent desde Hermes con el PR #122961). Quitar esto cuando la version minima lo traiga.
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
    INLINE_TOOL_EXECUTORS["delegar"] = lambda agent, args, _ctx: manejar(rutas, args, agent, politica)
