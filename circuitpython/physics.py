"""
Shared collision/physics engine for the tilt-maze game.

Used by both circuitpython/code.py (the on-device game) and
tools/level_editor.py (the desktop editor/playtester) so a physics or
level-geometry change only has to be made once and both stay in sync --
historically the two were hand-copied and drifted out of sync repeatedly.

Pure logic only: no displayio, no pygame, no hardware. Callers own their
own rendering and input (potentiometer vs mouse/keyboard) and just read
the shared state (`static_color` for the goal/spike/wall-render grid,
`wall_segments`/`bar_segments` for collision) to decide what to draw.

Walls are continuous line segments (`wall_segments`), not grid cells --
collision against them is exact point-segment geometry at any angle, the
same math spinner bars have always used (see point_segment_distance()).
There's no separate "ramp" type -- a diagonal wall is just a wall at an
angle, and step_ball() derives how much to roll (vs. bounce) a contact
from how diagonal that angle actually is at collision time, not from
anything stored per wall. Goals and spikes remain on the simpler
per-pixel `static_color` grid (a plain position check, not a collision
shape), and are unaffected by any of this.

Import names directly (`from physics import static_color, set_cell, ...`)
-- `static_color` is mutated in place (never reassigned), and
`wall_segments`/`bar_segments` are cleared-and-refilled in place too, so a
plain import stays valid even after clear_level()/apply_walls() runs.
"""

import math

WIDTH = 64
HEIGHT = 32
BALL_RADIUS = 2
BAR_HALF_THICKNESS = 1.4
# A COLLISION-ONLY half-thickness -- walls always render as a bare 1px
# centerline regardless of this value (see segment_pixels_for_draw(),
# which has no thickness concept at all: an earlier version banded the
# rendered line out to match collision's reach, which worked for a flat
# wall but rendered a diagonal one 2-3x thicker than a flat one for no
# reason other than its angle). This constant now ONLY feeds the
# collision reach (BALL_RADIUS + WALL_HALF_THICKNESS, see
# _wall_contact()), controlling how far from that always-thin rendered
# line the ball's edge settles.
#
# There is no value that's simultaneously flush (0px gap) AND
# clip-proof at every angle, at this ball's small (2px) radius: the
# wall's Bresenham-rasterized line and the ball's own rounded sprite
# anchor each carry their own sub-pixel rounding error against the TRUE
# continuous collision math, worse on a diagonal wall than a flat one,
# so a flush fit always leaves SOME chance of the ball's rendered edge
# clipping a pixel into the wall under the wrong angle/speed/approach
# combination (confirmed directly via fuzz testing). Priority here is
# flush over clip-proof (explicit choice, the alternative -- a
# consistent ~1-2px gap at 2.0, fully clip-proof -- was tried and
# rejected): 1.4 is the highest value that still rests the ball flush
# (0px gap) against a flat wall in testing, which also happens to
# minimize the clip rate among the flush-fitting values (clipped in
# ~0.3% of several thousand fuzzed approach trials, vs ~24% back at the
# first value tried here, 0.4) -- rare, not eliminated.
WALL_HALF_THICKNESS = 1.4

