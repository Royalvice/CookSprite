<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as THREE from "three";
import { HDRLoader } from "three/examples/jsm/loaders/HDRLoader.js";
import type { ArtifactRef } from "../api/generated";
import ArtifactVisual from "./ArtifactVisual.vue";

const props = defineProps<{ diffuse?: ArtifactRef; normal?: ArtifactRef }>();
const mount = ref<HTMLElement | null>(null);
const mode = ref<"lit" | "diffuse" | "normal">("lit");
const lightHorizontal = ref(-0.5);
const lightVertical = ref(0.62);
const height = ref(0.3);
const intensity = ref(8);
const lightRange = ref(4);
const environmentIntensity = ref(0.45);
const color = ref("#ffe7b0");
const normalStrength = ref(1);
const flipY = ref(false);
const showEnvironment = ref(false);
const hdri = ref("neutral_studio");
const loading = ref(false);
const loadError = ref("");
const dragging = ref(false);
const presets = [
  { id: "neutral_studio", label: "lighting.neutral" },
  { id: "bright_day", label: "lighting.day" },
  { id: "moon_night", label: "lighting.night" },
];
const stageLabel = computed(() => `${lightHorizontal.value.toFixed(2)}, ${lightVertical.value.toFixed(2)}`);

let renderer: THREE.WebGLRenderer | undefined;
let scene: THREE.Scene | undefined;
let camera: THREE.OrthographicCamera | undefined;
let material: THREE.MeshStandardMaterial | undefined;
let plane: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshStandardMaterial> | undefined;
let shadowMaterial: THREE.MeshDistanceMaterial | undefined;
let backdropMaterial: THREE.MeshStandardMaterial | undefined;
let backdrop: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshStandardMaterial> | undefined;
let point: THREE.PointLight | undefined;
let environment: THREE.Texture | null = null;
let resizeObserver: ResizeObserver | undefined;
let textureGeneration = 0;
let environmentGeneration = 0;
let textureAspect = 1;

function render() {
  if (mode.value === "lit" && renderer && scene && camera) renderer.render(scene, camera);
}

function fitPlane() {
  if (!plane || !camera) return;
  const viewWidth = camera.right - camera.left;
  const viewHeight = camera.top - camera.bottom;
  const viewAspect = viewWidth / viewHeight;
  let width: number;
  let planeHeight: number;
  if (textureAspect >= viewAspect) {
    width = viewWidth * 0.88;
    planeHeight = width / textureAspect;
  } else {
    planeHeight = viewHeight * 0.88;
    width = planeHeight * textureAspect;
  }
  plane.scale.set(width, planeHeight, 1);
}

function fitBackdrop() {
  if (!backdrop || !camera) return;
  const viewWidth = camera.right - camera.left;
  const viewHeight = camera.top - camera.bottom;
  backdrop.scale.set(viewWidth * 1.12, viewHeight * 1.12, 1);
}

function resize() {
  if (!mount.value || !renderer || !camera) return;
  const width = Math.max(1, mount.value.clientWidth);
  const heightValue = Math.max(1, mount.value.clientHeight);
  const aspect = width / heightValue;
  const half = 1.2;
  if (aspect >= 1) {
    camera.left = -half * aspect;
    camera.right = half * aspect;
    camera.top = half;
    camera.bottom = -half;
  } else {
    camera.left = -half;
    camera.right = half;
    camera.top = half / aspect;
    camera.bottom = -half / aspect;
  }
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, heightValue, false);
  fitPlane();
  fitBackdrop();
  updateLight();
}

