"""
Desktop editor + playtester for the tilt-maze levels -- supports any
number of levels and unlimited spinners, saving/loading level data to
levels_data.json.

This is NOT the CircuitPython game itself -- it's a plain-Python/
pygame tool for building and playtesting level layouts. It imports
circuitpython/physics.py, the same collision/physics engine the device
game uses, so what you build and playtest here matches what runs on the
board. The device game maintains its own code separately and just
reads whatever levels_data.json you save here.

MODE
  Tab             -- toggle between Edit and Play

TOP MENU BAR (both modes)
  File            -- Save (to wherever was last opened/saved-as, no
                     prompt), Save As... (always prompts, and that
                     becomes the new "last" path), Open...
  Edit            -- Undo / Redo (also Ctrl+Z / Ctrl+Shift+Z), scoped to
                     the level currently open -- switching levels,
                     loading a file, New, or Delete all clear the
                     history. A single Wall/Goal/Eraser drag undoes as
                     one step, not one step per pixel touched.
  Level           -- New, Delete, Clear (wipes the current level but
                     keeps it in the list), and a "Go to Level N" entry
                     per level that exists
  Play button     -- toggles Play/Edit (same as Tab); relabels itself
                     "Edit" while playing, and the tool panel hides
                     during Play since it isn't relevant there

RIGHT-SIDE PANEL (edit mode)
  Tool buttons    -- Wall, Wall Line, Goal, Spike, Ball start, Spinner,
                     Eraser. Walls are continuous line segments, not
                     pixel cells -- a diagonal/angled wall rolls the ball
                     smoothly at its own exact angle (see physics.py's
                     step_ball()/_wall_normal() for how), not an
                     approximation of one. Spikes don't block movement
                     like a wall -- touching one kills the ball and
                     respawns it at the start, same as falling off the
                     edge.
  Parameter       -- shows controls for whichever tool is selected:
  controls           Spinner -> half-length, speed, and starting-angle
                                 sliders, plus a Direction button that
                                 toggles clockwise/counterclockwise
                     Each new spinner placed picks up whatever the panel
                     is currently set to. Hovering the maze with Ball
                     start / Spinner selected shows a dimmed preview of
                     what clicking there would place.

EDIT MODE
  Left-drag       -- Wall: freehand-draws a connected line (a polyline)
                     following the cursor, one new wall per drag --
                     paint with Goal / Spike; with Eraser, clears
                     whatever's directly under the cursor -- a whole
                     wall (any point along it) or spinner (if it
                     touches its bar) as one object, or a single
                     goal/spike cell
  Left-click      -- Wall Line: click a start point, then an end point
                     to create a straight wall segment between them
                   -- Ball: sets the ball's start position
                   -- Spinner: adds a NEW one at that point, using
                     whatever the panel's parameters are set to (no
                     limit on how many)

PLAY MODE
  Mouse (or arrows/AD) -- tilt
  [ / ]                -- previous / next level
  R                     -- restart at the current start point
  Esc / close window    -- quit
"""

import os
import sys
import subprocess
import math
import json
import pygame
import pygame_gui

# Resolve circuitpython/ relative to this script's own location (tools/
# level_editor.py -> ../circuitpython), both to import the shared physics
# module from there and to locate levels_data.json below.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CIRCUITPYTHON_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "circuitpython"))
sys.path.insert(0, CIRCUITPYTHON_DIR)

from physics import (  # noqa: E402 -- must follow the sys.path insert above
    WIDTH, HEIGHT, BALL_RADIUS, BAR_HALF_THICKNESS, BOOST_SPEED, GOAL_WIN_PIXELS,
    static_color, bar_segments,
    idx, set_cell, clear_cell, clear_level,
    apply_walls, apply_goals, apply_spikes,
    ball_pixels_at, ball_touches_color, segment_pixels_for_draw,
    point_segment_distance, update_bar_segments, step_ball, update_ball_roll,
    ball_accent_pixels_at,
)

# Both Save (S) and Load (L) prompt with a file picker rather than
# assuming this path -- it's only used as the picker's starting
# directory and Save's default filename.
DEFAULT_LEVELS_PATH = os.path.join(CIRCUITPYTHON_DIR, "levels_data.json")


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
        "    filetypes=[('JSON files', '*.json'), ('All files', '*.*')],\n"
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


