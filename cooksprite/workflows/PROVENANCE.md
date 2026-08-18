# FLUX.2 Klein workflow provenance

These API-format graphs are a compact, runtime-independent normalization of
the official Comfy-Org workflow templates. The source templates and exact
revision are recorded in `flux2_klein.py`:

- Repository: https://github.com/Comfy-Org/workflow_templates
- Revision: `9b5fbc54d31adf325860cd1dbde9b627f96706e8`
- ComfyUI: `v0.33.2`
- Sources: `image_flux2_klein_text_to_image.json`,
  `image_flux2_text_to_image_9b.json`,
  `image_flux2_klein_image_edit_4b_distilled.json`, and
  `image_flux2_klein_image_edit_9b_distilled.json`.

CookSprite does not download templates or models at API startup. Model files
remain runtime data and are installed explicitly on the selected ComfyUI host.
