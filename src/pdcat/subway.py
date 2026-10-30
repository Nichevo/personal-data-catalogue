"""The subway map: the data flow drawn as a metro map, one coloured line per table, file or outside source.

Each line follows one piece of data down the page through the scripts and reports that touch it (the
stations): it starts (▶) where the data comes in or is first written, stops at every script or report
that writes or reads it, and ends (■) at the last one. Where several lines meet at one script they run side
by side through the same station, so you can see at a glance what a script takes in and hands on. A script
that reads and writes the same table (an update in place) gets a loop on that line.

Stations go top to bottom in run order (a script comes after the ones that write what it reads), and lines
take the left-most free track, so the layout is worked out automatically: nothing is placed by hand.
The picture is plain SVG made here in Python, so it needs nothing from the internet and saves as a file.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
from typing import Any

from .codemap import Graph

# Colour families by what the line carries: tables purple and blue, files green, outside sources orange.
FAMILIES = {
    "table": ["#5b3fd1", "#3f5bd1", "#8e5bd9", "#2f7fd6", "#6a4fb3", "#4a3fa0"],
    "file": ["#2fa77a", "#1f8f8f", "#5cbf6a", "#3a9fb8", "#4f9f4f", "#2a7f6a"],
    "outside": ["#e0901f", "#d9662f", "#c7a21f", "#d14f6a", "#b8732a", "#e07a5f"],
}
ICONS = {"table": "🛢️", "file": "📄"}

TRACK = 16        # gap between tracks
STATION_ROW = 70  # height of a row with a script or report on it
START_ROW = 34    # height of a row where a line starts
TOP, LEFT = 30, 60


@dataclass
class Line:
    id: str
    label: str
    kind: str
    colour: str = ""
    stops: list[str] = field(default_factory=list)   # station ids, top to bottom
    loops: set[str] = field(default_factory=set)      # stations that read and write it
    dashed: bool = False
    track: int = 0
    top: float = 0
    bottom: float = 0
    key: str | None = None
    rows: int | None = None
    mark: float = 0           # where the name goes: the ▶ for data coming in, or just under the script writing it
    born: bool = False        # first written by one of the scripts here
    ends_here: bool = False   # written and never read again


def _family(kind: str) -> str:
    return kind if kind in ("table", "file") else "outside"


def station_order(stations: set[str], after: dict[str, set[str]], names: dict[str, str]) -> list[str]:
    """Stations in run order: each after everything feeding it (longest path), ties by name.
    Loops between scripts are broken where they are found."""
    rank: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(s: str) -> int:
        if s in rank:
            return rank[s]
        visiting.add(s)
        r = 1 + max((depth(p) for p in sorted(after.get(s, ())) if p not in visiting), default=-1)
        visiting.discard(s)
        rank[s] = r
        return r

    for s in sorted(stations, key=lambda s: names[s].lower()):
        depth(s)
    return sorted(stations, key=lambda s: (rank[s], names[s].lower()))


def layout(g: Graph, rows: dict[str, int] | None = None) -> dict[str, Any]:
    """Work out where every station, line and label goes. Returns plain numbers for the drawing (and tests)."""
    rows = rows or {}
    reads: dict[str, set[str]] = defaultdict(set)   # station -> data
    writes: dict[str, set[str]] = defaultdict(set)
    after: dict[str, set[str]] = defaultdict(set)   # station -> stations that must come first
    stations: set[str] = set()
    for (a, b, rel) in g.edges:
        if rel == "reads":
            reads[b].add(a)
            stations.add(b)
        elif rel == "writes":
            writes[a].add(b)
            stations.add(a)
        elif rel == "runs":
            after[b].add(a)
            stations |= {a, b}
    writers: dict[str, set[str]] = defaultdict(set)
    for s, ds in writes.items():
        for d in ds:
            writers[d].add(s)
    for s, ds in reads.items():
        # Data a script both reads and writes (a run log, a table it updates in place) is a loop on that
        # line, not a reason to run after everything else that writes it.
        for d in ds - writes[s]:
            after[s] |= writers[d] - {s}
    names = {s: g.nodes[s]["label"] for s in stations}
    order = station_order(stations, after, names)
    pos = {s: i for i, s in enumerate(order)}

    lines: dict[str, Line] = {}
    for d in {d for ds in reads.values() for d in ds} | set(writers):
        n = g.nodes[d]
        touched = {s for s in stations if d in reads[s] or d in writes[s]}
        lines[d] = Line(id=d, label=n["label"], kind=n["kind"], stops=sorted(touched, key=pos.get),
                        loops={s for s in touched if d in reads[s] and d in writes[s]},
                        dashed=bool(n.get("uncatalogued")), key=n.get("key"), rows=rows.get(d))
    used: dict[str, int] = defaultdict(int)
    for line in sorted(lines.values(), key=lambda ln: (pos[ln.stops[0]], ln.label.lower())):
        fam = _family(line.kind)
        line.colour = FAMILIES[fam][used[fam] % len(FAMILIES[fam])]
        used[fam] += 1

    # Rows down the page. Data that comes in from outside (nothing here writes it) gets a ▶ row just before
    # the first station that reads it; data a script writes gets its row just after that script, where the
    # line comes out of the station. Then the station itself.
    for line in lines.values():
        line.born = line.id in writers and line.stops[0] in writers[line.id]
        line.ends_here = line.born and len(line.stops) == 1  # written and never read again: an output
    y = TOP
    station_y: dict[str, float] = {}
    starts: list[tuple[float, Line]] = []
    for s in order:
        mine = sorted((ln for ln in lines.values() if ln.stops[0] == s), key=lambda ln: ln.label.lower())
        for line in (ln for ln in mine if not ln.born):
            y += START_ROW
            line.top = line.mark = y
            starts.append((y, line))
        y += STATION_ROW
        station_y[s] = y
        y += 8
        for line in (ln for ln in mine if ln.born):
            y += START_ROW
            line.top, line.mark = station_y[s], y
            starts.append((station_y[s], line))
        y += 8
    for line in lines.values():
        line.bottom = line.mark if line.ends_here else station_y[line.stops[-1]] + 26

    # Tracks: each line takes the left-most track free for its whole length, preferring one next to the
    # lines it meets at its first station so lines that travel together sit together.
    free_from: list[float] = []
    for _, line in starts:
        open_ = [t for t, f in enumerate(free_from) if f < line.top - 12]
        if open_:
            line.track = open_[0]
            free_from[line.track] = line.bottom
        else:
            line.track = len(free_from)
            free_from.append(line.bottom)
    tracks = max(len(free_from), 1)
    label_x = LEFT + tracks * TRACK + 40
    return {"order": order, "names": names, "station_y": station_y, "lines": lines, "starts": starts,
            "tracks": tracks, "label_x": label_x, "height": y + STATION_ROW, "reads": reads, "writes": writes,
            "nodes": g.nodes}


def _short(n: int | None) -> str:
    if n is None:
        return ""
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0", "")
    return str(n)


def _x(track: int) -> float:
    return LEFT + track * TRACK


def _link(key: str | None, inner: str) -> str:
    """Clicking opens the item's page (the app opens whatever ?asset= names)."""
    if not key:
        return inner
    k = escape(key, quote=True)
    return f'<a href="?asset={k}" data-key="{k}">{inner}</a>'


