# 3D Environment Generator

Turns a landscaper's plan (plus photos, notes and optional drone footage) into one 3D model of
the finished yard, then takes every output from that model: photoreal images, videos, 360°
panoramas, a walkable 3D model in the browser, a PDF presentation and a plant schedule.

## Start

```bash
git clone <this repo> && cd <repo>
./launch              # Linux / macOS   (Windows: launch.bat)
./launch --install    # add it to your app menu (once)
./launch --setup      # optional extras (LiDAR point clouds) into ./.venv (once)
```

After `--install`, start it like any other app: Super+Space on Omarchy, the app grid on
GNOME/KDE, Launchpad or Spotlight on macOS (from `~/Applications`), the Start Menu on Windows.
`./launch --uninstall` removes the menu entry. Launching it again while it's running opens
another window on the same app.

The app opens in its own window: native (if `pip install pywebview`), a Chrome/Edge app
window, or your default browser, in that order. When started from the menu, output goes to
`app.log` in the settings folder. Requirements: Python 3 with numpy and
PyYAML, Blender ≥ 4.2 (5.2 tested), poppler (`pdftoppm`), ImageMagick, ffmpeg, and one AI
option (below). The bundled **Demo** project renders right away.

## How it works

```
your inputs ──(AI, once per round)──▶ design file ──(deterministic code)──▶ one 3D model ──▶ every output
```

- **A project is the property as it exists.** Its base design is a simulation of what's there
  today. Ideas for changing it (a landscaper's proposal, a renovation) are **child variants**
  that inherit the existing environment and describe only what changes.
- **Built step by step.** The existing environment is worked out in order: location &
  orientation → ground elevation → property boundary → feature inventory → ground surfaces →
  building outlines → building details → fences & walls → plants, trees & boulders. After each
  step the AI pauses: check it on the **map** (the step's outlines over an aerial photo, the
  plan, a LiDAR height map or a straight-down drone photo), then **Approve**, **adjust it
  yourself** (drag corners and edges, move, resize, remove or add elements; saved straight into
  the design file), **pin comments** on the map and have the step redone, or choose **Use best
  judgement for now** and come back later. A switch lets it use its best judgement for all
  remaining steps. Changing an earlier step flags the later ones for a recheck.
- **Input page.** Add inputs: the landscaper's plan, specification/notes, site photos, drone
  media, documents, inspiration images, a 3D scan, the site location. Then press **Send to
  Claude**. The AI writes the **design file**: exact positions and sizes of every element.
  It checks its work against your plan and reports back with a summary, notes, questions,
  and a *plan check* image (the model from above with your drawing laid over it).
  If something that would change the model is unclear (a missing dimension, whether a
  structure stays), it **asks first**: answer each question in your own words or tick
  *Exercise your best judgement* to accept the assumption it states, then it builds.
- **Sent inputs are frozen** and kept as history, round by round. New inputs stack on top.
  To change or delete a sent input, start a **child variant**; its copies are editable.
- **Outputs to make** (on both pages, never frozen): pick from still images, plan check,
  fly-around video, walk-through video, 3D walkthrough, 360° panoramas, four seasons,
  growth over time, dusk & lighting, PDF presentation, plant schedule (CSV), and the Blender
  model file. Or request something else in plain words: the AI adds what the design needs,
  or explains why it can't. Turning a type off hides its outputs; nothing is deleted.
- **Output page.** Every output appears as a cover image taken from the output itself.
  Images and PDFs show inline, videos play, and the 3D walkthrough opens in place. Download
  any one, or **Download all** as folders by type. Anything made from an older version of
  the design is marked **stale**. The visuals come first; Generate/Download, Publish and
  Outputs to make sit in the column on the right.
- **Publish as a web page.** Pick which outputs to include and build one web page in the
  app's own colors (gallery, 3D walkthrough, videos). Preview it, download it as a folder
  for any static host, or put it online with **GitHub Pages** if the GitHub CLI (`gh`) is
  installed and signed in. GitHub Pages sites are public; the app asks before publishing.
- **Design file.** Viewable and editable on the input page, with every version kept.
  Changes come from the AI, from you, or from **hand edits in Blender** (Open in Blender →
  move things → save → Pull Blender edits).
- **Child variants** (left navigation) inherit their parent's design and list only their
  differences, so fixes to the parent flow into every variant. Variants can have variants.
- **One model per design state.** Every output of a state comes from one saved model; a
  dusk image and a noon image of the same design come from the same file.

## Accuracy tools

- **Photo-match check.** Drone photos with GPS are re-created as cameras in the model and
  rendered side by side and overlaid with each photo (red lines = the model's edges). Wrong
  heights, roof shapes and tree positions stand out, and Claude uses it to correct itself.
  Original DJI files (from the drone's card or the DJI Fly album, not "beautify" exports) carry
  the camera's heading and tilt; without them Claude estimates and refines those too.
- **Public LiDAR data.** Add a "Public LiDAR data" input (an address, coordinates, or blank to
  use the photos' GPS). The app fetches free USGS data: measured terrain everywhere in the US,
  and where a laser survey exists, measured roof and tree heights plus a true-scale height map.
  The point-cloud part needs the optional extras: `./launch --setup` (one time).
- **Public aerial photo.** Add a "Public aerial photo" input (same location options). The app
  fetches a free straight-down USGS NAIP orthophoto (US only, ~0.6 m detail): no lean, lined up
  with the map. It's the map editor's backdrop and the AI traces outlines from it.
- **Roofs.** Houses are built from overlapping wings, each with its own eave height and roof
  (gable, hip, half-hip, gambrel, mansard, shed, flat), with porches on posts and decks with
  railings and stairs.

## AI options (Settings → AI)

| Option | Uses | Notes |
|---|---|---|
| Claude Code (default) | your Claude subscription | runs the checks and looks at the plan-check image itself |
| OpenAI Codex CLI | your ChatGPT/OpenAI subscription | sandboxed workspace-write mode |
| Google Gemini CLI | your Google account | may not be allowed to run the checks |
| Anthropic API | an API key, billed per use | `pip install anthropic`; validates, but can't see the check image |
| Custom command | any agent CLI | placeholders `{prompt_file}`, `{design_file}`, `{result_file}`, … |

Your inputs (plan, photos) are sent to whichever AI provider you choose.

## Settings

Per user, shared by all projects, stored outside the repo (`~/.config/3d-environment-generator/`
on Linux): theme mode, ten palettes based on Coolors' trending palettes (each checked for readable
contrast in light and dark mode) plus a high-contrast one, per-color overrides for light and dark,
interface and code fonts, text size, density, corner radius, navigation width, reduced
motion, explanations on/off (also on right-click), AI provider, default units, location
and outputs, and render device.

**Branding:** put your logo at `app/branding/logo.png` (transparent PNG). It's used in the
app and as the menu/window icon (until then, a default icon is used). The app name and
tagline live in `app/brand.json`; run `./launch --install` again after renaming so the menu
entry picks up the new name.

## Command line

Everything the app does is also available as `./lv` commands (`./lv --help`), and the
framework works without the app. `docs/SPEC.md` is the design-file reference;
`projects/demo/project.yaml` is a worked example.

## Notes for this machine

- The NVIDIA GPU is shared with the Windows VM. Renders check libvirt first and use the CPU
  while the VM runs. **Don't start the VM during a GPU render**: libvirt's GPU detach would
  hang while Blender holds the device.
- Drone photogrammetry uses OpenDroneMap in Docker: `sudo usermod -aG docker $USER`, then
  log out and back in.

## Tests

```bash
python3 -m unittest discover -s tests
```
