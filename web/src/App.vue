<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { PhX as X } from "@phosphor-icons/vue";
import { RouterView } from "vue-router";
import AppHeader from "./components/AppHeader.vue";
import QueueDrawer from "./components/QueueDrawer.vue";
import { useStudioStore } from "./stores/studio";

const store = useStudioStore();
const queueOpen = ref(false);
let queueTimer = 0;
let runtimeTimer = 0;
const refreshOnFocus = () => { void store.refreshRuntime(); };

onMounted(async () => {
  document.documentElement.dataset.theme = localStorage.getItem("cooksprite.theme") || "neon";
  await store.initialize();
  queueTimer = window.setInterval(store.refreshQueue, 2500);
  runtimeTimer = window.setInterval(store.refreshRuntime, 3000);
  window.addEventListener("focus", refreshOnFocus);
});
onBeforeUnmount(() => {
  window.clearInterval(queueTimer);
  window.clearInterval(runtimeTimer);
  window.removeEventListener("focus", refreshOnFocus);
});
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#main-content">{{ $t("common.skip") }}</a>
    <AppHeader @open-queue="queueOpen = true" />
    <div v-if="store.error" class="global-notice" role="alert">
      <span>{{ store.error }}</span>
      <button class="icon-button compact" :aria-label="$t('common.dismiss')" @click="store.error = ''"><X :size="16" /></button>
    </div>
    <main id="main-content" class="app-content">
      <RouterView />
    </main>
    <nav class="mobile-nav" aria-label="Mobile navigation">
      <RouterLink to="/">{{ $t("nav.gallery") }}</RouterLink>
      <RouterLink to="/studio">{{ $t("nav.studio") }}</RouterLink>
      <RouterLink to="/library">{{ $t("nav.library") }}</RouterLink>
      <button @click="queueOpen = true">{{ $t("common.queue") }} {{ store.runningCount || "" }}</button>
    </nav>
    <div class="small-screen-gate" role="status">
      <strong>{{ $t("common.preview") }}</strong>
      <span>{{ $t("studio.smallScreen") }}</span>
    </div>
    <QueueDrawer v-model:open="queueOpen" />
  </div>
</template>
