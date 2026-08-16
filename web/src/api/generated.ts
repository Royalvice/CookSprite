/* Public /api/v1 client. The browser never talks to ComfyUI. */
export type Locale = "zh-CN" | "en";
export type ProjectType = "static" | "character" | "tileset";
export type RunStatus = "queued" | "running" | "cancel_requested" | "cancelled" | "succeeded" | "failed";
export type ArtifactKind = "Image" | "ImageBatch" | "SpriteSheet" | "FrameSeq" | "Video" | "NormalMap" | "CookSpritePack" | string;

export interface LocalizedText { name: string; description: string }
export interface ActionInput { type: ArtifactKind | ArtifactKind[]; required: boolean; max: number }
export interface ActionOption { id: string; i18n: Record<Locale, LocalizedText>; example?: ArtifactRef }
export interface ActionControl {
  id: string;
  type: "select" | "multi-select" | "toggle" | "range" | "number" | "text" | "seed";
  default: unknown;
  options: ActionOption[];
  advanced: boolean;
  min?: number;
  max?: number;
  step?: number;
  i18n: Record<Locale, LocalizedText>;
}
export interface ModelOption { id: string; label: string; runtime_id: string; family: string; modes: string[] }
export interface ActionDescriptor {
  id: string;
  i18n: Record<Locale, LocalizedText>;
  accepts: Record<string, ActionInput>;
  produces: ArtifactKind[];
  controls: ActionControl[];
  available: boolean;
  unavailable_reason?: string;
  models: ModelOption[];
}
export interface ArtifactRef {
  id: string;
  sha256: string;
  media_type: string;
  size: number;
  kind: ArtifactKind;
  url: string;
  title: string;
  project_id?: string;
  favorite: boolean;
  trashed: boolean;
  meta: Record<string, unknown>;
  created_at: string;
}
export interface FrameSequenceManifest {
  schema: "cooksprite.frame-sequence/v1";
  action?: AnimationClip["action"];
  view?: "level" | "top45";
  direction?: Direction;
  frames: string[];
}
export interface FrameSequenceView {
  artifact: ArtifactRef;
  sequence: FrameSequenceManifest;
  frames: ArtifactRef[];
}
export interface RunView {
  id: string;
  status: RunStatus;
  progress: number;
  message: string;
  action_id?: string;
  project_id?: string;
  artifacts: ArtifactRef[];
  provenance: Record<string, unknown>;
  error?: { code: string; message: string; issues?: string[] };
  created_at: string;
  updated_at: string;
}
export interface QueueView { running: RunView[]; pending: RunView[]; history: RunView[]; runtime?: Record<string, unknown> | null }
export interface ProjectView {
  id: string;
  name: string;
  type: ProjectType;
  favorite: boolean;
  published: boolean;
  cover_artifact_id?: string;
  created_at: string;
  updated_at: string;
}
export interface FrameRef {
  id: string;
  artifact: string;
  normal?: string;
  duration_ms: number;
  offset_x: number;
  offset_y: number;
  source_artifact?: string;
  variant_of?: string;
}
export interface DirectionTrack { direction: Direction; frames: FrameRef[] }
export type Direction = "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "nw";
export interface ViewTrack { id: "level" | "top45"; enabled: boolean; tracks: DirectionTrack[] }
export interface AnimationClip {
  id: string;
  name: string;
  action: "idle" | "walk" | "run" | "attack" | "cast" | "hit" | "jump" | "death";
  loop: "none" | "linear" | "pingpong";
  views: ViewTrack[];
}
export interface SpriteDocument {
  schema: "cooksprite.sprite-document/v1";
  type: ProjectType;
  canvas: { width: number; height: number };
  static?: { primary?: string; normal?: string; pivot: { x: number; y: number } };
  character?: { pivot: { x: number; y: number }; clips: AnimationClip[] };
  tileset?: { source?: string; normal?: string; tile_width: number; tile_height: number; margin: number; spacing: number; exclude_empty: boolean };
  history: Record<string, unknown>[];
}
export interface DocumentView { document: SpriteDocument; revision: number; etag: string }
export interface GalleryItem { project: ProjectView; cover?: ArtifactRef; published_at: string }
export type RuntimeStatus = "unconfigured" | "offline" | "ready";
export interface HealthView {
  service: string;
  executor: "comfyui";
  runtime: RuntimeStatus;
  runtime_id?: string;
  checked_at: string;
  error?: string;
  actions: Record<string, { available: boolean; models: number; reason?: string }>;
  schema_version: number;
}
export interface RuntimeRecipe {
  id: string;
  label: string;
  family: string;
  actions: string[];
  modes: string[];
  checkpoint?: string;
  source: "discovered" | "imported" | string;
}
export interface RuntimeView {
  id: string;
  label: string;
  base_url: string;
  callback_url?: string;
  snapshot?: string;
  status?: RuntimeStatus;
  error?: string;
  checked_at?: string;
  recipes: RuntimeRecipe[];
}
export interface LocalSetupView {
  status: "idle" | "installed" | "installing" | "starting" | "validating" | "ready" | "failed";
  progress: number;
  message: string;
  error?: string;
  directory?: string;
  default_directory: string;
  model: { id: string; filename: string; size: number; license: string };
}

export class ApiError extends Error {
  constructor(public status: number, public detail: { code?: string; message?: string; [key: string]: unknown }) {
    super(detail.message || `CookSprite API error ${status}`);
  }
}

