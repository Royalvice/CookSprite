import { useEffect, useState } from "react";

interface SpritePreviewProps {
  diffuseUrl: string;
  frameCount: number;
  frameIndex: number;
  frameW?: number;
  frameH?: number;
}

interface Natural {
  w: number;
  h: number;
}

// Compute the (col,row) of a frame given the sheet's natural size and frame size.
// Assumes a left-to-right, top-to-bottom grid layout.
export function frameRect(
  natural: Natural,
  frameW: number,
  frameH: number,
  index: number
): { x: number; y: number; w: number; h: number } {
  const cols = Math.max(1, Math.floor(natural.w / frameW));
  const col = index % cols;
  const row = Math.floor(index / cols);
  return { x: col * frameW, y: row * frameH, w: frameW, h: frameH };
}

export function SpritePreview({
  diffuseUrl,
  frameCount,
  frameIndex,
  frameW,
  frameH,
}: SpritePreviewProps) {
  const [pixelPerfect, setPixelPerfect] = useState(true);
  const [natural, setNatural] = useState<Natural | null>(null);

  useEffect(() => {
    let active = true;
    const img = new Image();
    img.onload = () => {
      if (active) setNatural({ w: img.naturalWidth, h: img.naturalHeight });
    };
    img.src = diffuseUrl;
    return () => {
      active = false;
    };
  }, [diffuseUrl]);

  const multiFrame = frameCount > 1 && !!frameW && !!frameH && !!natural;

  // Target on-screen frame size; integer-scale up for pixel art readability.
  const baseW = multiFrame ? frameW! : natural?.w ?? 128;
  const baseH = multiFrame ? frameH! : natural?.h ?? 128;
  const scale = pixelPerfect ? Math.max(1, Math.floor(256 / Math.max(baseW, baseH))) : 1;
  const dispW = baseW * scale;
  const dispH = baseH * scale;

  const rendering = pixelPerfect ? "pixelated" : "auto";

  let inner: React.CSSProperties;
  if (multiFrame && natural) {
    const rect = frameRect(natural, frameW!, frameH!, frameIndex);
    inner = {
      width: dispW,
      height: dispH,
      backgroundImage: `url("${diffuseUrl}")`,
      backgroundRepeat: "no-repeat",
      backgroundPosition: `-${rect.x * scale}px -${rect.y * scale}px`,
      backgroundSize: `${natural.w * scale}px ${natural.h * scale}px`,
      imageRendering: rendering,
    };
  } else {
    inner = {
      width: dispW,
      height: dispH,
      backgroundImage: `url("${diffuseUrl}")`,
      backgroundRepeat: "no-repeat",
      backgroundSize: `${dispW}px ${dispH}px`,
      imageRendering: rendering,
    };
  }

  return (
    <div className="preview">
      <div className="preview__head">
        <h3 className="preview__title">Diffuse</h3>
        <label className="field field--inline">
          <input
            type="checkbox"
            checked={pixelPerfect}
            onChange={(e) => setPixelPerfect(e.target.checked)}
          />
          <span className="field__label">pixel-perfect</span>
        </label>
      </div>
      <div className="preview__stage checker">
        <div className="preview__sprite" style={inner} />
      </div>
      <div className="preview__meta">
        {baseW}×{baseH}px{scale > 1 ? ` · ${scale}x` : ""}
      </div>
    </div>
  );
}
