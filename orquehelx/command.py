"""``/ohx`` command: configured routes and the measured usage of each subscription.

Measurement happens on request (manual refresh, no timers). A provider without a meter shows "no data": a
percentage is never estimated.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from . import quota
from .routes import CONFIG_KEY, Route
from .texts import t

# English subcommands (v0.2) and their v0.1 names.
_SUB = {"routes": "routes", "rutas": "routes", "quota": "quota", "cuota": "quota"}


def _time(when) -> str:
    return when.astimezone().strftime("%Y-%m-%d %H:%M")


def _routes(routes: dict[str, Route]) -> str:
    return "\n".join(f"{r.name}: {r.provider}" + (f" ({r.model})" if r.model else "") for r in routes.values())


def _quota_of(route: Route, windows: list | None, error: Exception | None) -> list[str]:
    lines = []
    ex = quota.lookup(route.provider, since=time.time() - 24 * 3600)
    current = ex is not None and (ex.reset_at is None or ex.reset_at > datetime.now(timezone.utc))
    if current:
        reset = _time(ex.reset_at) if ex.reset_at else t("no_data")
        lines.append(t("out_since", route=route.name, since=time.strftime("%H:%M", time.localtime(ex.seen)),
                       reset=reset))
    if error is not None:
        return [*lines, t("measure_failed", route=route.name, error=error)]
    if windows is None:
        return [*lines, t("provider_silent", route=route.name)]
    if not windows:
        return [*lines, t("no_windows", route=route.name)]
    lines.append(f"{route.name}:")
    for label, used, reset_at in windows:
        # Truncate, do not round: 99.96 must not show as 100 (exhausted).
        value = t("no_data") if used is None else f"{math.floor(used * 10) / 10:g} %"
        lines.append(f"  {label}: {value}" + (t("resets", when=_time(reset_at)) if reset_at else ""))
    return lines


def run(routes: dict[str, Route], arguments: str) -> str:
    sub = _SUB.get((arguments or "").strip().lower())
    if sub and not routes:
        return t("no_routes", key=CONFIG_KEY)
    if sub == "routes":
        return _routes(routes)
    if sub == "quota":
        # One measurement per provider: several routes can share the same subscription.
        measured = {p: quota.measure_safe(p) for p in dict.fromkeys(r.provider for r in routes.values())}
        return "\n".join(line for route in routes.values() for line in _quota_of(route, *measured[route.provider]))
    return t("ohx_usage")
