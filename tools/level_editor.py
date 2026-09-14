"""
Desktop editor + playtester for the tilt-maze levels -- supports any
number of levels, unlimited spinners, and point-value holes, saving/
loading level data to levels_data.py.

This is NOT the CircuitPython game itself -- it's a plain-Python/
pygame tool for building and playtesting level layouts. It imports
circuitpython/physics.py, the same collision/physics engine the device
game uses, so what you build and playtest here matches what runs on the
board. The device game maintains its own code separately and just
imports whatever LEVELS_DATA you save here.

MODE
  Tab             -- toggle between Edit and Play

EDIT MODE
  1..7            -- tool: 1 Wall, 2 Ramp, 3 Goal, 4 Ball start,
                     5 Spinner, 6 Eraser, 7 Hole (press 7 again while
                     already selected to cycle its point value 1/2/3)
  Left-drag       -- paint with Wall / Goal / Eraser
  Left-click      -- Ramp: click a start point, then an end point
                   -- Ball: sets the ball's start position
                   -- Spinner / Hole: adds a NEW one at that point
                     (no limit on how many of either)
  Right-click     -- remove the nearest spinner or hole to the cursor
  C               -- clear the current level (keeps it in the list)
  N               -- new blank level, added after the current one
  [ / ]           -- previous / next level
  Delete          -- delete the current level (if more than one exists)
  Shift+1/2/3     -- reset the CURRENT level to one of the 3 built-in templates
  S               -- save all levels to circuitpython/levels_data.py
  L               -- open a file picker to load a levels_data.py file

PLAY MODE
  Mouse (or arrows/AD) -- tilt
  [ / ]                -- previous / next level
  R                     -- restart at the current start point
  Esc / close window    -- quit

Holes are alternate goals: reaching one ends the level (same as the
green goal) and adds its point value to your score. Score resets on a
fresh attempt (R, entering Play, or switching levels) but not just
from rolling off the screen.
"""

import os
import sys
import subprocess
import math
import pygame

# Resolve circuitpython/ relative to this script's own location (tools/
# level_editor.py -> ../circuitpython), both to import the shared physics
# module from there and to locate levels_data.py below.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CIRCUITPYTHON_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "circuitpython"))
sys.path.insert(0, CIRCUITPYTHON_DIR)

from physics import (  # noqa: E402 -- must follow the sys.path insert above
    WIDTH, HEIGHT, BALL_RADIUS, HOLE_RADIUS,
    static_color, ramp_code, bar_segments,
    idx, set_cell, clear_cell, clear_level, ramp_steepness_for_width,
    apply_cells, circle_outline_pixels, ball_pixels_at, bar_pixels_for_draw,
    update_bar_segments, step_ball, update_ball_roll, ball_accent_pixels_at,
)

# This is still where Save (S) writes to -- only Load (L) now prompts
# with a file picker instead of assuming this path.
DEFAULT_LEVELS_PATH = os.path.join(CIRCUITPYTHON_DIR, "levels_data.py")


