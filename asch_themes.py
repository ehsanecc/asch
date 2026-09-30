"""Colour themes for asch_render.py.

A theme uses KiCad's schematic colour names (background, wire, bus, junction, component_outline, component_body,
pin, pin_name, pin_number, reference, value, fields, label_local, label_global, label_hier, note, sheet,
no_connect, worksheet, grid), so a KiCad colour theme file (KiCad 6+ JSON, "schematic" section) can be used as it
is: asch_render.py --theme my-theme.json. The renderer gives every Altium object one of these roles (a wire is
"wire", a designator "reference", a power port "component_outline" + "value" like a KiCad power symbol, ...).

Built-in palettes and where they come from:
  kicad            KiCad's default schematic colours (as KiCanvas ships them)
                   https://github.com/theacodes/kicanvas/blob/main/src/kicanvas/themes/kicad-default.ts
  kicanvas         "Witch Hazel", the dark theme of the KiCanvas web viewer (kicanvas.org)
                   https://github.com/theacodes/kicanvas/blob/main/src/kicanvas/themes/witch-hazel.ts
  nord, solarized-light, solarized-dark, monokai, eagle-dark, behave-dark
                   the KiCad community colour schemes collection  https://github.com/pointhi/kicad-color-schemes
  dracula          the official Dracula theme for KiCad  https://github.com/dracula/kicad
  print, blueprint this project's own: black on white for paper (no fills that vanish in a photocopy), and white
                   on blueprint blue.
Style (besides colours): the grid ("lines", "dots" as in KiCad, or "none") and an optional font that replaces the
schematic's own fonts (the KiCad-derived themes use a sans font, closer to KiCad's stroke font).
"""
import json
import os
import re

