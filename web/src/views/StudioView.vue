<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { PhArrowRight as ArrowRight, PhCaretDown as CaretDown, PhCheck as Check, PhCircleNotch as CircleNotch, PhDownloadSimple as DownloadSimple, PhFilmStrip as FilmStrip, PhFloppyDisk as FloppyDisk, PhImageSquare as ImageSquare, PhMagicWand as MagicWand, PhPackage as Package, PhPlus as Plus, PhSlidersHorizontal as SlidersHorizontal, PhSparkle as Sparkle, PhUploadSimple as UploadSimple, PhWarning as Warning } from "@phosphor-icons/vue";
import ArtifactCard from "../components/ArtifactCard.vue";
import ArtifactVisual from "../components/ArtifactVisual.vue";
import DropTarget from "../components/DropTarget.vue";
import FrameStudio from "../components/FrameStudio.vue";
import StageRail from "../components/StageRail.vue";
import { inferArtifactKind, type ActionControl, type ActionDescriptor, type ArtifactKind, type ArtifactRef, type FrameSequenceView, type Locale } from "../api/generated";
import { useStudioStore } from "../stores/studio";

const store = useStudioStore();
const LightingPreview = defineAsyncComponent(() => import("../components/LightingPreview.vue"));
const route = useRoute();
const router = useRouter();
const { locale, t } = useI18n();
type StudioStage = "create" | "animate" | "normal" | "export";
const stage = ref<StudioStage>("create");
const imageValues = ref<Record<string, unknown>>({});
const animationValues = ref<Record<string, unknown>>({});
const inputs = ref<Record<string, string | string[]>>({});
const selectedArtifact = ref<ArtifactRef | null>(null);
const transientPreview = ref<ArtifactRef | null>(null);
const showAdvanced = ref(false);
const inspectorTab = ref<"properties" | "lineage">("properties");
const canvasFit = ref(true);
const reveal = ref(false);
const importInput = ref<HTMLInputElement | null>(null);
type HoverPreviewState = { artifact: ArtifactRef; label: string; description: string; motion: string; x: number; y: number };
const hoverPreview = ref<HoverPreviewState | null>(null);
let hoverHideTimer = 0;
let workspaceReady = false;
let normalTimer = 0;
const normalSequence = ref<FrameSequenceView | null>(null);
const normalFrameIndex = ref(0);
const normalPlaying = ref(false);

const imageAction = computed(() => store.actions.find((item) => item.id === "image.generate"));
const animationAction = computed(() => store.actions.find((item) => item.id === "animation.generate"));
const normalAction = computed(() => store.actions.find((item) => item.id === "normal.generate"));
const activeAction = computed(() => stage.value === "animate" ? animationAction.value : imageAction.value);
const activeValues = computed(() => stage.value === "animate" ? animationValues.value : imageValues.value);
const imageCandidates = computed(() => store.artifacts.filter((item) => item.kind === "Image" && !["animation.generate", "sheet.slice", "video.sample", "normal.generate"].includes(String(item.meta.action_id || ""))));
const createArtifacts = computed(() => store.artifacts.filter((item) => (
  ["Image", "SpriteSheet", "Video"].includes(item.kind)
  && !["animation.generate", "sheet.slice", "video.sample", "normal.generate"].includes(String(item.meta.action_id || ""))
)));
const sequences = computed(() => store.artifacts.filter((item) => item.kind === "FrameSeq"));
const recentCreateOutputs = computed(() => {
  const live = store.lastOutputsByAction["image.generate"] || [];
  if (live.length) return live;
  const latestRun = store.artifacts.find((item) => item.kind === "Image" && item.meta.action_id === "image.generate")?.meta.run_id;
  return latestRun ? store.artifacts.filter((item) => item.kind === "Image" && item.meta.run_id === latestRun) : [];
});
const sourceArtifact = computed(() => {
  const raw = inputs.value.source;
  const id = Array.isArray(raw) ? raw[0] : raw;
  return store.artifactById.get(String(id || ""));
});
const normalFrames = computed(() => normalSequence.value?.frames || (sourceArtifact.value?.kind === "Image" ? [sourceArtifact.value] : []));
const diffuse = computed(() => {
  if (stage.value === "normal" && normalFrames.value.length) return normalFrames.value[Math.min(normalFrameIndex.value, normalFrames.value.length - 1)];
  if (sourceArtifact.value?.kind === "Image" || sourceArtifact.value?.kind === "SpriteSheet") return sourceArtifact.value;
  if (selectedArtifact.value?.kind === "Image") return selectedArtifact.value;
  const linkedSource = selectedArtifact.value?.kind === "NormalMap" && Array.isArray(selectedArtifact.value.meta.source_artifacts)
    ? String(selectedArtifact.value.meta.source_artifacts[0] || "") : "";
  return store.artifacts.find((item) => item.id === linkedSource)
    || imageCandidates.value[0];
});
const normal = computed(() => {
  const sourceId = diffuse.value?.id;
  if (!sourceId) return undefined;
  const linkedId = store.document?.static?.primary === sourceId
    ? store.document.static.normal
    : store.document?.tileset?.source === sourceId
      ? store.document.tileset.normal
      : store.document?.character?.clips.flatMap((clip) => clip.views).flatMap((view) => view.tracks).flatMap((track) => track.frames).find((frame) => frame.artifact === sourceId)?.normal;
  return store.artifacts.find((item) => item.id === linkedId)
    || store.artifacts.find((item) => item.kind === "NormalMap" && Array.isArray(item.meta.source_artifacts) && item.meta.source_artifacts.includes(sourceId));
});
const character = computed(() => {
  const raw = inputs.value.character;
  const id = Array.isArray(raw) ? raw[0] : raw;
  return store.artifactById.get(String(id || ""));
});
const activeArtifact = computed(() => transientPreview.value || selectedArtifact.value || diffuse.value || store.curatedSequence?.artifact || store.activeSequence?.artifact || store.artifacts[0]);
const createDisplayArtifact = computed(() => transientPreview.value || selectedArtifact.value || recentCreateOutputs.value[0] || imageCandidates.value[0]);
const exportIssues = computed(() => store.activeRun?.action_id === "project.export" && store.activeRun.status === "failed"
  ? store.activeRun.error?.issues || [store.activeRun.error?.message || store.activeRun.message] : []);
