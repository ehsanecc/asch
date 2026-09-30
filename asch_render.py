#!/usr/bin/env python3
"""Draws an Altium Designer schematic (.SchDoc) or schematic library (.SchLib) as pictures: PNG, or vector PDF at
real size.

Libraries -- every component on its own page / in its own PNG, with its name and description under it:
    python asch_render.py Parts.SchLib                        -> Parts.pdf, one page per component
    python asch_render.py Parts.SchLib images/                -> images/<LIBREF>.png for every component
    python asch_render.py Parts.SchLib R.png --component RES_0805 --scale 4
    python asch_render.py Parts.SchLib --list                 (components, parts, display modes)
  --component NAME (wildcards, repeatable), --mode N / --all-modes (alternate display modes), --no-caption.
  Multi-part components get one page per part (<LIBREF>_A, _B ...).

Schematics:

    python asch_render.py Main.SchDoc                   -> Main.pdf next to it
    python asch_render.py Main.SchDoc out.png out.pdf   -> both
    python asch_render.py Main.SchDoc out.png --scale 3 --no-grid
    python asch_render.py Main.SchDoc dark.pdf --theme kicanvas      (see --list-themes, asch_themes.py)
    python asch_render.py Main.SchDoc x.pdf --theme my-kicad-theme.json --font "Segoe UI"

Handy for looking at a schematic without Altium (e.g. what File > Export Altium Schematic produced). It draws
what the file holds -- sheet, grid, border and zones, title block frame, component graphics, pins (names,
numbers, electrical-type arrows), wires (with hops where they cross unconnected), buses, junctions, net labels,
power ports, ports, parameter sets, blankets, embedded images, text; solid, dashed, dotted and dash-dot lines.
Colours: the file's own (theme "altium"), a built-in theme (KiCad, KiCanvas, Nord, Solarized, Dracula,
Monokai, Eagle, print, blueprint ...) or any KiCad colour theme file. It is a preview, not a pixel copy of
Altium's drawing (fonts and small symbol details differ); the title block's fields are not filled in.

Needs: pip install olefile pillow reportlab   (see requirements.txt; reportlab only for PDF)
"""
import argparse
import math
import os
import sys

import asch_themes
from asch_lib import colorref, open_file

# Altium line widths (Smallest, Small, Medium, Large) in sheet units (10 mil).
LINE_W = {0: 0.4, 1: 1.0, 2: 3.0, 3: 5.0}
DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # orientation 0..3: right, up, left, down
# Altium's pin margins (Preferences > Schematic > General, defaults): a pin's name starts 5 units inside the body,
# its number starts 8 units out from the body and reads towards the pin's end, just above the pin.
PIN_NAME_MARGIN, PIN_NUMBER_MARGIN = 5, 8


def find_font_file(name, bold, italic):
    """A TrueType file for a Windows font name (Arial, Times New Roman, ...), or None."""
    base = {"arial": "arial", "times new roman": "times", "courier new": "cour", "calibri": "calibri",
            "verdana": "verdana", "tahoma": "tahoma", "segoe ui": "segoeui"}.get(name.lower(), name.lower().replace(" ", ""))
    styled = {(False, False): [base], (True, False): [base + "bd", base + "b"],
              (False, True): [base + "i"], (True, True): [base + "bi", base + "z"]}[(bold, italic)]
    folders = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"), "/usr/share/fonts/truetype/msttcorefonts",
               "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    for stem in styled + [base, "arial", "DejaVuSans"]:
        for folder in folders:
            p = os.path.join(folder, stem + ".ttf")
            if os.path.exists(p):
                return p
    return None


