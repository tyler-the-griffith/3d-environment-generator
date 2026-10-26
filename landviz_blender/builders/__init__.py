"""Builder registry: resolved record kind → function(rec, ctx) that adds geometry.

Mirror of landviz/elements on the core side. Builders run in layer order (terrain/structures
first so later elements can drape onto them). A builder receives a BuildContext with the
Ground, the scene JSON, and helpers.
"""
from __future__ import annotations

BUILDERS = {}


def builder(kind):
    def deco(fn):
        BUILDERS[kind] = fn
        return fn
    return deco


from . import surfaces, linear, structures, vegetation, features  # noqa: E402,F401
