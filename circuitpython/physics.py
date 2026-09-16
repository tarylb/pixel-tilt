"""
Shared collision/physics engine for the tilt-maze game.

Used by both circuitpython/code.py (the on-device game) and
tools/level_editor.py (the desktop editor/playtester) so a physics or
level-geometry change only has to be made once and both stay in sync --
historically the two were hand-copied and drifted out of sync repeatedly.

Pure logic only: no displayio, no pygame, no hardware. Callers own their
own rendering and input (potentiometer vs mouse/keyboard) and just read
the shared grid state (`solid`, `static_color`) to decide what to draw.

There's no separate "ramp" cell type -- a wall is a wall. A diagonal or
staircase-shaped run of wall cells naturally acts like a slope because
step_ball() derives how much to roll (vs. bounce) a contact from how
diagonal the local wall surface actually is at collision time, not from
anything stored per cell.

Import names directly (`from physics import solid, set_cell, ...`) --
the grid arrays are mutated in place (never reassigned), so a plain
import stays valid even after clear_level() runs.
"""

import math

WIDTH = 64
HEIGHT = 32
BALL_RADIUS = 2
BAR_HALF_THICKNESS = 1.0

MAX_SPEED = 1.5
# Each frame, vel_x closes this fraction of the gap to tilt*MAX_SPEED (its
# "target" speed for the current tilt) -- so a steady tilt settles at a
# speed proportional to how far it's tilted (like gravity along an incline)
# instead of most of the tilt range saturating to MAX_SPEED almost
# immediately. Higher = snappier/twitchier, lower = smoother/more gradual.
TILT_RESPONSE = 0.15
FRICTION = 0.97
WALL_DAMPING = -0.1
# The ball reflects off whatever local surface direction the nearby solid
# cells actually form (see _corner_normal()) rather than independently
# negating whichever axis got blocked, which is what let it get stuck
# reversing in place at a corner instead of deflecting off to the side.
# There's no separate "ramp" concept -- a diagonal/staircase run of plain
# wall cells IS a slope: WALL_RESTITUTION is scaled down by how diagonal
# that local surface normal is (see the diagonal_factor comment in
# step_ball), from a square-on flat-wall bounce at one extreme to a
# frictionless roll/slide at the other, with nothing extra to configure
# per cell. A steep, close-to-45-degree staircase reads as touching a
# corner almost every frame and rolls smoothly; a shallow one spends most
# of its time resting on a flat tread between corners and reads more like
# an actual bumpy staircase -- which is the honest result of this being
# unit-cell pixel geometry, not a hand-tuned slope value.
WALL_RESTITUTION = 0.9
BAR_RESTITUTION = 0.8  # bounciness of the flipper bounce (1.0 = perfectly elastic)
# Fraction of an into-the-bar impact that gets redirected along the bar's
# length instead of just bounced straight back, using the bar's current
# (rotating) angle, so hitting a tilted flipper sends the ball rolling/
# sliding off along it rather than only ever bouncing off its normal.
BAR_ROLL = 0.4

# ---------- Level grid state (one level's worth of geometry) ----------
solid = bytearray(WIDTH * HEIGHT)
static_color = bytearray(WIDTH * HEIGHT)
bar_segments = []  # current spinner bars as (x0, y0, x1, y1) tuples


def idx(x, y):
    return y * WIDTH + x


def set_cell(x, y, wall=False, goal=False):
    i = idx(x, y)
    if wall:
        solid[i] = 1
        static_color[i] = 2
    elif goal:
        solid[i] = 0
        static_color[i] = 3


def clear_cell(x, y):
    i = idx(x, y)
    solid[i] = 0
    static_color[i] = 0


def clear_level():
    for i in range(WIDTH * HEIGHT):
        solid[i] = 0
        static_color[i] = 0


