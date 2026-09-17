"""Loads the tunable priority-weight config from YAML into a typed model."""

from __future__ import annotations

from pathlib import Path

import yaml

from collections_agent.models.domain import PriorityWeights

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "priority_weights.yaml"


def load_priority_weights(path: Path = DEFAULT_CONFIG_PATH) -> PriorityWeights:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PriorityWeights.model_validate(raw)
