<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { PhArrowRight as ArrowRight, PhCheck as Check, PhCircleNotch as CircleNotch, PhMagicWand as MagicWand, PhSparkle as Sparkle } from "@phosphor-icons/vue";
import ArtifactCard from "./ArtifactCard.vue";
import ArtifactVisual from "./ArtifactVisual.vue";
import DropTarget from "./DropTarget.vue";
import { type ActionControl, type ActionDescriptor, type ArtifactKind, type ArtifactRef, type Locale } from "../api/generated";
import { useStudioStore } from "../stores/studio";

type ToolMode = "cutout" | "pixelize";

const store = useStudioStore();
const { locale } = useI18n();
const mode = ref<ToolMode>("cutout");
const source = ref<ArtifactRef | null>(null);
const output = ref<ArtifactRef | null>(null);
const error = ref("");
const values = ref<Record<string, unknown>>({ target_width: 128, target_height: 128 });

const actionId = computed(() => mode.value === "cutout" ? "image.cutout" : "image.pixelize");
const action = computed(() => store.actions.find((item) => item.id === actionId.value));
const accepts = computed<ArtifactKind[]>(() => {
  const declared = action.value?.accepts.source?.type;
  return declared ? (Array.isArray(declared) ? declared : [declared]) : ["Image"];
});
const pixelControls = computed(() => action.value?.controls.filter((control) => ["target_width", "target_height"].includes(control.id)) || []);
// Keep mode-local state in the UI, but only send controls declared by the
// active Action. This prevents pixelize values from leaking into cutout (and
// keeps future Tool controls similarly isolated).
const actionValues = computed<Record<string, unknown>>(() => {
  const declared = new Set((action.value?.controls || []).map((control) => control.id));
  return Object.fromEntries(Object.entries(values.value).filter(([id]) => declared.has(id)));
});
const running = computed(() => Boolean(store.activeRun && ["queued", "running"].includes(store.activeRun.status) && store.activeRun.action_id === actionId.value));
const canRun = computed(() => Boolean(source.value && action.value?.available && !running.value));
const modeName = computed(() => mode.value === "cutout" ? "studio.toolCutout" : "studio.toolPixelize");
const modeCode = computed(() => mode.value === "cutout" ? "REMOVE BG" : "PIXEL SNAP");
const actionText = computed(() => mode.value === "cutout" ? "studio.toolRunCutout" : "studio.toolRunPixelize");

function copy(control: ActionControl) { return control.i18n[locale.value as Locale]; }

function fillDefaults(next = action.value) {
  if (!next) return;
  if (mode.value === "cutout") {
    delete values.value.target_width;
    delete values.value.target_height;
  }
  for (const control of next.controls) if (values.value[control.id] === undefined) values.value[control.id] = JSON.parse(JSON.stringify(control.default));
  if (mode.value === "pixelize") {
    if (values.value.target_width === undefined) values.value.target_width = 128;
    if (values.value.target_height === undefined) values.value.target_height = 128;
  }
}

watch(action, fillDefaults, { immediate: true });
watch(() => store.lastOutputsByAction[actionId.value], (artifacts) => {
  if (artifacts?.length) output.value = artifacts[0];
});

function selectMode(next: ToolMode) {
  if (mode.value === next) return;
  mode.value = next;
  output.value = null;
  error.value = "";
  fillDefaults();
}

function acceptArtifact(payload: { artifact_id: string }) {
  const artifact = store.artifactById.get(payload.artifact_id) || null;
  if (!artifact) return;
  source.value = artifact;
  output.value = null;
  error.value = "";
}

async function importFiles(files: File[]) {
  for (const file of files) {
    try {
      source.value = await store.upload(file, "Image");
      output.value = null;
      error.value = "";
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason);
    }
  }
}

function clearSource() {
  source.value = null;
  output.value = null;
  error.value = "";
}

async function runTool() {
  if (!canRun.value || !source.value) return;
  error.value = "";
  output.value = null;
  const run = await store.runAction(actionId.value, { source: source.value.id }, actionValues.value);
  if (!run && store.error) error.value = store.error;
}

function continuePixelize() {
  if (!output.value) return;
  source.value = output.value;
  output.value = null;
  error.value = "";
  mode.value = "pixelize";
  fillDefaults();
}

