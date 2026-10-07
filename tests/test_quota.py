import shutil
import sys
import time
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orquehelx import quota

RESET_5H = datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc)
RESET_WEEK = datetime(2026, 9, 28, 23, 59, tzinfo=timezone.utc)
AGY = "antigravity-subscription-directsdk"
CLAUDE = "claude-subscription-directsdk-experimental"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(quota, "_seen", {})


def _meter(monkeypatch, prefix, result):
    def measure():
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setitem(quota.METERS, prefix, measure)


def _fail(provider, message, status=None, model=""):
    # Real shape of Hermes' payload (agent/api_request_hooks.py): the text travels in error["message"].
    quota.on_error(provider=provider, model=model, status_code=status,
                   error={"type": "RuntimeError", "message": message})


def test_agy_is_recognized_by_text_with_no_measured_reset():
    _fail(AGY, "Antigravity model error: RESOURCE_EXHAUSTED: Individual quota reached")
    ex = quota.lookup(AGY, since=0)
    assert ex is not None and ex.reset_at is None


def test_generic_claude_error_is_confirmed_by_the_measured_window(monkeypatch):
    _meter(monkeypatch, "claude-subscription", [("Current session", 100.0, RESET_5H), ("Current week", 40.0, None)])
    _fail(CLAUDE, "Native request failed: error_during_execution", model="claude-haiku-4-5")
    assert quota.lookup(CLAUDE, since=0).reset_at == RESET_5H


def test_several_exhausted_windows_report_the_latest(monkeypatch):
    _meter(monkeypatch, "claude-subscription",
           [("Current session", 100.0, RESET_5H), ("Current week", 100.0, RESET_WEEK)])
    _fail(CLAUDE, "Native request failed: x", model="claude-haiku-4-5")
    assert quota.lookup(CLAUDE, since=0).reset_at == RESET_WEEK


def test_an_exhausted_window_without_time_leaves_the_reset_unknown(monkeypatch):
    _meter(monkeypatch, "claude-subscription",
           [("Current session", 100.0, RESET_5H), ("Current week", 100.0, None)])
    _fail(CLAUDE, "Native request failed: x", model="claude-haiku-4-5")
    ex = quota.lookup(CLAUDE, since=0)
    assert ex is not None and ex.reset_at is None


@pytest.mark.parametrize("model, exhausted", [("claude-haiku-4-5", False), ("claude-opus-5-5", True)])
def test_a_window_for_another_model_does_not_block_the_route(monkeypatch, model, exhausted):
    _meter(monkeypatch, "claude-subscription", [("Current session", 20.0, RESET_5H), ("Opus week", 100.0, RESET_WEEK)])
    _fail(CLAUDE, "Native request failed: x", model=model)
    assert (quota.lookup(CLAUDE, since=0) is not None) is exhausted


@pytest.mark.parametrize("measurement", [[("Current session", 30.0, RESET_5H)], [], ConnectionError("no network")])
def test_claude_without_an_exhausted_window_claims_no_quota(monkeypatch, measurement):
    _meter(monkeypatch, "claude-subscription", measurement)
    _fail(CLAUDE, "Native request failed: error_during_execution")
    assert quota.lookup(CLAUDE, since=0) is None


def test_429_with_usage_limit_on_any_provider():
    _fail("other", "Usage limit reached, resets at 14:30", status=429)
    assert quota.lookup("other", since=0) is not None


@pytest.mark.parametrize("provider, message, status", [
    (AGY, "Antigravity CLI is not authenticated", None),
    ("other", "Usage limit reached", 500),
    (CLAUDE, "connection reset", None),
    ("", "quota", 429),
])
def test_errors_that_are_not_quota(monkeypatch, provider, message, status):
    _meter(monkeypatch, "claude-subscription", [("Current session", 100.0, RESET_5H)])
    _fail(provider, message, status)
    assert quota.lookup(provider, since=0) is None


def test_a_payload_without_error_does_not_break_the_hook():
    quota.on_error(provider=AGY)
    quota.on_error(provider=AGY, error="RESOURCE_EXHAUSTED quota exceeded")
    assert quota.lookup(AGY, since=0) is not None


def test_lookup_ignores_earlier_exhaustions():
    _fail(AGY, "RESOURCE_EXHAUSTED quota exceeded")
    assert quota.lookup(AGY, since=time.time() + 1) is None