def apply_walls(walls):
    """Paint a level's wall cells (levels_data's "walls" list, entries of
    (x0, x1, y)) into the grid. There's no separate ramp type -- a
    diagonal run of wall cells (see add_wall_line() below) is just more
    wall, and reduces to ordinary per-row spans same as any other wall
    shape. Caller is responsible for clear_level() first and for
    anything else the level format carries (goals, spinners, start
    position)."""
    for x0, x1, y in walls:
        for x in range(x0, x1 + 1):
            set_cell(x, y, wall=True)


def apply_goals(goals):
    """Paint a level's goal cells (levels_data's "goals" list, entries of
    (x0, x1, y)) into the grid."""
    for x0, x1, y in goals:
        for x in range(x0, x1 + 1):
            set_cell(x, y, goal=True)


def wall_line_cells(x0, y0, x1, y1, thickness=1):
    """The grid cells a straight line between two endpoints would cover,
    thickness pixels wide, WITHOUT painting them -- the actual line math,
    shared by add_wall_line() (which paints the result) and the level
    editor's live preview of what a second click would place."""
    if x0 > x1:
        x0, x1 = x1, x0
        y0, y1 = y1, y0
    dx = x1 - x0
    dy = y1 - y0
    cells = []
    if dx == 0:
        for dyi in range(thickness):
            y = y0 + dyi
            if 0 <= y < HEIGHT:
                cells.append((x0, y))
        return cells

    if abs(dy) <= abs(dx):
        # Shallow-ish: step along x, banding a vertical thickness-band per
        # column -- consecutive columns' bands always overlap here since
        # the y-step per column is at most 1.
        for x in range(x0, x1 + 1):
            t = (x - x0) / dx
            line_y = int(round(y0 + t * dy))
            for dyi in range(thickness):
                y = line_y + dyi
                if 0 <= y < HEIGHT:
                    cells.append((x, y))
    else:
        # Steep: stepping along x would skip rows faster than `thickness`
        # can bridge, leaving gaps -- step along y instead (like a proper
        # line-drawing algorithm choosing the larger-delta axis) so
        # consecutive rows' horizontal bands always overlap instead.
        ylo, yhi = (y0, y1) if y1 >= y0 else (y1, y0)
        for y in range(ylo, yhi + 1):
            t = (y - y0) / dy
            line_x = int(round(x0 + t * dx))
            for dxi in range(thickness):
                x = line_x + dxi
                if 0 <= x < WIDTH:
                    cells.append((x, y))
    return cells


def add_wall_line(x0, y0, x1, y1, thickness=1):
    """Rasterize a straight line of wall cells between two endpoints,
    thickness pixels wide -- the level editor's click-two-points tool for
    building a clean diagonal/staircase wall run without having to
    freehand-drag every pixel. Purely a convenience for painting the
    grid: the result is indistinguishable from any other wall cell (no
    stored slope/direction) -- step_ball() derives "this is a slope"
    from the shape of whatever solid cells happen to be there at
    collision time, not from how they got painted."""
    for x, y in wall_line_cells(x0, y0, x1, y1, thickness):
        set_cell(x, y, wall=True)


# The ball's actual collision/rendering reach, squared -- BALL_RADIUS+1
# rather than BALL_RADIUS itself so the rounded shape below isn't a
# perfect (smaller) diamond/square. _touching_cells() uses this exact
# same threshold: collision and rendering must agree on how far the ball
# reaches, or the ball's drawn edge can visibly sit on top of a wall
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
    ball's edge can visibly sit on top of a wall cell (or vice versa)
    before physics agrees it's touching anything.

    This is a real geometric circle-vs-square test, NOT the ball's
    discretized rendering shape (BALL_OFFSETS, used by ball_pixels_at for
    the on-screen sprite) checked at a truncated int(cx), int(cy). That
    used to be how collision detection worked here too, but BALL_OFFSETS
    deliberately excludes the (+-BALL_RADIUS, +-BALL_RADIUS) corners to
    look round -- which means it has real blind spots at exactly those
    corners: a wall corner sitting in that gap went completely
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
    """Returns (blocked, is_bar). is_bar is True when what's blocking is a
    spinner bar rather than the solid grid -- resolve_bar_bounce() already
    computed the correct reflection for that case, so step_ball() just
    needs to know not to also run wall-corner reflection on top of it."""
    best_d = None
    for seg in bar_segments:
        d = point_segment_distance(cx, cy, seg[0], seg[1], seg[2], seg[3])
        if best_d is None or d < best_d:
            best_d = d
    if best_d is not None and best_d < BALL_RADIUS + BAR_HALF_THICKNESS:
        return True, True
    for _x, _y, _ddx, _ddy, _dist in _touching_cells(cx, cy):
        return True, False
    return False, False


