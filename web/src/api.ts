// Typed client for the CookSprite workflow server.
// All calls go through the /api prefix (proxied to :8000 in dev).

export type ParamType = "string" | "int" | "bool";

export interface ParamSpec {
  type: ParamType;
  default?: string | number | boolean;
  label?: string;
}

export type ParamsSchema = Record<string, ParamSpec>;

export interface Workflow {
  name: string;
  default: boolean;
  params_schema: ParamsSchema;
}

export interface Capability {
  id: string;
  description: string;
  workflows: Workflow[];
}

export interface CapabilitiesResponse {
  capabilities: Capability[];
}

export type ParamValue = string | number | boolean;
export type ParamMap = Record<string, ParamValue>;

export interface RunRequest {
  capability: string;
  workflow?: string;
  params: ParamMap;
  inputs?: Record<string, unknown>;
}

export interface RunHandle {
  run_id: string;
}

export type RunStatus = "queued" | "running" | "done" | "error";

export type ArtifactKind = "sprite_pair" | "image" | "sprite_sheet";

export interface Artifact {
  id: string;
  kind: ArtifactKind;
  diffuse_url?: string;
  normal_url?: string;
  url?: string;
  frames?: number;
  frame_w?: number;
  frame_h?: number;
  meta?: Record<string, unknown>;
}

export interface RunResult {
  artifacts: Artifact[];
}

export interface RunState {
  run_id: string;
  status: RunStatus;
  progress: number; // 0..1
  message: string;
  result?: RunResult;
}

const BASE = "/api";

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${init?.method ?? "GET"} ${url} failed: ${res.status} ${text}`);
  }
  return (await res.json()) as T;
}

export function getCapabilities(): Promise<CapabilitiesResponse> {
  return jsonFetch<CapabilitiesResponse>(`${BASE}/capabilities`);
}

export function startRun(req: RunRequest): Promise<RunHandle> {
  return jsonFetch<RunHandle>(`${BASE}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

export function getRun(runId: string): Promise<RunState> {
  return jsonFetch<RunState>(`${BASE}/runs/${encodeURIComponent(runId)}`);
}

export function getRunResult(runId: string): Promise<RunResult> {
  return jsonFetch<RunResult>(`${BASE}/runs/${encodeURIComponent(runId)}/result`);
}

export function artifactUrl(id: string): string {
  return `${BASE}/artifacts/${encodeURIComponent(id)}`;
}

// Resolve an artifact's diffuse image URL, preferring explicit fields.
export function diffuseUrlOf(a: Artifact): string | undefined {
  if (a.diffuse_url) return a.diffuse_url;
  if (a.url) return a.url;
  return artifactUrl(a.id);
}

export function normalUrlOf(a: Artifact): string | undefined {
  return a.normal_url;
}

// SSE subscription with automatic polling fallback.
// Returns an unsubscribe function.
export function subscribeRun(
  runId: string,
  onUpdate: (state: RunState) => void,
  onError?: (err: Error) => void
): () => void {
  let closed = false;
  let pollTimer: number | undefined;
  let source: EventSource | undefined;

  const stopPolling = () => {
    if (pollTimer !== undefined) {
      window.clearInterval(pollTimer);
      pollTimer = undefined;
    }
  };

  const startPolling = () => {
    if (closed || pollTimer !== undefined) return;
    const tick = () => {
      if (closed) return;
      getRun(runId)
        .then((state) => {
          if (closed) return;
          onUpdate(state);
          if (state.status === "done" || state.status === "error") {
            stopPolling();
          }
        })
        .catch((err: unknown) => {
          if (!closed && onError) onError(err instanceof Error ? err : new Error(String(err)));
        });
    };
    tick();
    pollTimer = window.setInterval(tick, 700);
  };

  try {
    source = new EventSource(`${BASE}/runs/${encodeURIComponent(runId)}/events`);
    source.onmessage = (ev: MessageEvent<string>) => {
      if (closed) return;
      try {
        const state = JSON.parse(ev.data) as RunState;
        onUpdate(state);
        if (state.status === "done" || state.status === "error") {
          source?.close();
        }
      } catch {
        // Ignore malformed lines (e.g. keep-alive comments).
      }
    };
    source.onerror = () => {
      // EventSource failed or the stream ended; fall back to polling.
      source?.close();
      source = undefined;
      startPolling();
    };
  } catch (err) {
    if (onError) onError(err instanceof Error ? err : new Error(String(err)));
    startPolling();
  }

  return () => {
    closed = true;
    source?.close();
    stopPolling();
  };
}
