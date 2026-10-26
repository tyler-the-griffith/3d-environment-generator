# 3D Environment Generator

Turns a landscaper's plan (plus photos, notes and optional drone footage) into one 3D model of
the finished yard, then takes every output from that model: photoreal images, videos, 360°
panoramas, a walkable 3D model in the browser, a PDF presentation and a plant schedule.

## Start

```bash
git clone <this repo> && cd <repo>
./launch              # Linux / macOS   (Windows: launch.bat)
./launch --install    # add it to your app menu (once)
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

- **Input page.** Add inputs: the landscaper's plan, specification/notes, site photos, drone
  media, documents, inspiration images, a 3D scan, the site location. Then press **Send to
  Claude**. The AI writes the **design file**: exact positions and sizes of every element.
  It checks its work against your plan and reports back with a summary, notes, questions,
  and a *plan check* image (the model from above with your drawing laid over it).
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
  the design is marked **stale**.
- **Design file.** Viewable and editable on the input page, with every version kept.
  Changes come from the AI, from you, or from **hand edits in Blender** (Open in Blender →
  move things → save → Pull Blender edits).
- **Child variants** (left navigation) inherit their parent's design and list only their
  differences, so fixes to the parent flow into every variant. Variants can have variants.
- **One model per design state.** Every output of a state comes from one saved model; a
  dusk image and a noon image of the same design come from the same file.

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
on Linux): theme mode, five palettes plus per-color overrides for light and dark,
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
