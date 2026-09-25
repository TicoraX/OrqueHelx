"""OrqueHelx: subagentes entre suscripciones sobre Hermes."""


def register(ctx):
    from .delegar import registrar
    registrar(ctx)