def pick_save_file():
    """Open a native "save as" file picker and return the chosen path, or
    '' if the user cancelled or something went wrong. Defaults to the
    name levels_data.json but lets the user change it. Runs in a separate
    subprocess for the same reason as pick_load_file() above."""
    initial_dir = os.path.dirname(DEFAULT_LEVELS_PATH)
    if not os.path.isdir(initial_dir):
        initial_dir = SCRIPT_DIR

    script = (
        "import tkinter, tkinter.filedialog as fd\n"
        "root = tkinter.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "path = fd.asksaveasfilename(\n"
        "    title='Save level data',\n"
        f"    initialdir={initial_dir!r},\n"
        "    initialfile='levels_data.json',\n"
        "    defaultextension='.json',\n"
        "    filetypes=[('JSON files', '*.json'), ('All files', '*.*')],\n"
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
PANEL_WIDTH = 220
TOP_BAR_HEIGHT = 32

TOOL_NAMES = {
    1: "Wall", 2: "Wall Line", 3: "Goal", 4: "Ball start", 5: "Spinner",
    6: "Eraser", 7: "Spike",
}

# ---------- Right-side panel layout ----------
# Computed up front so PANEL_CONTENT_HEIGHT is known before the panel
# (a scrolling container, see below) is created -- it's the panel's
# full scrollable content height, which can run taller than the maze
# area once there are enough tool buttons plus a parameter stack below
# them (e.g. the Spinner's half-length/speed/angle/Direction group);
# the panel scrolls to reach whatever doesn't fit rather than needing
# the window stretched to fit it. BTN_W is NOT set here -- it depends
# on the panel's actual inner width (narrower once its scrollbar
# appears), so it's set once the panel exists, right before it's used.
PANEL_PAD = 10
BTN_H = 26
BTN_GAP = 4
PARAMS_Y = PANEL_PAD + len(TOOL_NAMES) * (BTN_H + BTN_GAP) + 10
# The Spinner parameter stack (half-length, speed, start angle, each a
# label+slider, then the Direction button) is currently the tallest
# parameter group any tool shows -- its own bottom edge, relative to
# PARAMS_Y, mirrors the widgets' actual relative_rect y-offsets below.
SPINNER_PARAMS_BOTTOM = PARAMS_Y + 150 + 22
PANEL_CONTENT_HEIGHT = SPINNER_PARAMS_BOTTOM + PANEL_PAD

COLORS = {
    0: (10, 10, 14),
    1: (255, 51, 0),
    2: (34, 102, 255),
    3: (0, 255, 0),
    4: (170, 0, 255),
    5: (255, 255, 255),  # ball roll accent -- a bright star/sparkle against the red ball
    7: (255, 20, 147),  # spikes -- matches physics.py's set_cell() spike color index
}
BALL_ROLL_ACCENT_COLOR = 5


def in_bounds(x, y):
    return 0 <= x < WIDTH and 0 <= y < HEIGHT


def make_spinner(px, py, half_len=8, speed=0.04, start_angle=0.0, direction=1):
    """"angle" is live state the physics engine advances every play-mode
    frame (sp["angle"] += sp["speed"] * sp["direction"]); "start_angle" is
    a frozen copy of what it began at, kept alongside so
    snapshot_current_level() can save the level's intended starting angle
    even after play-testing has spun "angle" away from it. "direction" is
    1 (clockwise) or -1 (counterclockwise)."""
    return {"pivot_x": float(px), "pivot_y": float(py), "half_len": half_len,
            "angle": start_angle, "speed": speed, "start_angle": start_angle,
            "direction": direction}


# ---------- pygame setup ----------
# The maze area is drawn onto its own maze_surface at its original
# (0, 0)-relative coordinates -- completely unchanged math from before
# the top menu bar existed -- and that surface is blitted onto the real
# screen at (0, TOP_BAR_HEIGHT) each frame. This keeps every existing
# drawing/hit-testing function's coordinates untouched; only the blit
# offset and the mouse-position-to-cell translation need to know about
# the bar's height.
pygame.init()
# The window is sized to the maze's own natural height -- the panel no
# longer needs the window stretched to fit its content (which used to
# leave a black bar under the playfield whenever the panel's content
# was taller than the maze); it scrolls internally instead (see `panel`
# below).
MAZE_AREA_HEIGHT = HEIGHT * SCALE
WINDOW_SIZE = (WIDTH * SCALE + PANEL_WIDTH, TOP_BAR_HEIGHT + MAZE_AREA_HEIGHT)
screen = pygame.display.set_mode(WINDOW_SIZE)
pygame.display.set_caption("Tilt Maze -- Level Editor")
clock = pygame.time.Clock()
font = pygame.font.SysFont(None, 28)
small_font = pygame.font.SysFont(None, 18)
# Gives the Play button its own green look (via object_id="#play_button"
# below) so it reads as an action button rather than another menu label.
UI_THEME = {
    "#play_button": {
        "colours": {
            "normal_bg": "#2e7d32",
            "hovered_bg": "#43a047",
            "active_bg": "#1b5e20",
            "normal_text": "#ffffff",
            "hovered_text": "#ffffff",
        }
    }
}
gui_manager = pygame_gui.UIManager(WINDOW_SIZE, UI_THEME)
maze_surface = pygame.Surface((WIDTH * SCALE, HEIGHT * SCALE))

# ---------- Multi-level state ----------
# Each entry: {"start": (x, y),
#              "walls": [[(x0,y0), (x1,y1), ...], ...],
#              "goals": [(x0, x1, y), ...], "spikes": [(x0, x1, y), ...],
#              "spinners": [(pivot_x, pivot_y, half_len, speed,
#                            start_angle_degrees, direction), ...]}
# "walls" is a list of polylines -- each a list of >=1 points, consecutive
# pairs becoming one collision segment each (see apply_walls() in
# physics.py, whose actual math is fully continuous/float-based
# regardless of what produced the points). A straight Wall Line is just
# a 2-point polyline; a freehand Wall stroke is an N-point one -- both
# tools place their points on the whole-pixel grid (same as Ball
# start/Spinner), not the raw continuous mouse position. There's no
# separate ramp type -- a wall at any angle rolls the ball at that exact
# angle (see physics.py's step_ball()), nothing extra to track per wall.
levels_data = []
current_level_idx = 0

current_start_x, current_start_y = 25, 6
ball_start_set = False  # True once a ball start has actually been placed/loaded
spinners = []
walls = []  # list of polylines -- see the "Multi-level state" comment above
mode = "edit"
tool = 1
line_click_start = None
# The in-progress freehand Wall stroke's points (tool 1), or None when not
# currently dragging one. Points are whole-pixel grid cells (same as
# Wall Line/Ball start/Spinner), not the raw continuous mouse position --
# appended to only when the cursor has moved into a DIFFERENT cell than
# the last one recorded (see FREEHAND_MIN_STEP) to avoid flooding
# duplicate points every frame it's held over the same cell; flushed
# into `walls` on release.
current_freehand_points = None
FREEHAND_MIN_STEP = 0.5
# How close a click needs to land to a wall's true line to erase it (see
# wall_at_point()) -- deliberately its own constant rather than reusing
# physics.py's WALL_HALF_THICKNESS (a small collision-only buffer, not
# imported here at all -- see its comment in physics.py), which is far
# too tight a margin for a click to reliably land inside. This is purely
# an editor click-forgiveness margin, same role BAR_HALF_THICKNESS + 0.5
# plays for erasing a spinner (see thing_at_cell()), with no physics
# meaning.
WALL_ERASE_TOLERANCE = 1.5
spinner_half_len = 8
spinner_speed = 0.04
spinner_start_angle_deg = 0
spinner_direction = 1  # 1 = clockwise, -1 = counterclockwise

ball_x = ball_y = vel_x = vel_y = 0.0
ball_rotation = 0.0
ball_roll_direction = (1.0, 0.0)
won = False
win_timer = 0.0

# One-time-per-attempt brake (Left Shift) / boost (Space) -- mirrors
# code.py's board-button mapping (left=brake, right=boost) so the same
# feel can be playtested here. See physics.py's step_ball() `boost`
# parameter and code.py's BOOST_* handling for the on-device version.
BOOST_FRAMES = 11
BRAKE_FRAMES = 55
brake_used = False
boost_used = False
boost_requested = False  # set on Space's keydown, consumed once tilt is known
brake_frames_left = 0
boost_frames_left = 0
boost_dir = 0.0


# ---------- Right-side GUI panel ----------
# Tool buttons replace the old number-key shortcuts; the parameter group
# below them swaps to match whichever tool is selected (currently just
# Spinner -> half-length/speed/angle/direction) since only one tool's
# parameters are ever relevant at a time. Walls have a single fixed
# WALL_HALF_THICKNESS (see physics.py) rather than a per-wall setting, so
# neither Wall tool needs a parameter control. (PANEL_PAD/BTN_W/BTN_H/
# BTN_GAP/PARAMS_Y are all defined earlier, up with TOOL_NAMES.)
PANEL_X = WIDTH * SCALE

# A scrolling container rather than a plain panel -- its content
# (PANEL_CONTENT_HEIGHT, from the tool buttons down through the Spinner
# parameter stack) is taller than the maze area whenever there are
# enough tools/params, and this lets that overflow scroll into view
# instead of requiring the window itself to grow to fit it (which used
# to leave a black bar under the playfield).
panel = pygame_gui.elements.UIScrollingContainer(
    relative_rect=pygame.Rect(PANEL_X, TOP_BAR_HEIGHT, PANEL_WIDTH, MAZE_AREA_HEIGHT),
    manager=gui_manager,
    allow_scroll_x=False,
)
panel.set_scrollable_area_dimensions((PANEL_WIDTH, PANEL_CONTENT_HEIGHT))
# The vertical scrollbar (shown whenever PANEL_CONTENT_HEIGHT exceeds
# the visible MAZE_AREA_HEIGHT, which is the whole reason it's a
# scrolling container) eats into the container's own width, so widgets
# need to be sized off its actual inner width rather than the full
# PANEL_WIDTH -- otherwise a widget would run under/behind the
# scrollbar instead of stopping short of it.
BTN_W = panel.get_container().get_abs_rect().width - 2 * PANEL_PAD

tool_buttons = {}
for i, tool_id in enumerate(sorted(TOOL_NAMES)):
    y = PANEL_PAD + i * (BTN_H + BTN_GAP)
    btn = pygame_gui.elements.UIButton(
        relative_rect=pygame.Rect(PANEL_PAD, y, BTN_W, BTN_H),
        text=TOOL_NAMES[tool_id],
        manager=gui_manager,
        container=panel,
    )
    tool_buttons[tool_id] = btn
tool_buttons[tool].select()

spinner_half_len_label = pygame_gui.elements.UILabel(
    relative_rect=pygame.Rect(PANEL_PAD, PARAMS_Y, BTN_W, 20),
    text=f"Half-length: {spinner_half_len}",
    manager=gui_manager, container=panel,
)
spinner_half_len_slider = pygame_gui.elements.UIHorizontalSlider(
    relative_rect=pygame.Rect(PANEL_PAD, PARAMS_Y + 22, BTN_W, 22),
    start_value=spinner_half_len, value_range=(2, 20), click_increment=1,
    manager=gui_manager, container=panel,
)
spinner_speed_label = pygame_gui.elements.UILabel(
    relative_rect=pygame.Rect(PANEL_PAD, PARAMS_Y + 50, BTN_W, 20),
    text=f"Speed: {spinner_speed:.2f}",
    manager=gui_manager, container=panel,
)
spinner_speed_slider = pygame_gui.elements.UIHorizontalSlider(
    relative_rect=pygame.Rect(PANEL_PAD, PARAMS_Y + 72, BTN_W, 22),
    start_value=spinner_speed, value_range=(0.01, 0.2), click_increment=0.01,
    manager=gui_manager, container=panel,
)
spinner_start_angle_label = pygame_gui.elements.UILabel(
    relative_rect=pygame.Rect(PANEL_PAD, PARAMS_Y + 100, BTN_W, 20),
    text=f"Start angle: {spinner_start_angle_deg}°",
    manager=gui_manager, container=panel,
)
spinner_start_angle_slider = pygame_gui.elements.UIHorizontalSlider(
    # A spinner bar through its pivot at angle a is pixel-for-pixel
    # identical to a+180 (same segment, endpoints just swapped), so
    # every distinct orientation is covered by 0..179.
    relative_rect=pygame.Rect(PANEL_PAD, PARAMS_Y + 122, BTN_W, 22),
    start_value=spinner_start_angle_deg, value_range=(0, 179), click_increment=15,
    manager=gui_manager, container=panel,
)
spinner_direction_button = pygame_gui.elements.UIButton(
    relative_rect=pygame.Rect(PANEL_PAD, PARAMS_Y + 150, BTN_W, 22),
    text=f"Direction: {'CW' if spinner_direction == 1 else 'CCW'}",
    manager=gui_manager, container=panel,
)

SPINNER_PARAM_WIDGETS = (
    spinner_half_len_label, spinner_half_len_slider,
    spinner_speed_label, spinner_speed_slider,
    spinner_start_angle_label, spinner_start_angle_slider,
    spinner_direction_button,
)
ALL_PARAM_WIDGETS = SPINNER_PARAM_WIDGETS


def sync_panel_to_tool():
    """Show only the parameter widgets relevant to the selected tool, and
    keep the tool buttons' selected-highlight in sync with `tool`."""
    for tool_id, btn in tool_buttons.items():
        if tool_id == tool:
            btn.select()
        else:
            btn.unselect()
    for w in ALL_PARAM_WIDGETS:
        w.hide()
    if tool == 5:
        for w in SPINNER_PARAM_WIDGETS:
            w.show()


sync_panel_to_tool()


# ---------- Top menu bar ----------
# pygame_gui has no native cascading menu-bar widget, so each top-level
# menu (File/Edit/Level) is a header button that opens a small free-
# floating panel of action buttons positioned just below it -- built
# fresh each time it's opened (Level's contents depend on how many
# levels currently exist) and torn down on any click elsewhere, on the
# header again, or after an action runs.
top_bar = pygame_gui.elements.UIPanel(
    relative_rect=pygame.Rect(0, 0, WIDTH * SCALE + PANEL_WIDTH, TOP_BAR_HEIGHT),
    manager=gui_manager,
)

MENU_BTN_W = 64
MENU_BTN_H = TOP_BAR_HEIGHT - 8
menu_header_buttons = {}
for i, name in enumerate(("File", "Edit", "Level")):
    menu_header_buttons[name.lower()] = pygame_gui.elements.UIButton(
        relative_rect=pygame.Rect(8 + i * (MENU_BTN_W + 4), 4, MENU_BTN_W, MENU_BTN_H),
        text=name, manager=gui_manager, container=top_bar,
    )

PLAY_BTN_W = 70
play_button = pygame_gui.elements.UIButton(
    relative_rect=pygame.Rect(8 + 3 * (MENU_BTN_W + 4) + 12, 4, PLAY_BTN_W, MENU_BTN_H),
    text="Play", manager=gui_manager, container=top_bar,
    object_id="#play_button",
)

open_menu_panel = None
open_menu_actions = {}
open_menu_name = None


def close_open_menu():
    global open_menu_panel, open_menu_actions, open_menu_name
    if open_menu_panel is not None:
        open_menu_panel.kill()
    open_menu_panel = None
    open_menu_actions = {}
    open_menu_name = None


def menu_items_for(name):
    """(label, zero-arg callable) pairs for the given menu name. Level's
    list is rebuilt every time it's opened since it depends on how many
    levels currently exist and which one is selected."""
    if name == "file":
        return [("Save", action_save), ("Save As...", action_save_as), ("Open...", action_open)]
    if name == "edit":
        return [("Undo", do_undo), ("Redo", do_redo)]
    if name == "level":
        items = [("New", new_level), ("Delete", delete_current_level), ("Clear", clear_current_level)]
        for i in range(len(levels_data)):
            marker = "* " if i == current_level_idx else "  "
            items.append((f"{marker}Level {i + 1}", (lambda idx=i: goto_level(idx))))
        return items
    return []


def open_menu(name):
    global open_menu_panel, open_menu_actions, open_menu_name
    close_open_menu()
    header_btn = menu_header_buttons[name]
    header_rect = header_btn.get_abs_rect()
    items = menu_items_for(name)
    item_h = 26
    panel_w = 160
    content_h = len(items) * item_h + 8
    # Level's item count grows with the level list (New/Delete/Clear plus
    # one "Go to Level N" per level) and will eventually not fit below the
    # top bar -- a plain UIPanel would just run off the bottom of the
    # window uncropped. A scrolling container clips to whatever vertical
    # space is actually available and lets the rest scroll into view,
    # instead of growing the panel (and therefore the window) to fit
    # content that only exists while the dropdown happens to be open.
    available_h = max(item_h + 8, WINDOW_SIZE[1] - TOP_BAR_HEIGHT)
    panel_h = min(content_h, available_h)
    panel = pygame_gui.elements.UIScrollingContainer(
        relative_rect=pygame.Rect(header_rect.left, TOP_BAR_HEIGHT, panel_w, panel_h),
        manager=gui_manager,
        allow_scroll_x=False,
    )
    panel.set_scrollable_area_dimensions((panel_w, content_h))
    # The scrollbar (when the content doesn't all fit) eats into the
    # container's own width, so size buttons off its actual inner width
    # rather than panel_w -- otherwise a button would run under/behind
    # the scrollbar instead of stopping short of it.
    inner_w = panel.get_container().get_abs_rect().width
    actions = {}
    for i, (label, action) in enumerate(items):
        btn = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect(4, 4 + i * item_h, inner_w - 8, item_h - 2),
            text=label, manager=gui_manager, container=panel,
        )
        actions[btn] = action
    open_menu_panel = panel
    open_menu_actions = actions
    open_menu_name = name


def toggle_menu(name):
    if open_menu_name == name:
        close_open_menu()
    else:
        open_menu(name)



def snapshot_current_level():
    goals = []
    spikes = []
    for y in range(HEIGHT):
        x = 0
        while x < WIDTH:
            sc = static_color[idx(x, y)]
            if sc == 0 or sc == 2:
                # 0 = empty; 2 = wall -- walls are no longer grid-derived,
                # the live `walls` list below is the authoritative source.
                x += 1
                continue
            if sc == 3:
                span, target = goals, 3
            else:  # sc == 7, spike
                span, target = spikes, 7
            x2 = x
            while x2 + 1 < WIDTH and static_color[idx(x2 + 1, y)] == target:
                x2 += 1
            span.append((x, x2, y))
            x = x2 + 1
    # pivot_x/pivot_y saved as whole numbers -- like the ball start, a
    # spinner is always placed on the pixel grid (see the Spinner tool's
    # click handler), so the fractional part is always .0 anyway.
    # start_angle is saved in whole-number degrees rather than radians --
    # readable in the JSON file, and the Start angle slider only ever
    # produces whole degrees anyway (0-179 in steps of 15) -- converted
    # back to radians on load, since that's what the trig everywhere
    # else (here and in physics.py) needs.
    sps = [
        (
            int(s["pivot_x"]), int(s["pivot_y"]), s["half_len"], s["speed"],
            int(round(math.degrees(s["start_angle"]))), s["direction"],
        )
        for s in spinners
    ]
    # Saved as whole numbers -- the ball start is always placed on the
    # pixel grid (see the Ball start tool's click handler), so the
    # fractional part is always .0 anyway.
    start = (int(current_start_x), int(current_start_y))
    # A shallow copy of `walls` -- each polyline list inside it is only
    # ever replaced wholesale (appended to `walls`/removed from it), never
    # mutated in place, so sharing those inner lists between this
    # snapshot and the live editor state is safe; only the outer list
    # needs to be independent so a later live edit (walls.append(...))
    # can't also silently rewrite an already-saved undo/redo snapshot.
    return {"start": start, "walls": list(walls), "goals": goals, "spikes": spikes, "spinners": sps}


def apply_level_snapshot(data):
    global current_start_x, current_start_y, spinners, ball_start_set, walls
    clear_level()
    walls = list(data["walls"])
    apply_walls(walls)
    apply_goals(data["goals"])
    apply_spikes(data.get("spikes", []))  # .get: older saved levels predate spikes
    current_start_x, current_start_y = data["start"]
    ball_start_set = True
    spinners = [
        make_spinner(px, py, half_len, speed, math.radians(start_angle_deg), direction)
        for px, py, half_len, speed, start_angle_deg, direction in data["spinners"]
    ]


# ---------- Undo/redo (scoped to the level currently being edited --
# switching levels, loading a file, etc. clears both stacks) ----------
undo_stack = []
redo_stack = []
UNDO_LIMIT = 50


def push_undo():
    """Snapshot the current level's state before a mutating action.
    Continuous drag-painting (Wall/Goal/Eraser) only calls this once per
    stroke -- see painting_stroke_active in handle_edit_painting() --
    so one drag undoes as a single step rather than one per pixel.
    Pairs the snapshot with ball_start_set -- snapshot_current_level()'s
    dict always carries a "start" position (it's the save-file format,
    which has no way to express "no ball yet"), so without tracking this
    separately, undoing back past the very first ball placement would
    make apply_level_snapshot() force ball_start_set True again and the
    ball would reappear even though it hadn't been placed yet at that
    point in history."""
    undo_stack.append((snapshot_current_level(), ball_start_set))
    if len(undo_stack) > UNDO_LIMIT:
        undo_stack.pop(0)
    redo_stack.clear()


def clear_undo_history():
    undo_stack.clear()
    redo_stack.clear()


def do_undo():
    global ball_start_set
    if not undo_stack:
        return
    redo_stack.append((snapshot_current_level(), ball_start_set))
    data, was_set = undo_stack.pop()
    apply_level_snapshot(data)
    ball_start_set = was_set


def do_redo():
    global ball_start_set
    if not redo_stack:
        return
    undo_stack.append((snapshot_current_level(), ball_start_set))
    data, was_set = redo_stack.pop()
    apply_level_snapshot(data)
    ball_start_set = was_set


def goto_level(new_idx, save_first=True):
    global current_level_idx
    if not levels_data:
        return
    if save_first:
        levels_data[current_level_idx] = snapshot_current_level()
    current_level_idx = new_idx % len(levels_data)
    apply_level_snapshot(levels_data[current_level_idx])
    clear_undo_history()
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
    blank = {"start": (32, 16), "walls": [], "goals": [], "spikes": [], "spinners": []}
    levels_data.append(blank)
    current_level_idx = len(levels_data) - 1
    apply_level_snapshot(blank)
    clear_undo_history()
    ball_start_set = False  # a new level starts with no ball placed yet


def delete_current_level():
    global current_level_idx
    if len(levels_data) <= 1:
        return
    del levels_data[current_level_idx]
    current_level_idx = min(current_level_idx, len(levels_data) - 1)
    apply_level_snapshot(levels_data[current_level_idx])
    clear_undo_history()


def format_json(obj, indent=0, max_width=100):
    """Like json.dumps(obj, indent=2), except an array is only broken
    onto multiple lines if it holds objects or arrays of its own --  a
    "leaf" array of plain numbers/strings (a cell span, an [x, y] point,
    ...) stays on one line instead of one number per line. And when an
    array's elements are ALL leaf arrays themselves (goals/spikes' short
    [x0, x1, y] spans, a wall polyline's own [x, y] points, ...), pack as
    many as fit within max_width per line instead of one each -- one-per-
    line made levels_data.json far longer than the data actually needs.
    If the WHOLE packed result would fit on a single line anyway (a
    short wall -- e.g. a straight 2-point Wall Line -- or a short list of
    goal/spike spans), collapse it to just that one line rather than
    still wrapping it in its own opening-/closing-bracket lines."""
    pad = "  " * indent
    pad_in = "  " * (indent + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [f"{pad_in}{json.dumps(k)}: {format_json(v, indent + 1)}" for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(obj, (list, tuple)):
        if not obj:
            return "[]"
        is_leaf = lambda v: not isinstance(v, (dict, list, tuple))
        if obj and all(isinstance(v, (list, tuple)) and all(is_leaf(x) for x in v) for v in obj):
            rendered = [json.dumps(list(v)) for v in obj]
            one_line = "[" + ", ".join(rendered) + "]"
            if len(pad) + len(one_line) <= max_width:
                return one_line
            lines, line = [], pad_in
            for i, r in enumerate(rendered):
                piece = r + ("," if i < len(rendered) - 1 else "")
                if line != pad_in and len(line) + 1 + len(piece) > max_width:
                    lines.append(line)
                    line = pad_in + piece
                else:
                    line += (" " if line != pad_in else "") + piece
            lines.append(line)
            return "[\n" + "\n".join(lines) + "\n" + pad + "]"
        if any(isinstance(v, (dict, list, tuple)) for v in obj):
            items = [pad_in + format_json(v, indent + 1) for v in obj]
            return "[\n" + ",\n".join(items) + "\n" + pad + "]"
        return "[" + ", ".join(json.dumps(v) for v in obj) + "]"
    return json.dumps(obj)


def save_levels_to_file(path=None):
    if path is None:
        path = DEFAULT_LEVELS_PATH
    if levels_data:
        levels_data[current_level_idx] = snapshot_current_level()
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(path, "w") as f:
        f.write(format_json(levels_data))
        f.write("\n")
    msg = f"Saved {len(levels_data)} levels -- copy just {path} to the board, no need to reflash the game"
    print(msg)


def load_levels_from_file(path=None):
    if path is None:
        path = DEFAULT_LEVELS_PATH
    global levels_data, current_level_idx
    with open(path) as f:
        levels_data = json.load(f)
    current_level_idx = 0
    if levels_data:
        apply_level_snapshot(levels_data[0])
    else:
        clear_level()
        spinners.clear()
        walls.clear()
    clear_undo_history()
    restart_ball()
    print(f"Loaded {len(levels_data)} levels from {path}")


# current_file_path is what File > Save writes to without prompting;
# File > Save As... and File > Open... both update it to whatever path
# was just picked, so a later plain Save goes back to that same file.
current_file_path = DEFAULT_LEVELS_PATH


def action_save():
    save_levels_to_file(current_file_path)


def action_save_as():
    global current_file_path
    chosen = pick_save_file()
    if chosen:
        current_file_path = chosen
        save_levels_to_file(chosen)


def action_open():
    global current_file_path
    chosen = pick_load_file()
    if chosen:
        try:
            load_levels_from_file(chosen)
            current_file_path = chosen
        except Exception as e:
            print(f"Couldn't load {chosen}: {e}")


def clear_current_level():
    push_undo()
    clear_level()
    spinners.clear()
    walls.clear()


def enter_play_mode():
    global mode, line_click_start, current_freehand_points
    if mode != "edit":
        return
    if not ball_start_set:
        return
    if levels_data:
        levels_data[current_level_idx] = snapshot_current_level()
    else:
        # Nothing in the list yet -- the blank canvas the editor starts
        # with is still a real, playable level (see new_level()'s same
        # handling), not "no levels" just because New/Load was never
        # explicitly pressed.
        levels_data.append(snapshot_current_level())
    mode = "play"
    line_click_start = None
    current_freehand_points = None
    panel.hide()
    play_button.set_text("Edit")
    countdown()
    restart_ball()


def exit_play_mode():
    global mode
    if mode != "play":
        return
    mode = "edit"
    panel.show()
    play_button.set_text("Play")


def cell_from_mouse(pos):
    """pos is a raw screen coordinate (e.g. from pygame.mouse.get_pos()
    or event.pos); the maze itself starts TOP_BAR_HEIGHT pixels down."""
    return pos[0] // SCALE, (pos[1] - TOP_BAR_HEIGHT) // SCALE


def point_from_mouse(pos):
    """Like cell_from_mouse(), but the exact continuous (float) maze
    coordinate under the cursor, not rounded down to a grid cell. Wall
    points themselves are always whole pixel cells (see
    current_freehand_points); this continuous version is only used for
    the Eraser's wall-proximity check, where sub-pixel precision in the
    cursor's own position is useful for deciding what's "close enough"
    without snapping the cursor itself to a cell first."""
    return pos[0] / SCALE, (pos[1] - TOP_BAR_HEIGHT) / SCALE


def thing_at_cell(x, y):
    """Return ('spinner', obj) for a spinner the Eraser is directly
    touching at grid cell (x, y), or None -- only hits when the cursor
    is actually on the spinner's bar, so dragging the Eraser across a
    wall can't accidentally delete a spinner elsewhere on the level.
    Walls are checked separately (see wall_at_point()), and goal/spike
    cells are erased directly by the caller, one pixel at a time."""
    for sp in spinners:
        # Computed directly (matching update_bar_segments()'s formula)
        # rather than read from the shared bar_segments list, since that
        # list is only refreshed once per frame and may not yet reflect
        # a spinner just added this same frame.
        a, hl = sp["angle"], sp["half_len"]
        x0 = sp["pivot_x"] - hl * math.cos(a)
        y0 = sp["pivot_y"] - hl * math.sin(a)
        x1 = sp["pivot_x"] + hl * math.cos(a)
        y1 = sp["pivot_y"] + hl * math.sin(a)
        if point_segment_distance(x, y, x0, y0, x1, y1) <= BAR_HALF_THICKNESS + 0.5:
            return "spinner", sp
    return None


def wall_at_point(x, y):
    """The wall polyline (a direct reference into `walls`) the Eraser is
    touching at the continuous point (x, y), or None -- erases a whole
    wall as one object (any point along it), same as a spinner, rather
    than trimming individual points out of it."""
    for w in walls:
        if len(w) == 1:
            x0, y0 = w[0]
            if point_segment_distance(x, y, x0, y0, x0, y0) <= WALL_ERASE_TOLERANCE:
                return w
        else:
            for (x0, y0), (x1, y1) in zip(w, w[1:]):
                if point_segment_distance(x, y, x0, y0, x1, y1) <= WALL_ERASE_TOLERANCE:
                    return w
    return None


def restart_ball():
    global ball_x, ball_y, vel_x, vel_y, ball_rotation, ball_roll_direction
    global brake_used, boost_used, boost_requested, brake_frames_left, boost_frames_left, boost_dir
    ball_x, ball_y = current_start_x, current_start_y
    vel_x, vel_y = 0.0, 0.0
    ball_rotation = 0.0
    ball_roll_direction = (1.0, 0.0)
    brake_used = False
    boost_used = False
    boost_requested = False
    brake_frames_left = 0
    boost_frames_left = 0
    boost_dir = 0.0


def draw_pixel_cells(pixels, color):
    for x, y in pixels:
        pygame.draw.rect(maze_surface, color, (x * SCALE, y * SCALE, SCALE - 1, SCALE - 1))


GRID_COLOR = (45, 45, 52)
CENTER_GRID_COLOR = (90, 90, 102)
HOVER_COLOR = (255, 255, 255)


def draw_grid():
    center_x, center_y = WIDTH // 2, HEIGHT // 2
    for x in range(WIDTH + 1):
        if x == center_x:
            continue
        px = x * SCALE
        pygame.draw.line(maze_surface, GRID_COLOR, (px, 0), (px, HEIGHT * SCALE))
    for y in range(HEIGHT + 1):
        if y == center_y:
            continue
        py = y * SCALE
        pygame.draw.line(maze_surface, GRID_COLOR, (0, py), (WIDTH * SCALE, py))
    # Drawn last, on top of every regular gridline, so the crossing
    # regular lines don't punch 1px gaps into the highlighted center
    # lines at each intersection.
    center_px = center_x * SCALE
    center_py = center_y * SCALE
    pygame.draw.line(maze_surface, CENTER_GRID_COLOR, (center_px, 0), (center_px, HEIGHT * SCALE))
    pygame.draw.line(maze_surface, CENTER_GRID_COLOR, (0, center_py), (WIDTH * SCALE, center_py))


def hover_cell():
    """The (x, y) grid cell the mouse is currently over, or None if it's
    outside the maze area (off the edge, over the top menu bar, or over
    the side panel) -- or covered by an open dropdown, which can extend
    down over the maze's screen area (see the MOUSEBUTTONDOWN handling in
    pump_events()). Without this, drag-painting's continuous
    mouse-button poll (handle_edit_painting()) can't tell a click on a
    menu item like Edit > Undo from a click on the maze cell underneath
    it, and would paint there (and, worse, push a spurious undo entry
    for it) even though the click was meant for the menu."""
    if open_menu_panel is not None:
        return None
    pos = pygame.mouse.get_pos()
    if pos[0] >= WIDTH * SCALE or pos[1] < TOP_BAR_HEIGHT or pos[1] >= TOP_BAR_HEIGHT + HEIGHT * SCALE:
        return None
    x, y = cell_from_mouse(pos)
    if not in_bounds(x, y):
        return None
    return x, y


def dim_color(color, factor=0.5):
    return tuple(int(c * factor) for c in color)


def draw_hover_highlight():
    cell = hover_cell()
    if cell is None:
        return
    x, y = cell
    pygame.draw.rect(maze_surface, HOVER_COLOR, (x * SCALE, y * SCALE, SCALE, SCALE), 1)


def draw_tool_preview():
    """A dimmed preview of what the current tool would place at the mouse's
    current cell before clicking: Ball start / Spinner show what would land
    there; Wall Line, once its first point is set, shows the line that
    would be painted if you clicked again now."""
    if tool not in (2, 4, 5):
        return
    cell = hover_cell()
    if cell is None:
        return
    x, y = cell
    if tool == 2:
        if line_click_start is None:
            return
        seg = (line_click_start[0], line_click_start[1], x, y)
        draw_pixel_cells(segment_pixels_for_draw(seg), dim_color(COLORS[2]))
    elif tool == 4:
        draw_pixel_cells(ball_pixels_at(x, y), dim_color(COLORS[1]))
    elif tool == 5:
        a = math.radians(spinner_start_angle_deg)
        seg = (
            x - spinner_half_len * math.cos(a), y - spinner_half_len * math.sin(a),
            x + spinner_half_len * math.cos(a), y + spinner_half_len * math.sin(a),
        )
        draw_pixel_cells(segment_pixels_for_draw(seg), dim_color(COLORS[4]))


def draw_board():
    maze_surface.fill(COLORS[0])
    for y in range(HEIGHT):
        for x in range(WIDTH):
            c = static_color[idx(x, y)]
            if c:
                pygame.draw.rect(maze_surface, COLORS[c], (x * SCALE, y * SCALE, SCALE - 1, SCALE - 1))
    for seg in bar_segments:
        draw_pixel_cells(segment_pixels_for_draw(seg), COLORS[4])


def blit_maze_and_flip():
    # Clear the whole screen first -- the maze blit only covers the maze
    # area, and used to rely on pygame_gui fully repainting its own
    # top-bar/panel pixels every frame on top of whatever was already
    # there. That held as long as nothing in those areas ever moved, but
    # the side panel's UIScrollingContainer shifts its widgets' drawn
    # position as it scrolls -- without a clear, each widget's previous
    # position (now empty space, not covered by any widget) never gets
    # painted over, leaving a ghost trail behind it as it scrolls.
    screen.fill((0, 0, 0))
    screen.blit(maze_surface, (0, TOP_BAR_HEIGHT))
    gui_manager.draw_ui(screen)
    pygame.display.flip()


def countdown():
    for text in ("3", "2", "1", "GO!"):
        pump_events()
        update_bar_segments(spinners)
        draw_board()
        label_surf = font.render(text, True, (255, 255, 255))
        rect = label_surf.get_rect(center=(WIDTH * SCALE // 2, HEIGHT * SCALE // 2))
        maze_surface.blit(label_surf, rect)
        blit_maze_and_flip()
        pygame.time.wait(500 if text != "GO!" else 300)


def pump_events():
    global mode, tool, line_click_start, current_freehand_points
    global current_start_x, current_start_y
    global spinner_half_len, spinner_speed
    global spinner_start_angle_deg, spinner_direction, ball_start_set
    global brake_used, boost_used, boost_requested, brake_frames_left
    for event in pygame.event.get():
        gui_manager.process_events(event)

        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()

        if event.type == pygame.MOUSEBUTTONDOWN and open_menu_panel is not None:
            in_dropdown = open_menu_panel.get_abs_rect().collidepoint(event.pos)
            in_header = any(b.get_abs_rect().collidepoint(event.pos) for b in menu_header_buttons.values())
            if not in_dropdown and not in_header:
                close_open_menu()
            # Either way, this click belongs to the menu system, not the
            # maze underneath it -- a dropdown's panel can extend down
            # into the maze's screen area, so without this, clicking an
            # item in it would ALSO paint/place whatever tool is
            # currently selected at the cell underneath the click.
            continue

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element in open_menu_actions:
                action = open_menu_actions[event.ui_element]
                close_open_menu()
                action()
            elif event.ui_element in menu_header_buttons.values():
                name = next(n for n, b in menu_header_buttons.items() if b is event.ui_element)
                toggle_menu(name)
            elif event.ui_element is play_button:
                exit_play_mode() if mode == "play" else enter_play_mode()
            elif mode == "edit" and event.ui_element in tool_buttons.values():
                tool = next(t for t, b in tool_buttons.items() if b is event.ui_element)
                if tool != 2:
                    line_click_start = None
                if tool != 1:
                    current_freehand_points = None
                sync_panel_to_tool()
            elif event.ui_element is spinner_direction_button:
                spinner_direction *= -1
                spinner_direction_button.set_text(f"Direction: {'CW' if spinner_direction == 1 else 'CCW'}")
            continue

        if event.type == pygame_gui.UI_HORIZONTAL_SLIDER_MOVED:
            if event.ui_element is spinner_half_len_slider:
                spinner_half_len = int(round(event.value))
                spinner_half_len_label.set_text(f"Half-length: {spinner_half_len}")
            elif event.ui_element is spinner_speed_slider:
                spinner_speed = round(event.value, 3)
                spinner_speed_label.set_text(f"Speed: {spinner_speed:.2f}")
            elif event.ui_element is spinner_start_angle_slider:
                spinner_start_angle_deg = int(round(event.value))
                spinner_start_angle_label.set_text(f"Start angle: {spinner_start_angle_deg}°")
            continue

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                pygame.quit()
                sys.exit()
            if event.key == pygame.K_TAB:
                if mode == "edit":
                    enter_play_mode()
                else:
                    exit_play_mode()
                line_click_start = None
                continue

            mods = pygame.key.get_mods()
            if mode == "edit" and mods & pygame.KMOD_CTRL and event.key == pygame.K_z:
                do_redo() if mods & pygame.KMOD_SHIFT else do_undo()
                continue

            if mode != "edit":
                if event.key == pygame.K_LEFTBRACKET:
                    goto_level(current_level_idx - 1, save_first=False)
                elif event.key == pygame.K_RIGHTBRACKET:
                    goto_level(current_level_idx + 1, save_first=False)
                elif event.key == pygame.K_r:
                    restart_ball()
                elif event.key in (pygame.K_LSHIFT, pygame.K_RSHIFT) and not brake_used:
                    brake_used = True
                    brake_frames_left = BRAKE_FRAMES
                elif event.key == pygame.K_SPACE and not boost_used:
                    boost_used = True
                    boost_requested = True

        if mode == "edit" and event.type == pygame.MOUSEBUTTONDOWN:
            if (
                event.pos[0] >= WIDTH * SCALE
                or event.pos[1] < TOP_BAR_HEIGHT
                or event.pos[1] >= TOP_BAR_HEIGHT + HEIGHT * SCALE
            ):
                continue  # click landed on the top menu bar or side panel
            pos = cell_from_mouse(event.pos)
            if event.button == 1:
                if tool == 2:
                    if line_click_start is None:
                        line_click_start = pos
                    else:
                        push_undo()
                        walls.append([line_click_start, pos])
                        apply_walls(walls)
                        line_click_start = None
                elif tool == 4:
                    push_undo()
                    current_start_x, current_start_y = float(pos[0]), float(pos[1])
                    ball_start_set = True
                elif tool == 5:
                    push_undo()
                    spinners.append(make_spinner(
                        pos[0], pos[1], spinner_half_len, spinner_speed,
                        math.radians(spinner_start_angle_deg), spinner_direction,
                    ))


painting_stroke_active = False


def handle_edit_painting():
    global painting_stroke_active, current_freehand_points
    if mode != "edit" or tool not in (1, 3, 6, 7):
        painting_stroke_active = False
        current_freehand_points = None
        return
    if not pygame.mouse.get_pressed()[0]:
        if tool == 1 and current_freehand_points:
            # Flush the finished stroke as one new wall -- even a stroke
            # that never moved far enough to gain a second point is kept
            # (a 1-point "wall" is a valid point obstacle; see
            # apply_walls() in physics.py), so a single click-and-release
            # with this tool still places something.
            walls.append(current_freehand_points)
            apply_walls(walls)
        painting_stroke_active = False
        current_freehand_points = None
        return
    cell = hover_cell()
    if cell is None:
        return
    x, y = cell
    if not painting_stroke_active:
        push_undo()
        painting_stroke_active = True
        if tool == 1:
            current_freehand_points = [(x, y)]
    if tool == 1:
        last_x, last_y = current_freehand_points[-1]
        if math.hypot(x - last_x, y - last_y) >= FREEHAND_MIN_STEP:
            current_freehand_points.append((x, y))
    elif tool == 3:
        set_cell(x, y, goal=True)
    elif tool == 7:
        set_cell(x, y, spike=True)
    elif tool == 6:
        found = thing_at_cell(x, y)
        if found is not None:
            spinners.remove(found[1])
        else:
            point = point_from_mouse(pygame.mouse.get_pos())
            wall = wall_at_point(point[0], point[1])
            if wall is not None:
                walls.remove(wall)
                apply_walls(walls)
            else:
                clear_cell(x, y)


# ---------- Startup: always begin empty; use L to load a levels_data.json ----------
levels_data = []
current_level_idx = 0
clear_level()
spinners = []
walls = []

restart_ball()

while True:
    time_delta = clock.tick(50) / 1000.0
    pump_events()
    gui_manager.update(time_delta)

    if mode == "edit":
        handle_edit_painting()
        update_bar_segments(spinners)
        draw_board()
        draw_grid()
        if ball_start_set:
            draw_pixel_cells(ball_pixels_at(current_start_x, current_start_y), COLORS[1])
        draw_tool_preview()
        if tool == 2 and line_click_start:
            pygame.draw.circle(
                maze_surface, (255, 255, 255),
                (line_click_start[0] * SCALE + SCALE // 2, line_click_start[1] * SCALE + SCALE // 2),
                4,
            )
        draw_hover_highlight()
        blit_maze_and_flip()
        continue

    if won:
        win_timer -= 1 / 50
        if win_timer <= 0:
            goto_level(current_level_idx + 1, save_first=False)
            won = False
        draw_board()
        blit_maze_and_flip()
        continue

    for sp in spinners:
        sp["angle"] += sp["speed"] * sp["direction"]
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
        # Wide on purpose -- this is the only way to release a GENTLE
        # (non-keyboard, non-full-speed) tilt back to exactly level, and
        # a narrow deadzone (previously 0.05, ~19px either side of
        # center at this SCALE) makes that release nearly impossible to
        # land precisely: any leftover fraction of a pixel off-center
        # keeps tilt just barely nonzero, which keeps step_ball()'s
        # tilt-spring actively fighting for that tiny target instead of
        # ever reaching the FRICTION-only coast (see FRICTION's comment
        # in physics.py) -- so gentler tilts never got to coast after
        # releasing the way a keyboard tap-and-release (which snaps
        # cleanly to exactly tilt=0) always could.
        if abs(raw_tilt) < 0.15:
            tilt = 0.0
        else:
            SENSITIVITY_CURVE = 2.0
            tilt = math.copysign(abs(raw_tilt) ** SENSITIVITY_CURVE, raw_tilt)

    if boost_requested:
        boost_requested = False
        boost_frames_left = BOOST_FRAMES
        boost_dir = tilt if tilt != 0 else (1.0 if vel_x >= 0 else -1.0)

    if boost_frames_left > 0:
        boost_frames_left -= 1
        boost = BOOST_SPEED if boost_dir > 0 else -BOOST_SPEED
    else:
        boost = 0.0

    if brake_frames_left > 0:
        brake_frames_left -= 1
        brake = True
    else:
        brake = False

    prev_ball_x, prev_ball_y = ball_x, ball_y
    ball_x, ball_y, vel_x, vel_y = step_ball(ball_x, ball_y, vel_x, vel_y, tilt, boost=boost, brake=brake)
    ball_roll_direction, ball_rotation = update_ball_roll(
        ball_roll_direction, ball_rotation, prev_ball_x, prev_ball_y, ball_x, ball_y
    )

    if ball_touches_color(ball_x, ball_y, 3, min_count=GOAL_WIN_PIXELS):
        won = True
        win_timer = 1.0
    elif (
        ball_touches_color(ball_x, ball_y, 7)  # spike -- instant death
        or ball_x < -BALL_RADIUS - 2
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
    pygame.draw.line(maze_surface, (60, 60, 70), (center_px, 0), (center_px, HEIGHT * SCALE), 1)
    marker_x = int(center_px + tilt * center_px)
    pygame.draw.polygon(
        maze_surface, (255, 255, 255),
        [(marker_x, HEIGHT * SCALE - 6), (marker_x - 5, HEIGHT * SCALE), (marker_x + 5, HEIGHT * SCALE)],
    )
    blit_maze_and_flip()
