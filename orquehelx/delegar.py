"""Herramienta ``delegate_to`` (``delegar`` hasta v0.1): el modelo elige por nombre de ruta en que suscripcion corre el subagente.

``delegate_task`` nativo de Hermes no deja al modelo elegir el proveedor del hijo: usa uno solo,
de config. Aca el modelo nombra una ruta y el hijo corre en ese proveedor, sin fallback (D2).

    modelo --delegate_to(route, goal)--> manejar --> delegate_task(credentials_cfg=ruta)
                                                        '--> hijo en el proveedor de la ruta
    hijo falla y cuota.consultar(proveedor) lo confirma --> {"status": "out_of_quota", ...}
                                                            + instruccion segun la politica (D18)

Todo lo que lee el modelo va en ingles y no se traduce (D24); lo que lee el usuario sale de ``textos``.
"""
from __future__ import annotations

import json
import time

from . import comando, cuota
from .proveedores import registrar_acp
from .rutas import CLAVE_CONFIG, ErrorDeConfig, Ruta, ajuste, cargar, credenciales
from .textos import t

# D18: que hace el padre cuando un hijo se queda sin cuota. Nunca hay cambio automatico sin permiso.
POLITICAS = {
    "ask": ("Do not retry or switch routes on your own. Tell the user and ask whether to send the task to "
            "another route (say which model each one uses) or wait for the reset."),
    "parent_decides": ("You may resend the task to another route if its model suits the task; tell the user "
                       "which route you chose and why."),
    "wait": "Do nothing else with this task. Tell the user and wait for their next instruction.",
}
# Valores de v0.1, todavia aceptados en la config.
_POLITICAS_V01 = {"preguntar": "ask", "padre_decide": "parent_decides", "esperar": "wait"}
HERRAMIENTA = "delegate_to"
# Atributo en el agente padre con el turno donde una ruta quedo sin cuota (preguntar/esperar).
_BLOQUEO = "_orquehelx_turno_sin_cuota"


def esquema(rutas: dict[str, Ruta]) -> dict:
    opciones = _opciones(rutas)
    return {
        "name": HERRAMIENTA,
        "description": (
            "Delegate a task to a subagent that runs on another subscription and return its result. "
            f"Available routes: {opciones}. If the route is out of quota you are told; it never switches on its own."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                # Sin rutas no hay enum: un enum vacío es un esquema inválido para varios proveedores.
                "route": {"type": "string", "description": "Subscription the subagent runs on.",
                          **({"enum": list(rutas)} if rutas else {})},
                "goal": {"type": "string", "description": "Complete, self-contained task for the subagent."},
                "context": {"type": "string", "description": "Data the subagent needs and cannot see."},
            },
            "required": ["route", "goal"],
        },
    }


def _error(mensaje: str) -> str:
    return json.dumps({"error": mensaje}, ensure_ascii=False)


def _sin_cuota(rutas: dict[str, Ruta], ruta: Ruta, ag: cuota.Agotamiento, politica: str) -> str:
    reinicio = ag.reinicio.isoformat() if ag.reinicio else None
    cuando = f"resets at {reinicio}" if reinicio else "reset time unknown: the provider does not report it"
    return json.dumps({
        "status": "out_of_quota",
        "route": ruta.nombre,
        "provider": ruta.proveedor,
        "reset_at": reinicio,
        "other_routes": [n for n in rutas if n != ruta.nombre],
        "instruction": f"Route {ruta.nombre} is out of quota ({cuando}). {POLITICAS[politica]}",
    }, ensure_ascii=False)


def _fallo(salida: str) -> bool:
    try:
        resultados = json.loads(salida).get("results") or []
    except (ValueError, AttributeError):
        return False
    return any(r.get("status") != "completed" for r in resultados if isinstance(r, dict))


