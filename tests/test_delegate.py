import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from orquehelx import delegate, quota
from orquehelx.routes import ConfigError, RouteError, load

ROUTES = load({
    "claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
    "opencode": {"acp": ["opencode", "acp"]},
})


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake_delegate_task(**kw):
        log.append(kw)
        return json.dumps({"results": [{"status": "completed"}]})

    monkeypatch.setattr("tools.delegate_tool.delegate_task", fake_delegate_task)
    return log


@pytest.fixture
def clean_quota(monkeypatch):
    monkeypatch.setattr(quota, "_seen", {})


def _fake_ctx(routes_config, **settings):
    tools = {}
    values = {"routes": routes_config, **settings}
    return SimpleNamespace(
        tools=tools,
        get_config=lambda key, default=None: values.get(key, default),
        register_hook=lambda name, fn: tools.update({"hook:" + name: fn}),
        register_command=lambda name, fn, **_: tools.update({"command:" + name: fn}),
        register_system_prompt_section=lambda id, content, **_: tools.update({"section:" + id: content}),
        register_tool=lambda name, toolset, schema, handler, **_: tools.update(
            {name: {"toolset": toolset, "schema": schema, "handler": handler}}),
    )


def _child(status):
    return lambda **_: json.dumps({"results": [{"status": status, "summary": None, "error": "failure"}]})


def _exhaust(provider, reset_at=None):
    quota._seen[provider] = quota.Exhaustion(provider, "RESOURCE_EXHAUSTED", reset_at, time.time() + 5)


def _agent(turn):
    return SimpleNamespace(_current_turn_id=turn)


def test_schema_offers_only_the_configured_routes():
    schema = delegate.schema(ROUTES)
    props = schema["parameters"]["properties"]
    assert schema["name"] == "delegate_to"
    assert props["route"]["enum"] == ["claude", "opencode"]
    assert schema["parameters"]["required"] == ["route", "goal"]
    assert "claude-haiku-4-5" in schema["description"]


def test_delegating_pins_the_provider_and_turns_fallback_off(calls):
    agent = object()
    out = delegate.handle(ROUTES, {"route": "opencode", "goal": "say hi", "context": "c"}, agent)
    assert json.loads(out)["results"][0]["status"] == "completed"
    (kw,) = calls
    assert kw["goal"] == "say hi" and kw["context"] == "c" and kw["parent_agent"] is agent
    assert kw["credentials_cfg"] == {"provider": "ohx-opencode", "fallback_providers": []}


@pytest.mark.parametrize("args, piece", [
    ({"route": "codex", "goal": "x"}, "claude, opencode"),
    ({"route": "claude"}, "goal"),
    ({"route": "claude", "goal": "   "}, "goal"),
    ({"route": "claude", "goal": 42}, "goal"),
    ({"route": ["claude"], "goal": "x"}, "claude, opencode"),
])
def test_invalid_arguments_return_a_readable_error(calls, args, piece):
    # Arguments come from the model: unexpected types must not break the handler.
    out = json.loads(delegate.handle(ROUTES, args, object()))
    assert piece in out["error"]
    assert calls == []


def test_without_parent_agent_explains_the_cause(calls):
    out = json.loads(delegate.handle(ROUTES, {"route": "claude", "goal": "x"}, None))
    assert "parent agent" in out["error"]
    assert calls == []


@pytest.mark.parametrize("context, expected", [(" data ", "data"), ("   ", None), ({"a": 1}, None), (None, None)])
def test_context_is_normalized(calls, context, expected):
    delegate.handle(ROUTES, {"route": "claude", "goal": "x", "context": context}, object())
    assert calls[0]["context"] == expected


def test_the_inline_executor_receives_the_agent(monkeypatch, calls):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS

    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegate_to", raising=False)
    delegate.register(_fake_ctx({"claude": {"provider": "p"}}))
    agent = object()
    INLINE_TOOL_EXECUTORS["delegate_to"](agent, {"route": "claude", "goal": "x"}, None)
    assert calls[0]["parent_agent"] is agent
    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegate_to")


