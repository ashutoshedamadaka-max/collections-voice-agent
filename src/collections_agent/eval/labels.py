"""Human-judgement labels for the eval set (`fixtures/raw/*.json`).

Stored at `fixtures/eval/labels.json`, entirely separate from `fixtures/postcall/` (the
pipeline's own output) and from the eval's own pipeline-run cache
(`fixtures/eval/pipeline_runs/`) — there is no code path by which a specialist's answer could
leak into, or be influenced by, a label recorded here. `label_calls_cmd` (cli.py) writes these;
`eval/compare.py` reads them to measure agreement.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from collections_agent.models.domain import CallOutcomeType, WriteDecision

LABELS_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "eval" / "labels.json"


class HumanLabel(BaseModel):
    """One person's independent judgement of one call — not derived from, or shown alongside,
    any pipeline output at labeling time."""

    call_id: str
    outcome: CallOutcomeType
    promise_captured: bool
    promise_complete: bool | None = None  # only meaningful when promise_captured is True
    dispute_existed: bool
    compliance_violated: bool
    write_decision: WriteDecision
    notes: str = ""
    labeled_at: datetime


def load_labels(path: Path | None = None) -> dict[str, HumanLabel]:
    path = path or LABELS_PATH
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {call_id: HumanLabel.model_validate(data) for call_id, data in raw.items()}


def save_label(label: HumanLabel, path: Path | None = None) -> None:
    """Merges one label into the store and rewrites it — each call is saved immediately after
    it's labeled, so a session that stops partway through loses nothing already answered."""
    path = path or LABELS_PATH
    labels = load_labels(path)
    labels[label.call_id] = label
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {call_id: json.loads(lbl.model_dump_json()) for call_id, lbl in labels.items()}
    path.write_text(json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8")
