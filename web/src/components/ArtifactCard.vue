<script setup lang="ts">
import { PhCheck as Check, PhDotsThree as DotsThree } from "@phosphor-icons/vue";
import type { ArtifactRef } from "../api/generated";
import { beginArtifactDrag } from "../drag";
import ArtifactVisual from "./ArtifactVisual.vue";

const props = defineProps<{ artifact: ArtifactRef; selected?: boolean; compact?: boolean }>();
const emit = defineEmits<{ select: [artifact: ArtifactRef, event: MouseEvent]; preview: [artifact: ArtifactRef | null] }>();

function drag(event: DragEvent) { beginArtifactDrag(event, props.artifact); }
</script>

<template>
  <button
    class="artifact-card"
    :class="{ selected, compact }"
    draggable="true"
    :aria-pressed="selected"
    :aria-label="`${artifact.title || artifact.id}, ${artifact.kind}`"
    @dragstart="drag"
    @mouseenter="emit('preview', artifact)"
    @mouseleave="emit('preview', null)"
    @focus="emit('preview', artifact)"
    @blur="emit('preview', null)"
    @click="emit('select', artifact, $event)"
  >
    <span class="artifact-thumb checker">
      <ArtifactVisual :artifact="artifact" :draggable="false" />
      <span v-if="selected" class="selected-badge"><Check :size="14" weight="bold" />{{ $t("common.selected") }}</span>
    </span>
    <span class="artifact-copy">
      <strong>{{ artifact.title || artifact.id.slice(0, 12) }}</strong>
      <small>{{ artifact.kind }}</small>
    </span>
    <DotsThree :size="18" aria-hidden="true" />
  </button>
</template>
