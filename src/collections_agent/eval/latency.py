"""Read provider timing evidence from saved calls; no model or network requests."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

RAW_CALLS_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "raw"
METRICS = {
    "turn": "turnLatency",
    "model": "modelLatency",
    "voice": "voiceLatency",
    "transcriber": "transcriberLatency",
    "endpointing": "endpointingLatency",
}


def _milliseconds(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        return None
    return float(value)


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None, "max_ms": None}
    return {
        "samples": len(ordered),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 2),
        "mean_ms": round(statistics.mean(ordered), 2),
        "max_ms": round(ordered[-1], 2),
    }


def call_latency(raw: dict) -> dict:
    artifact = raw.get("artifact") or {}
    metrics = artifact.get("performanceMetrics") or raw.get("performanceMetrics") or {}
    entries = metrics.get("turnLatencies") or []
    turns = []
    invalid_turns = 0
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            invalid_turns += 1
            continue
        values = {
            name: _milliseconds(entry.get(key), positive=name == "turn") for name, key in METRICS.items()
        }
        if values["turn"] is None:
            invalid_turns += 1
        turns.append({"provider_turn_index": index, **values})
    distributions = {
        name: distribution([turn[name] for turn in turns if turn[name] is not None]) for name in METRICS
    }
    return {
        "call_id": raw.get("id"),
        "started_at": raw.get("startedAt"),
        "source": "Vapi artifact.performanceMetrics",
        "units": "milliseconds",
        "status": "available" if distributions["turn"]["samples"] else "unavailable",
        "invalid_or_missing_turn_measurements": invalid_turns,
        "provider_reported_turn_average_ms": _milliseconds(metrics.get("turnLatencyAverage"), positive=True),
        "metrics": distributions,
        "turns": turns,
    }


def latency_report(
    raw_dir: Path = RAW_CALLS_DIR, *, include_calls: bool = True, only_call_ids: set[str] | None = None
) -> dict:
    calls = []
    files = []
    for path in sorted(raw_dir.glob("*.json")):
        if only_call_ids is not None and path.stem not in only_call_ids:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        evidence = call_latency(raw)
        evidence["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        calls.append(evidence)
        files.append({"name": path.name, "sha256": evidence["source_sha256"]})
    metrics = {
        name: distribution([turn[name] for call in calls for turn in call["turns"] if turn[name] is not None])
        for name in METRICS
    }
    report = {
        "report_version": "1",
        "data_scope": "historical saved test calls; provider-reported timings",
        "units": "milliseconds",
        "methods": {"p50": "median", "p95": "nearest_rank", "weighting": "per measured turn"},
        "calls_inspected": len(calls),
        "calls_with_turn_measurements": sum(call["status"] == "available" for call in calls),
        "calls_without_turn_measurements": sum(call["status"] != "available" for call in calls),
        "invalid_or_missing_turn_measurements": sum(
            call["invalid_or_missing_turn_measurements"] for call in calls
        ),
        "metrics": metrics,
        "limitations": [
            "Historical test sample, not production latency or a model comparison.",
            "Provider timing boundary has not been independently verified against listener playback.",
            "Tool/no-tool association, substantive-answer timing and interruption recovery are unassessed.",
            "Averages without per-turn measurements are excluded; missing values are not zero.",
            "Small samples make tail estimates unstable; component durations may overlap.",
        ],
    }
    if include_calls:
        report.update(calls=calls, source_files=files)
    return report
