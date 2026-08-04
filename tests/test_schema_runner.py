"""Tests for schema validation and the workflow + task runners."""

from __future__ import annotations

import pytest

import workflow  # noqa: F401
from backend.ops import AdapterRouter
from backend.adapters.stub import StubAdapter
from workflow.clients import DirectClient
from workflow.tool import REGISTRY
from workflow.library import Library
from workflow.runner import run_task, run_workflow
from workflow.schema import (
    WorkflowValidationError,
    load_task_yaml,
    load_workflow_yaml,
    validate_task,
    validate_workflow,
)
from workflow.types import SpritePair

GOOD = """
id: t
output: b.image
params:
  prompt: { type: string, default: "" }
nodes:
  - id: a
    tool: text2img
    params: { prompt: "${prompt}" }
  - id: b
    tool: pixelize
    inputs: { image: a.image }
"""


def test_valid_workflow_passes():
    validate_workflow(load_workflow_yaml(GOOD), REGISTRY)


def test_unknown_tool_fails():
    bad = GOOD.replace("tool: pixelize", "tool: nope")
    with pytest.raises(WorkflowValidationError):
        validate_workflow(load_workflow_yaml(bad), REGISTRY)


def test_type_mismatch_fails():
    # Feed a normal_map output into pixelize's image input.
    bad = """
id: t
output: c.image
nodes:
  - id: a
    tool: text2img
  - id: b
    tool: normal_estimate
    inputs: { image: a.image }
  - id: c
    tool: pixelize
    inputs: { image: b.normal }
"""
    with pytest.raises(WorkflowValidationError):
        validate_workflow(load_workflow_yaml(bad), REGISTRY)


def test_missing_required_input_fails():
    bad = """
id: t
output: b.image
nodes:
  - id: b
    tool: pixelize
"""
    with pytest.raises(WorkflowValidationError):
        validate_workflow(load_workflow_yaml(bad), REGISTRY)


def test_external_input_ref_validates():
    wf = load_workflow_yaml(
        """
id: w
inputs: { src: image }
output: px.image
nodes:
  - id: px
    tool: pixelize
    inputs: { image: $in.src }
"""
    )
    validate_workflow(wf, REGISTRY)  # $in.src resolves to a declared input


def test_undeclared_external_input_fails():
    bad = """
id: w
output: px.image
nodes:
  - id: px
    tool: pixelize
    inputs: { image: $in.missing }
"""
    with pytest.raises(WorkflowValidationError):
        validate_workflow(load_workflow_yaml(bad), REGISTRY)


def _client() -> DirectClient:
    return DirectClient(AdapterRouter([StubAdapter()]))


# --- task layer ------------------------------------------------------------

def test_task_wiring_to_unknown_input_fails():
    lib = Library.load_builtin()
    bad = load_task_yaml(
        """
id: bt
output: spr
nodes:
  - id: gen
    workflow: generate_image
  - id: spr
    workflow: spritify
    inputs: { nope: gen }
"""
    )
    with pytest.raises(WorkflowValidationError):
        validate_task(bad, lib.workflow_map(), REGISTRY)


def test_single_sprite_task_runs_end_to_end():
    library = Library.load_builtin()
    task = library.get_task("single_sprite")
    progress = []
    artifact = run_task(
        task, library.workflow_map(), REGISTRY, _client(),
        params={"prompt": "a little robot", "width": 96, "height": 96},
        on_progress=lambda f, m: progress.append(f),
    )
    assert isinstance(artifact, SpritePair)
    assert (artifact.diffuse.width, artifact.diffuse.height) == (96, 96)
    assert artifact.normal is not None
    assert artifact.normal.pixels.shape == (96, 96, 3)
    # Progress advanced across both workflow-nodes to completion.
    assert progress and progress[-1] == pytest.approx(1.0, abs=1e-6)


def test_task_run_is_deterministic():
    library = Library.load_builtin()
    task = library.get_task("single_sprite")
    wfmap = library.workflow_map()
    a = run_task(task, wfmap, REGISTRY, _client(), params={"prompt": "x", "seed": 7})
    b = run_task(task, wfmap, REGISTRY, _client(), params={"prompt": "x", "seed": 7})
    import numpy as np

    np.testing.assert_array_equal(a.diffuse.pixels, b.diffuse.pixels)
