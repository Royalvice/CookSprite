<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  PhCheck as Check,
  PhCloudSlash as CloudSlash,
  PhDatabase as Database,
  PhFolderOpen as FolderOpen,
  PhGauge as Gauge,
  PhPlus as Plus,
  PhScan as Radar,
  PhSpinner as Spinner,
  PhSpeakerHigh as SpeakerHigh,
  PhSpeakerSlash as SpeakerSlash,
  PhTrash as Trash,
  PhWrench as Wrench,
} from "@phosphor-icons/vue";
import {
  api,
  type ComfyProbeView,
  type RuntimeCapabilities,
  type RuntimeDefaultsView,
  type RuntimeView,
} from "../api/generated";
import { useStudioStore } from "../stores/studio";

type RuntimeLocation = "local" | "remote";

const store = useStudioStore();
const { locale, t } = useI18n();
const runtimes = ref<RuntimeView[]>([]);
const comfyProbe = ref<ComfyProbeView | null>(null);
const probingComfy = ref(false);
const runtimeBusy = ref("");
const runtimeMessage = ref("");
const capabilities = ref<RuntimeCapabilities | null>(null);
const defaults = ref<RuntimeDefaultsView | null>(null);
const defaultAction = ref("image.generate");
const defaultModel = ref("");
const defaultBusy = ref(false);
const defaultMode = ref("");
// Leave the endpoint empty so deployment topology is always an explicit choice.
const endpointUrl = ref("");
const callbackUrl = ref("");
const runtimeLabel = ref("ComfyUI");
const runtimeLocation = ref<RuntimeLocation>("remote");
const workerManaged = ref(false);
const projectName = ref("");
const selectedProjectId = ref("");
const projectMessage = ref("");
const theme = ref(localStorage.getItem("cooksprite.theme") || "neon");
const sound = ref(localStorage.getItem("cooksprite.sound") === "on");

const usage = computed(() => store.allArtifacts.reduce((sum, item) => sum + item.size, 0));
const activeRuntime = computed(() => runtimes.value.find((item) => item.id === store.activeRuntimeId));
const probeCandidate = computed(() => comfyProbe.value?.candidates[0] || null);
const modelBundles = computed(() => defaults.value?.model_bundles || []);
const categoryLabels: Record<string, string> = {
  image: "settings.categoryImage",
  text: "settings.categoryText",
  video: "settings.categoryVideo",
  tools: "settings.categoryTools",
};
type CapabilityItem = Record<string, unknown>;

function normalizeEndpoint(value: string) {
  return value.trim().replace(/\/+$/, "");
}

function capabilityLabel(item: CapabilityItem) {
  return String(item.label || item.id || "");
}

function capabilitySource(item: CapabilityItem) {
  return String(item.source || "");
}

function identitySummary(runtime: RuntimeView): string {
  const identity = runtime.runtime_identity;
  if (!runtime.worker_managed) return String(t("worker.externalRuntime"));
  if (!identity) return String(t("worker.identityPending"));
  return `${identity.source_branch}@${identity.source_revision.slice(0, 12)} · ${identity.node_pack_version} · ${identity.dependency_lock_sha256.slice(0, 12)}`;
}

function nodeSummary(runtime: RuntimeView): string {
  const status = runtime.nodes_installed ? t("settings.nodesReady") : t("settings.nodesMissing");
  return `${runtime.cooksprite_nodes || 0} · ${String(status)}`;
}

async function refreshDefaults(id: string) {
  defaults.value = await api.runtimeDefaults(id).catch(() => null);
  syncDefaultModel();
}

async function refreshRuntimes() {
  runtimes.value = await store.refreshRuntimes().catch(() => []);
  if (!store.activeRuntimeId) {
    capabilities.value = null;
    defaults.value = null;
    return;
  }
  capabilities.value = await api.runtimeCapabilities(store.activeRuntimeId).catch(() => null);
  await refreshDefaults(store.activeRuntimeId);
}

async function refreshActiveRuntime(id: string) {
  capabilities.value = await api.runtimeCapabilities(id).catch(() => null);
  await refreshDefaults(id);
}

