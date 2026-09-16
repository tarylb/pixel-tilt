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
save_progress = None
clear_best_times = None


def init(display_, group_, bitmap_, palette_, pot_, width, height,
         ui_color_fn, apply_brightness_fn, save_brightness_fn, save_calibration_fn,
         save_progress_fn, clear_best_times_fn):
    global display, group, bitmap, palette, pot, WIDTH, HEIGHT
    global ui_color, apply_brightness, save_brightness, save_calibration, save_progress
    global clear_best_times
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
    save_progress = save_progress_fn
    clear_best_times = clear_best_times_fn


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


def right_held():
    """True if the right button is physically held down right now -- a
    direct, one-off read rather than going through poll_buttons()'s
    edge-detection state machine, for code.py to check once at startup
    (hold right on boot -> unlocked mode) before that state machine is
    otherwise engaged."""
    return not button_right.value


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


# ---------- Centered multi-line text ----------
# adafruit_display_text.label.Label doesn't center multi-line text
# per-line -- a "\n"-separated Label's bounding box is sized to its
# WIDEST line, and anchor_point/anchored_position only positions that
# whole box, so every other, shorter line renders flush against the
# long line's left edge instead of centered under/over it (e.g.
# "RESET?\nboth=yes" -- "both=yes" would hang off to one side rather
# than sitting centered beneath "RESET?"). The fix used everywhere in
# this file is one Label per line, each individually centered.
def _multiline_labels(text, scale=1, color=None):
    """One Label per "\n"-separated line of text, unpositioned and not
    yet added to any group -- see the module note above. Returns
    (labels, heights); heights are each label's actual measured
    bounding_box height (not a guessed constant -- see the level-select
    and level-complete-screen fixes for why that matters on this font),
    for a caller to use in its own vertical layout math before calling
    _place_centered_lines()."""
    if color is None:
        color = ui_color()
    lines = text.split("\n")
    labels = [label.Label(terminalio.FONT, text=line, color=color, scale=scale) for line in lines]
    heights = [lbl.bounding_box[3] if lbl.bounding_box else 8 * scale for lbl in labels]
    return labels, heights