THEMES = {
    "kicad": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#f5f4ef", "worksheet": "#840000", "grid": "#b5b5b5", "wire": "#009600",
            "bus": "#000084", "junction": "#009600", "component_outline": "#840000", "component_body": "#ffffc2",
            "pin": "#840000", "pin_name": "#006464", "pin_number": "#a90000", "reference": "#006464",
            "value": "#006464", "fields": "#840084", "label_local": "#0f0f0f", "label_global": "#840000",
            "label_hier": "#725600", "note": "#0000c2", "sheet": "#840000", "no_connect": "#000084",
        },
    },
    "kicanvas": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#131218", "worksheet": "#64becb", "grid": "#716799", "wire": "#ae81ff",
            "bus": "#81eeff", "junction": "#dcc8ff", "component_outline": "#c5a3ff", "component_body": "#433e56",
            "pin": "#81ffbe", "pin_name": "#81ffbe", "pin_number": "#64cb96", "reference": "#81eeff",
            "value": "#81eeff", "fields": "#ae81ff", "label_local": "#dcc8ff", "label_global": "#fff781",
            "label_hier": "#a3ffcf", "note": "#f8f8f0", "sheet": "#ae81ff", "no_connect": "#ff81ad",
        },
    },
    "nord": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#eceff4", "worksheet": "#3b4252", "grid": "#4c566a", "wire": "#a3be8c",
            "bus": "#81a1c1", "junction": "#a3be8c", "component_outline": "#4c566a", "component_body": "#e5e9f0",
            "pin": "#4c566a", "pin_name": "#434c5e", "pin_number": "#434c5e", "reference": "#5e81ac",
            "value": "#5e81ac", "fields": "#5e81ac", "label_local": "#434c5e", "label_global": "#bf616a",
            "label_hier": "#ebcb8b", "note": "#5e81ac", "sheet": "#b48ead", "no_connect": "#81a1c1",
        },
    },
    "solarized-light": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#fdf6e3", "worksheet": "#dc322f", "grid": "#586e75", "wire": "#859900",
            "bus": "#268bd2", "junction": "#859900", "component_outline": "#586e75", "component_body": "#eee8d5",
            "pin": "#586e75", "pin_name": "#cb4b16", "pin_number": "#b58900", "reference": "#268bd2",
            "value": "#6c71c4", "fields": "#93a1a1", "label_local": "#2aa198", "label_global": "#2aa198",
            "label_hier": "#2aa198", "note": "#586e75", "sheet": "#586e75", "no_connect": "#6c71c4",
        },
    },
    "solarized-dark": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#002b36", "worksheet": "#dc322f", "grid": "#93a1a1", "wire": "#859900",
            "bus": "#268bd2", "junction": "#859900", "component_outline": "#93a1a1", "component_body": "#073642",
            "pin": "#93a1a1", "pin_name": "#cb4b16", "pin_number": "#b58900", "reference": "#268bd2",
            "value": "#6c71c4", "fields": "#586e75", "label_local": "#2aa198", "label_global": "#2aa198",
            "label_hier": "#2aa198", "note": "#93a1a1", "sheet": "#93a1a1", "no_connect": "#6c71c4",
        },
    },
    "dracula": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#282936", "worksheet": "#b45bcf", "grid": "#44475a", "wire": "#00f769",
            "bus": "#62d6e8", "junction": "#00f769", "component_outline": "#ea51b2", "component_body": "#4d4f68",
            "pin": "#ea51b2", "pin_name": "#00f769", "pin_number": "#ea51b2", "reference": "#a1efe4",
            "value": "#a1efe4", "fields": "#a1efe4", "label_local": "#ebff87", "label_global": "#ff79c6",
            "label_hier": "#ebff87", "note": "#62d6e8", "sheet": "#b45bcf", "no_connect": "#62d6e8",
        },
    },
    "monokai": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#3b3a32", "worksheet": "#635778", "grid": "#96947f", "wire": "#a6e22e",
            "bus": "#66d9ef", "junction": "#a6e22e", "component_outline": "#f92672", "component_body": "#49483e",
            "pin": "#f92672", "pin_name": "#66d9ef", "pin_number": "#f92672", "reference": "#66d9ef",
            "value": "#66d9ef", "fields": "#ae81ff", "label_local": "#f8f8f2", "label_global": "#f92672",
            "label_hier": "#ffe792", "note": "#fd971f", "sheet": "#ae81ff", "no_connect": "#66d9ef",
        },
    },
    "eagle-dark": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#212121", "worksheet": "#c00000", "grid": "#3c3c3c", "wire": "#00a000",
            "bus": "#0060c0", "junction": "#00a000", "component_outline": "#c00000", "component_body": "#2c2c2c",
            "pin": "#c00000", "pin_name": "#c0c0c0", "pin_number": "#c00000", "reference": "#c0c0c0",
            "value": "#c0c0c0", "fields": "#8000a0", "label_local": "#c0c0c0", "label_global": "#00a0e0",
            "label_hier": "#a0a000", "note": "#c0c000", "sheet": "#8000a0", "no_connect": "#612be0",
        },
    },
    "behave-dark": {
        "style": {"grid": "dots", "font": "Arial"},
        "colors": {
            "background": "#232932", "worksheet": "#c4626b", "grid": "#848484", "wire": "#8dd272",
            "bus": "#4487aa", "junction": "#8dd272", "component_outline": "#c4626b", "component_body": "#513c56",
            "pin": "#c4626b", "pin_name": "#669696", "pin_number": "#c4626b", "reference": "#a683e1",
            "value": "#669696", "fields": "#786596", "label_local": "#4487aa", "label_global": "#c46c2d",
            "label_hier": "#c3ae72", "note": "#4487aa", "sheet": "#856fa5", "no_connect": "#95dbdf",
        },
    },
    # Paper: everything black on white, bodies unfilled -- survives photocopies and colour blindness.
    "print": {
        "style": {"grid": "none", "font": None},
        "colors": {
            "background": "#ffffff", "worksheet": "#000000", "grid": "#d0d0d0", "wire": "#000000",
            "bus": "#000000", "junction": "#000000", "component_outline": "#000000", "component_body": "#ffffff",
            "pin": "#000000", "pin_name": "#000000", "pin_number": "#404040", "reference": "#000000",
            "value": "#404040", "fields": "#404040", "label_local": "#000000", "label_global": "#000000",
            "label_hier": "#000000", "note": "#000000", "sheet": "#000000", "no_connect": "#000000",
        },
    },
    # Blueprint: white and pale cyan lines on a deep blue sheet with a faint square grid.
    "blueprint": {
        "style": {"grid": "lines", "font": "Arial"},
        "colors": {
            "background": "#123a6b", "worksheet": "#cfe3ff", "grid": "#2a5389", "wire": "#ffffff",
            "bus": "#9fd8ff", "junction": "#ffffff", "component_outline": "#e8f1ff", "component_body": "#1a4a82",
            "pin": "#e8f1ff", "pin_name": "#cfe3ff", "pin_number": "#9fbfe6", "reference": "#ffe28a",
            "value": "#9fd8ff", "fields": "#9fbfe6", "label_local": "#ffffff", "label_global": "#ffe28a",
            "label_hier": "#ffe28a", "note": "#cfe3ff", "sheet": "#cfe3ff", "no_connect": "#ff9f9f",
        },
    },
}