onMounted(async () => {
  selectedProjectId.value = store.currentProject?.id
    || localStorage.getItem("cooksprite.current-project")
    || store.projects[0]?.id
    || "";
  await refreshRuntimes();
});

watch(() => store.currentProject?.id, (id) => {
  selectedProjectId.value = id || "";
});

watch(() => store.activeRuntimeId, (id) => {
  if (id) void refreshActiveRuntime(id);
});

watch(defaultAction, syncDefaultModel);
watch(defaultMode, syncDefaultModel);

const defaultModes = computed(() => Array.from(new Set(
  (defaults.value?.recipes || [])
    .filter((recipe) => recipe.actions.includes(defaultAction.value))
    .flatMap((recipe) => recipe.modes),
)));
const defaultModels = computed(() => (defaults.value?.models || []).filter((model) => (
  model.actions.includes(defaultAction.value)
  && (!defaultMode.value || model.modes.includes(defaultMode.value))
)));
const defaultActions = computed(() => Array.from(
  new Set((defaults.value?.models || []).flatMap((model) => model.actions)),
));

function syncDefaultModel() {
  const binding = defaultMode.value
    ? defaults.value?.mode_defaults[defaultAction.value]?.[defaultMode.value]
    : defaults.value?.defaults[defaultAction.value];
  defaultModel.value = defaultModels.value.find((model) => model.id === binding?.model_id)?.id
    || defaultModels.value[0]?.id
    || "";
}

watch(defaultActions, (actions) => {
  if (actions.length && !actions.includes(defaultAction.value)) defaultAction.value = actions[0];
});
watch(defaultModes, (modes) => {
  defaultMode.value = modes.includes(defaultMode.value) ? defaultMode.value : modes[0] || "";
}, { immediate: true });

async function saveDefault() {
  if (!store.activeRuntimeId || !defaultModel.value) return;
  defaultBusy.value = true;
  try {
    if (defaultMode.value) {
      await api.setRuntimeModeDefault(store.activeRuntimeId, defaultAction.value, defaultMode.value, {
        model_id: defaultModel.value,
      });
    } else {
      await api.setRuntimeDefault(store.activeRuntimeId, defaultAction.value, {
        model_id: defaultModel.value,
      });
    }
    await refreshDefaults(store.activeRuntimeId);
    runtimeMessage.value = String(t("settings.defaultSaved"));
  } catch (error) {
    runtimeMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    defaultBusy.value = false;
  }
}

function setTheme(value: string) {
  theme.value = value;
  document.documentElement.dataset.theme = value;
  localStorage.setItem("cooksprite.theme", value);
}

function setLanguage(value: string) {
  locale.value = value;
  document.documentElement.lang = value;
  localStorage.setItem("cooksprite.language", value);
}

function setSound(value: boolean) {
  sound.value = value;
  localStorage.setItem("cooksprite.sound", value ? "on" : "off");
}

async function switchProject() {
  if (!selectedProjectId.value) return;
  try {
    await store.openProject(selectedProjectId.value);
    projectMessage.value = String(t("settings.projectSwitched"));
  } catch (error) {
    projectMessage.value = error instanceof Error ? error.message : String(error);
  }
}

async function createProject() {
  try {
    const project = await store.createProject(projectName.value.trim() || String(t("studio.untitled")));
    selectedProjectId.value = project.id;
    projectName.value = "";
    projectMessage.value = String(t("settings.projectCreated"));
  } catch (error) {
    projectMessage.value = error instanceof Error ? error.message : String(error);
  }
}

async function probeComfy() {
  probingComfy.value = true;
  runtimeMessage.value = "";
  try {
    comfyProbe.value = await api.probeComfy(normalizeEndpoint(endpointUrl.value));
  } catch (error) {
    runtimeMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    probingComfy.value = false;
  }
}

