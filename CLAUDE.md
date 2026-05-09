# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

The pet system runs as **three independent Python processes** that communicate through files:

```
hooks (Claude Code events)
  │
  ├─→ pet_bridge.py ──HTTP──→ pet_daemon.py ──writes──→ pet_state.json
  │                              (port 19876)
  │
  └─→ statusline command ──stdin──→ pet_statusline.py ──writes──→ pet_config.json

desktop_pet.py  ←── polls ──  pet_state.json (150ms)
                ←── mtime ──  pet_config.json (event-driven)
```

- **`pet_daemon.py`** — HTTP server on `127.0.0.1:19876`. Receives state commands via `POST /set`, enforces a 300ms minimum dwell before allowing state changes, writes `pet_state.json`.
- **`desktop_pet.py`** — tkinter transparent window. Polls `pet_state.json` every 150ms for animation state; checks `pet_config.json` mtime for context bar updates. Must receive `--atlas` and optionally `--manifest`.
- **`pet_launch.py`** — Orchestrator. Starts daemon (if not running), then spawns `desktop_pet.py` with the active pet from `pet_config.json`. Also runs `install_hooks.py` on first launch.
- **`pet_bridge.py`** — CLI for hooks. Sends HTTP requests to the daemon to change pet state.
- **`pet_statusline.py`** — Reads Claude Code statusline JSON from stdin, extracts `context_window.context_window_size` and `context_window.current_usage` (a dict of token type → count), sums usage values, writes to `pet_config.json`.

### State files (in `~/.claude/`)

| File | Writer | Reader | Purpose |
|------|--------|--------|---------|
| `pet_state.json` | daemon | desktop_pet.py | Animation state + bubble text |
| `pet_config.json` | statusline, desktop_pet.py | desktop_pet.py | Context tokens + user prefs (sound, agree, active pet) |

### State aliases

Chat states from hooks map to animation states via `STATE_ALIASES` in `pet_constants.py`:

| Hook state | Animation |
|------------|-----------|
| `thinking` | `waiting` |
| `coding` / `writing` | `running` |
| `debugging` | `failed` |
| `reading` | `review` |
| `searching` | `running-right` |
| `agree` | `waving` + agree button |

## Path conventions

The skill lives at `~/.claude/skills/claude-pet/`. The SKILL.md references `~/.workbuddy/skills/workbuddy-pet/` — this is stale; the actual install path is under `~/.claude/skills/claude-pet/`. When updating hooks or config, use `~/.claude/` paths.

## Commands

```bash
# Launch / restart the pet
python scripts/pet_launch.py

# Kill all Python processes (Windows), then restart
taskkill /F /IM python.exe && python scripts/pet_launch.py

# Install hooks into ~/.claude/settings.json (idempotent)
python scripts/install_hooks.py

# Uninstall hooks
python scripts/install_hooks.py --uninstall

# Generate a pet atlas from per-state GIFs
python scripts/compose_gif_atlas.py --gifs-dir <dir> --output <dir> --name <name>

# Validate an atlas against the spec
python scripts/validate_atlas.py --atlas <atlas.png> --manifest <pet.json>

# Manual state control (for debugging)
python scripts/pet_bridge.py thinking "thinking..."
python scripts/pet_bridge.py running "working..."
python scripts/pet_bridge.py idle
```

## Sprite atlas spec

- Grid: 8 columns × 9 rows, each cell 192×208 px, atlas 1536×1872 px, PNG with alpha
- Rows 0-8: idle, running-right, running-left, waving, jumping, failed, waiting, running, review
- 8 frames per state; per-frame durations supported via `durations` array in manifest
- Manifest: `pet.json` with `{"states": [{"name": "...", "row": N, "frames": 8, "fps": N}]}`

## Context bar

The bar at the bottom of the pet window shows `used/total` tokens with a colored fill (green <60%, amber 60-85%, red >85%). Updated event-driven:

1. `_poll_chat_state()` (150ms loop) checks `pet_config.json` mtime — redraws if file changed.
2. When chat state transitions to `idle` or `waving`, schedules a final redraw after 1 second.

`pet_statusline.py` must be configured as the Claude Code `statusLine.type: "command"` in `settings.json`. It expects the stdin JSON format: `context_window.context_window_size` (int) and `context_window.current_usage` (dict of token type → int).

## Hooks configuration

Hooks are defined in `hooks.json` and installed into `~/.claude/settings.json` by `install_hooks.py`. They cover: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop`, `SessionEnd`. The `statusLine` config is separate and must be set manually or by the installer.
