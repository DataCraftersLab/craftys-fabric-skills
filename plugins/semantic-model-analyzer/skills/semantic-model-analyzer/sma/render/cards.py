"""
The cards. One function per pattern, all of them reading the bound dataset.

This is the showcase generator, turned into a library. The change that mattered is at
the top: it used to parse two hardcoded fixture files at *import* time and leave the
results in module globals, which works exactly once, for one model. `bind()` now fills
the same globals from a dataset, so the same card functions serve any input.

The globals stayed globals on purpose. Threading a context object through seventeen card
functions and forty chart calls would have been a rewrite of code that is already
correct and already validated; rebinding one namespace is a change of a dozen lines. The
cost is that this module holds one model at a time, which is exactly what a run does.
"""

import base64
import collections
import html
import json
import math
import re

from .. import findings as findings_mod
from . import asset, asset_bytes
from ..dax_format import FORMAT_TONE, read_format
from ..tmdl_findings import HELP as FINDING_HELP

# --------------------------------------------------------------- the bound dataset
#
# Set by bind(). Nothing in this module reads a file or calls a parser.

REPORT = None
MEAS = COLS = TABS = TROWS = QRY = None
ANAT = BY_NAME = Q_BY_NAME = None
UNREFERENCED = MERGED_AWAY = None
TD = MD = None
FINDINGS = None
#: The objects read since dataset version 2: relationships and their stats, the model
#: block, calculation groups, hierarchies, roles, perspectives, shared expressions,
#: refresh policies, the coverage block and the suppressed findings.
REL = RSTATS = MODEL = CGS = HIER = ROLES = PERSP = EXPRS = RPOL = COVERAGE = None
SUPPRESSED = None
HAS_TMDL = HAS_M = False


def bind(report):
    """Point the cards at one dataset. Idempotent, and safe to call per render."""
    global REPORT, MEAS, COLS, TABS, TROWS, QRY, ANAT, BY_NAME, Q_BY_NAME
    global UNREFERENCED, MERGED_AWAY, TD, MD, FINDINGS, HAS_TMDL, HAS_M
    global REL, RSTATS, MODEL, CGS, HIER, ROLES, PERSP, EXPRS, RPOL, COVERAGE, SUPPRESSED
    REPORT = report
    HAS_TMDL = "tmdl" in report.get("parts", [])
    HAS_M = "m" in report.get("parts", [])

    if HAS_TMDL:
        t = report["tmdl"]
        MEAS = t["measures"]
        COLS = t["columns"]
        # `tableRows` carries the partitions too, so one list serves both readings that
        # the old script kept as separate `tables` and `tableRows` globals.
        TABS = TROWS = t["tables"]
        ANAT = t["anatomy"]
        UNREFERENCED = t["unreferenced"]
        MERGED_AWAY = [(m["name"], m["table"], m["folder"]) for m in t["merged"]]
        BY_NAME = {m["name"]: m for m in MEAS}
        TD = {"linkCount": t["totals"]["links"], "folders": t["folders"],
              "diagnostics": t["diagnostics"], "totals": t["totals"]}
        FINDINGS = t["findings"]
        SUPPRESSED = t.get("suppressed", [])
        REL = t.get("relationships", [])
        RSTATS = t.get("relationshipStats", {})
        MODEL = t.get("model", {})
        CGS = t.get("calculationGroups", [])
        HIER = t.get("hierarchies", [])
        ROLES = t.get("roles", [])
        PERSP = t.get("perspectives", [])
        EXPRS = t.get("expressions", [])
        RPOL = t.get("refreshPolicies", [])
        COVERAGE = t.get("coverage", {})
    else:
        MEAS = COLS = TABS = TROWS = []
        ANAT, BY_NAME = {}, {}
        UNREFERENCED, MERGED_AWAY, FINDINGS, SUPPRESSED = [], [], [], []
        TD = {"linkCount": 0, "folders": [], "diagnostics": [], "totals": {}}
        REL, CGS, HIER, ROLES, PERSP, EXPRS, RPOL = [], [], [], [], [], [], []
        RSTATS, MODEL, COVERAGE = {}, {}, {}

    if HAS_M:
        m = report["m"]
        QRY = m["queries"]
        Q_BY_NAME = {q["name"]: q for q in QRY}
        MD = {"stepCount": m["totals"]["steps"], "linkCount": m["totals"]["links"],
              "layers": m["layers"], "format": m["format"],
              "sourceKinds": m["sourceKinds"], "diagnostics": m["diagnostics"],
              "findings": m["findings"], "suppressed": m.get("suppressed", []),
              "sql": m.get("sql", []),
              "modelFindings": [f for f in m["findings"] if not f.get("subject")]}
    else:
        QRY, Q_BY_NAME = [], {}
        MD = {"stepCount": 0, "linkCount": 0, "layers": [], "format": None,
              "sourceKinds": [], "diagnostics": [], "findings": [], "suppressed": [],
              "sql": [], "modelFindings": []}


def an(m):
    """Anatomy for a measure record. Keyed by id, not by name."""
    return ANAT.get(m["id"], {})


# ====================================================== svg primitives ======
#
# Every fill is a token, never a hex: a hardcoded colour survives the theme
# toggle and goes invisible in one of the two themes. The accent ramp is the
# theme's own --a1..--a8, cycled by position, with --a0 as the neutral.

ACCENTS = 8


def acc(i, part_="dot"):
    return "var(--a%d-%s)" % ((i % ACCENTS) + 1, part_)


NEUTRAL = "var(--a0-%s)"
POS, NEG, WARN, INFO = (
    "var(--color-positive)",
    "var(--color-negative)",
    "var(--color-warning)",
    "var(--color-info)",
)
MUTED = "var(--color-muted-foreground)"
INK = "var(--color-foreground)"
LINE = "var(--color-border)"

# SVG cannot inherit the document's type scale, so it is restated here rather than
# invented: 10 / 12 / 20 are --text-100 / --text-200 / --text-500. Sizes were 9, 10, 11
# and 22 before, none of them on the scale, which is why chart text never quite matched
# the prose beside it. One size per ROLE, not per chart.
FS_LABEL = 12     # every category, row, node and column label
FS_VALUE = 12     # every number that belongs to a bar or a row
FS_AXIS = 10      # axis titles, tick values, in-chart annotations
FS_MICRO = 10     # dense grid cells, where 12 will not fit
FS_BIG = 20       # a single figure at the centre of a donut
LH = 1.2          # line height for a wrapped label, as a multiple of size


_NARROW = set("iljtfrI.,:;'|!()[] -")


def text_w(sc, size):
    """Rough advance width, by character class. Segoe UI averages a little over half
    its size in mixed case, but a CamelCase table name is mostly capitals, and capitals
    run nearer two thirds. One factor for everything under-measured those by a quarter,
    which is exactly how far they ran outside their charts.

    Rough is enough: it decides how many words fit on a line, and the standing checks
    catch a label that overflows its viewBox regardless of what this predicted.
    """
    total = 0.0
    for ch in str(sc):
        if ch in _NARROW:
            total += 0.32
        elif ch.isupper() or ch.isdigit() or ch in "_@#%&":
            total += 0.68
        else:
            total += 0.55
    return total * size


def wrap_lines(sc, maxw, size, max_lines=2):
    """Break on words. The last line takes an ellipsis if the text still will not fit."""
    # A single token wider than the line -- a URL, a long mask -- cannot be broken on a
    # space, so it is broken on a character. Without this it silently ran outside its own
    # node instead of wrapping.
    words = []
    for word in str(sc).split():
        while text_w(word, size) > maxw and len(word) > 1:
            take = max(1, int(maxw / (size * 0.55)) - 1)
            words.append(word[:take])
            word = word[take:]
        words.append(word)
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if cur and text_w(trial, size) > maxw:
            lines.append(cur)
            cur = word
            if len(lines) == max_lines:
                break
        else:
            cur = trial
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if not lines:
        return [""]
    placed = sum(len(l) for l in lines)
    if placed < len("".join(words)):
        last = lines[-1]
        while last and text_w(last + "…", size) > maxw:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def txt_wrap(x, y, sc, maxw, size=FS_LABEL, fill=INK, anchor="start", weight=400,
             max_lines=2, valign="top"):
    """A label on up to `max_lines` lines instead of rotated 45 degrees.

    Rotation costs a reader more than it saves: it is unreadable at a glance, it fights
    the horizontal rhythm of everything else on the page, and it never actually fits the
    long labels it was introduced for.
    """
    lines = wrap_lines(sc, maxw, size, max_lines)
    step = size * LH
    y0 = y if valign == "top" else y - step * (len(lines) - 1)
    spans = "".join(
        '<tspan x="%.1f" y="%.1f">%s</tspan>' % (x, y0 + i * step, E(l))
        for i, l in enumerate(lines))
    return (
        '<text font-size="%s" fill="%s" text-anchor="%s" font-weight="%s" '
        'font-family="var(--font-base)"><title>%s</title>%s</text>'
        % (size, fill, anchor, weight, E(str(sc)), spans)
    )


def wrap_h(sc, maxw, size=FS_LABEL, max_lines=2):
    """Pixel height a wrapped label will occupy."""
    return len(wrap_lines(sc, maxw, size, max_lines)) * size * LH

E = html.escape


def svg(body, w, h, cls="dcv"):
    return (
        '<svg class="%s" viewBox="0 0 %d %d" width="100%%" '
        'preserveAspectRatio="xMinYMin meet" role="img">%s</svg>' % (cls, w, h, body)
    )


def txt(x, y, s, size=12, fill=INK, anchor="start", weight=400, mono=False, op=1):
    """Default size is FS_LABEL (12, --text-200); it is spelled out because the scale
    is declared after the primitives that use it."""
    f = "var(--font-code)" if mono else "var(--font-base)"
    return (
        '<text x="%.1f" y="%.1f" font-size="%s" fill="%s" text-anchor="%s" '
        'font-weight="%s" font-family="%s" opacity="%s">%s</text>'
        % (x, y, size, fill, anchor, weight, f, op, E(str(s)))
    )


def num(x, y, s, size=12, fill=MUTED, anchor="end"):  # FS_VALUE
    return (
        '<text x="%.1f" y="%.1f" font-size="%s" fill="%s" text-anchor="%s" '
        'font-family="var(--font-numeric)" font-variant-numeric="tabular-nums">%s</text>'
        % (x, y, size, fill, anchor, E(str(s)))
    )


def rect(x, y, w, h, fill, r=2, op=1, stroke=None, sw=1):
    s = '' if stroke is None else ' stroke="%s" stroke-width="%s"' % (stroke, sw)
    return '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%s" fill="%s" opacity="%s"%s/>' % (
        x, y, max(w, 0), max(h, 0), r, fill, op, s
    )


def line(x1, y1, x2, y2, stroke=LINE, sw=1, dash=None, op=1):
    d = '' if dash is None else ' stroke-dasharray="%s"' % dash
    return '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%s" opacity="%s"%s/>' % (
        x1, y1, x2, y2, stroke, sw, op, d
    )


def path(d, stroke="none", fill="none", sw=1, op=1, cap="round", dash=None):
    extra = '' if dash is None else ' stroke-dasharray="%s"' % dash
    return '<path d="%s" stroke="%s" fill="%s" stroke-width="%s" opacity="%s" stroke-linecap="%s"%s/>' % (
        d, stroke, fill, sw, op, cap, extra
    )


def title(s):
    return "<title>%s</title>" % E(str(s))


def clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def fit(s, px, size=FS_LABEL):
    """clip() by pixels. A label column is so many pixels wide, not so many characters,
    and a 32-character table name at 12px is wider than a 190px column. The browser
    check that measures text against its own chart caught six of these on one model."""
    s = str(s)
    if text_w(s, size) <= px:
        return s
    while len(s) > 1 and text_w(s + "…", size) > px:
        s = s[:-1]
    return s + "…"


def fmt(v):
    if isinstance(v, float):
        return ("%.1f" % v).rstrip("0").rstrip(".")
    return "{:,}".format(v)


def empty_note():
    return '<p class="dc-caption">Nothing to draw — the parse found none of these.</p>'


# ---------------------------------------------------------- ranked bars ----

def ranked_bar(rows, labw=190, barw=430, rowh=20, unit="", colour=None, w=None):
    """rows = [(label, value)] already sorted. colour(i, label) -> token."""
    if not rows:
        return empty_note()
    top = max(v for _, v in rows) or 1
    W = w or (labw + barw + 74)
    H = len(rows) * rowh + 10
    o = []
    for i, (lab, v) in enumerate(rows):
        y = i * rowh + 6
        c = colour(i, lab) if colour else acc(i)
        o.append(txt(labw - 8, y + rowh - 7, fit(lab, labw - 12), FS_LABEL, INK, "end"))
        o.append(rect(labw, y + 3, barw, rowh - 9, NEUTRAL % "tint", 2, 0.55))
        o.append(rect(labw, y + 3, barw * v / top, rowh - 9, c, 2))
        o.append(num(W - 6, y + rowh - 7, ("%s%s" % (fmt(v), unit))))
    return svg("".join(o), W, H)


# ----------------------------------------------------------- histogram ----

def histogram(pairs, w=740, h=150, colour=None, xlab="", ylab=""):
    """pairs = [(bucket_label, count)] in bucket order."""
    if not pairs:
        return empty_note()
    top = max(c for _, c in pairs) or 1
    pad_l, pad_b, pad_t = 34, 30, 14
    plot_w, plot_h = w - pad_l - 12, h - pad_b - pad_t
    step = plot_w / len(pairs)
    o = [line(pad_l, pad_t + plot_h, w - 12, pad_t + plot_h, LINE, 1)]
    for g in (0, 0.5, 1):
        y = pad_t + plot_h - plot_h * g
        o.append(line(pad_l, y, w - 12, y, LINE, 1, "2 4", 0.7 if g else 0))
        o.append(num(pad_l - 6, y + 3, int(round(top * g)), FS_AXIS))
    for i, (lab, c) in enumerate(pairs):
        bh = plot_h * c / top
        x = pad_l + i * step + step * 0.16
        bw = step * 0.68
        col = colour(i, lab) if colour else acc(i)
        o.append(rect(x, pad_t + plot_h - bh, bw, bh, col, 2) + title("%s: %s" % (lab, c)))
        if c:
            o.append(num(x + bw / 2, pad_t + plot_h - bh - 4, c, FS_AXIS, MUTED, "middle"))
        o.append(txt(x + bw / 2, pad_t + plot_h + 13, lab, FS_AXIS, MUTED, "middle"))
    if xlab:
        o.append(txt(w - 12, h - 3, xlab, FS_AXIS, MUTED, "end"))
    if ylab:
        o.append(txt(pad_l - 28, pad_t - 4, ylab, FS_AXIS, MUTED, "start"))
    return svg("".join(o), w, h)


# --------------------------------------------------------- stacked rows ----

def stacked_rows(rows, keys, colours, labw=180, barw=420, rowh=20, w=None):
    """rows = [(label, {key: value})]. One row per label, segments in `keys` order."""
    if not rows:
        return empty_note()
    top = max(sum(d.get(k, 0) for k in keys) for _, d in rows) or 1
    W = w or (labw + barw + 62)
    H = len(rows) * rowh + 10
    o = []
    for i, (lab, d) in enumerate(rows):
        y = i * rowh + 6
        x = labw
        o.append(txt(labw - 8, y + rowh - 7, fit(lab, labw - 12), FS_LABEL, INK, "end"))
        for k in keys:
            v = d.get(k, 0)
            if not v:
                continue
            seg = barw * v / top
            o.append(rect(x, y + 3, seg, rowh - 9, colours[k], 2) + title("%s %s: %s" % (lab, k, v)))
            x += seg
        o.append(num(W - 6, y + rowh - 7, sum(d.get(k, 0) for k in keys)))
    return svg("".join(o), W, H)


def legend(items):
    """items = [(label, token)]"""
    sp = ['<span class="dcv-key"><i style="background:%s"></i>%s</span>' % (c, E(l)) for l, c in items]
    return '<p class="dcv-legend">%s</p>' % "".join(sp)


# ------------------------------------------------------------- scatter ----

def scatter(points, w=740, h=330, xlab="", ylab="", xline=None, yline=None):
    """points = [(x, y, label, colour, r)]"""
    if not points:
        return empty_note()
    pad_l, pad_b, pad_t, pad_r = 42, 34, 16, 16
    pw, ph = w - pad_l - pad_r, h - pad_b - pad_t
    xmax = max(p[0] for p in points) or 1
    ymax = max(p[1] for p in points) or 1
    X = lambda v: pad_l + pw * v / xmax
    Y = lambda v: pad_t + ph - ph * v / ymax
    o = [
        line(pad_l, pad_t, pad_l, pad_t + ph, LINE),
        line(pad_l, pad_t + ph, pad_l + pw, pad_t + ph, LINE),
    ]
    for g in (0.25, 0.5, 0.75, 1):
        o.append(line(pad_l, Y(ymax * g), pad_l + pw, Y(ymax * g), LINE, 1, "2 4", 0.6))
        o.append(num(pad_l - 6, Y(ymax * g) + 3, int(round(ymax * g)), FS_AXIS))
        o.append(num(X(xmax * g), pad_t + ph + 14, int(round(xmax * g)), 10, MUTED, "middle"))
    if xline:
        o.append(line(X(xline), pad_t, X(xline), pad_t + ph, WARN, 1, "4 3", 0.7))
    if yline:
        o.append(line(pad_l, Y(yline), pad_l + pw, Y(yline), WARN, 1, "4 3", 0.7))
    for x, y, lab, c, r in points:
        o.append(
            '<circle cx="%.1f" cy="%.1f" r="%s" fill="%s" opacity="0.78" stroke="var(--color-card)" stroke-width="1">%s</circle>'
            % (X(x), Y(y), r, c, title("%s — %s, %s" % (lab, x, y)))
        )
    o.append(txt(pad_l + pw, h - 4, xlab, FS_AXIS, MUTED, "end"))
    o.append(txt(pad_l - 34, pad_t - 3, ylab, FS_AXIS, MUTED, "start"))
    return svg("".join(o), w, h)


# --------------------------------------------------------------- donut ----

def donut(slices, size=190, hole=0.60, centre=None):
    """slices = [(label, value, colour)]"""
    total = sum(v for _, v, _ in slices) or 1
    cx = cy = size / 2
    R, r = size / 2 - 6, (size / 2 - 6) * hole
    o, a0 = [], -math.pi / 2
    for lab, v, c in slices:
        a1 = a0 + 2 * math.pi * v / total
        big = 1 if a1 - a0 > math.pi else 0
        d = (
            "M %.2f %.2f A %.2f %.2f 0 %d 1 %.2f %.2f L %.2f %.2f A %.2f %.2f 0 %d 0 %.2f %.2f Z"
            % (
                cx + R * math.cos(a0), cy + R * math.sin(a0), R, R, big,
                cx + R * math.cos(a1), cy + R * math.sin(a1),
                cx + r * math.cos(a1), cy + r * math.sin(a1), r, r, big,
                cx + r * math.cos(a0), cy + r * math.sin(a0),
            )
        )
        o.append('<path d="%s" fill="%s">%s</path>' % (d, c, title("%s: %s" % (lab, v))))
        a0 = a1
    if centre:
        o.append(txt(cx, cy + 2, centre[0], FS_BIG, INK, "middle", 600))
        o.append(txt(cx, cy + 18, centre[1], FS_AXIS, MUTED, "middle"))
    return svg("".join(o), size, size, "dcv dcv-donut")


# ------------------------------------------------------------- heatmap ----

def heatmap(rows, cols, cell, w=None, labw=170, cw=None, ch=22, colour="var(--a3-dot)",
            fmt_cell=None):
    """cell(r, c) -> (value, intensity 0..1)

    Column labels sit horizontal on up to three lines. They used to be rotated -55 deg,
    which needed a trigonometric fudge to size the viewBox, put the longest label
    outside the grid entirely, and still could not be read at a glance. So the cell
    width is derived from the labels instead: wide enough that the longest one wraps
    into the space above its own column.
    """
    if not rows or not cols:
        return empty_note()
    # A square self-matrix -- folder against folder, query against query -- repeats the
    # same list on both axes, so spelling it out twice costs eighteen columns of width and
    # truncates every one. The columns take an index instead and the row labels carry it,
    # which is the key.
    square = list(rows) == list(cols)
    if square:
        cw = cw or 30
        headh = FS_AXIS * LH + 16
    else:
        # Three lines of the longest label, with a floor so a grid of short codes stays
        # dense and a ceiling so one long code does not stretch every column.
        need = max(text_w(str(c), FS_AXIS) for c in cols) / 3 + 14
        cw = cw or max(34, min(88, need))
        headh = max(wrap_h(str(c), cw - 6, FS_AXIS, 3) for c in cols) + 14
    W = w or (labw + len(cols) * cw + 14)
    H = headh + len(rows) * ch + 8
    o = []
    for j, c in enumerate(cols):
        x = labw + j * cw + cw / 2
        if square:
            o.append(txt(x, headh - 8, j + 1, FS_AXIS, MUTED, "middle"))
        else:
            o.append(txt_wrap(x, 12, c, cw - 6, FS_AXIS, MUTED, "middle", max_lines=3))
    for i, r in enumerate(rows):
        y = headh + i * ch
        lab = ("%d  %s" % (i + 1, r)) if square else r
        o.append(txt(labw - 8, y + ch - 7, fit(lab, labw - 12), FS_LABEL, INK, "end"))
        for j, c in enumerate(cols):
            v, t = cell(r, c)
            x = labw + j * cw
            o.append(rect(x + 1, y + 1, cw - 2, ch - 2, LINE, 2, 0.35))
            if t > 0:
                o.append(rect(x + 1, y + 1, cw - 2, ch - 2, colour, 2, 0.14 + 0.86 * t)
                         + title("%s / %s: %s" % (r, c, v)))
                if fmt_cell:
                    o.append(txt(x + cw / 2, y + ch - 7, fmt_cell(v), FS_MICRO,
                                 "var(--color-card)" if t > 0.55 else INK, "middle"))
    return svg("".join(o), W, H)


# ------------------------------------------------------ layered DAG ----

def layered_dag(layers, edges, w=760, nodew=132, nodeh=22, gapy=54, labels=True, colour=None,
                pad=14):
    """layers = [[node]] top to bottom. edges = [(a, b)] a -> b. Positions by index."""
    pos, width_of = {}, {}
    H = pad * 2 + (len(layers) - 1) * gapy + nodeh
    for li, row in enumerate(layers):
        if not row:
            continue
        step = (w - pad * 2) / len(row)
        # Eleven nodes across 760px cannot each be 132px wide; the first version let them
        # overlap and hang past both edges of the chart. The row's nodes shrink to the
        # step instead, and their labels are fitted to what is left.
        nw = max(24, min(nodew, step - 4))
        for i, n in enumerate(row):
            pos[n] = (pad + step * (i + 0.5), pad + li * gapy)
            width_of[n] = nw
    o = []
    for a, b in edges:
        if a not in pos or b not in pos:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        ay += nodeh
        mid = (ay + by) / 2
        o.append(path("M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f" % (ax, ay, ax, mid, bx, mid, bx, by),
                      LINE, "none", 1.1, 0.85))
    for li, row in enumerate(layers):
        for n in row:
            cx, y = pos[n]
            c = colour(n) if colour else acc(li)
            nw = width_of[n]
            x = cx - nw / 2
            o.append(rect(x, y, nw, nodeh,
                          "color-mix(in srgb, %s 12%%, var(--color-card))" % c, 4, 1, c, 1))
            if labels and nw >= 40:
                o.append(txt(cx, y + nodeh - 7, fit(n, nw - 10), FS_LABEL, INK, "middle", 500))
            o.append(title(n))
    return svg("".join(o), w, H)


def dot_dag(layers, edges, w=760, r=4.2, gapy=30, colour=None, pad=12, hubs=None):
    """The same graph with no labels — for a model too big to label. hubs get a name."""
    pos = {}
    H = pad * 2 + (len(layers) - 1) * gapy + 10
    for li, row in enumerate(layers):
        if not row:
            continue
        step = (w - pad * 2) / len(row)
        for i, n in enumerate(row):
            pos[n] = (pad + step * (i + 0.5), pad + li * gapy)
    o = []
    for a, b in edges:
        if a in pos and b in pos:
            ax, ay = pos[a]
            bx, by = pos[b]
            m = (ay + by) / 2
            o.append(path("M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f" % (ax, ay, ax, m, bx, m, bx, by),
                          LINE, "none", 0.9, 0.6))
    for li, row in enumerate(layers):
        for n in row:
            cx, cy = pos[n]
            c = colour(n) if colour else acc(li)
            o.append('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s" stroke="var(--color-card)" '
                     'stroke-width="1">%s</circle>' % (cx, cy, r, c, title(n)))
            if hubs and n in hubs:
                # A hub at the edge of the chart would hang its name outside it.
                label = fit(n, w - 2 * pad, FS_MICRO)
                half = text_w(label, FS_MICRO) / 2
                tx = min(max(cx, half + 2), w - half - 2)
                o.append(txt(tx, cy - 8, label, FS_MICRO, INK, "middle", 600))
    return svg("".join(o), w, H)


# ---------------------------------------------------------- weighted flow --

