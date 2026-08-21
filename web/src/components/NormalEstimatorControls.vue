<script setup lang="ts">
import { computed } from "vue";

export type NormalEstimatorOption = {
  id: string;
  modelId: string;
  label: string;
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
const isNormalCrafter = computed(() => selected.value?.modelId === "normalcrafter-v1");

function value(name: string, fallback: number) {
  const raw = Number(props.params[name] ?? fallback);
  return Number.isFinite(raw) ? raw : fallback;
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
    <details v-if="advanced && isNormalCrafter" class="normal-estimator-advanced">
      <summary>{{ $t("normal.advanced") }}</summary>
      <div>
        <label>
          <span>{{ $t("normal.maxResolution") }}</span>
          <input
            :value="value('max_resolution', 1024)"
            type="number"
            min="256"
            max="1024"
            step="64"
            @change="emit('update:param', 'max_resolution', Number(($event.target as HTMLInputElement).value))"
          />
        </label>
        <label>
          <span>{{ $t("normal.windowSize") }}</span>
          <input
            :value="value('window_size', 14)"
            type="number"
            min="2"
            max="32"
            step="1"
            @change="emit('update:param', 'window_size', Number(($event.target as HTMLInputElement).value))"
          />
        </label>
        <label>
          <span>{{ $t("normal.timeStep") }}</span>
          <input
            :value="value('time_step_size', 10)"
            type="number"
            min="1"
            max="32"
            step="1"
            @change="emit('update:param', 'time_step_size', Number(($event.target as HTMLInputElement).value))"
          />
        </label>
        <label>
          <span>{{ $t("normal.decodeChunk") }}</span>
          <input
            :value="value('decode_chunk_size', 7)"
            type="number"
            min="1"
            max="32"
            step="1"
            @change="emit('update:param', 'decode_chunk_size', Number(($event.target as HTMLInputElement).value))"
          />
        </label>
      </div>
    </details>
  </section>
</template>
