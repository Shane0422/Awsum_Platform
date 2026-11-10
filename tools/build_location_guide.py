r"""Builds the Location Master guide image: static/images/platform/ui/location-master-guide.svg.

An illustrated supermarket aisle (perspective gondolas with products) and a warehouse pallet rack, with the three
Location Code parts called out and connected to the finished codes S01-B01-C01 / W01-R02-C01, and the AWSUM
Location policy in the bottom band. AWSUM navy / white style, English, large type (readable when scaled down).

The SVG is a built-in static asset (referenced through builtin_asset_url()); edit this script and regenerate
instead of editing the SVG by hand:

    .venv\Scripts\python -m tools.build_location_guide            # writes the built-in asset
    .venv\Scripts\python -m tools.build_location_guide out.svg    # writes another file (preview)

The output is deterministic (fixed random seed), so an unchanged script reproduces the same file.
"""
import random
import sys
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "static" / "images" / "platform" / "ui" / "location-master-guide.svg"

random.seed(7)
NAVY, BLUE, SKY = "#102e68", "#1263eb", "#0891b2"
PART = (NAVY, BLUE, SKY)
PRODUCT_COLORS = ["#e63946", "#f4a261", "#2a9d8f", "#e9c46a", "#457b9d", "#8ecae6", "#ff6b6b", "#06d6a0",
                  "#ffd166", "#118ab2", "#ef476f", "#90be6d", "#f8961e", "#577590", "#c77dff", "#f94144"]
out = []
w = out.append
f = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")


def poly(points, fill, extra=""):
    w(f'<polygon points="{" ".join(f"{f(x)},{f(y)}" for x, y in points)}" fill="{fill}" {extra}/>')


def badge(x, y, code, label, color, tx, ty, width=168):
    """Callout: rounded badge (code + part name) with a leader line and a dot on the target."""
    ax = x if tx < x else (x + width if tx > x + width else x + width / 2)
    ay = y + 38 if (tx < x or tx > x + width) else (y + 76 if ty > y + 76 else y)
    w(f'<line x1="{f(ax)}" y1="{f(ay)}" x2="{f(tx)}" y2="{f(ty)}" stroke="#ffffff" stroke-width="7" stroke-linecap="round"/>')
    w(f'<line x1="{f(ax)}" y1="{f(ay)}" x2="{f(tx)}" y2="{f(ty)}" stroke="{color}" stroke-width="3.5" stroke-linecap="round"/>')
    w(f'<circle cx="{f(tx)}" cy="{f(ty)}" r="9" fill="#ffffff"/><circle cx="{f(tx)}" cy="{f(ty)}" r="6" fill="{color}"/>')
    w(f'<g filter="url(#shadow)"><rect x="{f(x)}" y="{f(y)}" width="{width}" height="76" rx="14" fill="{color}" stroke="#ffffff" stroke-width="3"/></g>')
    w(f'<text x="{f(x + 18)}" y="{f(y + 42)}" class="bcode">{code}</text>')
    w(f'<text x="{f(x + 18)}" y="{f(y + 64)}" class="blabel">{label}</text>')


