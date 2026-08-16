import { computed, ref } from "vue";
import { defineStore } from "pinia";
import {
  api,
  ApiError,
  inferArtifactKind,
  subscribeRun,
  type ActionDescriptor,
  type AnimationClip,
  type ArtifactKind,
  type ArtifactRef,
  type DocumentView,
  type Direction,
  type GalleryItem,
  type FrameSequenceView,
  type ProjectType,
  type ProjectView,
  type QueueView,
  type RunView,
  type RuntimeStatus,
  type SpriteDocument,
} from "../api/generated";

const EMPTY_QUEUE: QueueView = { running: [], pending: [], history: [] };

export const useStudioStore = defineStore("studio", () => {
  const actions = ref<ActionDescriptor[]>([]);
  const projects = ref<ProjectView[]>([]);
  const gallery = ref<GalleryItem[]>([]);
  const currentProject = ref<ProjectView | null>(null);
  const documentView = ref<DocumentView | null>(null);
  const artifacts = ref<ArtifactRef[]>([]);
  const allArtifacts = ref<ArtifactRef[]>([]);
  const queue = ref<QueueView>(EMPTY_QUEUE);
  const runtimeStatus = ref<RuntimeStatus>("unconfigured");
  const runtimeError = ref("");
  const runtimeReady = computed(() => runtimeStatus.value === "ready");
  const activeSequence = ref<FrameSequenceView | null>(null);
  const curatedSequence = ref<FrameSequenceView | null>(null);
  const lastOutputsByAction = ref<Record<string, ArtifactRef[]>>({});
  const activeRun = ref<RunView | null>(null);
  const loading = ref(false);
  const error = ref("");
  const saveState = ref<"saved" | "saving" | "conflict" | "offline">("saved");
  const undoStack = ref<SpriteDocument[]>([]);
  const redoStack = ref<SpriteDocument[]>([]);
  let saveTimer = 0;
  let stopEvents: (() => void) | undefined;
  let runtimeRefreshing = false;

  const document = computed(() => documentView.value?.document || null);
  const runningCount = computed(() => queue.value.running.length + queue.value.pending.length);
  const actionExamples = computed(() => actions.value.flatMap((action) => action.controls.flatMap((control) => control.options.flatMap((option) => option.example ? [option.example] : []))));
  const artifactById = computed(() => new Map([
    ...allArtifacts.value,
    ...actionExamples.value,
    ...(activeSequence.value?.frames || []),
    ...(curatedSequence.value?.frames || []),
    ...(curatedSequence.value ? [curatedSequence.value.artifact] : []),
    ...artifacts.value,
  ].map((item) => [item.id, item])));

  async function initialize() {
    loading.value = true;
    error.value = "";
    const results = await Promise.allSettled([
      api.health(), api.actions(), api.projects(), api.gallery(), api.artifacts(), api.queue(),
    ]);
    if (results[0].status === "fulfilled") {
      runtimeStatus.value = results[0].value.runtime;
      runtimeError.value = results[0].value.error || "";
    }
    if (results[1].status === "fulfilled") actions.value = results[1].value;
    if (results[2].status === "fulfilled") projects.value = results[2].value;
    if (results[3].status === "fulfilled") gallery.value = results[3].value;
    if (results[4].status === "fulfilled") allArtifacts.value = results[4].value;
    if (results[5].status === "fulfilled") queue.value = results[5].value;
    const failed = results.find((result) => result.status === "rejected");
    if (failed?.status === "rejected") error.value = readableError(failed.reason);
    loading.value = false;
  }

  async function refreshActions() {
    const health = await api.health();
    runtimeStatus.value = health.runtime;
    runtimeError.value = health.error || "";
    actions.value = await api.actions();
  }

  async function refreshRuntime() {
    if (runtimeRefreshing) return;
    runtimeRefreshing = true;
    try {
      await refreshActions();
    } catch (reason) {
      runtimeStatus.value = "offline";
      runtimeError.value = readableError(reason);
    } finally {
      runtimeRefreshing = false;
    }
  }

  async function ensureProject(type: ProjectType = "static") {
    if (currentProject.value) return currentProject.value;
    const project = await api.createProject({ type });
    projects.value.unshift(project);
    await openProject(project.id);
    return project;
  }

  async function openProject(id: string) {
    loading.value = true;
    error.value = "";
    try {
      const [project, doc, assets] = await Promise.all([
        api.project(id), api.document(id), api.projectArtifacts(id),
      ]);
      currentProject.value = project;
      documentView.value = doc;
      artifacts.value = assets;
      undoStack.value = [];
      redoStack.value = [];
      saveState.value = "saved";
      activeSequence.value = null;
      curatedSequence.value = null;
      lastOutputsByAction.value = {};
    } catch (reason) {
      error.value = readableError(reason);
      throw reason;
    } finally {
      loading.value = false;
    }
  }

  async function patchProject(id: string, patch: Partial<Pick<ProjectView, "name" | "type" | "favorite">>) {
    const updated = await api.patchProject(id, patch);
    if (currentProject.value?.id === id) currentProject.value = updated;
    const index = projects.value.findIndex((item) => item.id === id);
    if (index >= 0) projects.value[index] = updated;
    return updated;
  }

  async function refreshArtifacts() {
    if (currentProject.value) artifacts.value = await api.projectArtifacts(currentProject.value.id);
    allArtifacts.value = await api.artifacts();
  }

  async function upload(file: File, forcedKind?: ArtifactKind) {
    const kind = forcedKind || inferArtifactKind(file);
    if (!kind) throw new Error("Unsupported file type");
    const project = await ensureProject(kind === "SpriteSheet" ? "character" : "static");
    const artifact = await api.uploadArtifact(file, project.id, kind);
    artifacts.value.unshift(artifact);
    allArtifacts.value.unshift(artifact);
    if (documentView.value && kind === "Image" && documentView.value.document.type === "static" && !documentView.value.document.static?.primary) {
      mutateDocument((doc) => { if (doc.static) doc.static.primary = artifact.id; }, "import_primary");
    }
    return artifact;
  }

  async function runAction(actionId: string, inputs: Record<string, string | string[]>, values: Record<string, unknown>) {
    const projectType: ProjectType = actionId === "animation.generate"
      ? "character"
      : actionId === "image.generate" && values.category === "terrain" && (!currentProject.value || currentProject.value.type === "static")
        ? "tileset"
        : currentProject.value?.type || "static";
    const project = await ensureProject(projectType);
    await saveDocument();
    if (saveState.value !== "saved") throw new Error(error.value || "Project save failed");
    error.value = "";
    let run: RunView;
    try {
      run = await api.runAction(actionId, { project: project.id, inputs, values });
      if (project.type !== projectType) {
        const [updatedProject, updatedDocument] = await Promise.all([
          api.project(project.id),
          api.document(project.id),
        ]);
        currentProject.value = updatedProject;
        documentView.value = updatedDocument;
        const index = projects.value.findIndex((item) => item.id === updatedProject.id);
        if (index >= 0) projects.value[index] = updatedProject;
      }
    } catch (reason) {
      await refreshRuntime();
      throw reason;
    }
    activeRun.value = run;
    await refreshQueue();
    stopEvents?.();
    let integrated = false;
    stopEvents = subscribeRun(run.id, async (next) => {
      activeRun.value = next;
      replaceQueueRun(next);
      if (next.status === "succeeded") {
        await refreshArtifacts();
        if (!integrated) lastOutputsByAction.value[actionId] = next.artifacts;
        if (!integrated && SEQUENCE_ACTIONS.has(actionId) && next.artifacts[0]?.kind === "FrameSeq") {
          integrated = true;
          await loadSequence(next.artifacts[0].id);
        }
        if (!integrated && actionId === "normal.generate" && next.artifacts.length) {
          integrated = true;
          attachNormals(inputs.source, next.artifacts);
        }
      } else if (next.status === "failed") {
        await refreshRuntime();
      }
    }, async (reason) => { error.value = readableError(reason); await refreshRuntime(); });
    return run;
  }

  async function loadSequence(artifactId: string) {
    const sequence = await api.sequence(artifactId);
    activeSequence.value = sequence;
    return sequence;
  }

  async function readSequence(artifactId: string) {
    return api.sequence(artifactId);
  }

  async function loadCuratedSequence(artifactId: string) {
    const sequence = await api.sequence(artifactId);
    curatedSequence.value = sequence;
    return sequence;
  }

  async function materializeTrackSequence(
    action: AnimationClip["action"],
    view: "level" | "top45",
    direction: Direction,
  ) {
    if (!currentProject.value) throw new Error("Project is unavailable");
    await saveDocument();
    if (saveState.value !== "saved") throw new Error(error.value || "Document save failed");
    const sequence = await api.materializeSequence(currentProject.value.id, { action, view, direction });
    curatedSequence.value = sequence;
    const upsert = (items: ArtifactRef[], artifact: ArtifactRef) => {
      const index = items.findIndex((item) => item.id === artifact.id);
      if (index >= 0) items[index] = artifact;
      else items.unshift(artifact);
    };
    upsert(artifacts.value, sequence.artifact);
    upsert(allArtifacts.value, sequence.artifact);
    return sequence;
  }

  async function ensureCharacterDocument() {
    if (!currentProject.value || !documentView.value) return;
    if (currentProject.value.type !== "character") {
      await patchProject(currentProject.value.id, { type: "character" });
    }
    if (documentView.value.document.type !== "character" || !documentView.value.document.character) {
      const next = cloneDocument(documentView.value.document);
      next.type = "character";
      next.character ||= { pivot: { x: 0.5, y: 1 }, clips: [] };
      next.history.push({ operation: "convert_to_character", at: new Date().toISOString() });
      documentView.value = await api.putDocument(currentProject.value.id, next, documentView.value.etag);
      saveState.value = "saved";
    }
  }

  async function cancel(runId: string) {
    const run = await api.cancel(runId);
    activeRun.value = run;
    replaceQueueRun(run);
  }

  async function retry(runId: string) {
    const run = await api.retry(runId);
    activeRun.value = run;
    await refreshQueue();
    replaceQueueRun(run);
    return run;
  }

  async function refreshQueue() {
    try { queue.value = await api.queue(); } catch { /* non-blocking queue */ }
  }

  function replaceQueueRun(run: RunView) {
    const all = [...queue.value.running, ...queue.value.pending, ...queue.value.history].filter((item) => item.id !== run.id);
    queue.value = {
      running: run.status === "running" || run.status === "cancel_requested" ? [run, ...all.filter((item) => item.status === "running" || item.status === "cancel_requested")] : all.filter((item) => item.status === "running" || item.status === "cancel_requested"),
      pending: run.status === "queued" ? [run, ...all.filter((item) => item.status === "queued")] : all.filter((item) => item.status === "queued"),
      history: ["succeeded", "failed", "cancelled"].includes(run.status) ? [run, ...all.filter((item) => ["succeeded", "failed", "cancelled"].includes(item.status))] : all.filter((item) => ["succeeded", "failed", "cancelled"].includes(item.status)),
    };
  }

  function mutateDocument(change: (document: SpriteDocument) => void, operation: string) {
    if (!documentView.value) return;
    undoStack.value.push(cloneDocument(documentView.value.document));
    if (undoStack.value.length > 80) undoStack.value.shift();
    redoStack.value = [];
    change(documentView.value.document);
    documentView.value.document.history.push({ operation, at: new Date().toISOString() });
    scheduleSave();
  }

  function attachNormals(source: string | string[] | undefined, normals: ArtifactRef[]) {
    const requested = Array.isArray(source) ? source : source ? [source] : [];
    const pairs = normals.map((normal, index) => ({
      source: Array.isArray(normal.meta.source_artifacts) && normal.meta.source_artifacts[0]
        ? String(normal.meta.source_artifacts[0])
        : requested[Math.min(index, requested.length - 1)],
      normal: normal.id,
    })).filter((pair) => pair.source);
    if (!documentView.value || !pairs.length) return;
    mutateDocument((doc) => {
      pairs.forEach(({ source: artifactId, normal: normalId }) => {
        if (doc.static?.primary === artifactId) doc.static.normal = normalId;
        if (doc.tileset?.source === artifactId) doc.tileset.normal = normalId;
        for (const clip of doc.character?.clips || []) {
          for (const view of clip.views) {
            for (const track of view.tracks) {
              for (const frame of track.frames) if (frame.artifact === artifactId) frame.normal = normalId;
            }
          }
        }
      });
    }, "normal_attach");
  }

  function undo() {
    if (!documentView.value || !undoStack.value.length) return;
    redoStack.value.push(cloneDocument(documentView.value.document));
    documentView.value.document = undoStack.value.pop()!;
    scheduleSave();
  }

  function redo() {
    if (!documentView.value || !redoStack.value.length) return;
    undoStack.value.push(cloneDocument(documentView.value.document));
    documentView.value.document = redoStack.value.pop()!;
    scheduleSave();
  }

  function scheduleSave() {
    window.clearTimeout(saveTimer);
    saveState.value = "saving";
    saveTimer = window.setTimeout(saveDocument, 550);
  }

  async function saveDocument() {
    if (!currentProject.value || !documentView.value) return;
    window.clearTimeout(saveTimer);
    try {
      documentView.value = await api.putDocument(currentProject.value.id, documentView.value.document, documentView.value.etag);
      saveState.value = "saved";
    } catch (reason) {
      saveState.value = reason instanceof ApiError && reason.status === 409 ? "conflict" : "offline";
      error.value = readableError(reason);
    }
  }

  async function publish(coverId?: string) {
    if (!currentProject.value) return;
    await saveDocument();
    currentProject.value = await api.publish(currentProject.value.id, coverId);
    gallery.value = await api.gallery();
  }

  async function exportPack(allowIncomplete = false) {
    await saveDocument();
    return runAction("sprite.export", {}, { allow_incomplete: allowIncomplete });
  }

  return {
    actions, projects, gallery, currentProject, documentView, document, artifacts, allArtifacts,
    queue, runtimeStatus, runtimeError, runtimeReady, activeSequence, curatedSequence, lastOutputsByAction, activeRun, loading, error, saveState, undoStack, redoStack,
    runningCount, artifactById,
    initialize, refreshActions, refreshRuntime, ensureProject, ensureCharacterDocument, openProject, patchProject, refreshArtifacts, upload, runAction, readSequence, loadSequence, loadCuratedSequence, materializeTrackSequence,
    cancel, retry, refreshQueue, mutateDocument, undo, redo, saveDocument, publish, exportPack,
  };
});

const SEQUENCE_ACTIONS = new Set(["animation.generate", "sheet.slice", "video.sample"]);

function readableError(reason: unknown): string {
  if (reason instanceof ApiError) return reason.detail.message || reason.message;
  return reason instanceof Error ? reason.message : String(reason);
}

function cloneDocument(document: SpriteDocument): SpriteDocument {
  return JSON.parse(JSON.stringify(document)) as SpriteDocument;
}