def pick_load_file():
    """Open a native file picker and return the chosen path, or '' if
    the user cancelled or something went wrong.

    This runs tkinter's dialog in a completely separate subprocess
    rather than importing tkinter directly into this process. On
    macOS, pygame/SDL and tkinter each try to claim the same native
    Cocoa application context -- creating a Tk window in the same
    process pygame is already running in causes a hard crash
    ('SDLApplication _setup:' NSInvalidArgumentException). Running it
    in its own process gives it a separate, clean application context
    and sidesteps that entirely; this also works fine on Windows/Linux."""
    initial_dir = os.path.dirname(DEFAULT_LEVELS_PATH)
    if not os.path.isdir(initial_dir):
        initial_dir = SCRIPT_DIR

    script = (
        "import tkinter, tkinter.filedialog as fd\n"
        "root = tkinter.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "path = fd.askopenfilename(\n"
        "    title='Load level data',\n"
        f"    initialdir={initial_dir!r},\n"
        "    filetypes=[('Python files', '*.py'), ('All files', '*.*')],\n"
        ")\n"
        "root.destroy()\n"
        "print(path)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return result.stdout.strip()
    except Exception:
        return ""


SCALE = 12

COLORS = {
    0: (10, 10, 14),
    1: (255, 51, 0),
    2: (34, 102, 255),
    3: (0, 255, 0),
    4: (170, 0, 255),
    5: (255, 255, 0),   # hole worth 1 point
    6: (255, 140, 0),   # hole worth 2 points
    7: (255, 0, 255),   # hole worth 3 points
    8: (255, 255, 255),  # ball roll accent -- a bright star/sparkle against the red ball
}
BALL_ROLL_ACCENT_COLOR = 8


def in_bounds(x, y):
    return 0 <= x < WIDTH and 0 <= y < HEIGHT


def add_border():
    for x in range(WIDTH):
        set_cell(x, 0, wall=True)
        set_cell(x, HEIGHT - 1, wall=True)
    for y in range(HEIGHT):
        set_cell(0, y, wall=True)


def add_ramp(x_start, x_end, y_start, y_end, thickness=1):
    if x_start > x_end:
        x_start, x_end = x_end, x_start
        y_start, y_end = y_end, y_start
    direction = 1 if y_end > y_start else (-1 if y_end < y_start else 0)
    dx = x_end - x_start
    dy = y_end - y_start
    if dx == 0:
        for dyi in range(thickness):
            y = y_start + dyi
            if 0 <= y < HEIGHT:
                set_cell(x_start, y, ramp_dir=direction or 1, steepness=1.0)
        return

    width = abs(dx) / max(1, abs(dy))
    steepness = ramp_steepness_for_width(width)

    if abs(dy) <= abs(dx):
        # Shallow-ish: step along x, painting a vertical thickness-band per
        # column -- consecutive columns' bands always overlap here since
        # the y-step per column is at most 1.
        for x in range(x_start, x_end + 1):
            t = (x - x_start) / dx
            ramp_y = int(round(y_start + t * dy))
            for dyi in range(thickness):
                y = ramp_y + dyi
                if 0 <= y < HEIGHT:
                    set_cell(x, y, ramp_dir=direction, steepness=steepness)
    else:
        # Steep: stepping along x would skip rows faster than `thickness`
        # can bridge, leaving gaps -- step along y instead (like a proper
        # line-drawing algorithm choosing the larger-delta axis) so
        # consecutive rows' horizontal bands always overlap instead.
        y0, y1 = (y_start, y_end) if y_end >= y_start else (y_end, y_start)
        for y in range(y0, y1 + 1):
            t = (y - y_start) / dy
            ramp_x = int(round(x_start + t * dx))
            for dxi in range(thickness):
                x = ramp_x + dxi
                if 0 <= x < WIDTH:
                    set_cell(x, y, ramp_dir=direction, steepness=steepness)


def add_lanes_and_goal(lane_x_start):
    for x in range(lane_x_start, WIDTH):
        set_cell(x, 10, wall=True)
        set_cell(x, 11, wall=True)
        set_cell(x, 20, wall=True)
        set_cell(x, 21, wall=True)
    for y in range(12, 20):
        for x in range(61, WIDTH):
            set_cell(x, y, goal=True)


def make_spinner(px, py, half_len=8, speed=0.04):
    return {"pivot_x": float(px), "pivot_y": float(py), "half_len": half_len,
            "angle": 0.0, "speed": speed}


def make_hole(px, py, value=1):
    return {"x": float(px), "y": float(py), "value": value}


# ---------- The 3 built-in templates (used as starting points) ----------
def build_level_1():
    clear_level()
    add_border()
    add_ramp(6, 19, 27, 2)
    add_lanes_and_goal(34)
    return 25.0, 6.0, [], []


def build_level_2():
    clear_level()
    for cx in range(0, 64): set_cell(cx, 0, wall=True)
    set_cell(0, 1, wall=True); set_cell(0, 2, wall=True); set_cell(0, 3, wall=True)
    set_cell(0, 4, wall=True); set_cell(0, 5, wall=True); set_cell(8, 5, ramp_dir=1)
    set_cell(0, 6, wall=True); set_cell(8, 6, ramp_dir=1)
    set_cell(0, 7, wall=True); set_cell(8, 7, ramp_dir=1); set_cell(9, 7, ramp_dir=1)
    set_cell(0, 8, wall=True); set_cell(9, 8, ramp_dir=1); set_cell(41, 8, ramp_dir=1)
    set_cell(0, 9, wall=True); set_cell(9, 9, ramp_dir=1); set_cell(10, 9, ramp_dir=1); set_cell(41, 9, ramp_dir=1)
    set_cell(0, 10, wall=True); set_cell(10, 10, ramp_dir=1); set_cell(41, 10, ramp_dir=1)
    for cx in range(58, 64): set_cell(cx, 10, wall=True)
    set_cell(0, 11, wall=True); set_cell(10, 11, ramp_dir=1); set_cell(29, 11, ramp_dir=-1); set_cell(42, 11, ramp_dir=1)
    for cx in range(58, 64): set_cell(cx, 11, wall=True)
    set_cell(0, 12, wall=True); set_cell(11, 12, ramp_dir=1); set_cell(29, 12, ramp_dir=-1); set_cell(42, 12, ramp_dir=1)
    for cx in range(61, 64): set_cell(cx, 12, goal=True)
    set_cell(0, 13, wall=True); set_cell(11, 13, ramp_dir=1); set_cell(28, 13, ramp_dir=-1); set_cell(29, 13, ramp_dir=-1); set_cell(42, 13, ramp_dir=1)
    for cx in range(61, 64): set_cell(cx, 13, goal=True)
    set_cell(0, 14, wall=True); set_cell(11, 14, ramp_dir=1); set_cell(12, 14, ramp_dir=1); set_cell(28, 14, ramp_dir=-1)
    for cx in range(61, 64): set_cell(cx, 14, goal=True)
    set_cell(0, 15, wall=True); set_cell(12, 15, ramp_dir=1); set_cell(27, 15, ramp_dir=-1); set_cell(28, 15, ramp_dir=-1)
    for cx in range(61, 64): set_cell(cx, 15, goal=True)
    set_cell(0, 16, wall=True); set_cell(12, 16, ramp_dir=1); set_cell(27, 16, ramp_dir=-1)
    for cx in range(61, 64): set_cell(cx, 16, goal=True)
    set_cell(0, 17, wall=True); set_cell(13, 17, ramp_dir=1); set_cell(27, 17, ramp_dir=-1)
    for cx in range(61, 64): set_cell(cx, 17, goal=True)
    set_cell(0, 18, wall=True); set_cell(13, 18, ramp_dir=1); set_cell(26, 18, ramp_dir=-1)
    for cx in range(61, 64): set_cell(cx, 18, goal=True)
    set_cell(0, 19, wall=True); set_cell(13, 19, ramp_dir=1); set_cell(26, 19, ramp_dir=-1)
    for cx in range(61, 64): set_cell(cx, 19, goal=True)
    set_cell(0, 20, wall=True); set_cell(25, 20, ramp_dir=-1); set_cell(26, 20, ramp_dir=-1)
    for cx in range(58, 64): set_cell(cx, 20, wall=True)
    set_cell(0, 21, wall=True); set_cell(25, 21, ramp_dir=-1)
    for cx in range(58, 64): set_cell(cx, 21, wall=True)
    set_cell(0, 22, wall=True); set_cell(24, 22, ramp_dir=-1); set_cell(25, 22, ramp_dir=-1)
    set_cell(0, 23, wall=True); set_cell(24, 23, ramp_dir=-1)
    set_cell(0, 24, wall=True); set_cell(24, 24, ramp_dir=-1)
    set_cell(0, 25, wall=True); set_cell(0, 26, wall=True); set_cell(0, 27, wall=True)
    set_cell(0, 28, wall=True); set_cell(0, 29, wall=True); set_cell(0, 30, wall=True)
    for cx in range(0, 64): set_cell(cx, 31, wall=True)
    return 5.0, 11.0, [], []


def build_level_3():
    clear_level()
    for cx in range(0, 64): set_cell(cx, 0, wall=True)
    for y in range(1, 10): set_cell(0, y, wall=True)
    set_cell(0, 10, wall=True)
    for cx in range(34, 64): set_cell(cx, 10, wall=True)
    set_cell(0, 11, wall=True)
    for cx in range(34, 64): set_cell(cx, 11, wall=True)
    for y in range(12, 20):
        set_cell(0, y, wall=True)
        for cx in range(61, 64): set_cell(cx, y, goal=True)
    set_cell(0, 20, wall=True)
    for cx in range(34, 64): set_cell(cx, 20, wall=True)
    set_cell(0, 21, wall=True)
    for cx in range(34, 64): set_cell(cx, 21, wall=True)
    for y in range(22, 31): set_cell(0, y, wall=True)
    for cx in range(0, 64): set_cell(cx, 31, wall=True)
    return 3.0, 16.0, [make_spinner(21, 11, 8, 0.04)], []


# ---------- pygame setup ----------
pygame.init()
screen = pygame.display.set_mode((WIDTH * SCALE, HEIGHT * SCALE + 40))
pygame.display.set_caption("Tilt Maze -- Level Editor")
clock = pygame.time.Clock()
font = pygame.font.SysFont(None, 28)
small_font = pygame.font.SysFont(None, 18)

TOOL_NAMES = {1: "Wall", 2: "Ramp", 3: "Goal", 4: "Ball start", 5: "Spinner", 6: "Eraser", 7: "Hole"}

# ---------- Multi-level state ----------
# Each entry: {"start": (x, y), "cells": [(x0, x1, y, kind), ...],
#              "spinners": [{...}, ...], "holes": [{...}, ...]}
levels_data = []
current_level_idx = 0

current_start_x, current_start_y = 25.0, 6.0
ball_start_set = False  # True once a ball start has actually been placed/loaded
spinners = []
holes = []
mode = "edit"
tool = 1
ramp_click_start = None
hole_value_selector = 1
score = 0

ball_x = ball_y = vel_x = vel_y = 0.0
ball_rotation = 0.0
ball_roll_direction = (1.0, 0.0)
won = False
win_timer = 0.0
status_message = ""
status_message_timer = 0.0


def set_status(msg, persistent=False):
    global status_message, status_message_timer
    status_message = msg
    status_message_timer = float("inf") if persistent else 2.5


def snapshot_current_level():
    cells = []
    for y in range(HEIGHT):
        x = 0
        while x < WIDTH:
            sc = static_color[idx(x, y)]
            if sc == 0:
                x += 1
                continue
            if sc == 2:
                rc = ramp_code[idx(x, y)]
                kind = 1 if rc == 0 else (2 if rc == 1 else 3)
                x2 = x
                while x2 + 1 < WIDTH and static_color[idx(x2 + 1, y)] == 2 and ramp_code[idx(x2 + 1, y)] == rc:
                    x2 += 1
            else:  # sc == 3, goal
                kind = 4
                x2 = x
                while x2 + 1 < WIDTH and static_color[idx(x2 + 1, y)] == 3:
                    x2 += 1
            cells.append((x, x2, y, kind))
            x = x2 + 1
    sps = [{"pivot_x": s["pivot_x"], "pivot_y": s["pivot_y"], "half_len": s["half_len"], "speed": s["speed"]} for s in spinners]
    hls = [{"x": h["x"], "y": h["y"], "value": h["value"]} for h in holes]
    return {"start": (current_start_x, current_start_y), "cells": cells, "spinners": sps, "holes": hls}


def apply_level_snapshot(data):
    global current_start_x, current_start_y, spinners, holes, ball_start_set
    clear_level()
    apply_cells(data["cells"])
    current_start_x, current_start_y = data["start"]
    ball_start_set = True
    spinners = [make_spinner(s["pivot_x"], s["pivot_y"], s.get("half_len", 8), s.get("speed", 0.04)) for s in data["spinners"]]
    holes = [make_hole(h["x"], h["y"], h.get("value", 1)) for h in data.get("holes", [])]


def goto_level(new_idx, save_first=True):
    global current_level_idx, score
    if not levels_data:
        return
    if save_first:
        levels_data[current_level_idx] = snapshot_current_level()
    current_level_idx = new_idx % len(levels_data)
    apply_level_snapshot(levels_data[current_level_idx])
    score = 0
    restart_ball()


def new_level():
    global current_level_idx, ball_start_set
    if levels_data:
        levels_data[current_level_idx] = snapshot_current_level()
    else:
        # Nothing in the list yet -- whatever's currently on the canvas
        # (even if it's just the blank default) becomes level 1, rather
        # than being discarded.
        levels_data.append(snapshot_current_level())
    blank = {"start": (32.0, 16.0), "cells": [], "spinners": [], "holes": []}
    levels_data.append(blank)
    current_level_idx = len(levels_data) - 1
    apply_level_snapshot(blank)
    ball_start_set = False  # a new level starts with no ball placed yet
    add_border()
    set_status(f"New level {current_level_idx + 1} -- place a ball start (4)")


def delete_current_level():
    global current_level_idx
    if len(levels_data) <= 1:
        set_status("Can't delete the only level")
        return
    del levels_data[current_level_idx]
    current_level_idx = min(current_level_idx, len(levels_data) - 1)
    apply_level_snapshot(levels_data[current_level_idx])
    set_status(f"Deleted -- now {len(levels_data)} level(s)")


def reset_current_to_template(n):
    global current_start_x, current_start_y, spinners, holes, ball_start_set
    fn = (build_level_1, build_level_2, build_level_3)[n]
    sx, sy, sps, hls = fn()
    current_start_x, current_start_y = sx, sy
    ball_start_set = True
    spinners = sps
    holes = hls
    set_status(f"Reset to built-in template {n + 1}")


def save_levels_to_file(path=None):
    if path is None:
        path = DEFAULT_LEVELS_PATH
    if levels_data:
        levels_data[current_level_idx] = snapshot_current_level()
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(path, "w") as f:
        f.write("# Level data for the tilt maze -- edit with pygame_test.py.\n")
        f.write("LEVELS_DATA = ")
        f.write(repr(levels_data))
        f.write("\n")
    msg = f"Saved {len(levels_data)} levels -- copy just {path} to the board, no need to reflash the game"
    set_status(msg)
    print(msg)


def load_levels_from_file(path=None):
    if path is None:
        path = DEFAULT_LEVELS_PATH
    global levels_data, current_level_idx
    ns = {}
    exec(compile(open(path).read(), path, "exec"), ns)
    levels_data = ns["LEVELS_DATA"]
    for lvl in levels_data:
        lvl.setdefault("holes", [])
    current_level_idx = 0
    if levels_data:
        apply_level_snapshot(levels_data[0])
    else:
        clear_level()
        spinners.clear()
        holes.clear()
    restart_ball()
    set_status(f"Loaded {len(levels_data)} levels from {path}")
    print(f"Loaded {len(levels_data)} levels from {path}")


def cell_from_mouse(pos):
    return pos[0] // SCALE, pos[1] // SCALE


def nearest_removable(px, py):
    """Return ('spinner'|'hole', obj) for whichever is nearest, or None."""
    best = None
    best_d = None
    for s in spinners:
        d = (s["pivot_x"] - px) ** 2 + (s["pivot_y"] - py) ** 2
        if best_d is None or d < best_d:
            best_d, best = d, ("spinner", s)
    for h in holes:
        d = (h["x"] - px) ** 2 + (h["y"] - py) ** 2
        if best_d is None or d < best_d:
            best_d, best = d, ("hole", h)
    return best


def restart_ball():
    global ball_x, ball_y, vel_x, vel_y, ball_rotation, ball_roll_direction
    ball_x, ball_y = current_start_x, current_start_y
    vel_x, vel_y = 0.0, 0.0
    ball_rotation = 0.0
    ball_roll_direction = (1.0, 0.0)


def draw_pixel_cells(pixels, color):
    for x, y in pixels:
        pygame.draw.rect(screen, color, (x * SCALE, y * SCALE, SCALE - 1, SCALE - 1))


GRID_COLOR = (45, 45, 52)
HOVER_COLOR = (255, 255, 255)


def draw_grid():
    for x in range(WIDTH + 1):
        px = x * SCALE
        pygame.draw.line(screen, GRID_COLOR, (px, 0), (px, HEIGHT * SCALE))
    for y in range(HEIGHT + 1):
        py = y * SCALE
        pygame.draw.line(screen, GRID_COLOR, (0, py), (WIDTH * SCALE, py))


def draw_hover_highlight():
    pos = pygame.mouse.get_pos()
    if pos[1] >= HEIGHT * SCALE:
        return
    x, y = cell_from_mouse(pos)
    if not in_bounds(x, y):
        return
    pygame.draw.rect(screen, HOVER_COLOR, (x * SCALE, y * SCALE, SCALE, SCALE), 1)


def draw_board():
    screen.fill(COLORS[0])
    for y in range(HEIGHT):
        for x in range(WIDTH):
            c = static_color[idx(x, y)]
            if c:
                pygame.draw.rect(screen, COLORS[c], (x * SCALE, y * SCALE, SCALE - 1, SCALE - 1))
    for seg in bar_segments:
        draw_pixel_cells(bar_pixels_for_draw(seg), COLORS[4])
    for h in holes:
        draw_pixel_cells(circle_outline_pixels(h["x"], h["y"], HOLE_RADIUS), COLORS[4 + h["value"]])
        center = (int(h["x"] * SCALE), int(h["y"] * SCALE))
        num_surf = font.render(str(h["value"]), True, (255, 255, 255))
        outline_surf = font.render(str(h["value"]), True, (0, 0, 0))
        for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, -2), (-2, 2), (2, 2)):
            oc_rect = outline_surf.get_rect(center=(center[0] + ox, center[1] + oy))
            screen.blit(outline_surf, oc_rect)
        num_rect = num_surf.get_rect(center=center)
        screen.blit(num_surf, num_rect)


