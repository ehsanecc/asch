# asch — Altium schematic tools

Python tools for Altium Designer schematic files: `.SchDoc` (schematic sheets) and `.SchLib` (schematic libraries). This is a side tool for a project. The app itself reads and writes SchDoc in C++ (`src/SchDocExport.cpp`, `src/SchDocImport.cpp`); these scripts are for looking at files, checking the app's output and exploring the format.

```bash
pip install -r tools/asch/requirements.txt
```

| Script | What it does |
|---|---|
| `asch_render.py` | Draws a SchDoc (one page) or every component of a SchLib (one page / PNG each) as PNG or vector PDF |
| `asch_netlist.py` | A SchDoc's netlist as Calay text; `--compare` with another netlist; `--dump` records (SchDoc and SchLib) |
| `asch_themes.py` | Colour themes for the renderer (built-in palettes, or any KiCad 6+ colour theme `.json`) |
| `asch_lib.py` | The shared reader: compound-file streams, records, sheets, library components |

## Rendering

```bash
python asch_render.py Main.SchDoc                          # Main.pdf
python asch_render.py Main.SchDoc out.png --scale 3
python asch_render.py Main.SchDoc dark.pdf --theme kicanvas
python asch_render.py "SampleLib.SchLib"                   # 7seg(LED).pdf, a page per component
python asch_render.py "SampleLib.SchLib" images/           # a PNG per component
python asch_render.py "SampleLib.SchLib" --list
python asch_render.py "SampleLib.SchLib" a.png --component 7SEG_1DIGIT_CA_0.3INCH --scale 4
```

Themes: `altium` (the file's own colours, default), `kicad`, `kicanvas`, `nord`, `solarized-light`,
`solarized-dark`, `dracula`, `monokai`, `eagle-dark`, `behave-dark`, `print`, `blueprint`, or a KiCad theme file.
`--font` replaces the file's fonts, `--no-grid` hides the grid.

A library component is drawn as Altium's library editor shows it: its graphics and pins for the chosen part
(multi-part components: a page per part) and display mode (`--mode N`, `--all-modes`), its visible parameters,
not its designator; with its name and description underneath (`--no-caption` to leave them out).

## The formats in short

Both are OLE compound files of `|KEY=VALUE|` text records (pins sometimes binary); coordinates in 10-mil units,
Y up, colours as Win32 COLORREF (`0x00BBGGRR`).

- **SchDoc**: `FileHeader` = a header record then every object; `OwnerIndex` points to the owner's position
  (the header not counted). `Additional` holds a few more objects (blankets), `Storage` the embedded images.
- **SchLib**: `FileHeader` = one record with the fonts and the component list (`LIBREF<n>`, `COMPDESCR<n>`,
  `PARTCOUNT<n>`); one storage per component with a `Data` stream — the component record first, then its
  primitives (no `OwnerIndex` = owned by the component; `OwnerIndex` counts from the component record);
  `Storage` holds the images. `PartCount` is one more than the number of parts; primitives carry
  `OwnerPartId` (part, 1-based) and `OwnerPartDisplayMode` (0 = normal).