# Colour roles a theme may leave out, and the role they fall back to.
FALLBACK = {"worksheet": "component_outline", "grid": "component_outline", "label_hier": "label_global",
            "fields": "value", "no_connect": "wire", "sheet": "component_outline", "junction": "wire",
            "bus": "wire", "pin_name": "pin", "pin_number": "pin", "pin": "component_outline",
            "reference": "value", "value": "component_outline", "label_local": "wire", "label_global": "wire",
            "note": "component_outline"}
REQUIRED = ("background", "wire", "component_outline", "component_body")


def _rgb(value):
    """"#rrggbb", "rgb(r, g, b)" or "rgba(r, g, b, a)" -> (r, g, b)."""
    value = value.strip()
    if value.startswith("#"):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(int(float(n)) for n in re.findall(r"[\d.]+", value)[:3])


class Theme:
    """Colours by role, plus style: `grid` ("lines" / "dots" / "none") and `font` (None = the file's fonts).
    `altium` (no colours) keeps every object's own colour from the file."""

    def __init__(self, name, colors=None, style=None):
        self.name = name
        self.colors = {k: _rgb(v) for k, v in (colors or {}).items()} if colors is not None else None
        self.style = {"grid": "lines", "font": None, **(style or {})}

    @property
    def native(self):
        return self.colors is None

    def __getitem__(self, role):
        seen = set()
        while role not in self.colors:
            if role in seen or role not in FALLBACK:
                raise KeyError(role)
            seen.add(role)
            role = FALLBACK[role]
        return self.colors[role]


def load(name_or_path):
    """A built-in theme by name, "altium" for the file's own colours, or a KiCad 6+ colour theme JSON file."""
    if name_or_path in (None, "", "altium"):
        return Theme("altium", None, {"grid": "lines"})
    if name_or_path in THEMES:
        t = THEMES[name_or_path]
        return Theme(name_or_path, t["colors"], t["style"])
    if not os.path.isfile(name_or_path):
        raise ValueError(f"unknown theme '{name_or_path}': use one of {', '.join(names())} or a KiCad theme .json file")
    with open(name_or_path, encoding="utf-8") as f:
        data = json.load(f)
    colors = data.get("schematic", data)
    missing = [r for r in REQUIRED if r not in colors]
    if missing:
        raise ValueError(f"{name_or_path}: not a KiCad colour theme (no {', '.join(missing)} in its schematic colours)")
    name = data.get("meta", {}).get("name", name_or_path)
    return Theme(name, {k: v for k, v in colors.items() if isinstance(v, str)}, {"grid": "dots", "font": None})


def names():
    return ["altium"] + list(THEMES)
