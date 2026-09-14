"""
Shared collision/physics engine for the tilt-maze game.

Used by both circuitpython/code.py (the on-device game) and
tools/level_editor.py (the desktop editor/playtester) so a physics or
level-geometry change only has to be made once and both stay in sync --
historically the two were hand-copied and drifted out of sync repeatedly.

Pure logic only: no displayio, no pygame, no hardware. Callers own their
own rendering and input (potentiometer vs mouse/keyboard) and just read
the shared grid state (`solid`, `ramp_code`, `ramp_slope`, `static_color`)
to decide what to draw.

Import names directly (`from physics import solid, set_cell, ...`) --
the grid arrays are mutated in place (never reassigned), so a plain
import stays valid even after clear_level() runs.
"""

import math

WIDTH = 64
HEIGHT = 32
BALL_RADIUS = 2
HOLE_RADIUS = BALL_RADIUS + 1
BAR_HALF_THICKNESS = 1.0

MAX_SPEED = 1.5
# Each frame, vel_x closes this fraction of the gap to tilt*MAX_SPEED (its
# "target" speed for the current tilt) -- so a steady tilt settles at a
# speed proportional to how far it's tilted (like gravity along an incline)
# instead of most of the tilt range saturating to MAX_SPEED almost
# immediately. Higher = snappier/twitchier, lower = smoother/more gradual.
TILT_RESPONSE = 0.15
FRICTION = 0.97
# RAMP_REDIRECT is the fraction of horizontal speed converted to vertical
# speed (or vice versa) on a steep (~45 degree) ramp -- scaled down for
# shallower ramps (see ramp_steepness_for_width). RAMP_SLOWDOWN stays flat
# regardless of steepness -- it's what brakes vel_x fast enough, every
# single frame the ball stays pressed against a ramp, to keep the redirect
# (which fires every one of those frames, not just once per contact) from
# compounding into far more speed than intended.
RAMP_REDIRECT = 0.5
RAMP_SLOWDOWN = 0.4
WALL_DAMPING = -0.3
# Used instead of WALL_DAMPING specifically when the ball is blocked on
# BOTH axes in the same frame (a corner/tip, not a flat wall face) -- see
# _corner_normal(). Chosen to give the same bounce-back magnitude as
# WALL_DAMPING for a square-on hit, but as a proper reflection off the
# corner's actual direction instead of independently negating each axis,
# which is what let the ball get stuck reversing in place at a corner
# instead of deflecting off to the side.
WALL_RESTITUTION = 0.3
BAR_RESTITUTION = 0.8  # bounciness of the flipper bounce (1.0 = perfectly elastic)
# Fraction of an into-the-bar impact that gets redirected along the bar's
# length instead of just bounced straight back -- like a ramp, but using
# the bar's current (rotating) angle instead of a fixed slope, so hitting
# a tilted flipper sends the ball rolling/sliding off along it rather than
# only ever bouncing off its normal.
BAR_ROLL = 0.4

# ---------- Level grid state (one level's worth of geometry) ----------
solid = bytearray(WIDTH * HEIGHT)
ramp_code = bytearray(WIDTH * HEIGHT)  # 0 none, 1 "\", 2 "/"
ramp_slope = bytearray(WIDTH * HEIGHT)  # 0..255, how strongly a ramp cell redirects
static_color = bytearray(WIDTH * HEIGHT)
bar_segments = []  # current spinner bars as (x0, y0, x1, y1) tuples


def idx(x, y):
    return y * WIDTH + x


def set_cell(x, y, wall=False, ramp_dir=0, goal=False, steepness=1.0):
    i = idx(x, y)
    if wall or ramp_dir != 0:
        solid[i] = 1
        static_color[i] = 2
        if ramp_dir > 0:
            ramp_code[i] = 1
        elif ramp_dir < 0:
            ramp_code[i] = 2
        ramp_slope[i] = min(255, max(0, int(round(steepness * 255))))
    elif goal:
        solid[i] = 0
        ramp_code[i] = 0
        static_color[i] = 3


def clear_cell(x, y):
    i = idx(x, y)
    solid[i] = 0
    ramp_code[i] = 0
    ramp_slope[i] = 0
    static_color[i] = 0


