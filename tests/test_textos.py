import pytest

from orquehelx import textos


def test_los_dos_idiomas_tienen_las_mismas_claves():
    assert set(textos.TEXTOS["en"]) == set(textos.TEXTOS["es"])


@pytest.mark.parametrize("hermes, idioma", [
    ("es", "es"), ("es-419", "es"), ("ES_mx", "es"),
    ("en", "en"), ("fr", "en"), ("", "en"), (None, "en"),
])
def test_el_idioma_sigue_al_de_hermes_y_cae_en_ingles(monkeypatch, hermes, idioma):
    monkeypatch.setattr("agent.i18n.get_language", lambda: hermes)
    assert textos.idioma() == idioma


def test_t_formatea_en_el_idioma_activo_o_en_el_pedido(monkeypatch):
    monkeypatch.setattr("agent.i18n.get_language", lambda: "es")
    assert textos.t("no_data") == "sin dato"
    assert textos.t("no_data", lang="en") == "no data"
    assert "x.y" in textos.t("no_routes", key="x.y")
