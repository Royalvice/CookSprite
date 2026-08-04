"""Tests for workflow schema validation and the end-to-end runner."""

from __future__ import annotations

import pytest

import workflow  # noqa: F401
from backend.ops import AdapterRouter
from backend.adapters.stub import StubAdapter
from workflow.clients import DirectClient
from workflow.component import REGISTRY
from workflow.library import Library
from workflow.runner import run_workflow
from workflow.schema import WorkflowValidationError, load_workflow_yaml, validate_workflow
from workflow.types import SpritePair

GOOD = """
id: t
capability: t
output: b.image
params:
  prompt: { type: string, default: "" }
nodes:
  - id: a
    component: text2img
    params: { prompt: "${prompt}" }
  - id: b
    component: pixelize
    inputs: { image: a.image }
"""


def test_valid_workflow_passes():
    spec = load_workflow_yaml(GOOD)
    validate_workflow(spec, REGISTRY)


def test_unknown_component_fails():
    bad = GOOD.replace("component: pixelize", "component: nope")
    with pytest.raises(WorkflowValidationError):
        validate_workflow(load_workflow_yaml(bad), REGISTRY)


def test_type_mismatch_fails():
    # Feed a normal_map output into pixelize's image input.
    bad = """
id: t
capability: t
output: c.image
nodes:
  - id: a
    component: text2img
  - id: b
    component: normal_estimate
    inputs: { image: a.image }
  - id: c
    component: pixelize
    inputs: { image: b.normal }
"""
    with pytest.raises(WorkflowValidationError):
        validate_workflow(load_workflow_yaml(bad), REGISTRY)


def test_missing_required_input_fails():
    bad = """
id: t
capability: t
output: b.image
nodes:
  - id: b
    component: pixelize
"""
    with pytest.raises(WorkflowValidationError):
        validate_workflow(load_workflow_yaml(bad), REGISTRY)


def _client() -> DirectClient:
    return DirectClient(AdapterRouter([StubAdapter()]))


def test_single_sprite_runs_end_to_end():
    library = Library.load_builtin()
    spec = library.resolve("single_sprite")
    progress = []
    artifact = run_workflow(
        spec, REGISTRY, _client(),
        params={"prompt": "a little robot", "width": 96, "height": 96},
        on_progress=lambda f, m: progress.append(f),
    )
    assert isinstance(artifact, SpritePair)
    assert (artifact.diffuse.width, artifact.diffuse.height) == (96, 96)
    assert artifact.normal is not None
    assert artifact.normal.pixels.shape == (96, 96, 3)
    # Progress advanced monotonically to completion.
    assert progress and progress[-1] == pytest.approx(1.0, abs=1e-6)


def test_run_is_deterministic():
    library = Library.load_builtin()
    spec = library.resolve("single_sprite")
    a = run_workflow(spec, REGISTRY, _client(), params={"prompt": "x", "seed": 7})
    b = run_workflow(spec, REGISTRY, _client(), params={"prompt": "x", "seed": 7})
    import numpy as np

    np.testing.assert_array_equal(a.diffuse.pixels, b.diffuse.pixels)