def flow(columns, links, w=760, colw=150, gap=6, minh=38, pad_t=10, colour=None,
         avail=320.0):
    """A weighted multi-column flow.

    columns = [[(id, label, weight)], ...] left to right
    links   = [(from_id, to_id, weight)]

    Node height is proportional to weight, so a ribbon's width is the count it carries.

    Three things make the ribbons readable, and the first is the one that matters:

    **Columns are ordered by what feeds them, not by weight.** Two barycenter sweeps put
    each node next to the nodes it connects to, which removes the crossings that carry
    no information. Ordering by weight looked tidier per column and produced a braid.

    **A ribbon is coloured by the source it starts from**, inherited through the middle
    column, so one strand keeps one colour end to end and can be followed by eye. One
    colour per column meant every left-to-right ribbon was the same blue.

    **Each ribbon carries a hairline edge**, so where two overlap the boundary is still
    visible, and hovering one dims the rest.
    """
    if not columns or not any(columns):
        return empty_note()

    cols = [list(c) for c in columns]
    col_of = {}
    for ci, col in enumerate(cols):
        for nid, _, _ in col:
            col_of[nid] = ci
    preds = collections.defaultdict(list)
    succs = collections.defaultdict(list)
    for aa, bb, v in links:
        if aa in col_of and bb in col_of:
            preds[bb].append((aa, v))
            succs[aa].append((bb, v))

    def row_of():
        return {nid: i / max(len(col) - 1, 1)
                for col in cols for i, (nid, _, _) in enumerate(col)}

    def sweep(indices, neighbours):
        pos = row_of()
        for ci in indices:
            keyed = []
            for k, (nid, lab, v) in enumerate(cols[ci]):
                ns = neighbours[nid]
                bary = (sum(pos[n] * wt for n, wt in ns) / sum(wt for _, wt in ns)
                        if ns else pos[nid])
                keyed.append((bary, k, (nid, lab, v)))
            cols[ci] = [t[2] for t in sorted(keyed, key=lambda t: (t[0], t[1]))]

    sweep(range(1, len(cols)), preds)
    sweep(range(len(cols) - 2, -1, -1), succs)
    sweep(range(1, len(cols)), preds)

    # A strand takes the colour of the source it starts from. A node in a later column
    # inherits from its heaviest inbound source, so a join keeps the colour of whatever
    # dominates it rather than introducing a hue nobody can trace back.
    strand = {}
    for i, (nid, _, _) in enumerate(cols[0]):
        strand[nid] = acc(i % 8)
    for ci in range(1, len(cols)):
        for nid, _, _ in cols[ci]:
            ns = sorted(preds[nid], key=lambda t: -t[1])
            strand[nid] = strand.get(ns[0][0], NEUTRAL % "dot") if ns else NEUTRAL % "dot"

    tallest = max(sum(v for _, _, v in col) for col in cols) or 1
    slots = max(len(col) for col in cols)
    scale = max((avail - (slots - 1) * gap) / tallest, 0.4)
    pos, H = {}, pad_t
    xstep = (w - colw) / max(len(cols) - 1, 1)
    for ci, col in enumerate(cols):
        y = pad_t
        for nid, lab, v in col:
            hgt = max(v * scale, minh)
            pos[nid] = (ci * xstep, y, hgt, lab, v, ci)
            y += hgt + gap
        H = max(H, y)

    o = []
    # Ribbons first, so a node's label always sits on top of the band it belongs to.
    # Widest first, so a thin strand is never hidden under a thick one.
    out_cur = collections.defaultdict(float)
    in_cur = collections.defaultdict(float)
    for aa, bb, v in sorted(links, key=lambda t: -t[2]):
        if aa not in pos or bb not in pos:
            continue
        ax, ay, ah, _, av, aci = pos[aa]
        bx, by, bh, _, bv, _ = pos[bb]
        th_a = ah * v / av if av else ah
        th_b = bh * v / bv if bv else bh
        y0, y1 = ay + out_cur[aa], by + in_cur[bb]
        out_cur[aa] += th_a
        in_cur[bb] += th_b
        x0, x1 = ax + colw, bx
        mx = (x0 + x1) / 2
        d = ("M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f L %.1f %.1f "
             "C %.1f %.1f %.1f %.1f %.1f %.1f Z"
             % (x0, y0, mx, y0, mx, y1, x1, y1,
                x1, y1 + th_b, mx, y1 + th_b, mx, y0 + th_a, x0, y0 + th_a))
        c = strand.get(aa, NEUTRAL % "dot")
        o.append('<path class="dcv-rib" d="%s" fill="%s" fill-opacity="0.42" stroke="%s" '
                 'stroke-width="0.5" stroke-opacity="0.65">%s</path>'
                 % (d, c, c, title("%s → %s: %s" % (pos[aa][3], pos[bb][3], v))))
    for nid, (x, y, hgt, lab, v, ci) in pos.items():
        c = colour(nid) if colour else strand.get(nid, NEUTRAL % "dot")
        o.append(rect(x, y, colw, hgt,
                      "color-mix(in srgb, %s 16%%, var(--color-card))" % c, 3, 1, c, 1)
                 + title("%s — %s" % (lab, v)))
        # Lines are whatever the box can hold, so a long connector string wraps rather
        # than being cut. The count sits in the corner instead of the vertical centre,
        # which is what used to cost the label its second half.
        room = max(1, int((hgt - 8) / (FS_AXIS * LH)))
        o.append(txt_wrap(x + 8, y + 13, lab, colw - 34, FS_AXIS, INK, "start", 500,
                          max_lines=room))
        o.append(num(x + colw - 7, y + 14, v, FS_VALUE))
    return svg("".join(o), w, H + 8)


# ============================================================ page parts ====

PARTS = []


def reset_parts():
    """`part()` appends to a module list, so a second render in one process would
    otherwise emit the first run's parts as well."""
    del PARTS[:]


def part(anchor, title_, kicker, tiers, lede=None):
    """tiers = [(anchor, heading, kicker, body_html)]

    `{n}` in the kicker becomes the number of cards the tiers actually emitted, so the
    count cannot drift from the page the way a literal does.
    """
    n = sum(h.count('class="dcv-card') for _, _, _, h in tiers)
    _check_numbering(anchor, tiers)
    PARTS.append((anchor, title_, kicker.replace("{n}", str(n)), lede, tiers))


def _check_numbering(anchor, tiers):
    """The card codes are literals in each card's head, so a reorder or an insert could
    put T9 above T8. Cheaper to assert the sequence here than to thread a counter through
    thirty-three call sites, and it fails the build rather than the reader.

    **Increasing, not contiguous.** The first version demanded `1..n` and duly failed on
    a folder of Power Query helper functions: with no native SQL anywhere, the SQL tier
    emits one card instead of two, so `Q7` is simply absent and the sequence reads
    `Q1..Q6, Q8..Q19`. That is correct behaviour -- a reading whose subject does not
    exist should be absent rather than present and empty, which is the same rule that
    says an analysis must name a subject. What must never happen is two cards swapping
    places, because a code is how a reader refers to a reading. So the codes stay stable
    and the gap is allowed.
    """
    letter = anchor[-1].upper()
    seen = [int(m) for _, _, _, h in tiers
            for m in re.findall(r'<span class="dcv-title">' + letter + r'(\d+)', h)]
    ok = all(b > a for a, b in zip(seen, seen[1:]))
    assert ok, ("card codes out of order in %s: %s"
                % (anchor, " ".join(letter + str(n) for n in seen)))


CARD_CODE = re.compile(r"^([TQ]\d+)\s")


def card_id(head):
    m = CARD_CODE.match(head or "")
    return ("card-" + m.group(1).lower()) if m else None


def card(inner, head=None, meta=None):
    h = ""
    if head:
        h = '<div class="dcv-head"><span class="dcv-title">%s</span>%s</div>' % (
            E(head), '<span class="dcv-meta">%s</span>' % E(meta) if meta else "")
    cid = card_id(head)
    # .dc-full lifts the 39rem measure cap, but only on a direct child of .dc-prose --
    # which is what a card is here. Prose stays narrow; charts get the whole sheet.
    return '<div class="dcv-card dc-full"%s>%s%s</div>' % (
        (' id="%s" tabindex="-1"' % cid) if cid else "", h, inner)


def how(*paras):
    """Method notes, folded. The card shows the reading; how it was read is one click
    away rather than a paragraph in front of it."""
    body = "".join("<p>%s</p>" % p for p in paras if p)
    return ('<details class="sma-how"><summary>How to read this</summary>'
            '<div class="sma-how-body">%s</div></details>' % body) if body else ""


def pair(chart, grid_html, chart_note=None):
    """One analysis, one row: the chart on the left, the same numbers on the right.

    A chart answers "what is the shape of this" at a glance and is bad at "what exactly
    is the third one". A table answers the second and is bad at the first. Neither
    replaces the other, so both are shown from one dataset -- and because they are built
    from the same list, they cannot disagree.
    """
    return (
        '<div class="dcv-pair">'
        '<div class="dcv-pair-chart">%s%s</div>'
        '<div class="dcv-pair-grid">%s</div>'
        "</div>" % (chart, chart_note or "", grid_html)
    )


def gtable(headers, rows, aligns=None):
    """A compact table for the right half of a pair.

    Its own class rather than `.dc-table`, because the prose table's padding is right
    for four rows of prose and wrong for fourteen rows of numbers sitting beside a
    chart of the same height.
    """
    aligns = aligns or ["l"] * len(headers)
    th = "".join('<th class="a-%s">%s</th>' % (a, E(h)) for h, a in zip(headers, aligns))
    tb = "".join(
        "<tr>%s</tr>" % "".join('<td class="a-%s">%s</td>' % (a, c)
                                for c, a in zip(r, aligns))
        for r in rows)
    return ('<div class="dcv-gwrap"><table class="dcv-gtable">'
            "<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>" % (th, tb))


def dot(colour):
    return '<i class="dcv-swatch" style="background:%s"></i>' % colour


def grid(*cards):
    return '<div class="dcv-grid dc-full">%s</div>' % "".join(cards)


def note(kind, title_, body):
    return (
        '<div class="dc-callout %s"><p class="dc-callout-title">%s</p>'
        '<div class="dc-callout-body"><p>%s</p></div></div>' % (kind, E(title_), body)
    )


def table(headers, rows, num_tail=True):
    th = "".join("<th>%s</th>" % E(h) for h in headers)
    tb = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)
    return (
        '<div class="dc-table-wrap"><table class="dc-table %s"><thead><tr>%s</tr></thead>'
        "<tbody>%s</tbody></table></div>" % ("num-tail" if num_tail else "", th, tb)
    )


def chip(text, tone=""):
    return '<span class="dcv-chip %s">%s</span>' % (tone, E(text))


def meter(label, value, total, tone="ok"):
    pct = 0 if not total else round(100 * value / total)
    return (
        '<div class="dcv-meter"><div class="dcv-meter-top"><span>%s</span>'
        '<b class="dc-num">%s%%</b></div><div class="dcv-track"><i class="%s" style="width:%s%%"></i></div>'
        '<p class="dcv-meter-sub">%s of %s</p></div>' % (E(label), pct, tone, pct, value, total)
    )


def rail(stats):
    cells = "".join(
        '<div class="dc-rail-stat"><span class="dc-rail-value">%s</span>'
        '<span class="dc-rail-label">%s</span><span class="dcv-rail-sub">%s</span></div>'
        % (fmt(v), E(l), E(s)) for l, v, s in stats
    )
    return '<div class="dc-rail dcv-rail dc-full">%s</div>' % cells


def scorecard(checks):
    o = []
    for label, v, t, thr in checks:
        ratio = 0 if not t else v / t
        tone = "ok" if ratio >= thr else ("warn" if ratio >= thr * 0.6 else "bad")
        o.append(
            '<div class="dcv-score %s"><span class="dcv-score-dot"></span>'
            '<span class="dcv-score-label">%s</span>'
            '<span class="dcv-score-val dc-num">%d / %d</span>'
            '<span class="dcv-score-thr">target %d%%</span></div>'
            % (tone, E(label), v, t, round(thr * 100))
        )
    return '<div class="dcv-scores">%s</div>' % "".join(o)


def bridge_rows():
    """One row per table the model mentions, joining what each parser knows about it.

    Computed in `dataset.py`, not here: deciding that a table is "referenced, not
    declared" is analysis, and a renderer reaching its own verdict would be a second
    opinion nobody asked for.
    """
    return REPORT["tmdl"]["bridge"] if HAS_TMDL else []


def t_bridge():
    rows = bridge_rows()
    body = [[
        "<b>%s</b>" % E(r["name"]),
        '<span class="dc-num">%d</span>' % r["fan"],
        E(r.get("storage") or "—"),
        "<code>%s</code>" % E("%s%s" % (r["kind"] or "—", (" / " + r["mode"]) if r["mode"] else "")),
        E(", ".join(r["conns"])) or "—",
        chip(r["state"], r["tone"]),
    ] for r in rows]
    missing = [r for r in rows if not r["declared"]]
    storages = collections.Counter(r.get("storage") for r in rows if r["declared"] and r.get("storage"))
    out = card(table(["Table", "Measures reading it", "Storage", "Partition kind / mode",
                      "Connector found in its M", "State"], body, False)
               + '<p class="dc-caption"><b>Storage:</b> %s.</p>'
               % E(", ".join("%s %d" % (k, v) for k, v in storages.most_common()))
               + how("Storage is read off the partition's kind, mode and source. An "
                     "<code>entity</code> partition is Direct Lake only when its mode says so; "
                     "with <code>mode: directQuery</code> and an <code>expressionSource</code> it "
                     "is DirectQuery to another semantic model."),
               "T6  The partition bridge",
               "%d tables — %d declared, %d referenced only"
               % (len(rows), len(rows) - len(missing), len(missing)))
    if missing:
        out += note(
            "warn", "Undeclared",
            "<b>The parse is incomplete, and only the join shows it.</b> %s %s referenced by "
            "DAX in this model but never declared as a table in the TMDL that was handed over. "
            "The TMDL parser cannot know — it only sees what it was given. The M "
            "reading of the partitions cannot know — those tables have no partition to read. It "
            "shows up only when the two readings are put side by side, which is why this part "
            "runs the M parser over the TMDL rather than over the properties alone."
            % (", ".join("<code>%s</code>" % E(r["name"]) for r in missing),
               "is" if len(missing) == 1 else "are"))
    return out


def json_island(payload):
    """JSON for a `<script type="application/json">` island.

    `<` is written as the six-character escape \u003c so a name, a description or an
    expression containing `</script><script>` cannot end the island early and run. JSON
    parses the escape back to the same character, so the page reads what the model said.

    The first version wrote the escape as a Python string escape, which evaluates to a
    literal `<` -- so the replace was a no-op and the injection went through. Caught by an
    outside review; proven by a test that puts `</script><script>` into a description
    and asserts both kinds of island survive it.
    """
    return json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")


def graph_block(mount_id, data_id, payload, spec):
    """A mount point, its data island, and the call that binds them."""
    blob = json_island(payload)
    return (
        '<div id="%s" class="dcvg-mount"></div>'
        '<script type="application/json" id="%s">%s</script>'
        "<script>dcvGraph(%s, %s, %s);</script>"
        % (mount_id, data_id, blob, json.dumps(mount_id), json.dumps(data_id),
           json.dumps(spec))
    )


def chain_block(mount_id, data_id, payload):
    """A mount point, its data island, and the call that binds them. The chain used to
    read the island the lineage canvas wrote; the canvas is gone (Oct-2026: two views of
    one graph, and the chain is the one a reader uses), so the chain writes its own."""
    blob = json_island(payload)
    return (
        '<div id="%s" class="dcvg-mount"></div>'
        '<script type="application/json" id="%s">%s</script>'
        "<script>dcvChain(%s, %s);</script>"
        % (mount_id, data_id, blob, json.dumps(mount_id), json.dumps(data_id))
    )


def tabular_block(mount_id, data_id, payload):
    """A mount, a data island, and the call that binds them.

    Same contract the two canvases use: the generator emits data, the widget emits
    markup. `<` is escaped so a description containing `</script` cannot end the island.
    """
    blob = json_island(payload)
    return (
        '<div id="%s" class="dcvg-mount"></div>'
        '<script type="application/json" id="%s">%s</script>'
        "<script>dcvTabular(%s, %s);</script>"
        % (mount_id, data_id, blob, json.dumps(mount_id), json.dumps(data_id))
    )


DASH_EM = chr(0x2014)
DASH_MID = chr(0xb7)

FLAG_HINT = {
    "$": "currency symbol",
    "%": "shown as a percentage",
    ",": "thousands separator",
    "\u00b1": "explicit negative section in the mask",
}


def fmt_cells(row):
    """The three format columns, as the ribbon would show them."""
    r = read_format(row.get("formatString"))
    return {
        "word": {"label": r["word"], "tone": FORMAT_TONE.get(r["word"], "var(--a0-dot)")},
        "flags": [{"sym": f, "hint": FLAG_HINT.get(f, "")} for f in r["flags"]],
        "dp": "Auto" if r["dp"] is None else r["dp"],
        "mask": row.get("formatString") or "",
    }


def group_rows(rows, key, order=None):
    """Group and sort. Measures group by display folder, columns by table -- never flat:
    the question of a column is always what THIS table looks like."""
    buckets = collections.OrderedDict()
    for r in rows:
        buckets.setdefault(r[key] or "Other", []).append(r)
    keys = order or sorted(buckets, key=lambda k: (k == "Other", k.lower()))
    return [(k, buckets[k]) for k in keys if k in buckets]


# One column per view carries no width: it absorbs the slack, and every other column
# then lands in the same place in every group.
FORMAT_COLS = [
    {"key": "name", "label": "Name", "grow": True},
    {"key": "word", "label": "Format", "kind": "chip", "w": 132},
    {"key": "flags", "label": "Properties", "kind": "flags", "w": 104},
    {"key": "dp", "label": "Decimals", "kind": "num", "w": 86},
    {"key": "mask", "label": "Format string", "kind": "code", "blank": "not set", "w": 212},
]


def format_payload():
    views = []

    m_rows, m_groups = [], []
    for folder, items in group_rows(MEAS, "folder"):
        rows = []
        for m in sorted(items, key=lambda m: m["name"].lower()):
            c = fmt_cells(m)
            c["name"] = m["name"]
            rows.append(c)
            m_rows.append(c)
        m_groups.append({"key": folder, "label": folder, "rows": rows})
    views.append({
        "id": "measures", "label": "Measures", "hint": "grouped by display folder",
        "columns": FORMAT_COLS, "groups": m_groups,
        "stats": fmt_stats(MEAS, "measures"),
        "note": "By display folder. The first three columns are what the format pane "
                "shows; the mask is what TMDL stores.",
    })

    c_cols = [FORMAT_COLS[0], {"key": "type", "label": "Data type", "w": 112}] + FORMAT_COLS[1:]
    c_groups = []
    for tname, items in group_rows(COLS, "table"):
        rows = []
        for c in sorted(items, key=lambda c: c["name"].lower()):
            cell = fmt_cells(c)
            cell["name"] = c["name"]
            cell["type"] = c["dataType"] or ""
            rows.append(cell)
        c_groups.append({"key": tname, "label": tname, "rows": rows})
    views.append({
        "id": "columns", "label": "Columns", "hint": "grouped by table",
        "columns": c_cols, "groups": c_groups,
        "stats": fmt_stats(COLS, "columns"),
        "note": "By table, so an odd one out among its siblings stands out.",
    })
    return {"views": views}


def fmt_stats(rows, noun):
    masks = collections.Counter(r.get("formatString") or "" for r in rows)
    unset = masks.get("", 0)
    once = sum(1 for m, n in masks.items() if m and n == 1)
    words = {read_format(r.get("formatString"))["word"] for r in rows}
    return [
        {"value": unset, "label": "no format set",
         "sub": "of %d %s" % (len(rows), noun), "tone": "bad" if unset else "ok"},
        {"value": len([m for m in masks if m]), "label": "distinct format strings"},
        {"value": once, "label": "used exactly once",
         "sub": "usually a slip", "tone": "warn" if once else "ok"},
        {"value": len(words), "label": "Format dropdown words in play"},
    ]


DESC_COLS = [
    {"key": "name", "label": "Name", "w": 264},
    {"key": "desc", "label": "Description", "kind": "prose", "blank": "not documented",
     "grow": True},
]


def desc_stats(rows, noun):
    got = sum(1 for r in rows if (r.get("description") or "").strip())
    missing = len(rows) - got
    return [
        {"value": missing, "label": "missing a description",
         "sub": "of %d %s" % (len(rows), noun), "tone": "bad" if missing else "ok"},
        {"value": got, "label": "documented"},
        {"value": "%d%%" % round(100 * got / len(rows)) if rows else "0%",
         "label": "coverage", "sub": "no threshold attached"},
    ]


def desc_group(key, label, items):
    rows = [{"name": r["name"], "desc": (r.get("description") or "").strip()}
            for r in sorted(items, key=lambda r: r["name"].lower())]
    got = sum(1 for r in rows if r["desc"])
    return {"key": key, "label": label, "rows": rows,
            "n": "%d/%d" % (got, len(rows)),
            "nTone": "ok" if got == len(rows) else "",
            "nHint": "%d of %d documented" % (got, len(rows))}


def description_payload():
    m_groups = [desc_group(f, f, items) for f, items in group_rows(MEAS, "folder")]
    c_groups = [desc_group(t, t, items) for t, items in group_rows(COLS, "table")]

    return {"views": [
        {"id": "measures", "label": "Measures", "hint": "grouped by display folder",
         "columns": DESC_COLS, "groups": m_groups, "stats": desc_stats(MEAS, "measures"),
         "note": "No description: the meaning lives only with whoever wrote it."},
        {"id": "columns", "label": "Columns", "hint": "grouped by table",
         "columns": DESC_COLS, "groups": c_groups, "stats": desc_stats(COLS, "columns"),
         "note": "By table. A key rarely needs a sentence; a flag column usually does."},
        {"id": "tables", "label": "Tables", "hint": "flat -- a table has no parent",
         "columns": DESC_COLS,
         "groups": [desc_group("all", "All tables", TROWS)],
         "stats": desc_stats(TROWS, "tables"),
         "note": "Tables carry a description but no format."},
    ]}


def t_across():
    """The same two questions, asked of every grain that can answer them."""
    fmt_card = card(
        tabular_block("dcvt-format", "dcvt-format-data", format_payload())
        + how("The Format word and the lit toggles are decoded from the mask, the only thing "
              "TMDL stores, the same way Power BI's format pane derives them. A mask the "
              "decoder cannot read fails the build rather than showing a wrong word."),
        "T4  Format " + DASH_EM + " the number as a visual shows it",
        "measures and columns " + DASH_MID + " a table has no format")

    desc_card = card(
        tabular_block("dcvt-desc", "dcvt-desc-data", description_payload())
        + '<p class="dc-caption">The stripe counts what is missing: that is the work.</p>',
        "T5  Description " + DASH_EM + " what is documented, and what is not?",
        "measures, columns and tables")
    return fmt_card + desc_card


def tmdl_graph_payload():
    items = []
    for m in MEAS:
        a = an(m)
        meta = [["Table", m["table"]]]
        if m["formatString"]:
            meta.append(["Format", m["formatString"]])
        meta.append(["Weight", "%s  ·  %s lines  ·  chain %s"
                     % (a.get("weight", 0), a.get("lines", 0), a.get("chain", 0))])
        if m["isHidden"]:
            meta.append(["Visibility", "Hidden in the model"])
        items.append({
            "id": m["id"], "name": m["name"], "folder": m["folder"],
            "folderPath": m["folderPath"], "dependsOn": m["dependsOn"], "usedBy": m["usedBy"],
            "isHidden": m["isHidden"], "description": m["description"],
            "meta": meta, "tables": m["allTables"],
            "code": a.get("formatted") or m["expression"],
            "codeHtml": dax_html(a.get("formatted") or m["expression"]),
            # One lowercased haystack rather than four fields checked at keystroke
            # time -- the app searches name, description, folder and tables.
            "search": " ".join([m["name"], m["description"], m["folderKey"]]
                               + m["allTables"]).lower(),
        })
    folders = sorted({m["folder"] for m in MEAS}, key=lambda f: (f == "Other", f.lower(), f))
    return {"items": items, "groups": folders}


def m_graph_payload():
    items = []
    for q in QRY:
        src = q["sources"][0] if q["sources"] else None
        meta = [["Kind", q["kind"] or "query"], ["Layer", str(q["layer"])]]
        if src:
            where = src.get("database") or src.get("target") or ""
            meta.append(["Source", src["label"] + (" · " + where if where else "")])
        meta.append(["Steps", "%d%s" % (len(q["steps"]),
                                        ", %d dead" % sum(1 for x in q["steps"] if x["dead"])
                                        if any(x["dead"] for x in q["steps"]) else "")])
        if q["foldBreakStep"]:
            meta.append(["Potential folding barrier", q["foldBreakStep"]])
        native = next((x.get("nativeQuery") for x in q["sources"] if x.get("nativeQuery")), None)
        items.append({
            "id": q["id"], "name": q["name"], "layer": q["layer"],
            "kind": q["kind"] or "query",
            "sub": (src["label"] if src else ("derived" if q["dependsOn"] else "no source")),
            "dependsOn": q["dependsOn"], "usedBy": q["usedBy"],
            "isHidden": False, "description": q["description"],
            "meta": meta, "tables": sorted({x["label"] for x in q["sources"]}),
            "code": native, "codeHtml": None,
            "list": [[s["name"], s["label"], s["dead"]] for s in q["steps"]],
            "search": " ".join(
                [q["name"], q["kind"] or "", str(q["sourceKind"] or "")]
                + [s["name"] for s in q["steps"]]
                + [s["label"] for s in q["steps"]]
                + [(native or "")]).lower(),
        })
    return {"items": items, "groups": MD["layers"]}


# ============================================== PART T — the TMDL model =====

def folder_colours():
    return {f: acc(i) for i, f in enumerate(sorted({m["folder"] for m in MEAS}))}


