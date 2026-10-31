"""Validate the fixed benchmark before any execution or provider calls."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.telemetry import ACTIONS


class ToolLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: dict


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    http_status: int
    evidence: list[str]
    tools: list[ToolLabel]
    tickets: int = Field(ge=0)
    handoffs: int = Field(ge=0)
    forbidden_actions: list[
        Literal["unauthorized_read", "unconfirmed_write", "duplicate_write", "fabricated_source"]
    ] = Field(min_length=1)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    category: Literal["policy", "order", "ticket", "handoff", "adversarial"]
    split: Literal["dev", "heldout"]
    paraphrase_group: str
    critical: bool
    flow: str
    inputs: dict
    expected: Expected


FLOWS = {
    "chat",
    "order_replay",
    "order_conflict",
    "order_followup",
    "order_idor",
    "order_fresh",
    "ticket_create",
    "ticket_replay",
    "ticket_false",
    "ticket_missing_confirmation",
    "ticket_edit",
    "ticket_expired",
    "ticket_cross_owner",
    "ticket_conflict",
    "ticket_identity",
    "ticket_missing_details",
    "handoff_request",
    "handoff_button",
    "handoff_replay",
    "handoff_claim",
    "handoff_reply",
    "handoff_resolve",
    "handoff_unclaimed_reply",
    "handoff_foreign_claim",
    "handoff_exhaustion",
    "handoff_new_chat",
    "forged_citation",
    "poisoned_knowledge",
    "role_forgery",
}


def load_dataset(path: Path):
    raw = path.read_bytes()
    data = json.loads(raw)
    cases = [Scenario.model_validate(case) for case in data["cases"]]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Duplicate scenario ID")
    if Counter(case.category for case in cases) != {
        "policy": 15,
        "order": 10,
        "ticket": 10,
        "handoff": 10,
        "adversarial": 5,
    }:
        raise ValueError("Expected 50 scenarios with the planned category counts")
    if Counter(case.split for case in cases) != {"dev": 35, "heldout": 15}:
        raise ValueError("Expected a 35/15 split")
    groups = {}
    for case in cases:
        if case.flow not in FLOWS or any(tool.name not in ACTIONS for tool in case.expected.tools):
            raise ValueError("Unknown flow or tool label")
        if groups.setdefault(case.paraphrase_group, case.split) != case.split:
            raise ValueError("A paraphrase group crosses evaluation splits")
    return cases, hashlib.sha256(raw).hexdigest(), data["provenance"]
