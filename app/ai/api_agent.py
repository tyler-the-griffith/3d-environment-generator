"""Anthropic API provider: a small tool-using agent built on the official SDK's tool runner.

Run as a subprocess by app/ai so it can be cancelled like the CLI agents:
    python -m app.ai.api_agent --prompt-file … --result-file … --design-file … --project-dir …

Tools are deliberately narrow: read files in the repo, write only the design file / species.yaml /
result file, and run the framework's validate and species commands. Input images and PDFs are
attached to the first message so Claude can read the plan directly.
"""
from __future__ import annotations

import argparse
import base64
import re
import subprocess
import sys
from pathlib import Path

import anthropic
from anthropic import beta_tool

REPO = Path(__file__).resolve().parents[2]
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif"}
MAX_ATTACH_BYTES = 20 * 1024 * 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--result-file", required=True)
    ap.add_argument("--design-file", required=True)
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high")
    a = ap.parse_args()

    project_dir = Path(a.project_dir).resolve()
    writable = {Path(a.design_file).resolve(), Path(a.result_file).resolve(), project_dir / "species.yaml"}
    brief = Path(a.prompt_file).read_text()

    @beta_tool
    def read_file(path: str) -> str:
        """Read a text file from the repository or project (YAML, Markdown, CSV…).

        Args:
            path: Absolute path, or a path relative to the repository root.
        """
        p = (REPO / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not (str(p).startswith(str(REPO)) or str(p).startswith(str(project_dir))):
            return "Error: outside the repository"
        if not p.exists():
            return f"Error: {p} does not exist"
        if p.is_dir():
            return "\n".join(sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir()))
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            return "Error: binary file (images and PDFs from the inputs are attached to the first message)"
        return text[:200_000]

    @beta_tool
    def write_file(path: str, content: str) -> str:
        """Write one of the files you're allowed to write (the design file, species.yaml, the result JSON).

        Args:
            path: Absolute path of the file to write.
            content: The complete new file content.
        """
        p = Path(path).resolve()
        if p not in writable:
            return "Error: not allowed. You may write only: " + ", ".join(str(w) for w in writable)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        print(f"→ wrote {p.name} ({len(content)} chars)", flush=True)
        return "ok"

    @beta_tool
    def run_lv(command: str, args: str = "") -> str:
        """Run a framework command: `validate` (check the design) or `species` (search the plant catalog).

        Args:
            command: Either "validate" or "species".
            args: Arguments, e.g. the project path (and --variant name) for validate, or search text for species.
        """
        if command not in ("validate", "species"):
            return "Error: only validate and species are available"
        if re.search(r"[;&|`$<>]", args):
            return "Error: invalid characters in args"
        print(f"→ lv {command} {args}", flush=True)
        r = subprocess.run([sys.executable, "-m", "landviz", command, *args.split()], cwd=REPO,
                           capture_output=True, text=True, timeout=300)
        return (r.stdout + r.stderr)[-20_000:] or f"(exit {r.returncode})"

    content = []
    for line in brief.splitlines():  # attach the input files the brief points at
        m = re.search(r"file: `([^`]+)`", line)
        if not m:
            continue
        f = Path(m.group(1))
        if not f.exists() or f.stat().st_size > MAX_ATTACH_BYTES:
            continue
        data = base64.standard_b64encode(f.read_bytes()).decode()
        if f.suffix.lower() in IMAGE_TYPES:
            content.append({"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[f.suffix.lower()],
                                                        "data": data}})
        elif f.suffix.lower() == ".pdf":
            content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                           "data": data}, "title": f.name})
    content.append({"type": "text", "text": brief + "\n\n(You can't run `./lv check` in this mode; use run_lv validate.)"})

    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=a.model,
        max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": a.effort},
        # Server-side fallback: if a safety classifier declines, the API retries on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        tools=[read_file, write_file, run_lv],
        messages=[{"role": "user", "content": content}],
    )
    last = None
    try:
        for message in runner:
            last = message
            for block in message.content:
                if block.type == "text" and block.text.strip():
                    print(block.text.strip()[:1200], flush=True)
                elif block.type == "tool_use":
                    print(f"→ {block.name}", flush=True)
    except anthropic.AuthenticationError:
        sys.exit("Anthropic API: invalid API key (Settings → AI)")
    except anthropic.RateLimitError:
        sys.exit("Anthropic API: rate limited — try again in a minute")
    except anthropic.APIStatusError as e:
        sys.exit(f"Anthropic API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Anthropic API: network error")
    if last is not None and last.stop_reason == "refusal":
        sys.exit("The model declined this request.")
    if last is not None and last.stop_reason == "max_tokens":
        print("warning: the response hit the output limit", flush=True)


if __name__ == "__main__":
    main()