class PngCanvas:
    """Pillow back end: `scale` pixels per sheet unit; the picture shows width x height sheet units from (x0, y0)."""

    def __init__(self, width, height, scale, background, x0=0, y0=0):
        from PIL import Image, ImageDraw
        self.Image = Image
        self.S, self.X0, self.Y1 = scale, x0, y0 + height
        self.img = Image.new("RGB", (round(width * scale) + 1, round(height * scale) + 1), background)
        self.g = ImageDraw.Draw(self.img)
        self._fonts = {}

    def P(self, x, y):
        return ((x - self.X0) * self.S, (self.Y1 - y) * self.S)

    def _w(self, width):
        return max(1, round(width * self.S))

    def line(self, pts, color, width=1.0, dash=None):
        """A polyline; `dash` = on / off lengths in sheet units ([4, 2] dashed, [5, 2, 0.6, 2] dash-dot ...)."""
        w = self._w(width)
        if not dash:
            self.g.line([self.P(*p) for p in pts], fill=color, width=w, joint="curve" if w > 2 else None)
            return
        # Walk along the path, switching the pen on and off per the pattern (it carries over the corners).
        k, left, on = 0, dash[0], True
        for a, b in zip(pts, pts[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            t = 0.0
            while t < seg:
                step = min(left, seg - t)
                if on:
                    p0 = (a[0] + (b[0] - a[0]) * t / seg, a[1] + (b[1] - a[1]) * t / seg)
                    p1 = (a[0] + (b[0] - a[0]) * (t + step) / seg, a[1] + (b[1] - a[1]) * (t + step) / seg)
                    self.g.line([self.P(*p0), self.P(*p1)], fill=color, width=w)
                    if w > 2:  # round ends, so a dot reads as a dot
                        for p in (p0, p1):
                            x, y = self.P(*p)
                            self.g.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=color)
                t += step
                left -= step
                if left <= 1e-9:
                    k = (k + 1) % len(dash)
                    left, on = dash[k], k % 2 == 0

    def dots(self, pts, color, size):
        r = max(0.5, size * self.S / 2)
        for p in pts:
            x, y = self.P(*p)
            if r < 1:
                self.g.point((x, y), fill=color)
            else:
                self.g.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def polygon(self, pts, fill, outline, width=1.0):
        self.g.polygon([self.P(*p) for p in pts], fill=fill, outline=outline, width=self._w(width) if outline else 0)

    def rect(self, x0, y0, x1, y1, fill, outline, width=1.0):
        self.g.rectangle([self.P(min(x0, x1), max(y0, y1)), self.P(max(x0, x1), min(y0, y1))], fill=fill,
                         outline=outline, width=self._w(width) if outline else 0)

    def ellipse(self, cx, cy, rx, ry, fill, outline, width=1.0):
        self.g.ellipse([self.P(cx - rx, cy + ry), self.P(cx + rx, cy - ry)], fill=fill, outline=outline,
                       width=self._w(width) if outline else 0)

    def arc(self, cx, cy, r, start, end, color, width=1.0):
        # Altium: counter-clockwise from `start` with Y up; Pillow: clockwise with Y down -- same arc.
        self.g.arc([self.P(cx - r, cy + r), self.P(cx + r, cy - r)], start=-end, end=-start, fill=color, width=self._w(width))

    def font(self, spec):
        name, size, bold, italic = spec
        key = (name, size, bold, italic)
        if key not in self._fonts:
            from PIL import ImageFont
            px = max(4, round(size * self.S))
            path = find_font_file(name, bold, italic)
            self._fonts[key] = ImageFont.truetype(path, px) if path else ImageFont.load_default(px)
        return self._fonts[key]

    def text_width(self, s, spec):
        return self.g.textlength(s, font=self.font(spec)) / self.S

    def baseline(self, spec, va):
        """How far above the anchor the baseline is, in sheet units, for va = bottom / middle / top of the text
        cell (descender to ascender) -- Altium places text by its cell, not by its baseline."""
        ascent, descent = (v / self.S for v in self.font(spec).getmetrics())
        return {"b": descent, "m": (descent - ascent) / 2, "t": -ascent}[va]

    def text(self, x, y, s, spec, color, ha="l", va="b", rot=0):
        if not s:
            return
        anchor = {"l": "l", "c": "m", "r": "r"}[ha] + "s"
        font = self.font(spec)
        up = self.baseline(spec, va) * self.S  # pixels
        if rot % 360 == 0:
            px, py = self.P(x, y)
            self.g.text((px, py - up), s, fill=color, font=font, anchor=anchor)
            return
        # Rotated: draw around the centre of a scratch image, turn it, and put that centre on the anchor point.
        side = int(2 * (self.g.textlength(s, font=font) + font.size) + 8)
        tmp = self.Image.new("RGBA", (side, side), (0, 0, 0, 0))
        from PIL import ImageDraw
        ImageDraw.Draw(tmp).text((side / 2, side / 2 - up), s, fill=color + (255,), font=font, anchor=anchor)
        tmp = tmp.rotate(rot, resample=self.Image.BICUBIC)
        px, py = self.P(x, y)
        self.img.paste(tmp, (round(px - side / 2), round(py - side / 2)), tmp)

    def image(self, pil, x0, y0, x1, y1):
        a, b = self.P(min(x0, x1), max(y0, y1)), self.P(max(x0, x1), min(y0, y1))
        w, h = max(1, round(b[0] - a[0])), max(1, round(b[1] - a[1]))
        im = pil.resize((w, h))
        self.img.paste(im, (round(a[0]), round(a[1])), im)

    def save(self, path):
        self.img.save(path)


class PdfCanvas:
    """reportlab back end: vector PDF at real size (1 sheet unit = 10 mil = 0.72 pt). Each page shows width x height
    sheet units from (x0, y0); new_page() starts another (a library: one page per component)."""
    K = 0.72

    def __init__(self, width, height, background, path, x0=0, y0=0):
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        self.pdfmetrics, self.TTFont = pdfmetrics, TTFont
        self.c = canvas.Canvas(path)
        self._fonts = {}
        self._begin(width, height, background, x0, y0)

    def _begin(self, width, height, background, x0, y0):
        self.c.setPageSize((width * self.K, height * self.K))
        self.c.scale(self.K, self.K)          # draw in sheet units, Y up -- the same as Altium
        self.c.translate(-x0, -y0)
        self.c.setLineCap(1)
        self.c.setLineJoin(1)
        self._fill(background)
        self.c.rect(x0, y0, width, height, stroke=0, fill=1)

    def new_page(self, width, height, background, x0=0, y0=0):
        self.c.showPage()                     # also resets the page's transformation
        self._begin(width, height, background, x0, y0)

    def _stroke(self, color, width=1.0):
        self.c.setStrokeColorRGB(*(v / 255 for v in color))
        self.c.setLineWidth(width)

    def _fill(self, color):
        self.c.setFillColorRGB(*(v / 255 for v in color))

    def _path(self, pts, close=False):
        p = self.c.beginPath()
        p.moveTo(*pts[0])
        for q in pts[1:]:
            p.lineTo(*q)
        if close:
            p.close()
        return p

    def line(self, pts, color, width=1.0, dash=None):
        """See PngCanvas.line."""
        self._stroke(color, width)
        self.c.setDash(dash or [])
        self.c.drawPath(self._path(pts), stroke=1, fill=0)
        self.c.setDash([])

    def dots(self, pts, color, size):
        # One path of zero-length strokes: with round caps each one is a dot -- compact even for a large grid.
        self._stroke(color, size)
        p = self.c.beginPath()
        for x, y in pts:
            p.moveTo(x, y)
            p.lineTo(x, y)
        self.c.drawPath(p, stroke=1, fill=0)

    def polygon(self, pts, fill, outline, width=1.0):
        if fill:
            self._fill(fill)
        if outline:
            self._stroke(outline, width)
        self.c.drawPath(self._path(pts, True), stroke=1 if outline else 0, fill=1 if fill else 0)

    def rect(self, x0, y0, x1, y1, fill, outline, width=1.0):
        if fill:
            self._fill(fill)
        if outline:
            self._stroke(outline, width)
        self.c.rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), stroke=1 if outline else 0, fill=1 if fill else 0)

    def ellipse(self, cx, cy, rx, ry, fill, outline, width=1.0):
        if fill:
            self._fill(fill)
        if outline:
            self._stroke(outline, width)
        self.c.ellipse(cx - rx, cy - ry, cx + rx, cy + ry, stroke=1 if outline else 0, fill=1 if fill else 0)

    def arc(self, cx, cy, r, start, end, color, width=1.0):
        self._stroke(color, width)
        extent = (end - start) % 360 or 360
        self.c.arc(cx - r, cy - r, cx + r, cy + r, startAng=start, extent=extent)

    def font(self, spec):
        """The PDF font for a spec: the real TrueType font when it is installed (any script), else a built-in."""
        name, size, bold, italic = spec
        key = (name, bold, italic)
        if key not in self._fonts:
            path = find_font_file(name, bold, italic)
            font = None
            if path:
                font = "F_" + os.path.splitext(os.path.basename(path))[0]
                try:
                    self.pdfmetrics.getFont(font)
                except KeyError:
                    try:
                        self.pdfmetrics.registerFont(self.TTFont(font, path))
                    except Exception:
                        font = None
            if not font:
                family = "Times" if "times" in name.lower() else "Courier" if "courier" in name.lower() else "Helvetica"
                style = {(False, False): "", (True, False): "-Bold", (False, True): "-Italic", (True, True): "-BoldItalic"}[(bold, italic)]
                if family == "Times" and style == "":
                    style = "-Roman"
                if family != "Times" and "Italic" in style:
                    style = style.replace("Italic", "Oblique")
                font = family + style
            self._fonts[key] = font
        return self._fonts[key], size

    def text_width(self, s, spec):
        font, size = self.font(spec)
        return self.pdfmetrics.stringWidth(s, font, size)

    def baseline(self, spec, va):
        """See PngCanvas.baseline."""
        font, size = self.font(spec)
        ascent, descent = self.pdfmetrics.getAscentDescent(font, size)
        descent = -descent
        return {"b": descent, "m": (descent - ascent) / 2, "t": -ascent}[va]

    def text(self, x, y, s, spec, color, ha="l", va="b", rot=0):
        if not s:
            return
        font, size = self.font(spec)
        w = self.pdfmetrics.stringWidth(s, font, size)
        dx = {"l": 0, "c": -w / 2, "r": -w}[ha]
        dy = self.baseline(spec, va)
        self.c.saveState()
        self.c.translate(x, y)
        self.c.rotate(rot)
        self._fill(color)
        self.c.setFont(font, size)
        self.c.drawString(dx, dy, s)
        self.c.restoreState()

    def image(self, pil, x0, y0, x1, y1):
        from reportlab.lib.utils import ImageReader
        self.c.drawImage(ImageReader(pil), min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), mask="auto")

    def save(self, path):
        self.c.showPage()
        self.c.save()


