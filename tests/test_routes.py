import pytest

from orquehelx.routes import ConfigError, RouteError, credentials, load, setting


def test_provider_route_with_model():
    routes = load({"claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"}})
    assert routes["claude"].provider == "claude-subscription-directsdk-experimental"
    assert routes["claude"].model == "claude-haiku-4-5"
    assert routes["claude"].acp is None


def test_an_acp_route_gets_its_own_provider():
    routes = load({"opencode": {"acp": ["opencode", "acp"]}})
    assert routes["opencode"].acp == ("opencode", "acp")
    assert routes["opencode"].provider == "ohx-opencode"


def test_credentials_turn_fallback_off():
    # Never an automatic provider switch; [] turns off Hermes' native chain (#65038).
    route = load({"agy": {"provider": "antigravity-subscription-directsdk", "model": "flash"}})["agy"]
    assert credentials(route) == {
        "provider": "antigravity-subscription-directsdk", "model": "flash", "fallback_providers": [],
    }
    no_model = load({"opencode": {"acp": ["opencode", "acp"]}})["opencode"]
    assert credentials(no_model) == {"provider": "ohx-opencode", "fallback_providers": []}


@pytest.mark.parametrize("config, piece", [
    ({"x": {}}, "'x'"),
    ({"x": {"provider": "a", "acp": ["b"]}}, "'x'"),
    ({"x": {"acp": []}}, "'x'"),
    ({"x": {"acp": ["opencode", 3]}}, "'x'"),
    ({"x": {"provider": ""}}, "'x'"),
    ({"x": {"provider": "a", "extra": 1}}, "extra"),
    ({"Bad Name": {"provider": "a"}}, "Bad Name"),
    ({"x": "claude"}, "'x'"),
])
def test_invalid_config_fails_naming_the_route(config, piece):
    with pytest.raises(RouteError, match=piece):
        load(config)


def test_no_routes_is_an_explicit_error():
    for config in (None, {}):
        with pytest.raises(RouteError, match="plugins.entries.orquehelx.settings.routes"):
            load(config)


def test_english_keys_win_and_the_v01_ones_still_work():
    assert setting({"routes": 1}.get, "routes", "rutas") == 1
    assert setting({"rutas": 2}.get, "routes", "rutas") == 2
    assert setting({}.get, "routes", "rutas") is None


def test_both_keys_at_once_is_an_error_naming_both():
    with pytest.raises(ConfigError, match="routes.*rutas"):
        setting({"routes": {}, "rutas": {}}.get, "routes", "rutas")
