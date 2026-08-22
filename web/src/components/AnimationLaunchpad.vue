<script setup lang="ts">
import { computed } from "vue";
import {
  PhArrowBendDownRight as Fall,
  PhArrowLineUp as Jump,
  PhArrowRight as ArrowRight,
  PhCircleNotch as CircleNotch,
  PhCubeTransparent as CubeTransparent,
  PhImageSquare as ImageSquare,
  PhPersonSimple as PersonSimple,
  PhPersonSimpleRun as PersonSimpleRun,
  PhPersonSimpleWalk as PersonSimpleWalk,
  PhSparkle as Sparkle,
} from "@phosphor-icons/vue";
import type { ArtifactRef } from "../api/generated";
import ArtifactVisual from "./ArtifactVisual.vue";
import DropTarget from "./DropTarget.vue";

export type AnimationTask = "views" | "idle" | "walk" | "run" | "jump" | "death";

const props = defineProps<{
  source?: ArtifactRef;
  task: AnimationTask;
  running: boolean;
  canRun: boolean;
  actionReady: boolean;
}>();

const emit = defineEmits<{
  artifact: [payload: { artifact_id: string; kind?: string }];
  files: [files: File[]];
  clear: [];
  selectTask: [task: AnimationTask];
  run: [];
}>();

const tasks = [
  { id: "views" as const, icon: CubeTransparent, accent: "view" },
  { id: "idle" as const, icon: PersonSimple, accent: "idle" },
  { id: "walk" as const, icon: PersonSimpleWalk, accent: "walk" },
  { id: "run" as const, icon: PersonSimpleRun, accent: "run" },
  { id: "jump" as const, icon: Jump, accent: "jump" },
  { id: "death" as const, icon: Fall, accent: "death" },
];

const runLabel = computed(() => props.task === "views" ? "animation.generateViews" : "animation.generateMotion");
const disabledReason = computed(() => {
  if (!props.source) return "animation.sourceMissing";
  if (!props.actionReady) return "animation.backendUnavailable";
  return "";
});
</script>

<template>
  <section class="animation-launchpad creation-deck" data-testid="animation-launchpad">
    <header class="animation-launchpad-head">
      <div>
        <span class="eyebrow">IMAGE → MOTION</span>
        <h1>{{ $t("animation.title") }}</h1>
        <p>{{ $t("animation.intro") }}</p>
      </div>
      <div class="animation-flow" aria-hidden="true">
        <ImageSquare :size="22" />
        <i></i>
        <Sparkle :size="20" weight="fill" />
        <i></i>
        <PersonSimpleRun :size="24" />
      </div>
    </header>

    <div class="animation-launchpad-body">
      <section class="animation-source-step" aria-labelledby="animation-source-title">
        <div class="animation-step-heading">
          <span>01</span>
          <div>
            <strong id="animation-source-title">{{ $t("animation.sourceTitle") }}</strong>
            <small>{{ $t("animation.sourceHint") }}</small>
          </div>
        </div>
        <DropTarget
          clearable
          :accepts="['Image']"
          :artifact="source"
          :label="source ? source.title || $t('animation.sourceReady') : $t('animation.sourceDrop')"
          :reason="$t('animation.sourceReason')"
          @artifact="emit('artifact', $event)"
          @files="emit('files', $event)"
          @clear="emit('clear')"
        />
        <div v-if="source" class="animation-source-confirm checker">
          <ArtifactVisual :artifact="source" :draggable="false" />
          <span><b>{{ $t("animation.sourceReady") }}</b><small>{{ $t("animation.sourceReadyHint") }}</small></span>
        </div>
      </section>

      <section class="animation-task-step" aria-labelledby="animation-task-title">
        <div class="animation-step-heading">
          <span>02</span>
          <div>
            <strong id="animation-task-title">{{ $t("animation.taskTitle") }}</strong>
            <small>{{ $t("animation.taskHint") }}</small>
          </div>
        </div>
        <div class="animation-task-grid" role="radiogroup" :aria-label="$t('animation.taskTitle')">
          <button
            v-for="item in tasks"
            :key="item.id"
            type="button"
            class="animation-task-card"
            :class="[{ active: task === item.id }, `motion-${item.accent}`]"
            role="radio"
            :aria-checked="task === item.id"
            @click="emit('selectTask', item.id)"
          >
            <span class="animation-task-icon"><component :is="item.icon" :size="26" weight="duotone" /></span>
            <span><b>{{ $t(`animation.tasks.${item.id}.name`) }}</b><small>{{ $t(`animation.tasks.${item.id}.description`) }}</small></span>
            <i aria-hidden="true"></i>
          </button>
        </div>
      </section>
    </div>

    <footer class="animation-run-bar">
      <div>
        <span class="eyebrow">{{ task === "views" ? "IMAGE.VIEWS" : "ANIMATION.GENERATE" }}</span>
        <strong>{{ $t(`animation.tasks.${task}.name`) }}</strong>
        <small v-if="disabledReason">{{ $t(disabledReason) }}</small>
        <small v-else>{{ $t("animation.readyHint") }}</small>
      </div>
      <button class="draw-button" type="button" :disabled="!canRun" @click="emit('run')">
        <CircleNotch v-if="running" class="spin" :size="20" />
        <Sparkle v-else :size="20" weight="fill" />
        {{ $t(runLabel, { action: $t(`animation.tasks.${task}.name`) }) }}
        <ArrowRight :size="18" />
      </button>
    </footer>
  </section>
</template>
