"""An ``acp`` route becomes a Hermes provider without the user writing a plugin.

Same contract as Hermes' ``plugins/model-providers/copilot-acp``: the profile launches the CLI over stdio and
the CLI keeps its own session. Hermes' ACP client is called ``CopilotACPClient`` but it is generic: it takes
the command and its arguments.
"""
from __future__ import annotations

from .routes import Route


def register_acp(route: Route) -> None:
    """Register (or replace) the ``ohx-<route>`` provider; last writer wins in Hermes."""
    from providers import register_provider
    from providers.base import ProviderProfile

    class ACPProfile(ProviderProfile):
        def create_client(self, **client_kwargs):
            from agent.copilot_acp_client import CopilotACPClient
            return CopilotACPClient(**client_kwargs)

    command, *args = route.acp
    register_provider(ACPProfile(
        name=route.provider, api_mode="chat_completions", env_vars=(),
        base_url=f"acp://{route.provider}", auth_type="external_process",
        process_command=command, process_args=tuple(args),
    ))
