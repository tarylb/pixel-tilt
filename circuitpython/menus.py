"""
Menu/UI screens for the tilt-maze game: button input handling, the main
menu, level select, tilt calibration, brightness settings, the countdown,
and the reset-progress confirmation.

CircuitPython-only (uses displayio/digitalio/board) -- unlike physics.py,
this isn't shared with the desktop level editor, which has no buttons,
no menus, and no potentiometer.

State that a screen both reads and can change (current brightness,
current calibration) is passed in as a plain argument and handed back as
a return value rather than shared as a module global -- e.g.
BRIGHTNESS = menus.set_brightness_screen(BRIGHTNESS) -- so code.py stays
the one place that owns that state.

Call init() once, right after code.py has set up its display, bitmap,
palette, and potentiometer, before calling anything else here.
"""

import time
import board
import digitalio
import displayio
import terminalio
from adafruit_display_text import label

# ---------- Wired up by init() before any other function is called ----------
display = None
group = None
bitmap = None
palette = None
pot = None
WIDTH = 64
HEIGHT = 32
ui_color = None
apply_brightness = None
save_brightness = None
save_calibration = None


def init(display_, group_, bitmap_, palette_, pot_, width, height,
         ui_color_fn, apply_brightness_fn, save_brightness_fn, save_calibration_fn):
    global display, group, bitmap, palette, pot, WIDTH, HEIGHT
    global ui_color, apply_brightness, save_brightness, save_calibration
    display = display_
    group = group_
    bitmap = bitmap_
    palette = palette_
    pot = pot_
    WIDTH = width
    HEIGHT = height
    ui_color = ui_color_fn
    apply_brightness = apply_brightness_fn
    save_brightness = save_brightness_fn
    save_calibration = save_calibration_fn


# How far a brightness adjustment steps per button press. The floor
# (MIN_BRIGHTNESS) is owned by code.py, since load_brightness() also needs
# it to validate a persisted value -- set_brightness_screen() takes it as
# a parameter instead of duplicating it here.
BRIGHTNESS_STEP = 0.1

# ---------- Buttons (left/right navigation, both-together = select) ----------
button_left = digitalio.DigitalInOut(board.SCK)
button_left.direction = digitalio.Direction.INPUT
button_left.pull = digitalio.Pull.UP

button_right = digitalio.DigitalInOut(board.A1)
button_right.direction = digitalio.Direction.INPUT
button_right.pull = digitalio.Pull.UP

# How long a lone button press waits to see if the other one joins it
# (making it a select) before it's committed as a left/right nav edge --
# pressing both "at once" is rarely frame-perfect, so this covers the gap.
SIMULTANEOUS_WINDOW = 0.15

# How long either button (or both) must be held continuously to fire the
# universal "back out" gesture -- leaving a menu screen, aborting
# calibration, or quitting an in-progress level back to the main menu.
BACK_HOLD_SECONDS = 3.0

_prev_left = False
_prev_right = False
_armed = None  # None, "left", or "right" -- a lone press waiting on the window
_armed_time = 0.0
_held_since = None  # when the current unbroken press (either/both) started
_back_fired = False  # so back_edge fires once per hold, not every frame past 3s


def poll_buttons():
    """Return (left_edge, right_edge, select_edge, back_edge) -- each True
    only once per physical action. A lone press doesn't fire immediately:
    it's "armed" and waits up to SIMULTANEOUS_WINDOW for the other button
    to join it (firing select instead) or for release/timeout (firing the
    nav edge). Independently, holding either button (or both) down for
    BACK_HOLD_SECONDS fires back_edge, regardless of how the hold started."""
    global _prev_left, _prev_right, _armed, _armed_time, _held_since, _back_fired
    cur_left = not button_left.value
    cur_right = not button_right.value
    now = time.monotonic()

    left_edge = right_edge = select_edge = back_edge = False

    if _armed == "left":
        if cur_right:
            select_edge = True
            _armed = None
        elif not cur_left:
            left_edge = True
            _armed = None
        elif now - _armed_time >= SIMULTANEOUS_WINDOW:
            left_edge = True
            _armed = None
    elif _armed == "right":
        if cur_left:
            select_edge = True
            _armed = None
        elif not cur_right:
            right_edge = True
            _armed = None
        elif now - _armed_time >= SIMULTANEOUS_WINDOW:
            right_edge = True
            _armed = None
    elif cur_left and cur_right and not _prev_left and not _prev_right:
        select_edge = True
    elif cur_left and not _prev_left:
        _armed = "left"
        _armed_time = now
    elif cur_right and not _prev_right:
        _armed = "right"
        _armed_time = now

    if cur_left or cur_right:
        if _held_since is None:
            _held_since = now
        elif not _back_fired and now - _held_since >= BACK_HOLD_SECONDS:
            back_edge = True
            _back_fired = True
    else:
        _held_since = None
        _back_fired = False

    _prev_left, _prev_right = cur_left, cur_right
    return left_edge, right_edge, select_edge, back_edge


