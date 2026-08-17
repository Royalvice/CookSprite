<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { PhCheckCircle as CheckCircle, PhCpu as Cpu, PhSpinnerGap as SpinnerGap, PhWarningCircle as WarningCircle } from "@phosphor-icons/vue";
import type { RunView } from "../api/generated";

const props = defineProps<{ run: RunView }>();
const { t } = useI18n();

const terminal = computed(() => ["succeeded", "failed", "cancelled"].includes(props.run.status));
const state = computed(() => props.run.runtime_state);
const error = computed(() => state.value.error || props.run.error);
const progress = computed(() => Math.max(0, Math.min(1, props.run.progress || 0)));
const modelLabel = computed(() => t(`runtime.model.${state.value.model_status}`));
const phaseLabel = computed(() => t(`runtime.phase.${state.value.phase}`));
const statusLabel = computed(() => t(`runtime.status.${props.run.status}`));
</script>

<template>
  <section class="run-status-panel panel" :class="{ terminal, failed: Boolean(error) }" role="status" aria-live="polite">
    <header>
      <div class="run-status-heading">
        <span class="eyebrow"><Cpu :size="14" />{{ $t("runtime.live") }}</span>
        <strong>{{ phaseLabel }}</strong>
        <small>{{ state.message || run.message }}</small>
      </div>
      <span class="run-status-badge" :class="run.status">
        <SpinnerGap v-if="!terminal" class="spin" :size="14" />
        <CheckCircle v-else-if="run.status === 'succeeded'" :size="14" weight="fill" />
        <WarningCircle v-else-if="error" :size="14" weight="fill" />
        {{ statusLabel }}
      </span>
    </header>
    <div class="run-progress" :aria-label="$t('runtime.progress')" role="progressbar" :aria-valuenow="Math.round(progress * 100)" aria-valuemin="0" aria-valuemax="100">
      <i :style="{ width: `${progress * 100}%` }"></i>
    </div>
    <div class="run-status-facts">
      <span><b>{{ $t("runtime.modelLabel") }}</b><em :class="`is-${state.model_status}`">{{ modelLabel }}</em></span>
      <span v-if="state.current"><b>{{ $t("runtime.nodeLabel") }}</b><em>{{ state.current.label }}</em></span>
      <span v-if="state.current?.total"><b>{{ $t("runtime.stepLabel") }}</b><em>{{ state.current.step || 0 }} / {{ state.current.total }}</em></span>
      <span v-if="state.queue_remaining !== undefined"><b>{{ $t("runtime.queueLabel") }}</b><em>{{ state.queue_remaining }}</em></span>
    </div>
    <div v-if="error" class="run-error" role="alert">
      <WarningCircle :size="17" weight="fill" />
      <div>
        <strong>{{ error.code }}</strong>
        <span>{{ error.message }}</span>
        <small v-if="error.node">{{ $t("runtime.errorNode", { node: error.node }) }}</small>
        <pre v-if="error.detail">{{ error.detail }}</pre>
      </div>
    </div>
  </section>
</template>