const running = computed(() => Boolean(store.activeRun && ["queued", "running"].includes(store.activeRun.status)));
const animationModel = computed(() => animationAction.value?.models.find((item) => item.id === animationValues.value.model));
const animationCanRun = computed(() => {
  if (!animationAction.value?.available || !animationModel.value || running.value) return false;
  const modes = new Set(animationModel.value.modes || []);
  return inputs.value.character
    ? modes.has("i2v") || modes.has("i2i-sequence")
    : modes.has("t2v") || modes.has("t2i-sequence");
});

function fillDefaults(target: Record<string, unknown>, action = activeAction.value) {
  if (!action) return;
  for (const control of action.controls) if (target[control.id] === undefined) target[control.id] = JSON.parse(JSON.stringify(control.default));
  if (!target.model && action.models[0]) target.model = action.models[0].id;
}
watch(imageAction, (action) => { if (action) fillDefaults(imageValues.value, action); }, { immediate: true });
watch(animationAction, (action) => { if (action) fillDefaults(animationValues.value, action); }, { immediate: true });
watch(stage, (next) => {
  hideHoverPreview(true);
  if (workspaceReady && next === "animate" && selectedArtifact.value?.kind === "Image") inputs.value.character = selectedArtifact.value.id;
  if (workspaceReady && next === "normal" && selectedArtifact.value && ["Image", "FrameSeq", "SpriteSheet"].includes(selectedArtifact.value.kind)) void useForNormal(selectedArtifact.value);
});
watch(() => store.lastOutputsByAction["image.generate"], (outputs) => {
  if (outputs?.length) selectedArtifact.value = outputs[0];
});

watch([stage, imageValues, animationValues, inputs, () => selectedArtifact.value?.id, () => store.activeSequence?.artifact.id], persistWorkspace, { deep: true });

onMounted(async () => {
  const projectId = route.params.projectId as string | undefined;
  if (projectId) await store.openProject(projectId);
  else if (store.projects[0]) { await store.openProject(store.projects[0].id); await router.replace(`/studio/${store.projects[0].id}`); }
  await restoreWorkspace();
  await applyRouteIntent();
  workspaceReady = true;
});
watch(() => route.params.projectId, async (id) => { if (id && id !== store.currentProject?.id) { workspaceReady = false; await store.openProject(String(id)); await restoreWorkspace(); workspaceReady = true; } });
watch(() => store.currentProject?.id, (id) => { if (id && route.params.projectId !== id) void router.replace(`/studio/${id}`); });
onBeforeUnmount(() => { window.clearInterval(normalTimer); window.clearTimeout(hoverHideTimer); });

