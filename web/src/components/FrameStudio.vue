<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { PhArrowsClockwise as ArrowsClockwise, PhCheck as Check, PhCopy as Copy, PhDotsSixVertical as DotsSixVertical, PhEye as Eye, PhFileArrowDown as FileArrowDown, PhKeyboard as Keyboard, PhMagicWand as MagicWand, PhPause as Pause, PhPlay as Play, PhPlus as Plus, PhSkipBack as SkipBack, PhSkipForward as SkipForward, PhTrash as Trash, PhWarning as Warning, PhX as X } from "@phosphor-icons/vue";
import type { ArtifactRef, FrameRef, FrameSequenceView } from "../api/generated";
import ArtifactCard from "./ArtifactCard.vue";
import ArtifactVisual from "./ArtifactVisual.vue";
import DropTarget from "./DropTarget.vue";
import SourceExtractor from "./SourceExtractor.vue";
import { useStudioStore } from "../stores/studio";

const props = defineProps<{ sequence: FrameSequenceView | null }>();
const emit = defineEmits<{ preview: [artifact: ArtifactRef | null]; useNormal: [artifact: ArtifactRef] }>();
const store = useStudioStore();
const { t } = useI18n();
const selected = ref<number[]>([]);
const focusedCandidate = ref(0);
const playing = ref(false);
const playIndex = ref(0);
const clipFps = ref(10);
const loop = ref<"none" | "linear" | "pingpong">("linear");
const onion = ref(false);
const pixelDiff = ref(false);
const compare = ref(false);
const shortcuts = ref(false);
const sourceTools = ref(false);
const viewport = ref<HTMLElement | null>(null);
const scrollLeft = ref(0);
const commitState = ref<"idle" | "saving" | "saved" | "error">("idle");
const commitMessage = ref("");
const addedFrames = ref<string[]>([]);
const redrawSourceFrameId = ref("");
const redrawMessage = ref("");
let playTimer = 0;

const candidates = computed(() => props.sequence?.frames || []);
const target = computed(() => props.sequence?.sequence);
const targetReady = computed(() => Boolean(target.value?.action && target.value?.view && target.value?.direction));
const targetLabel = computed(() => targetReady.value
  ? `${target.value!.action!.toUpperCase()} · ${target.value!.view!.toUpperCase()} · ${target.value!.direction!.toUpperCase()}`
  : t("frames.notLoaded"));
const clip = computed(() => store.document?.character?.clips.find((item) => item.action === target.value?.action));
const view = computed(() => clip.value?.views.find((item) => item.id === target.value?.view));
const track = computed(() => view.value?.tracks.find((item) => item.direction === target.value?.direction));
const timeline = computed(() => track.value?.frames || []);
const artifactMap = computed(() => store.artifactById);
const activeFrame = computed(() => timeline.value[playIndex.value]);
const redrawArtifact = computed(() => artifactMap.value.get(activeFrame.value?.artifact || "") || candidates.value[focusedCandidate.value]);
const compareArtifacts = computed(() => {
  const ids = selected.value.length >= 2
    ? selected.value.slice(-2).map((index) => candidates.value[index]?.id)
    : timeline.value.slice(Math.max(0, playIndex.value - 1), playIndex.value + 1).map((item) => item.artifact);
  return ids.map((id) => artifactMap.value.get(id || "") || candidates.value.find((item) => item.id === id)).filter((item): item is ArtifactRef => Boolean(item));
});
const cardWidth = 106;
const visibleCount = computed(() => Math.ceil((viewport.value?.clientWidth || 900) / cardWidth) + 6);
const start = computed(() => Math.max(0, Math.floor(scrollLeft.value / cardWidth) - 3));
const visibleCandidates = computed(() => candidates.value.slice(start.value, start.value + visibleCount.value));
const redrawVariants = computed(() => store.lastOutputsByAction["frame.redraw"] || []);
const redrawPending = computed(() => store.activeRun?.action_id === "frame.redraw" && ["queued", "running"].includes(store.activeRun.status));
const curatedSequence = computed(() => {
  const item = store.curatedSequence;
  if (!item || !targetReady.value) return null;
  return item.sequence.action === target.value?.action
    && item.sequence.view === target.value?.view
    && item.sequence.direction === target.value?.direction
    ? item
    : null;
});

