<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { PhCheck as Check, PhCloudSlash as CloudSlash, PhDatabase as Database, PhDownloadSimple as DownloadSimple, PhGauge as Gauge, PhSpeakerHigh as SpeakerHigh, PhSpeakerSlash as SpeakerSlash, PhWrench as Wrench } from "@phosphor-icons/vue";
import { api, type LocalSetupView, type RuntimeView } from "../api/generated";
import { useStudioStore } from "../stores/studio";

const store = useStudioStore();
const { locale, t } = useI18n();
const runtimes = ref<RuntimeView[]>([]);
const runtimeUrl = ref("http://127.0.0.1:8188");
const runtimeId = ref("rt_local");
const runtimeMessage = ref("");
const setup = ref<LocalSetupView | null>(null);
let setupTimer: number | undefined;
const theme = ref(localStorage.getItem("cooksprite.theme") || "neon");
const sound = ref(localStorage.getItem("cooksprite.sound") === "on");
const usage = computed(() => store.allArtifacts.reduce((sum, item) => sum + item.size, 0));

async function refreshRuntimes() {
  runtimes.value = await api.runtimes().catch(() => []);
  await store.refreshActions();
}
async function refreshSetup() {
  setup.value = await api.localSetup().catch(() => null);
  if (setup.value && ["installing", "starting", "validating"].includes(setup.value.status)) {
    setupTimer = window.setTimeout(refreshSetup, 1000);
  } else if (setup.value?.status === "ready") {
    await refreshRuntimes();
  }
}
onMounted(async () => { await Promise.all([refreshRuntimes(), refreshSetup()]); });
onBeforeUnmount(() => { if (setupTimer) window.clearTimeout(setupTimer); });
function setTheme(value: string) { theme.value = value; document.documentElement.dataset.theme = value; localStorage.setItem("cooksprite.theme", value); }
function setLanguage(value: string) { locale.value = value; document.documentElement.lang = value; localStorage.setItem("cooksprite.language", value); }
function setSound(value: boolean) { sound.value = value; localStorage.setItem("cooksprite.sound", value ? "on" : "off"); }
async function connect() {
  runtimeMessage.value = "CONNECTING…";
  try {
    await api.createRuntime({ id: runtimeId.value, label: "Local ComfyUI", base_url: runtimeUrl.value });
    await api.doctorRuntime(runtimeId.value);
    await refreshRuntimes();
    runtimeMessage.value = t("common.ready");
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
}
async function installLocal() {
  runtimeMessage.value = "";
  try {
    setup.value = await api.installLocal({ with_models: true });
    await refreshSetup();
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
}
</script>

<template>
  <div class="settings-view page-wrap narrow-page">
    <section class="page-heading"><span class="eyebrow">{{ $t("settings.eyebrow") }}</span><h1>{{ $t("settings.title") }}</h1><p>{{ $t("settings.privacy") }}</p></section>
    <section class="settings-section panel">
      <header><div><span class="section-index">01</span><h2>{{ $t("settings.runtime") }}</h2></div><span class="runtime-chip" :class="store.runtimeStatus">{{ $t(`common.${store.runtimeStatus}`) }}</span></header>
      <p>{{ $t("settings.runtimeHelp") }}</p>
      <div v-if="runtimes.length" class="runtime-list">
        <article v-for="runtime in runtimes" :key="runtime.id">
          <Gauge :size="22" />
          <div>
            <strong>{{ runtime.label }} · {{ $t(`common.${runtime.status || 'offline'}`) }}</strong>
            <span>{{ runtime.error || runtime.base_url }}</span>
            <small v-if="runtime.recipes?.length">{{ runtime.recipes.map(item => item.label).join(" · ") }}</small>
          </div>
          <Check v-if="runtime.status === 'ready'" :size="18" weight="bold" />
        </article>
      </div>
      <div class="managed-setup-card">
        <div>
          <strong>{{ $t("settings.installTitle") }}</strong>
          <span>{{ $t("settings.installBody", { size: setup ? (setup.model.size / 1024 ** 3).toFixed(1) : "2.1" }) }}</span>
          <small v-if="setup">{{ setup.model.filename }} · {{ setup.model.license }} · {{ setup.directory || setup.default_directory }}</small>
        </div>
        <button class="arcade-button primary" :disabled="!!setup && ['installing','starting','validating'].includes(setup.status)" @click="installLocal">
          <DownloadSimple :size="18" />{{ $t(setup && ['installed','ready'].includes(setup.status) ? "settings.installed" : "settings.install") }}
        </button>
        <div v-if="setup && setup.status !== 'idle'" class="setup-progress" :class="setup.status">
          <i :style="{ width: `${Math.max(2, setup.progress * 100)}%` }"></i>
          <span>{{ setup.error || setup.message }}</span>
        </div>
      </div>
      <h3>{{ $t("settings.existing") }}</h3>
      <div class="runtime-form"><label><span>ID</span><input v-model="runtimeId" /></label><label><span>URL</span><input v-model="runtimeUrl" /></label><button class="arcade-button primary" @click="connect"><Wrench :size="18" />{{ $t("settings.validate") }}</button></div>
      <p v-if="runtimeMessage" class="inline-status">{{ runtimeMessage }}</p>
      <small>{{ $t("settings.modelNote") }}</small>
    </section>
    <section class="settings-section panel">
      <header><div><span class="section-index">02</span><h2>{{ $t("settings.appearance") }}</h2></div></header>
      <div class="settings-grid">
        <fieldset><legend>{{ $t("settings.theme") }}</legend><button v-for="item in [{id:'neon',label:'NEON VOID'},{id:'ember',label:'EMBER CABINET'},{id:'mint',label:'MINT DAY'}]" :key="item.id" class="theme-option" :class="[{ selected: theme === item.id }, item.id]" @click="setTheme(item.id)"><span></span>{{ item.label }}<Check v-if="theme === item.id" :size="16" /></button></fieldset>
        <fieldset><legend>{{ $t("settings.language") }}</legend><button class="theme-option" :class="{ selected: locale === 'zh-CN' }" @click="setLanguage('zh-CN')">中文<Check v-if="locale === 'zh-CN'" :size="16" /></button><button class="theme-option" :class="{ selected: locale === 'en' }" @click="setLanguage('en')">ENGLISH<Check v-if="locale === 'en'" :size="16" /></button></fieldset>
        <fieldset><legend>{{ $t("settings.sound") }}</legend><button class="theme-option" :class="{ selected: sound }" @click="setSound(!sound)"><SpeakerHigh v-if="sound" :size="18" /><SpeakerSlash v-else :size="18" />{{ $t(sound ? "settings.soundOn" : "settings.soundOff") }}</button></fieldset>
      </div>
    </section>
    <section class="settings-section panel">
      <header><div><span class="section-index">03</span><h2>{{ $t("settings.storage") }}</h2></div><Database :size="24" /></header>
      <div class="storage-meter"><i :style="{ width: `${Math.min(100, usage / (10 * 1024 ** 3) * 100)}%` }"></i></div>
      <div class="storage-row"><strong>{{ (usage / 1024 / 1024).toFixed(1) }} MB</strong><span>{{ $t("settings.storageWarn") }}</span></div>
      <div class="privacy-card"><CloudSlash :size="28" /><div><strong>{{ $t("settings.offlineTitle") }}</strong><span>{{ $t("settings.offlineBody") }}</span></div></div>
    </section>
  </div>
</template>
