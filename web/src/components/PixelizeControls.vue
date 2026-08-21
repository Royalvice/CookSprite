<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { ActionDescriptor, ActionOption, Locale } from "../api/generated";

const props = defineProps<{
  action?: ActionDescriptor;
  values: Record<string, unknown>;
  showTemporal?: boolean;
}>();
const emit = defineEmits<{
  change: [id: string, value: unknown];
}>();
const { locale } = useI18n();

const controls = computed(() => props.action?.controls.filter((control) => (
  ["target_size", "palette_budget", "outline", "outline_color"].includes(control.id)
  || (props.showTemporal && control.id === "temporal_mode")
)) || []);

function options(control: ActionDescriptor["controls"][number]): ActionOption[] {
  if (control.options.length) return control.options;
  const range = control.options_range;
  if (!range) return [];
  const [start, stop, step] = range;
  return Array.from({ length: Math.floor((stop - start) / step) + 1 }, (_, index) => {
    const value = start + index * step;
    return {
      id: String(value),
      i18n: {
        "zh-CN": { name: String(value), description: "" },
        en: { name: String(value), description: "" },
      },
    };
  });
}
</script>

<template>
  <div class="tool-bench-controls">
    <label
      v-for="control in controls"
      :key="control.id"
      :class="['tool-size-field', { 'tool-toggle-field': control.type === 'toggle' }]"
    >
      <template v-if="control.type === 'toggle'">
        <span>{{ control.i18n[locale as Locale].name }}</span>
        <button
          type="button"
          class="tool-toggle-button"
          :class="{ active: Boolean(values[control.id]) }"
          role="switch"
          :aria-checked="Boolean(values[control.id])"
          :aria-label="control.i18n[locale as Locale].name"
          @click="emit('change', control.id, !Boolean(values[control.id]))"
        >
          {{ Boolean(values[control.id]) ? "ON" : "OFF" }}
        </button>
      </template>
      <template v-else-if="control.type === 'color'">
        <span>{{ control.i18n[locale as Locale].name }}</span>
        <input
          class="tool-color-input"
          type="color"
          :value="String(values[control.id] || control.default)"
          :disabled="!Boolean(values.outline)"
          :aria-label="control.i18n[locale as Locale].name"
          @input="emit('change', control.id, ($event.target as HTMLInputElement).value)"
        />
      </template>
      <template v-else>
        <span>{{ control.i18n[locale as Locale].name }}</span>
        <select
          :value="String(values[control.id] ?? control.default)"
          :aria-describedby="control.i18n[locale as Locale].description ? `${control.id}-description` : undefined"
          @change="emit('change', control.id, ($event.target as HTMLSelectElement).value)"
        >
          <option v-for="option in options(control)" :key="option.id" :value="option.id">
            {{ option.i18n[locale as Locale].name }}
          </option>
        </select>
        <small
          v-if="control.id === 'temporal_mode' && control.i18n[locale as Locale].description"
          :id="`${control.id}-description`"
        >{{ control.i18n[locale as Locale].description }}</small>
      </template>
    </label>
  </div>
</template>
