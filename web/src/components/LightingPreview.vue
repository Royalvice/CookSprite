<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { PhImage as ImageIcon, PhLightbulb as Lightbulb, PhMoonStars as MoonStars, PhSun as Sun } from "@phosphor-icons/vue";
import * as THREE from "three";
import { HDRLoader } from "three/examples/jsm/loaders/HDRLoader.js";
import type { ArtifactRef } from "../api/generated";
import ArtifactVisual from "./ArtifactVisual.vue";

const props = defineProps<{ diffuse?: ArtifactRef; normal?: ArtifactRef }>();
const mount = ref<HTMLElement | null>(null);
const mode = ref<"lit" | "diffuse" | "normal">("lit");
const position = ref(90);
const height = ref(2.2);
const intensity = ref(3.2);
const environmentIntensity = ref(0.7);
const color = ref("#ffe7b0");
const normalStrength = ref(1);
const flipY = ref(false);
const showEnvironment = ref(false);
const hdri = ref("neutral_studio");
const loading = ref(false);
const dragging = ref(false);
const presets = [
  { id: "neutral_studio", label: "lighting.neutral", icon: Lightbulb },
  { id: "bright_day", label: "lighting.day", icon: Sun },
  { id: "moon_night", label: "lighting.night", icon: MoonStars },
];
const lightTheta = computed(() => Math.PI * (1 - position.value / 180));
const lightX = computed(() => 8 + (position.value / 180) * 84);
const lightY = computed(() => 82 - Math.sin(lightTheta.value) * 66);

let renderer: THREE.WebGLRenderer | undefined;
let scene: THREE.Scene | undefined;
let camera: THREE.OrthographicCamera | undefined;
let material: THREE.MeshStandardMaterial | undefined;
let plane: THREE.Mesh | undefined;
let point: THREE.PointLight | undefined;
let gizmo: THREE.Mesh | undefined;
let environment: THREE.Texture | null = null;
let resizeObserver: ResizeObserver | undefined;
let textureGeneration = 0;

function render() { if (renderer && scene && camera) renderer.render(scene, camera); }
function setup() {
  if (!mount.value || renderer) return;
  scene = new THREE.Scene();
  scene.background = new THREE.Color("#171A22");
  camera = new THREE.OrthographicCamera(-1.35, 1.35, 1.35, -1.35, 0.1, 20);
  camera.position.z = 4;
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.domElement.dataset.lightingCanvas = "true";
  mount.value.appendChild(renderer.domElement);
  material = new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, alphaTest: 0.02, roughness: 0.62, metalness: 0.02 });
  plane = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
  scene.add(plane);
  point = new THREE.PointLight(color.value, intensity.value, 12, 1.4);
  point.position.z = 3;
  gizmo = new THREE.Mesh(new THREE.CircleGeometry(0.09, 24), new THREE.MeshBasicMaterial({ color: color.value, toneMapped: false }));
  point.add(gizmo);
  scene.add(point);
  scene.add(new THREE.AmbientLight(0xffffff, 0.22));
  resize();
  void refreshTextures();
  refreshEnvironment();
  updateLight();
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(mount.value);
}
function resize() {
  if (!mount.value || !renderer) return;
  renderer.setSize(Math.max(1, mount.value.clientWidth), Math.max(1, mount.value.clientHeight), false);
  render();
}
function loadTexture(url: string | undefined, colorSpace?: THREE.ColorSpace) {
  if (!url) return Promise.resolve<THREE.Texture | null>(null);
  return new Promise<THREE.Texture>((resolve, reject) => new THREE.TextureLoader().load(url, (texture) => {
    texture.magFilter = THREE.NearestFilter;
    texture.minFilter = THREE.NearestFilter;
    texture.generateMipmaps = false;
    if (colorSpace) texture.colorSpace = colorSpace;
    resolve(texture);
  }, undefined, reject));
}
async function refreshTextures() {
  if (!material) return;
  const generation = ++textureGeneration;
  const [diffuse, normal] = await Promise.all([
    loadTexture(props.diffuse?.url, THREE.SRGBColorSpace),
    loadTexture(props.normal?.url),
  ]).catch(() => [null, null]);
  if (generation !== textureGeneration || !material) { diffuse?.dispose(); normal?.dispose(); return; }
  material.map?.dispose();
  material.normalMap?.dispose();
  material.map = diffuse;
  material.normalMap = normal;
  material.normalScale.set(normalStrength.value, flipY.value ? -normalStrength.value : normalStrength.value);
  material.needsUpdate = true;
  render();
}
function refreshEnvironment() {
  if (!renderer || !scene) return;
  loading.value = true;
  new HDRLoader().load(`/hdri/${hdri.value}.hdr`, (texture) => {
    if (!renderer || !scene) { texture.dispose(); return; }
    environment?.dispose();
    const pmrem = new THREE.PMREMGenerator(renderer);
    environment = pmrem.fromEquirectangular(texture).texture;
    scene.environment = environment;
    scene.background = showEnvironment.value ? environment : new THREE.Color("#171A22");
    texture.dispose(); pmrem.dispose(); loading.value = false; render();
  }, undefined, () => { loading.value = false; if (scene) scene.background = new THREE.Color("#171A22"); render(); });
}
function updateLight() {
  if (!point || !material || !scene) return;
  const worldX = (lightX.value / 50 - 1) * 1.35;
  const worldY = (1 - lightY.value / 50) * 1.35;
  point.position.set(worldX, worldY, 0.35 + height.value * 0.65);
  point.color.set(color.value);
  point.intensity = intensity.value;
  const gizmoMaterial = gizmo?.material as THREE.MeshBasicMaterial | undefined;
  gizmoMaterial?.color.set(color.value);
  scene.environmentIntensity = environmentIntensity.value;
  material.normalScale.set(normalStrength.value, flipY.value ? -normalStrength.value : normalStrength.value);
  scene.background = showEnvironment.value && environment ? environment : new THREE.Color("#171A22");
  render();
}
function pointerPosition(event: PointerEvent) {
  if (!mount.value) return;
  const rect = mount.value.getBoundingClientRect();
  position.value = Math.max(0, Math.min(180, ((event.clientX - rect.left) / Math.max(1, rect.width)) * 180));
}
function pointerDown(event: PointerEvent) {
  if (mode.value !== "lit") return;
  dragging.value = true;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  pointerPosition(event);
}
function pointerMove(event: PointerEvent) { if (dragging.value) pointerPosition(event); }
function pointerUp(event: PointerEvent) { dragging.value = false; (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId); }

