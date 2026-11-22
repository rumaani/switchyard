import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type Endpoint, type InputDef, type Role, type StepDef, type Workflow } from "../api";
import { Badge, Field, PageHeader, Spinner, Tabs, useToast } from "../components/ui";

type Def = { inputs: InputDef[]; steps: StepDef[] };

function skeletonArgs(schema: any): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, p] of Object.entries<any>(schema?.properties ?? {})) {
    if ((schema.required ?? []).includes(k)) out[k] = p.default ?? (p.type === "array" ? [] : p.type === "number" || p.type === "integer" ? 0 : "");
  }
  return out;
}

export default function WorkflowEditor() {
  const { id } = useParams();
  const isNew = !id;
  const nav = useNavigate();
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  const [def, setDef] = useState<Def>({ inputs: [], steps: [] });
  const [allRoles, setAllRoles] = useState<Role[]>([]);
  const [eps, setEps] = useState<Endpoint[]>([]);
  const [tab, setTab] = useState<"steps" | "inputs" | "json">("steps");
  const [check, setCheck] = useState<{ errors: string[]; warnings: string[] } | null>(null);
  const [loaded, setLoaded] = useState(isNew);
  const [busy, setBusy] = useState(false);
  const [json, setJson] = useState("");
  const [jsonErr, setJsonErr] = useState("");

  useEffect(() => {
    api.get<Endpoint[]>("/api/endpoints").then(async (list) => {
      // fetch tool lists so step editors can offer tool pickers
      setEps(await Promise.all(list.map((e) => api.get<Endpoint>(`/api/endpoints/${e.id}`))));
    });
    api.get<Role[]>("/api/access/roles").then(setAllRoles);
    if (!isNew) {
      api.get<Workflow>(`/api/workflows/${id}`).then((w) => {
        setName(w.name); setDescription(w.description); setRoles(w.allowed_roles);
        setDef({ inputs: w.definition.inputs ?? [], steps: w.definition.steps ?? [] });
        setLoaded(true);
      });
    }
  }, [id, isNew]);

  useEffect(() => { if (tab === "json") { setJson(JSON.stringify(def, null, 2)); setJsonErr(""); } }, [tab]); // eslint-disable-line

  const epMap = useMemo(() => Object.fromEntries(eps.map((e) => [e.name, e])), [eps]);

  if (!loaded) return <Spinner />;

  const setStep = (i: number, patch: Partial<StepDef>) =>
    setDef((d) => ({ ...d, steps: d.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)) }));
  const move = (i: number, dir: -1 | 1) => setDef((d) => {
    const steps = [...d.steps];
    const j = i + dir;
    if (j < 0 || j >= steps.length) return d;
    [steps[i], steps[j]] = [steps[j], steps[i]];
    return { ...d, steps };
  });
  const nextId = (base: string) => {
    let n = def.steps.length + 1;
    while (def.steps.some((s) => s.id === `${base}${n}`)) n++;
    return `${base}${n}`;
  };
  const addStep = (type: "tool" | "approval") => setDef((d) => ({
    ...d,
    steps: [...d.steps, type === "tool"
      ? { id: nextId("step"), type, title: "", endpoint: eps[0]?.name ?? "", tool: "", args: {} }
      : { id: nextId("approve"), type, title: "Approval", message: "Approve to continue?" }],
  }));

  const payload = () => ({ name, description, allowed_roles: roles, definition: def });
  const validate = async () => {
    const r = await api.post("/api/workflows/validate", payload());
    setCheck(r);
    return r;
  };
  const save = async () => {
    setBusy(true);
    try {
      const w = isNew ? await api.post<Workflow>("/api/workflows", payload()) : await api.put<Workflow>(`/api/workflows/${id}`, payload());
      toast("ok", `Saved (version ${w.version})`);
      setCheck(null);
      if (isNew) nav(`/workflows/${w.id}`, { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.details?.errors) setCheck({ errors: e.details.errors, warnings: e.details.warnings ?? [] });
      toast("error", (e as Error).message);
    }
    setBusy(false);
  };
  const remove = async () => {
    if (!confirm(`Delete workflow "${name}"? Past runs stay in history.`)) return;
    await api.del(`/api/workflows/${id}`);
    nav("/workflows");
  };

  const refs = [
    ...def.inputs.map((i) => `inputs.${i.name}`),
    ...def.steps.map((s) => `steps.${s.id}.output`),
  ];

  return (
    <>
      <PageHeader title={isNew ? "New workflow" : `Edit: ${name}`}
        actions={<>
          {!isNew && <button className="btn btn-danger-ghost" onClick={remove}>Delete</button>}
          <button className="btn" onClick={validate}>Check</button>
          <button className="btn btn-primary" disabled={busy || !name} onClick={save}>{busy ? "Saving…" : "Save"}</button>
        </>} />

      <div className="card stack">
        <div className="grid-2 tight">
          <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Month-end actuals load" /></Field>
          <Field label="Who can run it" hint="No selection = anyone with the workflows.run permission">
            <div className="row gap-s wrap">
              {allRoles.map((r) => (
                <label key={r.id} className="chip-check">
                  <input type="checkbox" checked={roles.includes(r.name)}
                    onChange={(e) => setRoles(e.target.checked ? [...roles, r.name] : roles.filter((x) => x !== r.name))} />
                  {r.name}
                </label>
              ))}
            </div>
          </Field>
        </div>
        <Field label="Description"><textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      </div>

      {check && (check.errors.length > 0 || check.warnings.length > 0) ? (
        <div className={`alert ${check.errors.length ? "alert-error" : "alert-warn"}`}>
          <ul className="plain">
            {check.errors.map((e) => <li key={e}>✗ {e}</li>)}
            {check.warnings.map((w) => <li key={w}>⚠ {w}</li>)}
          </ul>
        </div>
      ) : check ? <div className="alert alert-ok">Looks good.</div> : null}

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "steps", label: <>Steps <Badge>{def.steps.length}</Badge></> },
        { id: "inputs", label: <>Inputs <Badge>{def.inputs.length}</Badge></> },
        { id: "json", label: "JSON" },
      ]} />

      {tab === "steps" && (
        <div className="stack">
          {def.steps.length === 0 && <div className="empty"><strong>No steps yet</strong><span className="muted">Add a tool call or an approval below.</span></div>}
          {def.steps.map((s, i) => (
            <StepEditor key={i} index={i} step={s} eps={eps} ep={epMap[s.endpoint ?? ""]} refs={refs}
              onChange={(p) => setStep(i, p)} onMove={(d) => move(i, d)}
              onDelete={() => setDef((d) => ({ ...d, steps: d.steps.filter((_, j) => j !== i) }))} />
          ))}
          <div className="row gap-s">
            <button className="btn" onClick={() => addStep("tool")}>+ Tool step</button>
            <button className="btn" onClick={() => addStep("approval")}>+ Approval</button>
          </div>
          <details className="small muted help-box">
            <summary>How to pass data between steps</summary>
            <p>Arguments and messages can use <code>{"{{ … }}"}</code> templates. A value that is exactly one template keeps its type (a list stays a list).</p>
            <p>Available here: {refs.length ? refs.map((r) => <code key={r}>{r}</code>) : "add inputs or steps first"}</p>
            <p>Examples: <code>{"{{ inputs.period }}"}</code>, <code>{"{{ steps.pull.output.rows }}"}</code>, condition <code>{"steps.variance.output.breaches | length > 0"}</code></p>
          </details>
        </div>
      )}

      {tab === "inputs" && (
        <div className="card">
          <table className="table">
            <thead><tr><th>Name</th><th>Label</th><th>Type</th><th>Default</th><th>Required</th><th /></tr></thead>
            <tbody>
              {def.inputs.map((inp, i) => {
                const upd = (p: Partial<InputDef>) => setDef((d) => ({ ...d, inputs: d.inputs.map((x, j) => (j === i ? { ...x, ...p } : x)) }));
                return (
                  <tr key={i}>
                    <td><input className="mono" value={inp.name} onChange={(e) => upd({ name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} /></td>
                    <td><input value={inp.label ?? ""} onChange={(e) => upd({ label: e.target.value })} /></td>
                    <td>
                      <select value={inp.type} onChange={(e) => upd({ type: e.target.value as InputDef["type"] })}>
                        {["string", "number", "boolean", "json"].map((t) => <option key={t}>{t}</option>)}
                      </select>
                    </td>
                    <td><input value={inp.default ?? ""} onChange={(e) => upd({ default: e.target.value })} /></td>
                    <td><input type="checkbox" checked={inp.required ?? true} onChange={(e) => upd({ required: e.target.checked })} /></td>
                    <td><button className="btn btn-ghost" onClick={() => setDef((d) => ({ ...d, inputs: d.inputs.filter((_, j) => j !== i) }))}>✕</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <button className="btn" onClick={() => setDef((d) => ({ ...d, inputs: [...d.inputs, { name: `input${d.inputs.length + 1}`, label: "", type: "string", required: true }] }))}>+ Input</button>
        </div>
      )}

      {tab === "json" && (
        <div className="card stack">
          <textarea className="mono" rows={24} value={json} spellCheck={false} onChange={(e) => {
            setJson(e.target.value);
            try { const v = JSON.parse(e.target.value); setDef({ inputs: v.inputs ?? [], steps: v.steps ?? [] }); setJsonErr(""); }
            catch { setJsonErr("Not valid JSON — changes not applied yet"); }
          }} />
          {jsonErr && <span className="error-text small">{jsonErr}</span>}
        </div>
      )}
    </>
  );
}

function StepEditor({ index, step, eps, ep, refs, onChange, onMove, onDelete }: {
  index: number; step: StepDef; eps: Endpoint[]; ep?: Endpoint; refs: string[];
  onChange: (p: Partial<StepDef>) => void; onMove: (d: -1 | 1) => void; onDelete: () => void;
}) {
  const [argsText, setArgsText] = useState(JSON.stringify(step.args ?? {}, null, 2));
  const [argsErr, setArgsErr] = useState("");
  useEffect(() => { setArgsText(JSON.stringify(step.args ?? {}, null, 2)); }, [step.tool, step.endpoint]); // eslint-disable-line
  const tool = ep?.tools?.find((t) => t.name === step.tool);
  void refs;

  return (
    <div className={`card step-editor ${step.type}`}>
      <div className="row between">
        <div className="row gap-s">
          <span className="step-num">{index + 1}</span>
          <strong>{step.type === "approval" ? "✋ Approval" : "Tool call"}</strong>
          {tool?.destructive && <Badge tone="danger">destructive</Badge>}
          {tool?.readOnly && <Badge tone="ok">read-only</Badge>}
        </div>
        <div className="row gap-xs">
          <button className="btn btn-ghost" onClick={() => onMove(-1)} title="Move up">↑</button>
          <button className="btn btn-ghost" onClick={() => onMove(1)} title="Move down">↓</button>
          <button className="btn btn-ghost" onClick={onDelete} title="Remove step">✕</button>
        </div>
      </div>
      <div className="grid-3 tight">
        <Field label="Title"><input value={step.title ?? ""} onChange={(e) => onChange({ title: e.target.value })} /></Field>
        <Field label="Step id" hint="Referenced as steps.<id>">
          <input className="mono" value={step.id} onChange={(e) => onChange({ id: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} />
        </Field>
        <Field label="Run only if (optional)" hint="Jinja expression">
          <input className="mono" value={step.when ?? ""} placeholder="always" onChange={(e) => onChange({ when: e.target.value })} />
        </Field>
      </div>
      {step.type === "tool" ? (
        <>
          <div className="grid-2 tight">
            <Field label="Endpoint">
              <select value={step.endpoint} onChange={(e) => onChange({ endpoint: e.target.value, tool: "" })}>
                <option value="">— choose —</option>
                {eps.map((e) => <option key={e.name} value={e.name}>{e.display_name} ({e.name})</option>)}
              </select>
            </Field>
            <Field label="Tool" hint={tool?.description}>
              <select value={step.tool} onChange={(e) => {
                const t = ep?.tools?.find((x) => x.name === e.target.value);
                onChange({ tool: e.target.value, args: Object.keys(step.args ?? {}).length ? step.args : skeletonArgs(t?.inputSchema), title: step.title || t?.title || "" });
              }}>
                <option value="">— choose —</option>
                {(ep?.tools ?? []).map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                {step.tool && !ep?.tools?.some((t) => t.name === step.tool) && <option value={step.tool}>{step.tool} (not found)</option>}
              </select>
            </Field>
          </div>
          <Field label="Arguments (JSON, templates allowed)"
            hint={tool ? <>Parameters: {Object.keys(tool.inputSchema?.properties ?? {}).map((k) => <code key={k}>{k}</code>)}</> : undefined}>
            <textarea className="mono" rows={Math.min(10, Math.max(3, argsText.split("\n").length))} value={argsText} spellCheck={false}
              onChange={(e) => {
                setArgsText(e.target.value);
                try { onChange({ args: JSON.parse(e.target.value || "{}") }); setArgsErr(""); } catch { setArgsErr("Not valid JSON yet"); }
              }} />
          </Field>
          {argsErr && <span className="error-text small">{argsErr}</span>}
          <div className="row gap-m wrap small">
            <label className="row gap-xs">Retries
              <input type="number" min={0} max={5} style={{ width: 60 }} value={step.retries ?? 0} onChange={(e) => onChange({ retries: Number(e.target.value) })} />
            </label>
            <label className="row gap-xs"><input type="checkbox" checked={!!step.continue_on_error} onChange={(e) => onChange({ continue_on_error: e.target.checked })} /> Continue if this step fails</label>
          </div>
        </>
      ) : (
        <>
          <Field label="Message shown to the approver" hint="Templates allowed, e.g. totals from earlier steps">
            <textarea rows={2} value={step.message ?? ""} onChange={(e) => onChange({ message: e.target.value })} />
          </Field>
          <label className="row gap-xs small">
            <input type="checkbox" checked={!!step.allow_self_approval} onChange={(e) => onChange({ allow_self_approval: e.target.checked })} />
            Let the person who started the run approve it (turns off segregation of duties)
          </label>
        </>
      )}
    </div>
  );
}
