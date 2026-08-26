"""Built-in, generic option examples registered as normal CookSprite artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from .domain import ArtifactRef
from .store import Store

ASSET_ROOT = Path(__file__).with_name("example_assets")
STILL_ASSETS = {
    "still.actor": "actor.svg",
    "still.object": "object.svg",
    "still.tile": "tile.svg",
    "still.effect": "effect.svg",
    "still.smooth": "smooth.svg",
}
MOTIONS = ("idle", "walk", "run", "attack", "cast", "hit", "jump", "death", "roll")


def register_action_examples(store: Store) -> dict[str, ArtifactRef]:
    """Return registry keys bound to content-addressed ArtifactRefs.

    These assets are generic product examples, not presets and not hidden
    prompts.  Because they use the same artifact bridge as user material, a
    preview can be dragged into every semantically compatible input.
    """

    examples: dict[str, ArtifactRef] = {}
    for key, filename in STILL_ASSETS.items():
        examples[key] = store.put_artifact(
            (ASSET_ROOT / filename).read_bytes(),
            "image/svg+xml",
            "Image",
            {"role": "action_example", "system": True, "example_key": key},
            title=f"EXAMPLE · {key.removeprefix('still.').upper()}",
        )

    frames = [
        store.put_artifact(
            (ASSET_ROOT / f"motion-{index}.svg").read_bytes(),
            "image/svg+xml",
            "Image",
            {"role": "action_example_frame", "system": True, "frame": index},
            title=f"EXAMPLE FRAME {index}",
        )
        for index in range(1, 5)
    ]
    for action in MOTIONS:
        manifest = {
            "schema": "cooksprite.frame-sequence/v1",
            "action": action,
            "view": "level",
            "direction": "s",
            "frames": [frame.id for frame in frames],
        }
        examples[f"motion.{action}"] = store.put_artifact(
            json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode(),
            "application/vnd.cooksprite.frame-sequence+json",
            "FrameSeq",
            {
                "role": "action_example",
                "system": True,
                "example_key": f"motion.{action}",
                "cover_artifact": frames[0].id,
                "source_artifacts": [frame.id for frame in frames],
                "frame_count": len(frames),
                "action": action,
                "view": "level",
                "direction": "s",
            },
            title=f"EXAMPLE · {action.upper()}",
        )
    return examples
