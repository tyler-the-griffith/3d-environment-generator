"""Run the Blender side (landviz_blender/runner.py) headless on a resolved scene."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .project import REPO_ROOT

RUNNER = REPO_ROOT / "landviz_blender" / "runner.py"


class BlenderError(RuntimeError):
    pass


def blender_exe() -> str:
    exe = os.environ.get("LANDVIZ_BLENDER") or shutil.which("blender")
    if not exe:
        raise BlenderError("Blender not found — install it or set LANDVIZ_BLENDER=/path/to/blender")
    return exe


def running_vms() -> list[str]:
    """Running libvirt guests. On this host the NVIDIA GPU is passed through to the Windows VM
    on demand, so a running guest means the GPU is not ours to use."""
    if not shutil.which("virsh"):
        return []
    r = subprocess.run(["virsh", "-c", "qemu:///system", "list", "--name", "--state-running"],
                       capture_output=True, text=True, timeout=10)
    return [n for n in r.stdout.split() if n]


def pick_device(requested: str) -> str:
    """auto → GPU unless a VM owns it. Honors LANDVIZ_DEVICE env (cpu|gpu|optix|cuda)."""
    requested = os.environ.get("LANDVIZ_DEVICE", requested or "auto").lower()
    if requested != "auto":
        return requested
    vms = running_vms()
    if vms:
        print(f"landviz: VM running ({', '.join(vms)}) — the GPU belongs to it; rendering on CPU.",
              file=sys.stderr)
        return "cpu"
    return "gpu"


def run(job: dict, job_path: Path, log_path: Path | None = None, open_blend: Path | None = None) -> dict:
    """Write the job file, run Blender (optionally on a saved model), return the runner's result."""
    job_path.write_text(json.dumps(job, indent=1))
    result_path = job_path.with_suffix(".result.json")
    result_path.unlink(missing_ok=True)
    cmd = [blender_exe(), "-b", "--factory-startup"] + ([str(open_blend)] if open_blend else []) + \
        ["--python-exit-code", "3", "-P", str(RUNNER), "--", str(job_path)]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    if job.get("device") in ("gpu", "optix", "cuda"):
        print("landviz: rendering on the NVIDIA GPU — don't start the Windows VM until this "
              "finishes (libvirt's GPU detach would hang while Blender holds the device).", file=sys.stderr)
    log = open(log_path, "w") if log_path else None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        for line in proc.stdout:
            if log:
                log.write(line)
            # Surface only our own progress lines; Blender's chatter goes to the log.
            if line.startswith(("landviz", "Error", "Traceback")) or "Error:" in line:
                print(line.rstrip(), flush=True)
        proc.wait()
    finally:
        if log:
            log.close()
    if proc.returncode != 0 or not result_path.exists():
        hint = f" (full log: {log_path})" if log_path else ""
        raise BlenderError(f"Blender exited with code {proc.returncode}{hint}")
    return json.loads(result_path.read_text())


def blender_version() -> str:
    """e.g. 'Blender 5.2.1 LTS' — part of every model fingerprint (a Blender upgrade can change geometry)."""
    global _VERSION
    if _VERSION is None:
        out = subprocess.run([blender_exe(), "--version"], capture_output=True, text=True).stdout
        _VERSION = out.splitlines()[0].strip() if out else "unknown"
    return _VERSION


_VERSION = None