def clear_level():
    for i in range(WIDTH * HEIGHT):
        solid[i] = 0
        ramp_code[i] = 0
        ramp_slope[i] = 0
        static_color[i] = 0


def ramp_steepness_for_width(width):
    """A ramp row's span width (columns covered by that one row of rise) is
    already an implicit slope: sin(angle) for a 1-row-rise/width-col-run
    triangle, normalized against a 1-column-wide span (the steepest a
    raster ramp row can be) so a ~45-degree ramp is full strength and
    shallower ones redirect less."""
    return min(1.0, math.sqrt(2) / math.sqrt(1 + width * width))


def apply_cells(cells):
    """Paint a level's wall/ramp/goal cells (levels_data's "cells" list,
    entries of (x0, x1, y, kind)) into the grid. Caller is responsible for
    clear_level() first and for anything else the level format carries
    (holes, spinners, start position)."""
    for x0, x1, y, kind in cells:
        steepness = ramp_steepness_for_width(x1 - x0 + 1)
        for x in range(x0, x1 + 1):
            if kind == 1:
                set_cell(x, y, wall=True)
            elif kind == 2:
                set_cell(x, y, ramp_dir=1, steepness=steepness)
            elif kind == 3:
                set_cell(x, y, ramp_dir=-1, steepness=steepness)
            elif kind == 4:
                set_cell(x, y, goal=True)


def circle_outline_pixels(cx, cy, r):
    """A ring of pixels roughly r away from (cx, cy). Uses a direct
    distance test rather than a midpoint/Bresenham circle -- at these
    small radii that reads as noticeably rounder (Bresenham circles tend
    to look octagonal/diamond-ish at radius ~3)."""
    pts = set()
    cx_i, cy_i = int(round(cx)), int(round(cy))
    r_inner = r - 0.5
    r_outer = r + 0.5
    span = r + 1
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            d = math.hypot(dx, dy)
            if r_inner <= d <= r_outer:
                px, py = cx_i + dx, cy_i + dy
                if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                    pts.add((px, py))
    return pts


# The ball's actual collision/rendering reach, squared -- BALL_RADIUS+1
# rather than BALL_RADIUS itself so the rounded shape below isn't a
# perfect (smaller) diamond/square. _touching_cells() uses this exact
# same threshold: collision and rendering must agree on how far the ball
# reaches, or the ball's drawn edge can visibly sit on top of a wall/ramp
# cell before physics agrees it's touching anything (or vice versa).
BALL_REACH_SQ = BALL_RADIUS * BALL_RADIUS + 1

BALL_OFFSETS = []
for _dy in range(-BALL_RADIUS, BALL_RADIUS + 1):
    for _dx in range(-BALL_RADIUS, BALL_RADIUS + 1):
        if _dx * _dx + _dy * _dy <= BALL_REACH_SQ:
            BALL_OFFSETS.append((_dx, _dy))
