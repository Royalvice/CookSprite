<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { PhFilmStrip as FilmStrip, PhImageSquare as ImageSquare, PhPackage as Package } from "@phosphor-icons/vue";
import { api, type ArtifactRef } from "../api/generated";
import { beginArtifactDrag } from "../drag";
import { useStudioStore } from "../stores/studio";

const props = withDefaults(defineProps<{
  artifact: ArtifactRef;
  draggable?: boolean;
  animated?: boolean;
  alt?: string;
}>(), {
  draggable: true,
  animated: false,
  alt: "",
});

const store = useStudioStore();
const sequenceFrames = ref<ArtifactRef[]>([]);
const frameIndex = ref(0);
let frameTimer = 0;
let loadGeneration = 0;

const cover = computed(() => {
  if (props.artifact.media_type.startsWith("image/") || props.artifact.media_type.startsWith("video/")) return props.artifact;
  const coverId = props.artifact.meta.cover_artifact;
  return typeof coverId === "string" ? store.artifactById.get(coverId) : undefined;
});
const visibleArtifact = computed(() => {
  if (props.artifact.kind === "FrameSeq" && sequenceFrames.value.length) {
    return sequenceFrames.value[frameIndex.value % sequenceFrames.value.length];
  }
  return cover.value;
});
const isImage = computed(() => Boolean(visibleArtifact.value?.media_type.startsWith("image/")));
const isVideo = computed(() => Boolean(visibleArtifact.value?.media_type.startsWith("video/")));

function stopPlayback() {
  window.clearInterval(frameTimer);
  frameTimer = 0;
}
function startPlayback() {
  stopPlayback();
  if (!props.animated || sequenceFrames.value.length < 2) return;
  frameTimer = window.setInterval(() => {
    frameIndex.value = (frameIndex.value + 1) % sequenceFrames.value.length;
  }, 140);
}
async function loadSequence() {
  const generation = ++loadGeneration;
  sequenceFrames.value = [];
  frameIndex.value = 0;
  stopPlayback();
  if (props.artifact.kind !== "FrameSeq") return;
  try {
    const sequence = await api.sequence(props.artifact.id);
    if (generation !== loadGeneration) return;
    sequenceFrames.value = sequence.frames;
    startPlayback();
  } catch {
    // The typed sequence remains draggable even if its cover cannot be loaded.
  }
}
function drag(event: DragEvent) {
  if (props.draggable) beginArtifactDrag(event, props.artifact);
}

watch(() => props.artifact.id, loadSequence, { immediate: true });
watch(() => props.animated, startPlayback);
onBeforeUnmount(() => { loadGeneration += 1; stopPlayback(); });
</script>

<template>
  <span
    class="artifact-visual"
    :class="{ animated, draggable }"
    :draggable="draggable"
    :data-artifact-id="artifact.id"
    :data-artifact-kind="artifact.kind"
    :aria-label="alt || artifact.title || artifact.kind"
    @dragstart.stop="drag"
  >
    <img v-if="isImage && visibleArtifact" :src="visibleArtifact.url" :alt="alt" draggable="false" />
    <video v-else-if="isVideo && visibleArtifact" :src="visibleArtifact.url" muted loop :autoplay="animated" playsinline preload="metadata"></video>
    <FilmStrip v-else-if="artifact.kind === 'Video' || artifact.kind === 'FrameSeq'" :size="30" aria-hidden="true" />
    <Package v-else-if="artifact.kind === 'CookSpritePack'" :size="30" aria-hidden="true" />
    <ImageSquare v-else :size="30" aria-hidden="true" />
  </span>
</template>