MAX_SPEED = 3
# Each frame, vel_x closes this fraction of the gap to tilt*MAX_SPEED (its
# "target" speed for the current tilt) -- so a steady tilt settles at a
# speed proportional to how far it's tilted (like gravity along an incline)
# instead of most of the tilt range saturating to MAX_SPEED almost
# immediately. Higher = snappier/twitchier, lower = smoother/more gradual.
TILT_RESPONSE = 0.24
# Fraction of speed lost per frame once nothing is actively driving it --
# vel *= (1 - FRICTION), so 0 = no friction (coasts forever) and 1 =
# instant stop. Bigger number = more friction, like the name suggests.
# Only applies to vel_x while level (tilt == 0) -- while actively tilted,
# TILT_RESPONSE's spring governs vel_x instead (see step_ball()); vel_y
# has no tilt input at all, so it's always subject to FRICTION.
FRICTION = 0.05
# Below this speed, vel_x counts as "at rest" for deciding whether an
# opposing tilt is braking or accelerating -- see step_ball()'s "cradle"
# handling. Small enough to be imperceptible once treated as reached.
BRAKE_THRESHOLD = 0.05
# Speed shed per frame by the dedicated brake button only (see
# _brake_toward_zero() and step_ball()'s `brake` parameter). A flat
# amount, not a fraction of current speed like TILT_RESPONSE/FRICTION
# are -- a fraction-based brake decelerates hardest right when it's
# fastest and crawls to a stop asymptotically (in the limit, it never
# quite reaches exactly 0), which reads as snapping to a stop almost
# instantly rather than slowing down the way an actual car's brakes do:
# roughly constant deceleration regardless of current speed, arriving
# at exactly 0 in a fixed, predictable distance.
BRAKE_DECEL = 0.06
# Fraction of the opposing-tilt "cradle" catch's speed cut per frame --
# see the `opposing` case in step_ball(). Deliberately fast (unlike
# BRAKE_DECEL above): this case also fires every time a wall bounce
# reverses the ball's velocity against whatever tilt is still
# commanding, not just on a deliberate tilt reversal, so a slow catch
# here gives the ball a long runway to re-accelerate into the wall
# before the next hit -- which doesn't decay the bounce at all, it just
# wanders indefinitely instead of ever settling against the wall.
CRADLE_RESPONSE = 0.6
# Speed step_ball() pins vel_x to, every frame a one-shot boost (see its
# `boost` parameter) is active -- deliberately above MAX_SPEED, since
# the whole point is to briefly exceed normal top speed rather than just
# snap to it early.
BOOST_SPEED = MAX_SPEED * 1.15
WALL_DAMPING = -0.1
# The ball reflects off the wall segment's own exact angle (see
# _wall_normal()) rather than independently negating whichever axis got
# blocked, which is what let it get stuck reversing in place at a corner
# instead of deflecting off to the side. There's no separate "ramp"
# concept -- a wall at an angle IS a slope: WALL_RESTITUTION is scaled
# down by how diagonal that angle is (see the diagonal_factor comment in
# step_ball), from a square-on flat-wall bounce at one extreme to a
# frictionless roll/slide at the other, continuously at any angle, with
# nothing extra to configure per wall.
WALL_RESTITUTION = 0.98
# Below this incoming speed -- AND only while tilt is actively driving
# the ball into that same wall (see step_ball()) -- an impact is
# absorbed instead of bounced. Well under typical gameplay speeds
# (MAX_SPEED is several times this), so it only ever kicks in once a
# repeated, tilt-re-driven bounce has already died down this far, not
# on a normal fast hit. Has to stay above whatever the steady-state
# tilt-held-against-a-wall bounce amplitude actually settles into --
# which scales up with WALL_RESTITUTION/TILT_RESPONSE, so a bouncier
# wall or a stronger tilt both need a correspondingly higher value here
# or this never catches the cycle at all and it bounces forever instead
# of ever settling (checked directly: it does settle at this value, for
# the current WALL_RESTITUTION/TILT_RESPONSE).
REST_IMPACT_SPEED = 1.8
# Spinner bars use this exact same diagonal_factor-scaled reflection, not
# a separate bar-only restitution/roll formula -- see _bar_normal() and
# step_ball()'s bar-contact handling below.

# ---------- Level state (one level's worth of geometry) ----------
# static_color is the render/lookup grid for goals and spikes (plain
# position checks -- see code.py/level_editor.py's win/spike checks) and
# also where wall segments get rasterized to for DISPLAY only (see
# apply_walls() below); it is never consulted for wall collision.
static_color = bytearray(WIDTH * HEIGHT)
bar_segments = []  # current spinner bars as (x0, y0, x1, y1) tuples
wall_segments = []  # current level's walls as (x0, y0, x1, y1) tuples


def idx(x, y):
    return y * WIDTH + x


def set_cell(x, y, goal=False, spike=False):
    i = idx(x, y)
    if goal:
        static_color[i] = 3
    elif spike:
        # A spike doesn't block movement like a wall does, it's a hazard
        # the ball passes over. Touching it is a plain position check
        # against static_color, same as the goal check, done by the
        # caller (code.py / level_editor.py) rather than anything in
        # step_ball().
        static_color[i] = 7


def clear_cell(x, y):
    static_color[idx(x, y)] = 0


def clear_level():
    for i in range(WIDTH * HEIGHT):
        static_color[i] = 0
    wall_segments.clear()


def apply_walls(walls):
    """Build wall_segments (for collision) from a level's "walls" list --
    each entry a polyline: a list of >=1 [x, y] points, consecutive pairs
    becoming one segment each (a 1-point polyline becomes a single
    zero-length segment, i.e. a point obstacle). Also rasterizes every
    resulting segment into static_color for rendering only -- collision
    against a wall is always exact point-segment geometry (see
    step_ball()), never this grid. There's no separate ramp type -- a
    diagonal wall is just a wall at an angle; step_ball() derives how
    much to roll (vs. bounce) a contact from how diagonal the segment
    actually is at collision time, not from anything stored per wall.
    Caller is responsible for clear_level() first and for anything else
    the level format carries (goals, spikes, spinners, start position).

    Safe to call again later with an updated `walls` (e.g. the desktop
    editor, after one wall is added/erased) without a clear_level() in
    between -- clears this function's OWN previously-painted wall(2)
    pixels first, so a removed wall's old static_color pixels never
    linger as a stale rendering after it's gone. Goal/spike pixels are
    untouched either way, since those are never color 2."""
    for i in range(WIDTH * HEIGHT):
        if static_color[i] == 2:
            static_color[i] = 0
    wall_segments.clear()
    for points in walls:
        if len(points) == 1:
            x0, y0 = points[0]
            wall_segments.append((x0, y0, x0, y0))
        else:
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                wall_segments.append((x0, y0, x1, y1))
    for seg in wall_segments:
        for x, y in segment_pixels_for_draw(seg):
            static_color[idx(x, y)] = 2


