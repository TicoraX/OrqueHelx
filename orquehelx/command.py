"""``/ohx`` command: configured routes and the measured usage of each subscription.

Measurement happens on request (a reading less than 30 s old is reused). A provider without a meter shows
"no data": a percentage is never estimated.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from . import quota
from .routes import CONFIG_KEY, Route

USAGE = "usage: /ohx routes | /ohx quota"


def _time(when) -> str:
    return when.astimezone().strftime("%Y-%m-%d %H:%M")


def _routes(routes: dict[str, Route]) -> str:
    return "\n".join(f"{r.name}: {r.provider}" + (f" ({r.model})" if r.model else "") for r in routes.values())


def _quota_of(route: Route, windows: list | None, error: Exception | None) -> list[str]:
    lines = []
    ex = quota.lookup(route.provider, since=time.time() - 24 * 3600)
    if ex is not None and (ex.reset_at is None or ex.reset_at > datetime.now(timezone.utc)):
        reset = _time(ex.reset_at) if ex.reset_at else "unknown"
        lines.append(f"{route.name}: out of quota since {time.strftime('%H:%M', time.localtime(ex.seen))}"
                     f" (reset: {reset})")
    if error is not None:
        return [*lines, f"{route.name}: could not measure ({error})"]
    if windows is None:
        return [*lines, f"{route.name}: no data (the provider does not report usage)"]
    if not windows:
        return [*lines, f"{route.name}: no data (Hermes returned no usage windows)"]
    lines.append(f"{route.name}:")
    for label, used, reset_at in windows:
        # Truncate, do not round: 99.96 must not show as 100 (exhausted).
        value = "no data" if used is None else f"{math.floor(used * 10) / 10:g} %"
        lines.append(f"  {label}: {value}" + (f", resets {_time(reset_at)}" if reset_at else ""))
    return lines


def run(routes: dict[str, Route], arguments: str) -> str:
    sub = (arguments or "").strip().lower()
    if sub in ("routes", "quota") and not routes:
        return f"no routes configured: define {CONFIG_KEY} in config.yaml"
    if sub == "routes":
        return _routes(routes)
    if sub == "quota":
        # One measurement per provider: several routes can share the same subscription.
        measured = {p: quota.measure_safe(p) for p in dict.fromkeys(r.provider for r in routes.values())}
        return "\n".join(line for route in routes.values() for line in _quota_of(route, *measured[route.provider]))
    return USAGE
