"""Human-judgement labels for the eval set (`fixtures/raw/*.json`).

Stored at `fixtures/eval/labels_v2.json`, entirely separate from `fixtures/postcall/` (the
pipeline's own output) and from the eval's own pipeline-run cache
(`fixtures/eval/pipeline_runs/`) — there is no code path by which a specialist's answer could
leak into, or be influenced by, a label recorded here. `label_calls_cmd` (cli.py) writes these;
`eval/compare.py` reads them to measure agreement.
The original labels.json is preserved as invalidated historical data and is excluded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from collections_agent.models.domain import CallOutcomeType, WriteDecision

EVAL_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "eval"
LEGACY_LABELS_PATH = EVAL_DIR / "labels.json"  # Invalidated; never overwritten or evaluated.
LABELS_PATH = EVAL_DIR / "labels_v2.json"


def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HumanLabel(BaseModel):
    """One person's independent judgement of one call — not derived from, or shown alongside,
    any pipeline output at labeling time."""

    call_id: str
    schema_version: Literal[2] = 2
    rubric_version: Literal["2"] = "2"
    reviewer: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    assessability: Literal["reviewed", "uncertain", "unusable"]
    outcome: CallOutcomeType | None = None
    promise_captured: bool | None = None
    promise_complete: bool | None = None  # only meaningful when promise_captured is True
    dispute_existed: bool | None = None
    compliance_violated: bool | None = None
    # Only populate after reviewing a particular proposed record, not business follow-up.
    write_decision: WriteDecision | None = None
    operational_followup: bool | None = None
    followup_reason: str = ""
    expected_records: str = ""
    evidence: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    labeled_at: datetime

    @model_validator(mode="after")
    def require_evidence(self):
        for name in (
            "outcome",
            "promise_captured",
            "promise_complete",
            "dispute_existed",
            "compliance_violated",
            "operational_followup",
            "write_decision",
        ):
            if getattr(self, name) is not None and not self.evidence.get(name, "").strip():
                raise ValueError(f"{name} requires a quote/turn reference and policy rationale")
        if self.write_decision is not None and not self.expected_records.strip():
            raise ValueError("Write eligibility requires a specified record; follow-up is separate")
        return self


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
    if label.call_id in labels:
        history = path.with_suffix(".history.jsonl")
        with history.open("a", encoding="utf-8") as f:
            f.write(labels[label.call_id].model_dump_json() + "\n")
    labels[label.call_id] = label
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {call_id: json.loads(lbl.model_dump_json()) for call_id, lbl in labels.items()}
    path.write_text(json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8")