def _wall_blocked(cx, cy):
    """Like circle_blocked, but only the solid-grid (wall) check -- no
    bar-proximity check. Used to keep the bar-bounce position correction
    below from ever shoving the ball into a wall: the normal per-frame
    collision code only checks whether the *next* step is blocked, so
    once a push embeds the ball's center inside solid cells, nothing
    would ever move it back out again."""
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
                # continuing whichever way the ball was already drifting
                # tangentially, so hitting a tilted flipper sends it
                # sliding off along the slope instead of only ever
                # bouncing straight back off it.
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
    """Approximate the direction pointing away from nearby solid cells,
    for resolving contacts where axis-separated collision alone would
    give the wrong answer -- a flat wall face reduces to a single clean
    axis-aligned normal here (matching the old per-axis behavior exactly),
    but a corner or a diagonal/staircase run of wall cells doesn't have
    one obvious "which axis" to bounce off; independently negating
    whichever axis got blocked would bounce a corner hit straight back
    the way the ball came instead of deflecting it to the side (or, for
    a diagonal run, would never let it roll along the slope at all).
    This uses the same true circle-vs-cell test as circle_blocked
    (_touching_cells) and averages the away-from-ball directions of
    every touching cell into one usable normal -- how far that normal
    leans off-axis is also what step_ball() uses to decide how much
    "roll" (vs. plain bounce) a given contact gets; see the
    diagonal_factor comment there. Returns a (nx, ny) unit vector, or
    None if nothing solid is touching (shouldn't normally happen right
    after both axes were just found blocked using this same test, but
    the position may have shifted slightly) or if the touching cells'
    directions cancel out (the ball pinched between two opposing
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
    acceleration, and wall collision with a geometry-derived roll/bounce.
    Returns the updated (ball_x, ball_y, vel_x, vel_y) -- callers handle
    their own win/out-of-bounds checks and rendering using the result."""
    ball_x, ball_y, vel_x, vel_y = resolve_bar_bounce(ball_x, ball_y, vel_x, vel_y)

    vel_x += (tilt * MAX_SPEED - vel_x) * TILT_RESPONSE
    vel_y *= FRICTION
    vel_x = max(-MAX_SPEED, min(MAX_SPEED, vel_x))
    vel_y = max(-MAX_SPEED, min(MAX_SPEED, vel_y))

    x_stuck = False
    y_stuck = False
    incoming_vel_x, incoming_vel_y = vel_x, vel_y

    new_x = ball_x + vel_x
    blocked, is_bar = circle_blocked(new_x, ball_y)
    if blocked:
        if not is_bar:  # a bar's reflection was already applied above
            x_stuck = True
    else:
        ball_x = new_x

    new_y = ball_y + vel_y
    blocked, is_bar = circle_blocked(ball_x, new_y)
    if blocked:
        if not is_bar:
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
        normal = _corner_normal(test_x, test_y)
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
                restitution = WALL_RESTITUTION * (1 - diagonal_factor)
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
            if not circle_blocked(ball_x + step_x, ball_y + step_y)[0]:
                ball_x += step_x
                ball_y += step_y
                break
            step_x *= 0.5
            step_y *= 0.5

    return ball_x, ball_y, vel_x, vel_y