function setup() {
  if (!mount.value || renderer) return;
  scene = new THREE.Scene();
  camera = new THREE.OrthographicCamera(-1.2, 1.2, 1.2, -1.2, 0.1, 20);
  camera.position.set(0, 0, 4);
  camera.lookAt(0, 0, 0);
  renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,
    powerPreference: "high-performance",
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.dataset.lightingCanvas = "true";
  mount.value.appendChild(renderer.domElement);
  backdropMaterial = new THREE.MeshStandardMaterial({
    color: 0x06070a,
    roughness: 1,
    metalness: 0,
  });
  backdrop = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), backdropMaterial);
  backdrop.position.z = -0.18;
  backdrop.receiveShadow = true;
  scene.add(backdrop);
  material = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    transparent: true,
    alphaTest: 0.02,
    roughness: 0.62,
    metalness: 0.02,
    normalMapType: THREE.TangentSpaceNormalMap,
  });
  plane = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), material);
  shadowMaterial = new THREE.MeshDistanceMaterial({ alphaTest: 0.02 });
  plane.customDistanceMaterial = shadowMaterial;
  plane.castShadow = true;
  plane.renderOrder = 1;
  scene.add(plane);
  point = new THREE.PointLight(color.value, intensity.value, lightRange.value, 2);
  point.castShadow = true;
  point.shadow.mapSize.set(1024, 1024);
  point.shadow.bias = -0.0005;
  point.shadow.normalBias = 0.01;
  point.shadow.radius = 2;
  point.shadow.camera.near = 0.05;
  point.shadow.camera.far = 6;
  scene.add(point);
  scene.add(new THREE.AmbientLight(0xffffff, 0.04));
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(mount.value);
  resize();
  void refreshTextures();
  refreshEnvironment();
}

function loadTexture(url: string | undefined, colorSpace?: THREE.ColorSpace) {
  if (!url) return Promise.resolve<THREE.Texture | null>(null);
  return new Promise<THREE.Texture>((resolve, reject) => {
    new THREE.TextureLoader().load(
      url,
      (texture) => {
        texture.magFilter = THREE.NearestFilter;
        texture.minFilter = THREE.NearestFilter;
        texture.generateMipmaps = false;
        if (colorSpace) texture.colorSpace = colorSpace;
        resolve(texture);
      },
      undefined,
      reject,
    );
  });
}

async function refreshTextures() {
  if (!material || !plane) return;
  const generation = ++textureGeneration;
  loadError.value = "";
  try {
    const [diffuse, normal] = await Promise.all([
      loadTexture(props.diffuse?.url, THREE.SRGBColorSpace),
      loadTexture(props.normal?.url),
    ]);
    if (generation !== textureGeneration || !material || !plane) {
      diffuse?.dispose();
      normal?.dispose();
      return;
    }
    material.map?.dispose();
    material.normalMap?.dispose();
    material.map = diffuse;
    material.normalMap = normal;
    if (shadowMaterial) {
      shadowMaterial.map = diffuse;
      shadowMaterial.needsUpdate = true;
    }
    const image = diffuse?.image as { naturalWidth?: number; naturalHeight?: number; width?: number; height?: number } | undefined;
    const width = Number(image?.naturalWidth || image?.width || 1);
    const heightValue = Number(image?.naturalHeight || image?.height || 1);
    textureAspect = width > 0 && heightValue > 0 ? width / heightValue : 1;
    plane.visible = Boolean(diffuse);
    material.normalScale.set(normalStrength.value, flipY.value ? -normalStrength.value : normalStrength.value);
    material.needsUpdate = true;
    fitPlane();
    render();
  } catch {
    if (generation === textureGeneration) {
      loadError.value = "lighting.loadError";
      if (plane) plane.visible = false;
      render();
    }
  }
}

function refreshEnvironment() {
  if (!renderer || !scene) return;
  const generation = ++environmentGeneration;
  loading.value = true;
  loadError.value = "";
  new HDRLoader().load(
    `/hdri/${hdri.value}.hdr`,
    (texture) => {
      if (generation !== environmentGeneration || !renderer || !scene) {
        texture.dispose();
        return;
      }
      const pmrem = new THREE.PMREMGenerator(renderer);
      const nextEnvironment = pmrem.fromEquirectangular(texture).texture;
      texture.dispose();
      pmrem.dispose();
      environment?.dispose();
      environment = nextEnvironment;
      scene.environment = environment;
      scene.background = showEnvironment.value ? environment : null;
      loading.value = false;
      render();
    },
    undefined,
    () => {
      if (generation !== environmentGeneration) return;
      loading.value = false;
      loadError.value = "lighting.environmentError";
      if (scene) {
        scene.environment = null;
        scene.background = null;
      }
      render();
    },
  );
}