def apply_goals(goals):
    """Paint a level's goal cells (levels_data's "goals" list, entries of
    (x0, x1, y)) into the grid."""
    for x0, x1, y in goals:
        for x in range(x0, x1 + 1):
            set_cell(x, y, goal=True)


def apply_spikes(spikes):
    """Paint a level's spike cells (levels_data's "spikes" list, entries
    of (x0, x1, y)) into the grid -- a hazard that instantly kills the
    ball on contact (checked by the caller, same as the goal check)."""
    for x0, x1, y in spikes:
        for x in range(x0, x1 + 1):
            set_cell(x, y, spike=True)


# The ball's rendered shape's reach, squared -- BALL_RADIUS+1 rather than
# BALL_RADIUS itself so the rounded shape below isn't a perfect (smaller)
# diamond/square. Purely a rendering concern now -- wall/bar collision
# reach is BALL_RADIUS + WALL_HALF_THICKNESS/BAR_HALF_THICKNESS against
# exact segment geometry (see _nearest_segment_contact()), independent of
# this.
BALL_REACH_SQ = BALL_RADIUS * BALL_RADIUS + 1

BALL_OFFSETS = []
for _dy in range(-BALL_RADIUS, BALL_RADIUS + 1):
    for _dx in range(-BALL_RADIUS, BALL_RADIUS + 1):
        if _dx * _dx + _dy * _dy <= BALL_REACH_SQ:
            BALL_OFFSETS.append((_dx, _dy))
del _dx, _dy


def ball_pixels_at(cx, cy):
    # _round_half_up, not int() (truncation/floor) -- floor always anchors
    # the rendered footprint to the LEFT of the true continuous center,
    # by up to just under a pixel depending on cx's fractional part. That
    # bias is invisible most of the time, but it's exactly why a wall
    # approached from one side rested with a clean 0px gap while the
    # SAME wall approached from the other side let the ball's rendered
    # pixels overlap it by a full pixel: floor() under-reaches on the
    # right (gap) and over-reaches on the left (overlap) by the same
    # fractional amount, in opposite directions. Rounding to the nearest
    # pixel instead centers the bias at +-0.5px either way instead of
    # 0/-1, which is what actually lets a small WALL_HALF_THICKNESS
    # collision buffer (see its comment) produce a clean, direction-
    # independent fit against the wall's rendered pixel.
    px = _round_half_up(cx)
    py = _round_half_up(cy)
    pts = []
    for dx, dy in BALL_OFFSETS:
        x = px + dx
        y = py + dy
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            pts.append((x, y))
    return pts


def ball_touches_color(cx, cy, color, min_count=1):
    """True if at least min_count pixels of the ball's sprite at (cx, cy)
    -- its full rendered footprint (see ball_pixels_at()), not just its
    center cell -- have the given static_color code. Used for goal/spike
    contact so a touch registers off actual overlapping pixels, matching
    how the ball visually touches a goal/spike, rather than off just its
    center. This also closes a tunneling gap: a goal/spike is often only
    one row thick, and checking just the ball's center pixel could skip
    clean over it in a single fast frame, but the ball's footprint
    (BALL_RADIUS=2, so ~5 pixels wide) is wider than any one frame's max
    movement (MAX_SPEED/BOOST_SPEED, both well under 4), so consecutive
    frames' footprints always overlap and can't both miss a row in
    between -- true regardless of min_count, since it only raises how
    much of that overlap has to land on the target color, not whether
    there's overlap at all.

    min_count > 1 (see GOAL_WIN_PIXELS) requires more than a single
    pixel's worth of overlap -- e.g. for the goal, so reaching it reads
    as the ball actually settling onto it, not just grazing its edge
    pixel while still mostly on the approach."""
    count = 0
    for x, y in ball_pixels_at(cx, cy):
        if static_color[idx(x, y)] == color:
            count += 1
            if count >= min_count:
                return True
    return False


# How many of the ball's own pixels have to land on a goal pixel before
# the level counts as won -- 1 (any single overlapping pixel) read as
# winning before the ball looked like it had really reached the goal,
# since the ball's footprint is round and its outermost pixels are only
# a thin sliver of it. Tune directly if this still feels early/late.
GOAL_WIN_PIXELS = 3


