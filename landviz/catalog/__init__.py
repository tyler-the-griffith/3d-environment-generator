"""Plant species catalog + growth/season model.

A catalog is a YAML file mapping species ids to records (see species/builtin.yaml for
the schema by example). Projects stack catalogs: later ones override earlier ones by id,
so a project-local species.yaml can tweak a builtin plant or add a nursery-specific
cultivar without touching the framework.

The output of this module is a *resolved plant state* — concrete height, spread, colors
and leaf/flower density for one plant at one (year, month) — which is all the Blender
side ever sees. Blender never reads catalogs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import units

BUILTIN_DIR = Path(__file__).parent / "species"

# Generator forms the Blender side knows how to build (landviz_blender/plantgen).
KNOWN_FORMS = {
    "tree_round", "tree_oval", "tree_vase", "tree_spreading", "tree_weeping", "tree_columnar",
    "tree_multistem", "conifer_conical", "conifer_columnar",
    "shrub_round", "shrub_upright", "shrub_spreading", "shrub_mound", "hedge",
    "grass_clump", "grass_fountain", "perennial_mound", "perennial_spike", "groundcover",
}
CATEGORIES = {"tree", "shrub", "grass", "perennial", "groundcover", "vine", "annual"}

# Size at planting as a fraction of mature (height, spread) when the plan doesn't say.
_PLANTED_FRACTION = {
    "tree": (0.30, 0.20), "shrub": (0.35, 0.35), "grass": (0.5, 0.4),
    "perennial": (0.5, 0.4), "groundcover": (0.6, 0.3), "vine": (0.3, 0.3), "annual": (1, 1),
}
_SEASON_OF_MONTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                    6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall"}
SEASON_MONTH = {"spring": 5, "summer": 7, "fall": 10, "winter": 1}


@dataclass
class Species:
    id: str
    common: str
    botanical: str
    category: str
    form: str
    height: float  # mature, meters
    spread: float  # mature, meters
    years_to_mature: float = 10
    evergreen: bool = False
    foliage: dict = field(default_factory=dict)  # season -> hex
    flowers: dict | None = None  # {color, months:[...], density}
    winter: str = "bare"  # bare | evergreen | dormant | persistent
    bark: str = "#5a4a3c"
    leaf_size: str = "medium"  # tiny | small | medium | large | needle | blade
    generator: dict = field(default_factory=dict)  # free-form overrides for the Blender generator
    asset: str | None = None  # optional model file to use instead of the procedural generator
    notes: str = ""

    def season_name(self, month: int, southern: bool = False) -> str:
        if southern:
            month = (month + 5) % 12 + 1
        return _SEASON_OF_MONTH[month]

    def size_at(self, years: float, planted_height: float | None = None,
                planted_spread: float | None = None) -> tuple[float, float]:
        fh, fs = _PLANTED_FRACTION.get(self.category, (0.4, 0.4))
        h0 = planted_height if planted_height is not None else self.height * fh
        s0 = planted_spread if planted_spread is not None else self.spread * fs
        if years is None or years == "mature":
            return self.height, self.spread
        k = 3.0 / max(self.years_to_mature, 0.5)  # ~95% of mature at years_to_mature
        g = 1 - math.exp(-k * float(years))
        return h0 + (self.height - h0) * g, s0 + (self.spread - s0) * g

    def state(self, month: int, southern: bool = False) -> dict:
        """Appearance for a month: leaf density, foliage color, flower color/density, dieback."""
        season = self.season_name(month, southern)
        fol = self.foliage
        color = fol.get(season) or fol.get("summer") or "#4a6b2f"
        leaf_density, height_scale = 1.0, 1.0
        if season == "winter" and not self.evergreen:
            if self.winter == "bare":
                leaf_density = 0.0
            elif self.winter == "dormant":  # herbaceous: cut back / died to the ground
                leaf_density, height_scale = 0.35, 0.25
                color = fol.get("winter") or "#8a7a5a"
            elif self.winter == "persistent":  # grasses left standing, tan
                color = fol.get("winter") or "#c2a878"
        if season == "spring" and not self.evergreen and self.category in ("perennial", "grass"):
            height_scale = 0.6  # still coming up
        flower_color, flower_density = None, 0.0
        if self.flowers and month in self.flowers.get("months", []):
            flower_color = self.flowers.get("color", "#ffffff")
            flower_density = float(self.flowers.get("density", 0.5))
        return {
            "season": season, "foliage_color": color, "leaf_density": leaf_density,
            "height_scale": height_scale, "flower_color": flower_color,
            "flower_density": flower_density,
        }


class CatalogError(ValueError):
    pass


def _parse_species(sid: str, rec: dict, source: str) -> Species:
    try:
        cat = rec.get("category", "shrub")
        if cat not in CATEGORIES:
            raise CatalogError(f"{source}:{sid}: unknown category {cat!r}")
        foliage = rec.get("foliage", {})
        if isinstance(foliage, str):
            foliage = {"summer": foliage}
        if "fall_color" in rec:
            foliage = {**foliage, "fall": rec["fall_color"]}
        evergreen = bool(rec.get("evergreen", False))
        return Species(
            id=sid,
            common=rec.get("common", sid),
            botanical=rec.get("botanical", ""),
            category=cat,
            form=rec.get("form", "shrub_round"),
            height=units.length(rec["height"], "ft"),
            spread=units.length(rec.get("spread", rec["height"]), "ft"),
            years_to_mature=float(rec.get("years_to_mature", 10 if cat == "tree" else 4)),
            evergreen=evergreen,
            foliage=foliage,
            flowers=rec.get("flowers"),
            winter=rec.get("winter", "evergreen" if evergreen else ("dormant" if cat == "perennial" else "bare")),
            bark=rec.get("bark", "#5a4a3c"),
            leaf_size=rec.get("leaf_size", "medium"),
            generator=rec.get("generator", {}),
            asset=rec.get("asset"),
            notes=rec.get("notes", ""),
        )
    except KeyError as e:
        raise CatalogError(f"{source}:{sid}: missing field {e}") from None


class Catalog:
    def __init__(self):
        self.species: dict[str, Species] = {}
        self.aliases: dict[str, str] = {}
        self.sources: list[str] = []

    def load(self, path: Path):
        data = yaml.safe_load(Path(path).read_text()) or {}
        for sid, rec in data.items():
            sp = _parse_species(sid, rec or {}, str(path))
            self.species[sid] = sp
            for alias in (rec or {}).get("aliases", []):
                self.aliases[_norm(alias)] = sid
            self.aliases[_norm(sp.common)] = sid
            if sp.botanical:
                self.aliases[_norm(sp.botanical)] = sid
        self.sources.append(str(path))
        return self

    def get(self, key: str) -> Species | None:
        if key in self.species:
            return self.species[key]
        sid = self.aliases.get(_norm(key))
        return self.species.get(sid) if sid else None

    def search(self, text: str) -> list[Species]:
        t = _norm(text)
        return [s for s in self.species.values()
                if t in _norm(s.id) or t in _norm(s.common) or t in _norm(s.botanical)]

    @classmethod
    def builtin(cls) -> "Catalog":
        c = cls()
        for p in sorted(BUILTIN_DIR.glob("*.yaml")):
            c.load(p)
        return c


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())
