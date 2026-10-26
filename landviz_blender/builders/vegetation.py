"""Plants: build one prototype per species variant, then place scaled instances."""
from __future__ import annotations

import statistics

from .. import plantgen
from ..util import log
from . import builder

VARIANTS = 3  # distinct prototypes per species so repeated plants don't look cloned


def build_all_plants(ctx):
    plants = [r for r in ctx.scene["elements"] if r["kind"] == "plant"]
    by_species: dict[str, list] = {}
    for p in plants:
        by_species.setdefault(p["species"], []).append(p)
    col = ctx.col("Plants")
    for sid, group in by_species.items():
        sp = ctx.scene["species"][sid]
        H = statistics.median(p["height"] for p in group)
        S = statistics.median(p["spread"] for p in group)
        st = group[0]["state"]
        nvar = min(VARIANTS, len(group))
        protos = []
        for v in range(nvar):
            if sp.get("asset"):
                protos.append(plantgen.asset_prototype(sid, sp["asset"], H, S))
                continue
            spec = plantgen.PlantSpec(
                species_id=sid, form=sp["form"], height=H, spread=S, leaf_size=sp["leaf_size"],
                foliage=st["foliage_color"], bark=sp["bark"], leaf_density=st["leaf_density"],
                flower_color=st["flower_color"], flower_density=st["flower_density"],
                sheared=group[0].get("sheared", False), generator=sp.get("generator") or {},
                seed=ctx.render["seed"] + v, quality_mult=ctx.render["leaf_mult"], category=sp["category"],
                impostor=ctx.render["quality"] == "web")
            protos.append(plantgen.prototype(spec, v))
        for i, p in enumerate(group):
            z = ctx.ground.z(p["x"], p["y"])
            inst = plantgen.place(protos[p["seed"] % nvar], p["id"], p["x"], p["y"], z,
                                  p["height"] / H, p["spread"] / S, p["rotation"], col)
            ctx.rec_objects[p["id"]] = (p, inst, [inst])
        log(f"plants: {sp['common']} ×{len(group)} ({nvar} variant{'s' if nvar > 1 else ''}, "
            f"~{H:.1f} m tall)")


@builder("plant")
def plant(rec, ctx):
    pass  # handled in bulk by build_all_plants (prototype sharing needs the whole group)
