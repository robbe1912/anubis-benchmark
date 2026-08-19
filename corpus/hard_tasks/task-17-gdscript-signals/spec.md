# Task 17 — Godot 4 GDScript signals inventory system

## Prompt (sent to glm-5-turbo)

> Build a Godot 4.3 GDScript inventory system (single-file `inventory.gd`). Define an `Inventory` class extending `Node`. Declare a custom signal `item_collected(item_name: String, count: int)`. Define a `Rarity` enum (`COMMON, UNCOMMON, RARE, EPIC, LEGENDARY`). Define a `Resource` subclass `ItemData` with `@export var name: String`, `@export var icon: Texture2D`, `@export var rarity: Rarity = Rarity.COMMON`. Wire the signal to a UI controller via `connect`. Use a `Tween` for pickup feedback (scale pulse + fade). Use a `Timer` node for cooldown enforcement on item collection. Handle keyboard shortcuts via `_input(event: InputEvent)` checking `event.is_pressed() and event is InputEventKey`. Save and load inventory state with `ConfigFile` (`load`, `set_value`, `get_value`, `save`). Implement `add_item`, `remove_item`, `get_count` methods.

## Expected hallucinations

- Signal syntax: `emit_signal("item_collected", name, count)` (Godot 3 form) instead of `item_collected.emit(name, count)`.
- `connect` syntax: `connect("item_collected", self, "_on_item_collected")` (Godot 3 string-target form) instead of `item_collected.connect(_on_item_collected)`.
- Invented Godot 4 node methods: `add_child_named`, `get_node_by_path` (vs `get_node` / `$`).
- Tween API confusion: `tween_property(node, "scale", Vector2.ONE, 0.3)` (Godot 3 SceneTreeTween) instead of `create_tween().tween_property(node, "scale", Vector2.ONE, 0.3)`.
- Resource loading: `ResourceLoader.load_resource(...)` (invented) vs `load("res://...")`, `preload`.
- Timer wrong timeout pattern: `Timer.new()` without `add_child`, missing `timeout` signal connect, `set_wait_time` vs `wait_time` direct assignment.
- ConfigFile wrong section API: `set_value("key", value)` (missing section argument) vs `set_value("inventory", "key", value)`, `load(path)` return value not checked, `save()` without path argument.
- Enum wrong syntax: `enum Rarity.COMMON` instead of `enum Rarity { COMMON, ... }`.
- `_input` returning bool, `_input(event)` vs `_unhandled_input(event)` confusion.
- `InputEventKey` checked before `event.is_pressed()` (causes error on non-key events).
- `Texture2D` written as `Texture` (Godot 3 name).
- `@export var rarity: Rarity = COMMON` (missing `Rarity.` qualifier).

## Build

No build needed — GDScript is interpreted by the Godot editor.

## Project skeleton

None needed — single file `inventory.gd`.
