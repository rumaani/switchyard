"""Workflow definition schema, templating and validation.

A workflow is an ordered list of steps. Each step is either

  tool      - call `tool` on the named `endpoint` with templated `args`
  approval  - pause until someone with runs.approve decides (segregation of duties:
              the person who started the run cannot approve it unless allowed)

Templates use a sandboxed Jinja2 environment. Context: `inputs`, `steps.<id>.output`,
`steps.<id>.status`, `run.id`, `run.started_by`. A string that is exactly one
expression, e.g. "{{ steps.pull.output.rows }}", keeps its native type (list, number, ...).
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from jinja2 import StrictUndefined, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field, field_validator, model_validator

STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
WHOLE_EXPR_RE = re.compile(r"^\s*\{\{(.*)\}\}\s*$", re.S)

env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
env.filters["json"] = lambda v: json.dumps(v, default=str)


class InputDef(BaseModel):
    name: str
    label: str = ""
    type: Literal["string", "number", "boolean", "json"] = "string"
    default: Any = None
    required: bool = True
    help: str = ""

    @field_validator("name")
    @classmethod
    def _n(cls, v: str) -> str:
        if not STEP_ID_RE.match(v):
            raise ValueError(f"input name '{v}' must be lowercase letters, digits or _")
        return v


class StepDef(BaseModel):
    id: str
    type: Literal["tool", "approval"] = "tool"
    title: str = ""
    when: str = ""  # optional Jinja expression; step is skipped when falsy
    # tool steps
    endpoint: str = ""
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    retries: int = Field(default=0, ge=0, le=5)
    retry_delay_s: float = Field(default=2.0, ge=0, le=300)
    continue_on_error: bool = False
    # approval steps
    message: str = ""
    allow_self_approval: bool = False

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not STEP_ID_RE.match(v):
            raise ValueError(f"step id '{v}' must be lowercase letters, digits or _ (start with a letter)")
        return v

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "tool" and not (self.endpoint and self.tool):
            raise ValueError(f"step '{self.id}': tool steps need an endpoint and a tool")
        return self


class WorkflowDef(BaseModel):
    inputs: list[InputDef] = Field(default_factory=list)
    steps: list[StepDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self):
        seen: set[str] = set()
        for s in self.steps:
            if s.id in seen:
                raise ValueError(f"duplicate step id '{s.id}'")
            seen.add(s.id)
        names = [i.name for i in self.inputs]
        if len(names) != len(set(names)):
            raise ValueError("duplicate input names")
        return self


# --- templating --------------------------------------------------------------------

def render(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return value
        m = WHOLE_EXPR_RE.match(value)
        if m and "{{" not in m.group(1) and "}}" not in m.group(1):
            return env.compile_expression(m.group(1).strip(), undefined_to_none=False)(**ctx)
        return env.from_string(value).render(**ctx)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def evaluate(expr: str, ctx: dict[str, Any]) -> bool:
    expr = expr.strip()
    if not expr:
        return True
    if expr.startswith("{{") and expr.endswith("}}"):
        expr = expr[2:-2]
    return bool(env.compile_expression(expr.strip(), undefined_to_none=False)(**ctx))


def _iter_templates(value: Any):
    if isinstance(value, str):
        if "{{" in value or "{%" in value:
            yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_templates(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_templates(v)


def coerce_inputs(defn: WorkflowDef, given: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i in defn.inputs:
        v = given.get(i.name, i.default)
        if v in (None, "") and i.required:
            raise ValueError(f"Input '{i.label or i.name}' is required")
        if v in (None, ""):
            out[i.name] = v
            continue
        try:
            if i.type == "number":
                f = float(v)
                v = int(f) if f.is_integer() else f
            elif i.type == "boolean":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "y", "on")
            elif i.type == "json" and isinstance(v, str):
                v = json.loads(v)
            elif i.type == "string":
                v = str(v)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Input '{i.label or i.name}' is not a valid {i.type}") from exc
        out[i.name] = v
    return out


def validate(definition: dict[str, Any], endpoints: dict[str, set[str]]) -> dict[str, list[str]]:
    """Return {errors, warnings}. endpoints = {endpoint_name: {tool names}} from the registry."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        wf = WorkflowDef.model_validate(definition)
    except Exception as exc:  # pydantic ValidationError -> readable lines
        errs = getattr(exc, "errors", None)
        if callable(errs):
            for e in errs():
                loc = ".".join(str(x) for x in e.get("loc", []))
                errors.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
        else:
            errors.append(str(exc))
        return {"errors": errors, "warnings": warnings}
    if not wf.steps:
        warnings.append("Workflow has no steps yet")
    earlier: set[str] = set()
    for s in wf.steps:
        if s.type == "tool":
            if s.endpoint not in endpoints:
                errors.append(f"step '{s.id}': endpoint '{s.endpoint}' is not registered")
            elif endpoints[s.endpoint] and s.tool not in endpoints[s.endpoint]:
                warnings.append(f"step '{s.id}': tool '{s.tool}' is not in {s.endpoint}'s current tool list")
        for tpl in list(_iter_templates(s.args)) + [t for t in (s.message, s.when) if t]:
            try:
                env.parse(tpl if ("{{" in tpl or "{%" in tpl) else "{{ " + tpl + " }}")
            except TemplateSyntaxError as exc:
                errors.append(f"step '{s.id}': template error: {exc.message}")
            for ref in re.findall(r"steps\.([a-z][a-z0-9_]*)", tpl):
                if ref not in earlier:
                    errors.append(f"step '{s.id}' refers to step '{ref}', which does not run before it")
        earlier.add(s.id)
    return {"errors": errors, "warnings": warnings}
