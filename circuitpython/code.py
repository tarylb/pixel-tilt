"""
Tilt-maze game. Flash this file (as code.py or main.py) together with
levels_data.py to CIRCUITPY.

Feather RP2040 + Adafruit 64x32 RGB Matrix FeatherWing (6mm pitch)
Potentiometer wiper on A0, outer legs on 3.3V and GND.
Two buttons: left on SCK, right on A1, both wired to ground with
internal pull-ups (press both together to select in menus).
Requires adafruit_display_text in CIRCUITPY/lib.
Requires boot.py to give this code write access for saving progress.
"""

import time
import math
import board
import displayio
import framebufferio
import rgbmatrix
import analogio
import digitalio
import terminalio
from adafruit_display_text import label

from levels_data import LEVELS_DATA

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

WIDTH = 64
HEIGHT = 32
BALL_RADIUS = 2
HOLE_RADIUS = BALL_RADIUS + 1

bitmap = displayio.Bitmap(WIDTH, HEIGHT, 8)
palette = displayio.Palette(8)
palette[0] = 0x000000
palette[1] = 0xFF3300
palette[2] = 0x2266FF
palette[3] = 0x00FF00
palette[4] = 0xAA00FF
palette[5] = 0xFFFF00
palette[6] = 0xFF8C00
palette[7] = 0xFF00FF

tile_grid = displayio.TileGrid(bitmap, pixel_shader=palette)
group = displayio.Group()
group.append(tile_grid)

display.root_group = group

solid = bytearray(WIDTH * HEIGHT)
ramp_code = bytearray(WIDTH * HEIGHT)
static_color = bytearray(WIDTH * HEIGHT)


def idx(x, y):
    return y * WIDTH + x


def set_cell(x, y, wall=False, ramp_dir=0, goal=False):
    i = idx(x, y)
    if wall or ramp_dir != 0:
        solid[i] = 1
        static_color[i] = 2
        if ramp_dir > 0:
            ramp_code[i] = 1
        elif ramp_dir < 0:
            ramp_code[i] = 2
    elif goal:
        static_color[i] = 3


def clear_level():
    global solid, ramp_code, static_color
    solid = bytearray(WIDTH * HEIGHT)
    ramp_code = bytearray(WIDTH * HEIGHT)
    static_color = bytearray(WIDTH * HEIGHT)
    for x in range(WIDTH):
        for y in range(HEIGHT):
            bitmap[x, y] = 0


