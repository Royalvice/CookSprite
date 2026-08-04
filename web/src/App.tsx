import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Artifact,
  Capability,
  RunResult,
  RunState,
  diffuseUrlOf,
  getCapabilities,
  getRunResult,
  normalUrlOf,
  startRun,
  subscribeRun,
  type RunRequest,
} from "./api";
import { Toolbox } from "./components/Toolbox";
import { RunStatus } from "./components/RunStatus";
import { SpritePreview } from "./components/SpritePreview";
import { FrameStrip } from "./components/FrameStrip";
import { LightPreview } from "./components/LightPreview";

export default function App() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [frameIndex, setFrameIndex] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    getCapabilities()
      .then((res) => {
        if (active) setCapabilities(res.capabilities);
      })
      .catch((err: unknown) => {
        if (active) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      active = false;
    };
  }, []);

  const handleRun = useCallback(async (req: RunRequest) => {
    setBusy(true);
    setResult(null);
    setFrameIndex(0);
    setRunState({ run_id: "", status: "queued", progress: 0, message: "Submitting..." });
    try {
      const handle = await startRun(req);
      const unsubscribe = subscribeRun(
        handle.run_id,
        (state) => {
          setRunState(state);
          if (state.status === "done") {
            const finish = state.result
              ? Promise.resolve(state.result)
              : getRunResult(state.run_id);
            finish
              .then((r) => setResult(r))
              .catch((err: unknown) =>
                setRunState((prev) =>
                  prev
                    ? { ...prev, status: "error", message: err instanceof Error ? err.message : String(err) }
                    : prev
                )
              )
              .finally(() => setBusy(false));
          } else if (state.status === "error") {
            setBusy(false);
          }
        },
        (err) => {
          setRunState({ run_id: handle.run_id, status: "error", progress: 0, message: err.message });
          setBusy(false);
        }
      );
      // Best-effort cleanup if a new run starts; kept simple for a single-run UI.
      void unsubscribe;
    } catch (err) {
      setRunState({
        run_id: "",
        status: "error",
        progress: 0,
        message: err instanceof Error ? err.message : String(err),
      });
      setBusy(false);
    }
  }, []);

  // Pick the primary artifact worth previewing: prefer a sprite pair / sheet.
  const primary = useMemo<Artifact | null>(() => {
    if (!result || result.artifacts.length === 0) return null;
    const withNormal = result.artifacts.find((a) => a.normal_url);
    return withNormal ?? result.artifacts[0];
  }, [result]);

  const frameCount = primary?.frames ?? 1;
  const diffuse = primary ? diffuseUrlOf(primary) : undefined;
  const normal = primary ? normalUrlOf(primary) : undefined;

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">CookSprite</h1>
        <p className="app__tagline">AI sprite generation toolbox — diffuse + normal, lit live.</p>
      </header>

      {loadError && (
        <div className="banner banner--error">
          Could not reach the workflow server: {loadError}. Start it on :8000 and reload.
        </div>
      )}

      <div className="layout">
        <aside className="layout__sidebar">
          <Toolbox capabilities={capabilities} busy={busy} onRun={handleRun} />
        </aside>

        <main className="layout__main">
          {runState && <RunStatus state={runState} />}

          {primary && diffuse && (
            <section className="results">
              <div className="results__grid">
                <SpritePreview
                  diffuseUrl={diffuse}
                  frameCount={frameCount}
                  frameIndex={frameIndex}
                  frameW={primary.frame_w}
                  frameH={primary.frame_h}
                />
                <LightPreview
                  diffuseUrl={diffuse}
                  normalUrl={normal}
                  frameCount={frameCount}
                  frameIndex={frameIndex}
                  frameW={primary.frame_w}
                  frameH={primary.frame_h}
                />
              </div>
              {frameCount > 1 && (
                <FrameStrip
                  diffuseUrl={diffuse}
                  frameCount={frameCount}
                  frameW={primary.frame_w}
                  frameH={primary.frame_h}
                  selected={frameIndex}
                  onSelect={setFrameIndex}
                />
              )}
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
