import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent.subagent_lifecycle import SubagentLifecycleError, SubagentState

from orquehelx import delegate, quota
from orquehelx.routes import ConfigError, RouteError, load

ROOT = Path(__file__).resolve().parents[1] / "orquehelx"
ROUTES = load({
    "claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
    "agy": {"provider": "antigravity-subscription-directsdk", "model": "flash"},
})


class FakeLifecycle:
    """Hermes' public subagent lifecycle, with the child's outcome decided by the test."""

    def __init__(self, state=SubagentState.SUCCEEDED, summary="done", error=None, launch_error=None):
        self.requests, self.state, self.summary, self.error, self.launch_error = [], state, summary, error, launch_error

    def launch(self, request):
        if self.launch_error:
            raise SubagentLifecycleError(self.launch_error)
        self.requests.append(request)
        return SimpleNamespace(subagent_id=f"sa-{len(self.requests)}")

    def wait(self, handle, timeout_seconds=None):
        return SimpleNamespace(handle=handle, state=self.state, completed=True)

    def result(self, handle):
        return SimpleNamespace(handle=handle, terminal_state=self.state, summary=self.summary,
                               error_message=self.error)


@pytest.fixture
def parent(monkeypatch):
    """The parent agent Hermes binds for the turn (agent.subagent_lifecycle.get_active_subagent_parent)."""
    agent = SimpleNamespace(_current_turn_id="turn-1")
    monkeypatch.setattr("agent.subagent_lifecycle.get_active_subagent_parent", lambda: agent)
    return agent


@pytest.fixture(autouse=True)
def clean_quota(monkeypatch):
    monkeypatch.setattr(quota, "_seen", {})


def _exhaust(provider, reset_at=None):
    quota._seen[provider] = quota.Exhaustion(provider, "RESOURCE_EXHAUSTED", reset_at, time.time() + 5)


def _fake_ctx(routes_config, lifecycle=None, **settings):
    tools = {}
    values = {"routes": routes_config, **settings}
    return SimpleNamespace(
        tools=tools, subagent_lifecycle=lifecycle or FakeLifecycle(),
        get_config=lambda key, default=None: values.get(key, default),
        register_hook=lambda name, fn: tools.update({"hook:" + name: fn}),
        register_command=lambda name, fn, **kw: tools.update({"command:" + name: (fn, kw)}),
        register_system_prompt_section=lambda id, content, **_: tools.update({"section:" + id: content}),
        register_tool=lambda name, toolset, schema, handler, **_: tools.update(
            {name: {"toolset": toolset, "schema": schema, "handler": handler}}),
    )


def test_schema_offers_only_the_configured_routes():
    schema = delegate.schema(ROUTES)
    props = schema["parameters"]["properties"]
    assert schema["name"] == "delegate_to"
    assert props["route"]["enum"] == ["claude", "agy"]
    assert schema["parameters"]["required"] == ["route", "goal"]
    assert "claude-haiku-4-5" in schema["description"]


def test_the_child_runs_on_the_route_provider_and_model(parent):
    life = FakeLifecycle(summary="ok")
    out = json.loads(delegate.handle(life, ROUTES, {"route": "claude", "goal": " say hi ", "context": "c"}))
    assert out == {"status": "completed", "route": "claude", "summary": "ok"}
    (req,) = life.requests
    assert (req.goal, req.context) == ("say hi", "c")
    assert (req.provider, req.model) == ("claude-subscription-directsdk-experimental", "claude-haiku-4-5")


def test_the_child_gets_the_route_model_never_the_parent_one(parent):
    life = FakeLifecycle()
    delegate.handle(life, ROUTES, {"route": "agy", "goal": "x"})
    assert life.requests[0].provider == "antigravity-subscription-directsdk" and life.requests[0].model == "flash"


