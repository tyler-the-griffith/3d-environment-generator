# project.yaml reference

One file per property describes everything. Coordinates are **site coordinates**: X = east,
Y = north, origin wherever is convenient (a property corner). Bare numbers use
`project.units`; any length may carry its own unit: `12`, `"12 ft"`, `"6' 6\""`, `"18 in"`,
`"2 m"`.

Points may instead be authored in **plan pixels** (`frame: plan` on an element, or
`defaults.frame: plan` for all) once `plan.calibration` is set. That's the fastest way to
transcribe a landscaper's drawing: read pixel positions off the plan image.

## Top level

| key | meaning |
|---|---|
| `project` | `name`, `units` (`ft` \| `m` \| `in` \| `cm` \| `mm` \| `yd`) |
| `site.location` | `lat`, `lon`, `utc_offset` (hours), `north_offset_deg` (how far site +Y is rotated clockwise from true north). Drives the sun. `origin: [lat, lon]` geo-references the site: the lat/lon of site (0, 0). Needed for photo matches and LiDAR terrain (`lv photos --anchor` computes it from a straight-down plan photo). |
| `site.boundary` | property line polygon |
| `site.terrain` | `type: flat` · `type: graded` + `points: [[x, y, elevation], ...]` (smooth thin-plate surface through them) · `type: lidar` (measured bare earth from `lv lidar fetch`, placed via `site.location.origin`; optional `offset`) · `type: scan` (ground filtered from a `mesh` element with `role: terrain`; `filter_window` m, default 14). `margin` (default 25 m) and `resolution` (default 0.5 m) for the modeled ground. |
| `plan` | `image` (from `lv plan import`), `calibration` (below) |
| `defaults.frame` | `site` (default) or `plan` |
| `catalogs` | extra species YAML files (a `species.yaml` beside project.yaml is loaded automatically) |
| `elements` | the landscape, see below |
| `views` | cameras, see below |
| `render` | `quality` (`draft` \| `preview` \| `final` \| `web`), `resolution`, `year` (years after planting or `mature`), `season` or `date`, `time` (`"HH:MM"` local), `exposure` (default −3.5), `samples`, `seed` |
| `environment` | `sky: physical \| hdri`, `hdri` (path), `hdri_strength`, `haze`, `cloudiness` 0–1, `surroundings: lawn \| field \| none` |
| `variants` | named overrides, see below |

### Plan calibration

```yaml
plan:
  image: plan/plan-1.png
  calibration:
    points:                       # two or more; three+ also reports an RMS error
      - {px: [212, 1840], at: [0, 0]}
      - {px: [1630, 1838], at: [100, 0]}
# or, from the drawing's stated scale:
#   calibration: {drawing_scale: "1in = 10ft", dpi: 150, origin_px: [212, 1840], origin_at: [0, 0], rotation_deg: 0}
```

## Elements

Every element has `kind`, and optionally `id` (recommended: shows in previews and errors),
`tags`, `frame`, `note`, `enabled: false`.

| kind | required | optional |
|---|---|---|
| `lawn` | `polygon` | `smooth` (corner-rounding passes), `grass: {height, color, health 0-1}`. Defaults follow the season. Beds/paving/buildings/walls inside are cut out automatically. |
| `bed` | `polygon` | `surface` (`mulch_dark` `mulch` `mulch_red` `pine_straw` `river_rock` `gravel` `lava_rock` `soil`), `edging` (`spade` `steel` `stone` `brick` `paver` `block` `none`), `mound`, `smooth` |
| `paving` | `polygon` | `material` (`concrete` `stamped_concrete` `exposed_aggregate` `pavers` `pavers_gray` `brick` `flagstone` `bluestone` `gravel` `pea_gravel` `asphalt` `decking` `composite_decking` `stepping_stone`, or `tex:<name>` for an image texture set), `thickness`, `raise` (top above grade), `scale`, `joint_color` |
| `path` | `line`, `width` | everything `paving` takes; `smooth`; `style: stepping_stones` + `stone_size`, `gap` |
| `wall` | `line`, `height` | `thickness`, `material` (`block` `stone` `fieldstone` `brick` `concrete` `timber` `gabion`), `top: level \| follow`, `cap`, `closed` |
| `fence` | `line`, `height` | `style` (`privacy` `picket` `rail` `split_rail` `horizontal` `metal`), `color`, `post_spacing`, `closed` |
| `structure` | `footprint` + `wall_height`, **or** `wings` | `wings: [{footprint, wall_height, roof, base, walls, colors}]`: convex footprints (usually rectangles) that overlap to make L/T plans and cross-gables. `roof: {type, pitch: "8/12", ridge: auto \| x \| y \| <edge index>, overhang}`, types `gable`, `hip`, `half_hip` (+ `half_hip: 0.6`), `gambrel` / `mansard` (+ `lower_pitch`, `break`), `shed` (+ `low_edge`), `flat` (+ `parapet`), `custom` (+ `edges: {i: [[pitch°, offset_m, start_m], …] \| wall}`). `base` raises a wing's floor (split levels); `walls: false` gives an open porch/carport on corner posts. `floor_raise`, `colors: {walls, roof, trim, door, foundation}`, `windows` / `doors` / `garage_doors`: `[{wing, edge, at 0-1, width, height, sill, count, spacing}]` (edge *i* runs from footprint point *i* to *i+1*). |
| `deck` | `polygon`, `height` (above grade) | `railing: {style: balusters \| horizontal \| glass \| none, height, color, open_edges}`, `stairs: [{edge, at, width, rise, run}]`, `material`, `color`, `posts` |
| `mesh` | `file` (.obj .ply .glb .gltf .fbx .stl .blend) | `role: terrain` (a drone scan), `alignment: {points: [{model: [x,y,z], site: [x,y,z]}, ...]}` (3+ pairs) or `matrix` or `at` + `rotation` + `scale`, `drop_to_ground`, `keep` (polygons to keep from a scan, e.g. the house), `clip_out` (polygons to cut; defaults to the boundary for terrain scans) |
| `plant` | `species`, `at` | `height`/`spread` (override growth), `planted_height`/`planted_spread`, `rotation`, `variation` (±fraction, default 0.12), `sheared`, `existing` |
| `plant_group` | `species`, `polygon` | `spacing` (on-center, triangular grid) **or** `count` (scattered), `jitter` |
| `plant_row` | `species`, `line` | `spacing` **or** `count`, `smooth`, `sheared` (hedges) |
| `boulder` | `at` or `polygon` (+ `count`) | `size`, `color`, `bury` |
| `light` | `at` | `type: path \| uplight`, `color`, `power`, `aim`. Only visible in dusk/night renders. |

