# pixel-tilt

A tilt-maze game for a 64x32 RGB LED matrix, controlled by tilting a potentiometer knob. Roll the ball to the green goal while dodging spinning bars and rolling off walls built into slopes.

## Hardware

- Feather RP2040
- Adafruit 64x32 RGB Matrix FeatherWing (6mm pitch)
- Potentiometer: wiper on `A0`, outer legs on `3.3V` and `GND`
- Two buttons, both wired to `GND` with internal pull-ups:
  - Left button on `SCK`
  - Right button on `A1`
  - Press both together to select in menus

## Repo layout

- [circuitpython/code.py](circuitpython/code.py) — the game itself: hardware setup, persistence, and the main loop (runs on the board)
- [circuitpython/menus.py](circuitpython/menus.py) — button input handling and all the menu/UI screens (main menu, level select, calibration, brightness, reset confirmation), imported by `code.py`
- [circuitpython/physics.py](circuitpython/physics.py) — the shared collision/physics engine, imported by both `code.py` and the level editor so a physics change only has to be made once
- [circuitpython/boot.py](circuitpython/boot.py) — grants the board write access to save progress on normal power-up; hold the left button while plugging in power to keep the drive writable from your computer instead
- [circuitpython/levels_data.json](circuitpython/levels_data.json) — level layouts, edited via the level editor rather than by hand
- [circuitpython/lib/](circuitpython/lib/) — CircuitPython libraries required on the board (`adafruit_display_text`)
- [tools/level_editor.py](tools/level_editor.py) — desktop pygame tool for building and playtesting levels

## Setting up the board

1. Copy `circuitpython/code.py`, `circuitpython/menus.py`, `circuitpython/physics.py`, `circuitpython/boot.py`, `circuitpython/levels_data.json`, and the contents of `circuitpython/lib/` onto `CIRCUITPY`.
2. Power up normally to play — progress is saved to `/progress.txt` on the board.
3. To edit files from your computer again, hold the **left** button while plugging in power (progress won't save during that session).
4. To play with every level unlocked without touching your saved progress or best times at all, hold the **right** button while plugging in power instead — nothing from this session is ever written to disk, so normal-mode progress is completely unaffected.

## Level editor

Run with:

```
python tools/level_editor.py
```

Requires `pygame` and `pygame_gui`. It's a plain desktop tool (not CircuitPython) that imports `circuitpython/physics.py`, the same collision/physics engine the on-device game uses, so what you build and playtest here matches what runs on the board.

**Mode**
- `Tab` — toggle Edit / Play

**Top menu bar (both modes)**
- **File** — Save (to wherever was last opened/saved-as, no prompt); Save As... (always prompts, and that becomes the new "last" path); Open...
- **Edit** — Undo / Redo (also `Ctrl+Z` / `Ctrl+Shift+Z`), scoped to the level currently open — switching levels, loading a file, New, or Delete all clear the history. A single Wall/Goal/Eraser drag undoes as one step, not one per pixel.
- **Level** — New, Delete, Clear (wipes the current level but keeps it in the list), and a "Go to Level N" entry per level that exists
- **Play button** — toggles Play/Edit (same as `Tab`); relabels itself "Edit" while playing, and the tool panel hides during Play since it isn't relevant there

**Right-side panel (edit mode)**
- Tool buttons — Wall, Wall Line, Goal, Ball start, Spinner, Eraser. There's no separate ramp type — a diagonal or staircase-shaped run of wall cells naturally acts like a slope, since the physics derives how much to roll (vs. bounce) a wall contact from how diagonal the local wall surface actually is, not from anything stored per cell. In practice this works well for steep runs (very roughly steeper than ~35°); much shallower/gentler diagonal runs can cause the ball to get stuck against them, since the ball is larger than the individual steps of a shallow staircase — worth testing a shallow slope before relying on it in a level.
- Parameter controls for whichever tool is selected — Wall Line: thickness slider; Spinner: half-length, speed, and starting-angle sliders, plus a Direction button toggling clockwise/counterclockwise. Each new line/spinner picks up whatever the panel is currently set to.
- Hovering the maze with Ball start / Spinner selected shows a dimmed preview of what clicking there would place; with Wall Line, once you've clicked the first point, hovering shows the line that a second click would paint.

**Edit mode**
- Left-drag — paint with Wall / Goal; with Eraser, clears whatever's directly under the cursor — a wall/goal cell, or a spinner if it touches its bar (a wall-line-drawn diagonal run is just more wall, erased pixel by pixel like any other)
- Left-click — Wall Line: click a start point, then an end point to paint a straight line of wall cells; Ball: sets start position; Spinner: adds a new one using the panel's current parameters

**Play mode**
- Mouse or arrows/`A`/`D` — tilt
- `[` / `]` — previous / next level
- `R` — restart at the start point
- `Esc` / close window — quit
