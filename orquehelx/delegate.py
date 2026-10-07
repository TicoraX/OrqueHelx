"""``delegate_to`` tool (``delegar`` up to v0.1): the model picks, by route name, which subscription the
subagent runs on.

Hermes' native ``delegate_task`` does not let the model choose the child's provider: it uses a single one from
config. Here the model names a route and the child runs on that provider, with no fallback.

    model --delegate_to(route, goal)--> handle --> delegate_task(credentials_cfg=route)
                                                       '--> child on the route's provider
    child fails and quota.lookup(provider) confirms it --> {"status": "out_of_quota", ...}
                                                            + instruction from the policy

Everything the model reads is English and is not translated; what the user reads comes from ``texts``.
"""
from __future__ import annotations

import json
import time

from . import command, quota
from .acp import register_acp
from .routes import CONFIG_KEY, ConfigError, Route, credentials, load, setting
from .texts import t

# What the parent does when a child runs out of quota. There is never an automatic switch without consent.
POLICIES = {
    "ask": ("Do not retry or switch routes on your own. Tell the user and ask whether to send the task to "
            "another route (say which model each one uses) or wait for the reset."),
    "parent_decides": ("You may resend the task to another route if its model suits the task; tell the user "
                       "which route you chose and why."),
    "wait": "Do nothing else with this task. Tell the user and wait for their next instruction.",
}
# v0.1 values, still accepted in the config.
_LEGACY_POLICIES = {"preguntar": "ask", "padre_decide": "parent_decides", "esperar": "wait"}
TOOL = "delegate_to"
# Attribute on the parent agent holding the turn in which a route ran out of quota (ask/wait).
_BLOCKED = "_orquehelx_out_of_quota_turn"


def schema(routes: dict[str, Route]) -> dict:
    return {
        "name": TOOL,
        "description": (
            "Delegate a task to a subagent that runs on another subscription and return its result. "
            f"Available routes: {_options(routes)}. If the route is out of quota you are told; it never "
            "switches on its own."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                # No routes, no enum: an empty enum is an invalid schema for several providers.
                "route": {"type": "string", "description": "Subscription the subagent runs on.",
                          **({"enum": list(routes)} if routes else {})},
                "goal": {"type": "string", "description": "Complete, self-contained task for the subagent."},
                "context": {"type": "string", "description": "Data the subagent needs and cannot see."},
            },
            "required": ["route", "goal"],
        },
    }


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _out_of_quota(routes: dict[str, Route], route: Route, ex: quota.Exhaustion, policy: str) -> str:
    reset_at = ex.reset_at.isoformat() if ex.reset_at else None
    when = f"resets at {reset_at}" if reset_at else "reset time unknown: the provider does not report it"
    return json.dumps({
        "status": "out_of_quota",
        "route": route.name,
        "provider": route.provider,
        "reset_at": reset_at,
        "other_routes": [n for n in routes if n != route.name],
        "instruction": f"Route {route.name} is out of quota ({when}). {POLICIES[policy]}",
    }, ensure_ascii=False)


def _failed(output: str) -> bool:
    try:
        results = json.loads(output).get("results") or []
    except (ValueError, AttributeError):
        return False
    return any(r.get("status") != "completed" for r in results if isinstance(r, dict))


def handle(routes: dict[str, Route], args: dict, parent_agent, policy: str = "ask") -> str:
    # Arguments come from the model: any type is possible and nothing may break the handler.
    if not routes:
        return _error(t("no_routes", lang="en", key=CONFIG_KEY))
    name, goal = args.get("route"), args.get("goal")
    route = routes.get(name) if isinstance(name, str) else None
    if route is None:
        return _error(f"unknown route {name!r}; valid routes: {', '.join(routes)}")
    goal = goal.strip() if isinstance(goal, str) else ""
    if not goal:
        return _error("missing 'goal': describe the complete task for the subagent")
    if parent_agent is None:
        return _error(f"{TOOL} needs the parent agent and Hermes did not pass it; update Hermes or report it")
    turn = getattr(parent_agent, "_current_turn_id", None)
    if policy != "parent_decides" and turn is not None and getattr(parent_agent, _BLOCKED, None) == turn:
        # After out_of_quota, switching routes needs the user's approval; their answer opens a new turn.
        return _error("a route ran out of quota in this turn and the policy requires the user to approve the "
                      "next step: ask them and wait for their answer before delegating again")
    context = args.get("context")
    context = context.strip() or None if isinstance(context, str) else None
    from tools.delegate_tool import delegate_task
    start = time.time()
    output = delegate_task(goal=goal, context=context, parent_agent=parent_agent,
                           credentials_cfg=credentials(route))
    ex = quota.lookup(route.provider, since=start) if _failed(output) else None
    if ex is None:
        return output
    if policy != "parent_decides" and turn is not None:
        setattr(parent_agent, _BLOCKED, turn)
    return _out_of_quota(routes, route, ex, policy)


