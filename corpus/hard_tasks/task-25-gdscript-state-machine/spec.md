# Task 25 — Godot 4.3 GDScript enemy AI state machine + AnimationTree blend spaces

## Purpose

Third GDScript benchmark task. Differs from task-05 (basic state machine, no
animation, no resources) and task-17 (signals/inventory) by targeting:

- **Concrete AI states**: PatrolState, ChaseState, AttackState (not generic Idle/Run/Jump)
- **AnimationTree + AnimationNodeBlendSpace2D** (complex animation API surface)
- **Resource-based configuration** (EnemyStats with typed exports + `preload`)
- **Signal-based transitions with typed arguments** between states
- **`_physics_process(delta: float)`** + `CharacterBody2D` integration
- **State base class** pattern with `enter`/`exit`/`process`/`physics_process`

Targets hallucination patterns specific to AnimationTree (which neither task-05
nor task-17 exercise) and typed Resource composition.

## Prompt (sent to glm-5-turbo)

> Build a Godot 4.3 GDScript finite state machine for an enemy AI in a single file `enemy_ai.gd`. Define a `StateMachine` node that manages an active state with `transition_to(state_name: StringName, args: Dictionary = {})` method, holds `@export var initial_state: State` and `@onready var states: Dictionary[StringName, State] = {}`. Define a `State` base class (extends `Node`) with virtual methods `enter(args: Dictionary = {}) -> void`, `exit() -> void`, `process(delta: float) -> void`, `physics_process(delta: float) -> void`, with `@onready var state_machine: StateMachine` lookup. Implement three concrete states: `PatrolState` (uses `NavigationAgent2D` for pathing, idle/walk cycle), `ChaseState` (locks onto player `CharacterBody2D`), `AttackState` (cooldown via `Timer`). Define a `Resource` subclass `EnemyStats` with `@export var move_speed: float = 80.0`, `@export var detection_range: float = 200.0`, `@export var attack_damage: int = 10`, `@export var attack_cooldown: float = 1.5`. Declare signals on `PatrolState` (e.g., `signal player_detected(player: Node2D, distance: float)`) and on `ChaseState` (`signal lost_player`), and have `AttackState` emit `signal attack_landed(target: Node2D, damage: int)`. Configure an `AnimationTree` with an `AnimationNodeBlendSpace2D` for blending idle/walk/run based on a velocity vector — set `blend_point_0`/`blend_point_1`/`blend_point_2` and call `animation_tree.set("parameters/Move/blend_position", velocity)` from `_physics_process`. Use `preload("res://enemy_stats.tres")` to load stats or fall back to `EnemyStats.new()`. Wire transitions through the state machine on signal emission (e.g., `player_detected.connect(func(p, d): state_machine.transition_to(&"ChaseState", {"target": p}))`). Include the `Enemy` (extends `CharacterBody2D`) node that owns the `StateMachine`, `AnimationTree`, `NavigationAgent2D`, and `Timer` as children.

## Expected hallucinations

- Signal syntax: `emit_signal("player_detected", player, distance)` (Godot 3) instead of `player_detected.emit(player, distance)`
- `connect` syntax: `connect("player_detected", self, "_on_detected")` (Godot 3 string form) instead of `player_detected.connect(_on_detected)`
- Wrong typed Dictionary: `Dictionary[StringName, State]` (4.3 syntax — may be flagged as invented by LLM unsure of 4.x typed-collection support) OR `Dict[StringName, State]` (invented)
- `AnimationTree` API: `animation_tree.set("parameters/Move/blend_position", velocity)` vs invented `animation_tree.set_blend_position("Move", velocity)` or `animation_tree.blend_position = ...`
- `AnimationNodeBlendSpace2D` setup: inventing `BlendSpace2D.new()` (must be created in editor or via `AnimationNodeBlendSpace2D.new()` with proper resource path), wrong blend point API (`add_blend_point` instead of `blend_point_0/X` property or `_set_blend_point`)
- `_physics_process(delta)` written as `_physics_process(delta: int)` (wrong type) or returning bool
- `@export var states: Dictionary` with type annotation mismatch (typed Dictionary in `@export` is unsupported at export time — must be plain `Dictionary` or unannotated)
- `NavigationAgent2D.get_next_path_position()` vs invented `get_next_position()` / `get_path_next()`
- `CharacterBody2D.velocity` vs `move_and_slide` confusion (calling `move_and_slide(velocity)` — Godot 3 form; Godot 4 takes no args, uses `velocity` property)
- `Resource` subclassing: missing `class_name EnemyStats extends Resource`
- `preload` vs `preloal` typo, OR `preload("enemy_stats.tres")` without `res://` scheme
- `StringName` literal syntax: `transition_to("ChaseState")` instead of `transition_to(&"ChaseState")` (works but inconsistent with declared `StringName` param)
- Invented `state_machine.call("transition_to", ...)` instead of direct method call
- `Timer` not added as child via `add_child(timer)` before `start()`, or `set_wait_time` (Godot 3) vs `wait_time` direct (Godot 4)
- `_onready` instead of `@onready` (Godot 3)

## Build

No build needed — GDScript is interpreted by the Godot editor.

## Project skeleton

None needed — single file `enemy_ai.gd`.