def manejar(rutas: dict[str, Ruta], args: dict, parent_agent, politica: str = "ask") -> str:
    # Los argumentos vienen del modelo: cualquier tipo es posible y nada debe romper el handler.
    if not rutas:
        return _error(t("no_routes", lang="en", key=CLAVE_CONFIG))
    nombre, objetivo = args.get("route"), args.get("goal")
    ruta = rutas.get(nombre) if isinstance(nombre, str) else None
    if ruta is None:
        return _error(f"unknown route {nombre!r}; valid routes: {', '.join(rutas)}")
    objetivo = objetivo.strip() if isinstance(objetivo, str) else ""
    if not objetivo:
        return _error("missing 'goal': describe the complete task for the subagent")
    if parent_agent is None:
        return _error(f"{HERRAMIENTA} needs the parent agent and Hermes did not pass it; update Hermes or report it")
    turno = getattr(parent_agent, "_current_turn_id", None)
    if politica != "parent_decides" and turno is not None and getattr(parent_agent, _BLOQUEO, None) == turno:
        # D2: tras un sin_cuota, cambiar de ruta requiere que el usuario lo autorice; su respuesta abre otro turno.
        return _error("a route ran out of quota in this turn and the policy requires the user to approve the "
                      "next step: ask them and wait for their answer before delegating again")
    contexto = args.get("context")
    contexto = contexto.strip() or None if isinstance(contexto, str) else None
    from tools.delegate_tool import delegate_task
    inicio = time.time()
    salida = delegate_task(goal=objetivo, context=contexto, parent_agent=parent_agent,
                           credentials_cfg=credenciales(ruta))
    ag = cuota.consultar(ruta.proveedor, desde=inicio) if _fallo(salida) else None
    if ag is None:
        return salida
    if politica != "parent_decides" and turno is not None:
        setattr(parent_agent, _BLOQUEO, turno)
    return _sin_cuota(rutas, ruta, ag, politica)


# Acciones de control de delegate_task: operan sobre subagentes ya lanzados (tambien los de delegar).
_CONTROL = {"list", "steer", "stop"}


def _opciones(rutas: dict[str, Ruta]) -> str:
    return ", ".join(f"{r.nombre} ({r.modelo or r.proveedor})" for r in rutas.values())


def redirigir(rutas: dict[str, Ruta], tool_name: str = "", args: dict | None = None, **_) -> dict | None:
    """Hook ``pre_tool_call``: el delegate_task nativo corre el hijo en el proveedor del padre, sin rutas
    ni politica de cuota. Lanzar subagentes pasa por ``delegar``; las acciones de control siguen."""
    if tool_name != "delegate_task" or not rutas:  # sin rutas no hay a dónde redirigir: sigue el nativo
        return None
    accion = str((args or {}).get("action") or "spawn").strip().lower()
    if accion in _CONTROL:
        return None
    return {"action": "block", "message": (
        f"With OrqueHelx, subagents are launched with the {HERRAMIENTA}(route, goal, context) tool, which picks "
        f"the subscription each one runs on. Routes: {_opciones(rutas)}. Ask again with {HERRAMIENTA}.")}


def seccion_prompt(rutas: dict[str, Ruta]) -> str:
    return (
        f"Subagents (OrqueHelx): to delegate work use the {HERRAMIENTA}(route, goal, context) tool, not "
        "delegate_task. Each route runs on another subscription with its own quota; pick the one that suits "
        f"the task. Routes: {_opciones(rutas)}. If a route answers out_of_quota, follow its instruction."
    )


def registrar(ctx) -> None:
    # No importar model_tools aca: su import descubre plugins y, dentro del loader de plugins,
    # se traba contra su lock hasta el timeout de carga (10 s) y Hermes descarta el plugin.
    # Recién instalado no hay rutas: carga igual y cada entrada dice qué configurar. Una config con
    # contenido mal escrito sigue fallando al cargar.
    config_rutas = ajuste(ctx.get_config, "routes", "rutas")
    rutas = {} if config_rutas is None or config_rutas == {} else cargar(config_rutas)
    politica = ajuste(ctx.get_config, "policy", "politica")
    if politica is None:
        politica = "ask"
    elif isinstance(politica, str):
        politica = _POLITICAS_V01.get(politica, politica)
    if not isinstance(politica, str) or politica not in POLITICAS:
        raise ErrorDeConfig(t("bad_policy", value=politica, options=", ".join(POLITICAS)))
    for ruta in rutas.values():
        if ruta.acp:
            registrar_acp(ruta)

    def handler(args, parent_agent=None, **_):
        return manejar(rutas, args, parent_agent, politica)

    definicion = esquema(rutas)
    ctx.register_tool(HERRAMIENTA, "orquehelx", definicion, handler, description=definicion["description"])
    ctx.register_hook("api_request_error", cuota.al_fallar)
    ctx.register_hook("pre_tool_call", lambda **kw: redirigir(rutas, **kw))
    if rutas:
        ctx.register_system_prompt_section("orquehelx.rutas", seccion_prompt(rutas))
    ctx.register_command("ohx", lambda argumentos: comando.ejecutar(rutas, argumentos),
                         description=t("ohx_description"), args_hint="routes|quota")
    # ponytail: la tabla inline recibe el agente en cualquier version de Hermes y el executor la consulta
    # antes que al registry. El handler del registry queda para quien despache sin tabla inline (y
    # recibe parent_agent desde Hermes con el PR #122961). Quitar esto cuando la version minima lo traiga.
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
    INLINE_TOOL_EXECUTORS[HERRAMIENTA] = lambda agent, args, _ctx: manejar(rutas, args, agent, politica)