def measure_layers():
    depth = {m["name"]: an(m).get("chain", 0) for m in MEAS}
    layers = [[] for _ in range(max(depth.values()) + 1)]
    for m in MEAS:
        layers[depth[m["name"]]].append(m["name"])
    for lay in layers:
        lay.sort(key=lambda n: (BY_NAME[n]["folder"], n))
    edges = [(d, m["name"]) for m in MEAS for d in m["dependsOn"] if d in BY_NAME]
    return layers, edges


class FindingView(dict):
    """Attribute access over a dataset finding.

    The cards were written against the rule objects and read `f.code`. The dataset
    carries plain dicts, because JSON does. One four-line view keeps both readings
    working rather than rewriting a dozen call sites into subscripts -- and it stays a
    dict, so `f["code"]` and `f.get(...)` still mean what they always meant.
    """

    __getattr__ = dict.__getitem__


def findings():
    """From the dataset. The rules ran during analysis, not during rendering."""
    return [FindingView(f) for f in (FINDINGS or [])]


CODE_LABEL = {
    "parse-failed": "Could not be read",
    "duplicate-calculation": "Two names, one calculation",
    "derived-format-mismatch": "A derived measure formats unlike its base",
    "deep-chain": "Deep dependency chains",
    "time-intel-mixed": "More than one spelling of the same period comparison",
    "format-split-folder": "A folder that disagrees with itself about format",
    "no-table-reference": "Measures that read no table",
    "undeclared-table": "Referenced but never declared",
    "implicit-measure": "Implicit summarisation left on",
    "unreferenced-measure": "Not referenced by another parsed object",
    "unformatted-measure": "Visible, numeric, no format string",
    "text-column-summarized": "Text columns set to summarise",
    "sort-by-missing": "A name column with no sort-by",
    "inactive-relationship": "Inactive relationships",
    "userelationship-unknown": "USERELATIONSHIP with nothing to activate",
    "bidirectional-relationship": "Filtering in both directions",
    "many-to-many-relationship": "Many-to-many relationships",
    "disconnected-table": "Tables no relationship touches",
    "relationship-loop": "A loop in the active relationships",
    "relationship-column-missing": "A relationship names a column that is not there",
}

FINDING_COLS = [
    {"key": "level", "label": "", "kind": "level", "w": 92},
    {"key": "subject", "label": "Subject", "w": 230},
    {"key": "message", "label": "What it says", "kind": "prose", "grow": True},
    {"key": "where", "label": "Where", "kind": "code", "blank": "", "w": 190},
]


def where_text(f):
    """`file:line` for a finding whose subject has a source, or blank."""
    w = f.get("where") or {}
    return "%s:%s" % (w.get("file"), w.get("line")) if w.get("file") else ""


def findings_payload():
    fs = findings()

    err_all = sum(1 for f in fs if f.level == "error")
    warn_all = sum(1 for f in fs if f.level == "warning")
    codes_all = len({f.code for f in fs})
    strip = ([{"value": err_all, "label": "could not be read", "sub": "level: error",
               "tone": "bad"}] if err_all else []) + [
        {"value": warn_all, "label": "worth acting on", "sub": "level: warning",
         "tone": "bad" if warn_all else "ok"},
        {"value": len(fs) - warn_all - err_all, "label": "worth knowing", "sub": "level: note"},
        {"value": codes_all, "label": "rules that fired", "sub": "of %d written" % len(FINDING_HELP)},
        {"value": len({f.subject for f in fs}), "label": "named subjects"},
    ]
    if SUPPRESSED:
        strip.append({"value": len(SUPPRESSED), "label": "suppressed by the rules file",
                      "sub": "kept in report.json"})

    def view(level_filter, vid, label, hint):
        picked = [f for f in fs if level_filter(f.level)]
        groups = []
        for code in sorted({f.code for f in picked},
                           key=lambda c: (-sum(1 for f in picked if f.code == c), c)):
            rows = [{"level": {"label": f.level, "tone": f.level},
                     "subject": f.subject, "message": f.message, "where": where_text(f)}
                    for f in picked if f.code == code]
            groups.append({"key": code, "label": CODE_LABEL.get(code, code), "rows": rows,
                           "meta": code, "help": FINDING_HELP[code]})
        return {
            "id": vid, "label": "%s (%d)" % (label, len(picked)), "hint": hint,
            "columns": FINDING_COLS, "groups": groups,
            "stats": strip, "note": hint,
        }

    return {"views": [
        view(lambda l: l in ("error", "warning"), "warnings", "Worth acting on",
             "Each names one subject and a decision. Unreadable text (error) comes first."),
        view(lambda l: l == "info", "notes", "Worth knowing",
             "Context for a review, not a task list."),
        view(lambda l: True, "all", "Everything",
             "Every rule that fired, errors and warnings first."),
    ]}


def t_findings():
    """The review layer. First, because it is what a reader came for."""
    fs = findings()
    rules_quiet = sorted(set(FINDING_HELP) - {f.code for f in fs})
    quiet = ""
    if rules_quiet:
        quiet = ("<b>Ran and found nothing:</b> %s."
                 % ", ".join("<code>%s</code>" % E(c) for c in rules_quiet))
    suppressed = ""
    if SUPPRESSED:
        suppressed = ('<p class="dc-caption">%d %s suppressed by the rules file and kept in '
                      "<code>report.json</code> under <code>suppressed</code>, each with the "
                      "reason given: %s.</p>"
                      % (len(SUPPRESSED), "finding" if len(SUPPRESSED) == 1 else "findings",
                         "; ".join(sorted({E(s["suppressedBy"]["reason"]) for s in SUPPRESSED}))))
    return card(
        tabular_block("dcvt-find", "dcvt-find-data", findings_payload())
        + suppressed
        + how("<b>Levels.</b> A modelling observation is <code>warning</code> or "
              "<code>info</code>, never a verdict: nothing here has seen the report or the data. "
              "<code>error</code> means only that the parser could not read the text.",
              "<b>Where</b> is the file and line the subject is declared on. Each rule group "
              "says how fixing it helps; rules that could not say so were dropped for noise.",
              quiet),
        "T1  What should I look at first?",
        "%d findings %s %d rules %s errors and warnings first"
        % (len(fs), chr(0xb7), len({f.code for f in fs}), chr(0xb7)))


def t_structural():
    if not MEAS:
        # The chain lays out measures; with none the renderer drew an SVG of negative
        # width (a calculation-group file on its own did this). One card says so.
        return card('<p class="dc-caption">This model declares no measure, so there is no '
                    'lineage to draw. Tables, columns and relationships are below.</p>',
                    "T2  Chain explorer", "no measures")
    return card(
        chain_block("dcvc-chain", "dcvg-tmdl-data", tmdl_graph_payload())
        + how("Above the selected measure: what it reads. Below: what reads it, step by step. "
              "The rail is the model's folder tree; its search ranks by match, then by how much "
              "of the model leans on the measure. Click a node for its facts."),
        "T2  Chain explorer",
        "%d measures, %d links, %d folders \u2014 one measure at a time, from the folder rail"
        % (len(MEAS), TD["linkCount"], len(TD["folders"])))


def t_blast():
    """Impact, ranked. Kept as a bar because a bar is what a ranking is."""
    ranked = sorted(MEAS, key=lambda m: (-len(m["usedBy"]), m["name"].lower()))
    ref = [m for m in ranked if m["usedBy"]][:12]
    rows = [[E(m["name"]), '<span class="dcv-ex">%s</span>' % E(m["folder"]),
             str(len(m["usedBy"])), str(an(m).get("chain", 0))]
            for m in ranked if m["usedBy"]]
    return card(
        pair(ranked_bar([(m["name"], len(m["usedBy"])) for m in ref],
                        labw=180, barw=300, colour=lambda i, l: acc(2)),
             gtable(["Measure", "Folder", "Read by", "Depth"], rows, ["l", "l", "r", "r"]),
             '<p class="dc-caption">Top twelve; the table lists all.</p>')
        + how("<b>Read by</b> is how many measures change when this one does. <b>Depth</b> is "
              "its distance from a base measure. High on both: load-bearing and hard to "
              "reason about."),
        "T3  Which measures is the model built on?",
        "%d measures something reads" % sum(1 for m in MEAS if m["usedBy"]))


SUMMARISE_TONE = {"none": NEUTRAL % "dot", "sum": NEG, "average": WARN,
                  "count": WARN, "min": WARN, "max": WARN, "distinctCount": WARN,
                  "(unset)": acc(3)}


def t_orientation():
    """Orientation, at the end, where orientation belongs.

    These describe the model's shape rather than naming anything to do about it, so
    they come after the review. Each one is here because it answers a question a reader
    asks in their first ten minutes on an unfamiliar model and never asks again.
    """
    fn = collections.Counter()
    for m in MEAS:
        for f in an(m).get("functions", []):
            fn[f["name"]] += f["count"]
    HOT = {"FILTER", "EARLIER", "ALLEXCEPT", "SUMX", "ADDCOLUMNS", "CROSSJOIN", "GENERATE"}
    fn_meas = collections.defaultdict(list)
    for m in MEAS:
        for f in an(m).get("functions", []):
            fn_meas[f["name"]].append(m["name"])
    fn_rows = [[dot(WARN if k in HOT else acc(1)) + "<code>%s</code>" % E(k), str(v),
                str(len(fn_meas[k])),
                '<span class="dcv-ex">%s</span>'
                % E(clip(", ".join(sorted(set(fn_meas[k]))), 40))]
               for k, v in fn.most_common()]
    dialect = card(pair(
        ranked_bar(fn.most_common(14), labw=150, barw=250,
                   colour=lambda i, l: WARN if l in HOT else acc(1)),
        gtable(["Function", "Calls", "Measures", "For example"], fn_rows,
               ["l", "r", "r", "l"]),
        '<p class="dc-caption">Top fourteen by calls; the table is all %d.</p>' % len(fn))
                   + how("Tells a SUM-and-DIVIDE model from one that iterates. Amber marks an "
                         "iterator or a context transition. A count is not a task: the measures "
                         "using each function are in the table."),
                   "T11  What DAX does this model speak?",
                   "%d distinct functions " % len(fn) + chr(0xb7) + " orientation, not a task list")

    dt = collections.defaultdict(collections.Counter)
    for c in COLS:
        dt[c["table"]][c["dataType"] or "unset"] += 1
    types = sorted({t for c in dt.values() for t in c})
    tcol = {t: acc(i) for i, t in enumerate(types)}
    ordered = sorted(dt.items(), key=lambda kv: -sum(kv[1].values()))
    type_rows = [[E(t)] + [str(c.get(ty, 0) or "") for ty in types]
                 + [str(sum(c.values()))] for t, c in ordered]
    typescard = card(pair(
        stacked_rows([(t, dict(c)) for t, c in ordered], types, tcol, labw=140, barw=220)
        + legend([(t, tcol[t]) for t in types]),
        gtable(["Table"] + [t for t in types] + ["All"], type_rows,
               ["l"] + ["r"] * (len(types) + 1)))
                     + how("A table of nearly all strings has nothing to aggregate. A date "
                           "typed as text is the most common reason time intelligence returns "
                           "blank."),
                     "T12  What types does each table carry?",
                     "%d columns over %d tables" % (len(COLS), len(dt)))

    rows = []
    for t in sorted(TROWS, key=lambda t: -(t.get("columns", 0) + t.get("measures", 0))):
        rows.append((t["name"], {
            "measures": t.get("measures", 0),
            "visible columns": t.get("columns", 0) - t.get("hiddenColumns", 0),
            "hidden columns": t.get("hiddenColumns", 0),
        }))
    keys = ["measures", "visible columns", "hidden columns"]
    cols = {"measures": acc(0), "visible columns": acc(2), "hidden columns": NEUTRAL % "dot"}
    shape_rows = [[E(n)] + [str(d[k]) for k in keys]
                  + [str(sum(d.values()))] for n, d in rows]
    shape = card(pair(
        stacked_rows(rows, keys, cols, labw=140, barw=260)
        + legend([(k, cols[k]) for k in keys]),
        gtable(["Table", "Measures", "Visible", "Hidden", "All"], shape_rows,
               ["l", "r", "r", "r", "r"]))
                 + how("All measures: a measure table. Mostly hidden columns: plumbing nobody "
                       "is meant to see. How each table loads is T6."),
                 "T13  How big is each table, and how much is hidden?",
                 "%d tables" % len(TROWS))
    # Each of these is a chart-and-table pair, and a pair needs the whole row: half a
    # row truncated the matrix and squeezed the bars to slivers.
    return dialect + typescard + shape


PART_TONE = {"m": acc(2), "calculated": acc(4), "query": acc(3), "entity": acc(1),
             "none": NEUTRAL % "dot"}
MODE_TONE = {"import": acc(0), "directLake": acc(3), "dual": acc(4), "directQuery": acc(6)}


def partition_strip(w=760):
    rows = []
    for t in TABS:
        for p in (t["partitions"] or [{"kind": "none", "mode": None}]):
            rows.append((t["name"], p))
    H = len(rows) * 30 + 14
    o = []
    for i, (tn, p) in enumerate(rows):
        y = i * 30 + 6
        o.append(txt(150, y + 15, fit(tn, 142), FS_LABEL, INK, "end", 500))
        k = p.get("kind") or "none"
        o.append(rect(160, y + 2, 120, 20, PART_TONE.get(k, NEUTRAL % "dot"), 3, 0.9))
        o.append(txt(220, y + 16, k, FS_AXIS, "var(--color-card)", "middle", 600))
        m = p.get("mode") or "—"
        o.append(rect(288, y + 2, 120, 20, MODE_TONE.get(m, NEUTRAL % "tint"), 3, 0.9))
        o.append(txt(348, y + 16, m, FS_AXIS,
                     "var(--color-card)" if m in MODE_TONE else MUTED, "middle", 600))
        src = (p.get("source") or "").strip().splitlines()
        o.append(txt(418, y + 16, clip(src[0] if src else "—", 46), 10, MUTED, "start", 400, True))
    return svg("".join(o), w, H)


def t_tmdl_checks():
    described = sum(1 for m in MEAS if m["description"].strip())
    fmtd = sum(1 for m in MEAS if m["formatString"])
    return [
        ("Measures with a description", described, len(MEAS), 0.60),
        ("Measures with a format string", fmtd, len(MEAS), 0.80),
        ("Measures referenced by another parsed object, or hidden",
         len(MEAS) - len(UNREFERENCED), len(MEAS), 0.95),
        ("Measures in a named folder",
         sum(1 for m in MEAS if m["folder"] != "Other"), len(MEAS), 0.90),
        ("Chains no deeper than 8",
         sum(1 for m in MEAS if an(m).get("chain", 0) <= 8), len(MEAS), 0.95),
        ("Tables with a declared partition",
         sum(1 for t in TABS if t["partitions"]), len(TABS), 0.90),
        # Both of these read the partition M as well as the TMDL properties. The input is
        # still only TMDL -- the M is already inside it -- which is why they live here.
        ("Referenced tables that are declared",
         sum(1 for r in bridge_rows() if r["declared"]), len(bridge_rows()), 1.00),
        ("Declared tables with a readable load path",
         sum(1 for r in bridge_rows()
             if r["declared"] and (r["conns"] or r["kind"] in ("calculated", "entity")
                                   or r.get("storage") == "Calculation group")),
         sum(1 for r in bridge_rows() if r["declared"]), 0.90),
    ]


_GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def colref(table, column):
    return "<code>%s[%s]</code>" % (E(table), E(column))


def lr_dag(layers, edges, w=760, colour=None, edge_style=None, pad=12, gap=56):
    """A layered graph read left to right: one column per layer, one row per node, and
    every node carries its full name.

    The first version stacked each layer as a horizontal band, so twenty tables shared
    760px and every label was cut to three letters; a reader could count the boxes and
    learn nothing else. Columns give each name a 200px row, and the height grows with the
    widest layer instead of the labels shrinking. Rows are ordered by the barycenter of
    their neighbours, two sweeps, so the links cross as little as a one-pass layout gets.

    `edges` are (a, b) or (a, b, meta); `edge_style(meta)` returns (stroke, width, dash,
    title) so a caller can say inactive or both-directions on the line itself.
    """
    layers = [list(row) for row in layers if row]
    if not layers:
        return empty_note()
    where = {n: li for li, row in enumerate(layers) for n in row}
    tri = [(e[0], e[1], e[2] if len(e) > 2 else None) for e in edges]
    tri = [e for e in tri if e[0] in where and e[1] in where]
    nbrs = {n: [] for n in where}
    for a, b, _ in tri:
        nbrs[a].append(b)
        nbrs[b].append(a)

    def sweep(order_of):
        for li in order_of:
            idx = {n: i for row in layers for i, n in enumerate(row)}
            def key(n):
                ns = [idx[m] for m in nbrs[n] if abs(where[m] - li) == 1]
                return (sum(ns) / len(ns)) if ns else idx[n]
            layers[li] = sorted(layers[li], key=key)
    sweep(range(1, len(layers)))
    sweep(range(len(layers) - 2, -1, -1))

    L = len(layers)
    colw = min(280, (w - 2 * pad - gap * (L - 1)) / L)
    if L > 1:
        gap = (w - 2 * pad - colw * L) / (L - 1)
    widest = max(len(row) for row in layers)
    dense = widest > 45
    size = FS_MICRO if dense else FS_LABEL
    nodeh = 16 if dense else 24
    wrap = {}
    for row in layers:
        for n in row:
            wrap[n] = (not dense) and text_w(n, size) > colw - 12
    rowh = {li: (38 if any(wrap[n] for n in row) and not dense else nodeh) + (3 if dense else 8)
            for li, row in enumerate(layers)}
    H = 2 * pad + max(len(row) * rowh[li] for li, row in enumerate(layers))
    pos = {}
    for li, row in enumerate(layers):
        x = pad + li * (colw + gap)
        step = (H - 2 * pad) / len(row)
        h = rowh[li] - (3 if dense else 8)
        for i, n in enumerate(row):
            pos[n] = (x, pad + step * (i + 0.5) - h / 2, h)
    o = []
    for a, b, meta in tri:
        ax, ay, ah = pos[a]
        bx, by, bh = pos[b]
        if where[a] <= where[b]:
            x1, x2 = ax + colw, bx
        else:
            x1, x2 = ax, bx + colw
        y1, y2 = ay + ah / 2, by + bh / 2
        mid = (x1 + x2) / 2
        stroke, sw, dash, tip = (edge_style(meta) if edge_style else (LINE, 1.1, None, None))
        o.append(path("M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f" % (x1, y1, mid, y1, mid, y2, x2, y2),
                      stroke, "none", sw, 0.85, dash=dash)
                 + (title(tip) if tip else ""))
    for li, row in enumerate(layers):
        for n in row:
            x, y, h = pos[n]
            c = colour(n) if colour else acc(li * 3)
            o.append(rect(x, y, colw, h, "color-mix(in srgb, %s 12%%, var(--color-card))" % c, 4, 1, c, 1))
            if wrap[n]:
                o.append(txt_wrap(x + colw / 2, y + 10, n, colw - 12, size, INK, "middle", 500, max_lines=2))
            else:
                o.append(txt(x + colw / 2, y + h / 2 + size * 0.36, fit(n, colw - 12, size), size, INK, "middle", 500))
            o.append(title(n))
    return svg("".join(o), w, H)


def schema_dag(w=760):
    """The tables as a left-to-right graph, read off the relationships.

    Many-side-only tables (facts) in the left column, tables that sit on both sides
    (bridges) in the middle, one-side-only tables (dimensions) on the right, every one
    with its full name. An edge runs from the many side to the one side. Inactive
    relationships are dashed, both-directions ones are amber, so the three facts a
    modeller asks of a relationship are on the picture.
    """
    if not REL:
        return empty_note()
    many = {r["fromTable"] for r in REL if r["fromCardinality"] == "many"}
    one = {r["toTable"] for r in REL if r["toCardinality"] == "one"} | \
          {r["fromTable"] for r in REL if r["fromCardinality"] == "one"} | \
          {r["toTable"] for r in REL if r["toCardinality"] == "many" and r["fromCardinality"] == "many"}
    tables = sorted({r["fromTable"] for r in REL} | {r["toTable"] for r in REL}, key=str.lower)
    facts = [t for t in tables if t in many and t not in one]
    bridges = [t for t in tables if t in many and t in one]
    dims = [t for t in tables if t not in many]
    role = {**{n: acc(0) for n in facts}, **{n: acc(3) for n in bridges}, **{n: acc(6) for n in dims}}

    def style(r):
        bidi = r["crossFilteringBehavior"] == "bothDirections"
        tip = "%s: %s[%s] \u2192 %s[%s]%s%s" % (
            r["name"], r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"],
            "" if r["isActive"] else ", inactive", ", both directions" if bidi else "")
        return (WARN if bidi else LINE, 1.8 if bidi else 1.1, None if r["isActive"] else "4 3", tip)

    edges = [(r["fromTable"], r["toTable"], r) for r in REL]
    return lr_dag([facts, bridges, dims], edges, w=w, colour=lambda n: role[n], edge_style=style) + legend(
        [("fact (many side only)", acc(0)), ("bridge (both sides)", acc(3)),
         ("dimension (one side)", acc(6)), ("both directions", WARN)]
    ) + '<p class="dcv-legend">dashed = inactive \u00b7 a line runs from the many side to the one side</p>'


def t_relationships():
    """What the relationships file says, and the two cross-checks against the DAX."""
    if not REL:
        return card(
            '<p class="dc-caption">No relationship was handed over. In a PBIP they live in '
            "<code>definition/relationships.tmdl</code>; a single-file export carries them "
            "inline. Without them nothing here can show cardinality or filter direction, and "
            "the disconnected-table rule stays silent rather than flagging every table.</p>",
            "T7  Relationships", "none in the handover")
    st = RSTATS or {}
    has_flags = any(r.get("relyOnReferentialIntegrity") or r.get("joinOnDateBehavior")
                    or r.get("securityFilteringBehavior") == "bothDirections" for r in REL)
    named = any(not _GUID_RE.match(r["name"] or "") for r in REL)
    rows = []
    for r in REL:
        card_ = "%s → %s" % (r["fromCardinality"], r["toCardinality"])
        rows.append([
            colref(r["fromTable"], r["fromColumn"]),
            colref(r["toTable"], r["toColumn"]),
            E(card_),
            chip("both", "warn") if r["crossFilteringBehavior"] == "bothDirections"
            else chip("single", ""),
            chip("active", "ok") if r["isActive"] else chip("inactive", "note"),
        ] + ([" ".join(x for x in [
                chip("RI", "note") if r.get("relyOnReferentialIntegrity") else "",
                chip(r["joinOnDateBehavior"], "") if r.get("joinOnDateBehavior") else "",
                chip("security both", "warn") if r.get("securityFilteringBehavior") == "bothDirections" else "",
            ] if x) or "—"] if has_flags else [])
          + (['<span class="dcv-ex dcv-nowrap">%s</span>' % E(fit(r["name"], 220, FS_AXIS))] if named else []))
    disconnected = st.get("tablesDisconnected") or []
    out = card(
        rail([("Relationships", st.get("total", len(REL)), "%d active" % st.get("active", 0)),
              ("Inactive", st.get("inactive", 0), "need USERELATIONSHIP"),
              ("Both directions", st.get("bidirectional", 0), "filter travels both ways"),
              ("Many-to-many", st.get("manyToMany", 0), "no guaranteed unique side"),
              ("Tables connected", len(st.get("tablesConnected") or []),
               "%d declared, unconnected" % len(disconnected))])
        + schema_dag()
        + table(["From (many)", "To (one)", "Cardinality", "Direction", "Active"]
                + (["Flags"] if has_flags else []) + (["Name"] if named else []), rows, False)
        + ('<p class="dc-caption"><b>Unconnected:</b> %s. Fine for a measure, parameter or '
           "security table; not for a dimension.</p>"
           % ", ".join("<code>%s</code>" % E(t) for t in disconnected) if disconnected else "")
        + how("Read as declared, with TMDL's defaults filled in where the file is silent. "
              "Not knowable here: the route a filter takes at query time. Inactive ones are "
              "cross-checked against every <code>USERELATIONSHIP</code> in the DAX."),
        "T7  Relationships",
        "%d relationships over %d tables" % (len(REL), len(st.get("tablesConnected") or [])))
    return out


