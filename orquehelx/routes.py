"""Routes: the short name the model uses to pick which subscription a subagent runs on.

Read from ``plugins.entries.orquehelx.settings.routes`` in Hermes' config.yaml (``rutas`` in v0.1)::

    routes:
      claude:   {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}
      opencode: {acp: [opencode, acp]}        # any CLI that speaks ACP

An ``acp`` route needs no plugin of its own: OrqueHelx registers an ``ohx-<route>`` provider that launches
that command (see ``acp.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .texts import t

CONFIG_KEY = "plugins.entries.orquehelx.settings.routes"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_FIELDS = {"provider", "model", "acp"}


class ConfigError(ValueError):
    """Unusable plugin config; the message names the key and what to fix."""


class RouteError(ConfigError):
    """Unusable route config; the message names the route and what to fix."""


@dataclass(frozen=True)
class Route:
    name: str
    provider: str
    model: str | None = None
    acp: tuple[str, ...] | None = None


def _route(name: str, value: object) -> Route:
    if not _NAME.match(name):
        raise RouteError(t("route_name", route=name))
    if not isinstance(value, dict):
        raise RouteError(t("route_map", route=name))
    extra = set(value) - _FIELDS
    if extra:
        raise RouteError(t("route_fields", route=name, fields=sorted(extra)))
    model = value.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise RouteError(t("route_model", route=name))
    provider, acp = value.get("provider"), value.get("acp")
    if (provider is None) == (acp is None):
        raise RouteError(t("route_one_of", route=name))
    if acp is not None:
        if not isinstance(acp, list) or not acp or not all(isinstance(p, str) and p.strip() for p in acp):
            raise RouteError(t("route_acp", route=name))
        return Route(name, f"ohx-{name}", model, tuple(acp))
    if not isinstance(provider, str) or not provider.strip():
        raise RouteError(t("route_provider", route=name))
    return Route(name, provider.strip(), model)


def load(config: object) -> dict[str, Route]:
    """Validate the whole config; fail loudly on the first unusable route."""
    if not isinstance(config, dict) or not config:
        raise RouteError(t("no_routes", key=CONFIG_KEY))
    return {str(name): _route(str(name), value) for name, value in config.items()}


def setting(get, key: str, legacy: str):
    """Value of ``key`` (English, v0.2) or of its Spanish ``legacy`` name (v0.1). Both at once is an error:
    there is no way to tell which one the user meant."""
    new, old = get(key), get(legacy)
    if new is not None and old is not None:
        raise ConfigError(t("both_keys", new=key, old=legacy))
    return old if new is None else new


def credentials(route: Route) -> dict:
    """``credentials_cfg`` for ``delegate_task``: the route pins the provider and there is no fallback."""
    cfg = {"provider": route.provider, "fallback_providers": []}
    if route.model:
        cfg["model"] = route.model
    return cfg