@pytest.mark.parametrize("args, piece", [
    ({"route": "codex", "goal": "x"}, "claude, agy"),
    ({"route": "claude"}, "goal"),
    ({"route": "claude", "goal": "   "}, "goal"),
    ({"route": "claude", "goal": 42}, "goal"),
    ({"route": ["claude"], "goal": "x"}, "claude, agy"),
])
def test_invalid_arguments_return_a_readable_error(parent, args, piece):
    # Arguments come from the model: unexpected types must not break the handler.
    life = FakeLifecycle()
    assert piece in json.loads(delegate.handle(life, ROUTES, args))["error"]
    assert life.requests == []


@pytest.mark.parametrize("context, expected", [(" data ", "data"), ("   ", None), ({"a": 1}, None), (None, None)])
def test_context_is_normalized(parent, context, expected):
    life = FakeLifecycle()
    delegate.handle(life, ROUTES, {"route": "claude", "goal": "x", "context": context})
    assert life.requests[0].context == expected


def test_a_launch_hermes_refuses_comes_back_as_a_readable_error(parent):
    out = json.loads(delegate.handle(FakeLifecycle(launch_error="depth limit"), ROUTES, {"route": "claude", "goal": "x"}))
    assert "claude" in out["error"] and "depth limit" in out["error"]


def test_a_failure_that_is_not_quota_passes_through(parent):
    life = FakeLifecycle(state=SubagentState.FAILED, summary=None, error="timeout")
    out = json.loads(delegate.handle(life, ROUTES, {"route": "claude", "goal": "x"}))
    assert out == {"status": "failed", "route": "claude", "error": "timeout", "summary": None}


def test_a_completed_child_ignores_an_exhaustion_seen_earlier(parent):
    _exhaust("claude-subscription-directsdk-experimental")
    out = json.loads(delegate.handle(FakeLifecycle(), ROUTES, {"route": "claude", "goal": "x"}))
    assert out["status"] == "completed"


@pytest.mark.parametrize("policy, piece", [
    ("ask", "ask whether to send"), ("parent_decides", "you may resend"), ("wait", "do nothing else"),
])
def test_a_child_out_of_quota_returns_the_status_and_the_policy(parent, policy, piece):
    life = FakeLifecycle(state=SubagentState.FAILED)
    _exhaust("claude-subscription-directsdk-experimental", datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc))
    out = json.loads(delegate.handle(life, ROUTES, {"route": "claude", "goal": "x"}, policy))
    assert out["status"] == "out_of_quota" and out["route"] == "claude"
    assert out["reset_at"] == "2026-09-26T05:59:00+00:00" and out["other_routes"] == ["agy"]
    assert piece in out["instruction"].lower()


def test_out_of_quota_without_a_measured_reset_says_so(parent):
    _exhaust("antigravity-subscription-directsdk")
    out = json.loads(delegate.handle(FakeLifecycle(state=SubagentState.FAILED), ROUTES, {"route": "agy", "goal": "x"}))
    assert out["reset_at"] is None and "unknown" in out["instruction"].lower()


@pytest.mark.parametrize("policy", ["ask", "wait"])
def test_out_of_quota_blocks_another_delegation_in_the_same_turn(parent, policy):
    # Without the user's consent there is no route switch; consent arrives with their next message (new turn).
    life = FakeLifecycle(state=SubagentState.FAILED)
    _exhaust("claude-subscription-directsdk-experimental")
    assert json.loads(delegate.handle(life, ROUTES, {"route": "claude", "goal": "x"}, policy))["status"] == "out_of_quota"
    blocked = json.loads(delegate.handle(life, ROUTES, {"route": "agy", "goal": "x"}, policy))
    assert "approve" in blocked["error"] and len(life.requests) == 1
    parent._current_turn_id = "turn-2"
    delegate.handle(life, ROUTES, {"route": "agy", "goal": "x"}, policy)
    assert len(life.requests) == 2


def test_parent_decides_may_switch_routes_in_the_same_turn(parent):
    life = FakeLifecycle(state=SubagentState.FAILED)
    _exhaust("claude-subscription-directsdk-experimental")
    delegate.handle(life, ROUTES, {"route": "claude", "goal": "x"}, "parent_decides")
    delegate.handle(life, ROUTES, {"route": "agy", "goal": "x"}, "parent_decides")
    assert len(life.requests) == 2