# ---------------- Sales Floor: one-point perspective aisle ----------------
def sales_floor():
    near_x, far_x, near_top, far_top, near_bot, far_bot = 18, 300, 44, 168, 440, 236
    k, bays = 0.85, 3

    def t_of(d):
        return (1 - 1 / (1 + d * k)) / (1 - 1 / (1 + bays * k))

    def P(side, d, v):
        t = t_of(d)
        x = near_x + (far_x - near_x) * t
        top = near_top + (far_top - near_top) * t
        bot = near_bot + (far_bot - near_bot) * t
        y = top + (bot - top) * v
        return (x if side == "L" else 740 - x), y

    w('<rect width="740" height="470" fill="#eef2f8"/>')
    # ceiling with light strips
    poly([(0, 0), (740, 0), (740 - near_x, near_top), (740 - far_x, far_top), (far_x, far_top), (near_x, near_top)], "#f7f9fc")
    for d in (0.35, 1.2, 2.3):
        t = t_of(d)
        y = 6 + (far_top - 30) * t
        half = 120 * (1 - t * 0.78)
        poly([(370 - half, y), (370 + half, y), (370 + half * 0.92, y + 10 * (1 - t * 0.6)), (370 - half * 0.92, y + 10 * (1 - t * 0.6))], "#ffffff", 'stroke="#d8e1ee" stroke-width="1"')
    # back wall with distant shelves
    w(f'<rect x="{far_x}" y="{far_top - 50}" width="{740 - 2 * far_x}" height="{far_bot - far_top + 50}" fill="#dfe7f2"/>')
    for i, y in enumerate((far_top - 30, far_top - 4, far_top + 22, far_top + 48)):
        w(f'<rect x="{far_x + 6}" y="{y}" width="{740 - 2 * far_x - 12}" height="3" fill="#b9c6d8"/>')
        for j in range(12):
            w(f'<rect x="{far_x + 8 + j * 11}" y="{y - 12}" width="8" height="12" fill="{PRODUCT_COLORS[(i * 5 + j) % 16]}" opacity=".55"/>')
    # floor with perspective tiles
    poly([(near_x, near_bot), (far_x, far_bot), (740 - far_x, far_bot), (740 - near_x, near_bot), (740, 470), (0, 470)], "url(#floorGrad)")
    vx, vy = 370, 205
    for bx in range(-200, 941, 80):
        w(f'<line x1="{vx}" y1="{vy}" x2="{bx}" y2="470" stroke="#c9d1dc" stroke-width="1.2" clip-path="url(#floorClip)"/>')
    for d in (0.15, 0.45, 0.85, 1.35, 2.0, 2.8):
        y = near_bot + (far_bot - near_bot) * t_of(d) + 18 * (1 - t_of(d))
        w(f'<line x1="0" y1="{f(y)}" x2="740" y2="{f(y)}" stroke="#c9d1dc" stroke-width="1.2" clip-path="url(#floorClip)"/>')

    boards = [0.2, 0.41, 0.62, 0.83, 0.985]
    for side in ("L", "R"):
        # gondola back panel
        poly([P(side, 0, 0.0), P(side, bays, 0.0), P(side, bays, 1.0), P(side, 0, 1.0)], "#d5dde8")
        # header strip
        poly([P(side, 0, 0.0), P(side, bays, 0.0), P(side, bays, 0.07), P(side, 0, 0.07)], NAVY)
        for bay in range(bays):
            for j, board in enumerate(boards):
                top_v = (boards[j - 1] + 0.03) if j else 0.09
                n = 5 if bay == 0 else 4
                for s in range(n):
                    d0 = bay + 0.04 + s * (0.92 / n)
                    d1 = d0 + 0.92 / n - 0.035
                    h = (board - top_v) * random.uniform(0.62, 0.92)
                    col = PRODUCT_COLORS[random.randrange(16)]
                    pts = [P(side, d0, board - h), P(side, d1, board - h), P(side, d1, board), P(side, d0, board)]
                    poly(pts, col)
                    if random.random() < 0.6:  # label band on the pack
                        a, b = board - h * 0.62, board - h * 0.38
                        poly([P(side, d0, a), P(side, d1, a), P(side, d1, b), P(side, d0, b)], "#ffffff", 'opacity=".75"')
            for j, board in enumerate(boards):  # shelf boards with price rails
                poly([P(side, bay, board), P(side, bay + 1, board), P(side, bay + 1, board + 0.022), P(side, bay, board + 0.022)], "#f4f6f9", 'stroke="#9aa8bb" stroke-width=".8"')
        for d in range(bays + 1):  # uprights between bays
            t = t_of(d)
            x0, y0 = P(side, d, 0.0)
            x1, y1 = P(side, d, 1.0)
            wd = 9 * (1 - t * 0.75)
            dx = wd if side == "L" else -wd
            poly([(x0, y0), (x0 + dx, y0), (x1 + dx, y1), (x1, y1)], "#8c99ad")
    # gondola end caps (near)
    poly([(0, 30), (near_x, near_top), (near_x, near_bot), (0, 452)], "#b9c4d4")
    poly([(740, 30), (740 - near_x, near_top), (740 - near_x, near_bot), (740, 452)], "#b9c4d4")
    # hanging aisle sign
    w('<line x1="318" y1="0" x2="318" y2="66" stroke="#7c8aa0" stroke-width="2"/><line x1="422" y1="0" x2="422" y2="66" stroke="#7c8aa0" stroke-width="2"/>')
    w(f'<rect x="282" y="62" width="176" height="62" rx="8" fill="{NAVY}" stroke="#ffffff" stroke-width="3"/>')
    w('<text x="370" y="88" text-anchor="middle" class="signsmall">ZONE</text><text x="370" y="116" text-anchor="middle" class="signbig">S01</text>')
    # highlight: Bay B01 (near bay, left) and Shelf C01 (bottom shelf of that bay)
    poly([P("L", 0.0, 0.08), P("L", 1.0, 0.08), P("L", 1.0, 1.0), P("L", 0.0, 1.0)], "none",
         f'stroke="{BLUE}" stroke-width="5" stroke-dasharray="14 8" stroke-linejoin="round"')
    poly([P("L", 0.02, 0.845), P("L", 0.98, 0.845), P("L", 0.98, 0.985), P("L", 0.02, 0.985)], SKY,
         f'fill-opacity=".38" stroke="{SKY}" stroke-width="4"')
    badge(548, 30, "S01", "Zone", NAVY, 458, 93)
    bx, by = P("L", 0.55, 0.32)
    badge(388, 238, "B01", "Bay", BLUE, bx, by)
    cx, cy = P("L", 0.55, 0.92)
    badge(388, 344, "C01", "Shelf", SKY, cx, cy)


