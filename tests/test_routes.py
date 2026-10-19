import pytest

from orquehelx.routes import ConfigError, RouteError, load, setting


def test_provider_route_with_model():
    routes = load({"claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"}})
    assert routes["claude"].provider == "claude-subscription-directsdk-experimental"
    assert routes["claude"].model == "claude-haiku-4-5"


def test_a_route_without_model_fails_instead_of_borrowing_the_parent_model():
    # Without a model Hermes gives the child the parent's model, on the route's provider.
    with pytest.raises(RouteError, match="'agy'.*'model'"):
        load({"agy": {"provider": "antigravity-subscription-directsdk"}})


@pytest.mark.parametrize("config, piece", [
    ({"x": {}}, "'x'"),
    ({"x": {"provider": "", "model": "m"}}, "'x'"),
    ({"x": {"provider": "a", "model": "m", "extra": 1}}, "extra"),
    ({"x": {"provider": "a", "model": ""}}, "model"),
    ({"Bad Name": {"provider": "a", "model": "m"}}, "Bad Name"),
    ({"x": "claude"}, "'x'"),
])
def test_invalid_config_fails_naming_the_route(config, piece):
    with pytest.raises(RouteError, match=piece):
        load(config)


def test_an_acp_route_says_it_was_removed_and_what_to_use():
    with pytest.raises(RouteError, match="removed in v0.3.0.*provider plugin"):
        load({"opencode": {"acp": ["opencode", "acp"]}})


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
