<script setup lang="ts">
import { PhExport as Export, PhFilmStrip as FilmStrip, PhImageSquare as ImageSquare, PhMagicWand as MagicWand, PhSparkle as Sparkle } from "@phosphor-icons/vue";

defineProps<{ active: string }>();
defineEmits<{ select: [id: string]; next: [] }>();
const stages = [
  { id: "create", label: "studio.stageCreate", icon: ImageSquare },
  { id: "animate", label: "studio.stageAnimate", icon: FilmStrip },
  { id: "normal", label: "studio.stageNormal", icon: Sparkle },
  { id: "export", label: "studio.stageExport", icon: Export },
];
</script>

<template>
  <nav class="stage-rail" :aria-label="$t('studio.title')">
    <span class="rail-label">{{ $t("studio.title") }}</span>
    <button v-for="(stage, index) in stages" :key="stage.id" :class="{ active: active === stage.id }" :aria-current="active === stage.id ? 'step' : undefined" @click="$emit('select', stage.id)">
      <span class="stage-number">0{{ index + 1 }}</span>
      <component :is="stage.icon" :size="21" />
      <span>{{ $t(stage.label) }}</span>
    </button>
    <button v-if="active !== 'export'" type="button" class="recommended" @click="$emit('next')"><MagicWand :size="15" /><span>→</span><strong>{{ active === "create" ? $t("studio.stageAnimate") : active === "animate" ? $t("studio.stageNormal") : $t("studio.stageExport") }}</strong></button>
  </nav>
</template>
