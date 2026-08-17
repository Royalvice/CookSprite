<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { PhCheck as Check, PhCloudSlash as CloudSlash, PhDatabase as Database, PhDownloadSimple as DownloadSimple, PhFolderOpen as FolderOpen, PhGauge as Gauge, PhPlus as Plus, PhScan as Radar, PhSpinner as Spinner, PhSpeakerHigh as SpeakerHigh, PhSpeakerSlash as SpeakerSlash, PhWrench as Wrench } from "@phosphor-icons/vue";
import { api, type LocalProbeView, type LocalSetupView, type RuntimeCapabilities, type RuntimeDefaultsView, type RuntimeView } from "../api/generated";
import { useStudioStore } from "../stores/studio";

const store = useStudioStore();
const { locale, t } = useI18n();
const runtimes = ref<RuntimeView[]>([]);
const localProbe = ref<LocalProbeView | null>(null);
const probingLocal = ref(false);
const runtimeBusy = ref("");
const runtimeMessage = ref("");
const capabilities = ref<RuntimeCapabilities | null>(null);
const defaults = ref<RuntimeDefaultsView | null>(null);
const defaultAction = ref("image.generate");
const defaultWorkflow = ref("");
const defaultModel = ref("");
const defaultBusy = ref(false);
const remoteUrl = ref("http://127.0.0.1:18188");
const remoteId = ref("h20-gpu0");
const remoteName = ref("H20-baidu · GPU0");
const projectName = ref("");
const selectedProjectId = ref("");
const projectMessage = ref("");
const setup = ref<LocalSetupView | null>(null);
let setupTimer: number | undefined;
const theme = ref(localStorage.getItem("cooksprite.theme") || "neon");
const sound = ref(localStorage.getItem("cooksprite.sound") === "on");
const usage = computed(() => store.allArtifacts.reduce((sum, item) => sum + item.size, 0));
const currentProject = computed(() => store.currentProject);
const localRuntime = computed(() => runtimes.value.find((item) => item.location === "local"));
const activeRuntime = computed(() => runtimes.value.find((item) => item.id === store.activeRuntimeId));
const categoryLabels: Record<string, string> = { image: "settings.categoryImage", text: "settings.categoryText", video: "settings.categoryVideo", tools: "settings.categoryTools" };
type CapabilityItem = Record<string, unknown>;
function capabilityLabel(item: CapabilityItem) { return String(item.label || item.id || ""); }
function capabilitySource(item: CapabilityItem) { return String(item.source || ""); }

