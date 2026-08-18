<script setup lang="ts">
import { computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { ActionControl, Locale } from "../api/generated";

const props = defineProps<{
  styleControl?: ActionControl;
  values: Record<string, unknown>;
}>();

const { locale } = useI18n();
const characterStyles = new Set(["ultra_realistic", "2d_action_game", "stylized_3d", "anime", "pixel_art"]);
const isCharacter = computed(() => String(props.values.category || "") === "character");
const styleOptions = computed(() => props.styleControl?.options.filter((option) => characterStyles.has(option.id)) || []);

function copy(control: ActionControl | undefined) {
  return control?.i18n[locale.value as Locale] || { name: "", description: "" };
}

function optionCopy(option: ActionControl["options"][number]) {
  return option.i18n[locale.value as Locale];
}

function syncCharacterDefaults() {
  if (!isCharacter.value || !styleOptions.value.length) return;
  if (!characterStyles.has(String(props.values.style || ""))) {
    props.values.style = "2d_action_game";
  }
}

watch(() => props.values.category, syncCharacterDefaults, { immediate: true });
</script>

<template>
  <section v-if="isCharacter && styleControl" class="prompt-style" data-testid="character-prompt-options">
    <label class="prompt-select-field">
      <span>{{ copy(styleControl).name }}</span>
      <small>{{ copy(styleControl).description }}</small>
      <select v-model="values.style" :disabled="values.prompt_compile !== true" data-testid="character-style-select">
        <option v-for="option in styleOptions" :key="option.id" :value="option.id">{{ optionCopy(option).name }}</option>
      </select>
    </label>
  </section>
</template>
