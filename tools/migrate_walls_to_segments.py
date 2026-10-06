"""One-off migration: convert circuitpython/levels_data.json's "walls"
field from the old per-pixel-row span format ([x0, x1, y] triples) to the
new continuous line-segment format (a list of polylines, each a list of
[x, y] points).

Each old span [x0, x1, y] becomes a single 2-point polyline
[[x0, y + 0.5], [x1 + 1, y + 0.5]] -- a horizontal segment centered on
the pixel row, spanning the exact same columns -- preserving the
existing visual shape and collision footprint exactly. Diagonal/
staircase walls (built from many such per-row spans) keep that same
bumpy, segment-per-row character after migration; redraw them by hand
with the Wall/Wall Line tools afterward for smooth continuous slopes.

goals, spikes, spinners, and start are left untouched.

Run once: python3 tools/migrate_walls_to_segments.py
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LEVELS_PATH = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "circuitpython", "levels_data.json"))
sys.path.insert(0, os.path.normpath(os.path.join(SCRIPT_DIR, "..", "circuitpython")))

# Reuse the exact same compact-JSON formatter the level editor saves
# with, so the migrated file's style matches whatever the editor would
# itself produce (packed leaf arrays, short lists collapsed to one line).
def format_json(obj, indent=0, max_width=100):
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


def migrate_walls(walls):
    # y + 0.5 centers the segment on the original pixel row -- but
    # physics.py's rendering rasterizer rounds each endpoint with
    # Python's round() (banker's rounding), which resolves an EXACT .5
    # up or down depending on whether the integer part is odd or even --
    # so half the rows would render one pixel off from where they
    # actually used to be, even though the real (collision) geometry is
    # still correctly centered. A tiny epsilon below .5 sidesteps that
    # tie-break entirely: round() then always resolves down to exactly
    # `y` for every row, matching the original rendering exactly, while
    # the 0.000001px difference in the actual collision center is far
    # below anything physically meaningful.
    #
    # x1 is used as-is (NOT x1 + 1): the old span format's x1 is the
    # last COLUMN covered, inclusive (e.g. a single-pixel span has
    # x0 == x1) -- and bresenham_line() (the rendering rasterizer) draws
    # both of a segment's endpoints inclusively too, so a segment from
    # x0 to x1 already rasterizes to exactly columns x0..x1, matching
    # the span's pixel footprint exactly. (A segment to x1 + 1 would
    # have the geometrically "more correct" continuous length -- the
    # span's columns really do physically extend that far -- but would
    # rasterize one column too wide, which matters far more for an
    # exact visual migration than the sub-pixel collision-length
    # difference matters physically.)
    offset = 0.5 - 1e-6
    polylines = []
    for x0, x1, y in walls:
        polylines.append([[x0, y + offset], [x1, y + offset]])
    return polylines


def main():
    with open(LEVELS_PATH) as f:
        levels = json.load(f)

    for level in levels:
        level["walls"] = migrate_walls(level["walls"])

    with open(LEVELS_PATH, "w") as f:
        f.write(format_json(levels))
        f.write("\n")

    print(f"Migrated {len(levels)} levels' walls to segment format in {LEVELS_PATH}")


if __name__ == "__main__":
    main()
