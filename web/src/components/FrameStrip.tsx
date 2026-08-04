import { useEffect, useState } from "react";
import { frameRect } from "./SpritePreview";

interface FrameStripProps {
  diffuseUrl: string;
  frameCount: number;
  frameW?: number;
  frameH?: number;
  selected: number;
  onSelect: (index: number) => void;
}

const THUMB = 48;

export function FrameStrip({
  diffuseUrl,
  frameCount,
  frameW,
  frameH,
  selected,
  onSelect,
}: FrameStripProps) {
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);

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

  if (!frameW || !frameH || !natural) return null;

  const scale = THUMB / Math.max(frameW, frameH);

  return (
    <div className="framestrip">
      <h3 className="preview__title">Frames ({frameCount})</h3>
      <div className="framestrip__row">
        {Array.from({ length: frameCount }, (_, i) => {
          const rect = frameRect(natural, frameW, frameH, i);
          const style: React.CSSProperties = {
            width: THUMB,
            height: THUMB,
            backgroundImage: `url("${diffuseUrl}")`,
            backgroundRepeat: "no-repeat",
            backgroundPosition: `-${rect.x * scale}px -${rect.y * scale}px`,
            backgroundSize: `${natural.w * scale}px ${natural.h * scale}px`,
            imageRendering: "pixelated",
          };
          return (
            <button
              key={i}
              type="button"
              className={`framestrip__cell checker ${
                i === selected ? "framestrip__cell--active" : ""
              }`}
              onClick={() => onSelect(i)}
              title={`Frame ${i}`}
            >
              <span className="framestrip__thumb" style={style} />
              <span className="framestrip__index">{i}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