# ---------------- Warehouse: pallet rack row ----------------
def warehouse():
    w('<rect width="740" height="470" fill="#e6ebf2"/>')
    for x in range(0, 741, 92):  # wall panels
        w(f'<rect x="{x}" y="0" width="90" height="380" fill="#edf1f6"/>')
    w('<rect x="0" y="0" width="740" height="26" fill="#c9d3e0"/>')
    for x in (110, 470, 640):  # high-bay lights
        w(f'<line x1="{x}" y1="26" x2="{x}" y2="40" stroke="#7c8aa0" stroke-width="2"/>'
          f'<path d="M{x - 26} 52 L{x - 14} 40 L{x + 14} 40 L{x + 26} 52 Z" fill="#5d6b80"/>'
          f'<ellipse cx="{x}" cy="53" rx="24" ry="4" fill="#fff7d6"/>')
    # floor with W01 zone marking
    w('<rect x="0" y="380" width="740" height="90" fill="url(#whFloor)"/>')
    w('<path d="M30 466 L56 392 L562 392 L588 466" fill="#fde68a" fill-opacity=".35" stroke="#f2b600" stroke-width="6" stroke-linejoin="round"/>')
    w('<text x="309" y="452" text-anchor="middle" class="stencil" transform="translate(309 452) scale(1 .62) translate(-309 -452)">W01</text>')
    xs, top, beams, floor = (72, 230, 388, 546), 112, (112, 202, 292), 380
    dx, dy = 22, -14
    # back uprights and beams (depth)
    for x in xs:
        w(f'<rect x="{x + dx - 6}" y="{top + dy}" width="12" height="{floor - top}" fill="#6f8fc4"/>')
    for y in beams:
        w(f'<rect x="{xs[0] + dx}" y="{y + dy - 6}" width="{xs[-1] - xs[0]}" height="10" fill="#f0a35e"/>')
    # loads: pallets with boxes / wrapped loads
    for bay in range(3):
        for lvl, base in enumerate((floor, beams[2], beams[1])):
            for slot in range(2):
                x0 = xs[bay] + 10 + slot * 72
                pw = 66
                w(f'<rect x="{x0}" y="{base - 14}" width="{pw}" height="12" fill="#c08a4e"/>'
                  f'<rect x="{x0 + 4}" y="{base - 6}" width="14" height="6" fill="#8a5a2b"/><rect x="{x0 + 35}" y="{base - 6}" width="14" height="6" fill="#8a5a2b"/><rect x="{x0 + 66}" y="{base - 6}" width="14" height="6" fill="#8a5a2b"/>')
                hmax = 70
                if (bay + lvl + slot) % 3 == 2:  # shrink-wrapped load
                    w(f'<rect x="{x0 + 2}" y="{base - 14 - hmax}" width="{pw - 4}" height="{hmax}" rx="6" fill="#bcd7f0" stroke="#8fb6dc" stroke-width="2"/>'
                      f'<path d="M{x0 + 6} {base - 30 - hmax * .3} L{x0 + pw - 6} {base - 40 - hmax * .45}" stroke="#ffffff" stroke-width="3" opacity=".8"/>')
                else:  # cardboard cases
                    rows, cols = 3, 2
                    cw, ch = (pw - 4) / cols, hmax / rows
                    for r in range(rows):
                        for c in range(cols):
                            bx0, by0 = x0 + 2 + c * cw, base - 14 - (r + 1) * ch
                            shade = ["#d9a066", "#cf9358", "#e2ad74"][(r + c + bay) % 3]
                            w(f'<rect x="{f(bx0)}" y="{f(by0)}" width="{f(cw - 2)}" height="{f(ch - 2)}" fill="{shade}" stroke="#a8743f" stroke-width="1"/>'
                              f'<rect x="{f(bx0 + cw / 2 - 4)}" y="{f(by0)}" width="6" height="{f(ch - 2)}" fill="#efc896" opacity=".8"/>')
    # front beams and uprights
    for y in beams:
        w(f'<rect x="{xs[0]}" y="{y - 6}" width="{xs[-1] - xs[0]}" height="12" fill="#e8731a" stroke="#b9560c" stroke-width="1"/>')
    for x in xs:
        w(f'<rect x="{x - 7}" y="{top - 8}" width="14" height="{floor - top + 8}" fill="#1f4e9c"/>')
        for hy in range(top, floor, 16):
            w(f'<rect x="{x - 2}" y="{hy}" width="4" height="7" fill="#9fb8e0"/>')
        w(f'<line x1="{x}" y1="{top - 8}" x2="{x + dx}" y2="{top - 8 + dy}" stroke="#1f4e9c" stroke-width="6"/>')
    # bay plates and level tags
    for i, x in enumerate(xs[:-1]):
        w(f'<rect x="{x + 45}" y="{top - 46}" width="68" height="30" rx="5" fill="{NAVY}"/><text x="{x + 79}" y="{top - 24}" text-anchor="middle" class="plate">R0{i + 1}</text>')
    for lvl, y in enumerate((floor - 50, beams[2] - 50, beams[1] - 50)):
        w(f'<rect x="8" y="{y}" width="48" height="26" rx="4" fill="#fde047" stroke="#ca8a04" stroke-width="1.5"/><text x="32" y="{y + 19}" text-anchor="middle" class="tag">C0{lvl + 1}</text>')
    # hanging zone sign
    w('<line x1="240" y1="0" x2="240" y2="14" stroke="#7c8aa0" stroke-width="2"/><line x1="380" y1="0" x2="380" y2="14" stroke="#7c8aa0" stroke-width="2"/>')
    w(f'<rect x="210" y="12" width="200" height="50" rx="8" fill="{NAVY}" stroke="#ffffff" stroke-width="3"/><text x="310" y="46" text-anchor="middle" class="signbig2">ZONE W01</text>')
    # highlight: Rack R02 and Level C01 of it
    w(f'<rect x="{xs[1] - 4}" y="{top - 52}" width="{xs[2] - xs[1] + 8}" height="{floor - top + 56}" rx="8" fill="none" stroke="{BLUE}" stroke-width="5" stroke-dasharray="14 8"/>')
    w(f'<rect x="{xs[1] + 8}" y="{beams[2] + 8}" width="{xs[2] - xs[1] - 16}" height="{floor - beams[2] - 10}" rx="6" fill="{SKY}" fill-opacity=".3" stroke="{SKY}" stroke-width="4"/>')
    badge(562, 58, "W01", "Zone", NAVY, 410, 37, width=166)
    badge(562, 168, "R02", "Rack", BLUE, xs[2] - 6, top + 40, width=166)
    badge(562, 292, "C01", "Level", SKY, xs[2] - 26, floor - 44, width=166)


