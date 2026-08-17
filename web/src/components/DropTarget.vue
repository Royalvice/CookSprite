<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { PhArrowSquareIn as ArrowSquareIn, PhCheck as Check, PhUploadSimple as UploadSimple, PhWarningCircle as WarningCircle, PhX as X } from "@phosphor-icons/vue";
import { inferArtifactKind, type ArtifactKind, type ArtifactRef } from "../api/generated";
import { ARTIFACT_MIME, activeArtifactDrag, decodeArtifact } from "../drag";
import { useStudioStore } from "../stores/studio";
import ArtifactVisual from "./ArtifactVisual.vue";

const props = defineProps<{ accepts: ArtifactKind[]; label: string; artifact?: ArtifactRef | null; reason?: string; multiple?: boolean; clearable?: boolean }>();
const emit = defineEmits<{ artifact: [payload: { artifact_id: string; kind: ArtifactKind }]; files: [files: File[]]; clear: [] }>();
const store = useStudioStore();
const state = ref<"idle" | "compatible" | "incompatible" | "success" | "error">("idle");
const message = ref("");
const input = ref<HTMLInputElement | null>(null);
const picker = ref(false);
const acceptedId = ref("");
const localPreview = ref("");
let dragDepth = 0;
let resetTimer = 0;
const displayArtifact = computed(() => store.artifactById.get(acceptedId.value) || props.artifact || null);
const displayTitle = computed(() => displayArtifact.value?.title || displayArtifact.value?.id || props.label);
const choices = computed(() => [...store.artifactById.values()].filter((item, index, items) => (
  props.accepts.includes(item.kind)
  && !item.trashed
  && item.meta.system !== true
  && items.findIndex((candidate) => candidate.id === item.id) === index
)));

function artifactFromTransfer(transfer: DataTransfer | null | undefined): ArtifactRef | null {
  if (!transfer) return null;
  const direct = decodeArtifact(transfer.getData(ARTIFACT_MIME) || "") || activeArtifactDrag();
  const artifactId = direct?.artifact_id || (transfer.getData("text/plain") || "").trim();
  const artifact = store.artifactById.get(artifactId);
  if (!artifact || (direct && direct.kind !== artifact.kind)) return null;
  return artifact;
}

function setTemporary(next: "success" | "error", text: string) {
  window.clearTimeout(resetTimer);
  state.value = next;
  message.value = text;
  resetTimer = window.setTimeout(() => { state.value = "idle"; message.value = ""; }, 1600);
}
function clearLocalPreview() {
  if (localPreview.value) URL.revokeObjectURL(localPreview.value);
  localPreview.value = "";
}
function previewFile(file: File | undefined) {
  clearLocalPreview();
  if (file?.type.startsWith("image/")) localPreview.value = URL.createObjectURL(file);
}
function inspect(event: DragEvent) {
  const artifact = artifactFromTransfer(event.dataTransfer);
  if (artifact && !props.accepts.includes(artifact.kind)) {
    state.value = "incompatible";
    message.value = props.reason || "这个素材不能用于这里 / Incompatible asset";
  } else if (artifact || event.dataTransfer?.files.length || event.dataTransfer?.types.includes("Files")) {
    state.value = "compatible";
    message.value = "";
  } else if (event.dataTransfer?.types.includes(ARTIFACT_MIME)) {
    state.value = "compatible";
    message.value = "";
  } else {
    state.value = "incompatible";
    message.value = props.reason || "请拖入素材卡或文件 / Use an artifact card or file";
  }
}
function enter(event: DragEvent) {
  event.preventDefault();
  dragDepth += 1;
  inspect(event);
}
function over(event: DragEvent) {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = state.value === "incompatible" ? "none" : "copy";
  inspect(event);
}
function leave(event: DragEvent) {
  event.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) state.value = "idle";
}
function accept(payload: { artifact_id: string; kind: ArtifactKind }) {
  if (!props.accepts.includes(payload.kind)) {
    setTemporary("error", props.reason || "这个素材不能用于这里 / Incompatible asset");
    return;
  }
  acceptedId.value = payload.artifact_id;
  clearLocalPreview();
  emit("artifact", payload);
  picker.value = false;
  setTemporary("success", "已接收 / Added");
}
function clearArtifact() {
  acceptedId.value = "";
  clearLocalPreview();
  picker.value = false;
  emit("clear");
}
function drop(event: DragEvent) {
  event.preventDefault();
  dragDepth = 0;
  const artifact = artifactFromTransfer(event.dataTransfer);
  if (artifact) {
    accept({ artifact_id: artifact.id, kind: artifact.kind });
    return;
  }
  const incoming = [...(event.dataTransfer?.files || [])];
  const files = (props.multiple ? incoming : incoming.slice(0, 1)).filter((file) => {
    const kind = inferArtifactKind(file);
    return Boolean(kind && props.accepts.includes(kind));
  });
  if (!files.length) {
    setTemporary("error", props.reason || "不支持这个文件 / Unsupported file");
    return;
  }
  previewFile(files[0]);
  emit("files", files);
  setTemporary("success", `已接收 ${files.length} 个文件 / Added`);
}
function fileChange(event: Event) {
  const incoming = [...((event.target as HTMLInputElement).files || [])];
  previewFile(incoming[0]);
  if (incoming.length) emit("files", props.multiple ? incoming : incoming.slice(0, 1));
  if (incoming.length) setTemporary("success", `已接收 ${incoming.length} 个文件 / Added`);
  (event.target as HTMLInputElement).value = "";
}

