import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { frameRect } from "./SpritePreview";

interface LightPreviewProps {
  diffuseUrl: string;
  normalUrl?: string;
  frameCount: number;
  frameIndex: number;
  frameW?: number;
  frameH?: number;
}

const CANVAS = 320;

const VERT = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vPos;
  void main() {
    vUv = uv;
    vPos = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

// Tangent-space normal mapping for a flat billboard quad. The quad's local
// frame (tangent +X, bitangent +Y, normal +Z) matches tangent space, so the
// decoded normal is used directly. Point light with quadratic attenuation so
// the light's height (z) has a visible effect.
const FRAG = /* glsl */ `
  precision highp float;
  uniform sampler2D uDiffuse;
  uniform sampler2D uNormal;
  uniform float uHasNormal;
  uniform vec3 uLightPos;
  uniform vec3 uLightColor;
  uniform float uAmbient;
  uniform float uIntensity;
  varying vec2 vUv;
  varying vec3 vPos;
  void main() {
    vec4 tex = texture2D(uDiffuse, vUv);
    if (tex.a < 0.01) discard;
    vec3 albedo = tex.rgb;
    vec3 N = uHasNormal > 0.5
      ? normalize(texture2D(uNormal, vUv).xyz * 2.0 - 1.0)
      : vec3(0.0, 0.0, 1.0);
    vec3 toLight = uLightPos - vPos;
    float dist = length(toLight);
    vec3 L = toLight / max(dist, 0.0001);
    float diff = max(dot(N, L), 0.0);
    float atten = 1.0 / (1.0 + 0.35 * dist * dist);
    vec3 lit = albedo * (uAmbient + uLightColor * diff * uIntensity * atten);
    gl_FragColor = vec4(lit, tex.a);
  }
`;

interface GL {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.OrthographicCamera;
  material: THREE.ShaderMaterial;
  diffuseTex: THREE.CanvasTexture;
  normalTex: THREE.CanvasTexture;
  diffuseCanvas: HTMLCanvasElement;
  normalCanvas: HTMLCanvasElement;
  mesh: THREE.Mesh;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Failed to load ${url}`));
    img.src = url;
  });
}

// Draw one frame region of a sheet (or the whole image) into a square canvas,
// letterboxing to preserve aspect. Returns the drawn aspect (w/h).
function drawFrame(
  canvas: HTMLCanvasElement,
  img: HTMLImageElement,
  frameCount: number,
  frameIndex: number,
  frameW?: number,
  frameH?: number,
  fill?: string
): number {
  const ctx = canvas.getContext("2d");
  if (!ctx) return 1;
  const useFrame = frameCount > 1 && !!frameW && !!frameH;
  const sx = useFrame
    ? frameRect({ w: img.naturalWidth, h: img.naturalHeight }, frameW!, frameH!, frameIndex).x
    : 0;
  const sy = useFrame
    ? frameRect({ w: img.naturalWidth, h: img.naturalHeight }, frameW!, frameH!, frameIndex).y
    : 0;
  const sw = useFrame ? frameW! : img.naturalWidth;
  const sh = useFrame ? frameH! : img.naturalHeight;

  canvas.width = sw;
  canvas.height = sh;
  ctx.clearRect(0, 0, sw, sh);
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fillRect(0, 0, sw, sh);
  }
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
  return sw / sh;
}

export function LightPreview({
  diffuseUrl,
  normalUrl,
  frameCount,
  frameIndex,
  frameW,
  frameH,
}: LightPreviewProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const glRef = useRef<GL | null>(null);
  const lightRef = useRef<THREE.Vector3>(new THREE.Vector3(0, 0, 1.0));
  const [ambient, setAmbient] = useState(0.15);
  const [color, setColor] = useState("#fff2d0");
  const [lightZ, setLightZ] = useState(1.0);
  const [dot, setDot] = useState<{ x: number; y: number }>({ x: 0.5, y: 0.5 });

  // One-time three.js setup.
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(CANVAS, CANVAS, false);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
    camera.position.set(0, 0, 3);
    camera.lookAt(0, 0, 0);

    const diffuseCanvas = document.createElement("canvas");
    const normalCanvas = document.createElement("canvas");
    const diffuseTex = new THREE.CanvasTexture(diffuseCanvas);
    const normalTex = new THREE.CanvasTexture(normalCanvas);
    for (const t of [diffuseTex, normalTex]) {
      t.magFilter = THREE.NearestFilter;
      t.minFilter = THREE.NearestFilter;
      t.generateMipmaps = false;
    }
    // The normal map encodes geometry, not color; keep it linear.
    diffuseTex.colorSpace = THREE.SRGBColorSpace;

    const material = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      transparent: true,
      uniforms: {
        uDiffuse: { value: diffuseTex },
        uNormal: { value: normalTex },
        uHasNormal: { value: 0 },
        uLightPos: { value: lightRef.current },
        uLightColor: { value: new THREE.Color(color) },
        uAmbient: { value: ambient },
        uIntensity: { value: 1.4 },
      },
    });

    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.6), material);
    scene.add(mesh);

    glRef.current = {
      renderer,
      scene,
      camera,
      material,
      diffuseTex,
      normalTex,
      diffuseCanvas,
      normalCanvas,
      mesh,
    };
    renderer.render(scene, camera);

    return () => {
      renderer.dispose();
      material.dispose();
      mesh.geometry.dispose();
      diffuseTex.dispose();
      normalTex.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      glRef.current = null;
    };
  }, []);

  const render = () => {
    const gl = glRef.current;
    if (gl) gl.renderer.render(gl.scene, gl.camera);
  };

  // Load / crop textures when the source or frame changes.
  useEffect(() => {
    let active = true;
    const gl = glRef.current;
    if (!gl) return;

    const run = async () => {
      const dImg = await loadImage(diffuseUrl);
      if (!active || !glRef.current) return;
      const aspect = drawFrame(gl.diffuseCanvas, dImg, frameCount, frameIndex, frameW, frameH);
      gl.diffuseTex.needsUpdate = true;

      // Fit the quad to the frame aspect within a 1.6 box.
      const box = 1.6;
      const w = aspect >= 1 ? box : box * aspect;
      const h = aspect >= 1 ? box / aspect : box;
      gl.mesh.geometry.dispose();
      gl.mesh.geometry = new THREE.PlaneGeometry(w, h);

      if (normalUrl) {
        try {
          const nImg = await loadImage(normalUrl);
          if (!active || !glRef.current) return;
          drawFrame(gl.normalCanvas, nImg, frameCount, frameIndex, frameW, frameH, "#8080ff");
          gl.normalTex.needsUpdate = true;
          (gl.material.uniforms.uHasNormal.value as number) = 1;
        } catch {
          (gl.material.uniforms.uHasNormal.value as number) = 0;
        }
      } else {
        (gl.material.uniforms.uHasNormal.value as number) = 0;
      }
      render();
    };

    void run();
    return () => {
      active = false;
    };
  }, [diffuseUrl, normalUrl, frameCount, frameIndex, frameW, frameH]);

  // Sync scalar/color controls into uniforms.
  useEffect(() => {
    const gl = glRef.current;
    if (!gl) return;
    (gl.material.uniforms.uAmbient.value as number) = ambient;
    (gl.material.uniforms.uLightColor.value as THREE.Color).set(color);
    lightRef.current.z = lightZ;
    render();
  }, [ambient, color, lightZ]);

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width; // 0..1
    const ny = (e.clientY - rect.top) / rect.height; // 0..1
    // Map to camera frustum coords (-1..1); flip Y for screen-to-world.
    lightRef.current.x = nx * 2 - 1;
    lightRef.current.y = -(ny * 2 - 1);
    setDot({ x: nx, y: ny });
    render();
  };

  const onWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    const next = Math.min(4, Math.max(0.1, lightZ + (e.deltaY > 0 ? -0.15 : 0.15)));
    setLightZ(Number(next.toFixed(2)));
  };

  return (
    <div className="preview">
      <div className="preview__head">
        <h3 className="preview__title">Light preview</h3>
        <span className="preview__hint">drag = move · scroll = height</span>
      </div>
      <div
        className="lightpreview__stage checker"
        style={{ width: CANVAS, height: CANVAS }}
        ref={mountRef}
        onPointerMove={onPointerMove}
        onWheel={onWheel}
      >
        <span
          className="lightpreview__dot"
          style={{ left: `${dot.x * 100}%`, top: `${dot.y * 100}%` }}
        />
      </div>
      <div className="lightpreview__controls">
        <label className="field">
          <span className="field__label">Ambient {ambient.toFixed(2)}</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={ambient}
            onChange={(e) => setAmbient(Number(e.target.value))}
          />
        </label>
        <label className="field field--inline">
          <span className="field__label">Light color</span>
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Height (z) {lightZ.toFixed(2)}</span>
          <input
            type="range"
            min={0.1}
            max={4}
            step={0.05}
            value={lightZ}
            onChange={(e) => setLightZ(Number(e.target.value))}
          />
        </label>
      </div>
    </div>
  );
}