del _dx, _dy


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
      slipping. Wraps to stay within [0, 2*pi).

    Callers track both alongside ball_x/ball_y/vel_x/vel_y (it's display
    state, not something step_ball needs to know about)."""
    dx = int(cx) - int(prev_x)
    dy = int(cy) - int(prev_y)
    distance = math.sqrt(dx * dx + dy * dy)
    if distance > 0.0001:
        direction = (dx / distance, dy / distance)
    rotation = (rotation + distance / BALL_RADIUS) % (2 * math.pi)
    return direction, rotation


# A small plus-shaped mark riding the ball's surface, centered wherever
# ball_accent_pixels_at currently places it. With only one mark active
# at a time (not several at once), a 5-pixel plus is a reasonable
# fraction of the ball's ~21 total pixels rather than overwhelming it.
BALL_STAR_OFFSETS = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]


def ball_accent_pixels_at(cx, cy, direction, rotation):
    """The list of (x, y) pixels -- a single mark, possibly empty -- to
    draw in a contrasting "roll accent" color on top of the base ball
    sprite, to fake the look of an actual rolling sphere viewed from
    directly above:

    Picture a marked point starting at the TOP of the ball, rolling
    without slipping about a horizontal axis perpendicular to its
    direction of travel (like a real ball rolling on a table). This is
    the classic "top of a rolling wheel moves the same way the wheel is
    traveling" result: viewed from directly above, the point's position
    projects onto a line through the ball along `direction`, sweeping
    from the trailing edge to the leading edge (offset = BALL_RADIUS *
    sin(rotation), which is 0 at rotation=0 and increases toward
    +BALL_RADIUS, i.e. the leading edge, as rotation grows) while it's
    on the near/"top" half of the roll (cos(rotation) >= 0); for the
    other half (cos(rotation) < 0) it's rotated around to the underside
    and hidden entirely, reappearing at the trailing edge once rotation
    wraps back past 2*pi. That's what makes it read as sweeping across
    in the same direction the ball is actually moving, then under, then
    back on top -- and it falls out the same way for any direction, not
    just horizontal/vertical."""
    if math.cos(rotation) < 0:
        return []
    ux, uy = direction
    offset = BALL_RADIUS * math.sin(rotation)
    mark_x = int(round(cx + ux * offset))
    mark_y = int(round(cy + uy * offset))
    px, py = int(cx), int(cy)
    pts = []
    for ox, oy in BALL_STAR_OFFSETS:
        x, y = mark_x + ox, mark_y + oy
        if (x - px) * (x - px) + (y - py) * (y - py) > BALL_REACH_SQ:
            continue  # keep the mark within the ball's own silhouette
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


def bar_pixels_for_draw(seg):
    x0, y0, x1, y1 = seg
    pts = set()
    for x, y in bresenham_line(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))):
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


def _touching_cells(cx, cy):
    """Yield (x, y, ddx, ddy, dist) for every solid grid cell whose unit
    square actually overlaps a circle of BALL_REACH_SQ centered at the
    continuous point (cx, cy) -- ddx/ddy/dist describe the vector from the
    nearest point on that cell back to (cx, cy). Uses the exact same
    reach as BALL_OFFSETS (the ball's rendered shape), not just
    BALL_RADIUS itself -- collision must match what's drawn, or the
    ball's edge can visibly sit on top of a wall/ramp cell (or vice
    versa) before physics agrees it's touching anything.

    This is a real geometric circle-vs-square test, NOT the ball's
    discretized rendering shape (BALL_OFFSETS, used by ball_pixels_at for
    the on-screen sprite) checked at a truncated int(cx), int(cy). That
    used to be how collision detection worked here too, but BALL_OFFSETS
    deliberately excludes the (+-BALL_RADIUS, +-BALL_RADIUS) corners to
    look round -- which means it has real blind spots at exactly those
    corners: a wall or ramp corner sitting in that gap went completely
    undetected until the ball's sub-pixel position drifted enough to
    bring it into a checked cell, at which point it would suddenly catch.
    A true per-cell distance test has no such gap."""
    px, py = int(cx), int(cy)
    search = BALL_RADIUS + 1
    for y in range(py - search, py + search + 1):
        if y < 0 or y >= HEIGHT:
            continue
        for x in range(px - search, px + search + 1):
            if x < 0 or x >= WIDTH:
                continue
            if not solid[idx(x, y)]:
                continue
            nearest_x = max(x, min(cx, x + 1))
            nearest_y = max(y, min(cy, y + 1))
            ddx = cx - nearest_x
            ddy = cy - nearest_y
            dist_sq = ddx * ddx + ddy * ddy
            if dist_sq <= BALL_REACH_SQ:
                yield x, y, ddx, ddy, math.sqrt(dist_sq)


def circle_blocked(cx, cy):
    """Returns (blocked, direction, steepness). direction is "bar" for a
    spinner, 0 for a plain wall, or +-1 for a ramp; steepness (0..1) scales
    how strongly a ramp redirects velocity -- see ramp_steepness_for_width."""
    best_d = None
    for seg in bar_segments:
        d = point_segment_distance(cx, cy, seg[0], seg[1], seg[2], seg[3])
        if best_d is None or d < best_d:
            best_d = d
    if best_d is not None and best_d < BALL_RADIUS + BAR_HALF_THICKNESS:
        return True, "bar", 0.0
    for x, y, _ddx, _ddy, _dist in _touching_cells(cx, cy):
        i = idx(x, y)
        code = ramp_code[i]
        direction = 1 if code == 1 else (-1 if code == 2 else 0)
        steepness = ramp_slope[i] / 255.0 if code else 0.0
        return True, direction, steepness
    return False, 0, 0.0


def _wall_blocked(cx, cy):
    """Like circle_blocked, but only the solid-grid (wall/ramp) check --
    no bar-proximity check. Used to keep the bar-bounce position
    correction below from ever shoving the ball into a wall: the normal
    per-frame collision code only checks whether the *next* step is
    blocked, so once a push embeds the ball's center inside solid cells,
    nothing would ever move it back out again."""
    for _ in _touching_cells(cx, cy):
        return True
    return False


def resolve_bar_bounce(ball_x, ball_y, vel_x, vel_y):
    """Apply spinner-bar bounce physics against the current bar_segments.
    Returns the updated (ball_x, ball_y, vel_x, vel_y)."""
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
            seg_len = math.sqrt(length_sq) if length_sq > 0 else 1.0
            if d > 0.0001:
                nx = (ball_x - closest_x) / d
                ny = (ball_y - closest_y) / d
            else:
                nx = -dys / seg_len
                ny = dxs / seg_len
            # Tangent direction along the bar's current length, for the
            # roll effect below.
            tx = dxs / seg_len
            ty = dys / seg_len
            # Reflect velocity across the contact normal (only if actually
            # moving into the bar) -- this is what makes the bounce
            # direction depend on the bar's current angle, not just which
            # axis got blocked.
            dot_n = vel_x * nx + vel_y * ny
            if dot_n < 0:
                vel_x -= (1 + BAR_RESTITUTION) * dot_n * nx
                vel_y -= (1 + BAR_RESTITUTION) * dot_n * ny
                # Also roll some of that impact along the bar's length,
                # like a ramp -- continuing whichever way the ball was
                # already drifting tangentially, so hitting a tilted
                # flipper sends it sliding off along the slope instead of
                # only ever bouncing straight back off it.
                dot_t = vel_x * tx + vel_y * ty
                roll_sign = 1.0 if dot_t >= 0 else -1.0
                vel_x += BAR_ROLL * -dot_n * roll_sign * tx
                vel_y += BAR_ROLL * -dot_n * roll_sign * ty
            overlap = contact_dist - d
            if overlap > 0:
                pushed_x = ball_x + nx * overlap
                pushed_y = ball_y + ny * overlap
                if not _wall_blocked(pushed_x, pushed_y):
                    ball_x = pushed_x
                    ball_y = pushed_y
                # else: leave the position alone -- pushing here would bury
                # the ball in a wall. The velocity reflection above still
                # applies, and the normal wall-collision code in step_ball
                # takes over from here to keep the ball out of the wall.
    return ball_x, ball_y, vel_x, vel_y


def _corner_normal(cx, cy):
    """Approximate the direction pointing away from nearby solid cells
    (walls AND ramps), for resolving corner contacts -- e.g. the tip of a
    wall span -- where the ball is blocked on both axes at once.
    Axis-separated collision has no idea a corner is round: it just
    negates whichever axis got blocked, which for a corner hit on both
    axes means bouncing straight back the way the ball came instead of
    deflecting to the side. This uses the same true circle-vs-cell test
    as circle_blocked (_touching_cells) and averages the away-from-ball
    directions of every touching cell into one usable normal. Ramp cells
    count too: step_ball only calls this once BOTH axes ended up in its
    generic "blocked" branch, which happens for a ramp cell too if the
    other axis already used up this frame's one ramp redirect
    (ramp_hit_this_frame) -- excluding ramp cells here would just
    reintroduce a fallback gap for that case. Returns a (nx, ny) unit
    vector, or None if nothing solid is touching (shouldn't normally
    happen right after both axes were just found blocked using this same
    test, but the position may have shifted slightly) or if the touching
    cells' directions cancel out (the ball pinched between two opposing
    surfaces, with no single well-defined escape direction)."""
    sum_x = 0.0
    sum_y = 0.0
    for _x, _y, ddx, ddy, dist in _touching_cells(cx, cy):
        if dist > 0.0001:
            sum_x += ddx / dist
            sum_y += ddy / dist
    length = math.sqrt(sum_x * sum_x + sum_y * sum_y)
    if length < 0.0001:
        return None
    return sum_x / length, sum_y / length


def step_ball(ball_x, ball_y, vel_x, vel_y, tilt):
    """Advance the ball one physics tick: spinner-bar bounce, tilt-driven
    acceleration, and wall/ramp collision with redirect. Returns the
    updated (ball_x, ball_y, vel_x, vel_y) -- callers handle their own
    win/hole/out-of-bounds checks and rendering using the result."""
    ball_x, ball_y, vel_x, vel_y = resolve_bar_bounce(ball_x, ball_y, vel_x, vel_y)

    vel_x += (tilt * MAX_SPEED - vel_x) * TILT_RESPONSE
    vel_y *= FRICTION
    vel_x = max(-MAX_SPEED, min(MAX_SPEED, vel_x))
    vel_y = max(-MAX_SPEED, min(MAX_SPEED, vel_y))

    ramp_hit_this_frame = False
    # Set when an axis is blocked with no ramp redirect applied -- either
    # a plain wall, or a ramp that lost out to the other axis's
    # one-redirect-per-frame limit.
    x_stuck = False
    y_stuck = False
    incoming_vel_x, incoming_vel_y = vel_x, vel_y

    new_x = ball_x + vel_x
    blocked, ramp_dir, steepness = circle_blocked(new_x, ball_y)
    if blocked:
        if ramp_dir == "bar":
            pass  # already reflected above -- just don't move into it
        elif ramp_dir != 0 and not ramp_hit_this_frame:
            vel_y += RAMP_REDIRECT * steepness * ramp_dir * vel_x
            vel_y = max(-MAX_SPEED, min(MAX_SPEED, vel_y))
            vel_x *= RAMP_SLOWDOWN
            ramp_hit_this_frame = True
        else:
            x_stuck = True
    else:
        ball_x = new_x

    new_y = ball_y + vel_y
    blocked, ramp_dir, steepness = circle_blocked(ball_x, new_y)
    if blocked:
        if ramp_dir == "bar":
            pass  # already reflected above -- just don't move into it
        elif ramp_dir != 0 and not ramp_hit_this_frame:
            vel_x += RAMP_REDIRECT * steepness * ramp_dir * vel_y
            vel_x = max(-MAX_SPEED, min(MAX_SPEED, vel_x))
            vel_y *= RAMP_SLOWDOWN
            ramp_hit_this_frame = True
        else:
            y_stuck = True
    else:
        ball_y = new_y

    if x_stuck or y_stuck:
        # Reflect off the ACTUAL nearby wall geometry rather than just
        # negating whichever axis got blocked. Requiring BOTH axes to be
        # blocked before doing this (the previous approach) misses the
        # single most common case: a ball moving mostly along one axis
        # with the other axis's velocity near zero can never register as
        # "blocked" on that axis at all -- moving by ~0 from an already-
        # valid position trivially succeeds -- so a ball sliding along a
        # flat run into a corner/tip would just sit there with the old
        # per-axis damping bouncing it back and forth against the same
        # spot forever, never gaining the sideways motion needed to clear
        # it (confirmed: a ball parked at a tip with tilt held stayed
        # completely motionless, vel_y pinned at exactly 0.0, indefinitely).
        # For a genuinely flat wall this reduces to exactly the old
        # per-axis behavior anyway, since the normal comes out purely
        # axis-aligned there -- this only changes anything right at a
        # corner or a diagonal graze, which is exactly where it needs to.
        #
        # Evaluate the geometry at the position the ball actually tried
        # to reach on each blocked axis (new_x/new_y), not its current,
        # already-valid resting position -- by construction that resting
        # position never overlaps a wall (that's exactly why it's safe to
        # be there), so querying it finds nothing and always falls
        # through to the pinch fallback below instead of ever reflecting.
        test_x = new_x if x_stuck else ball_x
        test_y = new_y if y_stuck else ball_y
        normal = _corner_normal(test_x, test_y)
        if normal is not None:
            nx, ny = normal
            dot = incoming_vel_x * nx + incoming_vel_y * ny
            if dot < 0:
                vel_x = incoming_vel_x - (1 + WALL_RESTITUTION) * dot * nx
                vel_y = incoming_vel_y - (1 + WALL_RESTITUTION) * dot * ny
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

    return ball_x, ball_y, vel_x, vel_y