def update_ball_roll(direction, rotation, prev_x, prev_y, cx, cy):
    """Advance the rolling-ball animation state given the ball's rendered
    (on-screen, integer-pixel) position moved from (prev_x, prev_y) to
    (cx, cy) this frame. Deliberately uses int(...) positions rather than
    the raw continuous ones: the ball can drift by a fractional amount
    every frame without its rendered pixel actually changing (e.g.
    oscillating slightly while resting against a wall), and the roll
    animation shouldn't appear to advance when nothing visibly moved.
    Returns the updated (direction, rotation):

    - direction: unit (ux, uy) vector the ball is currently observed to
      be rolling along. Kept from the previous frame when the rendered
      position didn't change this frame, so a visibly-stopped ball
      freezes its animation instead of losing its heading.
    - rotation: angle (radians) advanced by arc length / BALL_RADIUS --
      arc length = radius * angle for something rolling without
      slipping. Deliberately left unwrapped (not modulo 2*pi): the
      ball's checker pattern (ball_accent_pixels_at()) slides its grid
      by rotation * BALL_RADIUS, i.e. the total arc length rolled --
      wrapping rotation back to 0 partway through a level would make
      that slide distance jump backwards discontinuously, which is
      exactly the "checkers jump instead of moving smoothly" bug this
      fixed. A float has more than enough precision for this to never
      matter in practice (it's reset to 0 on every level load/restart
      anyway, alongside ball_x/ball_y).

    Callers track both alongside ball_x/ball_y/vel_x/vel_y (it's display
    state, not something step_ball needs to know about)."""
    dx = int(cx) - int(prev_x)
    dy = int(cy) - int(prev_y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance > 0.0001:
        direction = (dx / distance, dy / distance)
    rotation = rotation + distance / BALL_RADIUS
    return direction, rotation


# Cell size (in pixels) of the checker grid painted on the ball's
# surface, sliding smoothly with distance rolled. Smaller cells mean
# more, smaller squares (more checkered) but a bigger worst-case jump
# per 1px slide, since the whole grid moves in lockstep and more
# 2px-spaced boundaries cross the ball's small (~21px) silhouette at
# once: measured directly in headless testing, 4px cells top out at
# ~6 pixels changing in a single step, 3px at ~8, and 2px at ~11. A
# rotating angle-sector "pinwheel" (each pixel's own angle decides its
# wedge, independent of the others) stays smooth at any density but no
# longer reads as a checkerboard.
BALL_CHECKER_SIZE = 2
# Slows the checker pattern's slide (see ball_accent_pixels_at() below)
# to a visually readable creep without touching the ball's actual
# speed/physics at all -- rotation itself still advances at the true
# rolling rate (arc length / BALL_RADIUS, see update_ball_roll()), this
# just scales it down ONLY for how far the accent grid slides each
# frame. Needed because true rolling, at this ball's small radius, slides
# the grid by close to a full BALL_CHECKER_SIZE cell (or more, at top
# speed) every single frame -- each frame crossing a whole cell boundary
# reads as the pattern randomly flickering rather than visibly rolling,
# since there's nothing slower in between for the eye to track. 1.0
# would be the true (unscaled) rolling rate; smaller is a slower-looking
# roll. Tune directly to taste.
BALL_CHECKER_SLIDE_SCALE = 0.3


def ball_accent_pixels_at(cx, cy, direction, rotation):
    """The (x, y) pixels to draw in a contrasting "roll accent" color on
    top of the base ball sprite -- alternating squares of a checkerboard
    painted on the ball. The grid itself is always axis-aligned (never
    rotated): continuously rotating a checker pattern this small just
    turns it into single-pixel noise once it's off-axis, since there
    are only a couple of pixels per cell to begin with and a rotated
    grid almost never lines back up with the pixel grid.

    Instead, the grid's origin SLIDES along the direction of travel, by
    rotation * BALL_RADIUS pixels (the true rolled arc length -- arc
    length = radius * angle) scaled down by BALL_CHECKER_SLIDE_SCALE for
    a readable on-screen creep instead of the true rolling rate, which
    at this ball's small radius slides the grid by close to a whole
    checker cell every frame. This also makes the slide correctly
    reverse for the reverse
    direction with no extra handling: shift_x/shift_y are scaled by
    direction's own (signed) ux/uy components directly, so rolling left
    (ux < 0) slides the grid the other way from rolling right on its
    own, unlike a rotation-angle-based approach (see BALL_CHECKER_SIZE's
    comment above) which needs rotation's sign corrected by hand since
    rotation itself only tracks how far the ball has turned, not which
    way."""
    ux, uy = direction
    slide = rotation * BALL_RADIUS * BALL_CHECKER_SLIDE_SCALE
    shift_x = math.floor(slide * ux)
    shift_y = math.floor(slide * uy)
    # Must match ball_pixels_at()'s anchor exactly -- these accent pixels
    # are drawn on top of the base ball sprite, using the same
    # BALL_OFFSETS shape, so a different anchor here shifts the whole
    # checker pattern by a pixel relative to where the ball itself is
    # actually drawn, poking accent pixels out past the ball's own
    # rendered edge instead of staying inside it.
    px, py = _round_half_up(cx), _round_half_up(cy)
    pts = []
    for dx, dy in BALL_OFFSETS:
        cell = math.floor((dx - shift_x) / BALL_CHECKER_SIZE) + math.floor((dy - shift_y) / BALL_CHECKER_SIZE)
        if cell % 2 == 0:
            x, y = px + dx, py + dy
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                pts.append((x, y))
    return pts


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


def _round_half_up(v):
    """int(v) rounded to the nearest integer, ties always resolved up --
    unlike the builtin round(), which uses banker's rounding (round half
    to EVEN): round(0.5)==0 but round(1.5)==2, round(2.5)==2 but
    round(3.5)==4, etc. A hand-drawn wall's endpoint lands on an exact
    .5 maze coordinate often (the level editor's mouse position is
    divided by its SCALE, an even number, so any screen position that's
    a multiple of SCALE/2 does) -- with round(), whether that endpoint's
    rendered pixel is nudged up or down then depends on the unrelated
    parity of its integer part, reading as the same-looking wall
    rendering a pixel off from where it was actually drawn/clicked,
    seemingly at random. This is also exactly why the levels_data.json
    wall-format migration needed a tiny epsilon nudge to render
    correctly -- same underlying ambiguity, worked around there instead
    of fixed here, before this function existed in its current form."""
    return math.floor(v + 0.5)


def segment_pixels_for_draw(seg):
    """The on-screen pixels for one (x0, y0, x1, y1) segment's centerline
    -- used to render both spinner bars and walls, always a bare 1px
    line regardless of either one's collision half-thickness
    (BAR_HALF_THICKNESS/WALL_HALF_THICKNESS).

    An earlier version of this banded the line out to match whichever
    half-thickness collision was using, so the ball's edge (which rests
    at BALL_RADIUS + half_thickness from the centerline, not AT the
    centerline) would never find a gap between itself and the nearest
    drawn pixel. That worked for an axis-aligned wall, where a
    neighboring pixel one step off the centerline really is 1 full unit
    away -- but a diagonal line passes much closer to its OWN off-
    centerline neighbors (as close as sin(angle) per step), so the same
    band distance that stayed a thin line on a flat wall pulled in extra
    rows/columns along anything diagonal, rendering 2-3x thicker than a
    flat wall for no reason other than its angle. Rendering is simpler
    and more predictable just always drawing the bare line and instead
    giving collision a little its own slack to round into -- see
    WALL_HALF_THICKNESS's comment for how that's done without a gap."""
    x0, y0, x1, y1 = seg
    pts = set()
    x0i, y0i = int(_round_half_up(x0)), int(_round_half_up(y0))
    x1i, y1i = int(_round_half_up(x1)), int(_round_half_up(y1))
    for x, y in bresenham_line(x0i, y0i, x1i, y1i):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            pts.add((x, y))
    return pts


def update_bar_segments(spinners):
    """Recompute bar_segments from the current spinner angles."""
    bar_segments.clear()
    for sp in spinners:
        a = sp["angle"]
        hl = sp["half_len"]
        x0 = sp["pivot_x"] - hl * math.cos(a)
        y0 = sp["pivot_y"] - hl * math.sin(a)
        x1 = sp["pivot_x"] + hl * math.cos(a)
        y1 = sp["pivot_y"] + hl * math.sin(a)
        bar_segments.append((x0, y0, x1, y1))


def _nearest_segment_contact(cx, cy, segments, contact_dist):
    """(d, nx, ny) for the nearest segment in `segments` within
    contact_dist of (cx, cy) -- d is the distance to its surface, (nx, ny)
    the unit normal pointing from that surface toward (cx, cy) -- or None
    if nothing in `segments` is within range. Shared by bars and walls:
    both are just "segments with a half-thickness" as far as collision is
    concerned (see _bar_contact()/_wall_contact())."""
    best = None
    for seg in segments:
        d = point_segment_distance(cx, cy, seg[0], seg[1], seg[2], seg[3])
        if d < contact_dist and (best is None or d < best[0]):
            x0, y0, x1, y1 = seg
            dxs = x1 - x0
            dys = y1 - y0
            length_sq = dxs * dxs + dys * dys
            if length_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((cx - x0) * dxs + (cy - y0) * dys) / length_sq))
            closest_x = x0 + t * dxs
            closest_y = y0 + t * dys
            if d > 0.0001:
                nx = (cx - closest_x) / d
                ny = (cy - closest_y) / d
            else:
                seg_len = math.sqrt(length_sq) if length_sq > 0 else 1.0
                nx = -dys / seg_len
                ny = dxs / seg_len
            best = (d, nx, ny)
    return best


