"""Una ruta ``acp`` se vuelve proveedor de Hermes sin que el usuario escriba un plugin.

Mismo contrato que ``plugins/model-providers/copilot-acp`` de Hermes: el perfil lanza el CLI por
stdio y el CLI guarda su propia sesion. El cliente ACP de Hermes se llama ``CopilotACPClient`` pero
es generico: recibe el comando y los argumentos.
"""
from __future__ import annotations

from .rutas import Ruta


def registrar_acp(ruta: Ruta) -> None:
    """Registra (o reemplaza) el proveedor ``ohx-<ruta>``; last-writer-wins en Hermes."""
    from providers import register_provider
    from providers.base import ProviderProfile

    class PerfilACP(ProviderProfile):
        def create_client(self, **client_kwargs):
            from agent.copilot_acp_client import CopilotACPClient
            return CopilotACPClient(**client_kwargs)

    comando, *args = ruta.acp
    register_provider(PerfilACP(
        name=ruta.proveedor, api_mode="chat_completions", env_vars=(),
        base_url=f"acp://{ruta.proveedor}", auth_type="external_process",
        process_command=comando, process_args=tuple(args),
    ))
