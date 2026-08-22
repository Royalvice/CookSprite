<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { PhArrowsClockwise as ArrowsClockwise, PhCaretLeft as CaretLeft, PhCaretRight as CaretRight, PhCheck as Check, PhCopy as Copy, PhDotsSixVertical as DotsSixVertical, PhEye as Eye, PhFileArrowDown as FileArrowDown, PhKeyboard as Keyboard, PhMagicWand as MagicWand, PhPause as Pause, PhPlay as Play, PhPlus as Plus, PhSkipBack as SkipBack, PhSkipForward as SkipForward, PhTrash as Trash, PhWarning as Warning, PhX as X } from "@phosphor-icons/vue";
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
const hoveredCandidate = ref<number | null>(null);
const playing = ref(false);
const playIndex = ref(0);
const playDirection = ref(1);
const timelineIndex = ref(0);
const clipFps = ref(10);
const loop = ref<"none" | "linear" | "pingpong">("linear");
const onion = ref(false);
const pixelDiff = ref(false);
const compare = ref(false);
const shortcuts = ref(false);
const sourceTools = ref(false);
const candidatePage = ref(0);
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
  ? [target.value!.action, target.value!.view, target.value!.direction].filter(Boolean).join(" · ").toUpperCase()
  : t("frames.notLoaded"));