def t_security():
    """Security and the advanced metadata, as inventory. Listed, not judged, by the
    reviewer's own advice: inventory and declared dependencies first, rules later."""
    blocks = []

    # The model block.
    if MODEL and MODEL.get("present"):
        facts = [
            ("Compatibility level", MODEL.get("compatibilityLevel")),
            ("Culture", MODEL.get("culture")),
            ("Default mode", MODEL.get("defaultMode")),
            ("Discourage implicit measures", "yes" if MODEL.get("discourageImplicitMeasures") else "no"),
            ("Data source version", MODEL.get("defaultPowerBIDataSourceVersion")),
        ]
        refs = MODEL.get("refs") or {}
        blocks.append(
            '<p class="dc-eyebrow">Model block</p>'
            + table(["Setting", "Value"], [[E(k), "<code>%s</code>" % E(str(v if v is not None else "—"))]
                                            for k, v in facts], False)
            + ('<p class="dc-caption">model.tmdl lists %s.</p>'
               % ", ".join("%d %s%s" % (n, k, "" if n == 1 else "s") for k, n in sorted(refs.items()))
               if refs else ""))
    else:
        blocks.append('<p class="dc-eyebrow">Model block</p><p class="dc-caption">No '
                      "<code>model.tmdl</code> or <code>database.tmdl</code> in the handover, so "
                      "compatibility level, culture and <code>discourageImplicitMeasures</code> are "
                      "unknown and the implicit-measure rule assumes they are allowed.</p>")

    # Roles.
    if ROLES:
        rows = []
        for r in ROLES:
            rls = [p for p in r["tablePermissions"] if (p.get("filterExpression") or "").strip()]
            ols = [(p["table"], c) for p in r["tablePermissions"]
                   for c in (p.get("columnPermissions") or {})]
            rows.append([
                "<b>%s</b>" % E(r["name"]),
                "<code>%s</code>" % E(r.get("modelPermission") or "—"),
                ("<br>".join("%s: <code class='dcv-ex'>%s</code>"
                             % (E(p["table"]), E(clip(p["filterExpression"].replace("\n", " "), 70)))
                             for p in rls) or '<span class="dcv-none">no row filter</span>'),
                (", ".join(colref(t, c) for t, c in ols) or '<span class="dcv-none">none</span>'),
            ])
        blocks.append('<p class="dc-eyebrow">Roles (RLS and OLS)</p>'
                      + table(["Role", "Permission", "Row filters", "Column permissions"], rows, False))
    else:
        blocks.append('<p class="dc-eyebrow">Roles</p><p class="dc-caption">None declared.</p>')

    # Calculation groups.
    if CGS:
        rows = []
        for g in CGS:
            for item in g["items"]:
                rows.append([
                    "<b>%s</b>" % E(g["table"]),
                    str(g.get("precedence") if g.get("precedence") is not None else "—"),
                    E(item["name"]),
                    str(item.get("ordinal") if item.get("ordinal") is not None else "—"),
                    "<code class='dcv-ex'>%s</code>" % E(clip((item.get("expression") or "").replace("\n", " "), 72)),
                    chip("dynamic format", "note") if (item.get("formatStringDefinition") or "").strip() else "—",
                ])
        blocks.append('<p class="dc-eyebrow">Calculation groups</p>'
                      + table(["Group", "Precedence", "Item", "Ordinal", "DAX", "Format"], rows, False)
                      + '<p class="dc-caption">An item that uses time intelligence is counted as one '
                      "more spelling by the <code>time-intel-mixed</code> rule, which is how a "
                      "hand-written PY beside a PY item is reported together.</p>")
    else:
        blocks.append('<p class="dc-eyebrow">Calculation groups</p><p class="dc-caption">None declared.</p>')

    # Dynamic format strings, KPIs, detail rows.
    dyn = [m for m in MEAS if m.get("hasDynamicFormat")]
    kpi = [m for m in MEAS if m.get("hasKpi")]
    detail = [m for m in MEAS if m.get("hasDetailRows")]
    if dyn or kpi or detail:
        rows = ([[E(m["name"]), E(m["folder"]), "dynamic format string"] for m in dyn]
                + [[E(m["name"]), E(m["folder"]), "KPI"] for m in kpi]
                + [[E(m["name"]), E(m["folder"]), "detail rows expression"] for m in detail])
        blocks.append('<p class="dc-eyebrow">Measures with extra definitions</p>'
                      + table(["Measure", "Folder", "Carries"], rows, False)
                      + '<p class="dc-caption">The bodies are not read; that they exist is the fact. '
                      "A dynamic format string means the Format card's word for this measure is "
                      "the fallback, not what a visual shows.</p>")
    else:
        blocks.append('<p class="dc-eyebrow">Dynamic format strings, KPIs, detail rows</p>'
                      '<p class="dc-caption">None declared.</p>')

    # Hierarchies.
    if HIER:
        rows = [[E(h["table"]), E(h["name"]),
                 " › ".join(E(l["name"]) for l in h["levels"]),
                 chip("hidden", "") if h.get("isHidden") else ""] for h in HIER]
        blocks.append('<p class="dc-eyebrow">Hierarchies</p>'
                      + table(["Table", "Hierarchy", "Levels", ""], rows, False))
    else:
        blocks.append('<p class="dc-eyebrow">Hierarchies</p><p class="dc-caption">None declared.</p>')

    # Refresh policies.
    if RPOL:
        rows = [[E(p["table"]), E(p.get("policyType") or "—"),
                 "%s %s" % (p.get("rollingWindowPeriods") or "?", p.get("rollingWindowGranularity") or ""),
                 "%s %s" % (p.get("incrementalPeriods") or "?", p.get("incrementalGranularity") or ""),
                 "yes" if p.get("hasSourceExpression") else "no"] for p in RPOL]
        blocks.append('<p class="dc-eyebrow">Incremental refresh policies</p>'
                      + table(["Table", "Type", "Rolling window", "Incremental", "Source expression"],
                              rows, False))
    else:
        blocks.append('<p class="dc-eyebrow">Incremental refresh policies</p>'
                      '<p class="dc-caption">None declared.</p>')

    # Perspectives and shared expressions.
    if PERSP:
        rows = [[E(p["name"]), str(len(p["tables"])), str(p["columns"]), str(p["measures"]),
                 str(p["hierarchies"])] for p in PERSP]
        blocks.append('<p class="dc-eyebrow">Perspectives</p>'
                      + table(["Perspective", "Tables", "Columns", "Measures", "Hierarchies"], rows))
    if EXPRS:
        rows = [[E(e["name"]), E(e["kind"]), E(e.get("queryGroup") or "—"), str(e.get("lines") or "")]
                for e in EXPRS]
        blocks.append('<p class="dc-eyebrow">Shared expressions</p>'
                      + table(["Expression", "Kind", "Query group", "Lines of M"], rows)
                      + '<p class="dc-caption">The M itself belongs to the Power Query segment, '
                      "which reads these as queries. Here they are listed as objects a partition "
                      "can depend on.</p>")

    n = len(ROLES) + len(CGS) + len(HIER) + len(RPOL) + len(PERSP) + len(EXPRS) + len(dyn)
    return card("".join(blocks),
                "T8  Security and advanced metadata",
                "%d objects %s inventory, not verdicts" % (n, DASH_MID))


def t_audit():
    calc_c = [c for c in COLS if c["isCalculated"]]
    t10 = card(table(["Column", "Table", "Type", "DAX"],
                      [[E(c["name"]), E(c["table"]), "<code>%s</code>" % E(c["dataType"] or "—"),
                        "<code class='dcv-ex'>%s</code>"
                        % E(clip(c["expression"].replace("\n", " "), 60))] for c in calc_c],
                      False) if calc_c else empty_note(),
                "T9  Calculated columns", "%d — listed, not condemned" % len(calc_c))

    t11 = card(scorecard(t_tmdl_checks()), "T10  Semantic-model scorecard",
                "8 checks — a threshold is a house convention, not a rule of DAX")
    return t10 + t11

DAX_KW = {"VAR", "RETURN", "EVALUATE", "DEFINE", "MEASURE", "ORDER", "BY", "ASC", "DESC",
          "TRUE", "FALSE", "NOT", "IN", "AND", "OR"}


def dax_html(src):
    out = []
    for tok in re.split(r'("(?:[^"]|"")*"|//[^\n]*|--[^\n]*)', src):
        if not tok:
            continue
        if tok.startswith('"'):
            out.append('<i class="s">%s</i>' % E(tok))
        elif tok.startswith("//") or tok.startswith("--"):
            out.append('<i class="c">%s</i>' % E(tok))
        else:
            t = E(tok)
            t = re.sub(r"(&#x27;[^&]*?&#x27;)?\[[^\]]+\]",
                       lambda m: '<i class="r">%s</i>' % m.group(0), t)
            t = re.sub(r"\b([A-Z][A-Z0-9._]{1,})\b(?=\s*\()",
                       lambda m: '<i class="f">%s</i>' % m.group(1), t)
            t = re.sub(r"\b(%s)\b" % "|".join(DAX_KW),
                       lambda m: '<i class="k">%s</i>' % m.group(1), t)
            t = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])",
                       lambda m: '<i class="n">%s</i>' % m.group(1), t)
            out.append(t)
    return "".join(out)


#: Every measure gets a pre-rendered anatomy block up to this many; a model beyond it
#: gets its heaviest ones here and the rest through the chain explorer. 1,000 blocks
#: would double the page for a model nobody reads measure by measure.
ANATOMY_CAP = 400


def anatomy_block(m):
    """One measure taken apart: gauges, formatted DAX, VAR bindings, functions, and the
    columns and tables its chain stops at. Nothing here is a verdict."""
    a = an(m)
    chips = "".join(chip("%s \u00d7%d" % (f["name"], f["count"])) for f in a["functions"])
    varlist = "".join('<li><code>%s</code><span>%s</span></li>'
                      % (E(v["name"]), E(clip(v["expression"], 54))) for v in a["vars"]) \
        or "<li><span>none</span></li>"
    gauges = "".join('<div class="dcv-gauge"><b class="dc-num">%s</b><span>%s</span></div>'
                     % (v, E(k)) for k, v in
                     [("weight", a["weight"]), ("lines", a["lines"]), ("calls", a["calls"]),
                      ("nesting", a["depth"]), ("chain", a["chain"]),
                      ("reads", len(a["measureRefs"])), ("read by", len(m["usedBy"]))])
    stops = (a["columnRefs"] + a["tableRefs"])[:10]
    head = ('<p class="dcv-anat-head"><b>%s</b> <span>%s%s</span></p>'
            % (E(m["name"]), E(m["folder"] or "no folder"),
               (" \u00b7 " + E(clip(m["description"], 160))) if m.get("description") else ""))
    return (head + '<div class="dcv-anatomy">%s</div>' % gauges
            + '<div class="dcv-split"><div><p class="dc-eyebrow">Formatted DAX</p>'
            '<pre class="dcv-code">%s</pre></div>'
            '<div><p class="dc-eyebrow">VAR bindings</p><ul class="dcv-vars">%s</ul>'
            '<p class="dc-eyebrow">Functions</p><div class="dcv-chips">%s</div>'
            '<p class="dc-eyebrow">Stops at</p><div class="dcv-chips">%s</div></div></div>'
            % (dax_html(a["formatted"]), varlist, chips,
               "".join(chip(c) for c in stops)
               or "<span class='dcv-none'>nothing \u2014 it reads only other measures</span>"))


def t_detail():
    if not MEAS:
        return card('<p class="dc-caption">This model declares no measure, so there is no '
                    'DAX to open. A calculation group or a bare dimension lands here.</p>',
                    "T14  Measure anatomy", "nothing to open")
    # The heaviest by syntax opens first; the picker reaches every other one. The first
    # version printed the heaviest alone, which told a reader about one measure and
    # nothing about the choice -- a detail pane that cannot be aimed is a screenshot.
    by_weight = sorted(MEAS, key=lambda m: (-an(m).get("weight", 0), m["name"].lower()))
    shown = by_weight[:ANATOMY_CAP]
    shown_names = {m["name"] for m in shown}
    first = by_weight[0]["name"]
    # The rail is the chain explorer's: a filter box, the model's folders as disclosure
    # groups, one button per measure with its read-by count. Same classes, so it is the
    # same gesture in both places; folders take the same accent order the chain gives them.
    groups = {}
    for m in MEAS:
        groups.setdefault(m["folder"] or "Other", []).append(m)
    folders = sorted(groups, key=lambda f: (f == "Other", f.lower()))
    rail = []
    for fi, folder in enumerate(folders):
        dot = "var(--a0-dot)" if folder == "Other" else acc(fi)
        ms = sorted(groups[folder], key=lambda m: m["name"].lower())
        holds = any(m["name"] == first for m in ms)
        items = []
        for m in ms:
            ok = m["name"] in shown_names
            items.append(
                '<li><button type="button" class="dcvc-rail-item%s" data-anat-pick="%s" title="%s"%s>'
                '<span class="dcvc-rail-dot" style="background:%s"></span>'
                '<span class="dcvc-rail-name">%s</span>%s</button></li>'
                % (" on" if m["name"] == first else "", E(m["name"]),
                   E(m["name"] if ok else m["name"] + " \u2014 open it from the chain explorer"),
                   "" if ok else " disabled", dot, E(m["name"]),
                   ('<span class="dcvc-rail-n">%d</span>' % len(m["usedBy"])) if m["usedBy"] else ""))
        rail.append(
            '<details class="dcvc-rail-group" data-anat-group%s><summary>'
            '<span class="dcvc-rail-dot" style="background:%s"></span>'
            '<span class="dcvc-rail-folder">%s</span><span class="dcvc-rail-n">%d</span></summary>'
            '<ul class="dcvc-rail-list">%s</ul></details>'
            % (" open" if holds else "", dot, E(folder), len(ms), "".join(items)))
    side = ('<div class="dcvc-side"><div class="dcvc-side-head">'
            '<input type="search" class="dcvc-filter" placeholder="Filter measures\u2026" '
            'autocomplete="off" spellcheck="false" data-anat-filter></div>'
            '<div class="dcvc-rail" data-anat-rail>%s</div></div>' % "".join(rail))
    blocks = "".join('<div class="dcvc-anat" data-anat="%s"%s>%s</div>'
                     % (E(m["name"]), "" if m["name"] == first else " hidden", anatomy_block(m))
                     for m in shown)
    picker = ('<div class="dcvc dcv-anat-wrap" id="dcv-anat-root"><div class="dcvc-stage">%s'
              '<div class="dcvc-main" id="dcv-anat">%s</div></div></div>'
              % (side, blocks))
    script = (
        '<script>(function(){var root=document.getElementById("dcv-anat-root");if(!root)return;'
        'var box=document.getElementById("dcv-anat");'
        'function pick(name){root.querySelectorAll(".dcvc-rail-item").forEach(function(b){'
        'b.classList.toggle("on",b.getAttribute("data-anat-pick")===name);});'
        'box.querySelectorAll(".dcvc-anat").forEach(function(b){b.hidden=b.getAttribute("data-anat")!==name;});}'
        'root.addEventListener("click",function(e){var b=e.target.closest(".dcvc-rail-item");'
        'if(b&&!b.disabled)pick(b.getAttribute("data-anat-pick"));});'
        'var f=root.querySelector("[data-anat-filter]");'
        'f.addEventListener("input",function(){var q=f.value.toLowerCase();'
        'root.querySelectorAll("[data-anat-group]").forEach(function(g){var any=false;'
        'g.querySelectorAll("li").forEach(function(li){var n=li.querySelector(".dcvc-rail-name").textContent.toLowerCase();'
        'var hit=!q||n.indexOf(q)!==-1;li.hidden=!hit;if(hit)any=true;});'
        'g.hidden=!any;if(q)g.open=true;else g.open=!!g.querySelector(".dcvc-rail-item.on");});});'
        '})();</script>')
    note = ('<p class="dcv-ex dcv-anat-note">%d measures, heaviest by syntax first%s.</p>'
            % (len(MEAS), "" if len(shown) == len(MEAS)
               else "; the %d heaviest open here, the rest from the chain explorer" % len(shown)))
    t17 = card(note + picker + script
               + how("<b>weight</b> ranks within this model only: calls + lines + 2\u00d7nesting "
                     "+ 2\u00d7VARs + chain. <b>Stops at</b> is the columns and tables the measure "
                     "reads directly. The heaviest by syntax opens first."),
               "T14  DAX anatomy, any measure", "%d measures" % len(MEAS))

    rows = []
    for t in TROWS:
        parts = " · ".join("%s / %s" % (p.get("kind"), p.get("mode") or "—")
                           for p in (t.get("partitions") or [])) or "—"
        flags = ""
        if t.get("isCalculationGroup"):
            flags += " " + chip("calculation group", "note")
        elif t.get("isCalculated"):
            flags += " " + chip("calculated", "note")
        if t.get("hierarchies"):
            flags += " " + chip("%d hierarch%s" % (len(t["hierarchies"]),
                                                  "y" if len(t["hierarchies"]) == 1 else "ies"), "")
        if t.get("refreshPolicy"):
            flags += " " + chip("incremental", "")
        rows.append(["<b>%s</b>%s" % (E(t["name"]), flags),
                     '<span class="dc-num">%s</span>' % t.get("measures", 0),
                     '<span class="dc-num">%s</span>' % t.get("columns", 0),
                     '<span class="dc-num">%s</span>' % t.get("hiddenColumns", 0),
                     "<code>%s</code>" % E(parts)])
    t18 = card(table(["Table", "Measures", "Columns", "Hidden columns", "Partitions"], rows),
               "T15  Table cards", "%d tables" % len(TROWS))
    return t17 + t18


def build_part_tmdl():
    hidden = sum(1 for m in MEAS if m["isHidden"])
    vitals = rail([
        ("Measures", len(MEAS), "%d hidden" % hidden),
        ("Tables", len(TABS), "%d calculated" % sum(1 for t in TABS if t["isCalculated"])),
        ("Columns", len(COLS), "%d calculated" % sum(1 for c in COLS if c["isCalculated"])),
        ("Relationships", len(REL), "%d inactive" % (RSTATS or {}).get("inactive", 0)),
        ("Display folders", len(TD["folders"]),
         "deepest %d" % max((len(m["folderPath"]) for m in MEAS), default=0)),
        ("Measure links", TD["linkCount"], "%d unreferenced" % len(UNREFERENCED)),
        ("DAX functions", len({f["name"] for m in MEAS for f in an(m).get("functions", [])}),
         "distinct"),
    ])
    part("part-t", "Part T — The semantic model (TMDL)",
         "{n} readings. Input is TMDL: one file, a pasted table, or a whole PBIP "
         "definition folder.",
         [("t-findings", "Findings",
           "What to look at, most severe first", t_findings()),
          ("t-structural", "Impact",
           "What breaks if I change this", t_structural() + t_blast()),
          ("t-meta", "Metadata",
           "What a visual shows, and what the model says about itself", t_across()),
          ("t-load", "Load",
           "How each table gets its rows", t_bridge()),
          ("t-rel", "Relationships",
           "How the tables join, as declared", t_relationships()),
          ("t-sec", "Security",
           "Roles, calculation groups, hierarchies, refresh, the model block", t_security()),
          ("t-audit", "Audits", "Listed, never condemned", t_audit()),
          ("t-orient", "Orientation",
           "The shape of the model, for the first ten minutes", t_orientation()),
          ("t-detail", "Detail", "One measure in full, any measure",
           t_detail())],
         lede=vitals)


# ============================================ PART Q — Power Query (M) ======

SHAPE_TONE = {
    "source": "var(--a3-dot)", "filterRow": "var(--a7-dot)", "addColumn": "var(--a1-dot)",
    "removeColumn": "var(--a6-dot)", "renameColumn": "var(--a4-dot)",
    "retypeColumn": "var(--a2-dot)", "join": "var(--a5-dot)", "group": "var(--a8-dot)",
    "expand": "var(--a4-dot)", "pivot": "var(--a5-dot)", "append": "var(--a8-dot)",
    "sortRow": "var(--a7-dot)", "selectColumn": "var(--a2-dot)", "shape": "var(--a0-dot)",
    "reference": "var(--a3-dot)", "other": "var(--a0-dot)",
}
DELTA_TONE = {"added": POS, "removed": NEG, "renamed": "var(--a4-dot)", "retyped": INFO,
              "kept": NEUTRAL % "dot"}


def step_ribbon(q, w=760):
    steps = q["steps"]
    if not steps:
        return empty_note()
    h, pad = 116, 12
    cw = (w - pad * 2) / len(steps)
    o = []
    for i, s in enumerate(steps):
        x = pad + i * cw
        col = SHAPE_TONE.get(s["shape"], NEUTRAL % "dot")
        o.append(rect(x + 2, 26, cw - 6, 30, col, 3, 0.30 if s["dead"] else 1))
        if s["dead"]:
            o.append(rect(x + 2, 26, cw - 6, 30, "none", 3, 1, NEG, 1.4))
        o.append(txt(x + cw / 2, 45, str(i), FS_VALUE, "var(--color-card)", "middle", 700))
        o.append("<g>%s%s</g>" % (
            title("%s — %s%s%s" % (s["name"], s["label"], "  (dead)" if s["dead"] else "",
                                   "  (potential folding barrier)" if s["name"] == q["foldBreakStep"] else "")),
            txt_wrap(x + cw / 2, 70, s["label"], cw - 8, FS_AXIS, MUTED, "middle",
                     max_lines=2)))
        for rd in s["reads"]:
            j = next((k for k, t in enumerate(steps) if t["name"] == rd), None)
            if j is None or j == i - 1:
                continue
            ax, bx = pad + j * cw + cw / 2, x + cw / 2
            o.append(path("M %.1f 24 C %.1f 2 %.1f 2 %.1f 24" % (ax, ax, bx, bx), WARN, "none", 1.4, 0.9))
        if s["name"] == q["foldBreakStep"]:
            o.append(line(x, 20, x, 62, WARN, 2))
            o.append(txt(x + 3, 16, "barrier?", FS_AXIS, WARN, "start", 600))
    o.append(txt(pad, h - 6, "%s  →  %s" % (steps[0]["name"], q["resultStep"] or steps[-1]["name"]),
                 10, MUTED))
    o.append(txt(w - pad, h - 6, "arc above = reads a step that is not the one before it",
                 FS_AXIS, MUTED, "end"))
    return svg("".join(o), w, h) + legend(
        [(k, v) for k, v in SHAPE_TONE.items() if any(s["shape"] == k for s in steps)]
        + [("dead step", NEG)])


def column_lifecycle(q, w=760):
    steps = q["steps"]
    seen, events = [], collections.defaultdict(dict)
    for s in steps:
        for kind in ("added", "removed", "renamed", "retyped", "kept"):
            for item in s["columns"].get(kind, []):
                nm = item.get("name") if isinstance(item, dict) else item
                if isinstance(item, dict) and kind == "renamed":
                    nm = item.get("from") or item.get("name")
                if not nm:
                    continue
                if nm not in seen:
                    seen.append(nm)
                events[nm][s["index"]] = kind
    if not seen:
        return empty_note()
    labw, ch = 190, 19
    cw = (w - labw - 12) / len(steps)
    # The columns are over a hundred pixels wide, so the label was rotated for no
    # reason. Two lines, horizontal, and the header grows to whatever they need.
    head = max(wrap_h(s["label"], cw - 10, FS_AXIS, 3) for s in steps) + 14
    H = head + len(seen) * ch + 8
    o = []
    for i, s in enumerate(steps):
        x = labw + i * cw + cw / 2
        o.append(txt_wrap(x, 12, s["label"], cw - 10, FS_AXIS, MUTED, "middle", max_lines=3))
    for r, nm in enumerate(seen):
        y = head + r * ch
        o.append(txt(labw - 8, y + ch - 6, fit(nm, labw - 12), FS_LABEL, INK, "end"))
        prev = None
        for i, s in enumerate(steps):
            x = labw + i * cw
            k = events[nm].get(s["index"])
            o.append(line(x + cw / 2, y + 2, x + cw / 2, y + ch - 2, LINE, 1, "1 3", 0.7))
            if k:
                o.append(rect(x + 3, y + 3, cw - 6, ch - 8, DELTA_TONE[k], 2)
                         + title("%s — %s at %s" % (nm, k, s["label"])))
                if prev is not None:
                    o.append(line(prev, y + ch / 2 - 1, x + 3, y + ch / 2 - 1,
                                  NEUTRAL % "dot", 2, None, 0.6))
                prev = x + cw - 3
    return svg("".join(o), w, H) + legend([(k, v) for k, v in DELTA_TONE.items()])


JOIN_FNS = {"Table.NestedJoin", "Table.Join", "Table.FuzzyNestedJoin",
            "Table.Combine", "Table.FuzzyJoin"}


def q_upstream_sources(name, seen=None):
    """Every connector reachable from a query, through the whole upstream chain."""
    seen = seen or set()
    if name in seen:
        return set()
    seen.add(name)
    q = Q_BY_NAME.get(name)
    if not q:
        return set()
    out = {source_key(x) for x in q["sources"]}
    for dep in q["dependsOn"]:
        out |= q_upstream_sources(dep, seen)
    return out


def source_key(x):
    where = x.get("database") or x.get("target") or ""
    return x["label"] + ((" " + DASH_MID + " " + where) if where else "")


def q_flow():
    """Source → join → table. How each table is assembled, and out of what.

    The middle column is the join steps themselves rather than the queries holding
    them: a query that merges twice is doing two things, and the whole question
    here is which sources meet where.
    """
    tables = [q for q in QRY if q["kind"] == "table"]
    joins = [(q, s) for q in QRY for s in q["steps"] if s["functionName"] in JOIN_FNS]

    src_w = collections.Counter()
    for q in tables:
        for k in q_upstream_sources(q["name"]):
            src_w[k] += 1
    col1 = [("s:" + k, k, v) for k, v in src_w.most_common()]

    col2 = [("j:" + q["name"] + "/" + st["name"],
             st["name"] + "  " + DASH_MID + "  " + q["name"], 1) for q, st in joins]

    col3 = [("t:" + q["name"], q["name"], max(len(q_upstream_sources(q["name"])), 1))
            for q in sorted(tables, key=lambda q: -len(q_upstream_sources(q["name"])))]

    links = []
    joined_tables = {q["name"] for q, _ in joins}
    for q, st in joins:
        for k in q_upstream_sources(q["name"]):
            links.append(("s:" + k, "j:" + q["name"] + "/" + st["name"], 1))
        links.append(("j:" + q["name"] + "/" + st["name"], "t:" + q["name"], 1))
    # A table nobody joins into is loaded straight from its source, so it skips the
    # middle column rather than being given a join it does not have.
    for q in tables:
        if q["name"] in joined_tables:
            continue
        for k in q_upstream_sources(q["name"]):
            links.append(("s:" + k, "t:" + q["name"], 1))

    return card(
        flow([col1, col2, col3], links, colw=196, avail=320)
        + how("Connector, then join step, then the table it builds. A table with no join skips "
              "the middle. Width on the right is how many connectors feed a table: each one can "
              "break it independently. Hover a ribbon to isolate it."),
        "Q5  Source to table — how the joins assemble each table",
        "%d connectors, %d join steps, %d tables" % (len(col1), len(col2), len(col3)))


