# CookSprite Godot importer

Copy `addons/cooksprite_importer` into a Godot 4.4+ project and enable
**CookSprite Importer** under Project Settings → Plugins. Drag a `.cooksprite`
file into the FileSystem dock. The imported main resource is always a
`PackedScene`, so one file type has one predictable drag-and-reimport behavior:

- static packages instantiate a `Sprite2D` scene;
- character packages instantiate an `AnimatedSprite2D` scene;
- tileset packages instantiate a `TileMapLayer` scene with an embedded
  `TileSet` / `TileSetAtlasSource`.

Character animation names are `<view>_<action>_<direction>`, for example
`level_walk_ne` and `top45_attack_s`. Each frame is a `CanvasTexture` with the
diffuse and normal PNG paired together. `SpriteFrames` runs at 1000 FPS and
stores each manifest `duration_ms` as its relative frame duration, preserving
the exact authored milliseconds. Ping-pong tracks are materialized in reverse
without duplicating the endpoints.

The root `Node2D` origin is the package pivot. Reimporting the source updates
the generated scene through Godot's normal `EditorImportPlugin` lifecycle.