function selectCandidate(artifact: ArtifactRef, eventOrRange?: MouseEvent | boolean) {
  const index = Math.max(0, candidates.value.findIndex((item, candidateIndex) => item.id === artifact.id && candidateIndex >= focusedCandidate.value));
  const actualIndex = index >= 0 ? index : candidates.value.findIndex((item) => item.id === artifact.id);
  const range = typeof eventOrRange === "boolean" ? eventOrRange : Boolean(eventOrRange?.shiftKey);
  if (range && selected.value.length) {
    const anchor = selected.value[selected.value.length - 1];
    selected.value = Array.from({ length: Math.abs(actualIndex - anchor) + 1 }, (_, offset) => Math.min(anchor, actualIndex) + offset);
  } else if (selected.value.includes(actualIndex)) selected.value = selected.value.filter((value) => value !== actualIndex);
  else selected.value.push(actualIndex);
  focusedCandidate.value = actualIndex;
  emit("preview", artifact);
}

async function openSequence(payload: { artifact_id: string }) {
  try {
    await store.loadSequence(payload.artifact_id);
    commitState.value = "saved";
    commitMessage.value = t("frames.sequenceOpened");
  } catch (reason) {
    commitState.value = "error";
    commitMessage.value = reason instanceof Error ? reason.message : String(reason);
  }
}
async function redraw() {
  if (!redrawArtifact.value) return;
  store.lastOutputsByAction["frame.redraw"] = [];
  redrawSourceFrameId.value = activeFrame.value?.id || "";
  redrawMessage.value = "";
  await store.runAction("frame.redraw", { frame: redrawArtifact.value.id }, { prompt: "", strength: 0.35, count: 4 });
}
function ensureTrack(document: NonNullable<typeof store.document>) {
  if (!targetReady.value) throw new Error("Sequence target is missing");
  if (!document.character) document.character = { pivot: { x: 0.5, y: 1 }, clips: [] };
  let targetClip = document.character.clips.find((item) => item.action === target.value!.action);
  if (!targetClip) {
    targetClip = { id: `clip_${target.value!.action}`, name: target.value!.action!, action: target.value!.action!, loop: loop.value, views: [] };
    document.character.clips.push(targetClip);
  }
  let targetView = targetClip.views.find((item) => item.id === target.value!.view);
  if (!targetView) { targetView = { id: target.value!.view!, enabled: true, tracks: [] }; targetClip.views.push(targetView); }
  let targetTrack = targetView.tracks.find((item) => item.direction === target.value!.direction);
  if (!targetTrack) { targetTrack = { direction: target.value!.direction!, frames: [] }; targetView.tracks.push(targetTrack); }
  return targetTrack;
}
async function commitSelection(mode: "replace" | "append" = "replace") {
  if (!selected.value.length || !props.sequence || !targetReady.value) return;
  commitState.value = "saving";
  commitMessage.value = t("frames.saving");
  try {
    await store.ensureCharacterDocument();
    if (!store.document) throw new Error("Project document is unavailable");
    const created: string[] = [];
    store.mutateDocument((document) => {
      const destination = ensureTrack(document);
      if (mode === "replace") destination.frames = [];
      selected.value.sort((a, b) => a - b).forEach((index) => {
        const artifact = candidates.value[index];
        if (!artifact) return;
        const id = crypto.randomUUID();
        created.push(id);
        destination.frames.push({ id, artifact: artifact.id, duration_ms: Math.round(1000 / clipFps.value), offset_x: 0, offset_y: 0, source_artifact: props.sequence!.artifact.id });
      });
    }, "timeline_add_frames");
    await store.saveDocument();
    if (store.saveState !== "saved") throw new Error(store.error || "Timeline save failed");
    const sequence = await store.materializeTrackSequence(target.value!.action!, target.value!.view!, target.value!.direction!);
    addedFrames.value = created;
    selected.value = [];
    commitState.value = "saved";
    commitMessage.value = t("frames.finalSaved", { mode: t(mode === "replace" ? "frames.replaced" : "frames.appended"), count: created.length, title: sequence.artifact.title });
  } catch (reason) {
    commitState.value = "error";
    commitMessage.value = reason instanceof Error ? reason.message : String(reason);
  }
}
async function finalizeTrack() {
  if (!targetReady.value || !timeline.value.length) return;
  commitState.value = "saving";
  commitMessage.value = t("frames.finalUpdating");
  try {
    const sequence = await store.materializeTrackSequence(target.value!.action!, target.value!.view!, target.value!.direction!);
    commitState.value = "saved";
    commitMessage.value = t("frames.finalUpdated", { count: sequence.frames.length });
  } catch (reason) {
    commitState.value = "error";
    commitMessage.value = reason instanceof Error ? reason.message : String(reason);
  }
}
async function replaceWithVariant(artifact: ArtifactRef, insert = false) {
  if (!redrawSourceFrameId.value) return;
  const original = timeline.value.find((item) => item.id === redrawSourceFrameId.value);
  if (!original) return;
  store.mutateDocument((document) => {
    const destination = ensureTrack(document);
    const index = destination.frames.findIndex((item) => item.id === redrawSourceFrameId.value);
    if (index < 0) return;
    if (insert) {
      destination.frames.splice(index + 1, 0, {
        ...JSON.parse(JSON.stringify(destination.frames[index])),
        id: crypto.randomUUID(),
        artifact: artifact.id,
        source_artifact: destination.frames[index].source_artifact,
        variant_of: original.artifact,
      });
    } else {
      destination.frames[index].variant_of = destination.frames[index].artifact;
      destination.frames[index].artifact = artifact.id;
    }
  }, insert ? "frame_variant_insert" : "frame_variant_replace");
  await store.saveDocument();
  await finalizeTrack();
  redrawMessage.value = t(insert ? "frames.variantInserted" : "frames.variantReplaced");
  store.lastOutputsByAction["frame.redraw"] = [];
}
function updateFrame(frame: FrameRef, patch: Partial<FrameRef>, operation = "frame_update") {
  store.mutateDocument((document) => { const found = ensureTrack(document).frames.find((item) => item.id === frame.id); if (found) Object.assign(found, patch); }, operation);
}
function removeFrame(frame: FrameRef) {
  store.mutateDocument((document) => { const destination = ensureTrack(document); destination.frames = destination.frames.filter((item) => item.id !== frame.id); }, "frame_delete");
  playIndex.value = Math.max(0, Math.min(playIndex.value, timeline.value.length - 2));
}
function duplicateFrame(frame: FrameRef) {
  store.mutateDocument((document) => { const destination = ensureTrack(document); const index = destination.frames.findIndex((item) => item.id === frame.id); destination.frames.splice(index + 1, 0, { ...JSON.parse(JSON.stringify(frame)), id: crypto.randomUUID() }); }, "frame_duplicate");
}
function moveFrame(from: number, to: number) {
  if (from === to) return;
  store.mutateDocument((document) => { const destination = ensureTrack(document); const [frame] = destination.frames.splice(from, 1); if (frame) destination.frames.splice(to, 0, frame); }, "frame_reorder");
}
function startFrameReorder(event: DragEvent, index: number) {
  if (!event.dataTransfer) return;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/frame-index", String(index));
}
function dropFrame(event: DragEvent, index: number) {
  const raw = event.dataTransfer?.getData("text/frame-index") || "";
  if (raw !== "") moveFrame(Number(raw), index);
}
function applyFps() {
  clipFps.value = Math.max(1, Math.min(60, Number(clipFps.value) || 10));
  if (!targetReady.value || !store.document) return;
  store.mutateDocument((document) => { ensureTrack(document).frames.forEach((frame) => frame.duration_ms = Math.round(1000 / clipFps.value)); }, "clip_fps");
}
function updateDuration(frame: FrameRef, value: number) { updateFrame(frame, { duration_ms: Math.max(16, Math.min(60000, Number(value) || 16)) }, "frame_duration"); }
function activateFrame(index: number, artifactId: string) { playIndex.value = index; const artifact = artifactMap.value.get(artifactId); if (artifact) emit("preview", artifact); }
function changeLoop() { if (targetReady.value && store.document) store.mutateDocument((document) => { const item = document.character?.clips.find((entry) => entry.action === target.value!.action); if (item) item.loop = loop.value; }, "clip_loop"); }
function togglePlay() { playing.value = !playing.value; scheduleFrame(); }
function scheduleFrame() {
  window.clearTimeout(playTimer);
  if (!playing.value || !timeline.value.length) return;
  const duration = timeline.value[playIndex.value]?.duration_ms || 100;
  playTimer = window.setTimeout(() => { playIndex.value = (playIndex.value + 1) % timeline.value.length; scheduleFrame(); }, duration);
}
function candidateKey(event: KeyboardEvent) {
  if (event.key === "Escape") { shortcuts.value = false; sourceTools.value = false; return; }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? store.redo() : store.undo(); return; }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "y") { event.preventDefault(); store.redo(); return; }
  if (event.key === " ") { event.preventDefault(); togglePlay(); return; }
  if (event.key === "?") { shortcuts.value = !shortcuts.value; return; }
  if (event.key === "ArrowRight") focusedCandidate.value = Math.min(candidates.value.length - 1, focusedCandidate.value + 1);
  if (event.key === "ArrowLeft") focusedCandidate.value = Math.max(0, focusedCandidate.value - 1);
  if (event.key === "Enter" && candidates.value[focusedCandidate.value]) selectCandidate(candidates.value[focusedCandidate.value], event.shiftKey);
  if (event.key === "Delete" && activeFrame.value) removeFrame(activeFrame.value);
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "d" && activeFrame.value) { event.preventDefault(); duplicateFrame(activeFrame.value); }
  const candidate = candidates.value[focusedCandidate.value]; if (candidate) emit("preview", candidate);
}