def q_structural():
    qlayers = [[q["name"] for q in QRY if q["layer"] == l] for l in MD["layers"]]
    qedges = [(d, q["name"]) for q in QRY for d in q["dependsOn"]]
    kinds = sorted({q["sourceKind"] or "derived" for q in QRY})
    skind = {k: acc(i) for i, k in enumerate(kinds)}
    q1 = card(
        graph_block("dcvg-m", "dcvg-m-data", m_graph_payload(), {
            "kind": "m",
            "searchPlaceholder": "Search queries, steps, native SQL\u2026",
            "codeLabel": "Native query, decoded", "listLabel": "Steps",
            "upLabel": "Depends on", "downLabel": "Used by",
            "upEmpty": "A source query \u2014 it reads no other query.",
            "downEmpty": "Nothing else references this query.",
        })
        + how("Sources at the top, dependency layers below. A query opens to its steps, "
              "connector and references; dead steps are struck through. <b>Focus</b> narrows to "
              "one query and its neighbours; search reaches step names and decoded native SQL."),
        "Q1  Query lineage — interactive",
        "%d queries, %d links, %d layers" % (len(QRY), MD["linkCount"], len(MD["layers"])))
    q2 = card(lr_dag(qlayers, qedges, colour=lambda n: skind[Q_BY_NAME[n]["sourceKind"] or "derived"],
                     edge_style=lambda m: (LINE, 1.1, None, None))
              + legend([(k, skind[k]) for k in kinds])
              + '<p class="dc-caption">Q1 as a static diagram, for a handover document.</p>',
              "Q2  Query lineage \u2014 layers as columns, printable",
              "%d queries over %d layers" % (len(QRY), len(MD["layers"])))

    q = max(QRY, key=lambda q: len(q["steps"]))
    if q["steps"]:
        q3 = card(step_ribbon(q), "Q3  Step chain — %s" % q["name"],
                  "%d steps, %d dead, %s"
                  % (len(q["steps"]), sum(1 for s in q["steps"] if s["dead"]),
                     ("potential folding barrier at %s" % q["foldBreakStep"]) if q["foldBreakStep"]
                     else "no folding barrier detected"))
    else:
        q3 = card('<p class="dc-caption">No query here has a step: every one is a bare '
                  'expression or a parameter.</p>', "Q3  Step chain", "nothing to walk")
    q4 = card(column_lifecycle(q)
              + how("A grid, not a sankey: the parser reports what each step states about "
                    "columns, not the full column set, so a sankey would have to invent widths."),
              "Q4  Column lifecycle — %s" % q["name"],
              "what each step does to each column")
    return q1 + q2 + q3 + q4 + q_flow()


# Syntax highlighting only. The *facts* about a statement -- joins, WHERE, row cap --
# come from the dataset, where `findings.sql_facts` computed them once. A second scan
# here, with a slightly different pattern, was the one place the page could disagree
# with report.json, and an outside review caught it doing exactly that on FETCH FIRST.
SQL_KW = (
    "SELECT|FROM|WHERE|INNER|LEFT|RIGHT|FULL|OUTER|CROSS|JOIN|ON|GROUP|ORDER|BY|HAVING|"
    "UNION|ALL|DISTINCT|TOP|AS|AND|OR|NOT|IN|EXISTS|BETWEEN|LIKE|IS|NULL|CASE|WHEN|THEN|"
    "ELSE|END|WITH|OVER|PARTITION|ASC|DESC|LIMIT|OFFSET|FETCH|FIRST|NEXT|ROWS|ONLY|CAST|"
    "CONVERT|COALESCE|ISNULL|SUM|COUNT|AVG|MIN|MAX|DATEADD|GETDATE"
)
SQL_LIT_RE = re.compile(r"('(?:[^']|'')*'|--[^\n]*|/\*.*?\*/)", re.S)
SQL_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")


def sql_facts_for(query_name, step):
    """The dataset's facts for one native query, by query and step."""
    for row in MD.get("sql", []):
        if row["query"] == query_name and row.get("step") == step:
            return row["facts"]
    return None


def sql_html(src):
    out = []
    for tok in SQL_LIT_RE.split(src):
        if not tok:
            continue
        if tok[0] == "'":
            out.append('<i class="s">%s</i>' % E(tok))
        elif tok.startswith("--") or tok.startswith("/*"):
            out.append('<i class="c">%s</i>' % E(tok))
        else:
            t = E(tok)
            t = re.sub(r"\b(%s)\b" % SQL_KW,
                       lambda m: '<i class="k">%s</i>' % m.group(1), t, flags=re.I)
            t = SQL_NUM_RE.sub(lambda m: '<i class="n">%s</i>' % m.group(1), t)
            out.append(t)
    return "".join(out)


def sql_sources():
    """Every query that pushes a native query at its source, most SQL first."""
    return sorted([(q, src) for q in QRY for src in q["sources"] if src.get("nativeQuery")],
                  key=lambda r: -len(r[1]["nativeQuery"].splitlines()))


def q_native_sql():
    """What runs at the source, before Power Query sees a row.

    Its own tier, and before the distributions, because it is upstream of everything
    else in the part: a query with a WHERE at the source is a different object from one
    that pulls a whole table and filters in M, and no step count says so.
    """
    found = sql_sources()
    if not found:
        return card(empty_note(), "Q6  Native SQL", "no query pushes a native query")

    facts = [(q, src, sql_facts_for(q["name"], src.get("step")) or findings_mod.sql_facts(src["nativeQuery"]))
             for q, src in found]
    rows = []
    for q, src, f in facts:
        pushed = " ".join(x for x in [
            chip("WHERE", "ok") if f["where"] else "",
            chip("GROUP BY", "ok") if f["group"] else "",
            chip("%d JOIN" % f["joins"], "note") if f["joins"] else "",
            chip("ORDER BY", "note") if f["order"] else "",
            chip("row cap", "note") if f["cap"] else "",
        ] if x) or '<span class="dcv-none">nothing but a projection</span>'
        rows.append([
            E(q["name"]),
            '<span class="dcv-ex">%s</span>'
            % E(src["label"] + ((" " + DASH_MID + " " + src["database"])
                               if src.get("database") else "")),
            str(f["lines"]),
            chip("SELECT *", "bad") if f["cols"] == "*" else str(f["cols"] or ""),
            pushed,
            '<span class="dcv-ex">%s</span>' % E(", ".join(f["tables"])),
        ])

    n_star = sum(1 for _, _, f in facts if f["cols"] == "*")
    n_filter = sum(1 for _, _, f in facts if f["where"])
    total_lines = sum(f["lines"] for _, _, f in facts)

    overview = card(
        rail([("Queries with SQL", len(found), "of %d queries" % len(QRY)),
              ("Filtered at source", n_filter, "carry a WHERE"),
              ("SELECT *", n_star, "column list not stated"),
              ("SQL lines", total_lines, "run before M does")])
        + pair(ranked_bar([(q["name"], f["lines"]) for q, _, f in facts],
                          labw=170, barw=220, unit=" lines",
                          colour=lambda i, l: acc(3)),
               gtable(["Query", "Connector", "Lines", "Cols", "Pushed to the source",
                       "Tables"], rows, ["l", "l", "r", "r", "l", "l"]))
        + how("<code>WHERE</code> or <code>GROUP BY</code> at the source is work done on the "
              "server. A bare projection hands the whole table over. <code>SELECT *</code> takes "
              "whatever columns the source has on refresh day.",
              "A lexical scan of the decoded SQL, not a parse: a keyword inside a comment or a "
              "string literal is counted."),
        "Q6  Which queries push work to the source?",
        "%d of %d queries carry native SQL" % (len(found), len(QRY)))

    blocks = []
    for q, src, f in facts:
        blocks.append(
            '<div class="dcv-sql"><div class="dcv-sql-h"><b>%s</b>'
            '<span class="dcv-sql-m">%s</span>'
            '<span class="dcv-sql-m">step <code>%s</code>%s</span>'
            '<span class="dcv-sql-n">%d %s</span></div>'
            '<pre class="dcv-code">%s</pre></div>'
            % (E(q["name"]),
               E(src["label"] + ((" " + DASH_MID + " " + src["database"])
                                 if src.get("database") else "")),
               E(src["step"]),
               E(" " + DASH_MID + " line %d" % src["line"]) if src.get("line") else "",
               f["lines"], "lines" if f["lines"] != 1 else "line",
               sql_html(src["nativeQuery"])))
    decoded = card(
        "".join(blocks)
        + '<p class="dc-caption">In the M each of these is one string literal with every '
        "newline written <code>#(lf)</code>. Recovering it is the single biggest thing the "
        "parser does that the Power Query Editor does not " + DASH_EM + " the Editor shows the "
        "literal.</p>",
        "Q7  The SQL, decoded",
        "%d %s %s %d lines" % (len(found), "queries" if len(found) != 1 else "query",
                               DASH_MID, total_lines))
    return overview + decoded


def q_distribution():
    """Each one a chart-and-table pair on its own row.

    The chart carries the shape, the table carries the exact numbers and the names the
    chart had to truncate. Both are built from one list, so they cannot disagree.
    """
    steps_sorted = sorted(QRY, key=lambda q: (-len(q["steps"]), q["name"].lower()))
    q8 = card(pair(
        ranked_bar([(q["name"], len(q["steps"])) for q in steps_sorted],
                   labw=170, barw=250, colour=lambda i, l: acc(4)),
        gtable(["Query", "Kind", "Steps", "Dead", "Layer"],
               [[E(q["name"]), '<span class="dcv-ex">%s</span>' % E(q["kind"]),
                 str(len(q["steps"])),
                 str(sum(1 for st in q["steps"] if st["dead"]) or ""),
                 str(q["layer"])] for q in steps_sorted],
               ["l", "l", "r", "r", "r"]),
        '<p class="dc-caption">More steps is not more complex: eight named steps read better '
        "than three that each do three things.</p>"),
        "Q8  Steps per query", "%d steps over %d queries" % (MD["stepCount"], len(QRY)))

    ops = collections.Counter(s["functionName"] for q in QRY for s in q["steps"]
                              if s["functionName"])
    op_q = collections.defaultdict(set)
    for q in QRY:
        for st in q["steps"]:
            if st["functionName"]:
                op_q[st["functionName"]].add(q["name"])
    q9 = card(pair(
        ranked_bar(ops.most_common(12), labw=210, barw=220, colour=lambda i, l: acc(1)),
        gtable(["Library call", "Calls", "Queries", "Where"],
               [["<code>%s</code>" % E(k), str(v), str(len(op_q[k])),
                 '<span class="dcv-ex">%s</span>' % E(clip(", ".join(sorted(op_q[k])), 34))]
                for k, v in ops.most_common()], ["l", "r", "r", "l"]),
        '<p class="dc-caption">Top twelve by calls; the table is all %d.</p>' % len(ops)),
        "Q9  M operation frequency", "%d distinct library calls" % len(ops))

    shp = collections.Counter(s["shape"] for q in QRY for s in q["steps"])
    shp_q = collections.defaultdict(set)
    for q in QRY:
        for st in q["steps"]:
            shp_q[st["shape"]].add(q["name"])
    total_st = sum(shp.values()) or 1
    q10 = card(pair(
        ranked_bar(shp.most_common(), labw=150, barw=240,
                   colour=lambda i, l: SHAPE_TONE.get(l, NEUTRAL % "dot")),
        gtable(["Shape", "Steps", "Share", "Queries"],
               [[dot(SHAPE_TONE.get(k, NEUTRAL % "dot")) + E(k), str(v),
                 "%d%%" % round(100 * v / total_st), str(len(shp_q[k]))]
                for k, v in shp.most_common()], ["l", "r", "r", "r"])),
        "Q10  Step shape mix", "what the model spends its steps doing")

    kc = collections.Counter(q["kind"] for q in QRY)
    kind_q = collections.defaultdict(list)
    for q in QRY:
        kind_q[q["kind"]].append(q["name"])
    q11 = card(pair(
        donut([(k, v, acc(i)) for i, (k, v) in enumerate(kc.most_common())],
              centre=(str(len(QRY)), "queries")),
        gtable(["Kind", "Queries", "Which"],
               [[dot(acc(i)) + E(k), str(v),
                 '<span class="dcv-ex">%s</span>' % E(clip(", ".join(kind_q[k]), 44))]
                for i, (k, v) in enumerate(kc.most_common())], ["l", "r", "l"])),
        "Q11  Query kind census", "table, function, parameter")

    lw = collections.Counter(q["layer"] for q in QRY)
    layer_q = collections.defaultdict(list)
    for q in QRY:
        layer_q[q["layer"]].append(q["name"])
    q12 = card(pair(
        histogram([("L%d" % l, lw[l]) for l in MD["layers"]],
                  colour=lambda i, l: acc(i), xlab="layer", ylab="queries"),
        gtable(["Layer", "Queries", "Which"],
               [["L%d" % l, str(lw[l]),
                 '<span class="dcv-ex">%s</span>' % E(clip(", ".join(layer_q[l]), 44))]
                for l in MD["layers"]], ["l", "r", "l"]),
        '<p class="dc-caption">Layer 0 is what reads a source directly.</p>'),
        "Q12  Layer width", "a wide layer 0 is many independent sources")

    sk = collections.Counter(s["kind"] for q in QRY for s in q["sources"])
    src_q = collections.defaultdict(set)
    for q in QRY:
        for st in q["sources"]:
            src_q[st["kind"]].add(q["name"])
    q13 = card(pair(
        donut([(k, v, acc(i)) for i, (k, v) in enumerate(sk.most_common())],
              centre=(str(sum(sk.values())), "sources")),
        gtable(["Connector", "Sources", "Queries", "Where"],
               [[dot(acc(i)) + E(k), str(v), str(len(src_q[k])),
                 '<span class="dcv-ex">%s</span>' % E(clip(", ".join(sorted(src_q[k])), 34))]
                for i, (k, v) in enumerate(sk.most_common())], ["l", "r", "r", "l"])),
        "Q13  Connector mix", "grouped by what it connects to")
    return q8 + q9 + q10 + q11 + q12 + q13


def q_relational():
    codes = sorted({f["code"] for q in QRY for f in q["findings"]})
    qs = [q["name"] for q in QRY if q["findings"]]
    fmap = collections.Counter((q["name"], f["code"]) for q in QRY for f in q["findings"])
    tmax = max(fmap.values(), default=0) or 1
    q14 = card(heatmap(qs, codes, lambda r, c: (fmap.get((r, c), 0), fmap.get((r, c), 0) / tmax),
                       labw=180, cw=74, colour=WARN, fmt_cell=lambda v: v or ""),
               "Q14  Query × finding",
               "%d findings over %d queries" % (sum(fmap.values()), len(qs)))

    conn = collections.defaultdict(list)
    for q in QRY:
        for s in q["sources"]:
            key = s.get("database") or s.get("target") or s.get("label") or s["kind"]
            conn["%s · %s" % (s["label"], key)].append(q["name"])
    rows = [(k, len(v)) for k, v in sorted(conn.items(), key=lambda kv: -len(kv[1]))]
    q15 = card(ranked_bar(rows, labw=260, barw=300, colour=lambda i, l: acc(3))
               + '<p class="dc-caption">Queries opening the same endpoint separately, which '
               "the Editor does not show.</p>",
               "Q15  Connection reuse", "%d distinct endpoints" % len(conn))
    return grid(q14, q15)


def fold_waterfall(w=760):
    qs = [q for q in QRY if q["steps"]]
    if not qs:
        return ('<p class="dc-caption">No query here has a step, so there is no chain to '
                'walk for a folding barrier.</p>')
    labw, rowh = 170, 24
    barw = w - labw - 90
    top = max(len(q["steps"]) for q in qs)
    H = len(qs) * rowh + 14
    o = []
    for i, q in enumerate(qs):
        y = i * rowh + 6
        steps = q["steps"]
        brk = next((k for k, s in enumerate(steps) if s["name"] == q["foldBreakStep"]), None)
        folded = len(steps) if brk is None else brk
        unit = barw / top
        o.append(txt(labw - 8, y + rowh - 8, fit(q["name"], labw - 12), FS_LABEL, INK, "end"))
        o.append(rect(labw, y + 4, len(steps) * unit, rowh - 11, NEUTRAL % "tint", 2, 0.5))
        if folded:
            o.append(rect(labw, y + 4, folded * unit, rowh - 11, POS, 2, 0.85)
                     + title("%d steps before any barrier is detected" % folded))
        if brk is not None:
            o.append(rect(labw + folded * unit, y + 4, (len(steps) - folded) * unit, rowh - 11,
                          WARN, 2, 0.85)
                     + title("%d steps after the potential barrier at %s"
                             % (len(steps) - folded, q["foldBreakStep"])))
            o.append(line(labw + folded * unit, y + 2, labw + folded * unit, y + rowh - 5, INK, 1.4))
        o.append(num(w - 8, y + rowh - 8, "%d/%d" % (folded, len(steps))))
    return svg("".join(o), w, H) + legend(
        [("no barrier detected", POS), ("after a potential barrier", WARN),
         ("step slots", NEUTRAL % "tint")])


def q_m_checks():
    stepped = [q for q in QRY if q["steps"]]
    return [
        ("Queries with no folding barrier detected",
         sum(1 for q in stepped if not q["foldBreakStep"]), len(stepped), 0.50),
        ("Queries with no dead step",
         sum(1 for q in QRY if not any(s["dead"] for s in q["steps"])), len(QRY), 0.95),
        ("Queries with no warning",
         sum(1 for q in QRY if not any(f["level"] == "warning" for f in q["findings"])),
         len(QRY), 0.90),
        ("Queries with no unresolved reference",
         sum(1 for q in QRY if not q["unresolved"]), len(QRY), 1.00),
        ("Queries not in a reference cycle",
         sum(1 for q in QRY if not q["inCycle"]), len(QRY), 1.00),
        ("Queries reaching a named connector",
         sum(1 for q in QRY if q["sourceKind"]), len(QRY), 0.80),
    ]


def q_audit():
    q16 = card(fold_waterfall()
               + how("Green is <em>no barrier detected</em>, not proof that the query folds; only "
                     "a refresh trace shows what the source received. Amber is every step after "
                     "the first one the parser can name a reason for."),
               "Q16  Folding barriers", "green: no barrier detected; amber: after a potential barrier")

    # The dataset's findings, not the parser's raw list: two of them are re-levelled there
    # (parse-recovered is an error, native-query with EnableFolding is a note) and the
    # page has to say what the dataset says.
    all_f = [(f.get("subject") or "(model)", f) for f in MD["findings"]]
    tone = {"warning": "warn", "info": "note", "error": "bad"}
    rows = [[chip(f["level"], tone.get(f["level"], "")), E(qn), "<code>%s</code>" % E(f["code"]),
             E(f.get("step") or "—"), E(re.sub(r"`([^`]+)`", r"\1", f["message"]))]
            for qn, f in all_f]
    n_err = sum(1 for _, f in all_f if f["level"] == "error")
    q17 = card(table(["", "Query", "Code", "Step", "Observation"], rows, False)
               + ('<p class="dc-caption">%d %s could not be fully read; the parser recovered and '
                  "read the rest, so every count for %s is a lower bound.</p>"
                  % (n_err, "query" if n_err == 1 else "queries", "it" if n_err == 1 else "them")
                  if n_err else ""),
               "Q17  Findings", "%d findings%s" % (len(all_f), (", %d errors" % n_err) if n_err else ""))

    q18 = card(scorecard(q_m_checks()), "Q18  Power Query scorecard",
               "folding is reported as a barrier, never as a boolean, so its threshold is the softest")
    return q16 + q17 + q18


def q_detail():
    q = max(QRY, key=lambda q: len(q["steps"]))
    if not q["steps"]:
        return card('<p class="dc-caption">No query here has a step to open.</p>',
                    "Q19  Step detail", "nothing to open")
    cards = []
    for s in q["steps"]:
        deltas = []
        for k in ("added", "removed", "renamed", "retyped"):
            for it in s["columns"].get(k, []):
                nm = it.get("name") if isinstance(it, dict) else it
                if isinstance(it, dict) and k == "renamed":
                    nm = "%s → %s" % (it.get("from", "?"), it.get("to", "?"))
                deltas.append(chip("%s %s" % (k[:3], nm), k))
        cards.append('<div class="dcv-step %s"><div class="dcv-step-h"><b>%d</b>'
                     '<span class="dcv-step-n">%s</span><span class="dcv-step-l">%s</span>%s</div>'
                     '<code class="dcv-step-fn">%s</code><div class="dcv-chips">%s</div></div>'
                     % ("dead" if s["dead"] else "", s["index"], E(s["name"]), E(s["label"]),
                        chip("barrier?", "warn") if s["name"] == q["foldBreakStep"]
                        else (chip("dead", "bad") if s["dead"] else ""),
                        E(s["functionName"] or "—"),
                        "".join(deltas)
                        or "<span class='dcv-none'>no stated column change</span>"))
    q19 = card('<div class="dcv-steps">%s</div>' % "".join(cards),
               "Q19  Step cards — %s" % q["name"], "%d steps" % len(q["steps"]))
    return q19


def build_part_pq():
    dead = sum(1 for q in QRY for s in q["steps"] if s["dead"])
    vitals = rail([
        ("Queries", len(QRY), "read as %s" % MD["format"]),
        ("Steps", MD["stepCount"], "%d dead" % dead),
        ("Query links", MD["linkCount"], "%d layers" % len(MD["layers"])),
        ("Source calls", sum(len(q["sources"]) for q in QRY),
         "%d connectors" % len(MD["sourceKinds"])),
        ("Findings", len(MD["findings"]),
         "%d warnings" % sum(1 for f in MD["findings"] if f["level"] == "warning")),
        ("Potential barriers", sum(1 for q in QRY if q["foldBreakStep"]),
         "of %d with steps" % sum(1 for q in QRY if q["steps"])),
    ])
    part("part-q", "Part Q — Power Query (M)",
         "{n} readings. Input is M: a labelled dump, a section document, a bare "
         "expression, or the M inside a TMDL partition.",
         [("q-structural", "Structural", "The query graph and one query's steps",
           q_structural()),
          ("q-sql", "Native SQL",
           "What runs at the source, before Power Query sees a row", q_native_sql()),
          ("q-dist", "Distribution", "What the queries are made of",
           q_distribution()),
          ("q-rel", "Relational", "Two facts crossed", q_relational()),
          ("q-audit", "Audits", "Folding, findings, and the score they roll up to", q_audit()),
          ("q-detail", "Detail", "One query in full",
           q_detail())],
         lede=vitals)


# ============================================ PART X — the two together =====

# ------------------------------------------------- not shown --------------

NOT_SHOWN = [
    ("T", "T22", "Blast-radius fan for one measure",
     "<code>model.downstream(name)</code> already returns it, transitive and cycle-safe. "
     "A radial fan from the measure outwards.", "low"),
    ("T", "T23", "DAX expansion tree",
     "The <code>--expand</code> view: one measure with every referenced measure opened as a "
     "nested block underneath it.", "low"),
    ("T", "T24", "Measure-to-table matrix",
     "Folder rows against table columns from <code>allTables</code>.", "low"),
    ("T", "T25", "Function co-occurrence",
     "Which DAX functions travel together. <code>SUMX</code> with <code>FILTER</code> is a "
     "different object from <code>SUMX</code> alone.", "medium"),
    ("T", "T26", "Naming-convention clusters",
     "Prefix and suffix families read off measure names — <code>FMT </code>, <code> PY</code>, "
     "<code> %</code>. The outliers are the ones to look at.", "medium"),
    ("T", "T27", "Sort-by column map",
     "Which columns sort by which. A sort-by pointing at a column of different granularity is a "
     "real defect.", "low"),
    ("Q", "Q20", "Blast radius for one query",
     "A <code>downstream</code> walk over the query graph, drawn as a radial fan from the "
     "query outwards.", "low"),
    ("Q", "Q21", "Dead-step position histogram",
     "Where in a chain dead steps land. Early ones are usually abandoned sources; late ones are "
     "usually a half-finished edit.", "low"),
    ("Q", "Q22", "Row-by-row hotspot detail",
     "The <code>row-by-row</code> finding opened out: which lambda, which table read as a value.",
     "medium"),
    ("T", "T28", "Column-level lineage, partition to measure",
     "Join a partition step's column deltas to the columns a measure's DAX stops at. The deepest "
     "reading the two parsers can currently support over one TMDL file.", "high"),
    ("T", "T29", "Storage-mode consistency",
     "Direct Lake entities against import partitions in one model, cross-checked with which "
     "measures read which. A mixed-mode model with a measure spanning both is worth flagging.",
     "medium"),
]


N_NOT_SHOWN = {"T": sum(1 for r in NOT_SHOWN if r[0] == "T"),
               "Q": sum(1 for r in NOT_SHOWN if r[0] == "Q")}


def build_not_shown(letter):
    """Only this segment's own backlog. The other segment's is on the other page."""
    tone = {"low": "note", "medium": "warn", "high": "bad"}
    rows = [["<code>%s</code>" % E(i), "<b>%s</b>" % E(n), d, chip(cost, tone[cost])]
            for p, i, n, d, cost in NOT_SHOWN if p == letter]
    return table(["#", "Pattern", "What it draws", "Build cost"], rows, False)


# =============================================================== page ======

