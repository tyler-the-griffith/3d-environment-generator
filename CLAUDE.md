# 3D Environment Generator: architecture and working notes

Landscape plan (+ optional drone scan) → Blender scene → renders / video / web model.
User-facing docs: README.md. Spec reference: docs/SPEC.md.

Two layers:
- `app/`: the desktop app (local Python server + web UI in a native/app-mode window).
  Projects, child variants, inputs and rounds, AI providers, output types, settings.
- `landviz/` + `landviz_blender/`: the deterministic framework (`./lv` CLI). The app drives
  it only through `./lv` subprocesses and by importing `landviz` for validation. It never
  builds geometry itself.

## App (app/)

- `server.py`: stdlib `ThreadingHTTPServer` on 127.0.0.1 with a per-launch `SameSite=Strict`
  session cookie. JSON API under `/api`, project files under `/files/<path under projects
  root>` (Range-capable for video), and SSE at `/api/events` (`job`, `job_log`,
  `node_changed`, `tree_changed`, `settings`). Node ids are `project` or `project/variant`,
  URL-encoded (`%2F`) in routes. **Python edits need a server restart**; restarting kills
  running jobs, so wait for them.
- `store.py`: node model. Each node keeps `app/` beside its design file: `node.json`,
  `inputs.json` (items + rounds; `round != null` means frozen), `outputs.json` (selected
  types + custom requests; never frozen), `files/`, `design/vN.yaml` + `versions.json`,
  `rounds/N/{prompt.md,agent.log,result.json}`. A child variant is
  `variants/<name>/variant.yaml`, an overlay with `parent:`, resolved by
  `landviz.project.variant_chain`. A new variant copies the parent's inputs as editable items
  with an `origin`.
- `ai/`: `start_round` freezes the new items and pending custom requests; `build_brief`
  (`instructions.md` + task) tells the agent which files it may write, which exact commands
  to run, and to write `result.json`. Providers: claude_code (`claude -p`, stream-json, a
  narrow `--allowedTools` list; the agent must run `./lv validate|check` exactly as written,
  since `cd …;` chains were blocked in testing), codex, gemini, anthropic_api
  (`api_agent.py`: official SDK tool runner, restricted tools, input images/PDFs attached),
  and custom. After the agent (`finish_round`): validate, snapshot a design version, render
  the plan check, record the verdicts on custom requests.
