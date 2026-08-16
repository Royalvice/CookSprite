extends SceneTree


func _initialize() -> void:
	var static_scene: PackedScene = load("res://tests/fixtures/static.cooksprite")
	_assert(static_scene != null, "static package did not import as PackedScene")
	var static_root := static_scene.instantiate()
	_assert(static_root.name == "CookSpriteStatic", "static root type is wrong")
	var sprite: Sprite2D = static_root.get_node("Sprite")
	_assert(sprite.texture is CanvasTexture, "static texture is not CanvasTexture")
	_assert((sprite.texture as CanvasTexture).normal_texture != null, "static normal is missing")
	_assert(sprite.position == Vector2(-1.0, -2.0), "static pivot was not applied")
	static_root.free()

	var character_scene: PackedScene = load("res://tests/fixtures/character.cooksprite")
	_assert(character_scene != null, "character package did not import as PackedScene")
	var character_root := character_scene.instantiate()
	var animated: AnimatedSprite2D = character_root.get_node("AnimatedSprite")
	_assert(animated.sprite_frames.has_animation("level_walk_ne"), "level_walk_ne is missing")
	_assert(animated.sprite_frames.has_animation("top45_attack_s"), "top45_attack_s is missing")
	_assert(animated.sprite_frames.get_animation_names().size() == 16, "sixteen real view-direction tracks were not imported")
	_assert(animated.sprite_frames.get_frame_count("level_walk_ne") == 2, "character frame count is wrong")
	_assert(animated.sprite_frames.get_frame_duration("level_walk_ne", 0) == 80.0, "duration_ms was not preserved")
	_assert(animated.sprite_frames.get_animation_speed("level_walk_ne") == 1000.0, "millisecond timebase is wrong")
	var offsets: Dictionary = animated.get_meta("cooksprite_frame_offsets")
	_assert(offsets["level_walk_ne"][1] == Vector2i(1, 0), "per-frame offsets were not preserved")
	character_root.free()

	var tileset_scene: PackedScene = load("res://tests/fixtures/tileset.cooksprite")
	_assert(tileset_scene != null, "tileset package did not import as PackedScene")
	var tileset_root := tileset_scene.instantiate()
	var layer: TileMapLayer = tileset_root.get_node("TileMapLayer")
	_assert(layer.tile_set != null, "embedded TileSet is missing")
	_assert(layer.tile_set.tile_size == Vector2i(1, 1), "tile dimensions are wrong")
	_assert(layer.tile_set.get_source_count() == 1, "TileSet atlas source is missing")
	tileset_root.free()

	print("COOKSPRITE_GODOT_IMPORT_OK")
	quit(0)


func _assert(value: bool, message: String) -> void:
	if value:
		return
	push_error(message)
	quit(1)