function updateLight() {
  if (!point || !material || !scene || !camera) return;
  const worldX = lightHorizontal.value * (camera.right - camera.left) * 0.42;
  const worldY = lightVertical.value * (camera.top - camera.bottom) * 0.42;
  point.position.set(worldX, worldY, height.value);
  point.color.set(color.value);
  point.intensity = intensity.value;
  point.distance = lightRange.value;
  point.decay = 2;
  scene.environmentIntensity = environmentIntensity.value;
  material.normalScale.set(normalStrength.value, flipY.value ? -normalStrength.value : normalStrength.value);
  scene.background = showEnvironment.value && environment ? environment : null;
  render();
}

function pointerPosition(event: PointerEvent) {
  if (!mount.value) return;
  const rect = mount.value.getBoundingClientRect();
  lightHorizontal.value = Math.max(-1, Math.min(1, ((event.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1));
  lightVertical.value = Math.max(-1, Math.min(1, 1 - ((event.clientY - rect.top) / Math.max(1, rect.height)) * 2));
}

function pointerDown(event: PointerEvent) {
  if (mode.value !== "lit") return;
  dragging.value = true;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  pointerPosition(event);
}

function pointerMove(event: PointerEvent) {
  if (dragging.value) pointerPosition(event);
}

function pointerUp(event: PointerEvent) {
  dragging.value = false;
  (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
}

function keyboardMove(event: KeyboardEvent) {
  if (mode.value !== "lit") return;
  const step = event.shiftKey ? 0.2 : 0.05;
  if (event.key === "ArrowLeft") lightHorizontal.value = Math.max(-1, lightHorizontal.value - step);
  else if (event.key === "ArrowRight") lightHorizontal.value = Math.min(1, lightHorizontal.value + step);
  else if (event.key === "ArrowDown") lightVertical.value = Math.max(-1, lightVertical.value - step);
  else if (event.key === "ArrowUp") lightVertical.value = Math.min(1, lightVertical.value + step);
  else return;
  event.preventDefault();
}

watch(() => [props.diffuse?.id, props.normal?.id], refreshTextures);
watch(hdri, refreshEnvironment);
watch(mode, render);
watch(
  [lightHorizontal, lightVertical, height, intensity, lightRange, environmentIntensity, color, normalStrength, flipY, showEnvironment],
  updateLight,
);
onMounted(setup);
onBeforeUnmount(() => {
  textureGeneration += 1;
  environmentGeneration += 1;
  resizeObserver?.disconnect();
  material?.map?.dispose();
  material?.normalMap?.dispose();
  material?.dispose();
  shadowMaterial?.dispose();
  plane?.geometry.dispose();
  backdropMaterial?.dispose();
  backdrop?.geometry.dispose();
  environment?.dispose();
  renderer?.dispose();
  renderer?.domElement.remove();
  renderer = undefined;
  scene = undefined;
  camera = undefined;
  material = undefined;
  plane = undefined;
  shadowMaterial = undefined;
  backdropMaterial = undefined;
  backdrop = undefined;
  point = undefined;
  environment = null;
});
</script>

<template>
  <section class="lighting-preview">
    <div class="preview-mode-tabs" role="tablist" :aria-label="$t('lighting.preview')">
      <button id="lighting-tab-lit" :class="{ active: mode === 'lit' }" role="tab" :aria-selected="mode === 'lit'" aria-controls="lighting-panel" @click="mode = 'lit'">{{ $t("lighting.result") }}</button>
      <button id="lighting-tab-diffuse" :class="{ active: mode === 'diffuse' }" role="tab" :aria-selected="mode === 'diffuse'" aria-controls="lighting-panel" @click="mode = 'diffuse'">{{ $t("lighting.diffuse") }}</button>
      <button id="lighting-tab-normal" :class="{ active: mode === 'normal' }" role="tab" :aria-selected="mode === 'normal'" aria-controls="lighting-panel" :disabled="!normal" @click="mode = 'normal'">{{ $t("lighting.normalMap") }}</button>
    </div>
    <div
      id="lighting-panel"
      class="lighting-stage checker"
      :class="{ dragging, interactive: mode === 'lit' }"
      role="tabpanel"
      :aria-labelledby="`lighting-tab-${mode}`"
      :aria-label="$t('lighting.pointerHint')"
      :data-light-x="lightHorizontal.toFixed(3)"
      :data-light-y="lightVertical.toFixed(3)"
      tabindex="0"
      @keydown="keyboardMove"
      @pointerdown="pointerDown"
      @pointermove="pointerMove"
      @pointerup="pointerUp"
      @pointercancel="pointerUp"
    >
      <div v-show="mode === 'lit'" ref="mount" class="three-mount" :aria-label="$t('lighting.preview')"></div>
      <div v-if="mode !== 'lit'" class="map-preview">
        <ArtifactVisual v-if="mode === 'diffuse' && diffuse" :artifact="diffuse" :draggable="false" :alt="$t('lighting.diffuse')" />
        <ArtifactVisual v-else-if="mode === 'normal' && normal" :artifact="normal" :draggable="false" :alt="$t('lighting.normalMap')" />
        <span v-else>{{ $t("common.empty") }}</span>
      </div>
      <span v-if="loading && mode === 'lit'" class="render-status" role="status">{{ $t("lighting.loading") }}</span>
      <span v-else-if="loadError" class="render-status error" role="alert">{{ $t(loadError) }}</span>
    </div>
    <p class="lighting-hint">{{ $t("lighting.pointerHint") }} · {{ stageLabel }}</p>
    <div class="hdri-strip" role="radiogroup" :aria-label="$t('lighting.environment')">
      <button v-for="preset in presets" :key="preset.id" :class="{ active: hdri === preset.id }" role="radio" :aria-checked="hdri === preset.id" @click="hdri = preset.id">{{ $t(preset.label) }}</button>
    </div>
    <div class="lighting-controls">
      <label>{{ $t("lighting.horizontal") }} <input v-model.number="lightHorizontal" type="range" min="-1" max="1" step="0.01" /><b>{{ lightHorizontal.toFixed(2) }}</b></label>
      <label>{{ $t("lighting.vertical") }} <input v-model.number="lightVertical" type="range" min="-1" max="1" step="0.01" /><b>{{ lightVertical.toFixed(2) }}</b></label>
      <label>{{ $t("lighting.height") }} <input v-model.number="height" type="range" min="0.3" max="4" step="0.1" /><b>{{ height.toFixed(1) }}</b></label>
      <label>{{ $t("lighting.point") }} <input v-model.number="intensity" type="range" min="0" max="8" step="0.1" /><b>{{ intensity.toFixed(1) }}</b></label>
      <label>{{ $t("lighting.range") }} <input v-model.number="lightRange" type="range" min="0.6" max="4" step="0.1" /><b>{{ lightRange.toFixed(1) }}</b></label>
      <label>{{ $t("lighting.normal") }} <input v-model.number="normalStrength" type="range" min="0" max="2" step="0.05" /><b>{{ normalStrength.toFixed(2) }}</b></label>
      <label>IBL <input v-model.number="environmentIntensity" type="range" min="0" max="2" step="0.05" /><b>{{ environmentIntensity.toFixed(2) }}</b></label>
      <label class="color-field">{{ $t("lighting.color") }} <input v-model="color" type="color" /></label>
      <button class="toggle-icon" :class="{ active: flipY }" :aria-pressed="flipY" @click="flipY = !flipY">{{ $t("lighting.flip") }}</button>
      <button class="toggle-icon" :class="{ active: showEnvironment }" :aria-pressed="showEnvironment" @click="showEnvironment = !showEnvironment">{{ $t("lighting.show") }}</button>
    </div>
  </section>
</template>
