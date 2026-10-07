import sys

from orquehelx.acp import register_acp
from orquehelx.routes import credentials, load


def _acp_route(name="echo"):
    # A command that exists on any machine: the interpreter itself.
    return load({name: {"acp": [sys.executable, "-c", "pass"]}})[name]


def test_an_acp_route_is_registered_as_a_hermes_provider():
    from providers import get_provider_profile

    register_acp(_acp_route())
    profile = get_provider_profile("ohx-echo")
    assert profile is not None
    assert profile.process_command == sys.executable
    assert tuple(profile.process_args) == ("-c", "pass")
    assert profile.auth_type == "external_process"
    assert profile.base_url == "acp://ohx-echo"


def test_delegation_resolves_the_route_command():
    # The same path delegate_task(credentials_cfg=...) takes before launching the child.
    from tools.delegate_tool_config import _resolve_delegation_credentials

    route = _acp_route("echo2")
    register_acp(route)
    creds = _resolve_delegation_credentials(credentials(route), None)
    assert creds["provider"] == "ohx-echo2"
    assert creds["command"] == sys.executable
    assert list(creds["args"]) == ["-c", "pass"]


def test_registering_twice_does_not_duplicate():
    from providers import list_providers

    route = _acp_route("echo3")
    register_acp(route)
    register_acp(route)
    assert [p.name for p in list_providers()].count("ohx-echo3") == 1