def test_the_registry_handler_uses_whatever_parent_agent_arrives(monkeypatch, calls):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS

    ctx = _fake_ctx({"claude": {"provider": "p"}})
    delegate.register(ctx)
    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegate_to")
    agent = object()
    ctx.tools["delegate_to"]["handler"]({"route": "claude", "goal": "x"}, parent_agent=agent, task_id="t")
    assert calls[0]["parent_agent"] is agent


def _install(hermes_home, routes_yaml):
    # Install the plugin as `hermes plugins install` would, with the user's config.
    shutil.copytree(Path(__file__).resolve().parents[1] / "orquehelx", hermes_home / "plugins" / "orquehelx")
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n" + routes_yaml,
        encoding="utf-8")


def test_regression_cold_load_within_the_hermes_limit(hermes_home):
    # Regression: importing model_tools inside register() blocked the loader until its timeout (10 s) and
    # Hermes dropped the plugin. It does not show in the pytest process: the modules are already loaded.
    import os
    import subprocess
    import sys

    _install(hermes_home, "        routes:\n          opencode: {acp: [opencode, acp]}\n")
    code = (
        "from hermes_cli.plugins import PluginManager\n"
        "m = PluginManager(); m.discover_and_load()\n"
        "p = [p for p in m.list_plugins() if p['name'] == 'orquehelx'][0]\n"
        "print(p['enabled'], p['tools'], p['hooks'], p['commands'], p['error'])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False,
                         env={**os.environ, "HERMES_HOME": str(hermes_home)})
    assert out.stdout.strip().splitlines()[-1] == "True 1 2 1 None", out.stdout + out.stderr


def test_the_plugin_loads_in_the_real_plugin_manager(hermes_home):
    _install(hermes_home, "        routes:\n          opencode: {acp: [opencode, acp]}\n")
    from hermes_cli.plugins import PluginManager
    from providers import get_provider_profile
    from tools.registry import registry

    PluginManager().discover_and_load()
    assert registry.get_entry("delegate_to") is not None
    assert get_provider_profile("ohx-opencode").process_command == "opencode"


@pytest.mark.parametrize("policy, piece", [
    ("ask", "ask whether to send"),
    ("parent_decides", "you may resend"),
    ("wait", "do nothing else"),
])
@pytest.mark.usefixtures("clean_quota")
def test_child_out_of_quota_returns_status_and_policy(monkeypatch, policy, piece):
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _child("failed"))
    _exhaust("claude-subscription-directsdk-experimental", datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc))
    out = json.loads(delegate.handle(ROUTES, {"route": "claude", "goal": "x"}, object(), policy=policy))
    assert out["status"] == "out_of_quota"
    assert out["route"] == "claude"
    assert out["reset_at"] == "2026-09-26T05:59:00+00:00"
    assert out["other_routes"] == ["opencode"]
    assert piece in out["instruction"].lower()


@pytest.mark.usefixtures("clean_quota")
def test_out_of_quota_without_a_measured_reset_says_so(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _child("failed"))
    _exhaust("ohx-opencode")
    out = json.loads(delegate.handle(ROUTES, {"route": "opencode", "goal": "x"}, object()))
    assert out["status"] == "out_of_quota" and out["reset_at"] is None
    assert "unknown" in out["instruction"].lower()


@pytest.mark.usefixtures("clean_quota")
def test_a_failure_that_is_not_quota_passes_through(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _child("failed"))
    out = json.loads(delegate.handle(ROUTES, {"route": "claude", "goal": "x"}, object()))
    assert out["results"][0]["status"] == "failed"


@pytest.mark.usefixtures("clean_quota")
def test_a_completed_child_ignores_old_exhaustions(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _child("completed"))
    _exhaust("claude-subscription-directsdk-experimental")
    out = json.loads(delegate.handle(ROUTES, {"route": "claude", "goal": "x"}, object()))
    assert out["results"][0]["status"] == "completed"


