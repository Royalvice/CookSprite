<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { PhArrowClockwise as Restart, PhCheck as Check, PhCloudSlash as CloudSlash, PhDatabase as Database, PhDownloadSimple as DownloadSimple, PhFolderOpen as FolderOpen, PhGauge as Gauge, PhPlay as Play, PhPlus as Plus, PhScan as Radar, PhSpinner as Spinner, PhSpeakerHigh as SpeakerHigh, PhSpeakerSlash as SpeakerSlash, PhTrash as Trash, PhWrench as Wrench } from "@phosphor-icons/vue";
import { api, type ComfyProbeView, type LocalSetupView, type ModelBundleView, type ModelDownloadView, type RuntimeCapabilities, type RuntimeDefaultsView, type RuntimeView } from "../api/generated";
import NormalEstimatorControls, { type NormalEstimatorOption } from "../components/NormalEstimatorControls.vue";
import { useStudioStore } from "../stores/studio";

const store = useStudioStore();
const { locale, t } = useI18n();
const runtimes = ref<RuntimeView[]>([]);
const comfyProbe = ref<ComfyProbeView | null>(null);
const probingComfy = ref(false);
const runtimeBusy = ref("");
const runtimeMessage = ref("");
const nodeInstallCommand = ref("");
const capabilities = ref<RuntimeCapabilities | null>(null);
const defaults = ref<RuntimeDefaultsView | null>(null);
const defaultAction = ref("image.generate");
const defaultModel = ref("");
const defaultBusy = ref(false);
const normalSingleModel = ref("");
const normalTemporalModel = ref("");
const normalDefaultBusy = ref<"" | "single" | "temporal">("");
const modelDownload = ref<ModelDownloadView | null>(null);
const modelDownloadBusy = ref("");
const endpointUrl = ref("http://127.0.0.1:8188");
const projectName = ref("");
const selectedProjectId = ref("");
const projectMessage = ref("");
const setup = ref<LocalSetupView | null>(null);
let setupTimer: number | undefined;
let modelDownloadTimer: number | undefined;
const theme = ref(localStorage.getItem("cooksprite.theme") || "neon");
const sound = ref(localStorage.getItem("cooksprite.sound") === "on");
const usage = computed(() => store.allArtifacts.reduce((sum, item) => sum + item.size, 0));
const currentProject = computed(() => store.currentProject);
const activeRuntime = computed(() => runtimes.value.find((item) => item.id === store.activeRuntimeId));
function normalizeEndpoint(value: string) { return value.trim().replace(/\/+$/, ""); }
const comfyCandidate = computed(() => comfyProbe.value?.candidates.find((item) => normalizeEndpoint(item.base_url) === normalizeEndpoint(endpointUrl.value)) || comfyProbe.value?.candidates[0]);
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
  defaultModel.value = binding?.model_id || "";
  syncNormalDefaultModels();
}
const modelBundles = computed<ModelBundleView[]>(() => defaults.value?.model_bundles || []);
function stopModelDownloadPolling() {
  if (modelDownloadTimer) window.clearTimeout(modelDownloadTimer);
  modelDownloadTimer = undefined;
}
async function pollModelDownload() {
  if (!modelDownload.value || !store.activeRuntimeId) return;
  const current = await api.modelDownloadStatus(store.activeRuntimeId, modelDownload.value.id).catch(() => null);
  if (current) modelDownload.value = current;
  if (current && ["queued", "downloading", "verifying"].includes(current.status)) {
    modelDownloadTimer = window.setTimeout(pollModelDownload, 1000);
  } else {
    stopModelDownloadPolling();
    if (current?.status === "succeeded") await refreshDefaults(store.activeRuntimeId);
  }
}
async function downloadModelBundle(bundle: ModelBundleView) {
  if (!store.activeRuntimeId || bundle.ready || modelDownloadBusy.value) return;
  modelDownloadBusy.value = bundle.id;
  runtimeMessage.value = "";
  stopModelDownloadPolling();
  try {
    modelDownload.value = await api.downloadModelBundle(store.activeRuntimeId, bundle.id);
    void pollModelDownload();
  } catch (error) {
    runtimeMessage.value = error instanceof Error ? error.message : String(error);
  } finally { modelDownloadBusy.value = ""; }
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
    await Promise.all([refreshRuntimes(), probeComfy()]);
  }
}
onMounted(async () => {
  selectedProjectId.value = store.currentProject?.id || localStorage.getItem("cooksprite.current-project") || store.projects[0]?.id || "";
  await Promise.all([refreshRuntimes(), refreshSetup()]);
});
onBeforeUnmount(() => { if (setupTimer) window.clearTimeout(setupTimer); stopModelDownloadPolling(); });
watch(() => store.currentProject?.id, (id) => { selectedProjectId.value = id || ""; });
watch(() => store.activeRuntimeId, (id) => { if (id) { void api.runtimeCapabilities(id).then((value) => { capabilities.value = value; }).catch(() => { capabilities.value = null; }); void refreshDefaults(id); } });
watch(defaultAction, () => {
  const binding = defaults.value?.defaults[defaultAction.value];
  defaultModel.value = binding?.model_id || "";
});
const defaultModels = computed(() => (defaults.value?.models || []).filter((model) => model.actions.includes(defaultAction.value)));
const defaultActions = computed(() => Array.from(new Set((defaults.value?.models || []).flatMap((model) => model.actions)))
  .filter((action) => !["normal.generate", "sprite.pixelize"].includes(action)));
