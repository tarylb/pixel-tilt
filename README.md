# Pixel Tilt

A tilt-maze game for a 64x32 RGB LED matrix, controlled by tilting the LED matrix panel. Roll the ball to the green goal while dodging spinning bars, spikes, and rolling off walls and slopes.

## Hardware

- [Adafruit Feather RP2040](https://www.adafruit.com/product/4884)
- [Adafruit RGB Matrix Featherwing Kit - For RP2040, M0 and M4 Feathers](https://www.adafruit.com/product/3036)
- 64x32 RGB LED Matrix Panel (HUB75)
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
- **Edit** — Undo / Redo (also `Ctrl+Z` / `Ctrl+Shift+Z`), scoped to the level currently open — switching levels, loading a file, New, or Delete all clear the history. A single Wall/Goal/Spike/Eraser drag undoes as one step, not one per pixel.
- **Level** — New, Delete, Clear (wipes the current level but keeps it in the list), and a "Go to Level N" entry per level that exists
- **Play button** — toggles Play/Edit (same as `Tab`); relabels itself "Edit" while playing, and the tool panel hides during Play since it isn't relevant there

**Right-side panel (edit mode)**
- Tool buttons — Wall, Wall Line, Goal, Spike, Ball start, Spinner, Eraser. Walls are continuous line segments, not pixel cells — there's no separate ramp type, and no grid-approximation limits on how shallow a slope can be: a wall at any angle rolls the ball at that exact angle (the physics reflects/rolls off the wall's true geometric angle), smoothly at any steepness. Spikes don't block movement like a wall — touching one kills the ball and respawns it at the start, same as falling off the edge.
- Parameter controls for whichever tool is selected — Spinner: half-length, speed, and starting-angle sliders, plus a Direction button toggling clockwise/counterclockwise. Each new spinner picks up whatever the panel is currently set to. Walls all share one fixed thickness.
- Hovering the maze with Ball start / Spinner selected shows a dimmed preview of what clicking there would place; with Wall Line, once you've clicked the first point, hovering shows the line that a second click would create.

**Edit mode**
- Left-drag — Wall: freehand-draws a connected line (a polyline) following the cursor, one new wall per drag; paint with Goal / Spike; with Eraser, clears whatever's directly under the cursor — a whole wall (any point along it) or spinner (if it touches its bar) as one object, or a single goal/spike cell
- Left-click — Wall Line: click a start point, then an end point to create a straight wall segment between them; Ball: sets start position; Spinner: adds a new one using the panel's current parameters

**Play mode**
- Mouse or arrows/`A`/`D` — tilt
- `[` / `]` — previous / next level
- `R` — restart at the start point
- `Esc` / close window — quit