async function connectRuntime() {
  const baseUrl = normalizeEndpoint(endpointUrl.value);
  const callback = normalizeEndpoint(callbackUrl.value);
  if (!baseUrl) return;
  if (runtimeLocation.value === "remote" && !callback) {
    runtimeMessage.value = String(t("worker.callbackRequired"));
    return;
  }
  runtimeBusy.value = "connect";
  runtimeMessage.value = "";
  try {
    const runtime = await api.createRuntime({
      label: runtimeLabel.value.trim() || (workerManaged.value ? "Managed ComfyUI" : "ComfyUI"),
      base_url: baseUrl,
      location: runtimeLocation.value,
      transport: "http",
      callback_url: runtimeLocation.value === "remote" ? callback : undefined,
      worker_managed: workerManaged.value,
    });
    await api.doctorRuntime(runtime.id);
    await store.selectRuntime(runtime.id);
    await refreshRuntimes();
    runtimeMessage.value = String(t("worker.connected"));
  } catch (error) {
    runtimeMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    runtimeBusy.value = "";
  }
}

async function doctorRuntime(runtime: RuntimeView) {
  runtimeBusy.value = runtime.id;
  runtimeMessage.value = "";
  try {
    await api.doctorRuntime(runtime.id);
    await refreshRuntimes();
    runtimeMessage.value = String(t("worker.doctorDone"));
  } catch (error) {
    runtimeMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    runtimeBusy.value = "";
  }
}

async function selectRuntime(id: string) {
  runtimeBusy.value = id;
  try {
    await store.selectRuntime(id);
    await refreshRuntimes();
  } catch (error) {
    runtimeMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    runtimeBusy.value = "";
  }
}

async function deleteRuntime(runtime: RuntimeView) {
  if (!window.confirm(String(t("settings.deleteRuntimeConfirm", { label: runtime.label })))) return;
  runtimeBusy.value = runtime.id;
  runtimeMessage.value = "";
  try {
    const result = await api.deleteRuntime(runtime.id);
    await Promise.all([refreshRuntimes(), store.refreshRuntime()]);
    runtimeMessage.value = result.message;
  } catch (error) {
    runtimeMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    runtimeBusy.value = "";
  }
}
</script>