def _bar_contact(cx, cy):
    """_nearest_segment_contact() against the current spinner bars. Bars
    move (they rotate continuously), so unlike a wall this same geometry
    also has to be checked against the ball's CURRENT, already-settled
    position every frame (see step_ball), not just against where the ball
    is trying to move next -- since the bar itself can sweep into a spot
    the ball was already resting in."""
    return _nearest_segment_contact(cx, cy, bar_segments, BALL_RADIUS + BAR_HALF_THICKNESS)


def _bar_normal(cx, cy):
    """(nx, ny) from _bar_contact(cx, cy), or None."""
    contact = _bar_contact(cx, cy)
    return (contact[1], contact[2]) if contact else None


def _wall_contact(cx, cy):
    """_nearest_segment_contact() against the current level's walls.
    Unlike bars, walls are static, so (unlike _bar_contact) this is only
    ever queried reactively -- against where the ball is trying to move
    next, inside step_ball()'s circle_blocked()/_contact_normal() calls --
    never against the ball's current, already-valid resting position,
    since a static wall can never sweep into a spot on its own."""
    return _nearest_segment_contact(cx, cy, wall_segments, BALL_RADIUS + WALL_HALF_THICKNESS)


def _wall_normal(cx, cy):
    """(nx, ny) from _wall_contact(cx, cy), or None."""
    contact = _wall_contact(cx, cy)
    return (contact[1], contact[2]) if contact else None


