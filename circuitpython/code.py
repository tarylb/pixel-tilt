"""
Tilt-maze game. Flash this file (as code.py or main.py) together with
levels_data.json, physics.py, and menus.py to CIRCUITPY.

Feather RP2040 + Adafruit 64x32 RGB Matrix FeatherWing (6mm pitch)
Potentiometer wiper on A0, outer legs on 3.3V and GND.
Two buttons: left on SCK, right on A1, both wired to ground with
internal pull-ups (press both together to select in menus).
Requires adafruit_display_text in CIRCUITPY/lib.
Requires boot.py to give this code write access for saving progress.
"""

import time
import math
import json
import board
import displayio
import framebufferio
import rgbmatrix
import analogio

from physics import (
    WIDTH, HEIGHT, BALL_RADIUS,
    static_color, bar_segments,
    idx, clear_level, apply_walls, apply_goals,
    ball_pixels_at, bar_pixels_for_draw,
    update_bar_segments, step_ball, update_ball_roll, ball_accent_pixels_at,
)
import menus

displayio.release_displays()

matrix = rgbmatrix.RGBMatrix(
    width=64,
    bit_depth=4,
    rgb_pins=[board.D6, board.D5, board.D9, board.D11, board.D10, board.D12],
    addr_pins=[board.D25, board.D24, board.A3, board.A2],
    clock_pin=board.D13,
    latch_pin=board.D0,
    output_enable_pin=board.D1,
)
display = framebufferio.FramebufferDisplay(matrix, auto_refresh=False)

bitmap = displayio.Bitmap(WIDTH, HEIGHT, 7)
palette = displayio.Palette(7)

# Full-brightness reference colors -- apply_brightness() scales these into
# `palette` and into any UI text color (via ui_color()) so the whole
# display, not just game elements, dims consistently. Index 5 (yellow) is
# also what menus.py uses for the level-select underline -- it's not
# just a game color, so keep it even though the game itself only uses
# indices 0-4 and 6.
BASE_PALETTE_COLORS = (
    0x000000,
    0xFF3300,
    0x2266FF,
    0x00FF00,
    0xAA00FF,
    0xFFFF00,
    0xFFFFFF,  # ball roll accent -- a bright star/sparkle against the red ball
)
BALL_ROLL_ACCENT_COLOR = 6

BRIGHTNESS_PATH = "/brightness.txt"
DEFAULT_BRIGHTNESS = 1.0
MIN_BRIGHTNESS = 0.1


def load_brightness():
    try:
        with open(BRIGHTNESS_PATH, "r") as f:
            return max(MIN_BRIGHTNESS, min(1.0, float(f.read().strip())))
    except (OSError, ValueError):
        return DEFAULT_BRIGHTNESS


def save_brightness(value):
    """Returns True if the write actually succeeded -- if the board was
    booted with a button held (see boot.py), the filesystem is read-only
    to this code and the write silently no-ops, so callers can surface
    that instead of claiming a save that didn't happen."""
    try:
        with open(BRIGHTNESS_PATH, "w") as f:
            f.write(str(value))
        return True
    except OSError:
        return False


def _scale_color(color, factor):
    r = int(((color >> 16) & 0xFF) * factor)
    g = int(((color >> 8) & 0xFF) * factor)
    b = int((color & 0xFF) * factor)
    return (r << 16) | (g << 8) | b


def apply_brightness(factor):
    for i, color in enumerate(BASE_PALETTE_COLORS):
        palette[i] = _scale_color(color, factor)


def ui_color(factor=None):
    return _scale_color(0xFFFFFF, BRIGHTNESS if factor is None else factor)


BRIGHTNESS = load_brightness()
apply_brightness(BRIGHTNESS)

tile_grid = displayio.TileGrid(bitmap, pixel_shader=palette)
group = displayio.Group()
group.append(tile_grid)

display.root_group = group