def paint_static():
    for i in range(WIDTH * HEIGHT):
        if static_color[i]:
            bitmap[i % WIDTH, i // WIDTH] = static_color[i]


def circle_outline_pixels(cx, cy, r):
    pts = set()
    cx_i, cy_i = int(round(cx)), int(round(cy))
    r_inner = r - 0.5
    r_outer = r + 0.5
    span = r + 1
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            d = math.sqrt(dx * dx + dy * dy)
            if r_inner <= d <= r_outer:
                px, py = cx_i + dx, cy_i + dy
                if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                    pts.add((px, py))
    return pts


def apply_level_data(data):
    clear_level()
    for x0, x1, y, kind in data["cells"]:
        for x in range(x0, x1 + 1):
            if kind == 1:
                set_cell(x, y, wall=True)
            elif kind == 2:
                set_cell(x, y, ramp_dir=1)
            elif kind == 3:
                set_cell(x, y, ramp_dir=-1)
            elif kind == 4:
                set_cell(x, y, goal=True)
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

BALL_OFFSETS = []
for dy in range(-BALL_RADIUS, BALL_RADIUS + 1):
    for dx in range(-BALL_RADIUS, BALL_RADIUS + 1):
        if dx * dx + dy * dy <= BALL_RADIUS * BALL_RADIUS + 1:
            BALL_OFFSETS.append((dx, dy))


def point_segment_distance(px, py, x0, y0, x1, y1):
    dx = x1 - x0
    dy = y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        t = 0.0
    else:
        t = ((px - x0) * dx + (py - y0) * dy) / length_sq
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
    closest_x = x0 + t * dx
    closest_y = y0 + t * dy
    ddx = px - closest_x
    ddy = py - closest_y
    return math.sqrt(ddx * ddx + ddy * ddy)


BAR_HALF_THICKNESS = 1.0
bar_segments = []


def circle_blocked(cx, cy):
    best_d = None
    for seg in bar_segments:
        d = point_segment_distance(cx, cy, seg[0], seg[1], seg[2], seg[3])
        if best_d is None or d < best_d:
            best_d = d
    if best_d is not None and best_d < BALL_RADIUS + BAR_HALF_THICKNESS:
        return True, "bar"
    px = int(cx)
    py = int(cy)
    for dx, dy in BALL_OFFSETS:
        x = px + dx
        y = py + dy
        if x < 0 or x >= WIDTH or y < 0 or y >= HEIGHT:
            continue
        if solid[idx(x, y)]:
            code = ramp_code[idx(x, y)]
            direction = 1 if code == 1 else (-1 if code == 2 else 0)
            return True, direction
    return False, 0


def draw_sprite(pixels, color_index, prev_pixels):
    new_set = set(pixels)
    for x, y in prev_pixels:
        if (x, y) not in new_set:
            bitmap[x, y] = static_color[idx(x, y)]
    for x, y in pixels:
        bitmap[x, y] = color_index
    return list(pixels)


def ball_pixels_at(cx, cy):
    px = int(cx)
    py = int(cy)
    pts = []
    for dx, dy in BALL_OFFSETS:
        x = px + dx
        y = py + dy
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            pts.append((x, y))
    return pts


def bresenham_line(x0, y0, x1, y1):
    points = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return points


def bar_pixels_for_draw(seg):
    x0, y0, x1, y1 = seg
    pts = set()
    for x, y in bresenham_line(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            pts.add((x, y))
    return pts


pot = analogio.AnalogIn(board.A0)

POT_MIN = 27400
POT_LEVEL = 33000
POT_MAX = 37300
POT_DEADZONE = 1000


def read_tilt():
    raw = pot.value
    if abs(raw - POT_LEVEL) < POT_DEADZONE:
        return 0.0
    if raw >= POT_LEVEL:
        tilt = (raw - POT_LEVEL) / (POT_MAX - POT_LEVEL)
    else:
        tilt = (raw - POT_LEVEL) / (POT_LEVEL - POT_MIN)
    if tilt > 1.0:
        tilt = 1.0
    elif tilt < -1.0:
        tilt = -1.0
    return tilt


ACCEL_SCALE = 0.4
FRICTION = 0.97
RAMP_REDIRECT = 0.9
RAMP_SLOWDOWN = 0.4
WALL_DAMPING = -0.3
BAR_RESTITUTION = 0.8  # bounciness of the flipper bounce (1.0 = perfectly elastic)
MAX_SPEED = 1.5

# ---------- Buttons (left/right navigation, both-together = select) ----------
button_left = digitalio.DigitalInOut(board.SCK)
button_left.direction = digitalio.Direction.INPUT
button_left.pull = digitalio.Pull.UP

button_right = digitalio.DigitalInOut(board.A1)
button_right.direction = digitalio.Direction.INPUT
button_right.pull = digitalio.Pull.UP

_prev_left = False
_prev_right = False
_prev_both = False


def poll_buttons():
    """Return (left_edge, right_edge, select_edge) -- True only on the frame
    a press newly starts. Left/right are suppressed if both buttons are
    already down, so pressing both together doesn't also fire a nav edge."""
    global _prev_left, _prev_right, _prev_both
    cur_left = not button_left.value
    cur_right = not button_right.value
    cur_both = cur_left and cur_right
    left_edge = cur_left and not _prev_left and not cur_both
    right_edge = cur_right and not _prev_right and not cur_both
    select_edge = cur_both and not _prev_both
    _prev_left, _prev_right, _prev_both = cur_left, cur_right, cur_both
    return left_edge, right_edge, select_edge


# ---------- Progress persistence ----------
PROGRESS_PATH = "/progress.txt"


def load_progress():
    try:
        with open(PROGRESS_PATH, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_progress(level_index):
    try:
        with open(PROGRESS_PATH, "w") as f:
            f.write(str(level_index))
    except OSError:
        pass  # filesystem is read-only (see boot.py) -- progress just won't persist


def countdown():
    text_area = label.Label(terminalio.FONT, text="3", color=0xFFFFFF, scale=2)
    text_area.anchor_point = (0.5, 0.5)
    text_area.anchored_position = (WIDTH // 2, HEIGHT // 2)
    countdown_group = displayio.Group()
    countdown_group.append(text_area)
    display.root_group = countdown_group

    for n in ("3", "2", "1"):
        text_area.text = n
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.7)

    text_area.text = "GO!"
    display.refresh(minimum_frames_per_second=0)
    time.sleep(0.4)

    display.root_group = group


def main_menu():
    """One item shown at a time; left/right cycles, both-buttons selects.
    Returns 'start' or 'select'."""
    options = ["Start", "Level Select"]
    index = 0

    text_area = label.Label(terminalio.FONT, text=options[index], color=0xFFFFFF, scale=1)
    text_area.anchor_point = (0.5, 0.5)
    text_area.anchored_position = (WIDTH // 2, HEIGHT // 2)
    menu_group = displayio.Group()
    menu_group.append(text_area)
    display.root_group = menu_group

    while True:
        left, right, select = poll_buttons()
        if left:
            index = (index - 1) % len(options)
            text_area.text = options[index]
        elif right:
            index = (index + 1) % len(options)
            text_area.text = options[index]
        elif select:
            return "start" if index == 0 else "select"
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.02)


def level_select(furthest_level):
    """Row of numbered boxes, one per level 1..furthest_level+1 (levels
    beyond what you've reached aren't shown). Left/right moves the
    highlighted box, both-buttons confirms. Returns the chosen level index."""
    unlocked_count = furthest_level + 1
    box_w = 9
    box_h = 12
    gap = 1
    visible = min(unlocked_count, WIDTH // (box_w + gap))
    selected = 0
    window_start = 0

    select_group = displayio.Group()
    display.root_group = select_group
    box_labels = []

    def redraw():
        for lbl in box_labels:
            select_group.remove(lbl)
        box_labels.clear()
        for x in range(WIDTH):
            for y in range(HEIGHT):
                bitmap[x, y] = 0
        total_w = visible * box_w + (visible - 1) * gap
        start_x = (WIDTH - total_w) // 2
        top_y = (HEIGHT - box_h) // 2
        for i in range(visible):
            level_num = window_start + i
            bx = start_x + i * (box_w + gap)
            is_selected = level_num == selected
            color = 3 if is_selected else 2
            for xx in range(bx, bx + box_w):
                for yy in range(top_y, top_y + box_h):
                    edge = xx in (bx, bx + box_w - 1) or yy in (top_y, top_y + box_h - 1)
                    if edge:
                        bitmap[xx, yy] = color
            lbl = label.Label(terminalio.FONT, text=str(level_num + 1), color=0xFFFFFF, scale=1)
            lbl.anchor_point = (0.5, 0.5)
            lbl.anchored_position = (bx + box_w // 2, top_y + box_h // 2)
            select_group.append(lbl)
            box_labels.append(lbl)
        display.refresh(minimum_frames_per_second=0)

    redraw()

    while True:
        left, right, select = poll_buttons()
        moved = False
        if left and selected > 0:
            selected -= 1
            moved = True
        elif right and selected < unlocked_count - 1:
            selected += 1
            moved = True
        elif select:
            display.root_group = group
            return selected

        if moved:
            if selected < window_start:
                window_start = selected
            elif selected >= window_start + visible:
                window_start = selected - visible + 1
            redraw()

        time.sleep(0.02)


current_level_index = 0
current_start_x = 0.0
current_start_y = 0.0
spinners = []
holes = []
hole_labels = []
ball_pixels = []
bar_pixels_list = []
score = 0


def load_level(index):
    global current_level_index, current_start_x, current_start_y
    global spinners, holes, ball_x, ball_y, vel_x, vel_y, ball_pixels, bar_pixels_list, score

    current_level_index = index
    current_start_x, current_start_y, spinners, holes = LEVELS[index]()
    ball_x, ball_y = current_start_x, current_start_y
    vel_x, vel_y = 0.0, 0.0
    ball_pixels = []
    bar_pixels_list = []
    score = 0

    for lbl in hole_labels:
        group.remove(lbl)
    hole_labels.clear()
    for h in holes:
        lbl = label.Label(terminalio.FONT, text=str(h["value"]), color=0xFFFFFF, scale=1)
        lbl.anchor_point = (0.5, 0.5)
        lbl.anchored_position = (int(h["x"]), int(h["y"]))
        group.append(lbl)
        hole_labels.append(lbl)

    update_bar_segments()
    for i, seg in enumerate(bar_segments):
        pts = bar_pixels_for_draw(seg)
        bar_pixels_list.append(draw_sprite(pts, 4, []))

    ball_pixels = draw_sprite(ball_pixels_at(ball_x, ball_y), 1, ball_pixels)
    display.refresh(minimum_frames_per_second=0)


def update_bar_segments():
    bar_segments.clear()
    for sp in spinners:
        a = sp["angle"]
        hl = sp["half_len"]
        x0 = sp["pivot_x"] - hl * math.cos(a)
        y0 = sp["pivot_y"] - hl * math.sin(a)
        x1 = sp["pivot_x"] + hl * math.cos(a)
        y1 = sp["pivot_y"] + hl * math.sin(a)
        bar_segments.append((x0, y0, x1, y1))


furthest_level = load_progress()
furthest_level = max(0, min(furthest_level, len(LEVELS) - 1))

menu_choice = main_menu()
if menu_choice == "select":
    start_index = level_select(furthest_level)
else:
    start_index = furthest_level

countdown()
load_level(start_index)

won = False
win_timer = 0.0

while True:
    if won:
        win_timer -= 0.02
        if win_timer <= 0:
            reached = min(current_level_index + 1, len(LEVELS) - 1)
            if reached > furthest_level:
                furthest_level = reached
                save_progress(furthest_level)
            next_index = (current_level_index + 1) % len(LEVELS)
            countdown()
            load_level(next_index)
            won = False
        time.sleep(0.02)
        continue

    for sp in spinners:
        sp["angle"] += sp["speed"]
    update_bar_segments()
    while len(bar_pixels_list) < len(bar_segments):
        bar_pixels_list.append([])
    for i, seg in enumerate(bar_segments):
        pts = bar_pixels_for_draw(seg)
        bar_pixels_list[i] = draw_sprite(pts, 4, bar_pixels_list[i])

    for seg in bar_segments:
        d = point_segment_distance(ball_x, ball_y, seg[0], seg[1], seg[2], seg[3])
        contact_dist = BALL_RADIUS + BAR_HALF_THICKNESS
        if d < contact_dist:
            x0, y0, x1, y1 = seg
            dxs = x1 - x0
            dys = y1 - y0
            length_sq = dxs * dxs + dys * dys
            if length_sq == 0:
                t = 0.0
            else:
                t = ((ball_x - x0) * dxs + (ball_y - y0) * dys) / length_sq
                t = max(0.0, min(1.0, t))
            closest_x = x0 + t * dxs
            closest_y = y0 + t * dys
            if d > 0.0001:
                nx = (ball_x - closest_x) / d
                ny = (ball_y - closest_y) / d
            else:
                seg_len = math.sqrt(length_sq) if length_sq > 0 else 1.0
                nx = -dys / seg_len
                ny = dxs / seg_len
            dot = vel_x * nx + vel_y * ny
            if dot < 0:
                vel_x -= (1 + BAR_RESTITUTION) * dot * nx
                vel_y -= (1 + BAR_RESTITUTION) * dot * ny
            overlap = contact_dist - d
            if overlap > 0:
                ball_x += nx * overlap
                ball_y += ny * overlap

    tilt = read_tilt()
    vel_x += tilt * ACCEL_SCALE
    vel_x *= FRICTION
    vel_y *= FRICTION

    if vel_x > MAX_SPEED:
        vel_x = MAX_SPEED
    elif vel_x < -MAX_SPEED:
        vel_x = -MAX_SPEED
    if vel_y > MAX_SPEED:
        vel_y = MAX_SPEED
    elif vel_y < -MAX_SPEED:
        vel_y = -MAX_SPEED

    ramp_hit_this_frame = False

    new_x = ball_x + vel_x
    blocked, ramp_dir = circle_blocked(new_x, ball_y)
    if blocked:
        if ramp_dir == "bar":
            pass  # already reflected above -- just don't move into it
        elif ramp_dir != 0 and not ramp_hit_this_frame:
            vel_y += RAMP_REDIRECT * ramp_dir * vel_x
            vel_x *= RAMP_SLOWDOWN
            ramp_hit_this_frame = True
        else:
            vel_x *= WALL_DAMPING
    else:
        ball_x = new_x

    new_y = ball_y + vel_y
    blocked, ramp_dir = circle_blocked(ball_x, new_y)
    if blocked:
        if ramp_dir == "bar":
            pass  # already reflected above -- just don't move into it
        elif ramp_dir != 0 and not ramp_hit_this_frame:
            vel_x += RAMP_REDIRECT * ramp_dir * vel_y
            vel_y *= RAMP_SLOWDOWN
            ramp_hit_this_frame = True
        else:
            vel_y *= WALL_DAMPING
    else:
        ball_y = new_y

    gx, gy = int(ball_x), int(ball_y)
    hole_hit = None
    for h in holes:
        if point_segment_distance(ball_x, ball_y, h["x"], h["y"], h["x"], h["y"]) < HOLE_RADIUS:
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
        ball_x, ball_y = current_start_x, current_start_y
        vel_x, vel_y = 0.0, 0.0
        ball_pixels = draw_sprite(ball_pixels_at(ball_x, ball_y), 1, ball_pixels)
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.02)
        continue

    ball_pixels = draw_sprite(ball_pixels_at(ball_x, ball_y), 1, ball_pixels)
    display.refresh(minimum_frames_per_second=0)
    time.sleep(0.02)
