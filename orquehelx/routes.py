"""Routes: the short name the model uses to pick which subscription a subagent runs on.

Read from ``plugins.entries.orquehelx.settings.routes`` in Hermes' config.yaml (``rutas`` in v0.1)::

    routes:
      claude: {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}
      codex:  {provider: openai-codex, model: gpt-5.5}

``provider`` is any provider Hermes knows, including the ones subscription and ACP plugins register.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CONFIG_KEY = "plugins.entries.orquehelx.settings.routes"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_FIELDS = {"provider", "model"}


class ConfigError(ValueError):
    """Unusable plugin config; the message names the key and what to fix."""


class RouteError(ConfigError):
    """Unusable route config; the message names the route and what to fix."""


@dataclass(frozen=True)
class Route:
    name: str
    provider: str
    model: str


def _route(name: str, value: object) -> Route:
    if not _NAME.match(name):
        raise RouteError(f"route {name!r}: the name must be lowercase letters, digits or '-' (max. 32)")
    if not isinstance(value, dict):
        raise RouteError(f"route {name!r}: expected a map with 'provider' and 'model'")
    if "acp" in value:
        raise RouteError(f"route {name!r}: 'acp' routes were removed in v0.3.0; install an ACP provider plugin "
                         "and use its provider name")
    extra = set(value) - _FIELDS
    if extra:
        raise RouteError(f"route {name!r}: unknown fields {sorted(extra)}")
    provider, model = value.get("provider"), value.get("model")
    if not isinstance(provider, str) or not provider.strip():
        raise RouteError(f"route {name!r}: 'provider' must be non-empty text")
    # Required: without it Hermes runs the child with the parent's model on the route's provider.
    if not isinstance(model, str) or not model.strip():
        raise RouteError(f"route {name!r}: 'model' must be non-empty text")
    return Route(name, provider.strip(), model.strip())


def load(config: object) -> dict[str, Route]:
    """Validate the whole config; fail loudly on the first unusable route."""
    if not isinstance(config, dict) or not config:
        raise RouteError(f"no routes configured: define {CONFIG_KEY} in config.yaml")
    return {str(name): _route(str(name), value) for name, value in config.items()}


def setting(get, key: str, legacy: str):
    """Value of ``key`` or of its v0.1 Spanish name ``legacy``. Both at once is an error: there is no way to
    tell which one the user meant."""
    new, old = get(key), get(legacy)
    if new is not None and old is not None:
        raise ConfigError(f"set {key} or {legacy} in plugins.entries.orquehelx.settings, not both")
    return old if new is None else new