`species` accepts a catalog id (`acer_rubrum`) or any common/botanical name or alias
(`"Limelight Hydrangea"`). `./lv species <text>` searches.

### Ids, hand edits and `adjust`

Give elements an `id`; it names them in errors, previews and outputs, and it's how hand
edits find them. Elements without one get a positional id (`plant-12`) that breaks if you
reorder the list.

`edits.yaml` (written by `lv pull`, editable by hand) is merged over `project.yaml` on every
load. Base edits apply before variants (so every variant inherits them), and variant edits
after:

```yaml
elements:
  shade-tree: {at: [23.9, 17.7], frame: site, height: 34.4, spread: 25.3}
  patio: {polygon: [[33.4, 83.0], ...], frame: site}
  catmint-front: {adjust: {2: {at: [15.6, 19.4]}, 5: {remove: true}}}
variants:
  option-b:
    elements: {...}
```

`adjust` (on `plant_group`, `plant_row`, and `boulder` with `polygon`) edits individual
members of a generated layout by index: `at`, `height`, `spread` (`height` sets size for
boulders), or `remove: true`. Indices stay stable when members are removed. Explicit
`height`/`spread` are exact (no random variation).

## Photo matches

Re-create real photos as cameras so the model can be compared with them (`lv photomatch`).

```yaml
photo_matches:
  altitude_ref: {photo: app/files/<id>/ground.jpg, z: 4}   # a photo taken at site height z → GPS altitude calibration
  photos:
    - {name: overhead, file: app/files/<id>/DJI_0054.jpg, yaw: 0, pitch: -90, fov: 35.5}
    - {name: street, file: app/files/<id>/DJI_0057.jpg, yaw: 40, pitch: -25}
```

Position comes from GPS via `site.location.origin` (or give `position: [x, y, z]`). Heading `yaw`
(compass degrees), tilt `pitch` (−90 = straight down) and `roll` come from DJI gimbal metadata
when the file has it, otherwise from here. `fov` (horizontal degrees) overrides the lens data,
which is wrong on digitally zoomed or cropped exports. `lv photos <files> --project P` prints
each photo's metadata and site position.

## Views

```yaml
views:
  - {name: front, preset: eye, position: [40, -28], target: [52, 50], lens: 24}   # eye height 1.65 m unless z given
  - {name: aerial, preset: aerial, azimuth: 205, elevation: 32, zoom: 1.0}        # azimuth = where the camera sits, clockwise from +Y
  - {name: plan, preset: top}                                                      # orthographic, compare with the drawing
  - {name: orbit, preset: orbit, seconds: 20, radius: 150, height: 70}             # animation (lv animate)
  - {name: walk, preset: flythrough, path: [[..], [..], ...], seconds: 30}         # animation along a path
```

## Variants

```yaml
variants:
  year1:  {render: {year: 1}}
  fall:   {render: {season: fall}}
  option-b: {exclude_tags: [option-a]}           # drop elements tagged option-a
  bigger-patio: {elements_override: {patio: {polygon: [...]}}}
```

Any key can also be overridden from the command line: `--set render.year=10`.

## Species catalog

`landviz/catalog/species/builtin.yaml` documents every field. A project's `species.yaml`
adds or overrides entries by id. Each species has a generator `form` (crown shape), mature
`height`/`spread`, `years_to_mature`, seasonal `foliage` colors, `flowers` (color + months),
`winter` behavior, and optionally `asset:` (a model file to use instead of the generator).
