from __future__ import annotations

from cooksprite.recipe_assembler import assemble_recipe_workflow
from cooksprite.recipes import Recipe


def test_discovered_dimensions_bind_to_one_resolution_control():
    recipe = Recipe(
        id="discovered-image",
        label="Discovered image",
        family="comfy.image.unet",
        actions=["image.generate"],
        modes=["t2i"],
        source="discovered",
        workflow={
            "positive": {"class_type": "TextNode", "inputs": {"text": ""}},
            "latent": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
            },
        },
        slots={"text": "positive.text", "count": "latent.batch_size"},
        output=["latent", 0],
    )

    workflow = assemble_recipe_workflow("rt_test", recipe, "image.generate", "t2i")
    sealed = next(node for node in workflow.nodes if node.id == "sealed")

    assert workflow.inputs["resolution"] == "Number"
    assert sealed.inputs["width"].input == "resolution"
    assert sealed.inputs["height"].input == "resolution"
    assert "width" not in workflow.inputs
    assert "height" not in workflow.inputs
