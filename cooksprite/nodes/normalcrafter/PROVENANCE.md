# NormalCrafter adaptation provenance

This directory contains a compact ComfyUI-node adaptation of
[`Binyr/NormalCrafter`](https://github.com/Binyr/NormalCrafter) at
`75af9887a2cb14cd1ce3883c5773bc296565777c` (MIT; see `LICENSE`).

The model bundle is pinned separately to
[`Yanrui95/NormalCrafter`](https://huggingface.co/Yanrui95/NormalCrafter) at
`7e24d68d86ae008fe08ef50b4e51cd2fc2c8cf57` (Apache-2.0).

The adaptation retains the authors' one-step zero-latent SVD inference and
overlapping-window latent merge.  It deliberately does **not** vendor
`AIWarper/ComfyUI-NormalCrafterWrapper`: that wrapper was audited at
`cf0d92bc5480e4a2785ebecf878d40bf2eb5f5aa` and performs runtime downloads,
distorts aspect ratio, and fails to pass its short-clip padding into inference.

CookSprite additions are limited to typed bridge streaming, aspect-preserving
resize/pad, alpha restoration, bounded window memory, and CPU-resident model
caching between runs.  No model download happens at node import or execution.
