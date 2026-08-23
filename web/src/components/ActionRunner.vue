<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import type {
  ActionControl,
  ActionDescriptor,
  ArtifactKind,
  ArtifactRef,
  Locale,
} from "../api/generated";

const props = defineProps<{
  action: ActionDescriptor;
  artifacts: ArtifactRef[];
  locale: Locale;
  running?: boolean;
}>();

const emit = defineEmits<{
  run: [actionId: string, inputs: Record<string, string | string[]>, values: Record<string, unknown>, params: Record<string, unknown>];
}>();

const inputs = reactive<Record<string, string | string[]>>({});
const values = reactive<Record<string, unknown>>({});
const params = reactive<Record<string, unknown>>({});

const selectedModel = computed(() => props.action.models.find((item) => item.id === values.model));
const parameterFields = computed(() => Object.entries(selectedModel.value?.params_schema?.properties || {}));
const canRun = computed(() => props.action.available && !props.running && Object.entries(props.action.accepts).every(
  ([slot, rule]) => !rule.required || Boolean(Array.isArray(inputs[slot]) ? inputs[slot].length : inputs[slot]),
));

watch(
  () => props.action,
  (action) => {
    Object.keys(values).forEach((key) => delete values[key]);
    Object.keys(inputs).forEach((key) => delete inputs[key]);
    Object.keys(params).forEach((key) => delete params[key]);
    action.controls.forEach((control) => { values[control.id] = structuredClone(control.default); });
    if (action.models.length === 1) values.model = action.models[0].id;
    const model = action.models.find((item) => item.id === values.model);
    Object.entries(model?.params_schema?.properties || {}).forEach(([name, schema]) => {
      if (schema.default !== undefined) params[name] = schema.default;
    });
  },
  { immediate: true },
);

watch(selectedModel, (model) => {
  Object.keys(params).forEach((key) => delete params[key]);
  Object.entries(model?.params_schema?.properties || {}).forEach(([name, schema]) => {
    if (schema.default !== undefined) params[name] = schema.default;
  });
});

function copy(control: ActionControl) {
  return control.i18n[props.locale] || control.i18n.en;
}

function acceptedKinds(type: ArtifactKind | ArtifactKind[]) {
  return Array.isArray(type) ? type : [type];
}

function availableArtifacts(type: ArtifactKind | ArtifactKind[]) {
  const accepted = acceptedKinds(type);
  return props.artifacts.filter((artifact) => accepted.includes(artifact.kind));
}

function selectInput(slot: string, event: Event, multiple: boolean) {
  const select = event.target as HTMLSelectElement;
  inputs[slot] = multiple
    ? [...select.selectedOptions].map((option) => option.value)
    : select.value;
}

function rangeOptions(control: ActionControl) {
  if (!control.options_range) return [];
  const [start, stop, step] = control.options_range;
  const result: number[] = [];
  for (let value = start; value <= stop; value += step) result.push(value);
  return result;
}

function submit() {
  emit("run", props.action.id, { ...inputs }, { ...values }, { ...params });
}
</script>

<template>
  <section class="generic-action-runner panel">
    <header>
      <span class="eyebrow">ACTION · {{ action.id.toUpperCase() }}</span>
      <h2>{{ action.i18n[locale]?.name || action.id }}</h2>
      <p>{{ action.i18n[locale]?.description }}</p>
    </header>

    <div class="generic-action-grid">
      <label v-for="(rule, slot) in action.accepts" :key="slot">
        <span>{{ slot }}{{ rule.required ? " *" : "" }}</span>
        <select
          :multiple="rule.max > 1"
          :value="inputs[slot]"
          @change="selectInput(String(slot), $event, rule.max > 1)"
        >
          <option v-if="!rule.required" value="">—</option>
          <option v-for="artifact in availableArtifacts(rule.type)" :key="artifact.id" :value="artifact.id">
            {{ artifact.title || artifact.id }} · {{ artifact.kind }}
          </option>
        </select>
      </label>

      <label v-if="action.models.length">
        <span>Model</span>
        <select v-model="values.model">
          <option value="">—</option>
          <option v-for="model in action.models" :key="model.id" :value="model.id">{{ model.label }}</option>
        </select>
      </label>

      <label v-for="control in action.controls.filter((item) => !item.advanced)" :key="control.id">
        <span>{{ copy(control).name }}</span>
        <select v-if="control.type === 'select'" v-model="values[control.id]">
          <option v-for="option in control.options" :key="option.id" :value="option.id">
            {{ option.i18n[locale]?.name || option.id }}
          </option>
          <option v-for="option in rangeOptions(control)" :key="option" :value="String(option)">{{ option }}</option>
        </select>
        <textarea
          v-else-if="control.type === 'text'"
          :value="String(values[control.id] ?? '')"
          rows="2"
          @input="values[control.id] = ($event.target as HTMLTextAreaElement).value"
        />
        <input v-else-if="control.type === 'toggle'" v-model="values[control.id]" type="checkbox" />
        <input
          v-else
          v-model.number="values[control.id]"
          :type="control.type === 'color' ? 'color' : control.type === 'range' ? 'range' : 'number'"
          :min="control.min"
          :max="control.max"
          :step="control.step"
        />
      </label>
    </div>

    <details v-if="action.controls.some((item) => item.advanced) || parameterFields.length">
      <summary>Advanced</summary>
      <div class="generic-action-grid">
        <label v-for="control in action.controls.filter((item) => item.advanced)" :key="control.id">
          <span>{{ copy(control).name }}</span>
          <input
            v-model.number="values[control.id]"
            :type="control.type === 'range' ? 'range' : 'number'"
            :min="control.min"
            :max="control.max"
            :step="control.step"
          />
        </label>
        <label v-for="[name, schema] in parameterFields" :key="name">
          <span>{{ schema.title || name }}</span>
          <input
            v-model.number="params[name]"
            type="number"
            :min="schema.minimum"
            :max="schema.maximum"
            :step="schema.multipleOf || 1"
          />
        </label>
      </div>
    </details>

    <button class="arcade-button primary" :disabled="!canRun" @click="submit">Run</button>
  </section>
</template>

<style scoped>
.generic-action-runner { display: grid; gap: 1rem; padding: 1rem; }
.generic-action-runner header h2 { margin: .2rem 0; }
.generic-action-runner header p { margin: 0; color: var(--muted); }
.generic-action-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: .75rem; }
.generic-action-grid label { display: grid; gap: .35rem; }
.generic-action-grid select, .generic-action-grid input, .generic-action-grid textarea { width: 100%; }
</style>
