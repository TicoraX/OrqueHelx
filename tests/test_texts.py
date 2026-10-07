import pytest

from orquehelx import texts


def test_both_languages_have_the_same_keys():
    assert set(texts.TEXTS["en"]) == set(texts.TEXTS["es"])


@pytest.mark.parametrize("hermes, lang", [
    ("es", "es"), ("es-419", "es"), ("ES_mx", "es"),
    ("en", "en"), ("fr", "en"), ("", "en"), (None, "en"),
])
def test_the_language_follows_hermes_and_falls_back_to_english(monkeypatch, hermes, lang):
    monkeypatch.setattr("agent.i18n.get_language", lambda: hermes)
    assert texts.language() == lang


def test_t_formats_in_the_active_or_the_requested_language(monkeypatch):
    monkeypatch.setattr("agent.i18n.get_language", lambda: "es")
    assert texts.t("no_data") == "sin dato"
    assert texts.t("no_data", lang="en") == "no data"
    assert "x.y" in texts.t("no_routes", key="x.y")
