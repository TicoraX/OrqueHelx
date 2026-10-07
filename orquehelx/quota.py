"""Quota exhaustion: recorded only when unambiguous or measured, never on suspicion.

Hermes calls ``on_error`` (hook ``api_request_error``) on every failed call. It does not change how Hermes
classifies the error: it only notes which provider ran out of quota and when it resets, so ``delegate_to``
can tell the parent what happened instead of a generic failure.

    provider error
      |- unambiguous text (RESOURCE_EXHAUSTED from agy, 429 + "usage limit"/"quota") -> exhausted
      |- generic claude-subscription error -> measure /usage: window >= 100 % -> exhausted
      '- anything else -> not quota
    reset: the latest among the exhausted windows that apply to the model, or None if any of them reports
    no time ("no data"; never estimated). A window for another model ("Opus week" for a haiku route) does
    not block the route.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Exhaustion:
    provider: str
    message: str
    reset_at: datetime | None
    seen: float


Window = tuple[str, "float | None", "datetime | None"]  # (label, used %, reset)


def _windows(snap) -> list[Window]:
    return [(w.label, w.used_percent, w.reset_at) for w in snap.windows] if snap else []


def _measure_claude() -> list[Window]:
    # ponytail: private Hermes function; the claude-subscription profile does not implement
    # fetch_account_usage (reported upstream). Switch to fetch_account_usage once it does.
    from agent.account_usage import _fetch_anthropic_account_usage
    return _windows(_fetch_anthropic_account_usage())


def _measure_codex() -> list[Window]:
    from agent.account_usage import fetch_account_usage
    return _windows(fetch_account_usage("openai-codex"))


# Provider prefix -> window meter.
METERS = {"claude-subscription": _measure_claude, "openai-codex": _measure_codex}
_FAMILIES = ("opus", "sonnet", "haiku")

# A measurement is reused this long: the tab, /ohx and a pause each measure, and the usage endpoints
# answer 429 when called in a burst.
FRESH_SECONDS = 30
# After a 429 without a usable Retry-After, wait this long before calling that endpoint again.
DEFAULT_BACKOFF = 60

# HERMES_HOME this copy of the plugin was loaded for. Hermes loads the plugin once per home in the same process
# (dashboard profiles), and each copy keeps its own exhaustions: the dashboard picks the copy by this value.
HOME: str | None = None


def home_key(path) -> str:
    """One spelling per home: Hermes may hand the same path with a different case on Windows."""
    return os.path.normcase(os.path.abspath(str(path)))

_lock = threading.Lock()
_seen: dict[str, Exhaustion] = {}
_measured: dict[str, tuple[float, list[Window]]] = {}  # provider -> (monotonic time, windows)
_backoff: dict[str, tuple[float, Exception]] = {}  # provider -> (monotonic time it ends, the 429 error)


def _applies(label: str, model: str) -> bool:
    """A window named after a family ("Opus week") only counts for that model family."""
    families = [f for f in _FAMILIES if f in label.lower()]
    return not families or any(f in model.lower() for f in families)


def _retry_after(exc: Exception) -> float | None:
    """Seconds to hold off after a 429 (its Retry-After, else DEFAULT_BACKOFF); None for any other error."""
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 429:
        return None
    try:
        return max(float((getattr(response, "headers", None) or {}).get("retry-after")), 0)
    except (TypeError, ValueError):
        return DEFAULT_BACKOFF


def measure(provider: str, fresh: bool = False) -> list[Window] | None:
    """Measured windows for the provider; None if there is no meter. Measurement errors propagate.

    A measurement younger than FRESH_SECONDS is reused unless ``fresh``. After a 429 the same error is raised
    again, without calling the endpoint, until its Retry-After passes: the caller still shows the real cause."""
    meter = next((m for prefix, m in METERS.items() if provider.startswith(prefix)), None)
    if meter is None:
        return None
    now = time.monotonic()
    with _lock:
        held, cached = _backoff.get(provider), _measured.get(provider)
    if held is not None and now < held[0]:
        raise held[1]
    if not fresh and cached is not None and now - cached[0] < FRESH_SECONDS:
        return cached[1]
    try:
        windows = meter()
    except Exception as exc:
        wait = _retry_after(exc)
        if wait is not None:
            with _lock:
                _backoff[provider] = (now + wait, exc)
        raise
    with _lock:
        _measured[provider] = (now, windows)
    return windows


def measure_safe(provider: str) -> tuple[list[Window] | None, Exception | None]:
    """(windows, None) when measured, (None, None) without a meter, or (None, error) if measuring failed."""
    try:
        return measure(provider), None
    except Exception as exc:  # whoever shows the figure shows the cause; no value is made up
        return None, exc


def _window_exhausted(provider: str, model: str) -> tuple[bool | None, datetime | None]:
    """(exhausted, reset) from the measurement; (None, None) without a meter or if measuring failed."""
    try:
        # fresh: a cached figure from before the failure cannot confirm (or rule out) an exhaustion.
        windows = measure(provider, fresh=True)
    except Exception as exc:  # network, expired token, changed API: without a measurement nothing is claimed
        log.warning("orquehelx: could not measure the quota of %s: %s", provider, exc)
        return None, None
    if windows is None:
        return None, None
    exhausted = [r for label, used, r in windows if used is not None and used >= 100 and _applies(label, model)]
    if not exhausted:
        return False, None
    # The route comes back when ALL its exhausted windows reopen; one without a time leaves the reset unknown.
    return True, None if None in exhausted else max(exhausted)


def _unambiguous(message: str, status: int | None) -> bool:
    m = message.lower()
    if "resource_exhausted" in m and any(p in m for p in ("individual quota", "quota exceeded", "code 429")):
        return True
    return status == 429 and ("usage limit" in m or "quota" in m)


def on_error(provider: str = "", error: object = None, status_code: int | None = None, model: str = "", **_) -> None:
    """Hook ``api_request_error``. Hermes sends the text in ``error={"type", "message"}``
    (agent/api_request_hooks.py); return values are ignored."""
    provider, model = (provider or "").strip(), model or ""
    message = str(error.get("message") or "") if isinstance(error, dict) else str(error or "")
    if not provider:
        return
    if _unambiguous(message, status_code):
        _, reset_at = _window_exhausted(provider, model)
    elif provider.startswith("claude-subscription") and "native request failed" in message.lower():
        exhausted, reset_at = _window_exhausted(provider, model)
        if not exhausted:
            return
    else:
        return
    # ponytail: one record per provider, no task id; while the provider is exhausted, any child failing on
    # that provider in that window is quota. Track per task if false positives show up.
    with _lock:
        _seen[provider] = Exhaustion(provider, message[:300], reset_at, time.time())


def lookup(provider: str, since: float) -> Exhaustion | None:
    """The exhaustion of ``provider`` seen since ``since`` (epoch), or None."""
    with _lock:
        ex = _seen.get(provider)
    return ex if ex is not None and ex.seen >= since else None
