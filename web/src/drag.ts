import type { ArtifactKind, ArtifactRef } from "./api/generated";

export const ARTIFACT_MIME = "application/x-cooksprite-artifact";

export interface ArtifactDragPayload {
  artifact_id: string;
  kind: ArtifactKind;
}

let activeGhost: HTMLElement | null = null;
let activePayload: ArtifactDragPayload | null = null;

export function activeArtifactDrag(): ArtifactDragPayload | null {
  return activePayload;
}

export function endArtifactDrag(): void {
  activeGhost?.remove();
  activeGhost = null;
  activePayload = null;
}

export function encodeArtifact(artifact: Pick<ArtifactRef, "id" | "kind">): string {
  return JSON.stringify({ artifact_id: artifact.id, kind: artifact.kind });
}

export function decodeArtifact(raw: string): ArtifactDragPayload | null {
  try {
    const value = JSON.parse(raw) as Partial<ArtifactDragPayload>;
    if (typeof value.artifact_id !== "string" || typeof value.kind !== "string") return null;
    return { artifact_id: value.artifact_id, kind: value.kind };
  } catch {
    return null;
  }
}

export function beginArtifactDrag(event: DragEvent, artifact: ArtifactRef): void {
  if (!event.dataTransfer) return;
  endArtifactDrag();
  activePayload = { artifact_id: artifact.id, kind: artifact.kind };
  event.dataTransfer.effectAllowed = "copy";
  event.dataTransfer.setData(ARTIFACT_MIME, encodeArtifact(artifact));
  event.dataTransfer.setData("text/plain", artifact.id);
  const ghost = document.createElement("div");
  ghost.className = "artifact-drag-ghost";
  ghost.textContent = `${artifact.kind} · ${artifact.title || artifact.id.slice(0, 12)}`;
  document.body.appendChild(ghost);
  activeGhost = ghost;
  event.dataTransfer.setDragImage(ghost, 18, 18);
  (event.currentTarget as HTMLElement | null)?.addEventListener("dragend", endArtifactDrag, { once: true });
}
