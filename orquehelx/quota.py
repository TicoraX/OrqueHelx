"""Quota exhaustion: recorded only when unambiguous or measured, never on suspicion.

Hermes calls ``on_error`` (hook ``api_request_error``) on every failed call. It does not change how Hermes
classifies the error: it only notes which provider ran out of quota and when it resets, so ``delegate_to``
can tell the parent what happened instead of a generic failure.

    provider error
      |- unambiguous text (RESOURCE_EXHAUSTED from agy, 429 + "usage limit"/"quota") -> exhausted
      |- generic claude-subscription error -> measure the usage windows: one >= 100 % -> exhausted
      '- anything else -> not quota
    reset: the latest among the exhausted windows that apply to the model, or None if any of them reports
    no time (never estimated). A window for another model ("Opus week" for a haiku route) does not block it.
"""
from __future__ import annotations

import logging
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


def _measure(slug: str) -> list[Window]:
    # Hermes' public usage fetcher: "anthropic" reads the Claude Code OAuth login the subscription uses. It
    # answers None on any error (a 429 included), which shows here as no windows.
    from agent.account_usage import fetch_account_usage
    snap = fetch_account_usage(slug)
    return [(w.label, w.used_percent, w.reset_at) for w in snap.windows] if snap else []


# Provider prefix -> window meter.
METERS = {"claude-subscription": lambda: _measure("anthropic"), "openai-codex": lambda: _measure("openai-codex")}
_FAMILIES = ("opus", "sonnet", "haiku")
# A measurement is reused this long: /ohx and a failure confirmation each measure, and the usage endpoints
# answer 429 when called in a burst.
FRESH_SECONDS = 30

_lock = threading.Lock()
_seen: dict[str, Exhaustion] = {}
_measured: dict[str, tuple[float, list[Window]]] = {}  # provider -> (monotonic time, windows)


def _applies(label: str, model: str) -> bool:
    """A window named after a family ("Opus week") only counts for that model family."""
    families = [f for f in _FAMILIES if f in label.lower()]
    return not families or any(f in model.lower() for f in families)


def measure(provider: str, fresh: bool = False) -> list[Window] | None:
    """Measured windows for the provider; None if there is no meter. A measurement younger than FRESH_SECONDS
    is reused unless ``fresh``; an empty one is not (Hermes answers no windows on any error)."""
    meter = next((m for prefix, m in METERS.items() if provider.startswith(prefix)), None)
    if meter is None:
        return None
    with _lock:
        cached = _measured.get(provider)
    if not fresh and cached is not None and time.monotonic() - cached[0] < FRESH_SECONDS:
        return cached[1]
    windows = meter()
    if windows:
        with _lock:
            _measured[provider] = (time.monotonic(), windows)
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
    except Exception as exc:  # without a measurement nothing is claimed
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
    """Hook ``api_request_error``. Hermes sends the text in ``error={"type", "message"}``; return values are
    ignored."""
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
