import { useEffect, useMemo, useState } from "react";
import type {
  Capability,
  ParamMap,
  ParamSpec,
  ParamValue,
  RunRequest,
  Workflow,
} from "../api";

interface ToolboxProps {
  capabilities: Capability[];
  busy: boolean;
  onRun: (req: RunRequest) => void;
}

function defaultFor(spec: ParamSpec): ParamValue {
  if (spec.default !== undefined) return spec.default;
  switch (spec.type) {
    case "int":
      return 0;
    case "bool":
      return false;
    default:
      return "";
  }
}

function buildDefaults(wf: Workflow | undefined): ParamMap {
  const out: ParamMap = {};
  if (!wf) return out;
  for (const [key, spec] of Object.entries(wf.params_schema)) {
    out[key] = defaultFor(spec);
  }
  return out;
}

export function Toolbox({ capabilities, busy, onRun }: ToolboxProps) {
  const [capId, setCapId] = useState<string>("");
  const [workflowName, setWorkflowName] = useState<string>("");
  const [params, setParams] = useState<ParamMap>({});

  const capability = useMemo(
    () => capabilities.find((c) => c.id === capId),
    [capabilities, capId]
  );
  const workflow = useMemo(
    () => capability?.workflows.find((w) => w.name === workflowName),
    [capability, workflowName]
  );

  // Select the first capability once loaded.
  useEffect(() => {
    if (!capId && capabilities.length > 0) {
      setCapId(capabilities[0].id);
    }
  }, [capabilities, capId]);

  // When capability changes, preselect its default workflow.
  useEffect(() => {
    if (!capability) return;
    const def =
      capability.workflows.find((w) => w.default) ?? capability.workflows[0];
    setWorkflowName(def ? def.name : "");
  }, [capability]);

  // When workflow changes, reset params to its schema defaults.
  useEffect(() => {
    setParams(buildDefaults(workflow));
  }, [workflow]);

  const setParam = (key: string, value: ParamValue) => {
    setParams((prev) => ({ ...prev, [key]: value }));
  };

  const canRun = Boolean(capability && !busy);

  const submit = () => {
    if (!capability) return;
    onRun({
      capability: capability.id,
      workflow: workflowName || undefined,
      params,
    });
  };

  return (
    <div className="toolbox">
      <h2 className="toolbox__title">Toolbox</h2>

      <label className="field">
        <span className="field__label">Capability</span>
        <select
          className="field__control"
          value={capId}
          onChange={(e) => setCapId(e.target.value)}
          disabled={capabilities.length === 0}
        >
          {capabilities.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id}
            </option>
          ))}
        </select>
      </label>

      {capability && <p className="toolbox__desc">{capability.description}</p>}

      {capability && capability.workflows.length > 0 && (
        <label className="field">
          <span className="field__label">Workflow</span>
          <select
            className="field__control"
            value={workflowName}
            onChange={(e) => setWorkflowName(e.target.value)}
          >
            {capability.workflows.map((w) => (
              <option key={w.name} value={w.name}>
                {w.name}
                {w.default ? " (default)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      {workflow && (
        <div className="toolbox__params">
          {Object.entries(workflow.params_schema).map(([key, spec]) => (
            <ParamField
              key={key}
              name={key}
              spec={spec}
              value={params[key]}
              onChange={(v) => setParam(key, v)}
            />
          ))}
        </div>
      )}

      <button className="btn btn--primary" onClick={submit} disabled={!canRun}>
        {busy ? "Running..." : "Run"}
      </button>
    </div>
  );
}

interface ParamFieldProps {
  name: string;
  spec: ParamSpec;
  value: ParamValue | undefined;
  onChange: (value: ParamValue) => void;
}

function ParamField({ name, spec, value, onChange }: ParamFieldProps) {
  const label = spec.label ?? name;

  if (spec.type === "bool") {
    return (
      <label className="field field--inline">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="field__label">{label}</span>
      </label>
    );
  }

  if (spec.type === "int") {
    return (
      <label className="field">
        <span className="field__label">{label}</span>
        <input
          className="field__control"
          type="number"
          step={1}
          value={typeof value === "number" ? value : Number(value ?? 0)}
          onChange={(e) => onChange(Math.trunc(Number(e.target.value) || 0))}
        />
      </label>
    );
  }

  // string
  const isPrompt = /prompt|text|description/i.test(name);
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {isPrompt ? (
        <textarea
          className="field__control field__control--area"
          rows={3}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          className="field__control"
          type="text"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </label>
  );
}
