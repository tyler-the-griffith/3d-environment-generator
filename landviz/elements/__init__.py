"""Element type registry.

Each landscape element kind ("lawn", "bed", "plant_group", ...) is one class that knows how to
  1. resolve its authored YAML into one or more *resolved records* (site meters, all defaults
     filled, plants expanded) — the only thing the Blender side ever sees, and
  2. draw itself into the 2D SVG preview used to check the model against the landscaper's plan.

The Blender side has a matching builder registry keyed on the resolved record's `kind`
(landviz_blender/builders). Adding a new element type = one resolver class here (decorated
with @register) + one builder there. Resolvers may emit records of a different kind than
their own (e.g. `path` emits `paving`, `plant_group` emits `plant`), so many authoring
conveniences need no Blender code at all.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..resolve import Context

REGISTRY: dict[str, "ElementType"] = {}


def register(cls):
    REGISTRY[cls.kind] = cls()
    return cls


class ElementType:
    kind: str = ""
    # Draw order in previews and z-stacking order in 3D (higher = on top).
    layer: int = 0
    # Authored keys this type accepts beyond the common ones (id, kind, tags, frame, note).
    keys: set[str] = set()
    required: set[str] = set()

    COMMON = {"id", "kind", "tags", "frame", "note", "label", "enabled"}

    def check_keys(self, raw: dict, ctx: "Context"):
        unknown = set(raw) - self.keys - self.COMMON
        if unknown:
            ctx.warn(f"unknown keys {sorted(unknown)} (ignored)")
        missing = self.required - set(raw)
        if missing:
            ctx.error(f"missing required keys {sorted(missing)}")
            return False
        return True

    def resolve(self, raw: dict, ctx: "Context") -> list[dict]:
        raise NotImplementedError

    def preview(self, rec: dict, svg) -> None:
        """Draw a resolved record (of this kind) into the preview."""


def get(kind: str) -> ElementType | None:
    return REGISTRY.get(kind)


# Importing the modules registers their types.
from . import surfaces, linear, structures, plants, features  # noqa: E402,F401