def circle_blocked(cx, cy):
    """True if a circle at (cx, cy) touches a spinner bar or a wall --
    step_ball() treats both the same way (see _contact_normal()), so
    callers don't need to know which kind."""
    return _bar_normal(cx, cy) is not None or _wall_normal(cx, cy) is not None


# How far apart consecutive points checked along a movement step (see
# _swept_blocked()) are allowed to be -- comfortably less than the
# smallest collision margin (BALL_RADIUS + whichever of
# BAR_HALF_THICKNESS/WALL_HALF_THICKNESS is smaller) so two consecutive
# checks can never straddle something solid without either one landing
# inside it.
SWEEP_STEP = 1.5


def _swept_blocked(x0, y0, x1, y1):
    """True if the straight path from (x0, y0) to (x1, y1) is blocked at
    ANY point along it, not just at the destination (x1, y1) --
    checking only the destination lets a fast enough single-frame move
    (see BOOST_SPEED, which can exceed the collision margin) land clean
    on the far side of a thin wall or bar without either endpoint ever
    registering as blocked, tunneling straight through whatever was in
    between. Subdivides the path into steps no longer than SWEEP_STEP."""
    dx = x1 - x0
    dy = y1 - y0
    dist = math.sqrt(dx * dx + dy * dy)
    if dist <= SWEEP_STEP:
        return circle_blocked(x1, y1)
    steps = int(math.ceil(dist / SWEEP_STEP))
    for i in range(1, steps + 1):
        t = i / steps
        if circle_blocked(x0 + dx * t, y0 + dy * t):
            return True
    return False


def _wall_blocked(cx, cy):
    """Like circle_blocked, but walls only -- no bar-proximity check.
    Used to keep the bar-contact position correction in step_ball() from
    ever shoving the ball into a wall: the normal per-frame collision
    code only checks whether the *next* step is blocked, so once a push
    embeds the ball's center inside a wall, nothing would ever move it
    back out again."""
    return _wall_normal(cx, cy) is not None


def _contact_normal(cx, cy):
    """_bar_normal() if a bar is within reach of (cx, cy), else
    _wall_normal() -- the single normal step_ball()'s collision response
    reflects/rolls against, so a spinner bar gets exactly the same
    diagonal-aware bounce-vs-roll treatment a wall does, including at a
    steep angle that would otherwise read as a flat-on hit."""
    normal = _bar_normal(cx, cy)
    if normal is not None:
        return normal
    return _wall_normal(cx, cy)


def _brake_toward_zero(v):
    """v decelerated toward 0 by a flat BRAKE_DECEL, clamped at exactly 0
    rather than overshooting past it into the opposite sign."""
    if v > 0:
        return max(0.0, v - BRAKE_DECEL)
    if v < 0:
        return min(0.0, v + BRAKE_DECEL)
    return v


