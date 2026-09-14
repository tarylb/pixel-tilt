"""
Tilt-maze game. Flash this file (as code.py or main.py) together with
levels_data.py, physics.py, and menus.py to CIRCUITPY.

Feather RP2040 + Adafruit 64x32 RGB Matrix FeatherWing (6mm pitch)
Potentiometer wiper on A0, outer legs on 3.3V and GND.
Two buttons: left on SCK, right on A1, both wired to ground with
internal pull-ups (press both together to select in menus).
Requires adafruit_display_text in CIRCUITPY/lib.
Requires boot.py to give this code write access for saving progress.
"""

import time
import board
import displayio
import framebufferio
import rgbmatrix
import analogio
import terminalio
from adafruit_display_text import label

from levels_data import LEVELS_DATA
from physics import (
    WIDTH, HEIGHT, BALL_RADIUS, HOLE_RADIUS,
    static_color, bar_segments,
    idx, clear_level, apply_cells, circle_outline_pixels,
    ball_pixels_at, point_segment_distance, bar_pixels_for_draw,
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

bitmap = displayio.Bitmap(WIDTH, HEIGHT, 9)
palette = displayio.Palette(9)

# Full-brightness reference colors -- apply_brightness() scales these into
# `palette` and into any UI text color (via ui_color()) so the whole
# display, not just game elements, dims consistently.
BASE_PALETTE_COLORS = (
    0x000000,
    0xFF3300,
    0x2266FF,
    0x00FF00,
    0xAA00FF,
    0xFFFF00,
    0xFF8C00,
    0xFF00FF,
    0xFFFFFF,  # ball roll accent -- a bright star/sparkle against the red ball
)
BALL_ROLL_ACCENT_COLOR = 8

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
    for i in range(WIDTH * HEIGHT):
        if static_color[i]:
            bitmap[i % WIDTH, i // WIDTH] = static_color[i]


def apply_level_data(data):
    clear_level()
    apply_cells(data["cells"])
    holes = [
        {"x": h["x"], "y": h["y"], "value": h["value"]}
        for h in data.get("holes", [])
    ]
    for h in holes:
        color = 4 + h["value"]
        for px, py in circle_outline_pixels(h["x"], h["y"], HOLE_RADIUS):
            static_color[idx(px, py)] = color
    paint_static()
    sx, sy = data["start"]
    spinners = [
        {
            "pivot_x": s["pivot_x"],
            "pivot_y": s["pivot_y"],
            "half_len": s["half_len"],
            "angle": 0.0,
            "speed": s["speed"],
        }
        for s in data["spinners"]
    ]
    return sx, sy, spinners, holes


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

POT_DEADZONE = 1000
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
    """Returns True if the write actually succeeded -- see save_brightness()."""
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
    file -- see save_brightness() for the read-only-filesystem case."""
    best_times[level_index] = seconds
    try:
        with open(BEST_TIMES_PATH, "w") as f:
            for index, value in best_times.items():
                f.write(f"{index},{value}\n")
        return True
    except OSError:
        return False


def format_time(seconds):
    return f"{seconds:.1f}s"


menus.init(
    display, group, bitmap, palette, pot, WIDTH, HEIGHT,
    ui_color, apply_brightness, save_brightness, save_calibration,
)


current_level_index = 0
current_start_x = 0.0
current_start_y = 0.0
spinners = []
holes = []
hole_labels = []
ball_pixels = []
bar_pixels_list = []
score = 0
ball_rotation = 0.0
ball_roll_direction = (1.0, 0.0)
level_start_time = 0.0
win_label = None


def load_level(index):
    global current_level_index, current_start_x, current_start_y
    global spinners, holes, ball_x, ball_y, vel_x, vel_y, ball_pixels, bar_pixels_list, score
    global ball_rotation, ball_roll_direction, level_start_time, win_label

    current_level_index = index
    current_start_x, current_start_y, spinners, holes = LEVELS[index]()
    ball_x, ball_y = current_start_x, current_start_y
    vel_x, vel_y = 0.0, 0.0
    ball_pixels = []
    bar_pixels_list = []
    score = 0
    ball_rotation = 0.0
    ball_roll_direction = (1.0, 0.0)
    level_start_time = time.monotonic()

    if win_label is not None:
        group.remove(win_label)
        win_label = None

    for lbl in hole_labels:
        group.remove(lbl)
    hole_labels.clear()
    for h in holes:
        lbl = label.Label(terminalio.FONT, text=str(h["value"]), color=ui_color(), scale=1)
        lbl.anchor_point = (0.5, 0.5)
        lbl.anchored_position = (int(h["x"]), int(h["y"]))
        group.append(lbl)
        hole_labels.append(lbl)

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
    """Called the instant a level is won: records a new best time if this
    run beat it (or is the first time it's been finished at all), and
    shows a "NEW BEST" overlay with the time when it did. Returns how
    long, in seconds, to pause on the win screen before advancing."""
    global win_label
    elapsed = time.monotonic() - level_start_time
    previous_best = best_times.get(current_level_index)
    is_new_best = previous_best is None or elapsed < previous_best
    if is_new_best:
        save_best_time(current_level_index, elapsed)
        win_label = label.Label(
            terminalio.FONT,
            text="NEW BEST\n" + format_time(elapsed),
            color=ui_color(),
            scale=1,
        )
        win_label.anchor_point = (0.5, 0.5)
        win_label.anchored_position = (WIDTH // 2, HEIGHT // 2)
        win_label.line_spacing = 0.9
        group.append(win_label)
        display.refresh(minimum_frames_per_second=0)
        return 2.0
    return 1.0


furthest_level = load_progress()
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
        if menus.confirm_reset_progress():
            furthest_level = 0
            saved = save_progress(furthest_level)
            msg_label = label.Label(
                terminalio.FONT,
                text="Reset!" if saved else "Not saved\n(read-only)",
                color=ui_color(),
                scale=1,
            )
            msg_label.anchor_point = (0.5, 0.5)
            msg_label.anchored_position = (WIDTH // 2, HEIGHT // 2)
            msg_label.line_spacing = 0.9
            msg_group = displayio.Group()
            msg_group.append(msg_label)
            display.root_group = msg_group
            display.refresh(minimum_frames_per_second=0)
            time.sleep(1.0)
        continue
    if menu_choice == "select":
        start_index = menus.level_select(furthest_level, len(LEVELS), best_times, format_time)
        if start_index is None:
            continue  # backed out of level select
    else:
        start_index = furthest_level

    menus.countdown()
    load_level(start_index)

    won = False
    win_timer = 0.0
    backed_out = False

    while True:
        _left, _right, select, back = menus.poll_buttons()
        if back:
            backed_out = True
            break
        if select:
            load_level(current_level_index)
            won = False
            continue

        if won:
            win_timer -= 0.02
            if win_timer <= 0:
                reached = min(current_level_index + 1, len(LEVELS) - 1)
                if reached > furthest_level:
                    furthest_level = reached
                    save_progress(furthest_level)
                next_index = (current_level_index + 1) % len(LEVELS)
                menus.countdown()
                load_level(next_index)
                won = False
            time.sleep(0.02)
            continue

        for sp in spinners:
            sp["angle"] += sp["speed"]
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
        hole_hit = None
        for h in holes:
            if point_segment_distance(ball_x, ball_y, h["x"], h["y"], h["x"], h["y"]) < HOLE_RADIUS:
                hole_hit = h
                break

        if 0 <= gx < WIDTH and 0 <= gy < HEIGHT and static_color[idx(gx, gy)] == 3:
            won = True
            win_timer = mark_level_won()
        elif hole_hit is not None:
            score += hole_hit["value"]
            won = True
            win_timer = mark_level_won()
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
