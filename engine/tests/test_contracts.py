from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from soatvan.checking import Block, Preset, RuleEngine
from soatvan.entrypoints.sidecar import PUBLIC_EVENTS, PUBLIC_METHODS

CONTRACTS = Path(__file__).parents[2] / "contracts"


def _schema(name: str) -> dict[str, object]:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_all_json_schemas_are_valid_draft_2020_12() -> None:
    for path in CONTRACTS.glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_ipc_schema_and_engine_publish_the_same_methods_and_events() -> None:
    schema = _schema("ipc-v1.schema.json")
    variants = schema["oneOf"]  # type: ignore[index]
    request_methods = frozenset(variants[0]["properties"]["method"]["enum"])
    events = frozenset(variants[2]["properties"]["event"]["enum"])
    assert request_methods == PUBLIC_METHODS
    assert events == PUBLIC_EVENTS


def test_protocol_examples_validate_and_unknown_fields_fail() -> None:
    validator = Draft202012Validator(_schema("ipc-v1.schema.json"))
    examples = [
        {"v": 1, "id": "r1", "method": "engine.hello", "params": {}},
        {"v": 1, "id": "r1", "ok": True, "result": {"protocol": 1}},
        {
            "v": 1,
            "event": "job.progress",
            "data": {
                "job_id": "j1",
                "stage": "rules",
                "percent": 35,
                "message_code": "job.applying_rules",
            },
        },
    ]
    for frame in examples:
        validator.validate(frame)
    with pytest.raises(ValidationError):
        validator.validate({**examples[0], "document_content": "must never cross IPC"})


def test_job_start_schema_enforces_full_review_model_invariants() -> None:
    validator = Draft202012Validator(_schema("ipc-v1.schema.json"))
    valid = {
        "v": 1,
        "id": "r1",
        "method": "job.start",
        "params": {
            "use_model": True,
            "full_review": True,
            "include_rule_findings": True,
        },
    }
    validator.validate(valid)

    for params in (
        {"use_model": False, "full_review": True},
        {"use_model": True, "full_review": False, "include_rule_findings": True},
        {"use_model": False, "full_review": False, "include_rule_findings": True},
    ):
        with pytest.raises(ValidationError):
            validator.validate({**valid, "params": params})


def test_domain_finding_matches_contract() -> None:
    finding = RuleEngine().check(
        [Block("document:p0", "sát nhập")], Preset.STANDARD
    )[0]
    Draft202012Validator(_schema("finding.schema.json")).validate(finding.as_dict())
