from cooksprite.registry import CookSpriteRegistry
from cooksprite.workflows.minimax_h3 import minimax_h3_first_last_graph
from cooksprite.workflows.views import (
    klein_multi_angles_graph,
    pure_prompt_graph,
    quadview_krea_graph,
    tripview_graph,
)


VIEW_METHODS = {
    "multi_angles_klein9b",
    "tripview_klein9b",
    "quadview_krea2",
    "pure_prompt_klein9b",
}


def test_registry_exposes_four_view_methods_and_five_h3_actions():
    registry = CookSpriteRegistry()
    views = registry.get("image.views")
    animation = registry.get("animation.generate")

    assert views is not None
    methods = next(control for control in views.controls if control.id == "method")
    assert methods.default == "multi_angles_klein9b"
    assert {option.id for option in methods.options} == VIEW_METHODS
    assert {
        registry.recipe_binding("image.views", {"method": method})
        for method in VIEW_METHODS
    } == {
        "views-multi-angles-klein9b",
        "views-tripview-klein9b",
        "views-quadview-krea2",
        "views-pure-prompt-klein9b",
    }

    assert animation is not None
    actions = next(control for control in animation.controls if control.id == "action")
    assert [option.id for option in actions.options] == ["idle", "walk", "run", "jump", "roll"]

    idle_prompt = registry.execution("animation.generate")["prompt"]["action_prompts"]["idle"]
    assert "pronounced chest and shoulder breathing" in idle_prompt
    assert "readable side-to-side weight shift" in idle_prompt


def test_klein_view_graphs_use_three_independent_512_square_branches():
    multi = klein_multi_angles_graph()
    pure = pure_prompt_graph()
    for graph in (multi, pure):
        assert graph["latent"]["inputs"] == {"width": 512, "height": 512, "batch_size": 1}
        for index in (1, 2, 3):
            assert graph[f"noise_{index}"]["class_type"] == "RandomNoise"
            assert graph[f"decode_{index}"]["class_type"] == "VAEDecode"
        assert graph["batch_123"]["class_type"] == "ImageBatch"

    assert "lora" in multi
    assert "<sks>" in multi["positive_2"]["inputs"]["text"]
    assert "strict right-side profile" in multi["positive_2"]["inputs"]["text"]
    assert "exactly perpendicular" in multi["positive_2"]["inputs"]["text"]
    assert not any(node["class_type"].startswith("LoraLoader") for node in pure.values())
    assert "strict right-side profile" in pure["positive_2"]["inputs"]["text"]


def test_tripview_slices_three_columns_and_normalizes_to_512_square():
    graph = tripview_graph()
    assert graph["latent"]["inputs"] == {"width": 1536, "height": 1024, "batch_size": 1}
    assert "strict right-side profile" in graph["positive"]["inputs"]["text"]
    assert "exactly 90 degrees" in graph["positive"]["inputs"]["text"]
    assert graph["slice"]["inputs"]["columns"] == 3
    assert [graph[f"panel_{index}"]["inputs"]["batch_index"] for index in (1, 2, 3)] == [0, 1, 2]
    for index in (1, 2, 3):
        assert graph[f"panel_crop_{index}"]["inputs"]["width"] == 448
        assert graph[f"panel_crop_{index}"]["inputs"]["x"] == 32
        assert graph[f"panel_scale_{index}"]["inputs"]["width"] == 256
        assert graph[f"panel_scale_{index}"]["inputs"]["height"] == 512
        assert graph[f"panel_pad_{index}"]["inputs"]["left"] == 128
        assert graph[f"panel_pad_{index}"]["inputs"]["right"] == 128


def test_quadview_discards_closeup_and_normalizes_three_full_body_panels():
    graph = quadview_krea_graph()
    assert graph["latent"]["inputs"] == {"width": 1536, "height": 1024, "batch_size": 1}
    assert "strict right-side profile" in graph["positive"]["inputs"]["prompt"]
    assert "exactly 90 degrees" in graph["positive"]["inputs"]["prompt"]
    assert graph["slice"]["inputs"]["columns"] == 4
    assert [graph[f"panel_{index}"]["inputs"]["batch_index"] for index in (1, 2, 3)] == [1, 2, 3]
    for index in (1, 2, 3):
        assert graph[f"panel_crop_{index}"]["inputs"]["width"] == 288
        assert graph[f"panel_crop_{index}"]["inputs"]["x"] == 48
        assert graph[f"panel_scale_{index}"]["inputs"]["width"] == 192
        assert graph[f"panel_scale_{index}"]["inputs"]["height"] == 512
        assert graph[f"panel_pad_{index}"]["inputs"]["left"] == 160
        assert graph[f"panel_pad_{index}"]["inputs"]["right"] == 160


def test_h3_uses_same_source_for_124_frame_first_last_generation():
    graph = minimax_h3_first_last_graph()
    assert graph["source_scale"]["inputs"]["width"] == 512
    assert graph["source_scale"]["inputs"]["height"] == 512
    assert graph["h3"]["inputs"]["first_frame"] == ["source_scale", 0]
    assert graph["h3"]["inputs"]["last_frame"] == ["source_scale", 0]
    assert graph["h3"]["inputs"]["length"] == 124
    assert graph["latent"]["inputs"]["length"] == 124
    assert graph["schedule"]["inputs"]["steps"] == 20
    assert graph["sampler_select"]["inputs"]["sampler_name"] == "res_multistep"
    assert not any("Pixel" in node["class_type"] for node in graph.values())