async function refreshRuntimes() {
  runtimes.value = await store.refreshRuntimes().catch(() => []);
  if (store.activeRuntimeId) {
    capabilities.value = await api.runtimeCapabilities(store.activeRuntimeId).catch(() => null);
    await refreshDefaults(store.activeRuntimeId);
  }
}
async function refreshDefaults(id: string) {
  defaults.value = await api.runtimeDefaults(id).catch(() => null);
  const binding = defaults.value?.defaults[defaultAction.value];
  defaultWorkflow.value = binding?.workflow_id || "";
  defaultModel.value = binding?.model_id || "";
}
async function selectRuntime(id: string) {
  runtimeBusy.value = id;
  try {
    await store.selectRuntime(id);
    capabilities.value = await api.runtimeCapabilities(id).catch(() => null);
    await refreshDefaults(id);
    await refreshRuntimes();
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { runtimeBusy.value = ""; }
}
async function refreshSetup() {
  setup.value = await api.localSetup().catch(() => null);
  if (setup.value && ["installing", "starting", "validating"].includes(setup.value.status)) {
    setupTimer = window.setTimeout(refreshSetup, 1000);
  } else if (setup.value?.status === "ready") {
    await refreshRuntimes();
  }
}
onMounted(async () => {
  selectedProjectId.value = store.currentProject?.id || localStorage.getItem("cooksprite.current-project") || store.projects[0]?.id || "";
  await Promise.all([refreshRuntimes(), refreshSetup()]);
});
onBeforeUnmount(() => { if (setupTimer) window.clearTimeout(setupTimer); });
watch(() => store.currentProject?.id, (id) => { selectedProjectId.value = id || ""; });
watch(() => store.activeRuntimeId, (id) => { if (id) { void api.runtimeCapabilities(id).then((value) => { capabilities.value = value; }).catch(() => { capabilities.value = null; }); void refreshDefaults(id); } });
watch(defaultAction, () => {
  const binding = defaults.value?.defaults[defaultAction.value];
  defaultWorkflow.value = binding?.workflow_id || "";
  defaultModel.value = binding?.model_id || "";
});
const defaultRecipes = computed(() => (defaults.value?.recipes || []).filter((recipe) => recipe.actions.includes(defaultAction.value)));
watch(defaultWorkflow, (workflowId) => {
  const recipe = defaultRecipes.value.find((item) => item.id === workflowId);
  if (recipe) defaultModel.value = recipe.model_id;
});
async function saveDefault() {
  if (!store.activeRuntimeId || !defaultWorkflow.value || !defaultModel.value) return;
  defaultBusy.value = true;
  try { await api.setRuntimeDefault(store.activeRuntimeId, defaultAction.value, { workflow_id: defaultWorkflow.value, model_id: defaultModel.value }); await refreshDefaults(store.activeRuntimeId); runtimeMessage.value = t("settings.defaultSaved"); }
  catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { defaultBusy.value = false; }
}
function setTheme(value: string) { theme.value = value; document.documentElement.dataset.theme = value; localStorage.setItem("cooksprite.theme", value); }
function setLanguage(value: string) { locale.value = value; document.documentElement.lang = value; localStorage.setItem("cooksprite.language", value); }
function setSound(value: boolean) { sound.value = value; localStorage.setItem("cooksprite.sound", value ? "on" : "off"); }
async function switchProject() {
  if (!selectedProjectId.value) return;
  try { await store.openProject(selectedProjectId.value); projectMessage.value = t("settings.projectSwitched"); }
  catch (error) { projectMessage.value = error instanceof Error ? error.message : String(error); }
}
async function createProject() {
  try {
    const project = await store.createProject(projectName.value.trim() || t("studio.untitled"));
    selectedProjectId.value = project.id;
    projectName.value = "";
    projectMessage.value = t("settings.projectCreated");
  } catch (error) { projectMessage.value = error instanceof Error ? error.message : String(error); }
}
async function openProjectDirectory() {
  if (!currentProject.value) return;
  const result = await api.openProjectDirectory(currentProject.value.id);
  projectMessage.value = result.error || (result.opened ? t("settings.directoryOpened") : result.path);
}
async function probeLocal() {
  probingLocal.value = true;
  try { localProbe.value = await api.probeLocal(); }
  catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { probingLocal.value = false; }
}
async function connectRuntime(options: { id: string; label: string; base_url: string; location: "local" | "remote" }) {
  runtimeBusy.value = options.id;
  runtimeMessage.value = "";
  try {
    await api.createRuntime(options);
    await api.doctorRuntime(options.id);
    await api.selectRuntime(options.id);
    await refreshRuntimes();
    runtimeMessage.value = t("settings.runtimeConnected");
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { runtimeBusy.value = ""; }
}
async function connectLocal() {
  const candidate = localProbe.value?.candidates.find((item) => item.status === "found");
  if (!candidate) return;
  await connectRuntime({ id: localRuntime.value?.id || "local", label: "Local ComfyUI", base_url: candidate.base_url, location: "local" });
}
async function connectRemote() {
  await connectRuntime({ id: remoteId.value.trim(), label: remoteName.value.trim() || remoteId.value, base_url: remoteUrl.value.trim(), location: "remote" });
}
async function installLocal() {
  runtimeMessage.value = "";
  try {
    setup.value = await api.installLocal({});
    await refreshSetup();
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
}
</script>

<template>
  <div class="settings-view page-wrap narrow-page">
    <section class="page-heading"><span class="eyebrow">{{ $t("settings.eyebrow") }}</span><h1>{{ $t("settings.title") }}</h1><p>{{ $t("settings.privacy") }}</p></section>
    <section class="settings-section panel project-context-section">
      <header><div><span class="section-index">01</span><h2>{{ $t("settings.projects") }}</h2></div><FolderOpen :size="24" /></header>
      <p>{{ $t("settings.projectHelp") }}</p>
      <div class="project-switcher">
        <label><span>{{ $t("settings.currentProject") }}</span><select v-model="selectedProjectId" :disabled="!store.projects.length" @change="switchProject"><option v-if="!store.projects.length" value="">{{ $t("settings.noProjects") }}</option><option v-for="project in store.projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label>
        <button class="arcade-button" :disabled="!currentProject" @click="openProjectDirectory"><FolderOpen :size="18" />{{ $t("settings.openDirectory") }}</button>
      </div>
      <div class="project-create-form">
        <label><span>{{ $t("settings.newProject") }}</span><input v-model="projectName" :placeholder="$t('studio.untitled')" @keyup.enter="createProject" /></label>
        <button class="arcade-button primary" @click="createProject"><Plus :size="18" />{{ $t("settings.createProject") }}</button>
      </div>
      <small v-if="currentProject?.directory" class="project-directory">{{ currentProject.directory }}</small>
      <p v-if="projectMessage" class="inline-status" role="status">{{ projectMessage }}</p>
    </section>
    <section class="settings-section panel">
      <header><div><span class="section-index">02</span><h2>{{ $t("settings.runtime") }}</h2></div></header>
      <p>{{ $t("settings.runtimeHelp") }}</p>
      <div class="local-probe-card">
        <div><strong>{{ $t("settings.localRuntime") }}</strong><span>{{ $t("settings.localRuntimeHelp") }}</span></div>
        <button class="arcade-button" :disabled="probingLocal" @click="probeLocal"><Spinner v-if="probingLocal" class="spin" :size="18" /><Radar v-else :size="18" />{{ $t("settings.probeLocal") }}</button>
        <div v-if="localProbe" class="probe-result" :class="localProbe.status">
          <template v-if="localProbe.candidates.some((item) => item.status === 'found')">
            <strong>{{ $t("settings.localFound") }}</strong>
            <span>{{ localProbe.candidates.find((item) => item.status === 'found')?.base_url }} · {{ $t("settings.modelsCount", { count: localProbe.candidates.find((item) => item.status === 'found')?.models || 0 }) }} · {{ $t("settings.nodesCount", { count: localProbe.candidates.find((item) => item.status === 'found')?.nodes || 0 }) }}</span>
            <button class="arcade-button primary" :disabled="!!runtimeBusy" @click="connectLocal">{{ $t("settings.connectLocal") }}</button>
          </template>
          <template v-else-if="localProbe.status === 'missing'"><strong>{{ $t("settings.localMissing") }}</strong><span>{{ $t("settings.localMissingHelp") }}</span></template>
          <template v-else><strong>{{ $t("settings.localUnavailable") }}</strong><span>{{ $t("settings.localUnavailableHelp") }}</span></template>
        </div>
      </div>
      <div v-if="localProbe?.status === 'missing'" class="managed-setup-card">
        <div>
          <strong>{{ $t("settings.installTitle") }}</strong>
          <span>{{ $t("settings.installBody") }}</span>
          <small v-if="setup">{{ setup.directory || setup.default_directory }}</small>
        </div>
        <button class="arcade-button primary" :disabled="!!setup && ['installing','starting','validating'].includes(setup.status)" @click="installLocal">
          <DownloadSimple :size="18" />{{ $t(setup && ['installed','ready'].includes(setup.status) ? "settings.installed" : "settings.install") }}
        </button>
        <div v-if="setup && setup.status !== 'idle'" class="setup-progress" :class="setup.status">
          <i :style="{ width: `${Math.max(2, setup.progress * 100)}%` }"></i>
          <span>{{ setup.error || setup.message }}</span>
        </div>
      </div>
      <h3>{{ $t("settings.connectedRuntimes") }}</h3>
      <div v-if="runtimes.length" class="runtime-list">
          <article v-for="runtime in runtimes" :key="runtime.id" :class="[runtime.status, { active: runtime.id === store.activeRuntimeId }]">
          <Gauge :size="22" />
          <div><strong>{{ runtime.label }}</strong><span>{{ runtime.location === "local" ? $t("settings.local") : $t("settings.remote") }} · {{ runtime.base_url }}</span><small v-if="runtime.error" class="runtime-error">{{ runtime.error }}</small><small v-else>{{ runtime.recipes?.length || 0 }} {{ $t("settings.workflows") }}</small></div>
          <span class="runtime-status">{{ runtime.status === "ready" ? $t("settings.connected") : $t(`common.${runtime.status || 'offline'}`) }}</span>
          <button v-if="runtime.id !== store.activeRuntimeId" class="text-button" :disabled="!!runtimeBusy" @click="selectRuntime(runtime.id)">{{ $t("settings.useRuntime") }}</button><Check v-else-if="runtime.status === 'ready'" :size="18" weight="bold" />
        </article>
      </div>
      <div v-else class="runtime-empty">{{ $t("settings.noRuntimes") }}</div>
      <h3>{{ $t("settings.remoteRuntime") }}</h3>
      <div class="runtime-form"><label><span>ID</span><input v-model="remoteId" /></label><label><span>{{ $t("settings.endpoint") }}</span><input v-model="remoteUrl" /></label><label><span>{{ $t("settings.name") }}</span><input v-model="remoteName" /></label><button class="arcade-button primary" :disabled="!!runtimeBusy || !remoteId || !remoteUrl" @click="connectRemote"><Wrench :size="18" />{{ $t("settings.connectRemote") }}</button></div>
      <p v-if="runtimeMessage" class="inline-status" role="status">{{ runtimeMessage }}</p>
      <div v-if="activeRuntime && capabilities" class="capability-summary"><header><strong>{{ $t("settings.capabilities") }} · {{ activeRuntime.label }}</strong><span>{{ capabilities.system.comfyui_version || "ComfyUI" }}</span></header><div class="capability-grid"><article v-for="(category, key) in capabilities.categories" :key="key"><strong>{{ $t(categoryLabels[key] || key) }}</strong><span>{{ category.models.length }} {{ $t("settings.models") }} · {{ category.workflows.length }} {{ $t("settings.workflows") }} · {{ category.tools.length }} {{ $t("settings.tools") }}</span><details v-if="category.models.length || category.workflows.length || category.tools.length"><summary>{{ $t("settings.viewDetails") }}</summary><div class="capability-items"><div v-for="item in category.models.slice(0, 20)" :key="`model-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div><div v-for="item in category.workflows.slice(0, 20)" :key="`workflow-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div><div v-for="item in category.tools.slice(0, 20)" :key="`tool-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div></div></details></article></div></div>
      <div v-if="activeRuntime && defaults" class="runtime-defaults">
        <h3>{{ $t("settings.defaults") }}</h3>
        <div class="runtime-default-form">
          <label><span>{{ $t("settings.defaultAction") }}</span><select v-model="defaultAction"><option value="image.generate">image.generate</option><option value="animation.generate">animation.generate</option><option value="frame.redraw">frame.redraw</option><option value="normal.generate">normal.generate</option></select></label>
          <label><span>{{ $t("settings.defaultWorkflow") }}</span><select v-model="defaultWorkflow"><option value="" disabled>{{ $t("settings.noCompatibleWorkflow") }}</option><option v-for="recipe in defaultRecipes" :key="recipe.id" :value="recipe.id">{{ recipe.label }}</option></select></label>
          <label><span>{{ $t("settings.defaultModel") }}</span><select v-model="defaultModel"><option v-if="defaultModel" :value="defaultModel">{{ defaultModel }}</option><option v-for="recipe in defaultRecipes.filter((item) => item.id === defaultWorkflow)" :key="`model-${recipe.id}`" :value="recipe.model_id">{{ recipe.model_id }}</option></select></label>
          <button class="arcade-button primary" :disabled="defaultBusy || !defaultWorkflow || !defaultModel" @click="saveDefault">{{ $t("settings.saveDefault") }}</button>
        </div>
      </div>
      <small>{{ $t("settings.modelNote") }}</small>
    </section>
    <section class="settings-section panel">
      <header><div><span class="section-index">03</span><h2>{{ $t("settings.appearance") }}</h2></div></header>
      <div class="settings-grid">
        <fieldset><legend>{{ $t("settings.theme") }}</legend><button v-for="item in [{id:'neon',label:'NEON VOID'},{id:'ember',label:'EMBER CABINET'},{id:'mint',label:'MINT DAY'}]" :key="item.id" class="theme-option" :class="[{ selected: theme === item.id }, item.id]" @click="setTheme(item.id)"><span></span>{{ item.label }}<Check v-if="theme === item.id" :size="16" /></button></fieldset>
        <fieldset><legend>{{ $t("settings.language") }}</legend><button class="theme-option" :class="{ selected: locale === 'zh-CN' }" @click="setLanguage('zh-CN')">中文<Check v-if="locale === 'zh-CN'" :size="16" /></button><button class="theme-option" :class="{ selected: locale === 'en' }" @click="setLanguage('en')">ENGLISH<Check v-if="locale === 'en'" :size="16" /></button></fieldset>
        <fieldset><legend>{{ $t("settings.sound") }}</legend><button class="theme-option" :class="{ selected: sound }" @click="setSound(!sound)"><SpeakerHigh v-if="sound" :size="18" /><SpeakerSlash v-else :size="18" />{{ $t(sound ? "settings.soundOn" : "settings.soundOff") }}</button></fieldset>
      </div>
    </section>
    <section class="settings-section panel">
      <header><div><span class="section-index">04</span><h2>{{ $t("settings.storage") }}</h2></div><Database :size="24" /></header>
      <div class="storage-meter"><i :style="{ width: `${Math.min(100, usage / (10 * 1024 ** 3) * 100)}%` }"></i></div>
      <div class="storage-row"><strong>{{ (usage / 1024 / 1024).toFixed(1) }} MB</strong><span>{{ $t("settings.storageWarn") }}</span></div>
      <div class="privacy-card"><CloudSlash :size="28" /><div><strong>{{ $t("settings.offlineTitle") }}</strong><span>{{ $t("settings.offlineBody") }}</span></div></div>
    </section>
  </div>
</template>