CSS_EXTRA = """
/* ---- AI readiness checklist ---- */
.dcv-checks { display:grid; gap:0; border:1px solid var(--color-border); border-radius:var(--radius-md);
  overflow:hidden; margin:var(--space-s) 0 var(--space-l); }
.dcv-check { display:grid; grid-template-columns:40px 1fr auto; gap:var(--space-m); align-items:start;
  padding:var(--space-s) var(--space-l); border-bottom:1px solid var(--color-border); }
.dcv-check:last-child { border-bottom:0; }
.dcv-check-mark { width:28px; height:28px; border-radius:var(--radius-full); display:flex;
  align-items:center; justify-content:center; font-weight:700; font-size:16px; }
.dcv-check.pass .dcv-check-mark { background:var(--color-positive-bg); color:var(--color-positive); }
.dcv-check.fail .dcv-check-mark { background:var(--color-negative-bg); color:var(--color-negative); }
.dcv-check.na .dcv-check-mark { background:var(--color-info-bg); color:var(--color-info); }
.dc-prose .dcv-check p { margin:0; max-width:none; }
.dcv-check-text { padding-top:3px; }
.dcv-check-label { font-weight:600; }
.dcv-check-detail { margin:2px 0 0; color:var(--color-muted-foreground); font-size:0.92em; }
.dcv-check-count { white-space:nowrap; color:var(--color-muted-foreground); padding-top:4px; }

/* ---- chart chrome the doc theme does not carry ---- */
.dcv { display:block; max-width:100%; height:auto; overflow:visible; }
/* Where two ribbons overlap the hairline keeps the boundary visible; hovering one
   drops the rest away, which is the only way to follow a single strand through a
   crossing. :has() is the whole mechanism -- no script. */
.dcv-rib { transition: fill-opacity .12s ease, stroke-opacity .12s ease; }
svg.dcv:has(.dcv-rib:hover) .dcv-rib { fill-opacity: .08; stroke-opacity: .12; }
svg.dcv .dcv-rib:hover { fill-opacity: .85; stroke-opacity: 1; }
.dcv-card { background:var(--color-card); border:1px solid var(--color-border);
  border-radius:var(--radius-lg); padding:var(--space-l); margin:var(--space-l) 0; }
.dcv-head { display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-m);
  margin-bottom:var(--space-m); padding-bottom:var(--space-s);
  border-bottom:1px solid var(--color-border); flex-wrap:wrap; }
.dcv-title { font-weight:var(--weight-semibold); font-size:var(--text-300); }
.dcv-meta { font-size:var(--text-200); color:var(--color-muted-foreground); }
.dcv-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr));
  gap:var(--space-l); align-items:start; }
.dcv-grid > .dcv-card { margin:var(--space-l) 0 0; }
/* One analysis, one row: chart left, the same numbers right. Below 1000px the two
   stack, because a table squeezed to 300px is not a table. */
/* One decoded SQL statement per block: which query, which connector, which step it
   came from, then the statement. The header carries the provenance because the SQL on
   its own does not say where it was found. */
.dcv-sql { border:1px solid var(--color-border); border-radius:var(--radius-lg);
  overflow:hidden; margin:0 0 var(--space-m); background:var(--color-card); }
.dcv-sql-h { display:flex; align-items:baseline; gap:var(--space-m); flex-wrap:wrap;
  padding:var(--space-s) var(--space-m); border-bottom:1px solid var(--color-border);
  background:var(--color-muted); font-size:var(--text-200); }
.dcv-sql-m { color:var(--color-muted-foreground); font-size:var(--text-100); }
.dcv-sql-n { margin-left:auto; font-family:var(--font-numeric);
  font-size:var(--text-100); color:var(--color-muted-foreground); }
.dcv-sql .dcv-code { border:0; border-radius:0; max-height:none; }
.dcv-pair { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
  gap:var(--space-l); align-items:start; }
@media (max-width:1000px) { .dcv-pair { grid-template-columns:1fr; } }
.dcv-pair-chart, .dcv-pair-grid { min-width:0; }
.dcv-gwrap { overflow-x:auto; max-height:420px; overflow-y:auto; }
.dcv-gtable { width:100%; border-collapse:collapse; font-size:var(--text-200); }
.dcv-gtable th { position:sticky; top:0; z-index:1; background:var(--color-card);
  text-align:left; padding:5px var(--space-s); white-space:nowrap;
  font-size:var(--text-100); font-weight:var(--weight-semibold); letter-spacing:.04em;
  text-transform:uppercase; color:var(--color-muted-foreground);
  border-bottom:1px solid var(--color-border); }
.dcv-gtable td { padding:4px var(--space-s); border-bottom:1px solid var(--color-border);
  vertical-align:top; }
.dcv-gtable tbody tr:last-child td { border-bottom:0; }
.dcv-gtable tbody tr:hover { background:var(--color-hover); }
.dcv-gtable .a-r { text-align:right; font-family:var(--font-numeric); white-space:nowrap; }
.dcv-gtable .a-c { text-align:center; }
.dcv-swatch { display:inline-block; width:9px; height:9px; border-radius:2px;
  margin-right:6px; vertical-align:-1px; flex:none; }
.dcv-gtable .dcv-ex { color:var(--color-muted-foreground); }
.dcv-subject { margin:0 0 var(--space-l); padding:var(--space-m) var(--space-l);
  border-left:3px solid var(--a1-dot); background:var(--color-surface, var(--color-card)); border-radius:4px; }
.dcv-subject-name { margin:0 0 var(--space-xs); font-size:1.25rem; font-weight:600; line-height:1.3; }
.dcv-subject ul { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:2px; }
.dcv-subject li { font-size:var(--text-200, 0.9rem); color:var(--color-foreground); }
.dcv-subject li b { font-weight:500; margin-right:var(--space-xs); }
.dcv-subject li span { color:var(--color-foreground-muted, inherit); opacity:0.75; margin-left:var(--space-xs); word-break:break-all; }
.dcv-legend { display:flex; flex-wrap:wrap; gap:var(--space-s) var(--space-m);
  margin:var(--space-m) 0 0; font-size:var(--text-200); color:var(--color-muted-foreground); }
.dcv-key { display:inline-flex; align-items:center; gap:6px; }
.dcv-key i { width:10px; height:10px; border-radius:2px; display:inline-block; flex:none; }
.dcv-donut { max-width:190px; margin:0 auto; display:block; }
.dcv-rail { flex-wrap:wrap; }
.dcv-rail .dc-rail-stat { min-width:110px; }
.dcv-rail-sub { display:block; font-size:var(--text-100); color:var(--color-muted-foreground);
  margin-top:2px; }
.dcv-chip { display:inline-block; padding:1px 7px; border-radius:var(--radius-full);
  font-size:var(--text-100); background:var(--color-muted); color:var(--color-muted-foreground);
  border:1px solid var(--color-border); margin:2px 3px 2px 0; white-space:nowrap; }
.dcv-chip.warn { background:var(--color-warning-bg); color:var(--color-warning);
  border-color:transparent; }
.dcv-chip.bad { background:var(--color-negative-bg); color:var(--color-negative);
  border-color:transparent; }
.dcv-chip.ok { background:var(--color-positive-bg); color:var(--color-positive);
  border-color:transparent; }
.dcv-chip.note { background:var(--color-info-bg); color:var(--color-info); border-color:transparent; }
.dcv-chip.added { background:var(--color-positive-bg); color:var(--color-positive);
  border-color:transparent; }
.dcv-chip.removed { background:var(--color-negative-bg); color:var(--color-negative);
  border-color:transparent; }
.dcv-chip.retyped { background:var(--color-info-bg); color:var(--color-info); border-color:transparent; }
.dcv-chips { display:flex; flex-wrap:wrap; gap:2px; margin:var(--space-xs) 0 var(--space-m); }
.dcv-meters { display:grid; gap:var(--space-m); }
.dcv-meter-top { display:flex; justify-content:space-between; font-size:var(--text-200); }
.dcv-track { height:6px; background:var(--color-muted); border-radius:var(--radius-full);
  overflow:hidden; margin:5px 0 3px; }
.dcv-track i { display:block; height:100%; border-radius:var(--radius-full);
  background:var(--color-positive); }
.dcv-track i.warn { background:var(--color-warning); }
.dcv-track i.bad { background:var(--color-negative); }
.dcv-meter-sub { font-size:var(--text-100); color:var(--color-muted-foreground); margin:0; }
.dcv-scores { display:grid; gap:6px; }
.dcv-score { display:grid; grid-template-columns:10px 1fr auto auto; gap:var(--space-s);
  align-items:center; font-size:var(--text-200); padding:5px 0;
  border-bottom:1px solid var(--color-border); }
.dcv-score:last-child { border-bottom:0; }
.dcv-score-dot { width:8px; height:8px; border-radius:var(--radius-full);
  background:var(--color-positive); }
.dcv-score.warn .dcv-score-dot { background:var(--color-warning); }
.dcv-score.bad .dcv-score-dot { background:var(--color-negative); }
.dcv-score-val { font-family:var(--font-numeric); }
.dcv-score-thr { font-size:var(--text-100); color:var(--color-muted-foreground); min-width:74px;
  text-align:right; }
.dcv-code { font-family:var(--font-code); font-size:var(--text-200); line-height:1.55;
  background:var(--color-canvas); border:1px solid var(--color-border);
  border-radius:var(--radius-md); padding:var(--space-m); overflow:auto; margin:0 0 var(--space-m);
  white-space:pre; }
.dcv-code i { font-style:normal; }
.dcv-code i.k { color:var(--code-keyword); font-weight:var(--weight-semibold); }
.dcv-code i.f { color:var(--code-function); }
.dcv-code i.r { color:var(--code-ref); }
.dcv-code i.s { color:var(--code-string); }
.dcv-code i.n { color:var(--code-number); }
.dcv-code i.c { color:var(--color-muted-foreground); font-style:italic; }
.dcv-split { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(0,1fr); gap:var(--space-l); }
@media (max-width:860px) { .dcv-split { grid-template-columns:1fr; } }
.dcv-anatomy { display:flex; flex-wrap:wrap; gap:var(--space-s); margin-bottom:var(--space-l); }
.dcv-anat-wrap .dcvc-main { padding:0 0 0 var(--space-s); }
.dcv-anat-note { margin:0 0 var(--space-s); }
.dcvc-anat .dcvc-code, .dcv-anat-wrap .dcv-split { min-width:0; }
.dcv-anat-head { margin:0 0 var(--space-m); font-size:var(--text-400); }
.dcv-anat-head span { color:var(--color-muted-foreground); font-size:var(--text-300); }
.dcv-gauge { background:var(--color-canvas); border:1px solid var(--color-border);
  border-radius:var(--radius-md); padding:6px 12px; min-width:66px; text-align:center; }
.dcv-gauge b { display:block; font-family:var(--font-numeric); font-size:var(--text-500);
  line-height:1.1; }
.dcv-gauge span { font-size:var(--text-100); color:var(--color-muted-foreground); }
.dcv-vars { list-style:none; padding:0; margin:0 0 var(--space-m); font-size:var(--text-200); }
.dcv-vars li { display:flex; gap:var(--space-s); padding:3px 0;
  border-bottom:1px solid var(--color-border); }
.dcv-vars li span { color:var(--color-muted-foreground); font-family:var(--font-code);
  font-size:var(--text-100); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dcv-steps { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
  gap:var(--space-s); }
.dcv-step { background:var(--color-canvas); border:1px solid var(--color-border);
  border-radius:var(--radius-md); padding:var(--space-s) var(--space-m); }
.dcv-step.dead { border-style:dashed; opacity:.72; }
.dcv-step-h { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
.dcv-step-h b { font-family:var(--font-numeric); color:var(--color-muted-foreground);
  font-size:var(--text-200); }
.dcv-step-n { font-weight:var(--weight-semibold); font-size:var(--text-200); }
.dcv-step-l { font-size:var(--text-100); color:var(--color-muted-foreground); }
.dcv-step-fn { display:block; font-size:var(--text-100); color:var(--color-muted-foreground);
  margin:3px 0; word-break:break-all; }
.dcv-none { font-size:var(--text-100); color:var(--color-muted-foreground); }
.dcv-ex { font-size:var(--text-100); color:var(--color-muted-foreground); }
.dcv-nowrap { white-space:nowrap; }

/* A part is a banded region, so scrolling out of the TMDL half into the M half is a visible
   event rather than one more h2 in a long page. The badge carries the part's identity colour;
   nothing else on the page uses those three tints, so the band is unambiguous. */
.dcv-part { margin:var(--space-xxxl) 0 var(--space-l); padding:var(--space-l) var(--space-xl);
  border-radius:var(--radius-xl); border:1px solid var(--color-border);
  background:var(--color-canvas); }
.dcv-part h2 { margin:0 0 var(--space-xs); border:0; padding:0; }
.dcv-part-kicker { margin:0; font-size:var(--text-200); color:var(--color-muted-foreground); }
.dcv-part-badge { display:inline-block; font-size:var(--text-100); letter-spacing:.08em;
  text-transform:uppercase; font-weight:var(--weight-semibold); padding:2px 9px;
  border-radius:var(--radius-full); margin-bottom:var(--space-s); }
.dcv-part-badge.t { background:var(--a3-tint); color:var(--a3-ink); }
.dcv-part-badge.q { background:var(--a1-tint); color:var(--a1-ink); }
.dcv-part-badge.x { background:var(--a5-tint); color:var(--a5-ink); }
.dcv-modes { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:var(--space-m); margin:var(--space-l) 0; }
.dcv-mode { border:1px solid var(--color-border); border-radius:var(--radius-lg);
  padding:var(--space-m) var(--space-l); background:var(--color-card); }
.dcv-mode h4 { margin:0 0 var(--space-xs); font-size:var(--text-300); }
.dcv-mode p { margin:0 0 var(--space-xs); font-size:var(--text-200);
  color:var(--color-muted-foreground); }
.dcv-mode p:last-child { margin-bottom:0; color:var(--color-foreground); }
@media print { .dcv-card { break-inside:avoid; } .dcv-part { break-before:page; } }
"""

PART_BADGE = {"part-t": "t", "part-q": "q"}


