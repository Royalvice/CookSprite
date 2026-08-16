<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { PhArchiveTray as ArchiveTray, PhArrowRight as ArrowRight, PhFilmStrip as FilmStrip, PhFunnel as Funnel, PhMagnifyingGlass as MagnifyingGlass, PhSparkle as Sparkle, PhStar as Star, PhTrash as Trash, PhUploadSimple as UploadSimple } from "@phosphor-icons/vue";
import ArtifactCard from "../components/ArtifactCard.vue";
import ArtifactVisual from "../components/ArtifactVisual.vue";
import DropTarget from "../components/DropTarget.vue";
import { api, inferArtifactKind, type ArtifactKind, type ArtifactRef } from "../api/generated";
import { useStudioStore } from "../stores/studio";

const store = useStudioStore();
const router = useRouter();
const search = ref("");
const kind = ref("all");
const showTrash = ref(false);
const selected = ref<ArtifactRef | null>(null);
const preview = ref<ArtifactRef | null>(null);
const inspected = computed(() => preview.value || selected.value);
const filtered = computed(() => store.allArtifacts.filter((artifact) => {
  const matchesSearch = `${artifact.title} ${artifact.kind} ${JSON.stringify(artifact.meta)}`.toLowerCase().includes(search.value.toLowerCase());
  return matchesSearch && (kind.value === "all" || artifact.kind === kind.value) && artifact.trashed === showTrash.value;
}));
const usage = computed(() => store.allArtifacts.reduce((sum, item) => sum + item.size, 0));

onMounted(async () => { store.allArtifacts = await api.artifacts(`trashed=${showTrash.value}`); });
async function importFiles(files: File[]) {
  for (const file of files) await store.upload(file, inferArtifactKind(file) || "Image");
}
async function trash(artifact: ArtifactRef) {
  const changed = showTrash.value ? await api.restore(artifact.id) : await api.trash(artifact.id);
  store.allArtifacts = store.allArtifacts.filter((item) => item.id !== changed.id);
  selected.value = null;
}
async function favorite(artifact: ArtifactRef) {
  const changed = await api.patchArtifact(artifact.id, { favorite: !artifact.favorite });
  const index = store.allArtifacts.findIndex((item) => item.id === changed.id);
  if (index >= 0) store.allArtifacts[index] = changed;
  selected.value = changed;
}
function useInStudio(artifact: ArtifactRef, intent: "reference" | "animate" | "normal" | "sequence") {
  const path = store.currentProject ? `/studio/${store.currentProject.id}` : "/studio";
  void router.push({ path, query: { artifact: artifact.id, intent } });
}
</script>

<template>
  <div class="library-view page-wrap">
    <section class="page-heading split-heading">
      <div><span class="eyebrow">{{ $t("library.eyebrow") }}</span><h1>{{ $t("library.title") }}</h1><p>{{ $t("library.retention", { size: (usage / 1024 / 1024).toFixed(1) }) }}</p></div>
      <DropTarget class="library-import" :accepts="['Image','SpriteSheet','Video']" :label="$t('common.import')" @files="importFiles" />
    </section>
    <div class="library-toolbar panel">
      <label class="search-field"><MagnifyingGlass :size="18" /><span class="visually-hidden">{{ $t("library.search") }}</span><input v-model="search" :placeholder="$t('library.search')" /></label>
      <label class="select-field"><Funnel :size="17" /><select v-model="kind"><option value="all">{{ $t("library.allTypes") }}</option><option v-for="item in ['Image','SpriteSheet','FrameSeq','Video','NormalMap','CookSpritePack']" :key="item">{{ item }}</option></select></label>
      <button class="text-button" :class="{ active: showTrash }" @click="showTrash = !showTrash; api.artifacts(`trashed=${showTrash}`).then((items) => store.allArtifacts = items)"><Trash :size="17" />{{ $t("library.trash") }}</button>
    </div>
    <div class="library-layout">
      <section class="library-grid" aria-live="polite">
        <ArtifactCard v-for="artifact in filtered" :key="artifact.id" :artifact="artifact" :selected="selected?.id === artifact.id" @select="selected = $event" @preview="preview = $event" />
        <div v-if="!filtered.length" class="panel library-empty"><ArchiveTray :size="42" /><strong>{{ $t("common.empty") }}</strong><span>{{ $t("library.importHint") }}</span></div>
      </section>
      <aside class="inspector panel" :class="{ empty: !inspected }">
        <template v-if="inspected">
          <span class="eyebrow">ARTIFACT</span><h2>{{ inspected.title || inspected.id }}</h2>
          <div class="inspector-preview checker"><ArtifactVisual :artifact="inspected" animated /></div>
          <dl><dt>ID</dt><dd>{{ inspected.id }}</dd><dt>TYPE</dt><dd>{{ inspected.kind }}</dd><dt>SIZE</dt><dd>{{ (inspected.size / 1024).toFixed(1) }} KB</dd><dt>SHA-256</dt><dd class="hash">{{ inspected.sha256 }}</dd></dl>
          <div v-if="selected && selected.id === inspected.id && !showTrash" class="library-workflow-actions"><strong>{{ $t('library.continueWith') }}</strong><button v-if="selected.kind === 'Image'" class="arcade-button" type="button" @click="useInStudio(selected, 'reference')">{{ $t('studio.useReference') }}<ArrowRight :size="15" /></button><button v-if="selected.kind === 'Image'" class="arcade-button primary" type="button" @click="useInStudio(selected, 'animate')"><FilmStrip :size="16" />{{ $t('studio.makeAnimation') }}</button><button v-if="selected.kind === 'FrameSeq'" class="arcade-button primary" type="button" @click="useInStudio(selected, 'sequence')"><FilmStrip :size="16" />{{ $t('library.openSequence') }}</button><button v-if="['Image','FrameSeq','SpriteSheet'].includes(selected.kind)" class="arcade-button" type="button" @click="useInStudio(selected, 'normal')"><Sparkle :size="16" />{{ $t('studio.makeNormal') }}</button></div>
          <div v-if="selected && selected.id === inspected.id" class="stack-actions"><button class="arcade-button" :class="{ active: selected.favorite }" :aria-pressed="selected.favorite" @click="favorite(selected)"><Star :size="17" :weight="selected.favorite ? 'fill' : 'regular'" />{{ $t("library.favorite") }}</button><button class="arcade-button danger" @click="trash(selected)"><Trash :size="17" />{{ $t(showTrash ? "library.restore" : "library.moveTrash") }}</button></div>
        </template>
        <template v-else><UploadSimple :size="36" /><p>{{ $t("library.inspect") }}</p></template>
      </aside>
    </div>
  </div>
</template>
