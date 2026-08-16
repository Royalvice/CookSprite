<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { PhGearSix as GearSix, PhImagesSquare as ImagesSquare, PhLightning as Lightning, PhListBullets as ListBullets, PhMagicWand as MagicWand, PhSquaresFour as SquaresFour } from "@phosphor-icons/vue";
import { useStudioStore } from "../stores/studio";

defineEmits<{ "open-queue": [] }>();
const store = useStudioStore();
const { locale, t } = useI18n();
const runtimeLabel = computed(() => `COMFY · ${t(`common.${store.runtimeStatus}`)}`);

function swapLanguage() {
  locale.value = locale.value === "zh-CN" ? "en" : "zh-CN";
  localStorage.setItem("cooksprite.language", locale.value);
  document.documentElement.lang = locale.value;
}
</script>

<template>
  <header class="topbar">
    <RouterLink class="brand" to="/" :aria-label="$t('common.home')">
      <span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span>
      <span>
        <strong>COOKSPRITE</strong>
        <small>SPRITE FOUNDRY / 0.1</small>
      </span>
    </RouterLink>
    <nav class="desktop-nav" :aria-label="$t('common.mainNav')">
      <RouterLink to="/"><SquaresFour :size="18" />{{ $t("nav.gallery") }}</RouterLink>
      <RouterLink to="/studio"><MagicWand :size="18" />{{ $t("nav.studio") }}</RouterLink>
      <RouterLink to="/library"><ImagesSquare :size="18" />{{ $t("nav.library") }}</RouterLink>
      <RouterLink to="/settings"><GearSix :size="18" />{{ $t("nav.settings") }}</RouterLink>
    </nav>
    <div class="topbar-actions">
      <span class="runtime-chip" :class="[store.runtimeStatus]" :title="store.runtimeError">
        <Lightning :size="14" weight="fill" />{{ runtimeLabel }}
      </span>
      <button class="text-button language-button" :aria-label="locale === 'zh-CN' ? 'Switch to English' : '切换到中文'" @click="swapLanguage">
        {{ locale === "zh-CN" ? "EN" : "中" }}
      </button>
      <button class="queue-button" :aria-label="$t('queue.title')" @click="$emit('open-queue')">
        <ListBullets :size="19" /><span>{{ $t("common.queue") }}</span><b>{{ store.runningCount }}</b>
      </button>
    </div>
  </header>
</template>
