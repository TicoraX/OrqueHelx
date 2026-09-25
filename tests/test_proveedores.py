import sys

from orquehelx.proveedores import registrar_acp
from orquehelx.rutas import cargar, credenciales


def _ruta_acp(nombre="eco"):
    # Un comando que existe en cualquier maquina: el propio interprete.
    return cargar({nombre: {"acp": [sys.executable, "-c", "pass"]}})[nombre]


def test_ruta_acp_queda_registrada_como_proveedor_de_hermes():
    from providers import get_provider_profile

    registrar_acp(_ruta_acp())
    perfil = get_provider_profile("ohx-eco")
    assert perfil is not None
    assert perfil.process_command == sys.executable
    assert tuple(perfil.process_args) == ("-c", "pass")
    assert perfil.auth_type == "external_process"
    assert perfil.base_url == "acp://ohx-eco"


def test_delegacion_resuelve_el_comando_de_la_ruta():
    # El mismo camino que recorre delegate_task(credentials_cfg=...) antes de lanzar al hijo.
    from tools.delegate_tool_config import _resolve_delegation_credentials

    ruta = _ruta_acp("eco2")
    registrar_acp(ruta)
    creds = _resolve_delegation_credentials(credenciales(ruta), None)
    assert creds["provider"] == "ohx-eco2"
    assert creds["command"] == sys.executable
    assert list(creds["args"]) == ["-c", "pass"]


def test_registrar_dos_veces_no_duplica():
    from providers import list_providers

    ruta = _ruta_acp("eco3")
    registrar_acp(ruta)
    registrar_acp(ruta)
    assert [p.name for p in list_providers()].count("ohx-eco3") == 1
