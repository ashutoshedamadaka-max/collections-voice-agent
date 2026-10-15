"""Local reproducibility metadata; no secrets or network access."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def fingerprint() -> dict:
    paths = sorted((ROOT / "src" / "collections_agent").rglob("*.py"))
    paths += sorted((ROOT / "src" / "collections_agent" / "voice" / "templates").glob("*.j2"))
    paths += [ROOT / "config" / "sim_scenarios.yaml", ROOT / "uv.lock"]
    hashes = {
        str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.exists()
    }
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    return {"git_revision": revision, "file_sha256": hashes, "evaluator_version": "2"}