def countdown():
    text_area = label.Label(terminalio.FONT, text="3", color=ui_color(), scale=2)
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
    Returns 'start', 'select', 'calibrate', 'brightness', or
    'reset_progress'."""
    options = [
        ("Start", "start"),
        ("Level\nSelect", "select"),
        ("Calibrate\nTilt", "calibrate"),
        ("Brightness", "brightness"),
        ("Reset\nProgress", "reset_progress"),
    ]
    index = 0

    text_area = label.Label(terminalio.FONT, text=options[index][0], color=ui_color(), scale=1)
    text_area.anchor_point = (0.5, 0.5)
    text_area.line_spacing = 0.9
    text_area.anchored_position = (WIDTH // 2, HEIGHT // 2)
    menu_group = displayio.Group()
    menu_group.append(text_area)
    display.root_group = menu_group

    while True:
        left, right, select, _back = poll_buttons()
        if left:
            index = (index - 1) % len(options)
            text_area.text = options[index][0]
        elif right:
            index = (index + 1) % len(options)
            text_area.text = options[index][0]
        elif select:
            return options[index][1]
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.02)


def confirm_reset_progress():
    """Confirmation screen for the destructive Reset Progress menu item.
    A simultaneous press confirms (the same "choose this" gesture used
    everywhere else); a lone tap of either button, or holding for
    BACK_HOLD_SECONDS, cancels. Returns True if the user confirmed."""
    prompt = label.Label(terminalio.FONT, text="RESET?\nboth=yes", color=ui_color(), scale=1)
    prompt.anchor_point = (0.5, 0.5)
    prompt.anchored_position = (WIDTH // 2, HEIGHT // 2)
    prompt.line_spacing = 0.9
    confirm_group = displayio.Group()
    confirm_group.append(prompt)
    display.root_group = confirm_group

    while True:
        left, right, select, back = poll_buttons()
        if select:
            return True
        if left or right or back:
            return False
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.02)


def set_brightness_screen(current_brightness, min_brightness):
    """Adjust display brightness with left/right -- applied live to the
    whole display (both the maze palette and this screen's own text) so
    you can see the effect immediately. A simultaneous press saves it;
    holding either button for BACK_HOLD_SECONDS cancels and restores the
    brightness that was active before entering this screen. Returns the
    brightness value to keep using (either the new one or the original)."""
    original = current_brightness
    value = current_brightness

    def label_for(v):
        return f"Bright\n{int(round(v * 100))}%"

    prompt = label.Label(terminalio.FONT, text=label_for(value), color=ui_color(value), scale=1)
    prompt.anchor_point = (0.5, 0.5)
    prompt.anchored_position = (WIDTH // 2, HEIGHT // 2)
    prompt.line_spacing = 0.9
    brightness_group = displayio.Group()
    brightness_group.append(prompt)
    display.root_group = brightness_group

    while True:
        left, right, select, back = poll_buttons()
        if left:
            value = round(max(min_brightness, value - BRIGHTNESS_STEP), 2)
        elif right:
            value = round(min(1.0, value + BRIGHTNESS_STEP), 2)
        elif select:
            apply_brightness(value)
            saved = save_brightness(value)
            prompt.text = "Saved!" if saved else "Not saved\n(read-only)"
            prompt.color = ui_color(value)
            display.refresh(minimum_frames_per_second=0)
            time.sleep(1.0)
            return value
        elif back:
            apply_brightness(original)
            return original
        if left or right:
            apply_brightness(value)
            prompt.color = ui_color(value)
            prompt.text = label_for(value)
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.02)


def calibrate_pot():
    """Three-step calibration: level the board, tilt as far left as it
    goes, then as far right as it goes -- press ANY button (left, right,
    or both, it doesn't matter) to confirm each step. No line/bar is
    shown during those steps. Once all three are captured and saved, a
    verification screen appears with a center line and a live bar
    tracking the new calibration. Holding either button (or both) for
    BACK_HOLD_SECONDS backs out -- discarding an in-progress calibration,
    or simply leaving the finished verification screen. Returns the new
    (level, left, right) raw values, or None if aborted before all three
    were captured."""
    calib_tile_grid = displayio.TileGrid(bitmap, pixel_shader=palette)
    calib_group = displayio.Group()
    calib_group.append(calib_tile_grid)

    prompt = label.Label(terminalio.FONT, text="", color=ui_color(), scale=1)
    prompt.anchor_point = (0.5, 0.5)
    prompt.anchored_position = (WIDTH // 2, HEIGHT // 2)
    prompt.line_spacing = 0.9
    calib_group.append(prompt)

    for x in range(WIDTH):
        for y in range(HEIGHT):
            bitmap[x, y] = 0
    display.root_group = calib_group

    def wait_for_any_press(text):
        prompt.text = text
        while True:
            left, right, select, back = poll_buttons()
            if back:
                return None
            if left or right or select:
                return pot.value
            display.refresh(minimum_frames_per_second=0)
            time.sleep(0.02)

    new_level = wait_for_any_press("LEVEL\nboard")
    if new_level is None:
        display.root_group = group
        return None
    new_left = wait_for_any_press("TILT\nLEFT")
    if new_left is None:
        display.root_group = group
        return None
    new_right = wait_for_any_press("TILT\nRIGHT")
    if new_right is None:
        display.root_group = group
        return None

    saved = save_calibration(new_level, new_left, new_right)

    BAR_TOP = 26
    BAR_BOTTOM = 32

    def draw_bar(x):
        for xx in range(WIDTH):
            for yy in range(BAR_TOP, BAR_BOTTOM):
                bitmap[xx, yy] = 0
        center = WIDTH // 2
        for yy in range(BAR_TOP, BAR_BOTTOM):
            bitmap[center - 1, yy] = 2
            bitmap[center, yy] = 2
        x = max(0, min(WIDTH - 2, x))
        for yy in range(BAR_TOP, BAR_BOTTOM):
            bitmap[x, yy] = 1
            bitmap[x + 1, yy] = 1

    def bar_x_for(raw):
        # Left column of the 2px-wide indicator, over a WIDTH-2 wide track
        # so the indicator (like the center line) is exactly 2px wide and
        # can still reach both edges.
        span = new_right - new_left
        if span == 0:
            return (WIDTH - 2) // 2
        frac = (raw - new_left) / span
        frac = max(0.0, min(1.0, frac))
        return int(round(frac * (WIDTH - 2)))

    prompt.text = ("Saved!" if saved else "Not saved") + "\nhold=done"
    prompt.anchor_point = (0.5, 0.0)
    prompt.anchored_position = (WIDTH // 2, 0)

    while True:
        _left, _right, _select, back = poll_buttons()
        draw_bar(bar_x_for(pot.value))
        display.refresh(minimum_frames_per_second=0)
        if back:
            break
        time.sleep(0.02)

    display.root_group = group
    return new_level, new_left, new_right


def level_select(furthest_level, total_levels):
    """Row of numbered boxes, one for every level that exists -- green if
    unlocked (index <= furthest_level), red if locked. Left/right moves
    an underline across ALL of them (locked included, just for browsing);
    a simultaneous press starts the underlined level, but only if it's
    unlocked. Holding either button for BACK_HOLD_SECONDS backs out to
    the main menu, returning None instead of a level index."""
    total = total_levels
    box_w = 9
    box_h = 12
    gap = 1
    visible = min(total, WIDTH // (box_w + gap))
    selected = 0
    window_start = 0

    select_tile_grid = displayio.TileGrid(bitmap, pixel_shader=palette)
    select_group = displayio.Group()
    select_group.append(select_tile_grid)
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
            color = 3 if level_num <= furthest_level else 1  # green unlocked, red locked
            for xx in range(bx, bx + box_w):
                for yy in range(top_y, top_y + box_h):
                    edge = xx in (bx, bx + box_w - 1) or yy in (top_y, top_y + box_h - 1)
                    if edge:
                        bitmap[xx, yy] = color
            if level_num == selected:
                for xx in range(bx, bx + box_w):
                    bitmap[xx, top_y + box_h] = 5  # underline marks the highlighted level
            lbl = label.Label(terminalio.FONT, text=str(level_num + 1), color=ui_color(), scale=1)
            lbl.anchor_point = (0.5, 0.5)
            lbl.anchored_position = (bx + box_w // 2, top_y + box_h // 2)
            select_group.append(lbl)
            box_labels.append(lbl)
        display.refresh(minimum_frames_per_second=0)

    redraw()

    while True:
        left, right, select, back = poll_buttons()
        moved = False
        if left and selected > 0:
            selected -= 1
            moved = True
        elif right and selected < total - 1:
            selected += 1
            moved = True
        elif select:
            if selected <= furthest_level:
                display.root_group = group
                return selected
        elif back:
            display.root_group = group
            return None

        if moved:
            if selected < window_start:
                window_start = selected
            elif selected >= window_start + visible:
                window_start = selected - visible + 1
            redraw()

        time.sleep(0.02)