# Line styles (Altium LineStyle / LineStyleExt: 1 dashed, 2 dotted, 3 dash-dot) as on / off lengths per unit of
# line width, in sheet units.
DASHES = {1: [4.0, 2.5], 2: [0.6, 1.8], 3: [5.0, 2.0, 0.6, 2.0]}
# Fills that only mean "the symbol's body": a theme paints them in its component_body colour. Any other fill
# (an LED's red, a bus bar's grey ...) says something about the part and is kept.
GENERIC_FILLS = {11599871, 16777215, 12632256, 15138815, 14745599}


def arc_points(cx, cy, rx, ry, start=0.0, end=360.0, n=64):
    """An (elliptical) arc as a polyline: counter-clockwise from `start` to `end` degrees."""
    extent = (end - start) % 360 or 360
    steps = max(8, int(n * extent / 360))
    return [(cx + rx * math.cos(math.radians(start + extent * i / steps)),
             cy + ry * math.sin(math.radians(start + extent * i / steps))) for i in range(steps + 1)]


class Renderer:
    def __init__(self, doc, canvas, theme, grid=True, font=None):
        self.doc, self.cv, self.theme, self.grid = doc, canvas, theme, grid
        self.font_override = font or theme.style.get("font")
        self.fonts = doc.fonts()
        self.system_font = self.font(doc.sheet.int("SYSTEMFONT", 1))

    def font(self, font_id):
        spec = self.fonts.get(font_id) or self.fonts.get(self.doc.sheet.int("SYSTEMFONT", 1)) or ("Times New Roman", 10, False, False)
        return (self.font_override,) + tuple(spec[1:]) if self.font_override else spec

    # ---- colours: the object's own (theme "altium") or the theme's colour for the object's role ----
    def col(self, role, o=None, key="COLOR", default=0):
        if self.theme.native:
            return colorref(o.get(key) if o is not None else None, default)
        return self.theme[role]

    def owned_by_part(self, o):
        owner = self.doc.owner(o)
        return owner is not None and owner.type == "1"

    def graphic_role(self, o):
        """Lines, arcs, rectangles ... belong to a part's symbol, or are free drawing on the sheet."""
        return "component_outline" if self.owned_by_part(o) else "note"

    def fill_of(self, o, role):
        """The fill of a solid shape; None when it is not filled."""
        if o.get("ISSOLID") != "T":
            return None
        if self.theme.native:
            return colorref(o.get("AREACOLOR"))
        area, line = o.int("AREACOLOR", 0), o.int("COLOR", 0)
        if area == line:                 # an arrow head, a dot: filled in its line colour
            return self.theme[role]
        if area in GENERIC_FILLS:
            return self.theme["component_body"] if self.owned_by_part(o) else self.theme["background"]
        return colorref(area)

    def dash(self, o, width):
        """The dash pattern of an object's LineStyle (newer files: LineStyleExt), or None for a solid line."""
        style = o.int("LINESTYLEEXT", o.int("LINESTYLE", 0))
        pattern = DASHES.get(style)
        return [v * max(1.0, width) for v in pattern] if pattern else None

    def shape(self, outline_pts, fill, color, width, dash, draw_solid):
        """A filled / outlined shape: drawn by the canvas when solid-lined (`draw_solid`), else filled without an
        outline and then outlined with the dash pattern along `outline_pts`."""
        if not dash:
            draw_solid(fill, color)
            return
        if fill:
            draw_solid(fill, None)
        self.cv.line(outline_pts, color, width, dash)

    # ---- text with Altium's overline marks: a "\" after a character draws a bar over it ----
    def text(self, x, y, s, spec, color, ha="l", va="b", rot=0):
        plain = s.replace("\\", "")
        self.cv.text(x, y, plain, spec, color, ha, va, rot)
        if "\\" not in s:
            return
        w = self.cv.text_width(plain, spec)
        start = {"l": 0, "c": -w / 2, "r": -w}[ha]
        top = self.cv.baseline(spec, va) + 0.76 * spec[1]  # just above the capitals
        cos, sin = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        at, k = start, 0
        while k < len(s):
            ch = s[k]
            over = k + 1 < len(s) and s[k + 1] == "\\"
            cw = self.cv.text_width(ch, spec)
            if over:
                a, b = (at, top), (at + cw, top)
                self.cv.line([(x + a[0] * cos - a[1] * sin, y + a[0] * sin + a[1] * cos),
                              (x + b[0] * cos - b[1] * sin, y + b[0] * sin + b[1] * cos)], color, 0.5)
            at += cw
            k += 2 if over else 1

    # ---- the sheet ----
    def draw_grid(self, x0, y0, x1, y1):
        """The visible grid over x0..x1, y0..y1 (sheet units), if the file and the theme want one."""
        s, cv = self.doc.sheet, self.cv
        grid_style = self.theme.style.get("grid", "lines")
        if not (self.grid and grid_style != "none" and s.get("VISIBLEGRIDON") == "T"):
            return
        gs = max(1, s.int("VISIBLEGRIDSIZE", 10))
        xs = range(gs * (int(x0) // gs + 1), int(math.ceil(x1)), gs)
        ys = range(gs * (int(y0) // gs + 1), int(math.ceil(y1)), gs)
        if self.theme.native:
            grid_color = (232, 232, 222)
        else:  # the theme's grid colour, softened towards the background so it stays in the back
            bg, gc = self.theme["background"], self.theme["grid"]
            grid_color = tuple(round(b + (g - b) * (0.55 if grid_style == "dots" else 0.3)) for b, g in zip(bg, gc))
        if grid_style == "dots":
            cv.dots([(x, y) for x in xs for y in ys], grid_color, 0.9)
        else:
            for x in xs:
                cv.line([(x, y0), (x, y1)], grid_color, 0.3)
            for y in ys:
                cv.line([(x0, y), (x1, y)], grid_color, 0.3)

    def sheet(self):
        name, W, H, XZ, YZ, M = self.doc.sheet_size()
        s, cv = self.doc.sheet, self.cv
        if s.get("BORDERON") == "T":  # inside the border, not in the zone strips
            self.draw_grid(M, M, W - M, H - M)
        else:
            self.draw_grid(0, 0, W, H)
        ink = self.col("worksheet")  # border, zones, title block
        zone_font = (self.font_override or "Arial", 8, False, False)
        if s.get("BORDERON") == "T":
            cv.rect(0, 0, W, H, None, ink, 0.6)
            cv.rect(M, M, W - M, H - M, None, ink, 0.6)
            for i in range(XZ):
                x0, x1 = M + (W - 2 * M) * i / XZ, M + (W - 2 * M) * (i + 1) / XZ
                for yy in (H - M / 2, M / 2):
                    cv.text((x0 + x1) / 2, yy, str(i + 1), zone_font, ink, "c", "m")
                if i:
                    cv.line([(x0, 0), (x0, M)], ink, 0.6)
                    cv.line([(x0, H), (x0, H - M)], ink, 0.6)
            for j in range(YZ):
                y0, y1 = H - M - (H - 2 * M) * j / YZ, H - M - (H - 2 * M) * (j + 1) / YZ
                for xx in (M / 2, W - M / 2):
                    cv.text(xx, (y0 + y1) / 2, chr(ord("A") + j), zone_font, ink, "c", "m")
                if j:
                    cv.line([(0, y0), (M, y0)], ink, 0.6)
                    cv.line([(W, y0), (W - M, y0)], ink, 0.6)
        if s.get("TITLEBLOCKON") == "T":  # the standard title block frame, 350 x 80
            bx, by = W - M - 350, M
            cv.rect(bx, by, W - M, by + 80, None, ink, 0.6)
            for yy in (50, 20, 10):
                cv.line([(bx, by + yy), (W - M, by + yy)], ink, 0.6)
            for x, y0, y1 in ((50, 20, 50), (250, 20, 50), (200, 0, 20)):
                cv.line([(bx + x, by + y0), (bx + x, by + y1)], ink, 0.6)
            for label, x, y in [("Title", 5, 72), ("Size", 5, 42), ("Number", 55, 42), ("Revision", 255, 42),
                                ("Date:", 5, 12), ("File:", 5, 2), ("Sheet of", 205, 12), ("Drawn By:", 205, 2)]:
                cv.text(bx + x, by + y, label, zone_font, ink)

    # ---- a library component's page ----
    def text_box(self, o, text, spec):
        """Rough corners of a text record's box (for page sizing, before any canvas can measure it)."""
        x, y = o.coord("LOCATION.X"), o.coord("LOCATION.Y")
        w, h = 0.6 * spec[1] * len(text.replace("\\", "")), spec[1]
        j = o.int("JUSTIFICATION", 0)
        lx = {0: 0, 1: -w / 2, 2: -w}[j % 3]
        ly = {0: 0, 1: -h / 2, 2: -h}[j // 3]
        a = math.radians(90 * o.int("ORIENTATION", 0))
        return [(x + (dx * math.cos(a) - dy * math.sin(a)), y + (dx * math.sin(a) + dy * math.cos(a)))
                for dx in (lx, lx + w) for dy in (ly, ly + h)]

    def extent(self):
        """(x0, y0, x1, y1) around everything draw() will draw of a library component."""
        pts = []
        for o in self.doc.all_objects():
            if o.get("ISHIDDEN") == "T" or not self.doc.is_shown(o) or o.type in ("1", "34", "44", "45", "46", "48"):
                continue
            if o.type == "41" and not isinstance(o.get("TEXT"), str):
                continue
            if o.type == "2":
                congl = o.int("PINCONGLOMERATE")
                if congl & 4:
                    continue
                x, y, n = o.coord("LOCATION.X"), o.coord("LOCATION.Y"), o.coord("PINLENGTH")
                dx, dy = DIRS[congl & 3]
                pts += [(x, y), (x + dx * n, y + dy * n)]
                continue
            if "TEXT" in o and "LOCATION.X" in o:
                pts += self.text_box(o, o["TEXT"], self.font(o.int("FONTID", 1)))
                continue
            if "LOCATION.X" in o:
                x, y = o.coord("LOCATION.X"), o.coord("LOCATION.Y")
                r = max(o.coord("RADIUS"), o.coord("SECONDARYRADIUS"))
                pts += [(x - r, y - r), (x + r, y + r)]
            if "CORNER.X" in o or o.type in ("13", "14", "30"):
                pts.append((o.coord("CORNER.X"), o.coord("CORNER.Y")))
            pts += o.points()
        if not pts:
            return (0, 0, 10, 10)
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))

    def caption(self, x, y, lines):
        """Name and description under a library component."""
        for k, (text, bold) in enumerate(lines):
            spec = (self.font_override or "Arial", 10 if bold else 8, bold, False)
            self.cv.text(x, y - 13 * k, text, spec, self.col("reference" if bold else "value"), "l", "t")

    # ---- objects ----
    def draw(self, page=None):
        """Draws the sheet, or (a library component) the objects over `page` = (x0, y0, x1, y1)."""
        if self.doc.is_library:
            self.draw_grid(*page)
        else:
            self.sheet()
        self.collect_crossings()
        handlers = {"2": self.pin, "4": self.label, "6": self.polyline, "7": self.polygon, "8": self.ellipse,
                    "10": self.rectangle, "12": self.arc, "13": self.line, "14": self.rectangle, "15": self.sheet_symbol,
                    "17": self.power_port, "18": self.port, "22": self.no_erc, "25": self.label, "26": self.bus,
                    "27": self.wire, "28": self.text_frame, "29": self.junction, "30": self.image, "32": self.label,
                    "33": self.label, "34": self.label, "37": self.bus_entry, "41": self.parameter,
                    "43": self.parameter_set, "225": self.blanket}
        if self.doc.is_library:  # like Altium's library editor: visible parameters are drawn, the designator not
            handlers.pop("34")
        for o in self.doc.all_objects():
            handler = handlers.get(o.type)
            if handler and o.get("ISHIDDEN") != "T" and self.doc.is_shown(o):
                handler(o)

    def width(self, o):
        return LINE_W.get(o.int("LINEWIDTH", 0), 1.0)

    def polyline(self, o):
        pts = o.points()
        if len(pts) >= 2:
            w = self.width(o)
            self.cv.line(pts, self.col(self.graphic_role(o), o), w, self.dash(o, w))

    HOP = 4.0  # cross-over hop radius, sheet units

    def collect_crossings(self):
        """Horizontal wire segments (y, x0, x1) and junction points, for the cross-over hops."""
        self.hsegs, self.junctions = [], set()
        for o in self.doc.all_objects():
            if o.type == "27" and self.doc.is_shown(o):
                pts = o.points()
                for a, b in zip(pts, pts[1:]):
                    if a[1] == b[1] and a[0] != b[0]:
                        self.hsegs.append((a[1], min(a[0], b[0]), max(a[0], b[0])))
            elif o.type == "29":
                self.junctions.add((round(o.coord("LOCATION.X"), 3), round(o.coord("LOCATION.Y"), 3)))

    def wire(self, o):
        pts = o.points()
        c, w = self.col("wire", o), max(1.0, self.width(o))
        dash = self.dash(o, w)
        for a, b in zip(pts, pts[1:]):
            if a[0] != b[0] or a[1] == b[1] or dash:
                self.cv.line([a, b], c, w, dash)
                continue
            # A vertical segment hops (a half circle bulging right) over every horizontal wire it crosses: where
            # neither has an end or corner and there is no junction -- wires that cross there are not connected.
            x, lo, hi = a[0], min(a[1], b[1]), max(a[1], b[1])
            eps = 1e-6
            hops = sorted(y for y, x0, x1 in self.hsegs
                          if x0 + eps < x < x1 - eps and lo + eps < y < hi - eps
                          and (round(x, 3), round(y, 3)) not in self.junctions)
            at = lo
            for i, y in enumerate(hops):
                room = min(y - at, (hops[i + 1] - y) / 2 if i + 1 < len(hops) else hi - y)
                r = max(0.5, min(self.HOP, room))
                self.cv.line([(x, at), (x, y - r)], c, w)
                self.cv.arc(x, y, r, -90, 90, c, w)
                at = y + r
            self.cv.line([(x, at), (x, hi)], c, w)

    def bus(self, o):
        pts = o.points()
        if len(pts) >= 2:
            self.cv.line(pts, self.col("bus", o), 3.0, self.dash(o, 3.0))

    def bus_entry(self, o):
        self.cv.line([(o.coord("LOCATION.X"), o.coord("LOCATION.Y")), (o.coord("CORNER.X"), o.coord("CORNER.Y"))],
                     self.col("bus", o), max(1.0, self.width(o)))

    def blanket(self, o):
        pts = o.points()
        if len(pts) >= 2:
            self.cv.line(pts + [pts[0]], self.col("fields", o), 0.6, self.dash(o, 1.0) or DASHES[1])

    def polygon(self, o):
        pts = o.points()
        if len(pts) >= 2:
            role, w = self.graphic_role(o), self.width(o)
            self.shape(pts + [pts[0]], self.fill_of(o, role), self.col(role, o), w, self.dash(o, w),
                       lambda fill, line: self.cv.polygon(pts, fill, line, w))

    def line(self, o):
        w = self.width(o)
        self.cv.line([(o.coord("LOCATION.X"), o.coord("LOCATION.Y")), (o.coord("CORNER.X"), o.coord("CORNER.Y"))],
                     self.col(self.graphic_role(o), o), w, self.dash(o, w))

    def rectangle(self, o):
        x0, y0, x1, y1 = o.coord("LOCATION.X"), o.coord("LOCATION.Y"), o.coord("CORNER.X"), o.coord("CORNER.Y")
        role, w = self.graphic_role(o), self.width(o)
        self.shape([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], self.fill_of(o, role), self.col(role, o), w,
                   self.dash(o, w), lambda fill, line: self.cv.rect(x0, y0, x1, y1, fill, line, w))

    def ellipse(self, o):
        rx = o.coord("RADIUS")
        ry = o.coord("SECONDARYRADIUS") if "SECONDARYRADIUS" in o else rx
        if rx <= 0 or ry <= 0:  # a flat ellipse is not drawn (library parts use one as an invisible marker)
            return
        cx, cy = o.coord("LOCATION.X"), o.coord("LOCATION.Y")
        role, w = self.graphic_role(o), self.width(o)
        self.shape(arc_points(cx, cy, rx, ry), self.fill_of(o, role), self.col(role, o), w, self.dash(o, w),
                   lambda fill, line: self.cv.ellipse(cx, cy, rx, ry, fill, line, w))

    def arc(self, o):
        cx, cy, r = o.coord("LOCATION.X"), o.coord("LOCATION.Y"), o.coord("RADIUS")
        start, end = o.float("STARTANGLE", 0), o.float("ENDANGLE", 360)
        c, w = self.col(self.graphic_role(o), o), self.width(o)
        dash = self.dash(o, w)
        if dash:
            self.cv.line(arc_points(cx, cy, r, r, start, end), c, w, dash)
        else:
            self.cv.arc(cx, cy, r, start, end, c, w)

    def junction(self, o):
        self.cv.ellipse(o.coord("LOCATION.X"), o.coord("LOCATION.Y"), 2, 2, self.col("junction", o), None)

    def no_erc(self, o):
        x, y, c = o.coord("LOCATION.X"), o.coord("LOCATION.Y"), self.col("no_connect", o, default=255)
        self.cv.line([(x - 3, y - 3), (x + 3, y + 3)], c, 0.8)
        self.cv.line([(x - 3, y + 3), (x + 3, y - 3)], c, 0.8)

    def image(self, o):
        im = self.doc.images.get(o.get("FILENAME"))
        if im is not None:
            self.cv.image(im, o.coord("LOCATION.X"), o.coord("LOCATION.Y"), o.coord("CORNER.X"), o.coord("CORNER.Y"))

    def sheet_symbol(self, o):
        x, y = o.coord("LOCATION.X"), o.coord("LOCATION.Y")
        fill = self.fill_of(o, "sheet")
        if fill is not None and not self.theme.native:
            fill = self.theme["component_body"]
        self.cv.rect(x, y, x + o.coord("XSIZE"), y - o.coord("YSIZE"), fill, self.col("sheet", o), self.width(o))

    def text_frame(self, o):
        x0, y0, x1, y1 = o.coord("LOCATION.X"), o.coord("LOCATION.Y"), o.coord("CORNER.X"), o.coord("CORNER.Y")
        self.cv.rect(x0, y0, x1, y1, self.fill_of(o, "note"),
                     self.col("note", o) if o.get("SHOWBORDER") == "T" else None, 0.6)
        spec = self.font(o.int("FONTID", 1))
        for i, row in enumerate(o.get("TEXT", "").replace("~1", "\n").split("\n")):
            self.cv.text(min(x0, x1) + 2, max(y0, y1) - 2 - (i + 1) * spec[1], row, spec, self.col("note", o, "TEXTCOLOR"))

    # Text records and the role their colour plays: a free label is a note (inside a symbol: the symbol's own
    # text), a net label a local label, a designator a reference, a sheet symbol's name / file a sheet.
    TEXT_ROLES = {"25": "label_local", "34": "reference", "32": "sheet", "33": "sheet", "41": "value"}

    def label(self, o):  # label, net label, designator, sheet name / file name, parameter: text at a point
        if "TEXT" not in o or "LOCATION.X" not in o:
            return
        role = self.TEXT_ROLES.get(o.type) or ("pin_name" if self.owned_by_part(o) else "note")
        # Justification 0..8 = bottom / centre / top rows of left, centre, right.
        j = o.int("JUSTIFICATION", 0)
        ha, va = "lcr"[j % 3], "bmt"[j // 3]
        self.text(o.coord("LOCATION.X"), o.coord("LOCATION.Y"), o["TEXT"], self.font(o.int("FONTID", 1)),
                  self.col(role, o), ha, va, 90 * o.int("ORIENTATION", 0))

    def parameter(self, o):
        owner = self.doc.owner(o)
        if owner is not None and owner.type not in ("1", "15"):  # parameters of models, pins, ...: not drawn
            return
        self.label(o)

    def parameter_set(self, o):  # a directive: an "i" in a circle on a short stem, and its name
        x, y, c = o.coord("LOCATION.X"), o.coord("LOCATION.Y"), self.col("fields", o, default=255)
        self.cv.line([(x, y), (x + 4, y)], c, 1.0)
        self.cv.ellipse(x + 9, y, 5, 5, None, c, 1.0)
        self.cv.text(x + 9, y, "i", self.system_font, c, "c", "m")
        self.cv.text(x + 16, y, o.get("NAME", ""), self.system_font, c, "l", "m")

    def power_port(self, o):
        x, y = o.coord("LOCATION.X"), o.coord("LOCATION.Y")
        rot = o.int("ORIENTATION") % 4
        d = DIRS[rot]
        p = (-d[1], d[0])
        # Drawn like a KiCad power symbol: the symbol in the part outline colour, its name as a value.
        c, style = self.col("component_outline", o), o.int("STYLE")
        text_color = self.col("value", o)

        def at(a, b):  # `a` along the port's direction, `b` across it
            return (x + d[0] * a + p[0] * b, y + d[1] * a + p[1] * b)

        end = 10
        if style == 0:        # circle
            self.cv.line([at(0, 0), at(6, 0)], c)
            cx, cy = at(8, 0)
            self.cv.ellipse(cx, cy, 2, 2, None, c)
        elif style == 1:      # arrow
            self.cv.line([at(0, 0), at(7, 0)], c)
            self.cv.polygon([at(7, -3), at(7, 3), at(10, 0)], None, c)
        elif style == 4:      # power ground
            self.cv.line([at(0, 0), at(10, 0)], c)
            for k, half in enumerate((10, 7, 4, 1)):
                self.cv.line([at(10 + 3 * k, -half), at(10 + 3 * k, half)], c)
            end = 19
        elif style == 5:      # signal ground
            self.cv.line([at(0, 0), at(10, 0)], c)
            self.cv.polygon([at(10, -7), at(10, 7), at(17, 0)], None, c)
            end = 17
        elif style == 6:      # earth
            self.cv.line([at(0, 0), at(10, 0)], c)
            self.cv.line([at(10, -7), at(10, 7)], c)
            for k in range(3):
                self.cv.line([at(10, -7 + 7 * k), at(15, -10 + 7 * k)], c)
            end = 15
        else:                 # bar (2), wave (3) and anything else
            self.cv.line([at(0, 0), at(10, 0)], c)
            self.cv.line([at(10, -5), at(10, 5)], c, 1.5)
        if o.get("SHOWNETNAME", "T") == "T":
            tx, ty = at(end + 3, 0)
            ha, va = [("l", "m"), ("c", "b"), ("r", "m"), ("c", "t")][rot]
            self.text(tx, ty, o.get("TEXT", ""), self.font(o.int("FONTID", 1)), text_color, ha, va)

    def port(self, o):  # drawn like a KiCad global label
        x, y = o.coord("LOCATION.X"), o.coord("LOCATION.Y")
        w, h = o.coord("WIDTH") or 50, o.coord("HEIGHT") or 10
        style = o.int("STYLE")
        line, text_color = self.col("label_global", o), self.col("label_global", o, "TEXTCOLOR")
        fill = self.col("component_body", o, "AREACOLOR")
        if style >= 4:  # vertical port
            self.cv.rect(x - h / 2, y, x + h / 2, y + w, fill, line)
            self.text(x, y + w / 2, o.get("NAME", ""), self.font(o.int("FONTID", 1)), text_color, "c", "m", 90)
            return
        # Style 1 points left, 2 right, 3 both ends (0: a plain box); a point is half the height deep.
        kl = h / 2 if style in (1, 3) else 0
        kr = h / 2 if style in (2, 3) else 0
        left = [(x, y), (x + kl, y + h / 2)] if kl else [(x, y - h / 2), (x, y + h / 2)]
        right = ([(x + w - kr, y + h / 2), (x + w, y), (x + w - kr, y - h / 2)] if kr
                 else [(x + w, y + h / 2), (x + w, y - h / 2)])
        self.cv.polygon(left + right + ([(x + kl, y - h / 2)] if kl else []), fill, line)
        self.text(x + w / 2, y, o.get("NAME", ""), self.font(o.int("FONTID", 1)), text_color, "c", "m")

    def pin(self, o):
        x, y = o.coord("LOCATION.X"), o.coord("LOCATION.Y")
        length = o.coord("PINLENGTH")
        congl = o.int("PINCONGLOMERATE")
        rot = congl & 3
        if congl & 4:  # hidden pin
            return
        dx, dy = DIRS[rot]
        c = self.col("pin", o)
        name_color, number_color = self.col("pin_name", o), self.col("pin_number", o)
        self.cv.line([(x, y), (x + dx * length, y + dy * length)], c)
        f = self.system_font
        # Electrical type 0 input, 1 in/out, 2 output: small arrows just outside the body.
        el = o.int("ELECTRICAL", 4)
        arrows = {0: [(-1, 6)], 2: [(1, 2)], 1: [(-1, 6), (1, 2)]}.get(el, [])
        for sense, off in arrows:
            tip = off + (0 if sense < 0 else 4)
            base = tip - sense * 4
            self.cv.polygon([(x + dx * tip, y + dy * tip), (x + dx * base - dy * 2, y + dy * base + dx * 2),
                             (x + dx * base + dy * 2, y + dy * base - dx * 2)], None, c, 0.5)
        horizontal = rot in (0, 2)
        if congl & 8:   # name, inside the body
            name = o.get("NAME", "")
            m = PIN_NAME_MARGIN
            if horizontal:
                self.text(x - dx * m, y, name, f, name_color, "r" if rot == 0 else "l", "m")
            else:
                self.text(x, y - dy * m, name, f, name_color, "r" if rot == 1 else "l", "m", 90)
        if congl & 16:  # number, along the pin outside the body
            number, m = o.get("DESIGNATOR", ""), PIN_NUMBER_MARGIN
            if horizontal:
                self.text(x + dx * m, y, number, f, number_color, "l" if rot == 0 else "r", "b")
            else:
                self.text(x, y + dy * m, number, f, number_color, "l" if rot == 1 else "r", "b", 90)


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")


def render_sheet(doc, args, theme):
    name, W, H, *_ = doc.sheet_size()
    background = colorref(doc.sheet.get("AREACOLOR"), 0xFFFFFF) if theme.native else theme["background"]
    for out in args.outputs or [os.path.splitext(args.file)[0] + ".pdf"]:
        ext = os.path.splitext(out)[1].lower()
        if ext == ".pdf":
            cv = PdfCanvas(W, H, background, out)
        elif ext in IMAGE_EXTS:
            cv = PngCanvas(W, H, args.scale, background)
        else:
            sys.exit(f"{out}: unknown output type (use .png or .pdf)")
        Renderer(doc, cv, theme, grid=not args.no_grid, font=args.font).draw()
        cv.save(out)
        print(f"{out}: {name} sheet, {W} x {H} units, {len(doc.all_objects())} objects, theme {theme.name}")


def library_views(lib, args):
    """(component, part, display mode) for every page to draw, after --component / --mode / --all-modes."""
    import fnmatch
    comps = [c for c in lib.components
             if not args.component or any(fnmatch.fnmatch(c.name.upper(), p.upper()) for p in args.component)]
    views = []
    for c in comps:
        modes = range(c.mode_count) if args.all_modes else [args.mode if args.mode < c.mode_count else 0]
        views += [(c, part, mode) for part in range(1, c.part_count + 1) for mode in modes]
    return views


def view_name(comp, part, mode):
    """File-name-safe name of a page: LIBREF, plus _<part letter> for a multi-part part, _mode<n> for mode n > 0."""
    name = comp.name + (f"_{chr(ord('A') + part - 1)}" if comp.part_count > 1 else "") + (f"_mode{mode}" if mode else "")
    return "".join("_" if ch in '<>:"/\\|?*' else ch for ch in name)


def render_library(lib, args, theme):
    views = library_views(lib, args)
    if args.list:
        for c in lib.components:
            print(f"{c.name:<34} parts {c.part_count}  modes {c.mode_count}  {c.description}")
        return
    if not views:
        sys.exit("no component matches " + ", ".join(args.component or []))
    background = colorref(lib.header.get("AREACOLOR"), 0xFFFFFF) if theme.native else theme["background"]
    margin, caption_h = 20, 0 if args.no_caption else 30

    def title_of(comp, mode):
        return comp.name + (f"  (part {comp.part_name()})" if comp.part_count > 1 else "") + (f"  mode {mode}" if mode else "")

    def page_of(comp, part, mode, cv=None):
        """Page rectangle around the drawing (grid-aligned), with room for the caption below."""
        comp.part, comp.mode = part, mode
        x0, y0, x1, y1 = Renderer(comp, cv, theme, font=args.font).extent()
        g = 10
        x0, y0 = math.floor((x0 - margin) / g) * g, math.floor((y0 - margin - caption_h) / g) * g
        x1, y1 = math.ceil((x1 + margin) / g) * g, math.ceil((y1 + margin) / g) * g
        if caption_h:  # wide enough for the caption (bold 10 / plain 8, roughly 0.65 / 0.55 of the size per letter)
            x1 = max(x1, x0 + math.ceil((20 + max(6.5 * len(title_of(comp, mode)), 4.4 * len(comp.description))) / g) * g)
        return x0, y0, x1, y1

    def draw(comp, part, mode, cv, page):
        comp.part, comp.mode = part, mode
        r = Renderer(comp, cv, theme, grid=not args.no_grid, font=args.font)
        r.draw(page)
        if caption_h:
            r.caption(page[0] + 10, page[1] + caption_h - 2, [(title_of(comp, mode), True), (comp.description, False)])

    for out in args.outputs or [os.path.splitext(args.file)[0] + ".pdf"]:
        ext = os.path.splitext(out)[1].lower()
        if ext == ".pdf":  # every component on its own page, at real size
            cv = None
            for comp, part, mode in views:
                page = page_of(comp, part, mode)
                w, h = page[2] - page[0], page[3] - page[1]
                if cv is None:
                    cv = PdfCanvas(w, h, background, out, page[0], page[1])
                else:
                    cv.new_page(w, h, background, page[0], page[1])
                draw(comp, part, mode, cv, page)
            cv.save(out)
            print(f"{out}: {len(views)} pages, theme {theme.name}")
        elif ext in IMAGE_EXTS and len(views) == 1:
            comp, part, mode = views[0]
            page = page_of(comp, part, mode)
            cv = PngCanvas(page[2] - page[0], page[3] - page[1], args.scale, background, page[0], page[1])
            draw(comp, part, mode, cv, page)
            cv.save(out)
            print(f"{out}: {view_name(comp, part, mode)}, theme {theme.name}")
        elif ext in IMAGE_EXTS:
            sys.exit(f"{out}: {len(views)} components to draw -- give a folder for one PNG each, or pick one with --component")
        else:  # a folder: one PNG per component (part, mode)
            os.makedirs(out, exist_ok=True)
            for comp, part, mode in views:
                page = page_of(comp, part, mode)
                cv = PngCanvas(page[2] - page[0], page[3] - page[1], args.scale, background, page[0], page[1])
                draw(comp, part, mode, cv, page)
                cv.save(os.path.join(out, view_name(comp, part, mode) + ".png"))
            print(f"{out}: {len(views)} PNG files, theme {theme.name}")


def main():
    ap = argparse.ArgumentParser(description="Draw an Altium schematic (.SchDoc) or schematic library (.SchLib) "
                                             "as PNG or vector PDF.")
    ap.add_argument("file", help="the .SchDoc or .SchLib file")
    ap.add_argument("outputs", nargs="*",
                    help="schematic: .png and/or .pdf files. Library: a .pdf (one page per component), a folder (one "
                         ".png per component) or a .png (with one --component). Default: the file's name .pdf")
    ap.add_argument("--scale", type=float, default=1.5, help="PNG pixels per sheet unit (10 mil); default 1.5")
    ap.add_argument("--no-grid", action="store_true", help="leave out the visible grid")
    ap.add_argument("--theme", default="altium",
                    help="colour theme: altium (the file's own colours, default), " + ", ".join(asch_themes.names()[1:])
                    + " -- or a KiCad 6+ colour theme .json file")
    ap.add_argument("--font", help="draw all text in this font (e.g. Arial) instead of the file's fonts")
    ap.add_argument("--list-themes", action="store_true", help="list the built-in themes and exit")
    lib = ap.add_argument_group("libraries (.SchLib)")
    lib.add_argument("--list", action="store_true", help="list the library's components and exit")
    lib.add_argument("--component", action="append", metavar="NAME",
                     help="only this component (wildcards allowed, e.g. \"7SEG_4*\"); may be repeated")
    lib.add_argument("--mode", type=int, default=0, help="display mode to draw (0 = normal, 1.. = alternates)")
    lib.add_argument("--all-modes", action="store_true", help="draw every display mode of each component")
    lib.add_argument("--no-caption", action="store_true", help="leave out the name / description under each component")
    args = ap.parse_args()
    if args.list_themes:
        print("\n".join(asch_themes.names()))
        return
    try:
        theme = asch_themes.load(args.theme)
    except (ValueError, OSError) as e:
        sys.exit(str(e))
    doc = open_file(args.file)
    if doc.is_library:
        render_library(doc, args, theme)
    else:
        render_sheet(doc, args, theme)


if __name__ == "__main__":
    main()