def test_register_wires_the_tool_to_the_plugin_lifecycle(parent):
    life = FakeLifecycle(summary="from the child")
    ctx = _fake_ctx({"claude": {"provider": "p", "model": "m"}}, life)
    delegate.register(ctx)
    out = json.loads(ctx.tools["delegate_to"]["handler"]({"route": "claude", "goal": "x"}, task_id="t"))
    assert out["summary"] == "from the child" and life.requests[0].provider == "p"


@pytest.mark.parametrize("config", [None, {}])
def test_without_routes_it_loads_and_says_so_when_used(parent, config):
    # Freshly installed there are no routes: the plugin loads (Hermes' catalog CI tests it that way) and each
    # entry point explains what to configure. A badly written routes config still fails at load (test_routes).
    ctx = _fake_ctx(config)
    delegate.register(ctx)
    assert "enum" not in ctx.tools["delegate_to"]["schema"]["parameters"]["properties"]["route"]
    out = json.loads(ctx.tools["delegate_to"]["handler"]({"route": "x", "goal": "y"}))
    assert "plugins.entries.orquehelx.settings.routes" in out["error"]
    assert ctx.tools["hook:pre_tool_call"](tool_name="delegate_task", args={}) is None
    assert "plugins.entries.orquehelx.settings.routes" in ctx.tools["command:ohx"][0]("routes")
    assert "section:orquehelx.routes" not in ctx.tools


@pytest.mark.parametrize("config", [[], 0, False, "claude"])
def test_routes_of_an_invalid_type_fail_at_load(config):
    with pytest.raises(RouteError, match="plugins.entries.orquehelx.settings.routes"):
        delegate.register(_fake_ctx(config))


@pytest.mark.parametrize("policy", ["auto", ["ask"], {"a": 1}, 3])
def test_an_invalid_policy_fails_at_load_naming_the_key(policy):
    with pytest.raises(ConfigError, match=r"settings\.policy"):
        delegate.register(_fake_ctx({"claude": {"provider": "p", "model": "m"}}, policy=policy))


def test_an_invalid_v01_policy_names_the_key_the_user_wrote():
    with pytest.raises(ConfigError, match=r"settings\.politica"):
        delegate.register(_fake_ctx({"claude": {"provider": "p", "model": "m"}}, politica="auto"))


def test_the_v01_config_still_loads():
    ctx = _fake_ctx(None, rutas={"claude": {"provider": "p", "model": "m"}}, politica="padre_decide")
    delegate.register(ctx)
    assert ctx.tools["delegate_to"]["schema"]["parameters"]["properties"]["route"]["enum"] == ["claude"]


def _install(hermes_home, routes_yaml):
    # Install the plugin as `hermes plugins install` would, with the user's config.
    shutil.copytree(ROOT, hermes_home / "plugins" / "orquehelx", ignore=shutil.ignore_patterns("__pycache__"))
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n" + routes_yaml,
        encoding="utf-8")


def test_the_plugin_loads_in_the_real_plugin_manager(hermes_home):
    _install(hermes_home, "        routes:\n          claude: {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}\n")
    from hermes_cli.plugins import PluginManager
    from tools.registry import registry

    manager = PluginManager()
    manager.discover_and_load()
    (plugin,) = [p for p in manager.list_plugins() if p["name"] == "orquehelx"]
    assert plugin["enabled"] and plugin["error"] is None and plugin["tools"] == 1 and plugin["hooks"] == 2
    assert registry.get_entry("delegate_to") is not None


def test_the_plugin_imports_only_public_hermes_modules():
    # Catalog rule 9: `hermes plugins validate` warns on every import of an undocumented core module.
    from hermes_cli.plugin_validate_private_imports import private_import_findings
    assert private_import_findings(ROOT) == []
