import { useEffect, useState } from "react";

/** Renders a simple form from a tool's JSON Schema, with a raw-JSON escape hatch. */
export function SchemaForm({ schema, value, onChange }: {
  schema: any; value: Record<string, any>; onChange: (v: Record<string, any>) => void;
}) {
  const props: Record<string, any> = schema?.properties ?? {};
  const required: string[] = schema?.required ?? [];
  const [rawMode, setRawMode] = useState(Object.keys(props).length === 0);
  const [rawText, setRawText] = useState(JSON.stringify(value, null, 2));
  const [rawError, setRawError] = useState("");

  useEffect(() => {
    if (!rawMode) setRawText(JSON.stringify(value, null, 2));
  }, [value, rawMode]);

  const set = (k: string, v: any) => {
    const next = { ...value };
    if (v === "" || v === undefined) delete next[k];
    else next[k] = v;
    onChange(next);
  };

  return (
    <div className="schema-form">
      {Object.keys(props).length > 0 && (
        <div className="row right">
          <button type="button" className="link small" onClick={() => setRawMode(!rawMode)}>
            {rawMode ? "Use form" : "Edit as JSON"}
          </button>
        </div>
      )}
      {rawMode ? (
        <>
          <textarea className="mono" rows={8} value={rawText} spellCheck={false}
            onChange={(e) => {
              setRawText(e.target.value);
              try {
                const v = e.target.value.trim() ? JSON.parse(e.target.value) : {};
                setRawError("");
                onChange(v);
              } catch {
                setRawError("Not valid JSON yet");
              }
            }} />
          {rawError && <span className="field-hint error-text">{rawError}</span>}
        </>
      ) : (
        Object.entries(props).map(([k, p]) => (
          <SchemaField key={k} name={k} prop={p} required={required.includes(k)} value={value[k]} onChange={(v) => set(k, v)} />
        ))
      )}
    </div>
  );
}

function SchemaField({ name, prop, required, value, onChange }: {
  name: string; prop: any; required: boolean; value: any; onChange: (v: any) => void;
}) {
  const label = (
    <span className="field-label">
      {prop.title || name}
      {required && <span className="req"> *</span>}
    </span>
  );
  const hint = prop.description && <span className="field-hint">{prop.description}</span>;
  const type = Array.isArray(prop.type) ? prop.type[0] : prop.type;

  if (prop.enum) {
    return (
      <label className="field">{label}
        <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {prop.enum.map((o: any) => <option key={String(o)} value={o}>{String(o)}</option>)}
        </select>{hint}
      </label>
    );
  }
  if (type === "boolean") {
    return (
      <label className="field check">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {label}{hint}
      </label>
    );
  }
  if (type === "number" || type === "integer") {
    return (
      <label className="field">{label}
        <input type="number" value={value ?? ""} placeholder={prop.default !== undefined ? String(prop.default) : ""}
          onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />{hint}
      </label>
    );
  }
  if (type === "object" || type === "array") {
    return <JsonField label={label} hint={hint} value={value} onChange={onChange} />;
  }
  return (
    <label className="field">{label}
      <input value={value ?? ""} placeholder={prop.default !== undefined ? String(prop.default) : ""}
        onChange={(e) => onChange(e.target.value)} />{hint}
    </label>
  );
}

function JsonField({ label, hint, value, onChange }: { label: any; hint: any; value: any; onChange: (v: any) => void }) {
  const [text, setText] = useState(value === undefined ? "" : JSON.stringify(value, null, 2));
  const [err, setErr] = useState("");
  return (
    <label className="field">{label}
      <textarea className="mono" rows={4} value={text} spellCheck={false} onChange={(e) => {
        setText(e.target.value);
        if (!e.target.value.trim()) { setErr(""); onChange(""); return; }
        try { onChange(JSON.parse(e.target.value)); setErr(""); } catch { setErr("Not valid JSON yet"); }
      }} />
      {err ? <span className="field-hint error-text">{err}</span> : hint}
    </label>
  );
}