const BASE = "/api/v1";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, init);
  if (!response.ok) {
    let detail: ApiError["detail"] = { message: response.statusText };
    try { detail = (await response.json()).detail || detail; } catch { /* text-free error */ }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

const jsonBody = (value: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

export const api = {
  health: () => json<HealthView>("/health"),
  actions: () => json<ActionDescriptor[]>("/actions"),
  action: (id: string) => json<ActionDescriptor>(`/actions/${encodeURIComponent(id)}`),
  runAction: (id: string, body: { project: string; inputs: Record<string, string | string[]>; values: Record<string, unknown> }) =>
    json<RunView>(`/actions/${encodeURIComponent(id)}/runs`, { method: "POST", ...jsonBody(body) }),
  run: (id: string) => json<RunView>(`/runs/${encodeURIComponent(id)}`),
  cancel: (id: string) => json<RunView>(`/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  retry: (id: string) => json<RunView>(`/runs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  queue: () => json<QueueView>("/queue"),
  projects: () => json<ProjectView[]>("/projects"),
  createProject: (body: { name?: string; type: ProjectType }) => json<ProjectView>("/projects", { method: "POST", ...jsonBody(body) }),
  project: (id: string) => json<ProjectView>(`/projects/${encodeURIComponent(id)}`),
  patchProject: (id: string, body: Partial<Pick<ProjectView, "name" | "type" | "favorite">>) =>
    json<ProjectView>(`/projects/${encodeURIComponent(id)}`, { method: "PATCH", ...jsonBody(body) }),
  document: (id: string) => json<DocumentView>(`/projects/${encodeURIComponent(id)}/document`),
  putDocument: (id: string, document: SpriteDocument, etag: string) =>
    json<DocumentView>(`/projects/${encodeURIComponent(id)}/document`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "If-Match": etag },
      body: JSON.stringify(document),
    }),
  projectArtifacts: (id: string) => json<ArtifactRef[]>(`/projects/${encodeURIComponent(id)}/artifacts`),
  materializeSequence: (id: string, body: { action: AnimationClip["action"]; view: "level" | "top45"; direction: Direction }) =>
    json<FrameSequenceView>(`/projects/${encodeURIComponent(id)}/sequences`, { method: "POST", ...jsonBody(body) }),
  exportProject: (id: string, allowIncomplete = false) =>
    json<RunView>(`/projects/${encodeURIComponent(id)}/exports`, {
      method: "POST",
      ...jsonBody({ allow_incomplete: allowIncomplete }),
    }),
  sequence: (id: string) => json<FrameSequenceView>(`/artifacts/${encodeURIComponent(id)}/sequence`),
  artifacts: (query = "") => json<ArtifactRef[]>(`/artifacts${query ? `?${query}` : ""}`),
  patchArtifact: (id: string, body: { favorite?: boolean; title?: string }) => json<ArtifactRef>(`/artifacts/${encodeURIComponent(id)}`, { method: "PATCH", ...jsonBody(body) }),
  uploadArtifact: async (file: File, projectId: string, kind: ArtifactKind) => {
    const params = new URLSearchParams({ project_id: projectId, kind, media_type: file.type || "application/octet-stream", title: file.name });
    return json<ArtifactRef>(`/artifacts?${params}`, { method: "POST", body: file });
  },
  trash: (id: string) => json<ArtifactRef>(`/artifacts/${encodeURIComponent(id)}/trash`, { method: "POST" }),
  restore: (id: string) => json<ArtifactRef>(`/artifacts/${encodeURIComponent(id)}/restore`, { method: "POST" }),
  publish: (projectId: string, coverArtifactId?: string) => json<ProjectView>(`/projects/${encodeURIComponent(projectId)}/publish`, { method: "POST", ...jsonBody({ cover_artifact_id: coverArtifactId }) }),
  gallery: () => json<GalleryItem[]>("/gallery"),
  runtimes: () => json<RuntimeView[]>("/runtimes"),
  createRuntime: (body: { id: string; label: string; base_url: string; callback_url?: string }) => json<RuntimeView>("/runtimes", { method: "POST", ...jsonBody(body) }),
  doctorRuntime: (id: string) => json<{ runtime_id: string; snapshot: string; tool_count: number; recipe_count: number; system: Record<string, unknown>; device?: Record<string, unknown>; models: Record<string, number>; recipes: RuntimeRecipe[] }>(`/runtimes/${encodeURIComponent(id)}/doctor`, { method: "POST" }),
  localSetup: () => json<LocalSetupView>("/setup/local"),
  installLocal: (body: { directory?: string; with_models: boolean; host?: string; port?: number }) => json<LocalSetupView>("/setup/local", { method: "POST", ...jsonBody(body) }),
};

export function subscribeRun(id: string, update: (run: RunView) => void, fail: (error: Error) => void): () => void {
  const events = new EventSource(`${BASE}/runs/${encodeURIComponent(id)}/events`);
  events.onmessage = (event) => {
    const run = JSON.parse(event.data) as RunView;
    update(run);
    if (["succeeded", "failed", "cancelled"].includes(run.status)) events.close();
  };
  events.onerror = () => { events.close(); fail(new Error("Run event stream disconnected")); };
  return () => events.close();
}

export function inferArtifactKind(file: File): ArtifactKind | null {
  const name = file.name.toLowerCase();
  if (["image/png", "image/jpeg", "image/webp", "image/svg+xml"].includes(file.type) || name.endsWith(".svg")) return name.includes("sheet") ? "SpriteSheet" : "Image";
  if (file.type === "image/gif" || file.type.startsWith("video/") || name.endsWith(".webm")) return "Video";
  if (name.endsWith(".hdr") || name.endsWith(".exr")) return "Image";
  return null;
}

export const dragPayload = (artifact: ArtifactRef) => JSON.stringify({ artifact_id: artifact.id, kind: artifact.kind });
