import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from orquehelx import command, quota
from orquehelx.routes import load

ROUTES = load({
    "claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
    "agy": {"provider": "antigravity-subscription-directsdk", "model": "flash"},
})
RESET = datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(quota, "_seen", {})


def test_routes_lists_name_provider_and_model():
    out = command.run(ROUTES, "routes")
    assert "claude" in out and "claude-subscription-directsdk-experimental" in out
    assert "claude-haiku-4-5" in out and "agy" in out


def test_quota_shows_measured_windows_and_no_data(monkeypatch):
    monkeypatch.setitem(quota.METERS, "claude-subscription",
                        lambda: [("Current session", 30.0, RESET), ("Current week", 28.0, None)])
    out = command.run(ROUTES, "quota")
    assert "Current session: 30 %" in out
    assert RESET.astimezone().strftime("%Y-%m-%d %H:%M") in out
    assert "Current week: 28 %" in out
    assert "agy: no data" in out


def test_quota_with_a_failed_measurement_gives_the_cause(monkeypatch):
    def fails():
        raise ConnectionError("no network")
    monkeypatch.setitem(quota.METERS, "claude-subscription", fails)
    assert "could not measure (no network)" in command.run(ROUTES, "quota")


def test_quota_reports_a_seen_exhaustion():
    quota._seen["antigravity-subscription-directsdk"] = quota.Exhaustion(
        "antigravity-subscription-directsdk", "RESOURCE_EXHAUSTED", None, time.time())
    assert "agy: out of quota since" in command.run(ROUTES, "quota")


@pytest.mark.parametrize("args", ["", "other", "  "])
def test_unknown_subcommand_shows_usage(args):
    assert "usage: /ohx" in command.run(ROUTES, args)


def test_register_adds_the_ohx_command():
    from orquehelx import delegate
    commands = {}
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: {"routes": {"claude": {"provider": "p"}}}.get(key, default),
        register_tool=lambda *_, **__: None, register_hook=lambda *_, **__: None,
        register_system_prompt_section=lambda *_, **__: None,
        register_command=lambda name, fn, **_: commands.update({name: fn}),
    )
    delegate.register(ctx)
    assert commands["ohx"]("routes") == "claude: p"


def test_quota_measures_once_per_provider(monkeypatch):
    routes = load({
        "haiku": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
        "opus": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-opus-5-5"},
    })
    calls = []
    monkeypatch.setitem(quota.METERS, "claude-subscription",
                        lambda: calls.append(1) or [("Current session", 10.0, RESET)])
    out = command.run(routes, "quota")
    assert len(calls) == 1
    assert "haiku:" in out and "opus:" in out


def test_an_exhaustion_whose_reset_passed_is_not_shown_as_current():
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    quota._seen["antigravity-subscription-directsdk"] = quota.Exhaustion(
        "antigravity-subscription-directsdk", "RESOURCE_EXHAUSTED", past, time.time() - 3600)
    assert "out of quota since" not in command.run(ROUTES, "quota")


@pytest.mark.parametrize("used, text", [(56.0, "56 %"), (99.6, "99.6 %"), (99.96, "99.9 %"), (100.0, "100 %")])
def test_percentage_never_rounds_up_to_exhausted(monkeypatch, used, text):
    monkeypatch.setitem(quota.METERS, "claude-subscription", lambda: [("Current session", used, None)])
    assert f"Current session: {text}" in command.run(ROUTES, "quota")


def test_in_spanish_and_with_the_v01_subcommands(monkeypatch):
    monkeypatch.setattr("agent.i18n.get_language", lambda: "es-419")
    assert "agy: sin dato" in command.run(ROUTES, "cuota")
    assert "uso: /ohx" in command.run(ROUTES, "other")
