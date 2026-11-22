import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

// ---- toasts --------------------------------------------------------------------------
type Toast = { id: number; kind: "ok" | "error"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---- primitives ----------------------------------------------------------------------
const STATUS_LABEL: Record<string, string> = {
  ok: "Connected",
  error: "Error",
  unknown: "Not checked",
  waiting_approval: "Needs approval",
  succeeded: "Succeeded",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
  running: "Running",
  queued: "Queued",
  pending: "Pending",
  skipped: "Skipped",
};

export function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}>{STATUS_LABEL[value] ?? value}</span>;
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: string; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="row gap-s">{actions}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="spinner-wrap">
      <span className="spinner" aria-hidden /> {label}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Drawer({ title, onClose, children, footer, wide }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className={`drawer ${wide ? "drawer-wide" : ""}`} role="dialog" aria-label={title}>
        <header className="drawer-head">
          <h2>{title}</h2>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="drawer-body">{children}</div>
        {footer && <footer className="drawer-foot">{footer}</footer>}
      </aside>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { id: T; label: ReactNode }[]; value: T; onChange: (t: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "tab active" : "tab"}
          onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---- value rendering -------------------------------------------------------------------
function isFlatRecordArray(v: any): v is Record<string, any>[] {
  return Array.isArray(v) && v.length > 0 && v.length <= 500 &&
    v.every((r) => r && typeof r === "object" && !Array.isArray(r) &&
      Object.values(r).every((x) => x === null || typeof x !== "object"));
}

/** Shows tool output the way a finance user expects: tables for row sets, JSON otherwise. */
export function ValueView({ value }: { value: any }) {
  const [raw, setRaw] = useState(false);
  if (value === null || value === undefined) return <span className="muted">No output</span>;
  if (typeof value === "string") return <pre className="code">{value}</pre>;

  const tables: [string, Record<string, any>[]][] = [];
  const scalars: [string, any][] = [];
  if (isFlatRecordArray(value)) tables.push(["", value]);
  else if (typeof value === "object" && !Array.isArray(value)) {
    for (const [k, v] of Object.entries(value)) {
      if (isFlatRecordArray(v)) tables.push([k, v]);
      else if (v === null || typeof v !== "object") scalars.push([k, v]);
    }
  }
  const friendly = tables.length > 0 || scalars.length > 0;
  return (
    <div className="value-view">
      {friendly && (
        <div className="row right">
          <button className="link small" onClick={() => setRaw(!raw)}>{raw ? "Formatted view" : "Raw JSON"}</button>
        </div>
      )}
      {friendly && !raw ? (
        <>
          {scalars.length > 0 && (
            <dl className="kv">
              {scalars.map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{fmt(v)}</dd></div>
              ))}
            </dl>
          )}
          {tables.map(([k, rows]) => <DataTable key={k} caption={k} rows={rows} />)}
        </>
      ) : (
        <pre className="code">{JSON.stringify(value, null, 2)}</pre>
      )}
    </div>
  );
}

function fmt(v: any) {
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v ?? "—");
}

export function DataTable({ rows, caption }: { rows: Record<string, any>[]; caption?: string }) {
  const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  const csv = () => {
    const esc = (x: any) => `"${String(x ?? "").replace(/"/g, '""')}"`;
    const text = [cols.map(esc).join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = `${caption || "data"}.csv`;
    a.click();
  };
  return (
    <div className="table-block">
      <div className="row between">
        <span className="small muted">{caption ? `${caption} · ` : ""}{rows.length} rows</span>
        <button className="link small" onClick={csv}>Download CSV</button>
      </div>
      <div className="table-scroll">
        <table className="table">
          <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{cols.map((c) => <td key={c} className={typeof r[c] === "number" ? "num" : ""}>{fmt(r[c])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