function controlCopy(control: ActionControl) { return control.i18n[locale.value as Locale]; }
function optionCopy(option: ActionControl["options"][number]) { return option.i18n[locale.value as Locale]; }
function acceptedKinds(action: ActionDescriptor | undefined, slot: string): ArtifactKind[] {
  const declared = action?.accepts[slot]?.type;
  return declared ? (Array.isArray(declared) ? declared : [declared]) : [];
}
function showHoverPreview(event: MouseEvent | FocusEvent, artifact: ArtifactRef | undefined, label: string, description = "", motion = "idle") {
  if (!artifact) return;
  window.clearTimeout(hoverHideTimer);
  const target = event.currentTarget as HTMLElement;
  const rect = target.getBoundingClientRect();
  const width = 236;
  const height = 300;
  const x = rect.right + 16 + width <= window.innerWidth - 12
    ? rect.right + 16
    : Math.max(12, rect.left - width - 16);
  const y = Math.max(12, Math.min(window.innerHeight - height - 12, rect.top + rect.height / 2 - height / 2));
  hoverPreview.value = { artifact, label, description, motion, x, y };
}
function hideHoverPreview(immediate = false) {
  window.clearTimeout(hoverHideTimer);
  if (immediate) hoverPreview.value = null;
  else hoverHideTimer = window.setTimeout(() => { hoverPreview.value = null; }, 650);
}
function keepHoverPreview() { window.clearTimeout(hoverHideTimer); }
async function importFiles(files: File[], slot?: string) {
  for (const file of files) {
    const artifact = await store.upload(file, inferArtifactKind(file) || "Image");
    if (slot) inputs.value[slot] = artifact.id;
    selectedArtifact.value = artifact;
  }
}
async function acceptArtifact(slot: string, payload: { artifact_id: string }) {
  inputs.value[slot] = payload.artifact_id;
  const artifact = store.artifactById.get(payload.artifact_id) || null;
  if (artifact) selectedArtifact.value = artifact;
  if (artifact?.kind === "FrameSeq") {
    if (slot === "source") await loadNormalSequence(artifact.id);
    else await store.loadSequence(artifact.id);
  }
}
async function runCreate() {
  if (!imageAction.value) return;
  reveal.value = true;
  try { await store.runAction("image.generate", inputs.value.reference ? { reference: inputs.value.reference } : {}, imageValues.value); }
  finally { window.setTimeout(() => { reveal.value = false; }, 500); }
}
async function runAnimation() {
  if (!animationAction.value || !animationCanRun.value) return;
  reveal.value = true;
  try { await store.runAction("animation.generate", inputs.value.character ? { character: inputs.value.character } : {}, animationValues.value); }
  finally { window.setTimeout(() => { reveal.value = false; }, 500); }
}
async function normalRun() {
  const source = Array.isArray(inputs.value.source) ? inputs.value.source[0] : inputs.value.source || diffuse.value?.id;
  if (source) await store.runAction("normal.generate", { source }, { strength: 1, flip_y: false });
}
async function redrawCurrent() { if (diffuse.value?.kind === "Image") await store.runAction("frame.redraw", { frame: diffuse.value.id }, { prompt: "", strength: 0.35, count: 4 }); }
function selectArtifact(artifact: ArtifactRef) {
  selectedArtifact.value = artifact;
  transientPreview.value = null;
  if (artifact.kind === "FrameSeq" && stage.value === "animate") void store.loadSequence(artifact.id);
  if (stage.value === "create" && store.document?.type === "static" && artifact.kind === "Image") store.mutateDocument((document) => { if (document.static) document.static.primary = artifact.id; }, "select_primary");
}
function previewArtifact(artifact: ArtifactRef | null) { transientPreview.value = artifact; }
function selectStage(id: string) { if (["create", "animate", "normal", "export"].includes(id)) stage.value = id as typeof stage.value; }
function workspaceKey() { return store.currentProject ? `cooksprite.workspace.${store.currentProject.id}` : ""; }
function persistWorkspace() {
  if (!workspaceReady || !store.currentProject) return;
  localStorage.setItem(workspaceKey(), JSON.stringify({
    stage: stage.value,
    imageValues: imageValues.value,
    animationValues: animationValues.value,
    inputs: inputs.value,
    selectedArtifactId: selectedArtifact.value?.id,
    activeSequenceId: store.activeSequence?.artifact.id,
    curatedSequenceId: store.curatedSequence?.artifact.id,
  }));
}
async function restoreWorkspace() {
  const saved = workspaceKey() ? localStorage.getItem(workspaceKey()) : null;
  let state: Record<string, unknown> = {};
  try { state = saved ? JSON.parse(saved) : {}; } catch { state = {}; }
  if (["create", "animate", "normal", "export"].includes(String(state.stage))) stage.value = state.stage as StudioStage;
  if (state.imageValues && typeof state.imageValues === "object") Object.assign(imageValues.value, state.imageValues);
  if (state.animationValues && typeof state.animationValues === "object") Object.assign(animationValues.value, state.animationValues);
  const storedInputs = state.inputs && typeof state.inputs === "object" ? state.inputs as Record<string, string | string[]> : {};
  inputs.value = Object.fromEntries(Object.entries(storedInputs).filter(([, value]) => {
    const id = Array.isArray(value) ? value[0] : value;
    return Boolean(store.artifactById.get(String(id || "")));
  }));
  selectedArtifact.value = store.artifactById.get(String(state.selectedArtifactId || ""))
    || store.artifacts.find((item) => item.kind === "Image" && item.meta.action_id === "image.generate")
    || createArtifacts.value[0]
    || null;
  const curated = store.artifacts.find((item) => item.id === String(state.curatedSequenceId || "") && item.kind === "FrameSeq")
    || store.artifacts.find((item) => item.kind === "FrameSeq" && item.meta.role === "curated_sequence");
  if (curated) await store.loadCuratedSequence(curated.id).catch(() => undefined);
  const candidateId = String(state.activeSequenceId || "");
  const candidate = store.artifacts.find((item) => item.id === candidateId && item.kind === "FrameSeq")
    || store.artifacts.find((item) => item.kind === "FrameSeq" && item.meta.role !== "curated_sequence");
  if (candidate) await store.loadSequence(candidate.id).catch(() => undefined);
  if (stage.value === "normal") {
    const source = sourceArtifact.value;
    if (source?.kind === "FrameSeq") await loadNormalSequence(source.id);
  }
}
async function applyRouteIntent() {
  const id = typeof route.query.artifact === "string" ? route.query.artifact : "";
  const intent = typeof route.query.intent === "string" ? route.query.intent : "";
  const artifact = store.artifactById.get(id);
  if (!artifact) return;
  if (intent === "animate" && artifact.kind === "Image") useForAnimation(artifact);
  else if (intent === "sequence" && artifact.kind === "FrameSeq") { selectedArtifact.value = artifact; await store.loadSequence(artifact.id); stage.value = "animate"; }
  else if (intent === "reference" && artifact.kind === "Image") useAsReference(artifact);
  else if (intent === "normal" && ["Image", "FrameSeq", "SpriteSheet"].includes(artifact.kind)) await useForNormal(artifact);
  await router.replace({ path: route.path, query: {} });
}
function useAsReference(artifact: ArtifactRef) {
  if (artifact.kind !== "Image") return;
  selectedArtifact.value = artifact;
  inputs.value.reference = artifact.id;
  stage.value = "create";
}
function useForAnimation(artifact: ArtifactRef) {
  if (artifact.kind !== "Image") return;
  selectedArtifact.value = artifact;
  inputs.value.character = artifact.id;
  stage.value = "animate";
  transientPreview.value = null;
  document.querySelector(".animation-generator")?.scrollIntoView({ behavior: "smooth", block: "start" });
}
async function loadNormalSequence(id: string) {
  normalSequence.value = await store.readSequence(id);
  normalFrameIndex.value = 0;
  normalPlaying.value = false;
  scheduleNormalPlayback();
}
async function useForNormal(artifact: ArtifactRef) {
  if (!["Image", "FrameSeq", "SpriteSheet"].includes(artifact.kind)) return;
  selectedArtifact.value = artifact;
  inputs.value.source = artifact.id;
  stage.value = "normal";
  transientPreview.value = null;
  normalSequence.value = null;
  normalFrameIndex.value = 0;
  if (artifact.kind === "FrameSeq") await loadNormalSequence(artifact.id);
}
async function continueFromAnimation() {
  let sequence = store.curatedSequence;
  const target = store.activeSequence?.sequence;
  if (!sequence && target?.action && target.view && target.direction) {
    sequence = await store.materializeTrackSequence(target.action, target.view, target.direction);
  }
  if (sequence) await useForNormal(sequence.artifact);
}
async function nextStage() {
  if (stage.value === "create") {
    const artifact = selectedArtifact.value?.kind === "Image" ? selectedArtifact.value : createDisplayArtifact.value;
    if (artifact?.kind === "Image") useForAnimation(artifact);
  } else if (stage.value === "animate") await continueFromAnimation();
  else if (stage.value === "normal") stage.value = "export";
}
function scheduleNormalPlayback() {
  window.clearInterval(normalTimer);
  if (!normalPlaying.value || normalFrames.value.length < 2) return;
  normalTimer = window.setInterval(() => {
    normalFrameIndex.value = (normalFrameIndex.value + 1) % normalFrames.value.length;
  }, 140);
}
function toggleNormalPlayback() { normalPlaying.value = !normalPlaying.value; scheduleNormalPlayback(); }
watch([normalPlaying, () => normalFrames.value.length], scheduleNormalPlayback);
function setPivot(axis: "x" | "y", value: number) { store.mutateDocument((document) => { const pivot = document.static?.pivot || document.character?.pivot; if (pivot) pivot[axis] = value; }, "pivot_update"); }
async function publish() { await store.publish(diffuse.value?.id); }
async function exportPack(allow = false) { await store.exportPack(allow); }
function downloadPack(artifact: ArtifactRef) { const anchor = document.createElement("a"); anchor.href = artifact.url; anchor.download = artifact.title || "sprite.cooksprite"; anchor.click(); }
</script>

