// Thin fetch wrapper. Every state-changing call carries the CSRF header the backend requires.

export class ApiError extends Error {
  status: number;
  details: { errors?: string[]; warnings?: string[] } | null;
  constructor(status: number, message: string, details: ApiError["details"] = null) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "switchyard" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      throw new ApiError(res.status, detail.message || "Request failed", detail);
    }
    if (Array.isArray(detail)) {
      // pydantic validation errors
      const msg = detail.map((d: any) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
      throw new ApiError(res.status, msg);
    }
    throw new ApiError(res.status, detail || res.statusText || "Request failed");
  }
  return data as T;
}

function safeJson(t: string) {
  try {
    return JSON.parse(t);
  } catch {
    return { detail: t };
  }
}

export const api = {
  get: <T = any>(p: string) => request<T>("GET", p),
  post: <T = any>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T = any>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T = any>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T = any>(p: string) => request<T>("DELETE", p),
};

// ---- types ------------------------------------------------------------------------
export interface Me {
  id: number;
  email: string;
  name: string;
  groups: string[];
  roles: string[];
  permissions: string[];
  is_admin: boolean;
}

export interface Tool {
  name: string;
  title: string;
  description: string;
  inputSchema: any;
  readOnly: boolean;
  destructive: boolean;
  allowed: boolean;
}

export interface Endpoint {
  id: number;
  name: string;
  display_name: string;
  description: string;
  url: string;
  auth_type: string;
  auth_config: Record<string, string>;
  extra_headers: Record<string, string>;
  enabled: boolean;
  status: "ok" | "error" | "unknown";
  status_detail: string;
  last_checked_at: string | null;
  tool_count: number;
  allowed_tool_count: number;
  server: { name?: string; version?: string };
  tools?: Tool[];
}

export interface InputDef {
  name: string;
  label?: string;
  type: "string" | "number" | "boolean" | "json";
  default?: any;
  required?: boolean;
  help?: string;
}

export interface StepDef {
  id: string;
  type: "tool" | "approval";
  title?: string;
  when?: string;
  endpoint?: string;
  tool?: string;
  args?: Record<string, any>;
  retries?: number;
  retry_delay_s?: number;
  continue_on_error?: boolean;
  message?: string;
  allow_self_approval?: boolean;
}

export interface Workflow {
  id: number;
  name: string;
  description: string;
  definition: { inputs: InputDef[]; steps: StepDef[] };
  allowed_roles: string[];
  version: number;
  updated_at: string;
  updated_by: string;
  can_run: boolean;
  can_edit: boolean;
  step_count: number;
  last_run: { id: number; status: string; created_at: string } | null;
}

export interface RunStep {
  step_id: string;
  type: string;
  title: string;
  status: string;
  endpoint: string;
  tool: string;
  request: any;
  output: any;
  error: string;
  attempts: number;
  decided_by: string;
  decision_note: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  can_decide: boolean;
}

export interface Run {
  id: number;
  workflow_id: number | null;
  workflow_name: string;
  workflow_version: number;
  status: string;
  started_by: string;
  created_at: string;
  finished_at: string | null;
  error: string;
  inputs: Record<string, any>;
  progress: { done: number; total: number };
  steps?: RunStep[];
}

export interface Role {
  id: number;
  name: string;
  description: string;
  permissions: string[];
  builtin: boolean;
  grants: { id: number; endpoint_id: number; endpoint: string; tools: string[] }[];
}

export const TERMINAL = new Set(["succeeded", "failed", "rejected", "cancelled"]);

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return t.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
