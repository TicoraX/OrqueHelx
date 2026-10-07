"""Text the user reads (config errors, ``/ohx``): English by default, Spanish when Hermes runs in Spanish
(``display.language`` or ``HERMES_LANGUAGE``). Text the model reads is always English and lives next to its
code: it is not translated.
"""
from __future__ import annotations

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "no_routes": "no routes configured: define {key} in config.yaml",
        "both_keys": "set {new} or {old} in plugins.entries.orquehelx.settings, not both",
        "bad_policy": ("invalid policy {value!r} in plugins.entries.orquehelx.settings.{key}; "
                       "use one of: {options}"),
        "route_name": "route {route!r}: the name must be lowercase letters, digits or '-' (max. 32)",
        "route_map": "route {route!r}: expected a map with 'provider' or 'acp'",
        "route_fields": "route {route!r}: unknown fields {fields}",
        "route_model": "route {route!r}: 'model' must be non-empty text",
        "route_one_of": "route {route!r}: set exactly one of 'provider' or 'acp'",
        "route_acp": "route {route!r}: 'acp' must be a non-empty list of strings, e.g. [opencode, acp]",
        "route_provider": "route {route!r}: 'provider' must be non-empty text",
        "ohx_description": "OrqueHelx routes and the measured usage of each subscription",
        "ohx_usage": "usage: /ohx routes | /ohx quota",
        "no_data": "no data",
        "out_since": "{route}: out of quota since {since} (reset: {reset})",
        "measure_failed": "{route}: could not measure ({error})",
        "provider_silent": "{route}: no data (the provider does not report usage)",
        "no_windows": "{route}: no data (the measurement returned no windows)",
        "resets": ", resets {when}",
    },
    "es": {
        "no_routes": "no hay rutas configuradas: define {key} en config.yaml",
        "both_keys": "define {new} o {old} en plugins.entries.orquehelx.settings, no las dos",
        "bad_policy": ("política {value!r} inválida en plugins.entries.orquehelx.settings.{key}; "
                       "usa una de: {options}"),
        "route_name": "ruta {route!r}: el nombre debe ser minúsculas, dígitos o '-' (máx. 32)",
        "route_map": "ruta {route!r}: se esperaba un mapa con 'provider' o 'acp'",
        "route_fields": "ruta {route!r}: campos desconocidos {fields}",
        "route_model": "ruta {route!r}: 'model' debe ser texto no vacío",
        "route_one_of": "ruta {route!r}: define exactamente uno de 'provider' o 'acp'",
        "route_acp": "ruta {route!r}: 'acp' debe ser una lista no vacía de textos, p. ej. [opencode, acp]",
        "route_provider": "ruta {route!r}: 'provider' debe ser texto no vacío",
        "ohx_description": "Rutas de OrqueHelx y consumo medido de cada suscripción",
        "ohx_usage": "uso: /ohx routes | /ohx quota",
        "no_data": "sin dato",
        "out_since": "{route}: sin cuota desde {since} (reinicio: {reset})",
        "measure_failed": "{route}: no se pudo medir ({error})",
        "provider_silent": "{route}: sin dato (el proveedor no informa consumo)",
        "no_windows": "{route}: sin dato (la medición no devolvió ventanas)",
        "resets": ", reinicia {when}",
    },
}


def language() -> str:
    from agent.i18n import get_language
    return "es" if str(get_language() or "").lower().startswith("es") else "en"


def t(text_id: str, /, lang: str | None = None, **values) -> str:
    return TEXTS[lang or language()][text_id].format(**values)
