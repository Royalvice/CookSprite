import type { RunState } from "../api";

interface RunStatusProps {
  state: RunState;
}

export function RunStatus({ state }: RunStatusProps) {
  const pct = Math.round(Math.min(1, Math.max(0, state.progress)) * 100);
  const isError = state.status === "error";
  const isDone = state.status === "done";

  return (
    <div className={`runstatus runstatus--${state.status}`}>
      <div className="runstatus__row">
        <span className="runstatus__badge">{state.status}</span>
        <span className="runstatus__message">{state.message || " "}</span>
        {!isError && <span className="runstatus__pct">{pct}%</span>}
      </div>
      {!isError && (
        <div className="progress">
          <div
            className={`progress__fill ${isDone ? "progress__fill--done" : ""}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
