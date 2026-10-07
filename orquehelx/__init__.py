"""OrqueHelx: subagents across subscriptions on Hermes."""


def register(ctx):
    from .delegate import register as register_plugin
    register_plugin(ctx)