def build_archive():
    """Displaced from the analysis tiers, still rendered.

    Kept rather than deleted because a keep/cut call is easier against something
    you can see than against a line in a list. Each says what it answers and why
    it lost its place.
    """
    cd = collections.Counter(an(m).get("chain", 0) for m in MEAS)
    a1 = card(histogram([(str(k), cd[k]) for k in range(max(cd) + 1)],
                        colour=lambda i, l: acc(2), xlab="chain depth", ylab="measures")
              + '<p class="dc-caption">Answers "how deep does this model stack". Displaced '
              "because the chain explorer shows the same depth for any one measure, "
              "with names on it.</p>",
              "A1  Chain-depth distribution", "was in Distribution")

    buckets = [(1, 1), (2, 4), (5, 9), (10, 19), (20, 29), (30, 9999)]
    labs = ["1", "2-4", "5-9", "10-19", "20-29", "30+"]
    lc = [sum(1 for m in MEAS if lo <= an(m).get("lines", 0) <= hi) for lo, hi in buckets]
    a2 = card(histogram(list(zip(labs, lc)), colour=lambda i, l: acc(6),
                        xlab="lines of DAX", ylab="measures")
              + '<p class="dc-caption">Answers "how long is the DAX here". Displaced because '
              "syntax weight says the same thing with more in it.</p>",
              "A2  DAX length distribution", "was in Distribution")

    tf = collections.Counter(t for m in MEAS for t in m["allTables"])
    a3 = card(ranked_bar(tf.most_common(), labw=170, barw=380, colour=lambda i, l: acc(3))
              + '<p class="dc-caption">Answers "which tables do the measures actually touch". '
              "Displaced because the partition bridge carries the same count beside the load "
              "path, which is the more useful place for it.</p>",
              "A3  Table fan-in", "was in Distribution")

    root = sum(1 for m in MEAS if not m["dependsOn"])
    leaf = sum(1 for m in MEAS if not m["usedBy"])
    both = sum(1 for m in MEAS if not m["dependsOn"] and not m["usedBy"])
    mid = len(MEAS) - root - leaf + both
    a4 = card(donut([("base", root - both, acc(2)), ("intermediate", mid, acc(0)),
                     ("terminal", leaf - both, acc(6)), ("isolated", both, NEUTRAL % "dot")],
                    centre=(str(len(MEAS)), "measures"))
              + legend([("base %d" % (root - both), acc(2)), ("intermediate %d" % mid, acc(0)),
                        ("terminal %d" % (leaf - both), acc(6)),
                        ("isolated %d" % both, NEUTRAL % "dot")])
              + '<p class="dc-caption">Answers "how much of this model is plumbing". Displaced '
              "because a four-slice donut is a table with extra steps.</p>",
              "A4  Position in the graph", "was in Distribution")

    a5 = card(partition_strip()
              + '<p class="dc-caption">Answers "how does each table load". Displaced because the '
              "partition bridge answers it and cross-checks it against what the M actually "
              "says.</p>",
              "A5  Partitions — kind and storage mode", "was in Distribution")

    pts = []
    for m in MEAS:
        wgt, dn = an(m).get("weight", 0), len(m["usedBy"])
        pts.append((wgt, dn, m["name"], NEG if (wgt >= 30 and dn >= 4) else acc(2),
                    3 + min(len(m["dependsOn"]), 6) * 0.7))
    medw = sorted(an(m).get("weight", 0) for m in MEAS)[len(MEAS) // 2]
    a6 = card(scatter(pts, xlab="syntax weight " + chr(0x2192), ylab=chr(0x2191) + " measures that read it",
                      xline=30, yline=4)
              + '<p class="dc-caption">Answers "which measures are heavy AND load-bearing". '
              "Displaced because syntax weight and dependant count are each already ranked "
              "with names on them, and crossing them adds a quadrant, not a fact. Median "
              "weight is %d.</p>" % medw,
              "A6  Weight vs blast radius", "was in Relational")

    fol = {m["name"]: m["folder"] for m in MEAS}
    cc = collections.Counter()
    for m in MEAS:
        for d in m["dependsOn"]:
            if d in fol:
                cc[(m["folder"], fol[d])] += 1
    fsx = sorted({f for pair in cc for f in pair})
    top = max(cc.values()) or 1
    a7 = card(heatmap(fsx, fsx, lambda r, c: (cc.get((r, c), 0), cc.get((r, c), 0) / top),
                      labw=180, cw=26, fmt_cell=lambda v: v or "")
              + '<p class="dc-caption">Row reads column, and the column numbers are the row '
              "numbers beside them: the same eighteen folders on both axes, so naming them "
              "twice cost the width that truncated every heading. "
              'Answers "which folders depend on which". Displaced '
              "because the chain explorer shows the same coupling for the measure being "
              "renamed, which is when anyone asks. %d directed pairs.</p>" % len(cc),
              "A7  Folder-to-folder coupling", "was in Relational")

    described = sum(1 for m in MEAS if m["description"].strip())
    fmtd = sum(1 for m in MEAS if m["formatString"])
    a8 = card('<div class="dcv-meters">'
              + meter("Measures with a description", described, len(MEAS),
                      "ok" if described / len(MEAS) > 0.6 else "bad")
              + meter("Measures with a format string", fmtd, len(MEAS),
                      "ok" if fmtd / len(MEAS) > 0.8 else "warn")
              + meter("Measures in a named folder",
                      sum(1 for m in MEAS if m["folder"] != "Other"), len(MEAS), "ok")
              + meter("Measures referenced by another parsed object, or hidden",
                      len(MEAS) - len(UNREFERENCED), len(MEAS), "ok") + "</div>"
              + '<p class="dc-caption">Answers "how complete is the metadata". Displaced '
              "because the format and description cards answer two of these four with the "
              "list attached, and a ratio with no list is a number nothing can be done with.</p>",
              "A8  Coverage meters", "was in Audits")

    a9 = card((table(["Measure", "Folder", "Format"],
                     [[E(n), E(BY_NAME[n]["folder"]),
                       "<code>%s</code>" % E(BY_NAME[n]["formatString"] or chr(0x2014))]
                      for n in UNREFERENCED], False) if UNREFERENCED else empty_note())
              + '<p class="dc-caption">Answers "what is unreferenced". Displaced because the '
              "scorecard keeps it as a check, and a measure nothing reads inside this file "
              "may still be read by a report the parser has never seen " + chr(0x2014) + " which makes a "
              "standalone list of them read more like a verdict than it should.</p>",
              "A9  Unreferenced visible measures",
              "%d not referenced by another parsed object" % len(UNREFERENCED))

    heavy = sorted(MEAS, key=lambda m: an(m).get("weight", 0), reverse=True)[:12]
    a10 = card(ranked_bar([(m["name"], an(m)["weight"]) for m in heavy],
                          labw=180, barw=340, colour=lambda i, l: acc(0))
               + '<p class="dc-caption">Answers "which measures are hardest to read". Displaced '
               "because <em>weight</em> is a composite invented here " + chr(0x2014) + " lines, VARs, calls "
               "and nesting rolled into one number " + chr(0x2014) + " and nobody can act on <q>weight 47</q>. "
               "The parts of it that are actionable are actionable separately: chain depth is a "
               "finding, and line count is on the anatomy card.</p>",
               "A10  Syntax weight ranking", "was in Analysis " + chr(0xb7) + " Measures")

    fc = collections.Counter(m["folder"] for m in MEAS)
    a11 = card(ranked_bar(fc.most_common(), labw=200, barw=320)
               + '<p class="dc-caption">Answers "how many measures per folder". Displaced '
               "because no decision follows from it. That folder 04 holds sixteen measures and "
               "folder 09 holds four is true and inert.</p>",
               "A11  Measures per display folder", "was in Analysis " + chr(0xb7) + " Measures")

    hv = collections.defaultdict(lambda: {"visible": 0, "hidden": 0})
    for m in MEAS:
        hv[m["folder"]]["hidden" if m["isHidden"] else "visible"] += 1
    hvrows = sorted(hv.items(), key=lambda kv: -(kv[1]["visible"] + kv[1]["hidden"]))
    a12 = card(stacked_rows(hvrows, ["visible", "hidden"],
                            {"visible": acc(0), "hidden": NEUTRAL % "dot"}, labw=200, barw=330)
               + legend([("visible", acc(0)), ("hidden", NEUTRAL % "dot")])
               + '<p class="dc-caption">Answers "what does a report author see". Displaced '
               "because eighteen bars carry three facts: the folders that are entirely hidden, "
               "and any that are half hidden. A model this parser can read cannot tell a "
               "deliberate helper folder from an oversight, because it has never seen the "
               "reports.</p>",
               "A12  Hidden vs visible, per folder", "was in Analysis " + chr(0xb7) + " Measures")

    roles = collections.Counter()
    sorted_by = {c["sortByColumn"] for c in COLS if c["sortByColumn"]}
    for c in COLS:
        if c["isKey"]:
            roles["Key"] += 1
        if c["isCalculated"]:
            roles["Calculated"] += 1
        if c["isHidden"]:
            roles["Hidden"] += 1
        if c["sortByColumn"]:
            roles["Sorted by another"] += 1
        if c["name"] in sorted_by:
            roles["Sorts another"] += 1
        if c["dataCategory"]:
            roles["Data category set"] += 1
        if (c["summarizeBy"] or "none") != "none":
            roles["Summarised"] += 1
    a13 = card(ranked_bar(roles.most_common(), labw=170, barw=300,
                          colour=lambda i, l: acc(3))
               + '<p class="dc-caption">Answers "what roles do columns play". Displaced because '
               "a model-wide count of roles names nothing. <q>Seven columns are keys</q> is not a "
               "sentence anyone acts on; <q>which</q> is, and that is the column list on the "
               "metadata card.</p>",
               "A13  Column role counts", "was in Analysis " + chr(0xb7) + " Columns")

    sb = collections.defaultdict(collections.Counter)
    for c in COLS:
        sb[c["table"]][c["summarizeBy"] or "(unset)"] += 1
    kinds = sorted({k for v in sb.values() for k in v}, key=lambda k: (k != "none", k))
    a14 = card(stacked_rows(sorted(((t, dict(v)) for t, v in sb.items()),
                                   key=lambda kv: -sum(kv[1].values())), kinds,
                            {k: SUMMARISE_TONE.get(k, acc(5)) for k in kinds},
                            labw=150, barw=300)
               + legend([(k, SUMMARISE_TONE.get(k, acc(5))) for k in kinds])
               + '<p class="dc-caption">Answers "is implicit summarisation left on". Displaced '
               "because the findings register answers it by name: four columns, each one listed "
               "with the setting it carries. The distribution behind those four adds a chart and "
               "no information.</p>",
               "A14  summarizeBy, per table", "was in Analysis " + chr(0xb7) + " Columns")

    return (grid(a1, a2) + a3 + grid(a4, a5) + grid(a6, a7) + grid(a8, a9)
            + grid(a10, a11) + grid(a12, a13) + a14)


def merged_note():
    """Only rendered when the parse actually collided, which a normal model will not."""
    if not MERGED_AWAY:
        return ""
    rows = ", ".join("<code>%s</code> on <code>%s</code>" % (E(n), E(t))
                     for n, t, _ in MERGED_AWAY)
    return note(
        "warn", "Demo artefact",
        "<b>Two fixtures, parsed as one.</b> %s %s defined in both. The parser "
        "keys a measure id by name, so each collided pair shares one id — documented, "
        "deliberate, and unreachable in a real model, where TMDL keeps measure names unique "
        "model-wide. The duplicates are dropped once at parse time rather than per chart, so "
        "every count on this page reads %d measures and the canvas has one block per measure. "
        "On a genuinely merged export this is the warning that matters."
        % (rows, "is" if len(MERGED_AWAY) == 1 else "are", len(MEAS)))


# ============================================================== the doc set ==

# Three pages, one navigation. The rail is the template's own doc-set tree:
# `.dc-nav` with a <details> per page, the current page's own headings injected at
# runtime into [data-dc-outline], and every other page's outline pre-rendered so the
# tree is complete before any script runs.
def subject():
    """What was analysed: the label if one was given, else the input's own name."""
    inp = (REPORT or {}).get("input", {})
    return ((REPORT or {}).get("label") or "").strip() or (inp.get("subject") or "").strip()


def subject_block():
    """The line under every h1 that says which model, file or paste this page is
    about, with the full path where there is one. A report that does not name its
    subject is a report nobody can file."""
    given = (REPORT or {}).get("input", {}).get("given") or []
    name = subject()
    if not name and not given:
        return ""
    items = []
    for g in given[:6]:
        where = g.get("path") or ""
        items.append("<li><b>%s</b> <code>%s</code>%s</li>" % (
            E(g.get("kind") or "input"), E(g.get("name") or ""),
            (" <span>%s</span>" % E(where)) if where and where != g.get("name") else ""))
    if len(given) > 6:
        items.append("<li>and %d more</li>" % (len(given) - 6))
    return ('<div class="dcv-subject"><p class="dcv-subject-name">%s</p><ul>%s</ul></div>'
            % (E(name or "Analysed input"), "".join(items)))


def doc_title():
    """The rail heading and the window chrome. The run's label, else the input's name."""
    return subject() or "Semantic model analysis"


PAGE_ORDER = ["index", "tmdl", "pq", "ai", "refs"]

#: `label` is the short name; the <title>, the chrome and the eyebrow are built from it
#: and `doc_title()`, so a run's label reaches every page without three more literals.
PAGES = {
    "index": {
        "file": "index.html",
        "nav": "Overview",
        "label": "Overview",
        "h1": "What is in this model",
    },
    "tmdl": {
        "file": "semantic-model.html",
        "part": "part-t",
        "letter": "T",
        "nav": "Semantic model (TMDL)",
        "label": "Semantic model (TMDL)",
        "h1": "The semantic model, read as text",
    },
    "pq": {
        "file": "power-query.html",
        "part": "part-q",
        "letter": "Q",
        "nav": "Power Query (M)",
        "label": "Power Query (M)",
        "h1": "Power Query, read as text",
    },
    "ai": {
        "file": "ai-readiness.html",
        "nav": "AI readiness",
        "label": "AI readiness",
        "h1": "Is this model ready for AI?",
    },
    "refs": {
        "file": "references.html",
        "nav": "References",
        "label": "References",
        "h1": "References",
    },
}

#: Set by set_catalogue(). Off by default, because a report should carry the patterns
#: that survived the choice and not the choosing.
CATALOGUE = False


def set_catalogue(flag):
    """Add the archive and the not-yet-drawn backlog to every page that has one."""
    global CATALOGUE
    CATALOGUE = bool(flag)


def active_pages():
    """The pages this dataset can support, in rail order."""
    return (["index"] + (["tmdl"] if HAS_TMDL else []) + (["pq"] if HAS_M else [])
            + (["ai"] if has_readiness() else []) + ["refs"])


def has_readiness():
    return HAS_TMDL and bool((REPORT or {}).get("tmdl", {}).get("aiReadiness"))


def segment_pages():
    """The pages that carry a parse segment, which excludes the overview and the
    AI readiness checklist."""
    return [k for k in active_pages() if k in ("tmdl", "pq")]


def pages(report):
    """Every page this dataset supports. Yields (page key, html).

    The parts are built first and once: a card function can be expensive, both pages'
    navigation needs every page's outline, and an outline is read off the built tiers.
    The finder's index is built after them, because it lists their cards.
    """
    global APP
    if HAS_TMDL:
        build_part_tmdl()
    if HAS_M:
        build_part_pq()
    prepare_landing()
    APP = build_app_index()
    for key in active_pages():
        if key == "index":
            yield key, build_index()
        elif key == "ai":
            yield key, build_ai_page()
        elif key == "refs":
            yield key, build_refs_page()
        else:
            yield key, build_page(key)


#: The finder's and the inspector's index, one per render. See app.py.
APP = None

_CARD_HEAD = re.compile(r'id="(card-[tq]\d+)"[^>]*><div class="dcv-head"><span class="dcv-title">'
                        r'([TQ]\d+)\s+(.*?)</span>')


def cards_in(key):
    _, _, _, _, tiers = part_of(PAGES[key]["part"])
    return [(i, code, html.unescape(t)) for _, _, _, h in tiers
            for i, code, t in _CARD_HEAD.findall(h)]


def build_app_index():
    from . import app
    written = {k: PAGES[k] for k in active_pages()}
    views = {}
    for k in segment_pages():
        views[k] = ([(a, n) for _, a, n in outline_of(k)], cards_in(k))
    if "ai" in written:
        views["ai"] = ([(a, n) for _, a, n in outline_of("ai")], [])
    views["index"] = ([(a, n) for _, a, n in outline_of("index")], [])
    return app.build(REPORT, written, views, (REPORT or {}).get("help") or {})


def title_of(key):
    return "%s %s %s" % (PAGES[key]["label"], DASH_EM, doc_title())


def part_of(anchor):
    hit = [x for x in PARTS if x[0] == anchor]
    assert len(hit) == 1, "%d parts match %r" % (len(hit), anchor)
    return hit[0]


def outline_of(key):
    """Every h2 the page emits, in document order: on a segment page, one per view.

    Built from the same data the headings are, rather than by re-parsing the output or
    guessing at slugs: every id here is one this generator sets explicitly.
    """
    if key == "ai":
        ai = REPORT["tmdl"]["aiReadiness"]
        return ([(2, "score", "At a glance")]
                + [(2, "ai-" + g["key"], g["title"]) for g in ai["groups"]]
                + [(2, "about", "About this checklist")])
    if key == "refs":
        return [(2, "links", "Links")]
    if key == "index":
        return ([(2, a, n) for a, n in (
                    ("glance", "At a glance"), ("highlights", "What stands out"),
                    ("health", "Model health"), ("lineage", "Data lineage"),
                    ("mlineage", "Measure lineage"))
                 if a == "glance" or a in LANDING]
                + [(2, "first", "Look at first"), (2, "jump", "Go to")]
                + [(2, "fed", "Input")]
                + ([(2, "coverage", "Coverage")] if HAS_TMDL else [])
                + [(2, "about", "About this analysis")])
    spec = PAGES[key]
    _, _, _, _, tiers = part_of(spec["part"])
    out = [(2, a, heading) for a, heading, _, _ in tiers]
    if key == "tmdl" and CATALOGUE:
        out.append((2, "archive", "Archive"))
    if CATALOGUE:
        out.append((2, "later", "Not drawn here"))
    out.append((2, "about", "About this page"))
    return out


def nav_html(current):
    items = []
    for i, key in enumerate(active_pages(), start=1):
        spec = PAGES[key]
        outline = outline_of(key)
        is_cur = key == current
        head = ('<summary><span class="dc-nav-num">%02d</span>'
                '<span class="dc-nav-name">%s</span>'
                '<span class="dc-nav-group-count">%d</span></summary>'
                % (i, E(spec["nav"]), len(outline)))
        if is_cur:
            # dc-doc.js fills this from the live document, so the current page's outline
            # tracks the page rather than a build-time snapshot of it.
            inner = "<ol data-dc-outline></ol>"
        else:
            lis = "".join('<li class="lvl-%d"><a href="%s#%s">%s</a></li>'
                          % (lvl, spec["file"], anchor, E(text))
                          for lvl, anchor, text in outline)
            inner = ('<ol class="dc-nav-outline">%s</ol>'
                     '<p class="dc-nav-open"><a href="%s">Open page</a></p>'
                     % (lis, spec["file"]))
        items.append('<li><details class="dc-nav-page%s"%s>%s%s</details></li>'
                     % (" is-current" if is_cur else "", " open" if is_cur else "",
                        head, inner))
    return ('<nav class="dc-nav" aria-label="Documentation">'
            '<p class="dc-eyebrow dc-nav-head">%s</p>'
            '<ul class="dc-nav-list">%s</ul></nav>' % (E(doc_title()), "".join(items)))


def input_files(kind):
    """What was actually read, named. `<code>` per file, or a count once that is a wall."""
    files = ((REPORT or {}).get("input", {}).get(kind) or [])
    names = [f["name"] for f in files]
    if not names:
        return "no input"
    if len(names) > 4:
        return "%d files" % len(names)
    return ", ".join("<code>%s</code>" % E(n) for n in names)


def parse_line(seg):
    """One sentence naming the input and the size of what came out of it."""
    if seg == "tmdl":
        return ("<code>tmdl_parser.py</code> read %s: %d measures, %d tables, %d columns, "
                "%d relationships, %d dependency links. <code>m_parser.py</code> also read the "
                "M inside each partition.") % (
            input_files("tmdl"), len(MEAS), len(TABS), len(COLS), len(REL), TD["linkCount"])
    n_sql = len((REPORT or {}).get("m", {}).get("sql") or [])
    files = input_files("m")
    if files == "no input" and HAS_TMDL:
        files = "the M inside the model's partitions"
    return ("<code>m_parser.py</code> read %s: %d queries, %d steps, %d dependency "
            "links, %d with native SQL.") % (
        files, len(QRY), MD["stepCount"], MD.get("linkCount", 0), n_sql)


KBD_FIND = '<kbd class="sma-kbd">Ctrl K</kbd>'


def page_lede(seg):
    """The page's opening sentence: where to start and how to find anything else."""
    return ("Read from the definition, never the data. Start with <b>Findings</b>; %s finds "
            "any %s." % (KBD_FIND, "measure, table or column" if seg == "tmdl" else "query"))


def segment_note(seg):
    """Why this is one page and not half of one: a run emits only what its input
    supports, so the segments are separate pages."""
    other = "pq" if seg == "tmdl" else "tmdl"
    if other in active_pages():
        return ["<p>One of two segments. <b>%s</b> is the other page; the overview connects "
                "them.</p>" % E(PAGES[other]["nav"])]
    return ["<p>Only %s was handed over, so there is no %s page. Hand it over as well and the "
            "page appears in the rail.</p>"
            % ("TMDL" if seg == "tmdl" else "Power Query M", E(PAGES[other]["nav"]))]


def build_rules():
    """Shared by both segments, so it lives once, on the page that connects them."""
    return (
        "<ol class='dc-steps'>"
        "<li><p class='dc-step-title'>Colour is a token, never a hex</p><p>Every fill on these "
        "pages is <code>var(--a3-dot)</code> or a semantic token. Toggle the theme and check: a "
        "hardcoded hex survives the toggle and goes invisible in one of the two.</p></li>"
        "<li><p class='dc-step-title'>One visual channel per meaning</p><p>Where colour carries "
        "a folder, nothing else does. Where it carries a source kind, nothing else does. Two "
        "meanings on one channel is a chart that has to be explained.</p></li>"
        "<li><p class='dc-step-title'>Hand-emitted SVG, no chart library</p><p>A CDN dependency "
        "breaks the one property that makes these files worth sending: they open from "
        "<code>file://</code>, offline, forever. Every page here makes zero network requests, "
        "and that is asserted rather than assumed.</p></li>"
        "<li><p class='dc-step-title'>A modelling observation is never a verdict</p>"
        "<p>Inherited from the M parser. Folding is reported as a <em>potential barrier</em> "
        "with a reason, because a modeller can judge a reason and cannot judge a boolean. "
        "<code>error</code> exists for one thing only: text the parser could not read.</p></li>"
        "<li><p class='dc-step-title'>If the parser cannot say it, the chart does not draw it"
        "</p><p>Which is why Q3 is a grid rather than a sankey, and why the relationship "
        "diagram shows cardinality and direction as declared and nothing about which path a "
        "filter takes at query time.</p></li>"
        "<li><p class='dc-step-title'>The page reads the dataset</p><p>Every number on these "
        "pages is read from <code>report.json</code>; nothing is re-derived from the parse. "
        "Where the page needs a fact the dataset does not carry, the dataset is what "
        "changes.</p></li>"
        "<li><p class='dc-step-title'>Port the app, do not reinvent it</p><p>Q1 is the Power "
        "Query app's own canvas {d} the same layout arithmetic, the same barycenter sweeps, "
        "the same bezier rule and the same palette, and T2 is its measure-chain explorer. A "
        "second implementation that could disagree with the app would be one implementation "
        "and one liability. What changed is the wheel: the apps own the viewport and scroll "
        "the canvas with it, and here a canvas is a block in a long document, so a plain "
        "wheel scrolls the page and Ctrl or pinch zooms.</p></li>"
        "<li><p class='dc-step-title'>Analysis first, visual second</p><p>Every head in the "
        "analysis tiers is the question. The chart underneath is the answer, and it was chosen "
        "after the question rather than the question being written to suit a chart that already "
        "existed.</p></li>"
        "<li><p class='dc-step-title'>The segment is the input, not the toolchain</p><p>The TMDL "
        "page reads a table's partition M with the M parser and is still a TMDL segment, because "
        "TMDL is all that was handed over. What decides a segment is the input {d} never "
        "which module did the reading.</p></li>"
        "</ol>").replace("{d}", DASH_EM)


def build_cannot(seg):
    if seg == "tmdl":
        return note(
            "warn", "Caveats",
            "<b>Relationships as declared, not as resolved.</b> Cardinality, direction and the "
            "active flag are read from the file; which route a filter takes at query time, and "
            "whether an accepted path is the one the modeller meant, are not knowable here. "
            "<b>No report usage.</b> “Unreferenced” means no other parsed object reads the "
            "measure; the report layer is not in this file. <b>No row counts, no timings.</b> "
            "Text in, facts out " + DASH_EM + " a heaviest measure is heavy in <em>syntax</em>, and "
            "the page has to keep saying so. <b>No verdicts.</b> A calculated column is listed, "
            "not condemned. <b>Cultures and linguistic metadata are counted, not read.</b>")
    return note(
        "warn", "Caveats",
        "<b>No row counts, no timings, no refresh history.</b> Text in, facts out " + DASH_EM + " "
        "nothing here has seen the data or watched a refresh. <b>Folding is a potential barrier "
        "with a reason, never a verdict.</b> “No barrier detected” is not proof that a "
        "query folds; only a trace of the refresh shows what the source received, and "
        "<code>Value.NativeQuery</code> with <code>EnableFolding=true</code> is the one barrier "
        "that may not be one. <b>No credentials, no gateway, no connection test.</b> A connector "
        "is read off the M, so a source that is named here may still be unreachable.")


def build_coverage():
    """How much of the model the run actually read. Before the findings, because a run
    on a partial handover must say so before anyone reads them."""
    cov = COVERAGE or {}
    o = cov.get("objects") or {}
    labels = [("tables", "tables"), ("measures", "measures"), ("columns", "columns"),
              ("partitions", "partitions"), ("relationships", "relationships"),
              ("roles", "roles"), ("calculationGroups", "calculation groups"),
              ("calculationItems", "calculation items"), ("hierarchies", "hierarchies"),
              ("expressions", "shared expressions"), ("perspectives", "perspectives"),
              ("refreshPolicies", "refresh policies")]
    cells = "".join('<div class="dc-rail-stat"><span class="dc-rail-value">%s</span>'
                    '<span class="dc-rail-label">%s</span></div>' % (fmt(o.get(k, 0)), E(lab))
                    for k, lab in labels if o.get(k))
    body = ['<div class="dc-rail dcv-rail dc-full">%s</div>' % cells]
    bits = []
    if o.get("model"):
        bits.append("The model block was read, so compatibility level, culture and "
                    "<code>discourageImplicitMeasures</code> are known.")
    else:
        bits.append("No model block was handed over, so compatibility level and "
                    "<code>discourageImplicitMeasures</code> are unknown.")
    if not o.get("relationships"):
        bits.append("No relationships were handed over; the relationship tier and its rules "
                    "are silent rather than guessing.")
    if cov.get("unsupported"):
        bits.append("Recognised but not analysed: %s." % ", ".join(
            "<code>%s</code> &times;%d" % (E(k), v) for k, v in cov["unsupported"].items()))
    for kind, names in (cov.get("declaredNotParsed") or {}).items():
        bits.append("<b>model.tmdl lists %d %s%s not in the handover:</b> %s." % (
            len(names), E(kind), "" if len(names) == 1 else "s",
            ", ".join("<code>%s</code>" % E(n) for n in names[:12])
            + (", …" if len(names) > 12 else "")))
    body.append("<p>%s</p>" % " ".join(bits))
    diags = cov.get("diagnostics") or []
    if diags:
        body.append(note("warn", "Parser", "<br>".join(
            "<b>%s:</b> %s" % (E(d["level"]), E(d["message"])) for d in diags)))
    errors = [f for f in (FINDINGS or []) if f["level"] == "error"]
    if errors:
        body.append(note("warn", "Could not be read", "%d %s declared with nothing readable "
                         "under %s: %s. Every reading of %s below is a reading of nothing." % (
                             len(errors), "object" if len(errors) == 1 else "objects",
                             "it" if len(errors) == 1 else "them",
                             ", ".join("<code>%s</code>" % E(f["subject"]) for f in errors[:10]),
                             "it" if len(errors) == 1 else "them")))
    return "".join(body)


def all_findings():
    return (APP or {}).get("findings") or []


LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}
LEVEL_TONE = {"error": "bad", "warning": "warn", "info": "note"}


def stat_cells(items):
    return "".join('<div class="dc-stat"><span class="dc-stat-label">%s</span>'
                   '<span class="dc-stat-value">%s</span></div>' % (E(l), E(str(v)))
                   for l, v in items)


def header_stats(key):
    fs = all_findings()
    warn = sum(1 for f in fs if f["lv"] in ("error", "warning"))
    if key == "ai":
        sm = REPORT["tmdl"]["aiReadiness"]["summary"]
        return stat_cells([("Pass", "%d/%d" % (sm["passed"], sm["total"]))])
    if key == "refs":
        return stat_cells([("Links", len(REFERENCES))])
    if key == "tmdl":
        n = sum(1 for f in fs if f["p"] == "tmdl")
        return stat_cells([("Findings", n), ("Measures", len(MEAS))])
    if key == "pq":
        n = sum(1 for f in fs if f["p"] == "pq")
        return stat_cells([("Findings", n), ("Queries", len(QRY))])
    return stat_cells([("To act on", warn), ("Findings", len(fs))])


def finding_row(f):
    """One finding as a row that opens its subject in the inspector."""
    msg = re.sub(r"`([^`]+)`", r"\1", f["msg"])
    attrs = (' data-sma-open="%s"' % E(f["o"])) if f["o"] else \
        (' data-sma-go="%s#card-%s"' % (PAGES[f["p"]]["file"], "t1" if f["p"] == "tmdl" else "q17")
         if f["p"] in ("tmdl", "pq") else "")
    return ('<li><button type="button" class="sma-frow"%s>'
            '<span class="dcv-chip %s">%s</span><span class="sma-frow-s">%s</span>'
            '<span class="sma-frow-m">%s</span></button></li>'
            % (attrs, LEVEL_TONE.get(f["lv"], ""), E(f["lv"]), E(f["subj"] or f["code"]),
               E(clip(msg, 140))))


BROWSE = [("table", "Tables"), ("measure", "Measures"), ("column", "Columns"),
          ("relationship", "Relationships"), ("query", "Queries"), ("role", "Roles"),
          ("calc group", "Calculation groups"), ("hierarchy", "Hierarchies")]

#: Built once per render by build_index(); the landing page and its island read it.
LINEAGE = None
MLINEAGE = None
#: The landing sections this run has content for. The outline reads it, so the rail never
#: links to a section the page did not draw.
LANDING = set()


def prepare_landing():
    global LINEAGE, MLINEAGE, LANDING
    from . import overview
    LINEAGE = overview.lineage(REPORT)
    MLINEAGE = overview.measure_lineage(REPORT) if HAS_TMDL else None
    written = {k: PAGES[k] for k in active_pages()}
    LANDING = set()
    if overview.highlights(REPORT, written, LINEAGE):
        LANDING.add("highlights")
    if has_readiness() or overview.checklist(REPORT, t_tmdl_checks() if HAS_TMDL else [],
                                             q_m_checks() if HAS_M else [], LINEAGE):
        LANDING.add("health")
    if len(LINEAGE["nodes"]) >= 2:
        LANDING.add("lineage")
    if MLINEAGE and len(MLINEAGE["nodes"]) >= 2:
        LANDING.add("mlineage")


def stat_card(value, label, sub="", tone="", browse=None, href=None):
    inner = ('<span class="sma-stat-v dc-num">%s</span><span class="sma-stat-l">%s</span>%s'
             % (E(str(value)), E(label),
                ('<span class="sma-stat-s">%s</span>' % E(sub)) if sub else ""))
    cls = "sma-stat" + ((" t-" + tone) if tone else "")
    if browse:
        return ('<button type="button" class="%s" data-sma-browse="%s" title="Browse %s">%s</button>'
                % (cls, E(browse), E(label.lower()), inner))
    if href:
        return '<a class="%s" href="%s">%s</a>' % (cls, E(href), inner)
    return '<div class="%s">%s</div>' % (cls, inner)


def stat_cards():
    from . import overview
    counts = collections.Counter(o["k"] for o in (APP or {}).get("objects", []))
    cards_ = []
    if HAS_TMDL:
        auto = (LINEAGE or {}).get("autoDate", 0)
        cards_.append(stat_card(len(TABS) - auto, "Tables",
                                ("+%d auto date/time" % auto) if auto else "",
                                "bad" if auto else "", browse="table"))
        cards_.append(stat_card(len(MEAS), "Measures", "%d unreferenced" % len(UNREFERENCED)
                                if UNREFERENCED else "", browse="measure"))
        cards_.append(stat_card(len(COLS), "Columns",
                                "%d calculated" % sum(1 for c in COLS if c["isCalculated"])
                                if any(c["isCalculated"] for c in COLS) else "", browse="column"))
        real = [r for r in REL if not (overview.is_auto_date(r["fromTable"])
                                       or overview.is_auto_date(r["toTable"]))]
        cards_.append(stat_card(len(real), "Relationships",
                                ("+%d to auto date/time" % (len(REL) - len(real)))
                                if len(REL) != len(real) else "",
                                "warn" if not real and len(TABS) > 1 else "", browse="relationship"))
    if HAS_M:
        conns = {(s.get("label"), s.get("target"), s.get("database")) for q in QRY
                 for s in q["sources"] if s.get("tier") == "connector"}
        cards_.append(stat_card(len(QRY), "Queries", "%d steps" % MD["stepCount"], browse="query"))
        cards_.append(stat_card(len(conns), "Data sources", ", ".join(sorted(
            {s.get("label") for q in QRY for s in q["sources"] if s.get("tier") == "connector"}))[:40]))
    fs = all_findings()
    act = sum(1 for f in fs if f["lv"] in ("error", "warning"))
    first = segment_pages()[0] if segment_pages() else None
    cards_.append(stat_card(act, "To act on", "%d worth knowing" % (len(fs) - act),
                            "warn" if act else "good",
                            href=("%s#%s" % (PAGES[first]["file"],
                                             "t-findings" if first == "tmdl" else "q-audit"))
                            if first else None))
    if has_readiness():
        sm = REPORT["tmdl"]["aiReadiness"]["summary"]
        ratio = sm["passed"] / max(1, sm["total"])
        cards_.append(stat_card("%d/%d" % (sm["passed"], sm["total"]), "AI readiness", "checks pass",
                                "good" if ratio >= 0.8 else ("warn" if ratio >= 0.5 else "bad"),
                                href=PAGES["ai"]["file"]))
    return '<div class="sma-stats dc-full">%s</div>' % "".join(cards_)


def highlight_cards():
    from . import overview
    written = {k: PAGES[k] for k in active_pages()}
    items = overview.highlights(REPORT, written, LINEAGE or {"nodes": [], "edges": []})
    if not items:
        return ""
    return '<div class="sma-hl-grid dc-full">%s</div>' % "".join(
        '<a class="sma-hl t-%s" href="%s"><span class="sma-hl-v dc-num">%s</span>'
        '<span class="sma-hl-t">%s</span><span class="sma-hl-s">%s</span></a>'
        % (tone, E(href), E(str(v)), E(text), E(sub)) for tone, v, text, sub, href in items)


CHECK_MARK = {"pass": "✓", "warn": "!", "fail": "✗"}


def health_panel():
    from . import overview
    rows = overview.checklist(REPORT, t_tmdl_checks() if HAS_TMDL else [],
                              q_m_checks() if HAS_M else [], LINEAGE or {})
    if not rows:
        return ""
    order = {"fail": 0, "warn": 1, "pass": 2}
    rows = sorted(rows, key=lambda r: (order[r[4]], r[0]))
    by = collections.Counter(r[4] for r in rows)

    def row(g, label, v, tot, st):
        return ('<li class="sma-ck %s"><span class="sma-ck-m" aria-label="%s">%s</span>'
                '<span class="sma-ck-l">%s<small>%s</small></span>'
                '<span class="sma-ck-n dc-num">%s/%s</span></li>'
                % (st, {"pass": "Pass", "warn": "Close", "fail": "Not yet"}[st], CHECK_MARK[st],
                   E(label), E(g), fmt(v), fmt(tot)))

    shown = [r for r in rows if r[4] != "pass"]
    passes = [r for r in rows if r[4] == "pass"]
    lead = passes[:max(0, 8 - len(shown))]
    rest = passes[len(lead):]
    bar = "".join('<i class="sma-lv %s" style="flex-grow:%d"></i>'
                  % ({"pass": "ok", "warn": "warn", "fail": "bad"}[k], by[k])
                  for k in ("fail", "warn", "pass") if by.get(k))
    more = ""
    if rest:
        more = ('<details class="sma-how"><summary>%d more passing</summary>'
                '<ul class="sma-cklist">%s</ul></details>'
                % (len(rest), "".join(row(*r) for r in rest)))
    return ('<section class="sma-panel"><div class="sma-panel-h"><h3 data-dc-no-anchor>Checklist</h3>'
            '<span class="sma-panel-n dc-num">%d of %d pass</span></div>'
            '<div class="sma-lvbar" aria-hidden="true">%s</div>'
            '<ul class="sma-cklist">%s</ul>%s</section>'
            % (by.get("pass", 0), len(rows), bar, "".join(row(*r) for r in shown + lead), more))


