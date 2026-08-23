import type { ActionDescriptor } from "../api/generated";

const SPECIALIZED_PRESENTATIONS = new Set([
  "image-create",
  "animation-create",
  "image-views",
  "normal",
  "pixel-normal",
  "frame-redraw",
]);

export function usesGenericRunner(action: ActionDescriptor): boolean {
  return !SPECIALIZED_PRESENTATIONS.has(action.presentation);
}