def subway_svg(g: Graph, rows: dict[str, int] | None = None, *, width: int = 900) -> str:
    """The subway map as one SVG document."""
    lay = layout(g, rows)
    lines: dict[str, Line] = lay["lines"]
    sy, lx = lay["station_y"], lay["label_x"]
    out: list[str] = []
    w = max(width, lx + 380)
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {lay["height"]:.0f}" width="{w}" '
               f'height="{lay["height"]:.0f}" font-family="Segoe UI, Inter, Helvetica, Arial, sans-serif">')
    out.append('<defs><pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">'
               '<circle cx="1" cy="1" r="1" fill="#d6d6e4"/></pattern>'
               '<filter id="lift" x="-10%" y="-20%" width="120%" height="150%">'
               '<feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-opacity="0.12"/></filter></defs>')
    out.append('<rect width="100%" height="100%" fill="#f5f5fa"/><rect width="100%" height="100%" fill="url(#dots)"/>')

    # The lines, under everything else.
    for line in lines.values():
        x, c = _x(line.track), line.colour
        dash = ' stroke-dasharray="9 6"' if line.dashed else ""
        if not line.born:  # coming in from outside: a dashed lead-in from the ▶ to the first station
            out.append(f'<path d="M{x},{line.top + 9} L{x},{sy[line.stops[0]]}" stroke="{c}" stroke-width="5" '
                       f'fill="none" stroke-dasharray="9 6" stroke-linecap="round"/>')
        end = line.bottom - (8 if line.ends_here else 9)
        out.append(f'<path d="M{x},{sy[line.stops[0]]} L{x},{end}" stroke="{c}" stroke-width="5" fill="none"'
                   f'{dash} stroke-linecap="round"><title>{escape(line.label)}</title></path>')
        for s in line.loops:  # read and written by the same script: a loop beside the station
            y0 = sy[s]
            out.append(f'<path d="M{x},{y0 - 22} C{x - 34},{y0 - 22} {x - 34},{y0 + 22} {x},{y0 + 22}" '
                       f'stroke="{c}" stroke-width="4" fill="none"/>'
                       f'<path d="M{x + 1},{y0 + 22} l-8,-4 l1,8 z" fill="{c}"/>')
        m = line.mark
        if line.ends_here:  # an output: the line stops at a ■ with its name
            out.append(f'<circle cx="{x}" cy="{m}" r="8" fill="#fff" stroke="{c}" stroke-width="3"/>'
                       f'<rect x="{x - 3.5}" y="{m - 3.5}" width="7" height="7" fill="{c}"/>')
        else:
            out.append(f'<circle cx="{x}" cy="{m}" r="9" fill="#fff" stroke="{c}" stroke-width="3"/>'
                       f'<path d="M{x - 3},{m - 5} L{x + 5},{m} L{x - 3},{m + 5} z" fill="{c}"/>')
            out.append(f'<circle cx="{x}" cy="{line.bottom}" r="7" fill="#fff" stroke="{c}" stroke-width="3"/>'
                       f'<rect x="{x - 3}" y="{line.bottom - 3}" width="6" height="6" fill="{c}"/>')
        icon = ICONS.get(line.kind, "")
        extra = f'  <tspan fill="#777" font-weight="400" font-size="12">{_short(line.rows)} rows</tspan>' \
            if line.rows else ""
        out.append(f'<line x1="{x + 12}" y1="{m}" x2="{lx - 6}" y2="{m}" stroke="{c}" '
                   f'stroke-opacity="0.35" stroke-dasharray="1 4"/>')
        out.append(_link(line.key, f'<text x="{lx}" y="{m + 5}" font-size="15" font-weight="600" '
                                   f'fill="{c}">{escape((icon + " " if icon else "") + line.label)}{extra}</text>'))

    # Stations: one dot per line stopping here, joined like an interchange, and a label card.
    for s in lay["order"]:
        y = sy[s]
        here = [ln for ln in lines.values() if s in ln.stops]
        xs = sorted(_x(ln.track) for ln in here)
        out.append(f'<circle cx="{xs[0]}" cy="{y}" r="22" fill="#000" fill-opacity="0.04"/>')
        out.append(f'<rect x="{xs[0] - 9}" y="{y - 9}" width="{xs[-1] - xs[0] + 18}" height="18" rx="9" '
                   f'fill="#fff" stroke="#3a3a4a" stroke-width="1.5"/>')
        for ln in here:
            out.append(f'<circle cx="{_x(ln.track)}" cy="{y}" r="5" fill="#fff" stroke="{ln.colour}" '
                       f'stroke-width="3"><title>{escape(ln.label)}</title></circle>')
        n = lay["nodes"][s]
        r, wr = len(lay["reads"][s]), len(lay["writes"][s])
        sub = " · ".join(p for p in (f"reads {r}" if r else "", f"writes {wr}" if wr else "",
                                     (n.get("group") or "").lstrip("📁📊 ").strip()) if p)
        title = n["label"].rsplit("/", 1)[-1]
        card_w = max(len(title) * 8.4, len(sub) * 6.6) + 28
        out.append(f'<line x1="{xs[-1] + 10}" y1="{y}" x2="{lx - 4}" y2="{y}" stroke="#3a3a4a" '
                   f'stroke-opacity="0.25"/>')
        out.append(_link(n.get("key"), f'<g filter="url(#lift)"><rect x="{lx - 4}" y="{y - 24}" width="{card_w:.0f}" '
                                       f'height="48" rx="6" fill="#fff" stroke="#e2e2ea"/></g>'
                                       f'<text x="{lx + 8}" y="{y - 4}" font-size="15" font-weight="600" '
                                       f'fill="#1f1f2e">{escape(title)}</text>'
                                       f'<text x="{lx + 8}" y="{y + 14}" font-size="12" fill="#6b6b7b">'
                                       f'{escape(sub)}</text>'))
    out.append("</svg>")
    return "".join(out)


def table_rows(cat) -> dict[str, int]:
    """Row counts of the catalogued tables, shown next to their line's name."""
    import json
    out = {}
    for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind IN ('table', 'view')"):
        n = json.loads(r["attrs"] or "{}").get("row_count")
        if isinstance(n, int | float) and n > 0:
            out[r["key"]] = int(n)
    return out