const clip = computed(() => store.document?.character?.clips.find((item) => item.action === target.value?.action));
const view = computed(() => clip.value?.views.find((item) => item.id === target.value?.view));
const track = computed(() => view.value?.tracks.find((item) => item.direction === target.value?.direction));
const timeline = computed(() => track.value?.frames || []);
const artifactMap = computed(() => store.artifactById);
const activeFrame = computed(() => timeline.value[timelineIndex.value]);
const selectedCandidates = computed(() => selected.value.slice().sort((a, b) => a - b).map((index) => candidates.value[index]).filter((item): item is ArtifactRef => Boolean(item)));
const playbackArtifacts = computed(() => selectedCandidates.value.length ? selectedCandidates.value : candidates.value);
const playerArtifact = computed(() => playbackArtifacts.value[Math.min(playIndex.value, Math.max(0, playbackArtifacts.value.length - 1))]);
const hoverArtifact = computed(() => candidates.value[hoveredCandidate.value ?? focusedCandidate.value] || playerArtifact.value);
const hoverPosition = computed(() => Math.min((hoveredCandidate.value ?? focusedCandidate.value) + 1, candidates.value.length));
const playerPosition = computed(() => Math.min(playIndex.value + 1, playbackArtifacts.value.length));
const redrawArtifact = computed(() => artifactMap.value.get(activeFrame.value?.artifact || "") || playerArtifact.value);
const compareArtifacts = computed(() => {
  const ids = selected.value.length >= 2
    ? selected.value.slice(-2).map((index) => candidates.value[index]?.id)
    : timeline.value.slice(Math.max(0, timelineIndex.value - 1), timelineIndex.value + 1).map((item) => item.artifact);
  return ids.map((id) => artifactMap.value.get(id || "") || candidates.value.find((item) => item.id === id)).filter((item): item is ArtifactRef => Boolean(item));
});
const candidatePageSize = 50;
const candidatePageCount = computed(() => Math.max(1, Math.ceil(candidates.value.length / candidatePageSize)));
const candidatePageStart = computed(() => candidatePage.value * candidatePageSize);
const pageCandidates = computed(() => candidates.value.slice(candidatePageStart.value, candidatePageStart.value + candidatePageSize));
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
  const actualIndex = candidates.value.findIndex((item) => item.id === artifact.id);
  if (actualIndex < 0) return;
  const range = typeof eventOrRange === "boolean" ? eventOrRange : Boolean(eventOrRange?.shiftKey);
  if (range && selected.value.length) {
    const anchor = selected.value[selected.value.length - 1];
    selected.value = Array.from({ length: Math.abs(actualIndex - anchor) + 1 }, (_, offset) => Math.min(anchor, actualIndex) + offset);
  } else if (selected.value.includes(actualIndex)) selected.value = selected.value.filter((value) => value !== actualIndex);
  else selected.value.push(actualIndex);
  focusedCandidate.value = actualIndex;
  emit("preview", artifact);
}
function previewCandidate(artifact: ArtifactRef | null, index: number) {
  // Artifact cards emit a null preview while the pointer crosses their small
  // visual gaps. Retain the last candidate instead of falling back to a
  // selected frame, so inspection never flickers or jumps between cards.
  if (!artifact) return;
  hoveredCandidate.value = index;
  emit("preview", artifact);
}
function selectAllCandidates() {
  selected.value = candidates.value.map((_, index) => index);
  focusedCandidate.value = 0;
  hoveredCandidate.value = null;
  playIndex.value = 0;
  playDirection.value = 1;
}
function clearCandidateSelection() {
  selected.value = [];
  playIndex.value = 0;
  playDirection.value = 1;
}
function moveCandidatePage(delta: number) {
  const next = Math.max(0, Math.min(candidatePageCount.value - 1, candidatePage.value + delta));
  if (next === candidatePage.value) return;
  candidatePage.value = next;
  const nextIndex = Math.min(candidatePageStart.value, Math.max(0, candidates.value.length - 1));
  focusedCandidate.value = nextIndex;
  hoveredCandidate.value = null;
  const candidate = candidates.value[nextIndex];
  if (candidate) emit("preview", candidate);
}
function stepPlayback(delta: number) {
  playing.value = false;
  playIndex.value = Math.max(0, Math.min(playbackArtifacts.value.length - 1, playIndex.value + delta));
  playDirection.value = delta >= 0 ? 1 : -1;
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
  timelineIndex.value = Math.max(0, Math.min(timelineIndex.value, timeline.value.length - 2));
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
function activateFrame(index: number, artifactId: string) { timelineIndex.value = index; const artifact = artifactMap.value.get(artifactId); if (artifact) emit("preview", artifact); }
function changeLoop() { if (targetReady.value && store.document) store.mutateDocument((document) => { const item = document.character?.clips.find((entry) => entry.action === target.value!.action); if (item) item.loop = loop.value; }, "clip_loop"); }
function togglePlay() { playing.value = !playing.value; scheduleFrame(); }
function advancePlayback() {
  const count = playbackArtifacts.value.length;
  if (!count) return;
  if (loop.value === "none") {
    if (playIndex.value >= count - 1) { playing.value = false; return; }
    playIndex.value += 1;
    return;
  }
  if (loop.value === "pingpong" && count > 1) {
    const next = playIndex.value + playDirection.value;
    if (next >= count) { playDirection.value = -1; playIndex.value = count - 2; return; }
    if (next < 0) { playDirection.value = 1; playIndex.value = 1; return; }
    playIndex.value = next;
    return;
  }
  playIndex.value = (playIndex.value + 1) % count;
}
function scheduleFrame() {
  window.clearTimeout(playTimer);
  if (!playing.value || !playbackArtifacts.value.length) return;
  const duration = Math.round(1000 / clipFps.value);
  playTimer = window.setTimeout(() => { advancePlayback(); scheduleFrame(); }, duration);
}
function candidateKey(event: KeyboardEvent) {
  if (event.key === "Escape") { shortcuts.value = false; sourceTools.value = false; return; }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? store.redo() : store.undo(); return; }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "y") { event.preventDefault(); store.redo(); return; }
  if (event.key === " ") { event.preventDefault(); togglePlay(); return; }
  if (event.key === "?") { shortcuts.value = !shortcuts.value; return; }
  if (event.key === "ArrowRight" && candidates.value.length) focusedCandidate.value = Math.min(candidates.value.length - 1, focusedCandidate.value + 1);
  if (event.key === "ArrowLeft" && candidates.value.length) focusedCandidate.value = Math.max(0, focusedCandidate.value - 1);
  if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && candidates.value.length) candidatePage.value = Math.floor(focusedCandidate.value / candidatePageSize);
  if (event.key === "Enter" && candidates.value[focusedCandidate.value]) selectCandidate(candidates.value[focusedCandidate.value], event.shiftKey);
  if (event.key === "Delete" && activeFrame.value) removeFrame(activeFrame.value);
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "d" && activeFrame.value) { event.preventDefault(); duplicateFrame(activeFrame.value); }
  const candidate = candidates.value[focusedCandidate.value]; if (candidate) emit("preview", candidate);
}

function editorStateKey() { return `cooksprite.frame-editor.${store.currentProject?.id || "default"}`; }
function restoreEditorState() {
  try {
    const saved = JSON.parse(localStorage.getItem(editorStateKey()) || "{}");
    if ([6, 8, 10, 12, 15, 20, 24].includes(Number(saved.fps))) clipFps.value = Number(saved.fps);
    if (["none", "linear", "pingpong"].includes(String(saved.loop))) loop.value = saved.loop;
    onion.value = saved.onion === true;
    pixelDiff.value = saved.pixelDiff === true;
    compare.value = saved.compare === true;
  } catch { /* Ignore stale presentation state. */ }
}
function persistEditorState() {
  localStorage.setItem(editorStateKey(), JSON.stringify({ fps: clipFps.value, loop: loop.value, onion: onion.value, pixelDiff: pixelDiff.value, compare: compare.value }));
}