def paint_static():
    """Redraws the whole static layer (walls/goal) from scratch, blanking
    every other pixel first -- the bitmap is shared with menus.py's
    screens (level select's boxes, calibration's bar), which paint
    directly into it, so a level load has to reclaim the entire display
    rather than only touching the cells it cares about."""
    for i in range(WIDTH * HEIGHT):
        bitmap[i % WIDTH, i // WIDTH] = static_color[i]


def apply_level_data(data):
    clear_level()
    apply_walls(data["walls"])
    apply_goals(data["goals"])
    paint_static()
    sx, sy = data["start"]
    spinners = [
        {
            "pivot_x": px,
            "pivot_y": py,
            "half_len": half_len,
            "angle": math.radians(start_angle_deg),
            "speed": speed,
            "direction": direction,
        }
        for px, py, half_len, speed, start_angle_deg, direction in data["spinners"]
    ]
    return sx, sy, spinners


LEVELS_DATA_PATH = "/levels_data.json"
with open(LEVELS_DATA_PATH, "r") as f:
    LEVELS_DATA = json.load(f)

LEVELS = [lambda d=d: apply_level_data(d) for d in LEVELS_DATA]


def draw_sprite(pixels, color_index, prev_pixels):
    new_set = set(pixels)
    for x, y in prev_pixels:
        if (x, y) not in new_set:
            bitmap[x, y] = static_color[idx(x, y)]
    for x, y in pixels:
        bitmap[x, y] = color_index
    return list(pixels)


pot = analogio.AnalogIn(board.A0)

POT_DEADZONE = 500
CALIBRATION_PATH = "/calibration.txt"
DEFAULT_POT_LEVEL = 33000
DEFAULT_POT_LEFT = 27400
DEFAULT_POT_RIGHT = 37300


def load_calibration():
    try:
        with open(CALIBRATION_PATH, "r") as f:
            level_s, left_s, right_s = f.read().strip().split(",")
            return int(level_s), int(left_s), int(right_s)
    except (OSError, ValueError):
        return DEFAULT_POT_LEVEL, DEFAULT_POT_LEFT, DEFAULT_POT_RIGHT


def save_calibration(level, left, right):
    """Returns True if the write actually succeeded -- see save_brightness()."""
    try:
        with open(CALIBRATION_PATH, "w") as f:
            f.write(f"{level},{left},{right}")
        return True
    except OSError:
        return False


POT_LEVEL, POT_LEFT, POT_RIGHT = load_calibration()


def read_tilt():
    """POT_LEFT/POT_RIGHT are whatever raw values calibrate_pot() measured
    at each physical extreme, so either one can be numerically above or
    below POT_LEVEL depending on which way the pot happens to be wired --
    this compares sides of POT_LEVEL rather than assuming an ordering."""
    raw = pot.value
    if abs(raw - POT_LEVEL) < POT_DEADZONE:
        return 0.0
    if (raw > POT_LEVEL) == (POT_RIGHT > POT_LEVEL):
        span = POT_RIGHT - POT_LEVEL
    else:
        span = POT_LEVEL - POT_LEFT
    tilt = (raw - POT_LEVEL) / span if span else 0.0
    if tilt > 1.0:
        tilt = 1.0
    elif tilt < -1.0:
        tilt = -1.0
    return tilt


# ---------- Progress persistence ----------
PROGRESS_PATH = "/progress.txt"


def load_progress():
    try:
        with open(PROGRESS_PATH, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_progress(level_index):
    """Returns True if the write actually succeeded -- see save_brightness().
    No-ops (reporting success) in UNLOCKED_MODE, which never touches
    real save data -- see UNLOCKED_MODE's own comment."""
    if UNLOCKED_MODE:
        return True
    try:
        with open(PROGRESS_PATH, "w") as f:
            f.write(str(level_index))
        return True
    except OSError:
        return False


# ---------- Best-time persistence ----------
BEST_TIMES_PATH = "/besttimes.txt"


def load_best_times():
    times = {}
    try:
        with open(BEST_TIMES_PATH, "r") as f:
            for line in f.read().strip().split("\n"):
                if not line:
                    continue
                index_s, seconds_s = line.split(",")
                times[int(index_s)] = float(seconds_s)
    except OSError:
        pass
    return times


def save_best_time(level_index, seconds):
    """Records a new best time for level_index and rewrites the whole
    file -- see save_brightness() for the read-only-filesystem case.
    Still updates the in-memory best_times in UNLOCKED_MODE (so the
    level-complete screen's "BEST TIME!" feedback keeps working for the
    session) but never writes it to disk -- see UNLOCKED_MODE's own
    comment."""
    best_times[level_index] = seconds
    if UNLOCKED_MODE:
        return True
    try:
        with open(BEST_TIMES_PATH, "w") as f:
            for index, value in best_times.items():
                f.write(f"{index},{value}\n")
        return True
    except OSError:
        return False


def clear_best_times():
    """Wipes every recorded best time -- paired with save_progress(0) by
    confirm_reset_progress() so Reset Progress actually resets
    everything, not just which levels are unlocked. Returns True if the
    write succeeded -- see save_brightness() for the read-only case.
    Still clears the in-memory best_times in UNLOCKED_MODE, but leaves
    the real file on disk untouched -- see UNLOCKED_MODE's own comment."""
    best_times.clear()
    if UNLOCKED_MODE:
        return True
    try:
        with open(BEST_TIMES_PATH, "w"):
            pass
        return True
    except OSError:
        return False


def format_time(seconds):
    return f"{seconds:.1f}s"


menus.init(
    display, group, bitmap, palette, pot, WIDTH, HEIGHT,
    ui_color, apply_brightness, save_brightness, save_calibration, save_progress,
    clear_best_times,
)

# Hold the right button on boot for a special session: every level
# unlocked, nothing ever saved -- so it's safe to explore/practice any
# level without touching normal mode's real progress or best times.
# (The left button is boot.py's separate "keep CIRCUITPY writable from
# my computer" gesture -- unrelated to this one.) Checked once, right
# here at startup, rather than through poll_buttons()'s ongoing edge
# detection.
UNLOCKED_MODE = menus.right_held()


current_level_index = 0
current_start_x = 0.0
current_start_y = 0.0
spinners = []
ball_pixels = []
bar_pixels_list = []
ball_rotation = 0.0
ball_roll_direction = (1.0, 0.0)
level_start_time = 0.0


def load_level(index, reset_timer=True):
    """reset_timer=False is for the in-game reset gesture (both buttons)
    only -- it puts the ball back at the start like any other load, but
    a level's completion time is meant to run continuously from the
    moment you actually start/replay/advance to it, unaffected by
    falling off (which doesn't call this at all -- see the main loop's
    out-of-bounds handling) or manually resetting your position."""
    global current_level_index, current_start_x, current_start_y
    global spinners, ball_x, ball_y, vel_x, vel_y, ball_pixels, bar_pixels_list
    global ball_rotation, ball_roll_direction, level_start_time

    current_level_index = index
    current_start_x, current_start_y, spinners = LEVELS[index]()
    ball_x, ball_y = current_start_x, current_start_y
    vel_x, vel_y = 0.0, 0.0
    ball_pixels = []
    bar_pixels_list = []
    ball_rotation = 0.0
    ball_roll_direction = (1.0, 0.0)
    if reset_timer:
        level_start_time = time.monotonic()

    update_bar_segments(spinners)
    for seg in bar_segments:
        pts = bar_pixels_for_draw(seg)
        bar_pixels_list.append(draw_sprite(pts, 4, []))

    ball_pixels = draw_sprite(ball_pixels_at(ball_x, ball_y), 1, ball_pixels)
    accent_pixels = ball_accent_pixels_at(ball_x, ball_y, ball_roll_direction, ball_rotation)
    if accent_pixels:
        draw_sprite(accent_pixels, BALL_ROLL_ACCENT_COLOR, [])
    display.refresh(minimum_frames_per_second=0)


def mark_level_won():
    """Called the instant a level is won: records a best time whenever
    this run set or beat the record -- a level's first-ever completion
    counts too, since there's nothing yet for it to have beaten. Returns
    (elapsed_seconds, is_best) for menus.level_complete_screen() to show;
    it owns the actual win screen and blocks on the player's replay/next
    choice, so there's no timer here."""
    elapsed = time.monotonic() - level_start_time
    previous_best = best_times.get(current_level_index)
    is_best = previous_best is None or elapsed < previous_best
    if is_best:
        save_best_time(current_level_index, elapsed)
    return elapsed, is_best


furthest_level = (len(LEVELS) - 1) if UNLOCKED_MODE else load_progress()
furthest_level = max(0, min(furthest_level, len(LEVELS) - 1))
best_times = load_best_times()

while True:
    menu_choice = menus.main_menu()
    if menu_choice == "calibrate":
        result = menus.calibrate_pot()
        if result is not None:
            POT_LEVEL, POT_LEFT, POT_RIGHT = result
        continue
    if menu_choice == "brightness":
        BRIGHTNESS = menus.set_brightness_screen(BRIGHTNESS, MIN_BRIGHTNESS)
        continue
    if menu_choice == "reset_progress":
        # confirm_reset_progress() does the save and shows the Reset!/Not
        # saved result itself (reusing its own screen -- see its
        # docstring); it only reports back whether to also zero our own
        # in-memory furthest_level, which it has no reason to know about.
        if menus.confirm_reset_progress():
            furthest_level = 0
        continue
    if menu_choice == "select":
        start_index = menus.level_select(furthest_level, len(LEVELS), best_times, format_time)
        if start_index is None:
            continue  # backed out of level select
    else:
        start_index = furthest_level

    menus.countdown()
    load_level(start_index)

    backed_out = False

    while True:
        _left, _right, select, back = menus.poll_buttons()
        if back:
            backed_out = True
            break
        if select:
            load_level(current_level_index, reset_timer=False)
            continue

        for sp in spinners:
            sp["angle"] += sp["speed"] * sp["direction"]
        update_bar_segments(spinners)
        while len(bar_pixels_list) < len(bar_segments):
            bar_pixels_list.append([])
        for i, seg in enumerate(bar_segments):
            pts = bar_pixels_for_draw(seg)
            bar_pixels_list[i] = draw_sprite(pts, 4, bar_pixels_list[i])

        tilt = read_tilt()
        prev_ball_x, prev_ball_y = ball_x, ball_y
        ball_x, ball_y, vel_x, vel_y = step_ball(ball_x, ball_y, vel_x, vel_y, tilt)
        ball_roll_direction, ball_rotation = update_ball_roll(
            ball_roll_direction, ball_rotation, prev_ball_x, prev_ball_y, ball_x, ball_y
        )

        gx, gy = int(ball_x), int(ball_y)

        if 0 <= gx < WIDTH and 0 <= gy < HEIGHT and static_color[idx(gx, gy)] == 3:
            elapsed, is_best = mark_level_won()
            choice = menus.level_complete_screen(format_time(elapsed), is_best)
            if choice == "replay":
                load_level(current_level_index)
            elif choice == "next":
                reached = min(current_level_index + 1, len(LEVELS) - 1)
                if reached > furthest_level:
                    furthest_level = reached
                    save_progress(furthest_level)
                next_index = (current_level_index + 1) % len(LEVELS)
                menus.countdown()
                load_level(next_index)
            else:  # backed out of the win screen
                backed_out = True
                break
            continue
        elif (
            ball_x < -BALL_RADIUS - 2
            or ball_x > WIDTH + BALL_RADIUS + 2
            or ball_y < -BALL_RADIUS - 2
            or ball_y > HEIGHT + BALL_RADIUS + 2
        ):
            ball_x, ball_y = current_start_x, current_start_y
            vel_x, vel_y = 0.0, 0.0
            ball_rotation = 0.0
            ball_pixels = draw_sprite(ball_pixels_at(ball_x, ball_y), 1, ball_pixels)
            display.refresh(minimum_frames_per_second=0)
            time.sleep(0.02)
            continue

        ball_pixels = draw_sprite(ball_pixels_at(ball_x, ball_y), 1, ball_pixels)
        accent_pixels = ball_accent_pixels_at(ball_x, ball_y, ball_roll_direction, ball_rotation)
        if accent_pixels:
            draw_sprite(accent_pixels, BALL_ROLL_ACCENT_COLOR, [])
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.02)