<template>
  <div class="settings-view page-wrap narrow-page">
    <section class="page-heading">
      <span class="eyebrow">{{ $t("settings.eyebrow") }}</span>
      <h1>{{ $t("worker.title") }}</h1>
      <p>{{ $t("worker.privacy") }}</p>
    </section>

    <section class="settings-section panel project-context-section">
      <header><div><span class="section-index">01</span><h2>{{ $t("settings.projects") }}</h2></div><FolderOpen :size="24" /></header>
      <p>{{ $t("worker.projectHelp") }}</p>
      <div class="project-switcher">
        <label>
          <span>{{ $t("settings.currentProject") }}</span>
          <select v-model="selectedProjectId" :disabled="!store.projects.length" @change="switchProject">
            <option v-if="!store.projects.length" value="">{{ $t("settings.noProjects") }}</option>
            <option v-for="project in store.projects" :key="project.id" :value="project.id">{{ project.name }}</option>
          </select>
        </label>
      </div>
      <div class="project-create-form">
        <label><span>{{ $t("settings.newProject") }}</span><input v-model="projectName" :placeholder="$t('studio.untitled')" @keyup.enter="createProject" /></label>
        <button class="arcade-button primary" @click="createProject"><Plus :size="18" />{{ $t("settings.createProject") }}</button>
      </div>
      <p v-if="projectMessage" class="inline-status" role="status">{{ projectMessage }}</p>
    </section>

    <section class="settings-section panel">
      <header><div><span class="section-index">02</span><h2>{{ $t("settings.runtime") }}</h2></div><Wrench :size="24" /></header>
      <p>{{ $t("worker.runtimeHelp") }}</p>
      <div class="runtime-endpoint-card">
        <div class="runtime-connection-grid">
          <label><span>{{ $t("worker.runtimeLabel") }}</span><input v-model="runtimeLabel" autocomplete="off" /></label>
          <label><span>{{ $t("worker.location") }}</span><select v-model="runtimeLocation"><option value="remote">{{ $t("worker.remote") }}</option><option value="local">{{ $t("worker.local") }}</option></select></label>
          <label class="runtime-url"><span>{{ $t("worker.runtimeUrl") }}</span><input v-model="endpointUrl" type="url" autocomplete="url" spellcheck="false" placeholder="http://runtime-host:8288" @keyup.enter="probeComfy" /></label>
          <label v-if="runtimeLocation === 'remote'" class="callback-url"><span>{{ $t("worker.apiCallback") }}</span><input v-model="callbackUrl" type="url" autocomplete="url" spellcheck="false" @keyup.enter="connectRuntime" /></label>
        </div>
        <label class="worker-toggle"><input v-model="workerManaged" type="checkbox" /><span><b>{{ $t("worker.workerManaged") }}</b><small>{{ $t("worker.workerManagedHelp") }}</small></span></label>
        <small v-if="runtimeLocation === 'remote'">{{ $t("worker.callbackHelp") }}</small>
        <div class="runtime-endpoint-actions">
          <button class="arcade-button" :disabled="probingComfy || !!runtimeBusy || !endpointUrl.trim()" @click="probeComfy"><Spinner v-if="probingComfy" class="spin" :size="18" /><Radar v-else :size="18" />{{ $t("worker.probe") }}</button>
          <button class="arcade-button primary" :disabled="!!runtimeBusy || !endpointUrl.trim()" @click="connectRuntime"><Wrench :size="18" />{{ $t("worker.connect") }}</button>
        </div>
        <div v-if="comfyProbe" class="probe-result" :class="comfyProbe.status">
          <template v-if="probeCandidate?.status === 'found'">
            <strong>{{ $t("runtimeProbe.found") }}</strong>
            <span>{{ probeCandidate.base_url }} · {{ $t("settings.modelsCount", { count: probeCandidate.models || 0 }) }} · {{ $t("settings.nodesCount", { count: probeCandidate.nodes || 0 }) }}</span>
          </template>
          <template v-else><strong>{{ $t("runtimeProbe.unavailable") }}</strong><span>{{ probeCandidate?.error || $t("runtimeProbe.unavailableHelp") }}</span></template>
        </div>
      </div>

      <div class="worker-command-card">
        <div><strong>{{ $t("worker.lifecycle") }}</strong><span>{{ $t("worker.lifecycleHelp") }}</span></div>
        <code>cspr comfy worker sync --runtime-dir ../worker-runtime · cspr comfy worker start --runtime-dir ../worker-runtime · cspr comfy worker doctor --runtime-dir ../worker-runtime --json</code>
      </div>

      <h3>{{ $t("settings.connectedRuntimes") }}</h3>
      <div v-if="runtimes.length" class="runtime-list">
        <article v-for="runtime in runtimes" :key="runtime.id" :class="[runtime.status, { active: runtime.id === store.activeRuntimeId }]">
          <Gauge :size="22" />
          <div>
            <strong>{{ runtime.label }}</strong>
            <span>{{ runtime.location === "local" ? $t("worker.local") : $t("worker.remote") }} · {{ runtime.base_url }}</span>
            <small v-if="runtime.error" class="runtime-error">{{ runtime.error }}</small>
            <small v-else>{{ identitySummary(runtime) }}</small>
            <small>{{ runtime.callback_url || $t("worker.localCallback") }} · {{ nodeSummary(runtime) }}</small>
          </div>
          <span class="runtime-status">{{ runtime.status === "ready" ? $t("settings.connected") : $t(`common.${runtime.status || 'offline'}`) }}</span>
          <div class="runtime-actions">
            <button class="text-button" :disabled="!!runtimeBusy" @click="doctorRuntime(runtime)">{{ $t("worker.doctor") }}</button>
            <button v-if="runtime.id !== store.activeRuntimeId" class="text-button" :disabled="!!runtimeBusy" @click="selectRuntime(runtime.id)">{{ $t("settings.useRuntime") }}</button>
            <Check v-else-if="runtime.status === 'ready'" :size="18" weight="bold" />
            <button class="text-button danger" :disabled="!!runtimeBusy" @click="deleteRuntime(runtime)"><Trash :size="16" />{{ $t("settings.deleteRuntime") }}</button>
          </div>
        </article>
      </div>
      <div v-else class="runtime-empty">{{ $t("settings.noRuntimes") }}</div>
      <p v-if="runtimeMessage" class="inline-status" role="status">{{ runtimeMessage }}</p>

      <div v-if="activeRuntime && capabilities" class="capability-summary">
        <header><strong>{{ $t("settings.capabilities") }} · {{ activeRuntime.label }}</strong><span>{{ capabilities.system.comfyui_version || "ComfyUI" }}</span></header>
        <div class="capability-grid">
          <article v-for="(category, key) in capabilities.categories" :key="key">
            <strong>{{ $t(categoryLabels[key] || key) }}</strong>
            <span>{{ category.models.length }} {{ $t("settings.models") }} · {{ category.workflows.length }} {{ $t("settings.workflows") }} · {{ category.tools.length }} {{ $t("settings.tools") }}</span>
            <details v-if="category.models.length || category.workflows.length || category.tools.length">
              <summary>{{ $t("settings.viewDetails") }}</summary>
              <div class="capability-items">
                <div v-for="item in category.models.slice(0, 20)" :key="`model-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div>
                <div v-for="item in category.workflows.slice(0, 20)" :key="`workflow-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div>
                <div v-for="item in category.tools.slice(0, 20)" :key="`tool-${String(item.id)}`"><b>{{ capabilityLabel(item) }}</b><small>{{ capabilitySource(item) }}</small></div>
              </div>
            </details>
          </article>
        </div>
      </div>

      <div v-if="activeRuntime && defaults" class="runtime-defaults">
        <h3>{{ $t("settings.defaults") }}</h3>
        <div class="runtime-default-form" :class="{ 'with-normal-mode': defaultModes.length }">
          <label><span>{{ $t("settings.defaultAction") }}</span><select v-model="defaultAction"><option v-for="actionId in defaultActions" :key="actionId" :value="actionId">{{ actionId }}</option></select></label>
          <label v-if="defaultModes.length"><span>{{ $t("normal.inputMode") }}</span><select v-model="defaultMode"><option v-for="mode in defaultModes" :key="mode" :value="mode">{{ mode }}</option></select></label>
          <label><span>{{ $t("settings.defaultModel") }}</span><select v-model="defaultModel" :disabled="!defaultModels.length"><option value="" disabled>{{ $t("settings.noCompatibleModel") }}</option><option v-for="model in defaultModels" :key="model.id" :value="model.id">{{ model.label }}</option></select></label>
          <button class="arcade-button primary" :disabled="defaultBusy || !defaultModel" @click="saveDefault">{{ $t("settings.saveDefault") }}</button>
        </div>
      </div>

      <div v-if="activeRuntime && modelBundles.length" class="runtime-model-bundles">
        <h3>{{ $t("settings.modelBundles") }}</h3>
        <article v-for="bundle in modelBundles" :key="bundle.id" class="model-bundle-card" :class="{ ready: bundle.ready }">
          <div class="model-bundle-heading"><div><strong>{{ bundle.label }}</strong><small>{{ bundle.license }}<span v-if="bundle.recommended"> · {{ $t("settings.recommended") }}</span></small></div><span class="runtime-status">{{ bundle.ready ? $t("settings.modelReady") : $t("settings.modelMissing") }}</span></div>
          <div class="model-bundle-files"><span v-for="file in bundle.files" :key="file.path" :class="{ present: file.present }">{{ file.present ? "✓" : "·" }} {{ file.path }}</span></div>
          <small class="readonly-inventory">{{ $t("worker.modelInventory") }}</small>
        </article>
      </div>
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
      <div class="privacy-card"><CloudSlash :size="28" /><div><strong>{{ $t("worker.apiArtifacts") }}</strong><span>{{ $t("worker.apiArtifactsHelp") }}</span></div></div>
    </section>
  </div>
</template>