# delegate_task control actions: they act on subagents already launched (including those from delegate_to).
_CONTROL = {"list", "steer", "stop"}


def _options(routes: dict[str, Route]) -> str:
    return ", ".join(f"{r.name} ({r.model or r.provider})" for r in routes.values())


def redirect(routes: dict[str, Route], tool_name: str = "", args: dict | None = None, **_) -> dict | None:
    """Hook ``pre_tool_call``: native delegate_task runs the child on the parent's provider, with no routes
    and no quota policy. Launching subagents goes through ``delegate_to``; control actions pass."""
    if tool_name != "delegate_task" or not routes:  # no routes, nowhere to redirect: native goes on
        return None
    action = str((args or {}).get("action") or "spawn").strip().lower()
    if action in _CONTROL:
        return None
    return {"action": "block", "message": (
        f"With OrqueHelx, subagents are launched with the {TOOL}(route, goal, context) tool, which picks "
        f"the subscription each one runs on. Routes: {_options(routes)}. Ask again with {TOOL}.")}


def prompt_section(routes: dict[str, Route]) -> str:
    return (
        f"Subagents (OrqueHelx): to delegate work use the {TOOL}(route, goal, context) tool, not "
        "delegate_task. Each route runs on another subscription with its own quota; pick the one that suits "
        f"the task. Routes: {_options(routes)}. If a route answers out_of_quota, follow its instruction."
    )


def register(ctx) -> None:
    # Do not import model_tools here: importing it discovers plugins and, inside the plugin loader, it blocks
    # on its lock until the load timeout (10 s) and Hermes drops the plugin.
    # Freshly installed there are no routes: it loads anyway and each entry point says what to configure.
    # A config with badly written content still fails at load.
    routes_config = setting(ctx.get_config, "routes", "rutas")
    routes = {} if routes_config is None or routes_config == {} else load(routes_config)
    policy = setting(ctx.get_config, "policy", "politica")
    if policy is None:
        policy = "ask"
    elif isinstance(policy, str):
        policy = _LEGACY_POLICIES.get(policy, policy)
    if not isinstance(policy, str) or policy not in POLICIES:
        key = "politica" if ctx.get_config("policy") is None else "policy"
        raise ConfigError(t("bad_policy", value=policy, key=key, options=", ".join(POLICIES)))
    for route in routes.values():
        if route.acp:
            register_acp(route)

    def handler(args, parent_agent=None, **_):
        return handle(routes, args, parent_agent, policy)

    definition = schema(routes)
    ctx.register_tool(TOOL, "orquehelx", definition, handler, description=definition["description"])
    ctx.register_hook("api_request_error", quota.on_error)
    ctx.register_hook("pre_tool_call", lambda **kw: redirect(routes, **kw))
    if routes:
        ctx.register_system_prompt_section("orquehelx.routes", prompt_section(routes))
    ctx.register_command("ohx", lambda arguments: command.run(routes, arguments),
                         description=t("ohx_description"), args_hint="routes|quota")
    # ponytail: the inline table hands the agent over on any Hermes version and the executor checks it before
    # the registry. The registry handler stays for whoever dispatches without the inline table (and gets
    # parent_agent from Hermes once PR #122961 lands). Remove this when the minimum version ships it.
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS
    INLINE_TOOL_EXECUTORS[TOOL] = lambda agent, args, _ctx: handle(routes, args, agent, policy)