watch(
  () => props.artifact?.id,
  (id) => {
    acceptedId.value = id || "";
    if (id) clearLocalPreview();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  window.clearTimeout(resetTimer);
  clearLocalPreview();
});
</script>

<template>
  <div
    class="drop-target"
    :class="[state, { 'has-artifact': displayArtifact || localPreview }]"
    :data-drop-state="state"
    tabindex="0"
    @dragenter="enter"
    @dragover="over"
    @dragleave="leave"
    @drop="drop"
    @keydown.esc="picker = false"
  >
    <ArtifactVisual v-if="displayArtifact" class="drop-preview checker" :artifact="displayArtifact" :draggable="false" />
    <span v-else-if="localPreview" class="drop-preview checker"><img :src="localPreview" alt="" /></span>
    <Check v-else-if="state === 'success'" :size="24" weight="bold" />
    <WarningCircle v-else-if="state === 'incompatible' || state === 'error'" :size="24" weight="fill" />
    <ArrowSquareIn v-else-if="state === 'compatible'" :size="24" />
    <UploadSimple v-else :size="24" />
    <strong>{{ state === "compatible" ? "松开以使用 / Drop to use" : displayArtifact || localPreview ? displayTitle : message || label }}</strong>
    <span class="drop-meta">{{ displayArtifact ? `${displayArtifact.kind} · 已选择` : localPreview ? "正在导入图片…" : "CookSprite Artifact · 可用素材" }}</span>
    <button v-if="clearable && displayArtifact" class="text-button" type="button" @click="clearArtifact">{{ $t("common.clear") }}</button>
    <button v-else class="text-button" type="button" @click="picker = !picker">{{ $t("common.select") }}</button>
    <input ref="input" class="visually-hidden" type="file" :multiple="multiple" :aria-label="`${label} / Import file`" accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif,video/mp4,video/webm,.hdr,.exr" @change="fileChange" />
    <div v-if="picker" class="drop-picker" role="dialog" :aria-label="$t('common.select')">
      <header><strong>{{ $t("common.select") }}</strong><button class="icon-button compact" type="button" :aria-label="$t('common.close')" @click="picker = false"><X :size="14" /></button></header>
      <button v-for="artifact in choices" :key="artifact.id" type="button" @click="accept({ artifact_id: artifact.id, kind: artifact.kind })"><span>{{ artifact.title || artifact.id.slice(0, 14) }}</span><small>{{ artifact.kind }}</small></button>
      <p v-if="!choices.length">{{ $t("common.empty") }}</p>
      <button class="arcade-button" type="button" @click="input?.click()"><UploadSimple :size="15" />{{ $t("common.import") }}</button>
    </div>
    <span class="visually-hidden" aria-live="polite">{{ message }}</span>
  </div>
</template>
