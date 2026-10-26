"""Photogrammetry engines behind one interface.

An engine turns a folder of overlapping photos into a textured mesh. The rest of the
framework only consumes the result through a `mesh` element in project.yaml, so engines
are swappable. Add one by subclassing Engine and registering it in ENGINES.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class ReconstructError(RuntimeError):
    pass


class Engine:
    name = ""

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def run(self, images: Path, workdir: Path, quality: str) -> dict:
        """Return {'mesh': path, 'texture_dir': path|None, 'georeferenced': bool, ...}."""
        raise NotImplementedError


class OpenDroneMap(Engine):
    """OpenDroneMap in Docker (opendronemap/odm). Uses drone GPS EXIF for scale/georeference."""
    name = "odm"
    image = "opendronemap/odm"

    def available(self):
        if not shutil.which("docker"):
            return False, "docker not installed"
        r = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if r.returncode != 0:
            msg = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "docker not usable"
            if "permission denied" in msg:
                msg += "  → add yourself to the docker group: sudo usermod -aG docker $USER (then log out/in)"
            return False, msg
        return True, "ok"

    def run(self, images, workdir, quality="high"):
        ok, why = self.available()
        if not ok:
            raise ReconstructError(f"OpenDroneMap unavailable: {why}")
        proj = workdir / "odm"
        (proj / "images").mkdir(parents=True, exist_ok=True)
        for p in images.iterdir():
            dst = proj / "images" / p.name
            if not dst.exists():
                shutil.copy2(p, dst)
        opts = {
            "fast": ["--fast-orthophoto", "--mesh-size", "100000"],
            "high": ["--mesh-size", "400000", "--use-3dmesh", "--pc-quality", "high"],
            "ultra": ["--mesh-size", "1000000", "--use-3dmesh", "--pc-quality", "ultra",
                      "--feature-quality", "ultra"],
        }[quality]
        cmd = ["docker", "run", "--rm", "-v", f"{workdir}:/datasets", self.image,
               "--project-path", "/datasets", "odm", "--dtm", "--dsm", *opts]
        print("+", " ".join(cmd))
        subprocess.run(cmd, check=True)
        tex = proj / "odm_texturing"
        mesh = next(iter(sorted(tex.glob("odm_textured_model_geo.obj"))), None) or next(iter(tex.glob("*.obj")), None)
        if not mesh:
            raise ReconstructError(f"ODM finished but no textured mesh in {tex}")
        geo = proj / "odm_georeferencing" / "coords.txt"
        return {"engine": self.name, "mesh": str(mesh), "texture_dir": str(tex),
                "georeferenced": geo.exists(),
                "georef_coords": geo.read_text() if geo.exists() else None,
                "dtm": str(proj / "odm_dem" / "dtm.tif"), "orthophoto": str(proj / "odm_orthophoto" / "odm_orthophoto.tif")}


class Colmap(Engine):
    """COLMAP (native binary). No GPS georeferencing — align with control points afterwards."""
    name = "colmap"

    def available(self):
        return (True, "ok") if shutil.which("colmap") else (False, "colmap not installed (pacman -S colmap / AUR)")

    def run(self, images, workdir, quality="high"):
        ok, why = self.available()
        if not ok:
            raise ReconstructError(why)
        ws = workdir / "colmap"
        ws.mkdir(parents=True, exist_ok=True)
        subprocess.run(["colmap", "automatic_reconstructor", "--workspace_path", str(ws),
                        "--image_path", str(images), "--quality", "high" if quality != "fast" else "low",
                        "--use_gpu", "1"], check=True)
        mesh = next(iter(sorted(ws.rglob("meshed-poisson.ply"))), None)
        if not mesh:
            raise ReconstructError("COLMAP finished without a Poisson mesh")
        return {"engine": self.name, "mesh": str(mesh), "texture_dir": None, "georeferenced": False}


ENGINES: dict[str, Engine] = {e.name: e for e in (OpenDroneMap(), Colmap())}


def run(engine: str, images: Path, workdir: Path, quality: str = "high") -> dict:
    if engine not in ENGINES:
        raise ReconstructError(f"unknown engine {engine!r}; available: {', '.join(ENGINES)}")
    if not images.exists() or not any(images.iterdir()):
        raise ReconstructError(f"no images in {images} — run `landviz media ingest` first")
    workdir.mkdir(parents=True, exist_ok=True)
    result = ENGINES[engine].run(images, workdir, quality)
    (workdir / "manifest.json").write_text(json.dumps(result, indent=2))
    return result


def status() -> dict[str, str]:
    return {name: ("available" if ok else f"unavailable: {why}")
            for name, e in ENGINES.items() for ok, why in [e.available()]}


