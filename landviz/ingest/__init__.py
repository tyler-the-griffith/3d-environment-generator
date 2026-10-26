"""Reference ingestion: landscaper plans (PDF/images) and drone/site media."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}


class IngestError(RuntimeError):
    pass


def import_plan(src: Path, dest_dir: Path, page: int | None = None, dpi: int = 150) -> list[Path]:
    """Rasterize a plan PDF (each page, or one page) or copy an image into plan/."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        if not shutil.which("pdftoppm"):
            raise IngestError("pdftoppm (poppler) is required to import PDFs")
        args = ["pdftoppm", "-png", "-r", str(dpi)]
        if page:
            args += ["-f", str(page), "-l", str(page)]
        prefix = dest_dir / f"{src.stem}"
        subprocess.run(args + [str(src), str(prefix)], check=True)
        out = sorted(dest_dir.glob(f"{src.stem}*.png"))
    elif src.suffix.lower() in IMAGE_EXT:
        dst = dest_dir / src.name
        shutil.copy2(src, dst)
        out = [dst]
    else:
        raise IngestError(f"unsupported plan format {src.suffix}")
    (dest_dir / "import.json").write_text(json.dumps(
        {"source": str(src), "dpi": dpi if src.suffix.lower() == ".pdf" else None,
         "pages": [p.name for p in out]}, indent=2))
    return out


def ingest_media(src: Path, dest: Path, fps: float = 1.0) -> dict:
    """Collect drone/site photos into one flat image folder for reconstruction.

    Stills are hard-linked (or copied); videos are sampled at `fps` frames per second with
    ffmpeg at full quality. Returns an inventory summary.
    """
    dest.mkdir(parents=True, exist_ok=True)
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    stills = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXT]
    for p in stills:
        dst = dest / f"{p.parent.name}_{p.name}" if p.parent != src else dest / p.name
        if not dst.exists():
            try:
                dst.hardlink_to(p)
            except OSError:
                shutil.copy2(p, dst)
    frames = 0
    for v in videos:
        if not shutil.which("ffmpeg"):
            raise IngestError("ffmpeg is required to extract frames from video")
        pattern = dest / f"{v.stem}_%05d.jpg"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(v), "-vf", f"fps={fps}",
                        "-q:v", "2", str(pattern)], check=True)
        frames += len(list(dest.glob(f"{v.stem}_*.jpg")))
    summary = {"source": str(src), "stills": len(stills), "videos": len(videos),
               "video_frames": frames, "total_images": len(list(dest.glob("*")))}
    if summary["total_images"] < 30:
        summary["warning"] = "fewer than ~30 images usually gives a poor reconstruction"
    if videos and not stills:
        summary["note"] = ("video frames have no per-frame GPS; scale/orientation will come from "
                           "control points in project.yaml rather than the drone's GPS")
    return summary
