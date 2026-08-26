<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { PhGridFour as GridFour, PhMagicWand as MagicWand, PhVideoCamera as VideoCamera, PhX as X } from "@phosphor-icons/vue";
import type { ActionControl, ArtifactKind, ArtifactRef, Locale } from "../api/generated";
import { useStudioStore } from "../stores/studio";
import DropTarget from "./DropTarget.vue";

const props = defineProps<{ initialAction?: string; initialView?: string; initialDirection?: string }>();
const emit = defineEmits<{ close: []; preview: [artifact: ArtifactRef] }>();
const store = useStudioStore();
const { locale } = useI18n();
const mode = ref<"sheet.slice" | "video.sample">("sheet.slice");
const source = ref<ArtifactRef | null>(null);
const values = ref<Record<string, unknown>>({});
const action = computed(() => store.actions.find((item) => item.id === mode.value));
const slot = computed(() => mode.value === "sheet.slice" ? "sheet" : "video");
const accepts = computed<ArtifactKind[]>(() => {
  const declared = action.value?.accepts[slot.value]?.type;
  return declared ? (Array.isArray(declared) ? declared : [declared]) : [];
});
const visibleControls = computed(() => (action.value?.controls || []).filter((control) => !["prompt", "prompt_compile", "view", "direction"].includes(control.id)));
const motionActions = new Set(["idle", "walk", "run", "jump", "roll"]);
function optionsFor(control: ActionControl) {
  return control.id === "action" ? control.options.filter((option) => motionActions.has(option.id)) : control.options;
}

watch(action, (next) => {
  values.value = Object.fromEntries((next?.controls || []).map((control) => [control.id, JSON.parse(JSON.stringify(control.default))]));
  if (props.initialAction) values.value.action = props.initialAction;
  values.value.view = props.initialView || "level";
  values.value.direction = props.initialDirection || "s";
  if (!motionActions.has(String(values.value.action || ""))) values.value.action = "walk";
}, { immediate: true });

function copy(control: ActionControl) { return control.i18n[locale.value as Locale]; }
function acceptArtifact(payload: { artifact_id: string }) {
  source.value = store.artifactById.get(payload.artifact_id) || null;
  if (source.value) emit("preview", source.value);
}
async function importFiles(files: File[]) {
  if (!files[0]) return;
  source.value = await store.upload(files[0], mode.value === "sheet.slice" ? "SpriteSheet" : "Video");
  emit("preview", source.value);
}
async function suggestGrid() {
  if (!source.value || !source.value.media_type.startsWith("image/")) return;
  const image = new Image();
  image.src = source.value.url;
  await image.decode();
  const sizes = [256, 192, 128, 96, 64, 48, 32, 24, 16];
  const cell = sizes.find((size) => image.naturalWidth % size === 0 && image.naturalHeight % size === 0 && (image.naturalWidth / size) * (image.naturalHeight / size) >= 2) || Math.max(1, Math.min(image.naturalWidth, image.naturalHeight));
  values.value.frame_width = cell;
  values.value.frame_height = cell;
  values.value.columns = Math.max(1, Math.floor(image.naturalWidth / cell));
  values.value.rows = Math.max(1, Math.floor(image.naturalHeight / cell));
}
async function extract() {
  if (!source.value || !action.value) return;
  const actionValues = Object.fromEntries(action.value.controls.map((control) => [
    control.id,
    values.value[control.id] ?? control.default,
  ]));
  await store.runAction(action.value.id, { [slot.value]: source.value.id }, actionValues);
  emit("close");
}
</script>

<template>
  <section class="source-extractor panel" role="dialog" aria-modal="true" :aria-label="$t('frames.importCandidates')">
    <header>
      <div><span class="eyebrow">{{ $t("frames.frameSource") }}</span><h2>{{ $t("frames.importCandidates") }}</h2></div>
      <button class="icon-button compact" :aria-label="$t('frames.closeImport')" @click="emit('close')"><X :size="17" /></button>
    </header>
    <div class="extractor-tabs" role="tablist">
      <button :class="{ active: mode === 'sheet.slice' }" role="tab" :aria-selected="mode === 'sheet.slice'" @click="mode = 'sheet.slice'; source = null"><GridFour :size="17" />SPRITESHEET</button>
      <button :class="{ active: mode === 'video.sample' }" role="tab" :aria-selected="mode === 'video.sample'" @click="mode = 'video.sample'; source = null"><VideoCamera :size="17" />GIF / VIDEO</button>
    </div>
    <div class="extractor-body">
      <DropTarget :accepts="accepts" :artifact="source" :label="source ? source.title || source.id : $t(mode === 'sheet.slice' ? 'frames.dropSheet' : 'frames.dropVideo')" @artifact="acceptArtifact" @files="importFiles" />
      <div v-if="action" class="extractor-controls">
        <template v-for="control in visibleControls" :key="control.id">
          <label v-if="control.type === 'select'"><span>{{ copy(control).name }}</span><select v-model="values[control.id]"><option v-for="option in optionsFor(control)" :key="option.id" :value="option.id">{{ option.i18n[locale as Locale].name }}</option></select></label>
          <label v-else-if="control.type === 'number' || control.type === 'range'"><span>{{ copy(control).name }}</span><input v-model.number="values[control.id]" type="number" :min="control.min" :max="control.max" :step="control.step" /></label>
          <label v-else-if="control.type === 'toggle'" class="check-field"><input v-model="values[control.id]" type="checkbox" /><span>{{ copy(control).name }}</span></label>
        </template>
      </div>
    </div>
    <footer>
      <button v-if="mode === 'sheet.slice'" class="arcade-button" data-testid="auto-grid" :disabled="!source" @click="suggestGrid"><GridFour :size="16" />{{ $t("frames.autoGrid") }}</button>
      <button class="arcade-button primary" data-testid="extract-source" :disabled="!source || !action?.available" @click="extract"><MagicWand :size="17" />{{ action?.i18n[locale as Locale].name || 'EXTRACT' }}</button>
    </footer>
  </section>
</template>
