@tool
extends EditorImportPlugin

const SUPPORTED_SCHEMA := "cooksprite.package/v1"


func _get_importer_name() -> String:
	return "cooksprite.package"


func _get_visible_name() -> String:
	return "CookSprite Package"


func _get_recognized_extensions() -> PackedStringArray:
	return PackedStringArray(["cooksprite"])


func _get_save_extension() -> String:
	return "scn"


func _get_resource_type() -> String:
	return "PackedScene"


func _get_preset_count() -> int:
	return 1


func _get_preset_name(_preset_index: int) -> String:
	return "Default"


func _get_import_options(_path: String, _preset_index: int) -> Array[Dictionary]:
	return []


func _get_import_order() -> int:
	return 10


func _get_priority() -> float:
	return 1.0


func _import(
		source_file: String,
		save_path: String,
		_options: Dictionary,
		_platform_variants: Array[String],
		_gen_files: Array[String]
	) -> Error:
	var reader := ZIPReader.new()
	var open_error := reader.open(source_file)
	if open_error != OK:
		push_error("CookSprite: cannot open package: %s" % source_file)
		return open_error
	var manifest_bytes := reader.read_file("manifest.json")
	if manifest_bytes.is_empty():
		reader.close()
		push_error("CookSprite: manifest.json is missing")
		return ERR_FILE_CORRUPT
	var manifest_value: Variant = JSON.parse_string(manifest_bytes.get_string_from_utf8())
	if not manifest_value is Dictionary:
		reader.close()
		push_error("CookSprite: manifest.json is invalid")
		return ERR_PARSE_ERROR
	var manifest: Dictionary = manifest_value
	if manifest.get("schema", "") != SUPPORTED_SCHEMA:
		reader.close()
		push_error("CookSprite: unsupported package schema")
		return ERR_UNAVAILABLE
	var root := _build_root(reader, manifest)
	reader.close()
	if root == null:
		return ERR_FILE_CORRUPT
	root.set_meta("cooksprite_manifest", JSON.stringify(manifest))
	var packed := PackedScene.new()
	var pack_error := packed.pack(root)
	if pack_error != OK:
		root.free()
		return pack_error
	var save_error := ResourceSaver.save(packed, "%s.%s" % [save_path, _get_save_extension()])
	root.free()
	return save_error


func _build_root(reader: ZIPReader, manifest: Dictionary) -> Node:
	match manifest.get("type", ""):
		"static":
			return _build_static(reader, manifest)
		"character":
			return _build_character(reader, manifest)
		"tileset":
			return _build_tileset(reader, manifest)
		_:
			push_error("CookSprite: unknown package type")
			return null


func _build_static(reader: ZIPReader, manifest: Dictionary) -> Node2D:
	var root := Node2D.new()
	root.name = "CookSpriteStatic"
	var texture := _canvas_texture(reader, manifest.get("primary"), manifest.get("normal"))
	if texture == null:
		root.free()
		return null
	var sprite := Sprite2D.new()
	sprite.name = "Sprite"
	sprite.texture = texture
	sprite.centered = false
	sprite.position = _pivot_offset(manifest, texture.get_size())
	root.add_child(sprite)
	sprite.owner = root
	return root


