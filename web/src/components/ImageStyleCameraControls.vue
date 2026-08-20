<script setup lang="ts">
import { computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { ActionControl, Locale } from "../api/generated";

const props = defineProps<{
  styleControl?: ActionControl;
  values: Record<string, unknown>;
}>();

const { locale } = useI18n();
const category = computed(() => String(props.values.category || "character"));
const styleOptions = computed(() => props.styleControl?.options.filter(
  (option) => !option.categories?.length || option.categories.includes(category.value),
) || []);

function copy(control: ActionControl | undefined) {
  return control?.i18n[locale.value as Locale] || { name: "", description: "" };
}

function optionCopy(option: ActionControl["options"][number]) {
  return option.i18n[locale.value as Locale];
}

function syncStyleDefault() {
  if (!styleOptions.value.length) return;
  if (!styleOptions.value.some((option) => option.id === String(props.values.style || ""))) {
    props.values.style = styleOptions.value[0].id;
  }
}

watch(() => props.values.category, syncStyleDefault, { immediate: true });
</script>

<template>
  <section v-if="styleControl" class="prompt-style" data-testid="asset-style-options">
    <label class="prompt-select-field">
      <span>{{ copy(styleControl).name }}</span>
      <small>{{ copy(styleControl).description }}</small>
      <select v-model="values.style" :disabled="values.prompt_compile !== true" data-testid="asset-style-select">
        <option v-for="option in styleOptions" :key="option.id" :value="option.id">{{ optionCopy(option).name }}</option>
      </select>
    </label>
  </section>
</template>