def scene(fn, x, y, clip_id):
    w(f'<clipPath id="{clip_id}"><rect x="0" y="0" width="740" height="470" rx="14"/></clipPath>')
    w(f'<g transform="translate({x} {y})"><g clip-path="url(#{clip_id})">')
    fn()
    w('</g><rect x="0" y="0" width="740" height="470" rx="14" fill="none" stroke="#c7d3e3" stroke-width="2"/></g>')


def code_row(x, y, parts, labels):
    """The finished code as three colored parts, each connected to what it means."""
    chip, dash = 172, 44
    total = 3 * chip + 2 * dash
    x0 = x + (740 - total) / 2
    w(f'<text x="{x + 370}" y="{y - 14}" text-anchor="middle" class="caption">LOCATION CODE</text>')
    for i, (code, label) in enumerate(zip(parts, labels)):
        cx = x0 + i * (chip + dash)
        w(f'<rect x="{f(cx)}" y="{y}" width="{chip}" height="86" rx="16" fill="{PART[i]}"/>')
        w(f'<text x="{f(cx + chip / 2)}" y="{y + 62}" text-anchor="middle" class="chip">{code}</text>')
        if i < 2:
            w(f'<text x="{f(cx + chip + dash / 2)}" y="{y + 60}" text-anchor="middle" class="dash">-</text>')
        mx = cx + chip / 2
        w(f'<line x1="{f(mx)}" y1="{y + 90}" x2="{f(mx)}" y2="{y + 118}" stroke="{PART[i]}" stroke-width="3.5" marker-end="url(#arrow{i})"/>')
        w(f'<text x="{f(mx)}" y="{y + 146}" text-anchor="middle" class="plabel" fill="{PART[i]}">{label}</text>')


