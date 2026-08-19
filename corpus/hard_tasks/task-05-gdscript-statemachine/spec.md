# Task 05 — GDScript Godot 4.3 state machine

## Prompt (sent to qwen2.5-coder:7b)

> Create a Godot 4.3 state machine system. Base State class with enter(), exit(), update(delta), handle_input(event). StateMachine node that manages current state, transitions, and state history. Example PlayerState implementation with Idle, Run, Jump states. Use typed arrays and @export annotations.

## Expected hallucinations

- Godot 3 API in Godot 4 (`get_node()` vs `$`, `set_physics_process` vs `set_physics_process_internal`)
- Wrong node methods (mixing `Node2D` with `CharacterBody2D` APIs)
- Fabricated signal patterns (`emit_signal("state_changed")` instead of `state_changed.emit()`)
- `_physics_process` vs `_process` confusion
- `@onready` vs `onready` (Godot 3)
- `@export` typed arrays wrong syntax (`@export var states: Array[State] = []`)
- `_get_configuration_warning` removed in Godot 4

## Build

Manual review — no GDScript build system. Check syntactic correctness and Godot 4 API usage.
