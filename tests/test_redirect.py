import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from orquehelx import delegate
from orquehelx.routes import load

ROUTES = load({
    "claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
    "opencode": {"acp": ["opencode", "acp"]},
})


@pytest.mark.parametrize("args", [{"goal": "x"}, {"tasks": [{"goal": "x"}]}, {"goal": "x", "action": "spawn"}])
def test_native_delegate_task_is_redirected_to_delegate_to(args):
    verdict = delegate.redirect(ROUTES, tool_name="delegate_task", args=args)
    assert verdict["action"] == "block"
    assert "delegate_to" in verdict["message"]
    assert "claude" in verdict["message"] and "opencode" in verdict["message"]


@pytest.mark.parametrize("tool_name, args", [
    ("delegate_task", {"action": "list"}),
    ("delegate_task", {"action": "steer", "subagent_id": "sa-1", "message": "m"}),
    ("delegate_task", {"action": "stop", "subagent_id": "sa-1"}),
    ("terminal", {"command": "ls"}),
    ("delegate_to", {"route": "claude", "goal": "x"}),
])
def test_control_actions_and_other_tools_pass(tool_name, args):
    assert delegate.redirect(ROUTES, tool_name=tool_name, args=args) is None


def test_the_prompt_section_explains_the_routes():
    text = delegate.prompt_section(ROUTES)
    assert "delegate_to" in text
    assert "claude (claude-haiku-4-5)" in text and "opencode (ohx-opencode)" in text
    assert "delegate_task" in text


def test_register_hooks_up_the_hook_and_the_section():
    registered = {}
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: {"routes": {"claude": {"provider": "p"}}}.get(key, default),
        register_tool=lambda *_, **__: None,
        register_command=lambda *_, **__: None,
        register_hook=lambda name, fn: registered.setdefault("hooks", {}).update({name: fn}),
        register_system_prompt_section=lambda id, content, **_: registered.update({"section": (id, content)}),
    )
    delegate.register(ctx)
    assert "pre_tool_call" in registered["hooks"] and "api_request_error" in registered["hooks"]
    assert registered["section"][0] == "orquehelx.routes"
    verdict = registered["hooks"]["pre_tool_call"](tool_name="delegate_task", args={"goal": "x"}, task_id="t")
    assert verdict["action"] == "block"


def test_hermes_vetoes_delegate_task_with_the_plugin_loaded(hermes_home, monkeypatch):
    # The same path the executor takes before dispatching a tool.
    import hermes_cli.plugins as hp
    from hermes_cli.plugins import PluginManager, _dispatch_pre_tool_call_hooks

    shutil.copytree(Path(__file__).resolve().parents[1] / "orquehelx", hermes_home / "plugins" / "orquehelx")
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n"
        "        routes:\n          agy: {provider: antigravity-subscription-directsdk}\n", encoding="utf-8")
    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(hp, "_plugin_manager", manager, raising=False)
    blocked, _ = _dispatch_pre_tool_call_hooks("delegate_task", {"goal": "x"}, task_id="t", session_id="s")
    assert blocked and "delegate_to" in blocked
    allowed, _ = _dispatch_pre_tool_call_hooks("delegate_task", {"action": "list"}, task_id="t", session_id="s")
    assert allowed is None
