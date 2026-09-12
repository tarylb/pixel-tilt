# pixel-tilt

A tilt-maze game for a 64x32 RGB LED matrix, controlled by tilting a potentiometer knob. Roll the ball to the green goal (or into a bonus hole for points) while dodging spinning bars, avoiding walls, and riding ramps.

## Hardware

- Feather RP2040
- Adafruit 64x32 RGB Matrix FeatherWing (6mm pitch)
- Potentiometer: wiper on `A0`, outer legs on `3.3V` and `GND`
- Two buttons, both wired to `GND` with internal pull-ups:
  - Left button on `SCK`
  - Right button on `A1`
  - Press both together to select in menus

## Repo layout

- [circuitpython/code.py](circuitpython/code.py) — the game itself (runs on the board)
- [circuitpython/boot.py](circuitpython/boot.py) — grants the board write access to save progress on normal power-up; hold the left button while plugging in power to keep the drive writable from your computer instead
- [circuitpython/levels_data.py](circuitpython/levels_data.py) — level layouts, edited via the level editor rather than by hand
- [circuitpython/lib/](circuitpython/lib/) — CircuitPython libraries required on the board (`adafruit_display_text`)
- [tools/level_editor.py](tools/level_editor.py) — desktop pygame tool for building and playtesting levels

## Setting up the board

1. Copy `circuitpython/code.py`, `circuitpython/boot.py`, `circuitpython/levels_data.py`, and the contents of `circuitpython/lib/` onto `CIRCUITPY`.
2. Power up normally to play — progress is saved to `/progress.txt` on the board.
3. To edit files from your computer again, hold the left button while plugging in power (progress won't save during that session).

## Level editor

Run with:

```
python tools/level_editor.py
```

Requires `pygame`. It's a plain desktop tool (not CircuitPython) that shares the same geometry and physics code as the on-device game, so what you build and playtest here matches what runs on the board.

**Mode**
- `Tab` — toggle Edit / Play

**Edit mode**
- `1`–`7` — select tool: 1 Wall, 2 Ramp, 3 Goal, 4 Ball start, 5 Spinner, 6 Eraser, 7 Hole (press again to cycle its point value 1/2/3)
- Left-drag — paint with Wall / Goal / Eraser
- Left-click — Ramp: click a start point, then an end point; Ball: sets start position; Spinner/Hole: adds a new one
- Right-click — remove the nearest spinner or hole to the cursor
- `C` — clear the current level
- `N` — new blank level, added after the current one
- `[` / `]` — previous / next level
- `Delete` — delete the current level (if more than one exists)
- `Shift+1`/`2`/`3` — reset the current level to a built-in template
- `S` — save all levels to `circuitpython/levels_data.py`
- `L` — open a file picker to load a `levels_data.py` file

**Play mode**
- Mouse or arrows/`A`/`D` — tilt
- `[` / `]` — previous / next level
- `R` — restart at the start point
- `Esc` / close window — quit

Holes are alternate goals: reaching one ends the level and adds its point value to your score.
