"""Per-field gate policy (E4-S2/S3): fact type x evidence state -> gate.

The numbers are ASSUMED until fitted on adjudicated data (ARCHITECTURE §15.9); the
structural rules are not assumptions and cannot be loosened by an override file:
  * engines disagree            -> review, always
  * no reading at all           -> review, always
  * single engine (handwriting) -> review for every governed clinical field
  * grounding failed            -> review, always (a finding, blocker severity)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import settings

POLICY_ID = "field-policy-v1-assumed"

# evidence states ordered worst -> best; a fact takes the worst state of its evidence lines
STATE_ORDER = ["no_reading", "disagree", "single_engine", "page_level", "agree", "printed", "legacy"]
_ALWAYS_REVIEW = {"no_reading", "disagree"}
_GOVERNED = ("medication", "lab_result", "vital_sign", "investigation_order", "condition",
             "allergy")

DEFAULT_POLICY: dict[str, Any] = {
    "id": POLICY_ID,
    "default": {                      # state -> {action, min_conf}
        "printed":       {"action": "gate"},
        "legacy":        {"action": "gate"},
        "agree":         {"action": "gate", "min_conf": 0.90},
        "page_level":    {"action": "gate", "min_conf": 0.95},
        "single_engine": {"action": "gate", "min_conf": 0.97},
        "disagree":      {"action": "review"},
        "no_reading":    {"action": "review"},
    },
    "by_fact_type": {
        "medication": {"agree": {"action": "gate", "min_conf": 0.92},
                       "page_level": {"action": "review"},
                       "single_engine": {"action": "review"}},
        "lab_result": {"page_level": {"action": "review"},
                       "single_engine": {"action": "review"}},
        "vital_sign": {"single_engine": {"action": "review"}},
        "investigation_order": {"single_engine": {"action": "review"}},
    },
}


@dataclass
class GateRule:
    action: str            # gate | review
    min_conf: float | None
    key: str               # "<policy id>:<fact_type|default>:<state>"


@lru_cache(maxsize=1)
def _policy() -> dict[str, Any]:
    pol = json.loads(json.dumps(DEFAULT_POLICY))
    if settings.gate_policy_path:
        over = json.loads(Path(settings.gate_policy_path).read_text(encoding="utf-8"))
        pol["id"] = over.get("id", pol["id"])
        pol["default"].update(over.get("default") or {})
        for ft, m in (over.get("by_fact_type") or {}).items():
            pol["by_fact_type"].setdefault(ft, {}).update(m)
    return pol


def worst_state(states: list[str | None]) -> str:
    known = [s for s in states if s in STATE_ORDER]
    if not known:
        return "legacy"
    return min(known, key=STATE_ORDER.index)


def rule_for(fact_type: str, state: str) -> GateRule:
    pol = _policy()
    ft_rules = pol["by_fact_type"].get(fact_type) or {}
    r = ft_rules.get(state)
    scope = fact_type
    if r is None:
        r, scope = pol["default"].get(state) or {"action": "gate"}, "default"
    action = r.get("action", "gate")
    # structural rules win over any override
    if state in _ALWAYS_REVIEW or (state == "single_engine" and fact_type in _GOVERNED
                                   and fact_type != "condition"):
        action = "review"
    return GateRule(action, r.get("min_conf"), f"{pol['id']}:{scope}:{state}")