function normalEstimatorOptions(mode: "single" | "temporal"): NormalEstimatorOption[] {
  const requiredMode = mode === "single" ? "image-to-normal" : "frames-to-normal";
  const options = new Map<string, NormalEstimatorOption>();
  for (const recipe of defaults.value?.recipes || []) {
    if (!recipe.actions.includes("normal.generate") || !recipe.modes.includes(requiredMode)) continue;
    options.set(recipe.model_id, {
      id: recipe.model_id,
      modelId: recipe.model_id,
      label: recipe.label,
    });
  }
  return [...options.values()];
}
const normalSingleOptions = computed(() => normalEstimatorOptions("single"));
const normalTemporalOptions = computed(() => normalEstimatorOptions("temporal"));
function syncNormalDefaultModels() {
  const single = defaults.value?.normal_estimators.single?.model_id;
  const temporal = defaults.value?.normal_estimators.temporal?.model_id;
  normalSingleModel.value = normalSingleOptions.value.find((item) => item.modelId === single)?.id
    || normalSingleOptions.value[0]?.id || "";
  normalTemporalModel.value = normalTemporalOptions.value.find((item) => item.modelId === temporal)?.id
    || normalTemporalOptions.value[0]?.id || "";
}
watch(defaultActions, (actions) => {
  if (actions.length && !actions.includes(defaultAction.value)) defaultAction.value = actions[0];
});
async function saveDefault() {
  if (!store.activeRuntimeId || !defaultModel.value) return;
  defaultBusy.value = true;
  try { await api.setRuntimeDefault(store.activeRuntimeId, defaultAction.value, { model_id: defaultModel.value }); await refreshDefaults(store.activeRuntimeId); runtimeMessage.value = t("settings.defaultSaved"); }
  catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { defaultBusy.value = false; }
}
async function saveNormalEstimator(mode: "single" | "temporal") {
  if (!store.activeRuntimeId) return;
  const modelId = mode === "single" ? normalSingleModel.value : normalTemporalModel.value;
  if (!modelId) return;
  normalDefaultBusy.value = mode;
  try {
    await api.setRuntimeNormalEstimator(store.activeRuntimeId, mode, { model_id: modelId });
    await refreshDefaults(store.activeRuntimeId);
    runtimeMessage.value = t("normal.saved");
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { normalDefaultBusy.value = ""; }
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
async function probeComfy() {
  probingComfy.value = true;
  try { comfyProbe.value = await api.probeComfy(endpointUrl.value.trim()); }
  catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { probingComfy.value = false; }
}
async function connectRuntime(options: { label: string; base_url: string; location: "local" | "remote" }) {
  runtimeBusy.value = options.base_url;
  runtimeMessage.value = "";
  try {
    const runtime = await api.createRuntime(options);
    await api.doctorRuntime(runtime.id);
    await api.selectRuntime(runtime.id);
    await refreshRuntimes();
    runtimeMessage.value = t("settings.runtimeConnected");
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { runtimeBusy.value = ""; }
}
async function connectLocal() {
  await connectRuntime({ label: "Local ComfyUI", base_url: comfyCandidate.value?.base_url || endpointUrl.value.trim(), location: "local" });
}
async function connectRemote() {
  await connectRuntime({ label: "Remote ComfyUI", base_url: endpointUrl.value.trim(), location: "remote" });
}
async function startLocal() {
  runtimeBusy.value = "local-start";
  runtimeMessage.value = "";
  try {
    setup.value = await api.startLocal({ base_url: endpointUrl.value.trim(), directory: comfyCandidate.value?.directory });
    await refreshSetup();
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { runtimeBusy.value = ""; }
}
async function deleteRuntime(runtime: RuntimeView) {
  if (!window.confirm(t("settings.deleteRuntimeConfirm", { label: runtime.label }))) return;
  runtimeBusy.value = runtime.id;
  runtimeMessage.value = "";
  try {
    const result = await api.deleteRuntime(runtime.id);
    await refreshRuntimes();
    await store.refreshRuntime();
    capabilities.value = store.activeRuntimeId ? await api.runtimeCapabilities(store.activeRuntimeId).catch(() => null) : null;
    defaults.value = store.activeRuntimeId ? await api.runtimeDefaults(store.activeRuntimeId).catch(() => null) : null;
    runtimeMessage.value = result.message;
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { runtimeBusy.value = ""; }
}
async function restartRuntime(runtime: RuntimeView) {
  if (!window.confirm(t("settings.restartRuntimeConfirm", { label: runtime.label }))) return;
  runtimeBusy.value = runtime.id;
  runtimeMessage.value = "";
  try {
    const result = await api.restartRuntime(runtime.id);
    if (result.status === "manual_required") {
      runtimeMessage.value = result.message;
      return;
    }
    setup.value = result;
    runtimeMessage.value = setup.value.message;
    await refreshSetup();
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { runtimeBusy.value = ""; }
}
async function installNodes(runtime: RuntimeView) {
  runtimeBusy.value = runtime.id;
  runtimeMessage.value = "";
  nodeInstallCommand.value = "";
  try {
    const result = await api.installRuntimeNodes(runtime.id);
    runtimeMessage.value = result.message;
    nodeInstallCommand.value = result.command || "";
  } catch (error) { runtimeMessage.value = error instanceof Error ? error.message : String(error); }
  finally { runtimeBusy.value = ""; }
}
async function copyNodeInstallCommand() {
  if (nodeInstallCommand.value && navigator.clipboard) await navigator.clipboard.writeText(nodeInstallCommand.value);
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
      <div class="runtime-endpoint-card">
        <label><span>{{ $t("settings.endpoint") }}</span><input v-model="endpointUrl" type="url" autocomplete="url" spellcheck="false" @keyup.enter="probeComfy" /></label>
        <div class="runtime-endpoint-actions">
          <button class="arcade-button" :disabled="probingComfy || !!runtimeBusy" @click="probeComfy"><Spinner v-if="probingComfy" class="spin" :size="18" /><Radar v-else :size="18" />{{ $t("runtimeProbe.probe") }}</button>
          <button class="arcade-button" :disabled="!!runtimeBusy || !endpointUrl.trim()" @click="connectLocal">{{ $t("settings.connectLocal") }}</button>
          <button class="arcade-button primary" :disabled="!!runtimeBusy || !endpointUrl.trim()" @click="connectRemote"><Wrench :size="18" />{{ $t("settings.connectRemote") }}</button>
        </div>
        <small>{{ $t("settings.endpointHelp") }}</small>
        <div v-if="comfyProbe" class="probe-result" :class="comfyProbe.status">
          <template v-if="comfyCandidate?.status === 'found'">
            <strong>{{ $t("runtimeProbe.found") }}</strong>
            <span>{{ comfyCandidate.base_url }} · {{ $t("settings.modelsCount", { count: comfyCandidate.models || 0 }) }} · {{ $t("settings.nodesCount", { count: comfyCandidate.nodes || 0 }) }}</span>
          </template>
          <template v-else-if="comfyProbe.status === 'missing'"><strong>{{ $t("runtimeProbe.missing") }}</strong><span>{{ $t("runtimeProbe.missingHelp") }}</span></template>
          <template v-else><strong>{{ $t("runtimeProbe.unavailable") }}</strong><span>{{ $t("runtimeProbe.unavailableHelp") }}</span></template>
        </div>
      </div>
      <div v-if="comfyProbe?.status === 'missing' && comfyCandidate?.managed" class="managed-setup-card">
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
      <div v-if="comfyProbe && ['installed','unreachable'].includes(comfyProbe.status) && comfyCandidate?.managed" class="managed-setup-card">
        <div>
          <strong>{{ $t("settings.startTitle") }}</strong>
          <span>{{ $t("settings.startBody") }}</span>
          <small v-if="setup?.method">{{ setup.method }} · {{ setup.message }}</small>
          <small v-if="setup?.directory">{{ setup.directory }}</small>
        </div>
        <button class="arcade-button primary" :disabled="!!runtimeBusy || (!!setup && ['installing','starting','validating'].includes(setup.status))" @click="startLocal"><Play :size="18" />{{ $t("settings.startLocal") }}</button>
        <div v-if="setup && ['starting','validating','ready','failed'].includes(setup.status)" class="setup-progress" :class="setup.status">
          <i :style="{ width: `${Math.max(2, setup.progress * 100)}%` }"></i>
          <span>{{ setup.error || setup.message }}</span>
        </div>
      </div>
      <h3>{{ $t("settings.connectedRuntimes") }}</h3>
      <div v-if="runtimes.length" class="runtime-list">
        <article v-for="runtime in runtimes" :key="runtime.id" :class="[runtime.status, { active: runtime.id === store.activeRuntimeId }]">
          <Gauge :size="22" />
          <div><strong>{{ runtime.label }}</strong><span>{{ runtime.location === "local" ? $t("settings.local") : $t("settings.remote") }} · {{ runtime.base_url }}</span><small v-if="runtime.error" class="runtime-error">{{ runtime.error }}</small><small v-else>{{ runtime.recipes?.length || 0 }} {{ $t("settings.workflows") }} · {{ runtime.nodes_installed ? $t("settings.nodesReady") : $t("settings.nodesMissing") }}</small></div>
          <span class="runtime-status">{{ runtime.status === "ready" ? $t("settings.connected") : $t(`common.${runtime.status || 'offline'}`) }}</span>
          <div class="runtime-actions"><button v-if="runtime.location === 'local'" class="text-button" :disabled="!!runtimeBusy || (!!setup && ['installing','starting','validating'].includes(setup.status))" @click="restartRuntime(runtime)"><Restart :size="16" />{{ $t("settings.restartRuntime") }}</button><button v-if="!runtime.nodes_installed" class="text-button" :disabled="!!runtimeBusy" @click="installNodes(runtime)">{{ $t("settings.installNodes") }}</button><button v-if="runtime.id !== store.activeRuntimeId" class="text-button" :disabled="!!runtimeBusy" @click="selectRuntime(runtime.id)">{{ $t("settings.useRuntime") }}</button><Check v-else-if="runtime.status === 'ready'" :size="18" weight="bold" /><button class="text-button danger" :disabled="!!runtimeBusy" @click="deleteRuntime(runtime)"><Trash :size="16" />{{ $t("settings.deleteRuntime") }}</button></div>
        </article>
      </div>
      <div v-else class="runtime-empty">{{ $t("settings.noRuntimes") }}</div>
      <p v-if="runtimeMessage" class="inline-status" role="status">{{ runtimeMessage }}</p>
      <div v-if="nodeInstallCommand" class="copy-command"><code>{{ nodeInstallCommand }}</code><button class="text-button" @click="copyNodeInstallCommand">{{ $t("settings.copyCommand") }}</button></div>
      <div v-if="activeRuntime && capabilities" class="capability-summary"><header><strong>{{ $t("settings.capabilities") }} · {{ activeRuntime.label }}</strong><span>{{ capabilities.system.comfyui_version || "ComfyUI" }}</span></header><div class="capability-grid"><article v-for="(category, key) in capabilities.categories" :key="key"><strong>{{ $t(categoryLabels[key] || key) }}</strong><span>{{ category.models.length }} {{ $t("settings.models") }} · {{ category.workflows.length }} {{ $t("settings.workflows") }} · {{ category.tools.length }} {{ $t("settings.tools") }}</span><details v-if="category.models.length || category.workflows.length || category.tools.length"><summary>{{ $t("settings.viewDetails") }}</summary><div class="capability-items"><div v-for="item in category.models.slice(0, 20)" :key="`model-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div><div v-for="item in category.workflows.slice(0, 20)" :key="`workflow-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div><div v-for="item in category.tools.slice(0, 20)" :key="`tool-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div></div></details></article></div></div>
      <div v-if="activeRuntime && defaults" class="runtime-defaults">
        <h3>{{ $t("settings.defaults") }}</h3>
        <div class="runtime-default-form">
          <label><span>{{ $t("settings.defaultAction") }}</span><select v-model="defaultAction"><option v-for="actionId in defaultActions" :key="actionId" :value="actionId">{{ actionId }}</option></select></label>
          <label><span>{{ $t("settings.defaultModel") }}</span><select v-model="defaultModel" :disabled="!defaultModels.length"><option value="" disabled>{{ $t("settings.noCompatibleModel") }}</option><option v-for="model in defaultModels" :key="model.id" :value="model.id">{{ model.label }}</option></select></label>
          <button class="arcade-button primary" :disabled="defaultBusy || !defaultModel" @click="saveDefault">{{ $t("settings.saveDefault") }}</button>
        </div>
      </div>
      <section v-if="activeRuntime && defaults" class="runtime-defaults normal-defaults">
        <h3>{{ $t("normal.defaults") }}</h3>
        <div class="normal-default-grid">
          <div>
            <strong>{{ $t("normal.single") }}</strong>
            <NormalEstimatorControls
              :options="normalSingleOptions"
              :model="normalSingleModel"
              :disabled="normalDefaultBusy !== ''"
              @update:model="normalSingleModel = $event"
            />
            <button class="arcade-button primary" :disabled="normalDefaultBusy !== '' || !normalSingleModel" @click="saveNormalEstimator('single')">{{ $t("settings.saveDefault") }}</button>
          </div>
          <div>
            <strong>{{ $t("normal.temporal") }}</strong>
            <NormalEstimatorControls
              :options="normalTemporalOptions"
              :model="normalTemporalModel"
              :disabled="normalDefaultBusy !== ''"
              @update:model="normalTemporalModel = $event"
            />
            <button class="arcade-button primary" :disabled="normalDefaultBusy !== '' || !normalTemporalModel" @click="saveNormalEstimator('temporal')">{{ $t("settings.saveDefault") }}</button>
          </div>
        </div>
      </section>
      <div v-if="activeRuntime && defaults && modelBundles.length" class="runtime-model-bundles">
        <h3>{{ $t("settings.modelBundles") }}</h3>
        <article v-for="bundle in modelBundles" :key="bundle.id" class="model-bundle-card" :class="{ ready: bundle.ready }">
          <div class="model-bundle-heading"><div><strong>{{ bundle.label }}</strong><small>{{ bundle.license }}<span v-if="bundle.recommended"> · {{ $t("settings.recommended") }}</span></small></div><span class="runtime-status">{{ bundle.ready ? $t("settings.modelReady") : $t("settings.modelMissing") }}</span></div>
          <div class="model-bundle-files"><span v-for="file in bundle.files" :key="file.path" :class="{ present: file.present }">{{ file.present ? "✓" : "·" }} {{ file.path }}</span></div>
          <div class="model-bundle-actions"><button class="arcade-button" :disabled="bundle.ready || !!modelDownloadBusy || (!!modelDownload && ['queued','downloading','verifying'].includes(modelDownload.status))" @click="downloadModelBundle(bundle)"><DownloadSimple :size="18" />{{ bundle.ready ? $t("settings.modelReady") : $t("settings.downloadModel") }}</button><small v-if="modelDownload?.bundle_id === bundle.id">{{ modelDownload.message }}<template v-if="modelDownload.status !== 'failed'"> · {{ Math.round(modelDownload.progress * 100) }}%</template><template v-if="modelDownload.error"> · {{ modelDownload.error.message }}</template></small></div>
          <div v-if="modelDownload?.bundle_id === bundle.id && ['queued','downloading','verifying'].includes(modelDownload.status)" class="setup-progress model-download-progress"><i :style="{ width: `${Math.max(2, modelDownload.progress * 100)}%` }"></i></div>
        </article>
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