func _build_character(reader: ZIPReader, manifest: Dictionary) -> Node2D:
	var root := Node2D.new()
	root.name = "CookSpriteCharacter"
	var animated := AnimatedSprite2D.new()
	animated.name = "AnimatedSprite"
	animated.centered = false
	var frames := SpriteFrames.new()
	frames.remove_animation("default")
	var frame_offsets: Dictionary = {}
	for clip_value in manifest.get("clips", []):
		var clip: Dictionary = clip_value
		for view_value in clip.get("views", []):
			var view: Dictionary = view_value
			for track_value in view.get("tracks", []):
				var track: Dictionary = track_value
				var animation_name := "%s_%s_%s" % [view.get("id", "level"), clip.get("action", clip.get("name", "clip")), track.get("direction", "s")]
				frames.add_animation(animation_name)
				frames.set_animation_speed(animation_name, 1000.0)
				var loop_mode: String = clip.get("loop", "linear")
				frames.set_animation_loop(animation_name, loop_mode != "none")
				var source_frames: Array = track.get("frames", [])
				var export_frames: Array = source_frames.duplicate()
				if loop_mode == "pingpong" and source_frames.size() > 2:
					for reverse_index in range(source_frames.size() - 2, 0, -1):
						export_frames.append(source_frames[reverse_index])
				for frame_value in export_frames:
					var frame: Dictionary = frame_value
					var texture := _canvas_texture(reader, frame.get("diffuse"), frame.get("normal"))
					if texture == null:
						continue
					frames.add_frame(animation_name, texture, float(frame.get("duration_ms", 100)))
					if not frame_offsets.has(animation_name):
						frame_offsets[animation_name] = []
					frame_offsets[animation_name].append(Vector2i(int(frame.get("offset", {}).get("x", 0)), int(frame.get("offset", {}).get("y", 0))))
	animated.sprite_frames = frames
	animated.set_meta("cooksprite_frame_offsets", frame_offsets)
	var canvas: Dictionary = manifest.get("canvas", {"width": 64, "height": 64})
	animated.position = _pivot_offset(manifest, Vector2(float(canvas.get("width", 64)), float(canvas.get("height", 64))))
	root.add_child(animated)
	animated.owner = root
	return root


func _build_tileset(reader: ZIPReader, manifest: Dictionary) -> Node2D:
	var root := Node2D.new()
	root.name = "CookSpriteTileset"
	var layer := TileMapLayer.new()
	layer.name = "TileMapLayer"
	var tile_width := int(manifest.get("tile_width", 32))
	var tile_height := int(manifest.get("tile_height", 32))
	var tile_set := TileSet.new()
	tile_set.tile_size = Vector2i(tile_width, tile_height)
	var texture := _canvas_texture(reader, manifest.get("source"), manifest.get("normal"))
	if texture != null:
		var atlas := TileSetAtlasSource.new()
		atlas.texture = texture
		atlas.texture_region_size = Vector2i(tile_width, tile_height)
		atlas.margins = Vector2i(int(manifest.get("margin", 0)), int(manifest.get("margin", 0)))
		atlas.separation = Vector2i(int(manifest.get("spacing", 0)), int(manifest.get("spacing", 0)))
		var image_size := texture.get_size()
		var columns := int((image_size.x - atlas.margins.x * 2 + atlas.separation.x) / (tile_width + atlas.separation.x))
		var rows := int((image_size.y - atlas.margins.y * 2 + atlas.separation.y) / (tile_height + atlas.separation.y))
		for y in range(maxi(0, rows)):
			for x in range(maxi(0, columns)):
				atlas.create_tile(Vector2i(x, y))
		tile_set.add_source(atlas, 0)
	layer.tile_set = tile_set
	root.add_child(layer)
	layer.owner = root
	return root


func _canvas_texture(reader: ZIPReader, diffuse_path: Variant, normal_path: Variant) -> CanvasTexture:
	if diffuse_path == null or String(diffuse_path).is_empty():
		push_error("CookSprite: diffuse frame is missing")
		return null
	var diffuse := _image_texture(reader, String(diffuse_path))
	if diffuse == null:
		return null
	var texture := CanvasTexture.new()
	texture.diffuse_texture = diffuse
	if normal_path != null and not String(normal_path).is_empty():
		texture.normal_texture = _image_texture(reader, String(normal_path))
	return texture


func _image_texture(reader: ZIPReader, path: String) -> ImageTexture:
	var bytes := reader.read_file(path)
	if bytes.is_empty():
		push_error("CookSprite: package file is missing: %s" % path)
		return null
	var image := Image.new()
	var error := image.load_png_from_buffer(bytes)
	if error != OK:
		push_error("CookSprite: package image is not a PNG: %s" % path)
		return null
	return ImageTexture.create_from_image(image)


func _pivot_offset(manifest: Dictionary, size: Vector2) -> Vector2:
	var pivot: Dictionary = manifest.get("pivot", {"x": 0.5, "y": 1.0})
	return Vector2(-float(pivot.get("x", 0.5)) * size.x, -float(pivot.get("y", 1.0)) * size.y)
