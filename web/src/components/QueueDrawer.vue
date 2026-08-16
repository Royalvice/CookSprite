<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { DialogClose, DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from "reka-ui";
import { PhArrowClockwise as ArrowClockwise, PhCheckCircle as CheckCircle, PhClock as Clock, PhProhibit as Prohibit, PhSpinnerGap as SpinnerGap, PhX as X } from "@phosphor-icons/vue";
import { useStudioStore } from "../stores/studio";
import type { RunView } from "../api/generated";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ "update:open": [value: boolean] }>();
const store = useStudioStore();
const { t } = useI18n();
const groups = computed(() => [
  { id: "running", label: t("queue.running"), items: store.queue.running },
  { id: "pending", label: t("queue.pending"), items: store.queue.pending },
  { id: "history", label: t("queue.history"), items: store.queue.history.slice(0, 20) },
]);
const terminal = (run: RunView) => ["succeeded", "failed", "cancelled"].includes(run.status);
</script>

<template>
  <DialogRoot :open="props.open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="dialog-overlay" />
      <DialogContent class="queue-drawer" @open-auto-focus.prevent>
        <header class="drawer-head">
          <div>
            <span class="eyebrow">{{ $t("queue.eyebrow") }}</span>
            <DialogTitle>{{ $t("queue.title") }}</DialogTitle>
            <DialogDescription>{{ $t("queue.description") }}</DialogDescription>
          </div>
          <DialogClose class="icon-button" :aria-label="$t('common.close')"><X :size="20" /></DialogClose>
        </header>
        <div class="queue-groups">
          <section v-for="group in groups" :key="group.id" class="queue-group">
            <h3>{{ group.label }} <span>{{ group.items.length }}</span></h3>
            <p v-if="!group.items.length" class="muted empty-line">{{ $t("common.noRuns") }}</p>
            <article v-for="run in group.items" :key="run.id" class="run-row" :class="`is-${run.status}`">
              <SpinnerGap v-if="run.status === 'running'" class="spin" :size="18" />
              <Clock v-else-if="run.status === 'queued'" :size="18" />
              <CheckCircle v-else-if="run.status === 'succeeded'" :size="18" weight="fill" />
              <Prohibit v-else :size="18" />
              <div>
                <strong>{{ run.action_id || "contributor.run" }}</strong>
                <span>{{ run.message }}</span>
                <div v-if="!terminal(run)" class="micro-progress"><i :style="{ width: `${run.progress * 100}%` }"></i></div>
              </div>
              <button v-if="!terminal(run)" class="icon-button compact" :aria-label="$t('common.cancel')" @click="store.cancel(run.id)"><X :size="15" /></button>
              <button v-else-if="run.status !== 'succeeded'" class="icon-button compact" :aria-label="$t('common.retry')" @click="store.retry(run.id)"><ArrowClockwise :size="15" /></button>
            </article>
          </section>
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
