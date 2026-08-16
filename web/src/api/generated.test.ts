import { describe, expect, it } from "vitest";
import { dragPayload, inferArtifactKind, type ArtifactRef } from "./generated";

describe("shared artifact protocol", () => {
  it("classifies files into the same kinds used by Action slots", () => {
    expect(inferArtifactKind(new File(["x"], "hero.png", { type: "image/png" }))).toBe("Image");
    expect(inferArtifactKind(new File(["x"], "hero-sheet.webp", { type: "image/webp" }))).toBe("SpriteSheet");
    expect(inferArtifactKind(new File(["x"], "walk.gif", { type: "image/gif" }))).toBe("Video");
    expect(inferArtifactKind(new File(["x"], "notes.txt", { type: "text/plain" }))).toBeNull();
  });

  it("keeps drag payloads deliberately minimal", () => {
    const artifact = {
      id: "art_hero",
      kind: "Image",
      sha256: "abc",
      media_type: "image/png",
      size: 3,
      url: "/api/v1/artifacts/art_hero/content",
      title: "hero",
      favorite: false,
      trashed: false,
      meta: {},
      created_at: "",
    } satisfies ArtifactRef;
    expect(JSON.parse(dragPayload(artifact))).toEqual({ artifact_id: "art_hero", kind: "Image" });
  });

  it("treats SVG visual material as a typed image Artifact", () => {
    expect(inferArtifactKind(new File(["<svg/ >"], "actor.svg", { type: "image/svg+xml" }))).toBe("Image");
  });
});