LINEAGE_TONES = [("db", "Database"), ("file", "Files and SharePoint"), ("web", "Web and APIs"),
                 ("inline", "Entered data"), ("model", "Lakehouse and models"),
                 ("calc", "DAX (calculated)"), ("mix", "Mixed"), ("measure", "Measures"),
                 ("other", "Other")]


def lineage_block(anchor, title, sub, g, legend_items, flags_note=""):
    """One lineage graph: heading, legend, a scroll box the script draws into, and its data."""
    legend = "".join('<span class="sma-lg"><i class="sma-lg-sw tone-%s"></i>%s</span>' % (k, E(l))
                     for k, l in legend_items)
    if any(n.get("hidden") for n in g["nodes"]):
        legend += '<span class="sma-lg"><i class="sma-lg-sw dashed"></i>Hidden</span>'
    if any(n.get("flag") for n in g["nodes"]):
        legend += '<span class="sma-lg"><i class="sma-lg-sw flagged">!</i>Used by nothing</span>'
    return ('<h2 id="%s">%s</h2><p class="sma-sub">%s</p>'
            '<div class="sma-lin-bar dc-full">%s<span class="sma-fill"></span>'
            '<button type="button" class="dc-btn secondary" data-sma-lin-reset="%s">Reset</button></div>'
            '<div class="sma-lin-box dc-full"><div class="sma-lin" data-sma-lineage="%s-data">'
            '<noscript><p class="dc-caption">The lineage graph needs script.</p></noscript></div></div>'
            '<script type="application/json" id="%s-data">%s</script>'
            % (anchor, E(title), sub + flags_note, legend, anchor, anchor, anchor, json_island(g)))


def lineage_section():
    g = LINEAGE
    if "lineage" not in LANDING:
        return ""
    tones = {n["tone"] for n in g["nodes"]}
    flags = any(n.get("flag") for n in g["nodes"])
    return lineage_block(
        "lineage", "Data lineage",
        "Source to table to measures, left to right. Hover a node to trace it, click to pin, "
        "double-click for its details.",
        g, [(k, l) for k, l in LINEAGE_TONES if k in tones],
        " Flagged nodes (<b>!</b>) are used by nothing in the model; a report visual may still "
        "use them." if flags else "")


def measure_lineage_section():
    g = MLINEAGE
    if "mlineage" not in LANDING:
        return ""
    sub = ("Table to measure to the measures built on them, left to right. Colour is the "
           "display folder. Double-click a measure to open it.")
    if g["shown"] < g["total"]:
        sub += (" Showing %d of %d measures: the most-read and everything they depend on. "
                "The chain explorer on the semantic model page reaches every one."
                % (g["shown"], g["total"]))
    return lineage_block("mlineage", "Measure lineage", sub, g,
                       [("table", "Table")] + list(g["legend"]))


def findings_panel():
    fs = sorted(all_findings(), key=lambda f: (LEVEL_ORDER.get(f["lv"], 3), f["code"], f["subj"]))
    by = collections.Counter(f["lv"] for f in fs)
    total = len(fs) or 1
    bar = "".join('<i class="sma-lv %s" style="flex-grow:%d" title="%d %s"></i>'
                  % (LEVEL_TONE[lv], by[lv], by[lv], lv) for lv in ("error", "warning", "info")
                  if by.get(lv))
    legend_ = "".join('<span class="sma-lv-key"><i class="sma-lv %s"></i>%d %s</span>'
                      % (LEVEL_TONE[lv], by.get(lv, 0), lv) for lv in ("error", "warning", "info"))
    links = " ".join('<a class="dc-btn secondary" href="%s#%s">All %s findings</a>'
                     % (PAGES[k]["file"], "t-findings" if k == "tmdl" else "q-audit",
                        "TMDL" if k == "tmdl" else "Power Query")
                     for k in segment_pages())
    top = fs[:8]
    return ('<section class="sma-panel"><div class="sma-panel-h"><h3 data-dc-no-anchor>Findings</h3>'
            '<span class="sma-panel-n dc-num">%d findings</span></div>'
            '<div class="sma-lvbar" aria-hidden="true">%s</div><div class="sma-lv-legend">%s</div>'
            '<ul class="sma-flist">%s</ul><p class="sma-panel-f">%s</p></section>'
            % (len(fs), bar if fs else "", legend_,
               "".join(finding_row(f) for f in top) or "<li class='sma-none'>No findings.</li>",
               links))


def readiness_panel():
    ai = REPORT["tmdl"]["aiReadiness"]
    sm = ai["summary"]
    pct = round(100 * sm["passed"] / sm["total"]) if sm["total"] else 0
    groups = "".join(
        '<li><a href="%s#ai-%s"><span>%s</span><b class="dc-num">%d/%d</b>'
        '<i class="sma-gbar"><i style="width:%d%%"></i></i></a></li>'
        % (PAGES["ai"]["file"], g["key"], E(g["title"]),
           sum(1 for c in g["checks"] if c["status"] == "pass"), len(g["checks"]),
           round(100 * sum(1 for c in g["checks"] if c["status"] == "pass") / max(1, len(g["checks"]))))
        for g in ai["groups"])
    return ('<section class="sma-panel"><div class="sma-panel-h"><h3 data-dc-no-anchor>AI readiness</h3>'
            '<span class="sma-panel-n dc-num">%d checks</span></div>'
            '<div class="sma-ai-sum"><div class="sma-ring" style="--p:%d" role="img" '
            'aria-label="%d of %d checks pass"><b class="dc-num">%d/%d</b><span>pass</span></div>'
            '<ul class="sma-groups">%s</ul></div>'
            '<p class="sma-panel-f"><a class="dc-btn secondary" href="%s">Open the checklist</a></p></section>'
            % (sm["total"], pct, sm["passed"], sm["total"], sm["passed"], sm["total"], groups,
               PAGES["ai"]["file"]))


def jump_grid():
    cards_ = []
    for k in segment_pages():
        chips = "".join('<a class="sma-chip" href="%s#%s">%s</a>' % (PAGES[k]["file"], a, E(n))
                        for _, a, n in outline_of(k) if a != "about")
        cards_.append('<div class="sma-jcard"><a class="sma-jcard-t" href="%s">%s</a>'
                      '<div class="sma-chips">%s</div></div>'
                      % (PAGES[k]["file"], E(PAGES[k]["nav"]), chips))
    if has_readiness():
        chips = "".join('<a class="sma-chip" href="%s#ai-%s">%s</a>'
                        % (PAGES["ai"]["file"], g["key"], E(g["title"]))
                        for g in REPORT["tmdl"]["aiReadiness"]["groups"])
        cards_.append('<div class="sma-jcard"><a class="sma-jcard-t" href="%s">%s</a>'
                      '<div class="sma-chips">%s</div></div>'
                      % (PAGES["ai"]["file"], E(PAGES["ai"]["nav"]), chips))
    return '<div class="sma-jump dc-full">%s</div>' % "".join(cards_)


def build_index():
    """The landing page: what wins attention first, then the way in, then the method."""
    segs = segment_pages()
    body = [
        '<p class="dc-eyebrow">%s</p>' % E(PAGES["index"]["label"]),
        "<h1>%s</h1>" % E(PAGES["index"]["h1"]),
        subject_block(),
        '<p class="dc-lede">Read from the definition, never the data. %s finds any object.</p>'
        % KBD_FIND,
        '<h2 id="glance">At a glance</h2>',
        stat_cards(),
    ]
    if "highlights" in LANDING:
        body += ['<h2 id="highlights">What stands out</h2>', highlight_cards()]
    health = health_panel()
    if "health" in LANDING:
        body += ['<h2 id="health">Model health</h2>',
                 '<div class="sma-panels dc-full">%s%s</div>'
                 % (health, readiness_panel() if has_readiness() else "")]
    body.append(lineage_section())
    body.append(measure_lineage_section())
    body += ['<h2 id="first">Look at first</h2>',
             '<div class="sma-panels sma-panels-1 dc-full">%s</div>' % findings_panel(),
             '<h2 id="jump">Go to</h2>', jump_grid()]

    method = ['<ul class="sma-facts">%s</ul>' % "".join("<li>%s</li>" % parse_line(k) for k in segs)]
    notes = (REPORT or {}).get("input", {}).get("notes") or []
    if notes:
        method.append('<ul class="sma-facts sma-muted">%s</ul>'
                      % "".join("<li>%s</li>" % E(d) for d in notes))
    if MERGED_AWAY:
        method.append(merged_note())
    body.append('<h2 id="fed">Input</h2>')
    body.append("".join(method))
    if HAS_TMDL:
        body.append('<h2 id="coverage">Coverage</h2>')
        body.append(build_coverage())
    body.append('<h2 id="about">About this analysis</h2>')
    body.append(
        "<p>A run is handed TMDL, Power Query M, or both, and writes one page per segment it "
        "can support. Every page opens offline from <code>file://</code> and makes no network "
        "requests.</p>")
    body.append(
        '<details class="sma-how dc-full"><summary>What a run can emit</summary>'
        '<div class="sma-how-body"><div class="dcv-modes">'
        '<div class="dcv-mode%s"><h4 data-dc-no-anchor>TMDL only</h4>'
        "<p>One <code>.tmdl</code> file, a pasted table, or a PBIP <code>definition</code> "
        "folder.</p></div>"
        '<div class="dcv-mode%s"><h4 data-dc-no-anchor>Power Query only</h4>'
        "<p>A labelled query dump, a section document, or one <code>let … in …</code>."
        "</p></div>"
        '<div class="dcv-mode%s"><h4 data-dc-no-anchor>Both</h4>'
        "<p>A PBIP project, or the two handed over together: one page per segment.</p></div>"
        "</div></div></details>"
        % tuple(" is-run" if segs == want else ""
                for want in (["tmdl"], ["pq"], ["tmdl", "pq"])))
    body.append('<details class="sma-how dc-full"><summary>The rules these pages follow</summary>'
                '<div class="sma-how-body">%s</div></details>' % build_rules())
    return render_shell("index", body)


#: One list, no groups: name, what it is, link, the host shown on the card.
REFERENCES = [
    ("Data Crafters", "Microsoft data and AI consultancy", "https://datacrafters.io",
     "datacrafters.io"),
    ("Data Crafters on LinkedIn", "Company page", "https://www.linkedin.com/company/datacraftersio",
     "LinkedIn"),
    ("Khaled Chowdhury", "Data Crafters", "https://www.linkedin.com/in/khaledchowdhury/",
     "LinkedIn"),
    ("Fabric Data & AI Platform", "Knowledge hub for Microsoft Fabric, data and AI",
     "https://fabricdataaiplatfrom.substack.com/", "Substack"),
    ("Nikola Ilic", "Data Mozart", "https://data-mozart.com/about/", "data-mozart.com"),
    ("Microsoft Learn", "Power BI documentation", "https://learn.microsoft.com/power-bi/",
     "learn.microsoft.com"),
]


def build_refs_page():
    body = [
        '<p class="dc-eyebrow">%s</p>' % E(PAGES["refs"]["label"]),
        "<h1>%s</h1>" % E(PAGES["refs"]["h1"]),
        subject_block(),
        '<p class="dc-lede">The people, places and documentation behind this analyzer.</p>',
        '<h2 id="links" class="dc-sr">Links</h2>',
        '<div class="sma-refs-grid dc-full">%s</div>' % "".join(
            '<a class="sma-refcard" href="%s" target="_blank" rel="noopener noreferrer">'
            '<span class="sma-refcard-n">%s</span><span class="sma-refcard-r">%s</span>'
            '<span class="sma-refcard-u">%s \u2197</span></a>' % (E(url), E(name), E(role), E(host))
            for name, role, url, host in REFERENCES),
    ]
    return render_shell("refs", body)


def view_section(anchor, heading, sub, content, label=None):
    return ('<section class="sma-view dc-full" id="view-%s" data-view="%s" data-label="%s" '
            'aria-labelledby="%s">'
            '<div class="sma-view-head"><h2 id="%s">%s</h2>%s</div>%s</section>'
            % (anchor, anchor, E(label or heading), anchor, anchor, E(heading),
               ('<p class="sma-view-sub">%s</p>' % E(sub)) if sub else "", content))


def view_tabs(views):
    """The section strip. Links, so it works with no script and the rail's anchors
    still land; the script turns it into tabs that show one view at a time."""
    tabs = "".join('<a class="sma-tab" href="#%s" data-view="%s">%s%s</a>'
                   % (a, a, E(label), ('<span class="sma-tab-n dc-num">%s</span>' % n) if n else "")
                   for a, label, n in views)
    return ('<nav class="sma-tabs dc-full" aria-label="Sections of this page">'
            '<div class="sma-tabs-in">%s</div>'
            '<button type="button" class="sma-tab sma-tab-all" data-view-all aria-pressed="false">'
            'Show all</button></nav>' % tabs)


def build_page(seg):
    spec = PAGES[seg]
    anchor, title_, kicker, lede, tiers = part_of(spec["part"])
    fs = [f for f in all_findings() if f["p"] == seg]

    body = [
        '<p class="dc-eyebrow">%s</p>' % E(spec["label"]),
        "<h1>%s</h1>" % E(spec["h1"]),
        subject_block(),
        '<p class="dc-lede">%s</p>' % page_lede(seg),
        '<div class="sma-kpis dc-full" id="%s">%s</div>' % (anchor, lede or ""),
    ]
    views = []
    for t_anchor, heading, _, _ in tiers:
        n = ""
        if t_anchor == "t-findings":
            n = str(len(fs))
        views.append((t_anchor, heading, n))
    if seg == "tmdl" and CATALOGUE:
        views.append(("archive", "Archive", ""))
    if CATALOGUE:
        views.append(("later", "Not drawn", ""))
    views.append(("about", "About", ""))
    body.append(view_tabs(views))

    for t_anchor, heading, t_kicker, content in tiers:
        body.append(view_section(t_anchor, heading, t_kicker, content))

    if seg == "tmdl" and CATALOGUE:
        body.append(view_section(
            "archive", "Archive", "Displaced from the analysis tiers, still rendered.",
            build_archive()))
    if CATALOGUE:
        body.append(view_section(
            "later", "Not drawn here",
            "Derivable from the dataset, not built: %d for this segment."
            % N_NOT_SHOWN[spec["letter"]], build_not_shown(spec["letter"])))

    about = ["<p>%s</p>" % parse_line(seg)] + segment_note(seg)
    if seg == "tmdl":
        about.append(merged_note())
    about.append('<h3 id="cannot" data-dc-no-anchor>What this page cannot claim</h3>')
    about.append(build_cannot(seg))
    body.append(view_section("about", "About this page", "How it was read, and its limits",
                             "".join(about), "About"))
    return render_shell(seg, body)


# ============================================================ AI readiness ====

MARK = {"pass": "✓", "fail": "✗", "na": "–"}
STATUS_WORD = {"pass": "Pass", "fail": "Not yet", "na": "Not in this input"}
FOLLOW = ("This analyzer is a free Data Crafters skill, published at "
          "github.com/DataCraftersLab/craftys-fabric-skills. Follow Data Crafters on "
          "LinkedIn for the next ones.")


def ai_check_row(c):
    count = ""
    if c.get("total"):
        count = '<span class="dcv-check-count dc-num">%s / %s</span>' % (
            fmt(c["count"] or 0), fmt(c["total"]))
    return ('<div class="dcv-check %s" id="chk-%s" data-status="%s" tabindex="-1">'
            '<span class="dcv-check-mark" aria-label="%s">%s</span>'
            '<div class="dcv-check-text"><p class="dcv-check-label">%s</p>'
            '<p class="dcv-check-detail">%s</p></div>%s</div>'
            % (c["status"], E(c["key"]), c["status"], STATUS_WORD[c["status"]], MARK[c["status"]],
               E(c["label"]), E(c["detail"]), count))


def build_ai_page():
    ai = REPORT["tmdl"]["aiReadiness"]
    sm = ai["summary"]
    filters = "".join(
        '<button type="button" class="sma-chip%s" data-ai-filter="%s" aria-pressed="%s">%s'
        '<span class="dc-num">%d</span></button>'
        % (" on" if k == "all" else "", k, "true" if k == "all" else "false", label, n)
        for k, label, n in (("all", "All", sm["total"]), ("fail", "Not yet", sm["failed"]),
                            ("pass", "Pass", sm["passed"]), ("na", "Not in this input", sm["unknown"])))
    body = [
        '<p class="dc-eyebrow">%s</p>' % E(PAGES["ai"]["label"]),
        "<h1>%s</h1>" % E(PAGES["ai"]["h1"]),
        subject_block(),
        '<p class="dc-lede"><b>%d of %d checks pass.</b> Each reads the model\'s own files: '
        "what Copilot and data agents will find.</p>" % (sm["passed"], sm["total"]),
        '<h2 id="score">At a glance</h2>',
        '<div class="sma-dash dc-full"><div class="sma-panels sma-panels-1">%s</div></div>'
        % readiness_panel().replace('<p class="sma-panel-f"><a class="dc-btn" href="%s">Open the '
                                    'checklist</a></p>' % PAGES["ai"]["file"], ""),
        '<div class="sma-ai-filter dc-full" role="group" aria-label="Show checks">%s</div>' % filters,
    ]
    for g in ai["groups"]:
        passed = sum(1 for c in g["checks"] if c["status"] == "pass")
        body.append('<div class="sma-ai-group dc-full" data-ai-group>')
        body.append('<h2 id="ai-%s">%s <span class="sma-h-n dc-num">%d/%d</span></h2>'
                    % (g["key"], E(g["title"]), passed, len(g["checks"])))
        body.append('<p class="dc-caption">%s</p>' % E(g["lede"]))
        body.append('<div class="dcv-checks">%s</div>'
                    % "".join(ai_check_row(c) for c in g["checks"]))
        body.append("</div>")
    body.append('<h2 id="about">About this checklist</h2>')
    body.append('<h3 id="why" data-dc-no-anchor>Why we are doing this</h3>')
    body.append("<p>%s</p>" % E(ai["why"]))
    body.append(note("info", "Follow Data Crafters", E(FOLLOW)))
    body.append('<h3 id="cannot" data-dc-no-anchor>What this page cannot claim</h3>')
    body.append(note(
        "warn", "Caveats",
        "<b>A pass describes the model, not an answer.</b> Nothing here has asked Copilot a "
        "question. <b>Not in this input</b> means the files that would answer were not handed "
        "over; it is not a fail. <b>The AI data schema</b> is not read by this version: confirm "
        "it in Prep data for AI."))
    return render_shell("ai", body)


def render_shell(key, body):
    css = (asset("dc-theme.css") + CSS_EXTRA + asset("dcv-graph.css")
           + asset("dcv-chain.css") + asset("dcv-tabular.css") + asset("sma-app.css"))
    js = asset("dc-doc.js") + "\n" + asset("sma-app.js")
    # The engine must be defined before the per-graph dcvGraph() calls the cards emit
    # inline, and those run as the parser reaches them -- so it goes in the head, not
    # beside dc-doc.js at the foot.
    graph_js = "\n".join([asset("dcv-graph.js"), asset("dcv-chain.js"),
                          asset("dcv-tabular.js")])
    graph_js = (graph_js.replace("</script", "\\/script")
                .replace("<!--", "\\u0021--"))
    logo = asset_bytes("dc-logo-white.svg")
    logo_uri = "data:image/svg+xml;base64," + base64.b64encode(logo).decode("ascii")
    js = js.replace("</script", "<\\/script").replace("<!--", "<\\u0021--")

    return TEMPLATE.format(css=css, js=js, graph_js=graph_js, logo=logo_uri,
                           title=E(title_of(key)), chrome=E(doc_title()),
                           rail=nav_html(key), content="\n".join(body),
                           stats=header_stats(key), page=key,
                           index=json_island(APP or {}))


TEMPLATE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="light dark" />
    <title>{title}</title>
    <!-- Generated by Semantic Model Analyzer. This file is output; edit the source. -->
    <style>{css}</style>
    <noscript>
      <style>
        /* The doc-set tree needs no script, so it stays. Search and the drawer are
           script, so they go, and the rail reverts to sitting above the sheet. */
        .dc-search, .dc-to-top, .dc-toc-trigger {{ display: none; }}
        @media (max-width: 1080px) {{
          .dc-nav {{
            position: static; inset: auto; width: auto; max-height: none;
            transform: none; box-shadow: none;
          }}
        }}
      </style>
    </noscript>
    <script>{graph_js}</script>
  </head>
  <body data-sma-page="{page}">
    <a class="sma-skip" href="#main">Skip to content</a>
    <header class="dc-panel">
      <div class="dc-bar">
        <div class="dc-brand">
          <img src="{logo}" alt="Data Crafters" />
          <span class="dc-rule"></span>
          <span class="dc-title">{chrome}</span>
        </div>
        <div class="dc-search">
          <div class="dc-search-box sma-find-box">
            <span data-dc-search-icon></span>
            <label class="dc-sr" for="sma-find">Find anything in this analysis</label>
            <input id="sma-find" type="search" role="combobox" aria-expanded="false"
              aria-controls="sma-results" aria-autocomplete="list" aria-haspopup="listbox"
              placeholder="Find a measure, table, column, finding"
              autocomplete="off" spellcheck="false" />
            <kbd class="sma-kbd sma-find-kbd" aria-hidden="true">Ctrl K</kbd>
          </div>
          <div class="sma-pop" id="sma-pop" hidden>
            <div class="sma-kinds" role="group" aria-label="Filter by kind" data-sma-kinds></div>
            <ul class="sma-results" id="sma-results" role="listbox" aria-label="Results"></ul>
            <p class="sma-pop-foot"><span><kbd>&uarr;</kbd><kbd>&darr;</kbd> move</span>
              <span><kbd>Enter</kbd> open</span><span><kbd>Tab</kbd> kind</span>
              <span><kbd>Esc</kbd> close</span></p>
          </div>
          <p class="dc-sr" aria-live="polite" data-sma-live></p>
        </div>
        <div class="dc-side">
          <div class="dc-stats" data-dc-stats>{stats}</div>
          <button type="button" class="dc-ghost sma-help-btn" data-sma-help
            aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)">?</button>
          <button type="button" class="dc-ghost dc-toc-trigger" data-dc-toc-trigger
            aria-label="Show contents"></button>
          <button type="button" class="dc-toggle" data-dc-theme
            aria-label="Switch to dark theme" title="Switch to dark theme"><svg viewBox="0 0 24 24" class="dc-icon" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        </div>
      </div>
    </header>

    <div class="dc-shell">
{rail}
      <main class="dc-sheet" id="main" tabindex="-1">
        <div class="sma-findbar" data-sma-findbar hidden>
          <div class="dc-search-box">
            <label class="dc-sr" for="dc-find">Search the text of this page</label>
            <input id="dc-find" type="search" data-dc-search placeholder="Search the text of this page"
              autocomplete="off" spellcheck="false" />
            <span class="dc-search-count dc-num" data-dc-search-count></span>
            <button type="button" class="dc-search-clear" data-dc-search-clear
              aria-label="Clear search"></button>
          </div>
          <span class="sma-findbar-hint"><kbd>Enter</kbd> next <kbd>Shift</kbd>+<kbd>Enter</kbd> previous</span>
          <button type="button" class="dc-ghost" data-sma-findbar-close>Close</button>
        </div>
        <article class="dc-prose">
{content}
        </article>
      </main>
    </div>

    <footer class="dc-footer">
      <span>Data Crafters</span>
      <a href="references.html">References</a>
    </footer>

    <button type="button" class="dc-to-top dc-glass" data-dc-top aria-label="Back to top"></button>

    <aside class="sma-drawer" id="sma-drawer" role="dialog" aria-modal="false"
      aria-labelledby="sma-d-title" hidden>
      <div class="sma-d-head">
        <button type="button" class="sma-icon-btn" data-sma-back aria-label="Back" hidden>&larr;</button>
        <div class="sma-d-titles"><span class="sma-d-kind" data-sma-d-kind></span>
          <p class="sma-d-title" id="sma-d-title" data-sma-d-title></p>
          <p class="sma-d-sub" data-sma-d-sub></p></div>
        <button type="button" class="sma-icon-btn" data-sma-close aria-label="Close">&times;</button>
      </div>
      <div class="sma-d-body" data-sma-d-body></div>
    </aside>

    <div class="sma-help" data-sma-helpdlg role="dialog" aria-modal="true" aria-labelledby="sma-help-t" hidden>
      <div class="sma-help-card">
        <p class="sma-help-title" id="sma-help-t">Keyboard shortcuts</p>
        <dl>
          <dt><kbd>Ctrl</kbd> <kbd>K</kbd> or <kbd>/</kbd></dt><dd>Find any object, finding or section</dd>
          <dt><kbd>[</kbd> <kbd>]</kbd></dt><dd>Previous or next section</dd>
          <dt><kbd>1</kbd> to <kbd>9</kbd></dt><dd>Go to section by number</dd>
          <dt><kbd>A</kbd></dt><dd>Show every section on one page</dd>
          <dt><kbd>Alt</kbd> <kbd>F</kbd></dt><dd>Search the text of this page</dd>
          <dt><kbd>Esc</kbd></dt><dd>Close the finder, the inspector or this list</dd>
          <dt><kbd>?</kbd></dt><dd>This list</dd>
        </dl>
        <button type="button" class="dc-btn" data-sma-help-close>Close</button>
      </div>
    </div>

    <script type="application/json" id="sma-index">{index}</script>

    <script>{js}</script>
  </body>
</html>
"""