def _place_centered_lines(parent_group, labels, heights, top_y):
    """Stack labels (from _multiline_labels()) horizontally centered on
    the display, starting at top_y and advancing downward by each
    line's own height, appending each to parent_group as it's placed."""
    y = top_y
    for lbl, h in zip(labels, heights):
        lbl.anchor_point = (0.5, 0.0)
        lbl.anchored_position = (WIDTH // 2, y)
        parent_group.append(lbl)
        y += h


def _clear_labels(parent_group, labels):
    for lbl in labels:
        parent_group.remove(lbl)


def _set_centered_lines(parent_group, old_labels, text, scale=1, color=None, valign="center"):
    """Replace old_labels (previously placed in parent_group by this
    function) with a freshly built, per-line-centered rendering of text
    -- the common case of _multiline_labels()/_place_centered_lines()
    for a screen that's just showing one centered block of text and
    needs to change it (a new prompt, a value ticking up/down, a result
    message). valign="center" vertically centers the whole block in the
    display; valign="top" pins its top edge to y=0 instead, for a screen
    where something else (calibrate_pot()'s live bar) occupies the
    bottom. Returns the new label list to pass back in next time."""
    _clear_labels(parent_group, old_labels)
    labels, heights = _multiline_labels(text, scale=scale, color=color)
    total_h = sum(heights)
    top_y = 0 if valign == "top" else max(0, (HEIGHT - total_h) // 2)
    _place_centered_lines(parent_group, labels, heights, top_y)
    return labels


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

    menu_group = displayio.Group()
    display.root_group = menu_group
    current_labels = _set_centered_lines(menu_group, [], options[index][0])

    while True:
        left, right, select, _back = poll_buttons()
        if left:
            index = (index - 1) % len(options)
            current_labels = _set_centered_lines(menu_group, current_labels, options[index][0])
        elif right:
            index = (index + 1) % len(options)
            current_labels = _set_centered_lines(menu_group, current_labels, options[index][0])
        elif select:
            return options[index][1]
        display.refresh(minimum_frames_per_second=0)
        time.sleep(0.02)


def confirm_reset_progress():
    """Confirmation screen for the destructive Reset Progress menu item.
    A simultaneous press confirms (the same "choose this" gesture used
    everywhere else) and, like set_brightness_screen()'s "Saved!" step,
    shows the Reset!/Not saved result in this SAME group/prompt rather
    than building a separate one for it -- one less display.root_group
    swap (each of which has a visible flicker cost on this display) than
    handing the result back to the caller to show its own message would
    need. A lone tap of either button, or holding for BACK_HOLD_SECONDS,
    cancels without resetting anything. Returns True if progress was
    reset (the caller still needs this to also zero its own in-memory
    furthest_level, which menus.py has no reason to know about)."""
    confirm_group = displayio.Group()
    display.root_group = confirm_group
    current_labels = _set_centered_lines(confirm_group, [], "RESET?\nboth=yes")

    while True:
        left, right, select, back = poll_buttons()
        if select:
            # Both writes are attempted regardless of whether the first
            # one succeeds -- they're independent files, and either
            # could be the one a read-only filesystem happens to reject.
            progress_saved = save_progress(0)
            times_cleared = clear_best_times()
            saved = progress_saved and times_cleared
            current_labels = _set_centered_lines(
                confirm_group, current_labels,
                "Reset!" if saved else "Not saved\n(read-only)",
            )
            display.refresh(minimum_frames_per_second=0)
            time.sleep(1.0)
            display.root_group = group
            return True
        if left or right or back:
            display.root_group = group
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

    brightness_group = displayio.Group()
    display.root_group = brightness_group
    current_labels = _set_centered_lines(brightness_group, [], label_for(value), color=ui_color(value))

    while True:
        left, right, select, back = poll_buttons()
        if left:
            value = round(max(min_brightness, value - BRIGHTNESS_STEP), 2)
        elif right:
            value = round(min(1.0, value + BRIGHTNESS_STEP), 2)
        elif select:
            apply_brightness(value)
            saved = save_brightness(value)
            current_labels = _set_centered_lines(
                brightness_group, current_labels,
                "Saved!" if saved else "Not saved\n(read-only)",
                color=ui_color(value),
            )
            display.refresh(minimum_frames_per_second=0)
            time.sleep(1.0)
            return value
        elif back:
            apply_brightness(original)
            return original
        if left or right:
            apply_brightness(value)
            current_labels = _set_centered_lines(brightness_group, current_labels, label_for(value), color=ui_color(value))
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

    for x in range(WIDTH):
        for y in range(HEIGHT):
            bitmap[x, y] = 0
    display.root_group = calib_group

    prompt_labels = []

    def wait_for_any_press(text):
        nonlocal prompt_labels
        prompt_labels = _set_centered_lines(calib_group, prompt_labels, text)
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

    prompt_labels = _set_centered_lines(
        calib_group, prompt_labels,
        ("Saved!" if saved else "Not saved") + "\nhold=done",
        valign="top",
    )

    while True:
        _left, _right, _select, back = poll_buttons()
        draw_bar(bar_x_for(pot.value))
        display.refresh(minimum_frames_per_second=0)
        if back:
            break
        time.sleep(0.02)

    display.root_group = group
    return new_level, new_left, new_right


def level_complete_screen(time_text, is_best):
    """Shown right after finishing a level: the time just taken, and
    "BEST TIME!" too if this run set or beat the record (is_best is the
    caller's call -- see code.py's mark_level_won()). If there's enough
    room left over, also shows a REPLAY / NEXT hint for the left/right
    buttons, which is what actually drives what happens next -- there's
    no auto-advance timer here, this blocks like any other menus.py
    screen until the player picks one (or backs out the usual way).
    Returns "replay", "next", or None if backed out.

    "next" deliberately leaves display.root_group pointed at this
    screen's own group instead of restoring it to the game's group first
    -- code.py always follows "next" with countdown(), which sets its
    own root_group as the very first thing it does, so swapping back to
    the game's group (still showing the just-finished level, since
    load_level() for the new one hasn't run yet) in between would just
    be an extra, unnecessary hop through stale content -- and swapping
    root_group has a visible flicker/glitch cost on this display, worth
    avoiding when there's nothing to show for it."""
    comp_group = displayio.Group()

    lines_text = ("BEST TIME!\n" + time_text) if is_best else ("TIME\n" + time_text)
    main_labels, main_heights = _multiline_labels(lines_text)
    main_h = sum(main_heights)

    replay_label = label.Label(terminalio.FONT, text="REPLAY", color=ui_color(), scale=1)
    replay_label.anchor_point = (0.0, 0.0)
    next_label = label.Label(terminalio.FONT, text="NEXT", color=ui_color(), scale=1)
    next_label.anchor_point = (1.0, 0.0)

    # Measured, not guessed -- terminalio.FONT's glyphs render taller
    # than you'd expect from the display's 32px height (see the level
    # select bottom-clipping fix), so whether the hint row actually fits
    # underneath the time text has to be checked against its real
    # rendered size rather than an assumed constant.
    hint_h = max(
        replay_label.bounding_box[3] if replay_label.bounding_box else 8,
        next_label.bounding_box[3] if next_label.bounding_box else 8,
    )
    replay_w = replay_label.bounding_box[2] if replay_label.bounding_box else 36
    next_w = next_label.bounding_box[2] if next_label.bounding_box else 24
    hint_gap = 2
    show_hints = (
        main_h + hint_gap + hint_h <= HEIGHT
        and replay_w + next_w <= WIDTH
    )

    block_h = main_h + (hint_gap + hint_h if show_hints else 0)
    top_y = max(0, (HEIGHT - block_h) // 2)
    _place_centered_lines(comp_group, main_labels, main_heights, top_y)

    if show_hints:
        hint_y = top_y + main_h + hint_gap
        replay_label.anchored_position = (0, hint_y)
        next_label.anchored_position = (WIDTH, hint_y)
        comp_group.append(replay_label)
        comp_group.append(next_label)

    display.root_group = comp_group
    display.refresh(minimum_frames_per_second=0)

    while True:
        left, right, _select, back = poll_buttons()
        if left:
            display.root_group = group
            return "replay"
        if right:
            return "next"
        if back:
            display.root_group = group
            return None
        time.sleep(0.02)


def level_select(furthest_level, total_levels, best_times=None, format_time=None):
    """Row of numbered boxes, one for every level that exists -- green if
    unlocked (index <= furthest_level), red if locked. Left/right moves
    an underline across ALL of them (locked included, just for browsing);
    a simultaneous press starts the underlined level, but only if it's
    unlocked. The best recorded time for whichever level is underlined is
    shown below the row (best_times maps level index -> seconds; "--" if
    that level has no time yet). Holding either button for
    BACK_HOLD_SECONDS backs out to the main menu, returning None instead
    of a level index."""
    if best_times is None:
        best_times = {}
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
    time_label = label.Label(terminalio.FONT, text="", color=ui_color(), scale=1)
    time_label.anchor_point = (0.5, 0.0)
    select_group.append(time_label)
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

        # Set the time label's text before measuring it -- its actual
        # rendered height (from bounding_box, not a guessed constant) is
        # what decides how far up the whole block needs to sit, since
        # terminalio.FONT's glyphs are taller than the 12px boxes alone
        # would suggest and previously ran the time text off the bottom
        # of the display when centered on the boxes alone.
        best = best_times.get(selected)
        time_label.text = format_time(best) if best is not None and format_time else "--"
        time_h = time_label.bounding_box[3] if time_label.bounding_box else 8
        underline_h = 1
        label_gap = 1
        block_h = box_h + underline_h + label_gap + time_h
        top_y = max(0, (HEIGHT - block_h) // 2)

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
        time_label.anchored_position = (WIDTH // 2, top_y + box_h + underline_h + label_gap)
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