@pytest.mark.parametrize("config", [None, {}])
def test_without_routes_it_loads_and_says_so_when_used(calls, config):
    # Freshly installed there are no routes: the plugin loads (Hermes' catalog CI tests it that way) and each
    # entry point explains what to configure. A badly written routes config still fails at load (test_routes).
    ctx = _fake_ctx(config)
    delegate.register(ctx)
    assert "enum" not in ctx.tools["delegate_to"]["schema"]["parameters"]["properties"]["route"]
    out = json.loads(ctx.tools["delegate_to"]["handler"]({"route": "x", "goal": "y"}, parent_agent=object()))
    assert "plugins.entries.orquehelx.settings.routes" in out["error"]
    assert calls == []
    assert ctx.tools["hook:pre_tool_call"](tool_name="delegate_task", args={}) is None
    assert "plugins.entries.orquehelx.settings.routes" in ctx.tools["command:ohx"]("routes")
    assert "section:orquehelx.routes" not in ctx.tools


@pytest.mark.parametrize("config", [[], 0, False, "claude"])
def test_routes_of_an_invalid_type_fail_at_load(config):
    with pytest.raises(RouteError, match="plugins.entries.orquehelx.settings.routes"):
        delegate.register(_fake_ctx(config))


@pytest.mark.parametrize("policy", ["auto", ["ask"], {"a": 1}, 3])
def test_an_invalid_policy_fails_at_load_naming_the_key(policy):
    with pytest.raises(ConfigError, match="policy"):
        delegate.register(_fake_ctx({"claude": {"provider": "p"}}, policy=policy))


def test_the_v01_spanish_config_still_loads(calls):
    # v0.1.x used rutas/politica and Spanish values; whoever upgrades must not have to touch config.yaml.
    ctx = _fake_ctx(None, rutas={"claude": {"provider": "p"}}, politica="padre_decide")
    delegate.register(ctx)
    assert ctx.tools["delegate_to"]["schema"]["parameters"]["properties"]["route"]["enum"] == ["claude"]


def test_what_the_model_reads_does_not_depend_on_the_language(monkeypatch):
    monkeypatch.setattr("agent.i18n.get_language", lambda: "es")
    assert delegate.prompt_section(ROUTES).startswith("Subagents (OrqueHelx)")
    assert "Delegate a task" in delegate.schema(ROUTES)["description"]


@pytest.mark.usefixtures("clean_quota")
@pytest.mark.parametrize("policy", ["ask", "wait"])
def test_out_of_quota_blocks_another_delegation_in_the_same_turn(monkeypatch, policy):
    # Without the user's consent there is no route switch; consent arrives with their next message (new turn).
    calls = []
    monkeypatch.setattr("tools.delegate_tool.delegate_task", lambda **kw: calls.append(kw) or _child("failed")())
    _exhaust("claude-subscription-directsdk-experimental")
    parent = _agent("turn-1")
    first = json.loads(delegate.handle(ROUTES, {"route": "claude", "goal": "x"}, parent, policy))
    assert first["status"] == "out_of_quota"

    blocked = json.loads(delegate.handle(ROUTES, {"route": "opencode", "goal": "x"}, parent, policy))
    assert "approve" in blocked["error"]
    assert len(calls) == 1

    parent._current_turn_id = "turn-2"
    delegate.handle(ROUTES, {"route": "opencode", "goal": "x"}, parent, policy)
    assert len(calls) == 2


@pytest.mark.usefixtures("clean_quota")
def test_parent_decides_may_switch_routes_in_the_same_turn(monkeypatch):
    calls = []
    monkeypatch.setattr("tools.delegate_tool.delegate_task", lambda **kw: calls.append(kw) or _child("failed")())
    _exhaust("claude-subscription-directsdk-experimental")
    parent = _agent("turn-1")
    delegate.handle(ROUTES, {"route": "claude", "goal": "x"}, parent, "parent_decides")
    delegate.handle(ROUTES, {"route": "opencode", "goal": "x"}, parent, "parent_decides")
    assert len(calls) == 2


def test_an_invalid_v01_policy_names_the_key_the_user_wrote():
    with pytest.raises(ConfigError, match=r"settings\.politica"):
        delegate.register(_fake_ctx({"claude": {"provider": "p"}}, politica="auto"))