function useOutput() {
  if (!output.value) return;
  source.value = output.value;
}
</script>

<template>
  <section class="tool-bench panel" :aria-label="$t('studio.toolBench')">
    <header class="tool-bench-header">
      <div>
        <span class="eyebrow">{{ $t("studio.toolBenchEyebrow") }}</span>
        <h2>{{ $t("studio.toolBench") }}</h2>
        <p>{{ $t("studio.toolBenchDescription") }}</p>
      </div>
      <span class="tool-bench-status" :class="{ ready: action?.available }">
        <Check v-if="action?.available" :size="14" />
        <span>{{ action?.available ? $t("studio.toolReady") : $t("studio.toolUnavailable") }}</span>
      </span>
    </header>

    <div class="tool-bench-tabs" role="tablist" :aria-label="$t('studio.toolBench')">
      <button type="button" role="tab" :aria-selected="mode === 'cutout'" :class="{ active: mode === 'cutout' }" @click="selectMode('cutout')">
        <MagicWand :size="17" />
        <span><b>{{ $t("studio.toolCutout") }}</b><small>REMOVE BG</small></span>
      </button>
      <button type="button" role="tab" :aria-selected="mode === 'pixelize'" :class="{ active: mode === 'pixelize' }" @click="selectMode('pixelize')">
        <Sparkle :size="17" />
        <span><b>{{ $t("studio.toolPixelize") }}</b><small>PIXEL SNAP</small></span>
      </button>
    </div>

    <div class="tool-bench-flow">
      <div class="tool-bench-column">
        <span class="tool-bench-label">{{ $t("studio.toolInput") }}</span>
        <DropTarget clearable :accepts="accepts" :artifact="source" :label="source?.title || $t('studio.toolDrop')" :reason="$t('studio.toolDropReason')" @artifact="acceptArtifact" @clear="clearSource" @files="importFiles" />
      </div>
      <ArrowRight class="tool-bench-arrow" :size="22" />
      <div class="tool-bench-column tool-output-column">
        <span class="tool-bench-label">{{ $t("studio.toolOutput") }}</span>
        <div v-if="output" class="tool-output-preview checker">
          <ArtifactVisual :artifact="output" />
          <span class="tool-output-kind">{{ output.kind }}</span>
        </div>
        <div v-else class="tool-output-empty">
          <Sparkle :size="24" />
          <span>{{ $t("studio.toolOutputEmpty") }}</span>
        </div>
        <button v-if="output" class="tool-output-link" type="button" @click="useOutput">{{ $t("studio.toolUseOutput") }}</button>
      </div>
    </div>

    <div v-if="mode === 'pixelize'" class="tool-bench-controls">
      <span class="tool-bench-label">{{ $t("studio.toolTargetSize") }}</span>
      <label v-for="control in pixelControls" :key="control.id" class="tool-size-field">
        <span>{{ copy(control).name }}</span>
        <input v-model.number="values[control.id]" type="number" :min="control.min" :max="control.max" :step="control.step" />
      </label>
    </div>
    <p v-if="error" class="tool-bench-error" role="alert">{{ error }}</p>
    <p v-else-if="!action?.available" class="tool-bench-hint">{{ action?.unavailable_reason || $t("studio.toolUnavailableHint") }}</p>
    <p v-else class="tool-bench-hint">{{ output ? $t("studio.toolResultHint") : $t("studio.toolDragHint") }}</p>

    <footer class="tool-bench-footer">
      <span><b>{{ modeCode }}</b><small>{{ $t(modeName) }}</small></span>
      <button class="arcade-button primary" type="button" :disabled="!canRun" @click="runTool">
        <CircleNotch v-if="running" class="spin" :size="17" />
        <Sparkle v-else :size="17" />
        {{ $t(actionText) }}
        <ArrowRight :size="15" />
      </button>
    </footer>

    <div v-if="output" class="tool-bench-result-card">
      <ArtifactCard :artifact="output" compact :selected="false" @select="useOutput" />
      <button v-if="mode === 'cutout'" class="text-button" type="button" @click="continuePixelize">{{ $t("studio.toolContinuePixelize") }}</button>
    </div>
  </section>
</template>