w('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 1084" width="1600" height="1084" role="img" aria-labelledby="t d">')
w('<title id="t">Location Master guide</title>')
w('<desc id="d">A supermarket aisle and a warehouse pallet rack. Sales Floor: Zone S01, Bay B01, Shelf C01 make the Location '
  'Code S01-B01-C01. Warehouse: Zone W01, Rack R02, Level C01 make W01-R02-C01. Each product has one '
  'Sales Location and one Warehouse Location per Store; moving it to another shelf or cooler keeps them; stock moved '
  'between the Warehouse and the Sales Floor is recorded as a Stock Transfer.</desc>')
w('''<defs>
<style>
text { font-family: "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; }
.h1 { font-size: 46px; font-weight: 700; fill: #ffffff; }
.h2 { font-size: 23px; fill: #c9d8f5; }
.ptitle { font-size: 30px; font-weight: 700; fill: #102e68; }
.psub { font-size: 20px; fill: #5a6b85; }
.bcode { font-size: 34px; font-weight: 800; fill: #ffffff; font-family: Consolas, "Cascadia Mono", Menlo, monospace; }
.blabel { font-size: 17px; font-weight: 600; fill: #e6eeff; }
.signsmall { font-size: 15px; font-weight: 700; fill: #c9d8f5; letter-spacing: .18em; }
.signbig { font-size: 30px; font-weight: 800; fill: #ffffff; }
.signbig2 { font-size: 26px; font-weight: 800; fill: #ffffff; letter-spacing: .06em; }
.plate { font-size: 19px; font-weight: 800; fill: #ffffff; font-family: Consolas, Menlo, monospace; }
.tag { font-size: 16px; font-weight: 800; fill: #713f12; font-family: Consolas, Menlo, monospace; }
.stencil { font-size: 92px; font-weight: 900; fill: #f2b600; fill-opacity: .55; font-family: Consolas, Menlo, monospace; }
.caption { font-size: 18px; font-weight: 700; fill: #6b7a90; letter-spacing: .2em; }
.chip { font-size: 56px; font-weight: 800; fill: #ffffff; font-family: Consolas, "Cascadia Mono", Menlo, monospace; }
.dash { font-size: 56px; font-weight: 800; fill: #102e68; font-family: Consolas, Menlo, monospace; }
.plabel { font-size: 25px; font-weight: 700; }
.btitle { font-size: 31px; font-weight: 800; fill: #ffffff; }
.btext { font-size: 20px; fill: #e6eeff; }
.bdot { fill: #8fb0ea; }
</style>
<filter id="shadow" x="-20%" y="-20%" width="140%" height="160%"><feDropShadow dx="0" dy="3" stdDeviation="3" flood-color="#0b1f47" flood-opacity=".35"/></filter>
<linearGradient id="floorGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfe5ec"/><stop offset="1" stop-color="#f3f5f8"/></linearGradient>
<linearGradient id="whFloor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b9c2ce"/><stop offset="1" stop-color="#d7dde5"/></linearGradient>
<clipPath id="floorClip"><polygon points="18,440 300,236 440,236 722,440 740,470 0,470"/></clipPath>''')
for i, c in enumerate(PART):
    w(f'<marker id="arrow{i}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 10 5 0 10z" fill="{c}"/></marker>')