watch([playing, () => playbackArtifacts.value.length, clipFps], scheduleFrame);
watch(() => playbackArtifacts.value.length, (length) => {
  if (playIndex.value >= length) playIndex.value = Math.max(0, length - 1);
  if (length < 2) playDirection.value = 1;
});
watch(() => timeline.value.length, (length) => { if (timelineIndex.value >= length) timelineIndex.value = Math.max(0, length - 1); });
watch(candidatePageCount, (count) => { candidatePage.value = Math.min(candidatePage.value, Math.max(0, count - 1)); });
watch(() => props.sequence?.artifact.id, () => { selected.value = []; candidatePage.value = 0; focusedCandidate.value = 0; hoveredCandidate.value = null; playIndex.value = 0; timelineIndex.value = 0; playDirection.value = 1; commitState.value = "idle"; });
watch(() => playerArtifact.value?.id, () => { if (playerArtifact.value) emit("preview", playerArtifact.value); });
watch([clipFps, loop, onion, pixelDiff, compare], persistEditorState);
onMounted(() => { restoreEditorState(); window.addEventListener("keydown", candidateKey); });
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
    <section v-if="sequence" class="frame-player-workspace">
      <section class="frame-preview-panel frame-hover-preview">
        <header class="frame-preview-heading"><div><span class="eyebrow">{{ $t("frames.hoverPreview") }}</span><strong>{{ targetLabel }}</strong><small>{{ $t("frames.hoverPreviewHint") }}</small></div><b v-if="candidates.length">F{{ String(hoverPosition).padStart(2, "0") }} / {{ candidates.length }}</b></header>
        <div class="frame-player checker" data-testid="hover-preview">
          <ArtifactVisual v-if="hoverArtifact" :artifact="hoverArtifact" :draggable="false" />
          <span v-else>{{ $t("frames.previewEmpty") }}</span>
        </div>
      </section>
      <section class="frame-preview-panel frame-playback-preview">
        <header class="frame-preview-heading"><div><span class="eyebrow">{{ $t("frames.player") }}</span><strong>{{ targetLabel }}</strong><small>{{ selected.length ? $t("frames.playingSelected", { count: selected.length }) : $t("frames.playingCandidates") }}</small></div><b v-if="playbackArtifacts.length">{{ playerPosition }} / {{ playbackArtifacts.length }}</b></header>
        <div class="frame-player checker" data-testid="animation-preview" aria-live="off">
          <ArtifactVisual v-if="playerArtifact" :artifact="playerArtifact" :draggable="false" />
          <span v-else>{{ $t("frames.previewEmpty") }}</span>
        </div>
        <div class="frame-player-panel">
          <div class="playback-controls frame-player-controls">
            <button class="icon-button" :aria-label="$t('frames.first')" @click="playing = false; playIndex = 0; playDirection = 1"><SkipBack :size="18" /></button>
            <button class="icon-button" :aria-label="$t('frames.previous')" @click="stepPlayback(-1)"><CaretLeft :size="19" /></button>
            <button class="icon-button primary-icon" :aria-label="$t(playing ? 'frames.pause' : 'frames.play')" :disabled="!playbackArtifacts.length" @click="togglePlay"><Pause v-if="playing" :size="18" weight="fill" /><Play v-else :size="18" weight="fill" /></button>
            <button class="icon-button" :aria-label="$t('frames.next')" @click="stepPlayback(1)"><CaretRight :size="19" /></button>
            <button class="icon-button" :aria-label="$t('frames.last')" @click="playing = false; playIndex = Math.max(0, playbackArtifacts.length - 1); playDirection = -1"><SkipForward :size="18" /></button>
          </div>
          <div class="frame-player-settings">
            <label class="mini-field">FPS <select v-model.number="clipFps" @change="applyFps"><option v-for="fps in [6, 8, 10, 12, 15, 20, 24]" :key="fps" :value="fps">{{ fps }}</option></select></label>
            <label class="mini-field">LOOP <select v-model="loop" @change="changeLoop"><option value="none">NONE</option><option value="linear">LINEAR</option><option value="pingpong">PINGPONG</option></select></label>
          </div>
          <div class="playback-frame-dots" role="list" :aria-label="$t('frames.playbackDots', { current: playerPosition, total: playbackArtifacts.length })" data-testid="playback-dots">
            <span v-for="(_, index) in playbackArtifacts" :key="`playback-dot-${index}`" class="playback-frame-dot" :class="{ active: index === playIndex }" role="listitem" :aria-current="index === playIndex ? 'step' : undefined" :aria-label="$t('frames.playbackFrame', { current: index + 1, total: playbackArtifacts.length })" :title="$t('frames.playbackFrame', { current: index + 1, total: playbackArtifacts.length })" data-testid="playback-dot"></span>
          </div>
        </div>
      </section>
    </section>

    <section v-if="sequence" class="candidate-workspace" data-testid="candidate-row" :data-candidate-count="candidates.length">
      <header class="candidate-heading">
        <div><span class="eyebrow">{{ $t("frames.currentCandidates") }}</span><strong>{{ $t("frames.pickBestFrames") }}</strong><small>{{ $t("frames.pickHelp") }}</small></div>
        <div class="candidate-heading-actions"><b>{{ selected.length }} / {{ candidates.length }}</b><nav v-if="candidatePageCount > 1" class="candidate-pager" :aria-label="$t('frames.candidatePages')"><button class="icon-button" type="button" :aria-label="$t('frames.previousPage')" :disabled="candidatePage === 0" @click="moveCandidatePage(-1)"><CaretLeft :size="17" /></button><span>{{ $t('frames.candidatePage', { current: candidatePage + 1, total: candidatePageCount }) }}</span><button class="icon-button" type="button" :aria-label="$t('frames.nextPage')" :disabled="candidatePage >= candidatePageCount - 1" @click="moveCandidatePage(1)"><CaretRight :size="17" /></button></nav><button class="text-button" type="button" @click="selectAllCandidates">{{ $t("frames.selectAll") }}</button><button class="text-button" type="button" :disabled="!selected.length" @click="clearCandidateSelection">{{ $t("frames.clearSelection") }}</button></div>
      </header>
      <div class="candidate-row">
        <div class="candidate-viewport" data-testid="candidate-grid" :data-page-size="candidatePageSize" :data-page-count="candidatePageCount"><div class="candidate-grid" role="list"><div v-for="(artifact, index) in pageCandidates" :key="`${artifact.id}:${candidatePageStart + index}`" class="candidate-frame" :class="{ selected: selected.includes(candidatePageStart + index) }" role="listitem"><ArtifactCard :artifact="artifact" :selected="selected.includes(candidatePageStart + index)" compact @select="(item, event) => { focusedCandidate = candidatePageStart + index; selectCandidate(item, event); }" @preview="previewCandidate($event, candidatePageStart + index)" /><span>F{{ String(candidatePageStart + index + 1).padStart(2, "0") }}</span><b v-if="selected.includes(candidatePageStart + index)">{{ selected.indexOf(candidatePageStart + index) + 1 }}</b></div></div></div>
        <div class="candidate-commit-actions"><button class="arcade-button primary confirm-selection" :disabled="!selected.length || !targetReady || commitState === 'saving'" @click="commitSelection('replace')"><Check :size="17" weight="bold" />{{ $t('frames.replaceFinal', { count: selected.length || '' }) }}</button><button class="text-button" :disabled="!selected.length || !targetReady || commitState === 'saving'" @click="commitSelection('append')"><Plus :size="15" />{{ $t('frames.appendTrack') }}</button></div>
        <p v-if="commitMessage" class="commit-feedback" :class="commitState" role="status"><Check v-if="commitState === 'saved'" :size="15" /><Warning v-else-if="commitState === 'error'" :size="15" />{{ commitMessage }}</p>
      </div>
    </section>

    <header v-if="sequence" class="frame-toolbar frame-edit-toolbar">
      <span class="target-badge" :class="{ missing: !targetReady }">{{ targetLabel }}</span>
      <button class="toggle-icon" :class="{ active: onion }" :aria-pressed="onion" @click="onion = !onion"><Eye :size="17" />{{ $t("frames.onion") }}</button>
      <button class="toggle-icon" :class="{ active: pixelDiff }" :aria-pressed="pixelDiff" @click="pixelDiff = !pixelDiff"><ArrowsClockwise :size="17" />{{ $t("frames.diff") }}</button>
      <button class="toggle-icon" :class="{ active: compare }" :aria-pressed="compare" @click="compare = !compare">A/B</button>
      <button class="toggle-icon" data-testid="redraw-frame" :disabled="!redrawArtifact" @click="redraw"><MagicWand :size="17" />{{ $t("frames.redraw") }}</button>
      <button class="toggle-icon" data-testid="import-frame-source" @click="sourceTools = true"><FileArrowDown :size="17" />{{ $t("frames.importSource") }}</button>
      <button class="icon-button" :aria-label="$t('frames.shortcuts')" @click="shortcuts = !shortcuts"><Keyboard :size="19" /></button>
    </header>

    <div v-if="sequence" class="timeline-row" :class="{ onion, 'pixel-diff': pixelDiff }">
      <div v-if="!timeline.length" class="empty-timeline">{{ $t("frames.empty") }}</div>
      <article v-for="(frame, index) in timeline" :key="frame.id" class="timeline-frame" :class="{ active: timelineIndex === index, added: addedFrames.includes(frame.id) }" @dragover.prevent @drop="dropFrame($event, index)" @click="activateFrame(index, frame.artifact)">
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