def step_ball(ball_x, ball_y, vel_x, vel_y, tilt, boost=0.0, brake=False):
    """Advance the ball one physics tick: tilt-driven acceleration, then
    collision with a geometry-derived roll/bounce against both the
    current level's walls and the current spinner bars. Returns the
    updated (ball_x, ball_y, vel_x, vel_y) -- callers handle their own
    win/out-of-bounds checks and rendering using the result.

    boost and brake are one-shot, caller-owned power-ups (see code.py's
    BOOST_*/BRAKE_* handling) -- physics.py has no notion of "remaining"
    or duration for either; the caller re-passes a truthy value every
    frame its effect should still be running (typically a fixed number
    of frames), and False/0.0 (the defaults) disable them entirely, each
    frame behaving exactly as if the parameter didn't exist.

    boost pins vel_x to its (signed) value for this frame instead of
    letting tilt/friction set it normally, with the usual +-MAX_SPEED
    clamp widened to match. brake overrides tilt entirely for this frame
    -- both axes decelerate toward 0 via _brake_toward_zero() regardless
    of what tilt is doing, rather than the one-frame "set velocity to 0"
    this used to be, which barely read as braking at all whenever tilt
    was still held: it got zeroed for exactly one frame and then started
    accelerating right back up the very next one, same as if nothing
    had happened."""
    # Bars move on their own (continuous rotation), unlike walls -- so a
    # bar can sweep into the ball's current, already-settled position
    # even with the ball not moving at all, which the position-advance
    # check below can't catch (it only reacts to the ball's own
    # attempted next step). Handle that intrusion here first, with the
    # same geometry-driven reflect used for everything else below.
    bar_contact = _bar_contact(ball_x, ball_y)
    if bar_contact is not None:
        d, nx, ny = bar_contact
        dot = vel_x * nx + vel_y * ny
        if dot < 0:
            diagonal_factor = 2 * abs(nx * ny)
            restitution = WALL_RESTITUTION * (1 - diagonal_factor)
            vel_x -= (1 + restitution) * dot * nx
            vel_y -= (1 + restitution) * dot * ny
        overlap = (BALL_RADIUS + BAR_HALF_THICKNESS) - d
        if overlap > 0:
            pushed_x = ball_x + nx * overlap
            pushed_y = ball_y + ny * overlap
            if not _wall_blocked(pushed_x, pushed_y):
                ball_x, ball_y = pushed_x, pushed_y
            # else: leave position alone -- pushing here would bury the
            # ball in a wall. The velocity reflection above still
            # applies, and the wall-collision pass below keeps it out
            # of the wall from here.

    if boost != 0:
        vel_x = boost

    if brake:
        # Ignores tilt completely for as long as the caller keeps this
        # True -- both axes decelerate toward 0 at BRAKE_DECEL's flat,
        # car-like rate, instead of only ever fighting whatever tilt is
        # currently commanding. Deliberately NOT the same mechanism the
        # opposing-tilt "cradle" case below uses (CRADLE_RESPONSE) --
        # see its comment for why that one has to stay fast.
        vel_x = _brake_toward_zero(vel_x)
        vel_y = _brake_toward_zero(vel_y)
    else:
        if tilt != 0:
            target_vel_x = tilt * MAX_SPEED
            # Tilting the SAME way the ball is already (meaningfully) moving,
            # or from a near-standstill, springs vel_x toward the target as
            # usual -- tilt commands a target speed (proportional to how far
            # it's tilted, like gravity along an incline -- see
            # TILT_RESPONSE's comment). But tilting AGAINST existing motion
            # (trying to stop or reverse it) used to spring straight toward
            # the full opposite target just as fast, which blew through zero
            # in about 3 frames at full speed/full opposite tilt -- reading
            # as "the ball instantly starts rolling the other way" instead of
            # ever actually catching it. Braking toward 0 specifically (not
            # the opposite target) while still meaningfully moving gives a
            # real, gradual "cradle" window: release tilt during it and the
            # ball just continues slowing to a stop like normal, instead of
            # rocketing past zero into reverse. Once speed decays under
            # BRAKE_THRESHOLD it counts as "at rest", and the normal spring
            # above takes over, accelerating into the new direction from a
            # standing start -- same as if that tilt had been applied fresh.
            opposing = (
                (vel_x > BRAKE_THRESHOLD and target_vel_x < 0)
                or (vel_x < -BRAKE_THRESHOLD and target_vel_x > 0)
            )
            if opposing:
                vel_x += (0 - vel_x) * CRADLE_RESPONSE
            else:
                vel_x += (target_vel_x - vel_x) * TILT_RESPONSE
        else:
            # Level: nothing is commanding a target speed anymore, so instead
            # of springing straight to 0 (which used to happen even with
            # FRICTION maxed out, since the spring above doesn't care what
            # FRICTION is set to -- tilt=0 just makes ITS OWN target 0 and
            # brakes for it), the ball coasts on its existing momentum and
            # only slows via FRICTION, same as vel_y always has.
            vel_x *= (1 - FRICTION)
        vel_y *= (1 - FRICTION)
    speed_cap = max(MAX_SPEED, abs(boost))
    vel_x = max(-speed_cap, min(speed_cap, vel_x))
    vel_y = max(-MAX_SPEED, min(MAX_SPEED, vel_y))

    x_stuck = False
    y_stuck = False
    incoming_vel_x, incoming_vel_y = vel_x, vel_y

    new_x = ball_x + vel_x
    if _swept_blocked(ball_x, ball_y, new_x, ball_y):
        x_stuck = True
    else:
        ball_x = new_x

    new_y = ball_y + vel_y
    if _swept_blocked(ball_x, ball_y, ball_x, new_y):
        y_stuck = True
    else:
        ball_y = new_y

    if x_stuck or y_stuck:
        # Reflect off the ACTUAL nearby wall geometry rather than just
        # negating whichever axis got blocked. Requiring BOTH axes to be
        # blocked before doing this (an earlier approach) misses the
        # single most common case: a ball moving mostly along one axis
        # with the other axis's velocity near zero can never register as
        # "blocked" on that axis at all -- moving by ~0 from an already-
        # valid position trivially succeeds -- so a ball sliding along a
        # flat run into a corner/tip would just sit there with the old
        # per-axis damping bouncing it back and forth against the same
        # spot forever, never gaining the sideways motion needed to clear
        # it (confirmed: a ball parked at a tip with tilt held stayed
        # completely motionless, vel_y pinned at exactly 0.0, indefinitely).
        #
        # Evaluate the geometry at the position the ball actually tried
        # to reach on each blocked axis (new_x/new_y), not its current,
        # already-valid resting position -- by construction that resting
        # position never overlaps a wall (that's exactly why it's safe to
        # be there), so querying it finds nothing and always falls
        # through to the pinch fallback below instead of ever reflecting.
        test_x = new_x if x_stuck else ball_x
        test_y = new_y if y_stuck else ball_y
        normal = _contact_normal(test_x, test_y)
        if normal is not None:
            nx, ny = normal
            dot = incoming_vel_x * nx + incoming_vel_y * ny
            if dot < 0:
                # This one formula covers both a flat wall bounce and a
                # rolling slope -- there's no separate ramp case. |nx*ny|
                # is 0 whenever the normal is purely horizontal/vertical
                # (a flat wall face) and peaks at 0.5 exactly at 45
                # degrees (nx=ny=1/sqrt(2), a corner or a diagonal/
                # staircase run of wall cells), so doubling it gives a
                # clean 0..1 "how much of a slope is this contact"
                # reading. At 0 the effective restitution is just
                # WALL_RESTITUTION -- an ordinary bounce, unchanged from
                # a flat wall. At 1 it's driven to exactly 0, which makes
                # this reflection formula cancel the into-the-wall
                # velocity component and keep the along-the-wall
                # component untouched -- a frictionless slide along the
                # surface, i.e. rolling, with no extra ramp-specific code
                # needed to get there.
                diagonal_factor = 2 * abs(nx * ny)
                # A genuinely fast impact always bounces at the usual
                # WALL_RESTITUTION -- that's the "bounciness" a wall is
                # supposed to have, whether or not tilt happens to be
                # pushing that way too. Below REST_IMPACT_SPEED, it
                # absorbs instead, but ONLY if tilt is ALSO actively
                # driving the ball into this same surface right now
                # (tilt only ever drives vel_x, so this just compares
                # tilt's sign against the normal's x-component -- tilt
                # and nx pushing opposite ways means tilt is pushing
                # further INTO the surface, since nx points away from
                # it, toward the ball): that combination -- repeatedly
                # re-driven back into a wall by a steady held tilt, at a
                # re-approach speed that's already slowed down close to
                # resting -- is specifically what turned into endless
                # tiny jitter that never actually settled. A slow hit
                # that ISN'T currently being re-driven that way (no
                # tilt, or tilt pointing elsewhere) has no repeated
                # re-driving force behind it, so it was never going to
                # jitter in the first place -- it still bounces (gently,
                # since it's already slow) and then friction alone
                # settles it, same as always.
                pushed_into_wall = tilt != 0 and tilt * nx < 0
                if abs(dot) < REST_IMPACT_SPEED and pushed_into_wall:
                    base_restitution = 0.0
                else:
                    base_restitution = WALL_RESTITUTION
                restitution = base_restitution * (1 - diagonal_factor)
                vel_x = incoming_vel_x - (1 + restitution) * dot * nx
                vel_y = incoming_vel_y - (1 + restitution) * dot * ny
            else:
                vel_x, vel_y = incoming_vel_x, incoming_vel_y
        else:
            # No single escape direction exists -- the ball is pinched
            # between two opposing surfaces (e.g. a gap narrower than its
            # own diameter) rather than at a simple corner. Reversing
            # would be a guess with no basis (which way is actually open
            # is exactly what's ambiguous); just bleed off speed instead
            # and let tilt sort out which way it goes.
            if x_stuck:
                vel_x *= abs(WALL_DAMPING)
            if y_stuck:
                vel_y *= abs(WALL_DAMPING)

        # The corrected velocity above is worthless if position never
        # actually advances to use it. When more than one nearby cell is
        # within reach at once (a corner, or several staircase steps
        # close together), escaping can require genuinely DIAGONAL
        # movement -- bisecting vel_x and vel_y independently can each
        # stay blocked by some other nearby cell even when the combined
        # diagonal step would clear all of them at once (resting square
        # on top of a cluster of cells is the clearest case: moving in x
        # alone never gains any distance from cells directly below, and
        # moving in y alone never gains any distance from cells off to
        # the side). So move both axes together as one 2D step, backing
        # off geometrically (not all-or-nothing) if the full corrected
        # velocity is still blocked, so the ball creeps free of a tight
        # multi-cell contact over a few frames instead of freezing
        # completely until some single frame's full step happens to
        # clear the whole cluster in one bound.
        step_x, step_y = vel_x, vel_y
        for _ in range(8):
            if not _swept_blocked(ball_x, ball_y, ball_x + step_x, ball_y + step_y):
                ball_x += step_x
                ball_y += step_y
                break
            step_x *= 0.5
            step_y *= 0.5

    return ball_x, ball_y, vel_x, vel_y
