<script setup lang="ts">
import { computed } from "vue";

export type ParameterSchema = {
  type?: string;
  title?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  multipleOf?: number;
};

export type NormalEstimatorOption = {
  id: string;
  modelId: string;
  label: string;
  paramsSchema?: { properties?: Record<string, ParameterSchema> };
};

const props = withDefaults(defineProps<{
  options: NormalEstimatorOption[];
  model: string;
  params?: Record<string, unknown>;
  disabled?: boolean;
  advanced?: boolean;
}>(), {
  params: () => ({}),
  disabled: false,
  advanced: false,
});

const emit = defineEmits<{
  "update:model": [value: string];
  "update:param": [name: string, value: number];
}>();

const selected = computed(() => props.options.find((option) => option.id === props.model));
const parameterFields = computed(() => Object.entries(selected.value?.paramsSchema?.properties || {}));

function value(name: string, fallback: unknown) {
  const defaultValue = Number(fallback ?? 0);
  const raw = Number(props.params[name] ?? defaultValue);
  return Number.isFinite(raw) ? raw : defaultValue;
}
</script>

<template>
  <section class="normal-estimator-controls">
    <label>
      <span>{{ $t("normal.estimator") }}</span>
      <select
        :value="model"
        :disabled="disabled || !options.length"
        :aria-label="$t('normal.estimator')"
        @change="emit('update:model', ($event.target as HTMLSelectElement).value)"
      >
        <option v-if="!options.length" value="">{{ $t("settings.noCompatibleModel") }}</option>
        <option v-for="option in options" :key="option.id" :value="option.id">{{ option.label }}</option>
      </select>
    </label>
    <details v-if="advanced && parameterFields.length" class="normal-estimator-advanced">
      <summary>{{ $t("normal.advanced") }}</summary>
      <div>
        <label v-for="[name, schema] in parameterFields" :key="name">
          <span>{{ schema.title || name }}</span>
          <input
            :value="value(name, schema.default)"
            type="number"
            :min="schema.minimum"
            :max="schema.maximum"
            :step="schema.multipleOf || 1"
            @change="emit('update:param', name, Number(($event.target as HTMLInputElement).value))"
          />
        </label>
      </div>
    </details>
  </section>
</template>