w('</defs>')

w('<rect width="1600" height="1084" rx="28" fill="#ffffff"/>')
w(f'<path d="M0 28 Q0 0 28 0 H1572 Q1600 0 1600 28 V118 H0 Z" fill="{NAVY}"/>')
w('<text x="48" y="66" class="h1">How a Location Code is built</text>')
w('<text x="48" y="100" class="h2">Each Store names its own places. One code = three parts, from the biggest place to the smallest.</text>')

for x, title, sub in ((40, "Sales Floor", "Supermarket aisle"), (820, "Warehouse", "Pallet rack storage")):
    w(f'<rect x="{x}" y="140" width="740" height="742" rx="20" fill="#f6f9fd" stroke="#d5deeb" stroke-width="2"/>')
    w(f'<circle cx="{x + 30}" cy="176" r="11" fill="{NAVY}"/><text x="{x + 52}" y="186" class="ptitle">{title}</text>'
      f'<text x="{x + 52 + len(title) * 17 + 20}" y="185" class="psub">{sub}</text>')
scene(sales_floor, 40, 206, "clipSales")
scene(warehouse, 820, 206, "clipWh")
code_row(40, 722, ("S01", "B01", "C01"), ("Zone", "Bay", "Shelf"))
code_row(820, 722, ("W01", "R02", "C01"), ("Zone", "Rack", "Level"))

# AWSUM Location policy: products move, locations stay
POLICY = (
    "Each product has one Sales Location per Store.",
    "Each product has one Warehouse Location per Store.",
    "Moving it to another shelf or cooler keeps its Location.",
    "Warehouse ↔ Sales Floor moves are Stock Transfers.",
)
w(f'<rect x="40" y="902" width="1520" height="160" rx="18" fill="{NAVY}"/>')
w('<g transform="translate(70 948)">'
  '<rect x="0" y="34" width="44" height="34" rx="4" fill="#e2ad74" stroke="#ffffff" stroke-width="2"/>'
  '<rect x="86" y="34" width="44" height="34" rx="4" fill="none" stroke="#8fb0ea" stroke-width="2.5" stroke-dasharray="6 4"/>'
  '<path d="M18 26 C30 0 98 0 108 24" fill="none" stroke="#ffffff" stroke-width="3.5"/><path d="M100 16 L109 28 L114 13 Z" fill="#ffffff"/>'
  '<rect x="-6" y="70" width="142" height="5" rx="2" fill="#8fb0ea"/></g>')
w('<text x="236" y="944" class="btitle">Products move. Locations stay.</text>')
for i, line in enumerate(POLICY):  # two columns, two rows
    x, y = 236 + (i % 2) * 664, 990 + (i // 2) * 40
    w(f'<circle cx="{x + 7}" cy="{y - 7}" r="5" class="bdot"/><text x="{x + 22}" y="{y}" class="btext">{line}</text>')
w('</svg>')


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    target = Path(argv[0]) if argv else DEFAULT_OUTPUT
    svg = "\n".join(out) + "\n"
    target.write_text(svg, encoding="utf-8")
    print(f"{target} ({len(svg.encode('utf-8'))} bytes)")


if __name__ == "__main__":
    main()
