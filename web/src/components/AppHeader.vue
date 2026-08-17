<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { PhCaretDown as CaretDown, PhCheck as Check, PhGearSix as GearSix, PhImagesSquare as ImagesSquare, PhListBullets as ListBullets, PhMagicWand as MagicWand, PhSquaresFour as SquaresFour } from "@phosphor-icons/vue";
import { useStudioStore } from "../stores/studio";

defineEmits<{ "open-queue": [] }>();
const store = useStudioStore();
const { locale, t } = useI18n();
const runtimeSwitching = ref(false);
const runtimeMenuOpen = ref(false);
const runtimeTrigger = ref<HTMLButtonElement | null>(null);
const runtimeOptionRefs = ref<HTMLButtonElement[]>([]);
const activeRuntime = computed(() => store.runtimes.find((item) => item.id === store.activeRuntimeId));
const activeRuntimeIndex = computed(() => Math.max(0, store.runtimes.findIndex((item) => item.id === store.activeRuntimeId)));

function swapLanguage() {
  locale.value = locale.value === "zh-CN" ? "en" : "zh-CN";
  localStorage.setItem("cooksprite.language", locale.value);
  document.documentElement.lang = locale.value;
}

function runtimeStatusClass(runtime: typeof activeRuntime.value) {
  return runtime?.status || "offline";
}

function runtimeStatusLabel(runtime: typeof activeRuntime.value) {
  if (runtime?.status === "ready") return t("common.ready");
  if (runtime?.status === "unconfigured") return t("common.unconfigured");
  return t("common.offline");
}

function runtimeLocationLabel(runtime: typeof activeRuntime.value) {
  return runtime?.location === "local" ? t("settings.local") : t("settings.remote");
}

function setRuntimeOptionRef(element: unknown, index: number) {
  if (element instanceof HTMLButtonElement) runtimeOptionRefs.value[index] = element;
}

function closeRuntimeMenu(restoreFocus = false) {
  runtimeMenuOpen.value = false;
  runtimeOptionRefs.value = [];
  if (restoreFocus) void nextTick(() => runtimeTrigger.value?.focus());
}

async function openRuntimeMenu(index = activeRuntimeIndex.value) {
  if (runtimeSwitching.value || !store.runtimes.length) return;
  runtimeMenuOpen.value = true;
  await nextTick();
  runtimeOptionRefs.value[index]?.focus();
}

function toggleRuntimeMenu() {
  if (runtimeMenuOpen.value) closeRuntimeMenu();
  else void openRuntimeMenu();
}

function focusRuntimeOption(index: number) {
  const count = store.runtimes.length;
  if (!count) return;
  const nextIndex = (index + count) % count;
  void nextTick(() => runtimeOptionRefs.value[nextIndex]?.focus());
}

function handleRuntimeTriggerKeydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    void openRuntimeMenu(event.key === "ArrowUp" ? activeRuntimeIndex.value - 1 : activeRuntimeIndex.value);
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    toggleRuntimeMenu();
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeRuntimeMenu();
  }
}

function handleRuntimeOptionKeydown(event: KeyboardEvent, index: number, id: string) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    focusRuntimeOption(index + 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    focusRuntimeOption(index - 1);
  } else if (event.key === "Home") {
    event.preventDefault();
    focusRuntimeOption(0);
  } else if (event.key === "End") {
    event.preventDefault();
    focusRuntimeOption(store.runtimes.length - 1);
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    void selectRuntime(id);
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeRuntimeMenu(true);
  }
}

async function selectRuntime(id: string) {
  closeRuntimeMenu();
  if (!id || id === store.activeRuntimeId) {
    void nextTick(() => runtimeTrigger.value?.focus());
    return;
  }
  runtimeSwitching.value = true;
  try { await store.selectRuntime(id); }
  finally {
    runtimeSwitching.value = false;
    void nextTick(() => runtimeTrigger.value?.focus());
  }
}

function handleDocumentPointerdown(event: PointerEvent) {
  const target = event.target;
  if (target instanceof Element && !target.closest(".runtime-selector")) closeRuntimeMenu();
}

function handleDocumentFocusin(event: FocusEvent) {
  const target = event.target;
  if (target instanceof Element && !target.closest(".runtime-selector")) closeRuntimeMenu();
}

onMounted(() => {
  document.addEventListener("pointerdown", handleDocumentPointerdown);
  document.addEventListener("focusin", handleDocumentFocusin);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", handleDocumentPointerdown);
  document.removeEventListener("focusin", handleDocumentFocusin);
});
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
      <div v-if="store.runtimes.length" class="runtime-selector">
        <span class="runtime-kicker">COMFY</span>
        <button
          ref="runtimeTrigger"
          class="runtime-trigger"
          type="button"
          :disabled="runtimeSwitching"
          :title="activeRuntime?.error || activeRuntime?.base_url"
          aria-haspopup="listbox"
          :aria-expanded="runtimeMenuOpen"
          aria-controls="runtime-menu"
          aria-label="Current ComfyUI runtime"
          @click.stop="toggleRuntimeMenu"
          @keydown="handleRuntimeTriggerKeydown"
        >
          <span class="runtime-status-dot" :class="runtimeStatusClass(activeRuntime)" aria-hidden="true"></span>
          <span class="runtime-trigger-label">{{ activeRuntime?.label || "ComfyUI" }}</span>
          <CaretDown :size="16" aria-hidden="true" />
        </button>
        <div v-if="runtimeMenuOpen" id="runtime-menu" class="runtime-menu" role="listbox" :aria-label="$t('settings.runtime')">
          <button
            v-for="(runtime, index) in store.runtimes"
            :key="runtime.id"
            :ref="(element) => setRuntimeOptionRef(element, index)"
            class="runtime-option"
            :class="[runtimeStatusClass(runtime), { selected: runtime.id === store.activeRuntimeId }]"
            type="button"
            role="option"
            :aria-selected="runtime.id === store.activeRuntimeId"
            :disabled="runtimeSwitching"
            :title="runtime.error || runtime.base_url"
            @click="void selectRuntime(runtime.id)"
            @keydown="handleRuntimeOptionKeydown($event, index, runtime.id)"
          >
            <span class="runtime-status-dot" aria-hidden="true"></span>
            <span class="runtime-option-copy">
              <strong>{{ runtime.label }}</strong>
              <small>{{ runtimeLocationLabel(runtime) }} · {{ runtimeStatusLabel(runtime) }}</small>
            </span>
            <Check v-if="runtime.id === store.activeRuntimeId" class="runtime-check" :size="18" weight="bold" aria-hidden="true" />
          </button>
        </div>
      </div>
      <button class="text-button language-button" :aria-label="locale === 'zh-CN' ? 'Switch to English' : '切换到中文'" @click="swapLanguage">
        {{ locale === "zh-CN" ? "EN" : "中" }}
      </button>
      <button class="queue-button" :aria-label="$t('queue.title')" @click="$emit('open-queue')">
        <ListBullets :size="19" /><span>{{ $t("common.queue") }}</span><b>{{ store.runningCount }}</b>
      </button>
    </div>
  </header>
</template>
