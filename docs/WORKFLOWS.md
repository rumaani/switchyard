# Writing workflows

A workflow is a list of **inputs** and an ordered list of **steps**. Build it in the editor, or paste JSON in the
editor's JSON tab.

```json
{
  "inputs": [
    { "name": "period", "label": "Period", "type": "string", "default": "Oct-2026" },
    { "name": "threshold_pct", "type": "number", "default": 5 }
  ],
  "steps": [
    { "id": "pull", "type": "tool", "endpoint": "erp", "tool": "get_actuals",
      "args": { "period": "{{ inputs.period }}" }, "retries": 2 },
    { "id": "review", "type": "approval",
      "message": "Load {{ steps.pull.output.row_count }} rows for {{ inputs.period }}?" },
    { "id": "load", "type": "tool", "endpoint": "essbase", "tool": "load_data",
      "args": { "cube": "Finance.Plan", "rows": "{{ steps.pull.output.rows }}" } },
    { "id": "ticket", "type": "tool", "endpoint": "servicenow", "tool": "create_ticket",
      "when": "steps.load.output.rejected > 0",
      "args": { "title": "{{ steps.load.output.rejected }} rows rejected for {{ inputs.period }}" } }
  ]
}
```

## Steps

| Field | Applies to | Meaning |
|---|---|---|
| `id` | all | Lowercase name, used as `steps.<id>` by later steps |
| `type` | all | `tool` or `approval` |
| `title` | all | Shown on the run timeline |
| `when` | all | Optional condition; the step is skipped when it is false |
| `endpoint`, `tool`, `args` | tool | Which endpoint (by short name), which tool, and its arguments |
| `retries`, `retry_delay_s` | tool | Retry on error (0–5) |
| `continue_on_error` | tool | Record the failure and keep going |
| `message` | approval | What the approver sees (templates allowed) |
| `allow_self_approval` | approval | Let the run's starter approve (off by default) |

## Templates

Anything inside `{{ … }}` is evaluated with these variables:

- `inputs.<name>` — run inputs
- `steps.<id>.output` — the tool's result (its structured content, or its text parsed as JSON when possible)
- `steps.<id>.status` — `succeeded`, `failed`, `skipped`
- `run.id`, `run.started_by`, `run.workflow`

A value that is **only** a template keeps its type: `"{{ steps.pull.output.rows }}"` passes a list, not text.
Mixed text is rendered to a string. Loops and filters work: `{% for b in steps.v.output.breaches %}…{% endfor %}`,
`| length`, `| round(2)`, `| json`.

A step can only refer to steps that run before it — **Check** in the editor catches mistakes, unknown endpoints and
tools that no longer exist.

## Who can run what

- *Who can run it* on the workflow limits which roles may start it (empty = anyone with `workflows.run`).
- Each tool step also needs the starter's roles to be granted that tool on that endpoint (Access → Endpoint grants).
  This is checked when the step executes, not when the run starts.
- Approvals need `runs.approve`, and by default someone other than the starter.
