<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { DialogClose, DialogContent, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from "reka-ui";
import { PhArrowRight as ArrowRight, PhCheckCircle as CheckCircle, PhLightning as Lightning, PhPlay as Play, PhPlus as Plus, PhX as X } from "@phosphor-icons/vue";
import type { GalleryItem } from "../api/generated";
import { useStudioStore } from "../stores/studio";
import ArtifactVisual from "../components/ArtifactVisual.vue";

const store = useStudioStore();
const router = useRouter();
const focused = ref<GalleryItem | null>(null);

async function derive(item: GalleryItem) {
  await store.openProject(item.project.id);
  await router.push(`/studio/${item.project.id}`);
}
</script>

<template>
  <div class="gallery-view page-wrap">
    <section class="gallery-hero">
      <div>
        <span class="eyebrow">{{ $t("gallery.eyebrow") }}</span>
        <h1>{{ $t("gallery.title") }}</h1>
        <p>{{ $t("gallery.intro") }}</p>
      </div>
      <RouterLink class="arcade-button primary" to="/studio">
        <Plus :size="20" weight="bold" />{{ $t("common.create") }}<ArrowRight :size="18" />
      </RouterLink>
    </section>

    <section v-if="store.gallery.length" class="cabinet-grid" :aria-label="$t('gallery.aria')">
      <button v-for="item in store.gallery" :key="item.project.id" class="arcade-cabinet" :data-project-id="item.project.id" @click="focused = item">
        <span class="cabinet-marquee"><i></i>{{ item.project.name }}<i></i></span>
        <span class="cabinet-screen checker">
          <ArtifactVisual v-if="item.cover" :artifact="item.cover" />
          <span v-else class="cabinet-placeholder">CS</span>
          <span class="screen-play"><Play :size="20" weight="fill" />{{ $t("common.preview") }}</span>
        </span>
        <span class="cabinet-controls"><i></i><i></i><b>{{ $t("common.start") }}</b></span>
        <span class="cabinet-meta"><strong>{{ item.project.type.toUpperCase() }}</strong><small>{{ new Date(item.published_at).toLocaleDateString() }}</small></span>
      </button>
    </section>

    <section v-else class="gallery-empty">
      <div class="empty-cabinet" aria-hidden="true"><span>{{ $t("gallery.insert") }}</span></div>
      <div>
        <span class="eyebrow">{{ $t("gallery.noExhibits") }}</span>
        <h2>{{ $t("gallery.emptyTitle") }}</h2>
        <p>{{ $t("gallery.emptyText") }}</p>
        <RouterLink class="arcade-button" to="/studio"><Plus :size="18" />{{ $t("common.create") }}</RouterLink>
      </div>
    </section>

    <DialogRoot :open="Boolean(focused)" @update:open="!$event && (focused = null)">
      <DialogPortal>
        <DialogOverlay class="dialog-overlay" />
        <DialogContent v-if="focused" class="gallery-dialog" @open-auto-focus.prevent>
          <DialogClose class="icon-button dialog-close" :aria-label="$t('common.close')"><X :size="20" /></DialogClose>
          <div class="immersive-stage checker">
            <ArtifactVisual v-if="focused.cover" :artifact="focused.cover" />
            <div class="light-sweep"></div>
          </div>
          <div class="immersive-copy">
            <span class="eyebrow"><CheckCircle :size="15" weight="fill" />{{ $t("gallery.finished") }}</span>
            <DialogTitle>{{ focused.project.name }}</DialogTitle>
            <div class="token-row"><span>{{ focused.project.type }}</span><span>{{ $t("gallery.directions") }}</span><span><Lightning :size="13" />{{ $t("gallery.normalLight") }}</span></div>
            <button class="arcade-button primary" @click="derive(focused)">{{ $t("gallery.derive") }}<ArrowRight :size="18" /></button>
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </div>
</template>
