"""Strict executable-procedure contracts and canonical fingerprints."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import KnowledgeArticle
from ..security.crypto import canonical_json, sha256_hex
from .actions.registry import ACTIONS, ActionValidationError, validate_params

PROGID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
MODULE = re.compile(r"^[A-Za-z0-9_.-]{1,180}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosticStep(StrictModel):
    action: Literal["outlook.pdf_crash.inspect"]
    params: dict[str, Any]
    expect: dict[str, bool | str | int]

    @model_validator(mode="after")
    def validate_contract(self):
        allowed = {"addin_progid", "faulting_module", "lookback_hours", "since"}
        if set(self.params) - allowed:
            raise ValueError("unexpected diagnostic parameter")
        progid = self.params.get("addin_progid")
        module = self.params.get("faulting_module")
        hours = self.params.get("lookback_hours", 24)
        if not isinstance(progid, str) or not PROGID.fullmatch(progid):
            raise ValueError("diagnostic requires exact addin_progid")
        if not isinstance(module, str) or not MODULE.fullmatch(module):
            raise ValueError("diagnostic requires exact faulting_module")
        if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 168:
            raise ValueError("lookback_hours out of range")
        required = {"supported", "outlook.kind", "addin.installed", "crash.matched"}
        if not required.issubset(self.expect):
            raise ValueError("diagnostic expectations are incomplete")
        return self


class Postcondition(StrictModel):
    type: str = Field(min_length=1, max_length=80)
    params: dict[str, Any]


class RemediationStep(StrictModel):
    action: str
    params: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=2000)
    postconditions: list[Postcondition]

    @model_validator(mode="after")
    def validate_action(self):
        spec = ACTIONS.get(self.action)
        if not spec or spec.channel != "desktop" or spec.computer_use:
            raise ValueError("unsupported executable desktop action")
        if self.action not in {"office.addin.set_load_behavior", "app.restart"}:
            raise ValueError("action is not allowed by this procedure type")
        try:
            self.params = validate_params(self.action, self.params)
        except ActionValidationError as exc:
            raise ValueError(str(exc)) from exc
        if not self.postconditions:
            raise ValueError("remediation step requires a postcondition")
        return self


class RestartDefinition(StrictModel):
    action: Literal["app.restart"]
    params: dict[str, Any]


class RollbackDefinition(StrictModel):
    action: Literal["office.addin.restore_load_behavior"]
    source_step: int = Field(ge=0)
    restart: RestartDefinition


class ProcedureDisplay(StrictModel):
    addin_name: str = Field(min_length=1, max_length=200)
    addin_progid: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,120}$")


class ProcedureDefinition(StrictModel):
    procedure_schema: Literal[2]
    kind: Literal["windows_outlook_pdf_crash"]
    display: ProcedureDisplay
    diagnostics: list[DiagnosticStep] = Field(min_length=1)
    procedure: list[RemediationStep] = Field(min_length=1)
    rollback: RollbackDefinition
    messages: dict[str, str]

    @model_validator(mode="after")
    def validate_outlook_contract(self):
        diagnostic = self.diagnostics[0]
        change_steps = [step for step in self.procedure if step.action == "office.addin.set_load_behavior"]
        if len(change_steps) != 1:
            raise ValueError("procedure requires exactly one add-in change")
        if diagnostic.params["addin_progid"] != self.display.addin_progid:
            raise ValueError("diagnostic and display ProgID differ")
        if change_steps[0].params.get("addin_progid") != self.display.addin_progid:
            raise ValueError("remediation and display ProgID differ")
        if self.rollback.source_step != self.procedure.index(change_steps[0]):
            raise ValueError("rollback source_step must name the add-in change")
        if not {"diagnostic_consent", "remediation"}.issubset(self.messages):
            raise ValueError("required customer messages missing")
        return self


def validate_executable_body(body: dict) -> ProcedureDefinition:
    if not isinstance(body, dict):
        raise ValueError("procedure body must be an object")
    # Titles, symptoms, and other knowledge presentation fields are deliberately
    # outside the executable contract and therefore outside its fingerprint.
    contract = {name: body[name] for name in ProcedureDefinition.model_fields if name in body}
    return ProcedureDefinition.model_validate(contract)


def canonical_payload(definition: ProcedureDefinition) -> dict:
    return definition.model_dump(mode="json", exclude_none=True)


def procedure_hash(definition: ProcedureDefinition) -> str:
    return sha256_hex(canonical_json(canonical_payload(definition)))


def _legacy_browser_payload(body: dict) -> dict:
    """Canonicalize the old browser-only format without admitting desktop actions."""
    steps = body.get("procedure") if isinstance(body, dict) else None
    if not isinstance(steps, list) or not steps:
        raise ValueError("legacy browser procedure requires steps")
    clean_steps = []
    allowed = {"action", "params", "rationale", "postconditions"}
    for raw in steps:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("invalid legacy browser step")
        action = raw.get("action")
        spec = ACTIONS.get(action)
        if not spec or spec.channel != "browser" or spec.computer_use:
            raise ValueError("legacy executable procedures may contain browser actions only")
        params = raw.get("params")
        rationale = raw.get("rationale")
        postconditions = raw.get("postconditions", [])
        if not isinstance(params, dict) or not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("legacy browser step is incomplete")
        if not isinstance(postconditions, list) or not all(isinstance(item, dict) for item in postconditions):
            raise ValueError("legacy browser postconditions must be objects")
        try:
            params = validate_params(action, params)
        except ActionValidationError as exc:
            raise ValueError(str(exc)) from exc
        clean_steps.append({
            "action": action,
            "params": params,
            "rationale": rationale,
            "postconditions": postconditions,
        })
    return {"procedure_schema": 1, "kind": "browser", "procedure": clean_steps}


def publication_hash(body: dict) -> str:
    """Fingerprint either the typed desktop contract or legacy browser contract."""
    if isinstance(body, dict) and body.get("procedure_schema") == 2:
        return procedure_hash(validate_executable_body(body))
    return sha256_hex(canonical_json(_legacy_browser_payload(body)))


def load_published_procedure(article: KnowledgeArticle) -> ProcedureDefinition:
    if article.status != "published" or not article.executable or not article.procedure_hash:
        raise ValueError("article is not a fingerprinted executable procedure")
    definition = validate_executable_body(article.body or {})
    if procedure_hash(definition) != article.procedure_hash:
        raise ValueError("published procedure fingerprint mismatch")
    return definition