<template>
  <div class="studio-view">
    <aside class="studio-stages"><StageRail :active="stage" @select="selectStage" @next="nextStage" /></aside>
    <section class="studio-main">
      <header class="project-bar"><div class="project-title"><span class="project-icon"><ImageSquare :size="19" /></span><div><input v-if="store.currentProject" :value="store.currentProject.name" :aria-label="$t('studio.projectName')" @change="store.patchProject(store.currentProject!.id, { name: ($event.target as HTMLInputElement).value })" /><strong v-else>{{ $t("studio.untitled") }}</strong><span>{{ store.currentProject?.type.toUpperCase() || $t("studio.autoProject") }} · COOKSPRITE API</span></div></div><div class="save-indicator" :class="store.saveState"><FloppyDisk :size="16" /><span>{{ $t(`common.${store.saveState}`) }}</span></div></header>
      <div v-if="store.runtimeStatus !== 'ready'" class="runtime-warning" role="status"><Warning :size="18" weight="fill" /><span>{{ $t("studio.noRuntime") }} <b>{{ $t(`common.${store.runtimeStatus}`) }}</b></span><RouterLink to="/settings">{{ $t("common.setup") }}<ArrowRight :size="15" /></RouterLink></div>

      <div class="studio-stage-content">
        <template v-if="stage === 'create'">
          <section v-if="imageAction" class="creation-deck">
            <div class="creation-layout"><div class="creation-fields"><div class="action-heading"><span class="eyebrow">ACTION · IMAGE.GENERATE</span><h1>{{ imageAction.i18n[locale as Locale].name }}</h1><p>{{ imageAction.i18n[locale as Locale].description }}</p></div><div class="prompt-field"><label for="prompt">{{ $t("studio.promptLabel") }}</label><textarea id="prompt" maxlength="600" :value="String(imageValues.prompt || '')" :placeholder="$t('studio.prompt')" rows="3" @input="imageValues.prompt = ($event.target as HTMLTextAreaElement).value"></textarea><span>{{ String(imageValues.prompt || '').length }}/600</span></div><div class="control-stack"><template v-for="control in imageAction.controls.filter(item => !item.advanced && item.id !== 'prompt')" :key="control.id"><div v-if="control.type === 'select'" class="segmented-control"><span class="control-copy"><b>{{ controlCopy(control).name }}</b><small>{{ controlCopy(control).description }}</small></span><div><button v-for="option in control.options" :key="option.id" :class="{ active: imageValues[control.id] === option.id }" @mouseenter="showHoverPreview($event, option.example, optionCopy(option).name, optionCopy(option).description, option.id)" @focus="showHoverPreview($event, option.example, optionCopy(option).name, optionCopy(option).description, option.id)" @mouseleave="hideHoverPreview()" @blur="hideHoverPreview()" @click="imageValues[control.id] = option.id">{{ optionCopy(option).name }}</button></div></div><label v-else-if="control.type === 'number'" class="inline-control"><span>{{ controlCopy(control).name }}</span><input v-model.number="imageValues[control.id]" type="number" :min="control.min" :max="control.max" :step="control.step" /></label></template></div><div class="model-row"><label>{{ $t("studio.model") }}<select v-model="imageValues.model" :disabled="!imageAction.models.length"><option v-if="!imageAction.models.length" value="">{{ $t("studio.noModel") }}</option><option v-for="model in imageAction.models" :key="model.id" :value="model.id">{{ model.label }}</option></select></label><button class="text-button" :aria-expanded="showAdvanced" @click="showAdvanced = !showAdvanced"><SlidersHorizontal :size="17" />{{ $t("common.advanced") }}<CaretDown :size="14" /></button></div><div v-if="showAdvanced" class="advanced-grid"><template v-for="control in imageAction.controls.filter(item => item.advanced)" :key="control.id"><label v-if="control.type === 'range'"><span>{{ controlCopy(control).name }} <b>{{ imageValues[control.id] }}</b></span><input v-model.number="imageValues[control.id]" type="range" :min="control.min" :max="control.max" :step="control.step" /></label><label v-else-if="control.type === 'seed' || control.type === 'number'"><span>{{ controlCopy(control).name }}</span><input v-model.number="imageValues[control.id]" type="number" :min="control.min" :max="control.max" /></label></template></div></div>
              <aside class="artifact-input-panel"><DropTarget :accepts="acceptedKinds(imageAction, 'reference')" :artifact="inputs.reference ? store.artifactById.get(String(inputs.reference)) : undefined" :label="inputs.reference ? (store.artifactById.get(String(inputs.reference))?.title || $t('studio.selectedReference')) : $t('studio.drop')" :reason="$t('studio.referenceReason')" @artifact="acceptArtifact('reference', $event)" @files="importFiles($event, 'reference')" /></aside></div>
            <footer class="draw-bar"><div><MagicWand :size="20" /><span><strong>{{ $t("studio.drawImage") }}</strong><small>{{ $t("studio.actionFlow") }}</small></span></div><button class="draw-button" :disabled="!imageAction.available || running" @click="runCreate"><CircleNotch v-if="running" class="spin" :size="20" /><Sparkle v-else :size="20" weight="fill" />{{ $t("common.run") }} · {{ imageValues.count || 1 }}<ArrowRight :size="18" /></button></footer><div v-if="reveal" class="card-reveal" aria-hidden="true"><i></i><span>{{ $t("studio.cooking") }}</span><i></i></div>
          </section>
          <section v-if="recentCreateOutputs.length" class="run-results panel"><header><div><span class="eyebrow">{{ $t('studio.currentRun') }}</span><strong>{{ $t('studio.chooseResult') }}</strong></div><small>{{ recentCreateOutputs.length }} {{ $t('studio.results') }}</small></header><div class="artifact-strip"><ArtifactCard v-for="artifact in recentCreateOutputs" :key="artifact.id" :artifact="artifact" :selected="selectedArtifact?.id === artifact.id" compact @select="selectArtifact" @preview="previewArtifact" /></div></section>
          <section v-if="selectedArtifact?.kind === 'Image'" class="continue-bar" :aria-label="$t('studio.continueCreating')"><div class="continue-preview checker"><ArtifactVisual :artifact="selectedArtifact" :draggable="false" /></div><div><span class="eyebrow">{{ $t('studio.nextStep') }}</span><strong>{{ selectedArtifact.title || selectedArtifact.id.slice(0, 14) }}</strong></div><button class="arcade-button" type="button" @click="useAsReference(selectedArtifact)">{{ $t('studio.useReference') }}</button><button class="arcade-button primary" type="button" @click="useForAnimation(selectedArtifact)"><FilmStrip :size="17" />{{ $t('studio.makeAnimation') }}<ArrowRight :size="16" /></button><button class="arcade-button" type="button" @click="useForNormal(selectedArtifact)"><Sparkle :size="17" />{{ $t('studio.makeNormal') }}</button></section>
          <section class="canvas-workspace create-canvas"><div class="canvas-head"><span class="eyebrow">{{ $t("studio.canvas") }}</span><div class="zoom-controls"><button :class="{ active: !canvasFit }" @click="canvasFit = false">100%</button><button :class="{ active: canvasFit }" @click="canvasFit = true">{{ $t("studio.fit") }}</button></div></div><div class="sprite-canvas checker" :class="{ empty: !createDisplayArtifact, original: !canvasFit }"><ArtifactVisual v-if="createDisplayArtifact" :artifact="createDisplayArtifact" /><div v-else class="canvas-empty"><UploadSimple :size="40" /><strong>{{ $t("studio.canvasEmpty") }}</strong><span>{{ $t("studio.transparentNote") }}</span></div><span class="canvas-origin"><i></i>{{ $t("studio.pivot") }}</span></div></section>
          <section class="asset-dock"><header><div class="dock-tabs"><button class="active">{{ $t("studio.projectStills") }} <b>{{ createArtifacts.length }}</b></button></div><button class="text-button" @click="importInput?.click()"><Plus :size="16" />{{ $t("common.import") }}</button><input ref="importInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif,video/mp4,video/webm" @change="importFiles([...(($event.target as HTMLInputElement).files || [])])" /></header><div class="artifact-strip"><ArtifactCard v-for="artifact in createArtifacts" :key="artifact.id" :artifact="artifact" :selected="selectedArtifact?.id === artifact.id" compact @select="selectArtifact" @preview="previewArtifact" /><DropTarget v-if="!createArtifacts.length" :accepts="['Image','SpriteSheet','Video']" :label="$t('studio.canvasEmpty')" @files="importFiles" /></div></section>
        </template>

        <template v-else-if="stage === 'animate'">
          <section v-if="animationAction" class="creation-deck animation-generator"><div class="creation-layout"><div class="creation-fields"><div class="action-heading"><span class="eyebrow">ACTION · ANIMATION.GENERATE</span><h1>{{ animationAction.i18n[locale as Locale].name }}</h1><p>{{ animationAction.i18n[locale as Locale].description }}</p></div><div class="prompt-field compact-prompt"><label for="animation-prompt">{{ controlCopy(animationAction.controls.find(item => item.id === 'prompt')!).name }}</label><textarea id="animation-prompt" maxlength="600" :value="String(animationValues.prompt || '')" :placeholder="controlCopy(animationAction.controls.find(item => item.id === 'prompt')!).description" rows="2" @input="animationValues.prompt = ($event.target as HTMLTextAreaElement).value"></textarea></div><div class="action-grid"><button v-for="option in animationAction.controls.find(item => item.id === 'action')?.options" :key="option.id" :class="{ active: animationValues.action === option.id }" @mouseenter="showHoverPreview($event, option.example, option.i18n[locale as Locale].name, option.i18n[locale as Locale].description, option.id)" @focus="showHoverPreview($event, option.example, option.i18n[locale as Locale].name, option.i18n[locale as Locale].description, option.id)" @mouseleave="hideHoverPreview()" @blur="hideHoverPreview()" @click="animationValues.action = option.id">{{ option.i18n[locale as Locale].name }}</button></div><div class="control-stack"><template v-for="control in animationAction.controls.filter(item => ['view','direction'].includes(item.id))" :key="control.id"><div class="segmented-control"><span>{{ controlCopy(control).name }}</span><div><button v-for="option in control.options" :key="option.id" :class="{ active: animationValues[control.id] === option.id }" @click="animationValues[control.id] = option.id">{{ option.i18n[locale as Locale].name }}</button></div></div></template><label class="inline-control"><span>{{ controlCopy(animationAction.controls.find(item => item.id === 'count')!).name }}</span><input v-model.number="animationValues.count" type="number" min="1" max="16" /></label></div><div class="model-row"><label>{{ $t("studio.model") }}<select v-model="animationValues.model" :disabled="!animationAction.models.length"><option v-if="!animationAction.models.length" value="">{{ $t("studio.noModel") }}</option><option v-for="model in animationAction.models" :key="model.id" :value="model.id">{{ model.label }} · {{ model.modes.join('/') }}</option></select></label><small class="model-compatibility">{{ inputs.character ? 'IMAGE + TEXT' : 'TEXT' }} → {{ animationCanRun ? $t('studio.compatibleModel') : $t('studio.incompatibleModel') }}</small></div></div>
              <aside class="artifact-input-panel"><DropTarget :accepts="acceptedKinds(animationAction, 'character')" :artifact="character" :label="character ? character.title || character.id || $t('studio.selectedCharacter') : $t('studio.characterDrop')" :reason="$t('studio.characterReason')" @artifact="acceptArtifact('character', $event)" @files="importFiles($event, 'character')" /></aside></div>
            <footer class="draw-bar"><div><FilmStrip :size="20" /><span><strong>{{ $t("studio.drawFrames", { action: String(animationValues.action || 'walk').toUpperCase() }) }}</strong><small>{{ String(animationValues.view || 'level').toUpperCase() }} · {{ String(animationValues.direction || 's').toUpperCase() }} · {{ $t('studio.animationSequence') }}</small></span></div><button class="draw-button" :disabled="!animationCanRun" @click="runAnimation"><CircleNotch v-if="running" class="spin" :size="20" /><Sparkle v-else :size="20" weight="fill" />{{ $t("common.run") }} · {{ animationValues.count || 8 }}<ArrowRight :size="18" /></button></footer><div v-if="reveal" class="card-reveal" aria-hidden="true"><i></i><span>{{ $t("studio.cooking") }}</span><i></i></div></section>
          <section class="animation-preview-row"><div class="animation-preview checker"><ArtifactVisual v-if="activeArtifact?.kind === 'Image'" :artifact="activeArtifact" /><span v-else>{{ $t('studio.previewFrameHint') }}</span></div><div class="sequence-dock"><span class="eyebrow">{{ $t('studio.reusableAnimations') }}</span><div><ArtifactCard v-for="sequence in sequences" :key="sequence.id" :artifact="sequence" :selected="store.activeSequence?.artifact.id === sequence.id" compact @select="selectArtifact" @preview="previewArtifact" /></div></div></section>
          <FrameStudio :sequence="store.activeSequence" @preview="previewArtifact" @use-normal="useForNormal" />
        </template>

        <section v-else-if="stage === 'normal'" class="normal-workspace"><div class="normal-input panel"><span class="eyebrow">{{ $t("studio.diffusePair") }}</span><h2>{{ $t("studio.light") }}</h2><DropTarget :accepts="acceptedKinds(normalAction, 'source')" :artifact="sourceArtifact" :label="sourceArtifact ? sourceArtifact.title || sourceArtifact.id : $t('studio.dropDiffuse')" @artifact="acceptArtifact('source', $event)" @files="importFiles($event, 'source')" /><div v-if="normalFrames.length > 1" class="normal-sequence-controls"><button class="arcade-button" type="button" @click="toggleNormalPlayback">{{ normalPlaying ? $t('frames.pause') : $t('frames.play') }}</button><span>{{ normalFrameIndex + 1 }} / {{ normalFrames.length }}</span></div><div v-if="normalFrames.length > 1" class="normal-frame-strip"><button v-for="(frame, index) in normalFrames" :key="frame.id" type="button" :class="{ active: normalFrameIndex === index, complete: Boolean(store.artifacts.find(item => item.kind === 'NormalMap' && Array.isArray(item.meta.source_artifacts) && item.meta.source_artifacts.includes(frame.id))) }" @click="normalFrameIndex = index"><ArtifactVisual :artifact="frame" :draggable="false" /><span>F{{ String(index + 1).padStart(2, '0') }}</span></button></div><div v-if="diffuse" class="normal-source-row"><ArtifactCard :artifact="diffuse" selected compact @select="selectArtifact" @preview="previewArtifact" /><ArtifactCard v-if="normal" :artifact="normal" compact @select="selectArtifact" @preview="previewArtifact" /></div><button class="arcade-button primary" :disabled="!sourceArtifact || !normalAction?.available || running" @click="normalRun"><Sparkle :size="18" />{{ normalFrames.length > 1 ? $t('studio.generateSequenceNormals', { count: normalFrames.length }) : $t("studio.generateNormals") }}</button><button class="text-button" :disabled="diffuse?.kind !== 'Image'" @click="redrawCurrent">{{ $t("studio.redoFrame") }}</button><button v-if="normal" class="arcade-button" type="button" @click="stage = 'export'">{{ $t('studio.continueDelivery') }}<ArrowRight :size="16" /></button></div><LightingPreview :diffuse="diffuse" :normal="normal" /></section>

        <section v-else class="export-workspace"><div class="export-card panel"><Package :size="48" /><span class="eyebrow">{{ $t("export.eyebrow") }}</span><h1>.cooksprite</h1><p>manifest.json + frames/*.png + normals/*.png + provenance.json</p><ul><li v-for="index in 4" :key="index"><Check :size="16" />{{ $t(`export.checks.${index - 1}`) }}</li></ul><div v-if="exportIssues.length" class="export-warning" role="alert"><strong>{{ $t("export.incomplete") }}</strong><ul><li v-for="issue in exportIssues" :key="issue"><Warning :size="15" />{{ issue }}</li></ul></div><button class="arcade-button primary large" @click="exportPack(false)"><Package :size="20" />{{ $t("export.validate") }}</button><button class="text-button warning-link" @click="exportPack(true)">{{ $t("export.accept") }}</button></div><div class="package-list panel"><h2>{{ $t("export.packages") }}</h2><article v-for="artifact in store.artifacts.filter(item => item.kind === 'CookSpritePack')" :key="artifact.id"><Package :size="24" /><div><strong>{{ artifact.title }}</strong><span>{{ (artifact.size / 1024).toFixed(1) }} KB</span></div><button class="arcade-button" @click="downloadPack(artifact)"><DownloadSimple :size="17" />{{ $t("common.download") }}</button></article><p v-if="!store.artifacts.some(item => item.kind === 'CookSpritePack')" class="muted">{{ $t("export.empty") }}</p></div></section>
      </div>
    </section>

    <aside class="studio-inspector"><header class="inspector-tabs"><button :class="{ active: inspectorTab === 'properties' }" @click="inspectorTab = 'properties'">{{ $t("studio.properties") }}</button><button :class="{ active: inspectorTab === 'lineage' }" @click="inspectorTab = 'lineage'">{{ $t("studio.lineage") }}</button></header><div v-if="inspectorTab === 'properties'" class="inspector-body"><template v-if="activeArtifact"><div class="inspector-preview checker"><ArtifactVisual :artifact="activeArtifact" animated /></div><span class="eyebrow">{{ activeArtifact.kind }}</span><h2>{{ activeArtifact.title || activeArtifact.id.slice(0, 14) }}</h2><dl><dt>ARTIFACT</dt><dd>{{ activeArtifact.id }}</dd><dt>SIZE</dt><dd>{{ (activeArtifact.size / 1024).toFixed(1) }} KB</dd><dt>ACTION</dt><dd>{{ activeArtifact.meta.action_id || 'import' }}</dd></dl></template><template v-else><div class="inspector-empty"><ImageSquare :size="34" /><p>{{ $t("studio.inspectorEmpty") }}</p></div></template><div v-if="store.document" class="pivot-editor"><h3>{{ $t("studio.projectPivot") }}</h3><div><label>X<input type="number" step="0.01" :value="store.document.static?.pivot.x ?? store.document.character?.pivot.x ?? 0.5" @change="setPivot('x', Number(($event.target as HTMLInputElement).value))" /></label><label>Y<input type="number" step="0.01" :value="store.document.static?.pivot.y ?? store.document.character?.pivot.y ?? 1" @change="setPivot('y', Number(($event.target as HTMLInputElement).value))" /></label></div></div></div><div v-else class="inspector-body lineage-list"><article v-for="(entry, index) in [...(store.document?.history || [])].reverse().slice(0, 30)" :key="index"><i></i><div><strong>{{ entry.operation }}</strong><span>{{ entry.at }}</span></div></article><p v-if="!store.document?.history.length" class="muted">{{ $t("studio.historyEmpty") }}</p><div class="lineage-actions"><button class="arcade-button" :disabled="!store.undoStack.length" @click="store.undo">{{ $t("studio.undo") }}</button><button class="arcade-button" :disabled="!store.redoStack.length" @click="store.redo">{{ $t("studio.redo") }}</button></div></div><footer v-if="stage === 'export'" class="inspector-footer"><button class="arcade-button" :disabled="!diffuse" @click="publish"><Check :size="17" />{{ $t("common.publish") }}</button></footer></aside>
  </div>
  <Teleport to="body">
    <aside v-if="hoverPreview" class="hover-example" :class="`motion-${hoverPreview.motion}`" :style="{ left: `${hoverPreview.x}px`, top: `${hoverPreview.y}px` }" role="tooltip" @mouseenter="keepHoverPreview" @mouseleave="hideHoverPreview()" @dragstart.capture="keepHoverPreview" @dragend.capture="hideHoverPreview(true)">
      <small>{{ $t('studio.hoverArtifact') }}</small>
      <strong>{{ hoverPreview.label }}</strong>
      <p v-if="hoverPreview.description">{{ hoverPreview.description }}</p>
      <span class="hover-example-stage checker"><i></i><ArtifactVisual :artifact="hoverPreview.artifact" animated /></span>
      <em>{{ $t('studio.hoverDragHint') }}</em>
    </aside>
  </Teleport>
</template>