def test_hermes_calls_the_hook_with_its_real_payload(hermes_home, monkeypatch):
    # Calls the Hermes method that builds the payload (not on_error directly): if Hermes changes the payload
    # shape, this test fails.
    from types import SimpleNamespace

    import hermes_cli.plugins as hp
    from agent.api_request_hooks import ApiRequestHooksMixin
    from hermes_cli.plugins import PluginManager

    shutil.copytree(Path(__file__).resolve().parents[1] / "orquehelx", hermes_home / "plugins" / "orquehelx")
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n"
        "        routes:\n          agy: {provider: antigravity-subscription-directsdk}\n", encoding="utf-8")
    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(hp, "_plugin_manager", manager, raising=False)
    agent = SimpleNamespace(session_id="s", platform="cli", model="flash", provider=AGY, base_url="", api_mode="x",
                            _api_request_payload_for_hook=lambda _: {})
    ApiRequestHooksMixin._invoke_api_request_error_hook(
        agent, task_id="t", turn_id="u", api_request_id="a", api_call_count=1, api_start_time=time.time(),
        api_kwargs=None, error_type="RuntimeError",
        error_message="Antigravity model error: RESOURCE_EXHAUSTED: Individual quota reached")
    # Hermes names the module hermes_plugins.orquehelx, or adds __home_<digest> when another home already
    # claimed that name in this process (an earlier test that loaded the plugin).
    loaded = [m for k, m in sys.modules.items() if k.startswith("hermes_plugins.orquehelx") and k.endswith(".quota")]
    assert any(m.lookup(AGY, since=0) is not None for m in loaded)


class _HTTP429(Exception):
    """Same shape as httpx.HTTPStatusError: the response carries the status and the headers."""

    def __init__(self, retry_after=None):
        super().__init__("429 Too Many Requests")
        headers = {"retry-after": retry_after} if retry_after is not None else {}
        self.response = SimpleNamespace(status_code=429, headers=headers)


def test_a_measurement_is_reused_for_a_short_while(monkeypatch):
    calls = []
    monkeypatch.setitem(quota.METERS, "claude-subscription", lambda: calls.append(1) or [("Current session", 10.0, None)])
    assert quota.measure(CLAUDE) == quota.measure(CLAUDE) == [("Current session", 10.0, None)]
    assert len(calls) == 1
    monkeypatch.setattr(quota.time, "monotonic", lambda real=quota.time.monotonic: real() + quota.FRESH_SECONDS + 1)
    quota.measure(CLAUDE)
    assert len(calls) == 2


def test_confirming_an_exhaustion_always_measures_again(monkeypatch):
    windows = [[("Current session", 90.0, None)]]
    monkeypatch.setitem(quota.METERS, "claude-subscription", lambda: windows[0])
    quota.measure(CLAUDE)  # cached at 90 %
    windows[0] = [("Current session", 100.0, RESET_5H)]
    _fail(CLAUDE, "Native request failed: x", model="claude-haiku-4-5")
    assert quota.lookup(CLAUDE, since=0).reset_at == RESET_5H


@pytest.mark.parametrize("retry_after, wait", [("120", 120), (None, quota.DEFAULT_BACKOFF), ("not-a-number", quota.DEFAULT_BACKOFF)])
def test_a_429_is_not_retried_until_its_retry_after(monkeypatch, retry_after, wait):
    calls = []

    def meter():
        calls.append(1)
        raise _HTTP429(retry_after)
    monkeypatch.setitem(quota.METERS, "claude-subscription", meter)
    first = quota.measure_safe(CLAUDE)
    second = quota.measure_safe(CLAUDE)
    assert first[0] is None and isinstance(first[1], _HTTP429)
    assert second[1] is first[1] and len(calls) == 1  # the same error, honest, without calling again
    monkeypatch.setattr(quota.time, "monotonic", lambda real=quota.time.monotonic: real() + wait + 1)
    quota.measure_safe(CLAUDE)
    assert len(calls) == 2


def test_other_errors_are_not_cached(monkeypatch):
    calls = []

    def meter():
        calls.append(1)
        raise ConnectionError("no network")
    monkeypatch.setitem(quota.METERS, "claude-subscription", meter)
    quota.measure_safe(CLAUDE)
    quota.measure_safe(CLAUDE)
    assert len(calls) == 2