watch(() => [props.diffuse?.id, props.normal?.id], refreshTextures);
watch(hdri, refreshEnvironment);
watch([position, height, intensity, environmentIntensity, color, normalStrength, flipY, showEnvironment], updateLight);
onMounted(setup);
onBeforeUnmount(() => {
  textureGeneration += 1;
  resizeObserver?.disconnect();
  if (scene && point) scene.remove(point);
  if (scene && plane) scene.remove(plane);
  material?.map?.dispose(); material?.normalMap?.dispose(); material?.dispose();
  (plane?.geometry as THREE.BufferGeometry | undefined)?.dispose();
  (gizmo?.geometry as THREE.BufferGeometry | undefined)?.dispose();
  (gizmo?.material as THREE.Material | undefined)?.dispose();
  environment?.dispose(); renderer?.dispose(); renderer?.domElement.remove();
  renderer = undefined; scene = undefined; camera = undefined; material = undefined;
  plane = undefined; point = undefined; gizmo = undefined;
});
</script>

<template>
  <section class="lighting-preview">
    <div class="preview-mode-tabs" role="tablist">
      <button :class="{ active: mode === 'lit' }" role="tab" :aria-selected="mode === 'lit'" @click="mode = 'lit'"><Lightbulb :size="16" />{{ $t("lighting.result") }}</button>
      <button :class="{ active: mode === 'diffuse' }" role="tab" :aria-selected="mode === 'diffuse'" @click="mode = 'diffuse'"><ImageIcon :size="16" />{{ $t("lighting.diffuse") }}</button>
      <button :class="{ active: mode === 'normal' }" role="tab" :aria-selected="mode === 'normal'" :disabled="!normal" @click="mode = 'normal'"><ImageIcon :size="16" />{{ $t("lighting.normalMap") }}</button>
    </div>
    <div class="lighting-stage" :class="{ dragging }" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="pointerUp">
      <div v-show="mode === 'lit'" ref="mount" class="three-mount" role="img" :aria-label="$t('lighting.preview')"></div>
      <div v-if="mode !== 'lit'" class="map-preview checker"><ArtifactVisual v-if="mode === 'diffuse' && diffuse" :artifact="diffuse" /><ArtifactVisual v-else-if="mode === 'normal' && normal" :artifact="normal" /><span v-else>{{ $t("common.empty") }}</span></div>
      <span class="render-label">{{ mode === "lit" ? "THREE.JS / REALTIME" : mode.toUpperCase() }}</span>
      <span v-if="loading && mode === 'lit'" class="render-loading">{{ $t("lighting.loading") }}</span>
      <i v-if="mode === 'lit'" class="screen-light-gizmo" :style="{ left: `${lightX}%`, top: `${lightY}%`, background: color, color }" :aria-label="$t('lighting.light')"></i>
    </div>
    <div class="hdri-strip" role="radiogroup" :aria-label="$t('lighting.environment')"><button v-for="preset in presets" :key="preset.id" :class="{ active: hdri === preset.id }" role="radio" :aria-checked="hdri === preset.id" @click="hdri = preset.id"><component :is="preset.icon" :size="17" />{{ $t(preset.label) }}</button></div>
    <div class="light-arc-control"><div class="arc"><i class="arc-dot" :style="{ left: `${lightX}%`, top: `${lightY}%`, background: color }"></i></div><input v-model.number="position" type="range" min="0" max="180" step="1" :aria-label="$t('lighting.move')" /><div class="light-values"><span>{{ $t("lighting.left") }}</span><strong>{{ $t("lighting.light") }} {{ Math.round(position) }}°</strong><span>{{ $t("lighting.right") }}</span></div></div>
    <div class="lighting-controls">
      <label>{{ $t("lighting.normal") }} <input v-model.number="normalStrength" type="range" min="0" max="2" step="0.05" /><b>{{ normalStrength.toFixed(2) }}</b></label>
      <label>{{ $t("lighting.height") }} <input v-model.number="height" type="range" min="0.3" max="4" step="0.1" /><b>{{ height.toFixed(1) }}</b></label>
      <label>{{ $t("lighting.point") }} <input v-model.number="intensity" type="range" min="0" max="8" step="0.1" /><b>{{ intensity.toFixed(1) }}</b></label>
      <label>IBL <input v-model.number="environmentIntensity" type="range" min="0" max="2" step="0.05" /><b>{{ environmentIntensity.toFixed(2) }}</b></label>
      <label class="color-field">{{ $t("lighting.color") }} <input v-model="color" type="color" /></label>
      <button class="toggle-icon" :class="{ active: flipY }" :aria-pressed="flipY" @click="flipY = !flipY">{{ $t("lighting.flip") }}</button>
      <button class="toggle-icon" :class="{ active: showEnvironment }" :aria-pressed="showEnvironment" @click="showEnvironment = !showEnvironment">{{ $t("lighting.show") }}</button>
    </div>
  </section>
</template>