def draw_hud():
    bar = pygame.Rect(0, HEIGHT * SCALE, WIDTH * SCALE, 40)
    pygame.draw.rect(screen, (20, 20, 24), bar)
    level_info = f"Level {current_level_idx + 1}/{len(levels_data)}" if levels_data else "No levels yet"
    if mode == "edit":
        tool_label = f"Hole ({hole_value_selector} pt)" if tool == 7 else TOOL_NAMES[tool]
        text = f"EDIT {level_info} -- {tool_label}  1-7=tool N=new [ ]=lvl Del=del C=clr  S=save  L=load"
    else:
        text = f"PLAY {level_info} -- mouse/arrows tilt  [ ]=lvl  R=restart  Tab=edit"
    label_surf = small_font.render(text, True, (200, 200, 200))
    screen.blit(label_surf, (4, HEIGHT * SCALE + 6))
    if status_message_timer > 0:
        msg_surf = small_font.render(status_message, True, (120, 255, 120))
        screen.blit(msg_surf, (4, HEIGHT * SCALE + 22))


def countdown():
    for text in ("3", "2", "1", "GO!"):
        pump_events()
        update_bar_segments(spinners)
        draw_board()
        draw_hud()
        label_surf = font.render(text, True, (255, 255, 255))
        rect = label_surf.get_rect(center=(WIDTH * SCALE // 2, HEIGHT * SCALE // 2))
        screen.blit(label_surf, rect)
        pygame.display.flip()
        pygame.time.wait(500 if text != "GO!" else 300)


def pump_events():
    global mode, tool, ramp_click_start, current_start_x, current_start_y, hole_value_selector, score, ball_start_set
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                pygame.quit()
                sys.exit()
            if event.key == pygame.K_TAB:
                if mode == "edit":
                    if not levels_data:
                        set_status("Add a level first (press N) before testing")
                    elif not ball_start_set:
                        set_status("Place a ball start first (tool 4) before testing")
                    else:
                        levels_data[current_level_idx] = snapshot_current_level()
                        mode = "play"
                        countdown()
                        score = 0
                        restart_ball()
                else:
                    mode = "edit"
                ramp_click_start = None
                continue

            mods = pygame.key.get_mods()
            shifted = mods & pygame.KMOD_SHIFT

            if mode == "edit":
                if event.key in (pygame.K_1, pygame.K_2, pygame.K_3) and shifted:
                    n = {pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2}[event.key]
                    reset_current_to_template(n)
                    ramp_click_start = None
                elif event.key == pygame.K_1:
                    tool = 1
                elif event.key == pygame.K_2:
                    tool = 2
                    ramp_click_start = None
                elif event.key == pygame.K_3:
                    tool = 3
                elif event.key == pygame.K_4:
                    tool = 4
                elif event.key == pygame.K_5:
                    tool = 5
                elif event.key == pygame.K_6:
                    tool = 6
                elif event.key == pygame.K_7:
                    if tool == 7:
                        hole_value_selector = hole_value_selector % 3 + 1
                    else:
                        tool = 7
                elif event.key == pygame.K_c:
                    clear_level()
                    spinners.clear()
                    holes.clear()
                    set_status("Cleared current level")
                elif event.key == pygame.K_n:
                    new_level()
                elif event.key == pygame.K_LEFTBRACKET:
                    goto_level(current_level_idx - 1)
                elif event.key == pygame.K_RIGHTBRACKET:
                    goto_level(current_level_idx + 1)
                elif event.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
                    delete_current_level()
                elif event.key == pygame.K_s:
                    save_levels_to_file()
                elif event.key == pygame.K_l:
                    chosen = pick_load_file()
                    if chosen:
                        try:
                            load_levels_from_file(chosen)
                        except Exception as e:
                            set_status(f"Couldn't load that file: {e}")
            else:
                if event.key == pygame.K_LEFTBRACKET:
                    goto_level(current_level_idx - 1, save_first=False)
                elif event.key == pygame.K_RIGHTBRACKET:
                    goto_level(current_level_idx + 1, save_first=False)
                elif event.key == pygame.K_r:
                    score = 0
                    restart_ball()

        if mode == "edit" and event.type == pygame.MOUSEBUTTONDOWN:
            if event.pos[1] >= HEIGHT * SCALE:
                continue
            pos = cell_from_mouse(event.pos)
            if event.button == 3:
                found = nearest_removable(*pos)
                if found:
                    kind, obj = found
                    if kind == "spinner":
                        spinners.remove(obj)
                    else:
                        holes.remove(obj)
            elif event.button == 1:
                if tool == 2:
                    if ramp_click_start is None:
                        ramp_click_start = pos
                    else:
                        add_ramp(ramp_click_start[0], pos[0], ramp_click_start[1], pos[1])
                        ramp_click_start = None
                elif tool == 4:
                    current_start_x, current_start_y = float(pos[0]), float(pos[1])
                    ball_start_set = True
                elif tool == 5:
                    spinners.append(make_spinner(pos[0], pos[1]))
                elif tool == 7:
                    holes.append(make_hole(pos[0], pos[1], hole_value_selector))


def handle_edit_painting():
    if mode != "edit" or tool not in (1, 3, 6):
        return
    if not pygame.mouse.get_pressed()[0]:
        return
    pos = pygame.mouse.get_pos()
    if pos[1] >= HEIGHT * SCALE:
        return
    x, y = cell_from_mouse(pos)
    if not in_bounds(x, y):
        return
    if tool == 1:
        set_cell(x, y, wall=True)
    elif tool == 3:
        set_cell(x, y, goal=True)
    elif tool == 6:
        clear_cell(x, y)


# ---------- Startup: always begin empty; use L to load a levels_data.py ----------
levels_data = []
current_level_idx = 0
clear_level()
spinners = []
holes = []
set_status("No levels loaded -- press L to load a file, or N to start a new level", persistent=True)

restart_ball()

while True:
    pump_events()
    if status_message_timer > 0:
        status_message_timer -= 1 / 50

    if mode == "edit":
        handle_edit_painting()
        update_bar_segments(spinners)
        draw_board()
        draw_grid()
        if ball_start_set:
            draw_pixel_cells(ball_pixels_at(current_start_x, current_start_y), COLORS[1])
        if tool == 2 and ramp_click_start:
            pygame.draw.circle(
                screen, (255, 255, 255),
                (ramp_click_start[0] * SCALE + SCALE // 2, ramp_click_start[1] * SCALE + SCALE // 2),
                4,
            )
        draw_hover_highlight()
        draw_hud()
        pygame.display.flip()
        clock.tick(50)
        continue

    if won:
        win_timer -= 1 / 50
        if win_timer <= 0:
            goto_level(current_level_idx + 1, save_first=False)
            won = False
        draw_board()
        draw_hud()
        pygame.display.flip()
        clock.tick(50)
        continue

    for sp in spinners:
        sp["angle"] += sp["speed"]
    update_bar_segments(spinners)

    keys = pygame.key.get_pressed()
    tilt = 0.0
    if keys[pygame.K_LEFT] or keys[pygame.K_a]:
        tilt = -1.0
    elif keys[pygame.K_RIGHT] or keys[pygame.K_d]:
        tilt = 1.0
    else:
        mouse_x = pygame.mouse.get_pos()[0]
        center_x = (WIDTH * SCALE) / 2
        half_width = (WIDTH * SCALE) / 2
        raw_tilt = max(-1.0, min(1.0, (mouse_x - center_x) / half_width))
        if abs(raw_tilt) < 0.05:
            tilt = 0.0
        else:
            SENSITIVITY_CURVE = 2.0
            tilt = math.copysign(abs(raw_tilt) ** SENSITIVITY_CURVE, raw_tilt)

    prev_ball_x, prev_ball_y = ball_x, ball_y
    ball_x, ball_y, vel_x, vel_y = step_ball(ball_x, ball_y, vel_x, vel_y, tilt)
    ball_roll_direction, ball_rotation = update_ball_roll(
        ball_roll_direction, ball_rotation, prev_ball_x, prev_ball_y, ball_x, ball_y
    )

    gx, gy = int(ball_x), int(ball_y)
    hole_hit = None
    for h in holes:
        dh = math.hypot(ball_x - h["x"], ball_y - h["y"])
        if dh < HOLE_RADIUS:
            hole_hit = h
            break

    if 0 <= gx < WIDTH and 0 <= gy < HEIGHT and static_color[idx(gx, gy)] == 3:
        won = True
        win_timer = 1.0
    elif hole_hit is not None:
        score += hole_hit["value"]
        won = True
        win_timer = 1.0
    elif (
        ball_x < -BALL_RADIUS - 2
        or ball_x > WIDTH + BALL_RADIUS + 2
        or ball_y < -BALL_RADIUS - 2
        or ball_y > HEIGHT + BALL_RADIUS + 2
    ):
        restart_ball()

    draw_board()
    draw_pixel_cells(ball_pixels_at(ball_x, ball_y), COLORS[1])
    accent_pixels = ball_accent_pixels_at(ball_x, ball_y, ball_roll_direction, ball_rotation)
    if accent_pixels:
        draw_pixel_cells(accent_pixels, COLORS[BALL_ROLL_ACCENT_COLOR])
    center_px = (WIDTH * SCALE) // 2
    pygame.draw.line(screen, (60, 60, 70), (center_px, 0), (center_px, HEIGHT * SCALE), 1)
    marker_x = int(center_px + tilt * center_px)
    pygame.draw.polygon(
        screen, (255, 255, 255),
        [(marker_x, HEIGHT * SCALE - 6), (marker_x - 5, HEIGHT * SCALE), (marker_x + 5, HEIGHT * SCALE)],
    )
    draw_hud()
    pygame.display.flip()
    clock.tick(50)
