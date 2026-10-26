"""Background jobs with live progress, plus an event bus the UI listens to (server-sent events).

Two lanes so a long render never blocks the AI (or vice versa):
  "gpu" — anything that runs Blender (builds, renders, video, export). One at a time.
  "ai"  — AI rounds. One at a time.
Every job runs subprocesses (landviz CLI, AI agents) so output is captured per job and a job
can be cancelled by terminating its process.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config


class Bus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: str, data: dict):
        msg = {"event": event, "data": data}
        with self._lock:
            for q in list(self._subs):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


BUS = Bus()


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, node: str | None, title: str, fn, lane: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.node, self.title, self.fn, self.lane = kind, node, title, fn, lane
        self.status = "queued"
        self.progress = None  # 0..1 or None
        self.step = ""
        self.log: list[str] = []
        self.created = time.time()
        self.started = self.finished = None
        self.error = None
        self.result = None
        self.proc: subprocess.Popen | None = None
        self.cancel_requested = False

    def public(self, with_log=False) -> dict:
        d = {k: getattr(self, k) for k in ("id", "kind", "node", "title", "lane", "status", "progress", "step",
                                           "created", "started", "finished", "error")}
        d["log_tail"] = self.log[-6:]
        if with_log:
            d["log"] = self.log[-2000:]
        return d

    # ---- used by job functions
    def say(self, line: str):
        line = line.rstrip()
        if not line:
            return
        self.log.append(line)
        BUS.publish("job_log", {"id": self.id, "line": line})

    def set_step(self, step: str, progress: float | None = None):
        self.step = step
        if progress is not None:
            self.progress = progress
        self.say(f"▸ {step}")
        BUS.publish("job", self.public())

    def run(self, cmd: list[str], cwd: Path | None = None, env: dict | None = None,
            on_line=None, stdin_text: str | None = None, on_start=None) -> int:
        """Run a subprocess, streaming its output into the job log. Raises on failure/cancel."""
        if self.cancel_requested:
            raise Cancelled()
        e = dict(os.environ)
        e["PYTHONPATH"] = str(config.REPO_ROOT) + os.pathsep + e.get("PYTHONPATH", "")
        e["PYTHONUNBUFFERED"] = "1"
        blender = config.load_settings()["rendering"].get("blender_path")
        if blender:
            e["LANDVIZ_BLENDER"] = blender
        e.update(env or {})
        kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000200}
        self.proc = subprocess.Popen(cmd, cwd=cwd or config.REPO_ROOT, env=e, text=True, bufsize=1,
                                     stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        if on_start:
            on_start(self.proc.pid)
        if stdin_text is not None:
            self.proc.stdin.write(stdin_text)
            self.proc.stdin.close()
        for line in self.proc.stdout:
            if on_line:
                shown = on_line(line)
                if shown:
                    self.say(shown)
            else:
                self.say(line)
        rc = self.proc.wait()
        self.proc = None
        if self.cancel_requested:
            raise Cancelled()
        if rc != 0:
            raise RuntimeError(f"{Path(cmd[0]).name} exited with code {rc}")
        return rc

    def lv(self, *args: str) -> int:
        return self.run([sys.executable, "-m", "landviz", *args])


class Manager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lanes = {"gpu": queue.Queue(), "ai": queue.Queue()}
        for lane, q in self.lanes.items():
            threading.Thread(target=self._worker, args=(q,), daemon=True, name=f"lane-{lane}").start()

    def submit(self, kind: str, node: str | None, title: str, fn, lane: str = "gpu") -> Job:
        job = Job(kind, node, title, fn, lane)
        self.jobs[job.id] = job
        self.lanes[lane].put(job)
        BUS.publish("job", job.public())
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.cancel_requested = True
        if job.proc and job.proc.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(job.proc.pid, signal.SIGTERM)
                else:
                    job.proc.terminate()
            except ProcessLookupError:
                pass
        if job.status == "queued":
            job.status = "cancelled"
            BUS.publish("job", job.public())
        return True

    def list(self) -> list[dict]:
        return [j.public() for j in sorted(self.jobs.values(), key=lambda j: -j.created)[:60]]

    def busy_for(self, node: str) -> list[Job]:
        return [j for j in self.jobs.values() if j.node == node and j.status in ("queued", "running")]

    def _worker(self, q: queue.Queue):
        while True:
            job: Job = q.get()
            if job.status == "cancelled":
                continue
            job.status, job.started = "running", time.time()
            BUS.publish("job", job.public())
            try:
                job.result = job.fn(job)
                job.status = "done"
            except Cancelled:
                job.status = "cancelled"
                job.say("cancelled")
            except Exception as e:  # noqa: BLE001 — report every failure to the UI
                job.status = "failed"
                job.error = str(e)
                job.say(f"error: {e}")
                for line in traceback.format_exc().splitlines()[-6:]:
                    job.log.append(line)
            job.finished = time.time()
            job.progress = 1.0 if job.status == "done" else job.progress
            BUS.publish("job", job.public())
            if job.node:
                BUS.publish("node_changed", {"node": job.node})


MANAGER = Manager()


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