watch([playing, () => timeline.value.length], scheduleFrame);
watch(() => props.sequence?.artifact.id, () => { selected.value = []; focusedCandidate.value = 0; playIndex.value = 0; commitState.value = "idle"; });
watch(playIndex, () => { const artifact = artifactMap.value.get(activeFrame.value?.artifact || ""); if (artifact) emit("preview", artifact); });
onMounted(() => window.addEventListener("keydown", candidateKey));
onBeforeUnmount(() => { window.removeEventListener("keydown", candidateKey); window.clearTimeout(playTimer); });
</script>

<template>
  <section class="frame-studio" :class="{ 'is-empty': !sequence }" :aria-label="$t('frames.editor')">
    <div class="sequence-source-row">
      <div><span class="eyebrow">{{ $t('frames.currentCandidates') }}</span><strong>{{ sequence?.artifact.title || $t('frames.notLoaded') }}</strong><small v-if="sequence">{{ $t('frames.frameCountTarget', { count: candidates.length, target: targetLabel }) }}</small></div>
      <DropTarget :accepts="['FrameSeq']" :artifact="sequence?.artifact" :label="$t('frames.sequenceDrop')" :reason="$t('frames.sequenceReason')" @artifact="openSequence" />
    </div>
    <div v-if="!sequence" class="frame-studio-empty">
      <FileArrowDown :size="30" />
      <strong>{{ $t('frames.autoLoad') }}</strong>
      <span>{{ $t('frames.characterAbove') }}</span>
      <button class="arcade-button" type="button" @click="sourceTools = true"><FileArrowDown :size="16" />{{ $t("frames.importSource") }}</button>
    </div>
    <header v-if="sequence" class="frame-toolbar">
      <div class="playback-controls"><button class="icon-button" :aria-label="$t('frames.first')" @click="playIndex = 0"><SkipBack :size="18" /></button><button class="icon-button primary-icon" :aria-label="$t(playing ? 'frames.pause' : 'frames.play')" @click="togglePlay"><Pause v-if="playing" :size="18" weight="fill" /><Play v-else :size="18" weight="fill" /></button><button class="icon-button" :aria-label="$t('frames.next')" @click="playIndex = Math.min(timeline.length - 1, playIndex + 1)"><SkipForward :size="18" /></button></div>
      <span class="target-badge" :class="{ missing: !targetReady }">{{ targetLabel }}</span>
      <label class="mini-field">FPS <input v-model.number="clipFps" type="number" min="1" max="60" @change="applyFps" /></label>
      <label class="mini-field">LOOP <select v-model="loop" @change="changeLoop"><option value="none">NONE</option><option value="linear">LINEAR</option><option value="pingpong">PINGPONG</option></select></label>
      <button class="toggle-icon" :class="{ active: onion }" :aria-pressed="onion" @click="onion = !onion"><Eye :size="17" />{{ $t("frames.onion") }}</button>
      <button class="toggle-icon" :class="{ active: pixelDiff }" :aria-pressed="pixelDiff" @click="pixelDiff = !pixelDiff"><ArrowsClockwise :size="17" />{{ $t("frames.diff") }}</button>
      <button class="toggle-icon" :class="{ active: compare }" :aria-pressed="compare" @click="compare = !compare">A/B</button>
      <button class="toggle-icon" data-testid="redraw-frame" :disabled="!redrawArtifact" @click="redraw"><MagicWand :size="17" />{{ $t("frames.redraw") }}</button>
      <button class="toggle-icon" data-testid="import-frame-source" @click="sourceTools = true"><FileArrowDown :size="17" />{{ $t("frames.importSource") }}</button>
      <button class="icon-button" :aria-label="$t('frames.shortcuts')" @click="shortcuts = !shortcuts"><Keyboard :size="19" /></button>
    </header>

    <div v-if="sequence" class="candidate-row" data-testid="candidate-row" :data-candidate-count="candidates.length">
      <div ref="viewport" class="candidate-viewport" tabindex="0" @scroll="scrollLeft = ($event.target as HTMLElement).scrollLeft"><div class="virtual-track" :style="{ width: `${candidates.length * cardWidth}px` }"><ArtifactCard v-for="(artifact, index) in visibleCandidates" :key="`${artifact.id}:${start + index}`" class="virtual-card" :style="{ left: `${(start + index) * cardWidth}px` }" :artifact="artifact" :selected="selected.includes(start + index)" compact @select="(item, event) => { focusedCandidate = start + index; selectCandidate(item, event); }" @preview="emit('preview', $event)" /></div></div>
      <div class="candidate-commit-actions"><button class="arcade-button primary confirm-selection" :disabled="!selected.length || !targetReady || commitState === 'saving'" @click="commitSelection('replace')"><Check :size="17" weight="bold" />{{ $t('frames.replaceFinal', { count: selected.length || '' }) }}</button><button class="text-button" :disabled="!selected.length || !targetReady || commitState === 'saving'" @click="commitSelection('append')"><Plus :size="15" />{{ $t('frames.appendTrack') }}</button></div>
      <p v-if="commitMessage" class="commit-feedback" :class="commitState" role="status"><Check v-if="commitState === 'saved'" :size="15" /><Warning v-else-if="commitState === 'error'" :size="15" />{{ commitMessage }}</p>
    </div>

    <div v-if="sequence" class="timeline-row" :class="{ onion, 'pixel-diff': pixelDiff }">
      <div v-if="!timeline.length" class="empty-timeline">{{ $t("frames.empty") }}</div>
      <article v-for="(frame, index) in timeline" :key="frame.id" class="timeline-frame" :class="{ active: playIndex === index, added: addedFrames.includes(frame.id) }" @dragover.prevent @drop="dropFrame($event, index)" @click="activateFrame(index, frame.artifact)">
        <span class="frame-index">F{{ String(index + 1).padStart(2, "0") }}</span><button class="frame-drag-handle" draggable="true" :aria-label="$t('frames.dragSort')" @dragstart.stop="startFrameReorder($event, index)"><DotsSixVertical :size="15" /></button><span class="frame-image checker"><ArtifactVisual v-if="artifactMap.get(frame.artifact)" :artifact="artifactMap.get(frame.artifact)!" /></span><label><input :value="frame.duration_ms" type="number" min="16" max="60000" :aria-label="$t('frames.duration')" @change="updateDuration(frame, Number(($event.target as HTMLInputElement).value))" />ms</label><div class="offsets"><label>X<input :value="frame.offset_x" type="number" @change="updateFrame(frame, { offset_x: Number(($event.target as HTMLInputElement).value) }, 'frame_offset')" /></label><label>Y<input :value="frame.offset_y" type="number" @change="updateFrame(frame, { offset_y: Number(($event.target as HTMLInputElement).value) }, 'frame_offset')" /></label></div><div class="frame-actions"><button :aria-label="$t('frames.duplicate')" @click.stop="duplicateFrame(frame)"><Copy :size="14" /></button><button :aria-label="$t('frames.delete')" @click.stop="removeFrame(frame)"><Trash :size="14" /></button></div>
      </article>
    </div>
    <section v-if="redrawPending || redrawVariants.length" class="redraw-variants panel" aria-live="polite"><header><div><span class="eyebrow">{{ $t('frames.redrawVariants') }}</span><strong>{{ redrawPending ? $t('frames.redrawGenerating') : $t('frames.chooseVariant') }}</strong></div></header><div v-if="redrawVariants.length" class="redraw-variant-grid"><article v-for="artifact in redrawVariants" :key="artifact.id"><ArtifactCard :artifact="artifact" compact @select="emit('preview', $event)" @preview="emit('preview', $event)" /><div><button class="arcade-button primary" type="button" @click="replaceWithVariant(artifact)">{{ $t('frames.replaceCurrent') }}</button><button class="arcade-button" type="button" @click="replaceWithVariant(artifact, true)">{{ $t('frames.insertAfter') }}</button></div></article></div><p v-if="redrawMessage" role="status">{{ redrawMessage }}</p></section>
    <section v-if="timeline.length" class="final-sequence-bar"><div><span class="eyebrow">{{ $t('frames.finalSequence') }}</span><strong>{{ curatedSequence?.artifact.title || $t('frames.frameCountTarget', { count: timeline.length, target: targetLabel }) }}</strong><small>{{ curatedSequence ? $t('frames.reusableReady') : $t('frames.reusableNeeded') }}</small></div><button class="arcade-button" type="button" :disabled="commitState === 'saving'" @click="finalizeTrack">{{ $t('frames.updateFinal') }}</button><button v-if="curatedSequence" class="arcade-button primary" type="button" @click="emit('useNormal', curatedSequence.artifact)"><MagicWand :size="16" />{{ $t('frames.useSequenceNormal') }}</button></section>
    <div v-if="compare && compareArtifacts.length" class="compare-overlay" role="region" aria-label="A/B"><figure v-for="(artifact, index) in compareArtifacts" :key="`${artifact.id}:${index}`"><figcaption>{{ index === 0 ? 'A' : 'B' }} · {{ artifact.id.slice(0, 12) }}</figcaption><span class="checker"><ArtifactVisual :artifact="artifact" /></span></figure></div>
    <div v-if="shortcuts" class="shortcut-panel" role="dialog" :aria-label="$t('frames.shortcuts')"><button class="icon-button compact" :aria-label="$t('common.close')" @click="shortcuts = false"><X :size="16" /></button><strong>{{ $t("frames.keyMap") }}</strong><dl><dt>SPACE</dt><dd>{{ $t("frames.play") }} / {{ $t("frames.pause") }}</dd><dt>← →</dt><dd>{{ $t("frames.first") }} / {{ $t("frames.next") }}</dd><dt>ENTER</dt><dd>{{ $t("common.selected") }}</dd><dt>DELETE</dt><dd>{{ $t("frames.delete") }}</dd><dt>⌘D</dt><dd>{{ $t("frames.duplicate") }}</dd><dt>⌘Z / ⇧⌘Z</dt><dd>{{ $t("studio.undo") }} / {{ $t("studio.redo") }}</dd></dl></div>
    <div v-if="sourceTools" class="source-extractor-scrim" @click.self="sourceTools = false"><SourceExtractor :initial-action="target?.action" :initial-view="target?.view" :initial-direction="target?.direction" @close="sourceTools = false" @preview="emit('preview', $event)" /></div>
  </section>
</template>