- Ask before building: the brief lets the agent write `rounds/N/questions.json`
  (`{id, text, why, default}`) and stop; `run_round` then parks the round as
  `awaiting_answers` (no new round can start). The input page shows a form; each answer is
  text or "use your best judgement" (= the agent's stated `default`). `answer_round` records the
  answers as a frozen input of the *same* round (`answers_to`), moves them to
  `asked_questions`, and continues: Claude Code resumes its session (`--resume`), other
  providers get the brief plus the answers. Questions in `result.json` (raised while
  building) get the same form; answering them starts the next round.
- Rounds record `agent_pid` and the Claude Code `session_id` (from the stream-json init
  event; `session_id_for` falls back to scanning `~/.claude/projects/<repo>/` transcripts for
  the round's prompt path). "Continue in Claude Code" opens `claude --resume <id>` in a
  terminal (`$TERMINAL` / xdg-terminal-exec on Linux). The agent runs in its own session and
  outlives the app, so `recover_rounds` at server start waits for a live agent, finishes a
  round whose `result.json` exists, or marks it `interrupted` (retryable). A design changed
  outside the app is versioned as `external` the next time the node is opened.
- `generate.py`: `OUTPUT_TYPES` and how each maps to `./lv` commands (`--output-type`
  tags each output in the manifest; `--extra-view` supplies orbit/pano views the design
  lacks). Types that are already up to date (current fingerprints) are skipped.
  `list_outputs` groups the manifest by type for the output page. `standalone_viewer` embeds
  the .glb so a downloaded walkthrough opens from `file://`.
- `__main__.py` / `install.py`: launcher and app-menu entry. One instance per user:
  `instance.json` in the config dir holds the running URL, and a second launch only opens
  another window on it. Chrome app windows share one profile (`window-profile`), so the server
  lives until the last window closes. On Wayland, Chrome ignores `--class`, so the window
  opens at `/envgen` to get the stable app id `chrome-127.0.0.1__envgen-Default` (the
  `.desktop` file's `StartupWMClass`). The menu icon `branding/app-icon.png` is refreshed from
  `logo.png` (or `default-icon.png`) on every launch.
- `jobs.py`: three lanes (gpu: anything Blender, ai: rounds, net: publishing), subprocess per
  job, cancellable.
- `publish.py`: builds a static site from a node's outputs into `renders/_site/<state>/`,
  styled with the user's theme tokens (sent by the client, validated before going into CSS),
  stills as web-sized JPEGs, the walkthrough copied with a themed overlay. GitHub Pages via the
  `gh` CLI: public repo, a separate git checkout in `renders/_site/.deploy-<state>/`, push with a
  one-shot `gh auth git-credential` helper and a noreply identity (no change to the user's git
  config). The UI requires an explicit "this is public" confirmation (`confirm_public`).
- `config.py`: brand (`brand.json`, `branding/logo.png`) and per-user settings in the OS
  config dir (not the repo). `appearance.overrides` is replaced, not merged, on save.
- `web/`: vanilla ES modules, no build step. `main.js` (state, routing
  `#/n/<id>/<input|output>`, right-click menu), `inputs.js`, `outputs.js`, `outspec.js`
  (the one "Outputs to make" component used on both pages), `publish.js` (the web page
  widget), `settings.js`, `theme.js` (palettes → CSS variables), `ui.js`. Palettes are
  generated: `tools/make_palettes.py` turns Coolors palettes into light/dark token sets in
  OKLab and nudges accent/status lightness until every text pair passes WCAG AA; edit the
  anchors there and re-run it rather than hand-editing `palettes.js`. `h()` skips null children, but `replaceChildren()`
  doesn't, so filter first. While the user is typing in a pane, refreshes re-render only the
  nav and topbar.

## The user's priority: one model, every view

The user cares most about consistency: every output of a design state (stills, videos, web
walkthrough, plan check) must be taken from one 3D model, so positions and rough sizes always
agree. Detail can differ between tiers; layout can't. Don't add an output path that draws
from anything other than the saved model. (`lv preview` is the one exception: a labeled 2D
diagnostic drawn from the same resolved layout, and labeled as diagnostic.)

```
project.yaml + edits.yaml → resolved scene → saved model derived/models/<fp>.blend → outputs
```

- `landviz/models.py` holds the fingerprint (resolved site/elements/species/growth +
  detail-tier keys + Blender-side code hash + Blender version + imported-file stamps).
  Environment, views and samples are deliberately excluded: a dusk render reuses the noon
  model. Models are keyed by fingerprint, so states that resolve identically share one file.
- Build jobs only build and save (no cameras or world). Output jobs open the saved model
  (`blender -b model.blend -P runner.py`) and check `scene["lv_fingerprint"]` matches.
- A model whose file changed after it was built (hand-edited) is never rendered from.
  `lv pull` diffs every object against its as-built transform (`lv_m0`, stamped by
  `scene.tag_records`) and writes patches to `edits.yaml`; base edits are merged before
  variants, variant edits after. Group members are addressed by original index
  (`adjust: {i: …}`; record ids `group#i` keep their index when siblings are removed).
  Elements without an `id` get a positional one assigned at load, before variants filter.
- Primary objects have their pivot moved to their own center, and an element's parts are
  parented to it, so hand rotation and moves behave naturally in the Blender GUI.
- Animation frames live in `derived/frames/<view>_<hash(fp, view, render, env)>`, so a
  resumed render can never splice frames from two models.
- Outputs: `renders/<state>/…`, recorded in `renders/manifest.json` with their fingerprint.
  The gallery recomputes today's fingerprint per state and flags mismatches as stale. State
  names are filesystem-safe on Windows (this folder may be shared with the VM).

## Core / Blender split

Two halves joined by a single JSON contract:

```
project.yaml ──landviz (core, system python)──▶ resolved scene JSON ──landviz_blender (inside Blender)──▶ model
```

- **Core** (`landviz/`): stdlib + numpy + PyYAML only (no shapely/scipy, deliberately: it
  must run anywhere and be unit-testable without Blender). It owns all *meaning*: units,
  plan calibration, plant catalog, growth and seasons, plant layout, sun position, terrain
  height grids, validation.
- **Blender side** (`landviz_blender/`): owns all *geometry and appearance*. It reads only
  the resolved JSON, never project.yaml or catalogs. Records arrive in site meters, sorted
  by `layer`, with every default filled.

Keep it that way. A new spec feature is resolved in core into concrete numbers; Blender just
draws them.

## Accuracy tools (core)

- `roofs.py`: each roof is the lower envelope of per-edge planes over a convex wing footprint
  (hip, gable, half_hip, gambrel, mansard, shed, flat, custom are just different plane sets),
  computed in core and passed to Blender as faces plus wall top profiles. Non-convex wings fall
  back to a bounding-rectangle roof with a warning. Houses are overlapping wings.
- `photos.py`: EXIF/DJI-XMP reader, FOV from 35 mm-equivalent (diagonal convention),
  lat/lon ↔ site (`site.location.origin` + `north_offset_deg`). Some exports drop the E/W refs;
  the site's longitude sign settles it. Digitally zoomed exports report the unzoomed focal
  length, so `fov` overrides it, and `lv photos --anchor` prints a scale check.
- `lv photomatch`: `scene["photo_views"]` (resolve) → Blender cameras built from yaw (site-
  relative), pitch and roll via Rz(-yaw)·Rx(90+pitch)·Rz(roll) → 2×2 sheets. Photo views aren't
  in the fingerprint.
- `lidar.py`: bare-earth DEM from the 3DEP `getSamples` endpoint (1000 points per request,
  parallel); optional point cloud from USGS EPT on AWS (laspy+lazrs from `./launch --setup` into
  `.venv`, which `launch` and `lv` prefer). DSM is aligned to the DEM by the median offset on
  ground returns. Terrain type `lidar` samples the DEM relative to the elevation at the site
  origin. Address geocoding uses the US Census geocoder.

## Extension points (registries)

| add a… | core | Blender |
|---|---|---|
| element kind | class in `landviz/elements/*.py` with `@register` (`resolve` + `preview`) | function in `landviz_blender/builders/*.py` with `@builder("kind")`. Resolvers may emit an existing kind instead (e.g. `path` → `paving`, `plant_group` → `plant`), so no Blender code is needed. |
| material | name list in the element's validator | `@register("name")` in `landviz_blender/materials.py`; or drop a PBR set in `assets/textures/<name>/` and use `tex:<name>` |
| plant species | entry in `landviz/catalog/species/*.yaml` or a project `species.yaml` | nothing, unless it needs a new form |
| plant form | add to `KNOWN_FORMS` | `@form("name")` in `landviz_blender/plantgen/` (+ envelope profile) |
| photogrammetry engine | subclass `Engine` in `landviz/reconstruct/` | nothing |
| quality preset | `QUALITY_PRESETS` in `landviz/resolve.py` | nothing |

## Non-obvious decisions (each one cost a debugging round)

- **Plants are collection instances of per-species prototypes** (3 variants each), scaled
  per plant. Memory stays flat with hundreds of plants.
- **Scattering uses Geometry Nodes, not particle systems.** A hidden particle emitter
  inside a collection instance also hides its particles in Cycles (so do the ray-visibility
  flags). A GN modifier that outputs only instances makes the emitter shell vanish by
  construction. See `landviz_blender/scatter.py`.
- **Leaves are twig sprays, not botanical leaves.** Real 2–7 cm leaves at real counts are
  invisible or unaffordable at landscape distance. Spray size scales with the plant
  (`spray_size`), and tiny leaves and needles get a visual size floor. Instanced sprays have
  +Z = outward, and the GN rotation aligns +Z to the canopy normal.
- **Crowns are overlapping "lobes"** filling a form-specific envelope (heavy overlap, about
  4× volume; fewer looked like pom-poms). Branches run from trunk tops to each lobe.
- **Sky orientation verified empirically:** `ShaderNodeTexSky.sun_rotation` 0 = toward
  site +Y, 90° = +X (a clockwise compass bearing). Default exposure −3.5 with AgX for the
  physical `MULTIPLE_SCATTERING` sky.
- **Web export uses canopy-shell plants** (`impostor`; the lobe hull with a flat material)
  instead of leaves. Realizing about 1M leaf instances into a glTF ran for 10+ minutes.
  The web build takes seconds and produces about 12 MB.
- **Scan terrain:** the scan is imported unclipped, a bare-earth grid is filtered from it
  (top-down DSM → morphological opening → diffusion fill), and only then is the yard
  clipped away (keeping `keep` polygons such as the house). Draping onto the raw scan would
  put new plants on roofs.
- **Lawn holes are exact:** lawn meshes are a constrained Delaunay triangulation
  (`mathutils.geometry.delaunay_2d_cdt`) including the outlines of beds, paving and
  buildings as constraint edges, then triangles inside them are dropped.
- Seeds come from `zlib.crc32`, never `hash()` (randomized per process), so layouts are
  deterministic.

## Host GPU caveat (this machine)

The RTX GPU is passed through to the Windows VM on demand (see ~/.claude/CLAUDE.md and
`~/Work/winvm-gpu`). `blender_bridge.pick_device` checks `virsh list --state-running` and
uses the CPU when a VM is up. If a GPU render is running and the VM is then started,
libvirt's managed hostdev detach can hang. That's why the bridge prints the warning; don't
remove it.

## Testing

- `python3 -m unittest discover -s tests`: core only, under 1 s.
- `tests/test_app.py`: app rules (freezing, rounds, variant input copies, overlay
  inheritance through grandchildren, output selection, the brief, questions before building) against a temporary
  projects root. It never touches the real settings file.
- `tests/test_models.py`: fingerprint invariants (lighting shares a model, geometry
  doesn't), edits merging, and pull-report-to-patch conversion, all without Blender.
- End to end: `./lv render demo --quality draft` (about 5 s), and `./lv render scantest`
  for the scan path (needs `tests/make_fake_scan.py` run once; see its header).
- To exercise pull without the GUI: open a saved model with `blender -b <fp>.blend -P
  script.py`, move or delete objects, `save_mainfile()`, then `lv pull`.
- `derived/logs/<job>_<fp>.log` holds Blender's full output for each job.
