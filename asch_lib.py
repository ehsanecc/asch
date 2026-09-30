"""Reading Altium Designer schematics (.SchDoc) and schematic libraries (.SchLib), shared by asch_render.py and
asch_netlist.py.

A .SchDoc ("Schematic Capture Binary File Version 5.0") is an OLE Compound File with three streams:
  FileHeader  a header record, then one record per object (sheet, components, pins, wires, labels, ...)
  Additional  objects newer Altium versions keep apart (blankets, ...)
  Storage     embedded images (zlib-compressed), keyed by their original file name
A .SchLib ("Schematic Library Editor Binary File Version 5.0") has a FileHeader with just the header record
(fonts, the component list), the Storage stream, and one storage per component with a Data stream: the
component record, then its pins and graphics (see SchLib).
Every record is a 4-byte header (24-bit length, 1 type byte: 0 = text, 1 = binary) and its payload. Text
records are "|KEY=VALUE|KEY=VALUE...\\0" in Windows-1252; a value that needs more is repeated first as
"%UTF8%KEY=<utf-8>". OwnerIndex is the position of the owner among the records after the header.

Coordinates are in 10-mil units, Y up; "X" + "X_Frac"/100000 is the exact value. Colours are Win32
COLORREFs (0x00BBGGRR). The same reader is in the app: src/SchDocImport.cpp.
"""
import io
import struct
import zlib

import olefile

SCALE = 100000  # exact coordinates: sheet units * SCALE

# Altium's sheet styles: SheetStyle value -> (name, width, height, x zones, y zones), in 10-mil units.
SHEET_STYLES = {
    0: ("A4", 1150, 760, 4, 4), 1: ("A3", 1550, 1110, 6, 4), 2: ("A2", 2230, 1570, 8, 6),
    3: ("A1", 3150, 2230, 8, 6), 4: ("A0", 4460, 3150, 8, 6), 5: ("A", 950, 750, 4, 4),
    6: ("B", 1500, 950, 6, 4), 7: ("C", 2000, 1500, 8, 6), 8: ("D", 3200, 2000, 8, 6),
    9: ("E", 4200, 3200, 8, 6), 10: ("Letter", 1100, 850, 4, 4), 11: ("Legal", 1400, 850, 4, 4),
    12: ("Tabloid", 1700, 1100, 6, 4),
}

RECORD_NAMES = {
    "1": "Component", "2": "Pin", "4": "Label", "6": "Polyline", "7": "Polygon", "8": "Ellipse",
    "12": "Arc", "13": "Line", "14": "Rectangle", "15": "Sheet Symbol", "16": "Sheet Entry",
    "17": "Power Port", "18": "Port", "22": "No ERC", "25": "Net Label", "26": "Bus", "27": "Wire",
    "28": "Text Frame", "29": "Junction", "30": "Image", "31": "Sheet", "34": "Designator",
    "37": "Bus Entry", "41": "Parameter", "43": "Parameter Set", "44": "Implementation List",
    "45": "Implementation", "46": "Map Definer List", "48": "Implementation Parameters",
    "225": "Blanket",
}


class Record(dict):
    """One record's fields, keys upper-cased (Altium's own capitalisation varies between versions)."""

    @property
    def type(self):
        return self.get("RECORD", "")

    def int(self, key, default=0):
        try:
            return int(self.get(key, default))
        except ValueError:
            return default

    def float(self, key, default=0.0):
        try:
            return float(self.get(key, default))
        except ValueError:
            return default

    def coord(self, key):
        """A coordinate in sheet units (float), including its _FRAC part."""
        return self.int(key) + self.int(key + "_FRAC") / SCALE

    def exact(self, key):
        """A coordinate as an exact integer (sheet units * SCALE), for comparing connection points."""
        return self.int(key) * SCALE + self.int(key + "_FRAC")

    def points(self, exact=False):
        """X1/Y1 .. Xn/Yn of a wire, polyline, polygon, bus or blanket."""
        f = self.exact if exact else self.coord
        return [(f(f"X{i}"), f(f"Y{i}")) for i in range(1, self.int("LOCATIONCOUNT") + 1)]


def _parse_text(payload):
    text = payload.rstrip(b"\0")
    rec, utf8 = Record(), {}
    for part in text.split(b"|"):
        if b"=" not in part:
            continue
        key, value = part.split(b"=", 1)
        key = key.decode("latin-1").upper()
        if key.startswith("%UTF8%"):
            utf8[key[6:]] = value.decode("utf-8", "replace")
        elif key not in rec:
            rec[key] = value.decode("cp1252", "replace")
    rec.update(utf8)  # the UTF-8 copy of a value is the exact one
    return rec


def _parse_binary_pin(payload, owner):
    """Older Altium versions store pins as binary records (layout as in KiCad's importer / python-altium).
    A binary pin has no OwnerIndex: it belongs to the component before it."""
    at = 0

    def take(fmt):
        nonlocal at
        v = struct.unpack_from(fmt, payload, at)
        at += struct.calcsize(fmt)
        return v[0]

    def pascal():
        nonlocal at
        n = payload[at]
        s = payload[at + 1:at + 1 + n].decode("latin-1")
        at += 1 + n
        return s

    try:
        if take("<i") != 2:
            return Record()
        take("<B")
        rec = Record(RECORD="2", OWNERINDEX=str(owner))
        rec["OWNERPARTID"] = str(take("<h"))
        rec["OWNERPARTDISPLAYMODE"] = str(take("<B"))
        for _ in range(4):
            take("<B")  # pin symbols
        rec["DESCRIPTION"] = pascal()
        take("<B")  # formal type
        rec["ELECTRICAL"] = str(take("<B"))
        rec["PINCONGLOMERATE"] = str(take("<B"))
        rec["PINLENGTH"] = str(take("<h"))
        rec["LOCATION.X"] = str(take("<h"))
        rec["LOCATION.Y"] = str(take("<h"))
        rec["COLOR"] = str(take("<i"))
        rec["NAME"] = pascal()
        rec["DESIGNATOR"] = pascal()
        return rec if owner >= 0 else Record()
    except (struct.error, IndexError):
        return Record()


def parse_records(data, has_header=True):
    """Every record of a stream (a schematic's FileHeader: header first; a library component's Data: no header,
    the component first). OwnerIndex counts from the first record that is not a header."""
    recs, i, last_component = [], 0, -1
    first = 1 if has_header else 0
    while i + 4 <= len(data):
        n = data[i] | data[i + 1] << 8 | data[i + 2] << 16
        kind = data[i + 3]
        payload = data[i + 4:i + 4 + n]
        i += 4 + n
        if kind == 0:
            rec = _parse_text(payload)
        elif kind == 1:
            rec = _parse_binary_pin(payload, last_component)
        else:
            rec = Record()
        if rec.type == "1":
            last_component = len(recs) - first  # its OwnerIndex position
        recs.append(rec)
    return recs


def read_images(data):
    """The Storage stream: file name -> PIL image (empty when Pillow is not installed)."""
    images = {}
    try:
        from PIL import Image
    except ImportError:
        return images
    i = 0
    while i + 4 <= len(data):
        n = data[i] | data[i + 1] << 8 | data[i + 2] << 16
        kind, payload = data[i + 3], data[i + 4:i + 4 + n]
        i += 4 + n
        if kind != 1 or not payload or payload[0] != 0xD0:
            continue
        name_len = payload[1]
        name = payload[2:2 + name_len].decode("latin-1")
        size = struct.unpack_from("<I", payload, 2 + name_len)[0]
        try:
            raw = zlib.decompress(payload[6 + name_len:6 + name_len + size])
            images[name] = key_transparent(Image.open(io.BytesIO(raw)))
        except Exception:
            pass
    return images


def key_transparent(im):
    """Altium draws an embedded image the way a Delphi bitmap with automatic transparency is drawn: the colour of
    its bottom-left pixel is transparent (so a logo's white background disappears, while a white that differs by
    even one step -- a bird's 254-white belly -- stays). Images that carry their own alpha are left as they are.
    (Embedded images are stored as bitmaps whatever their original file name says.)"""
    if im.mode in ("RGBA", "LA") or "transparency" in im.info:
        return im.convert("RGBA")
    im = im.convert("RGBA")
    im.putalpha(_key_mask(im, im.getpixel((0, im.height - 1))[:3]))
    return im


def _key_mask(im, key):
    """An alpha mask: 0 where the pixel is exactly `key`, else 255."""
    from PIL import ImageChops
    r, g, b, _ = im.split()
    mask = None
    for band, value in zip((r, g, b), key):
        m = band.point(lambda v, value=value: 255 if v == value else 0)
        mask = m if mask is None else ImageChops.multiply(mask, m)
    return ImageChops.invert(mask)


def fonts_of(rec):
    """FontID -> (name, size, bold, italic), from a sheet record or a library header."""
    return {i: (rec.get(f"FONTNAME{i}", "Times New Roman"), rec.int(f"SIZE{i}", 10),
                rec.get(f"BOLD{i}") == "T", rec.get(f"ITALIC{i}") == "T")
            for i in range(1, rec.int("FONTIDCOUNT", 0) + 1)}


class SchDoc:
    """A parsed schematic: `header`, `objects` (OwnerIndex positions), `extra` (the Additional stream's
    top-level objects), `images` (file name -> PIL image, only when Pillow is installed) and `sheet`."""

    def __init__(self, path):
        ole = olefile.OleFileIO(path)
        recs = parse_records(ole.openstream("FileHeader").read())
        if not recs or "Schematic" not in recs[0].get("HEADER", ""):
            raise ValueError(f"{path}: not an Altium schematic")
        self.header, self.objects = recs[0], recs[1:]
        self.extra = []
        if ole.exists("Additional"):
            self.extra = [r for r in parse_records(ole.openstream("Additional").read())[1:] if "OWNERINDEX" not in r]
        self.images = read_images(ole.openstream("Storage").read()) if ole.exists("Storage") else {}
        self.sheet = next((o for o in self.objects if o.type == "31"), Record())

    is_library = False

    def all_objects(self):
        return self.objects + self.extra

    def owner(self, rec):
        idx = rec.int("OWNERINDEX", -1)
        return self.objects[idx] if 0 <= idx < len(self.objects) else None

    def is_shown(self, rec):
        """False for a component's primitive that belongs to another part / display mode than the placed one."""
        comp = self.owner(rec)
        while comp is not None and comp.type != "1":  # e.g. a pin's parameter -> the pin -> the component
            comp = self.owner(comp)
        if comp is None:
            return True
        part = rec.int("OWNERPARTID", -1)
        if part not in (-1, comp.int("CURRENTPARTID", 1)):
            return False
        return rec.int("OWNERPARTDISPLAYMODE", 0) == comp.int("DISPLAYMODE", 0)

    def sheet_size(self):
        """(name, width, height, x zones, y zones, margin) of the sheet, in 10-mil units."""
        s = self.sheet
        margin = s.int("CUSTOMMARGINWIDTH", 20)
        if s.get("USECUSTOMSHEET") == "T":
            w, h = s.int("CUSTOMX", 1500), s.int("CUSTOMY", 950)
            return (f"Custom {w} x {h}", w, h, s.int("CUSTOMXZONES", 6), s.int("CUSTOMYZONES", 4), margin)
        name, w, h, xz, yz = SHEET_STYLES.get(s.int("SHEETSTYLE", 0), SHEET_STYLES[0])
        return name, w, h, xz, yz, margin

    def fonts(self):
        """FontID -> (name, size, bold, italic)."""
        return fonts_of(self.sheet)

    def designators(self):
        """Component record index -> its designator text."""
        out = {}
        for rec in self.objects:
            if rec.type == "34" and rec.get("NAME", "").lower() == "designator":
                out[rec.int("OWNERINDEX", -1)] = rec.get("TEXT", "")
        return out


class SchLib:
    """A schematic library (.SchLib, "Schematic Library Editor Binary File Version 5.0"): a FileHeader stream with
    the fonts and the list of components (LIBREF<n>, COMPDESCR<n>, PARTCOUNT<n>), one storage per component
    holding a Data stream (the component record first, then its pins and graphics -- pins are often binary
    records), and the shared Storage stream of images. `components` is a list of LibComponent, in the order
    the header lists them."""
    is_library = True

    def __init__(self, path):
        ole = olefile.OleFileIO(path)
        recs = parse_records(ole.openstream("FileHeader").read())
        if not recs or "Schematic Library" not in recs[0].get("HEADER", ""):
            raise ValueError(f"{path}: not an Altium schematic library")
        self.header = recs[0]
        self.images = read_images(ole.openstream("Storage").read()) if ole.exists("Storage") else {}
        parts = {}
        for entry in ole.listdir():
            if len(entry) == 2 and entry[1].upper() == "DATA":
                comp = LibComponent(self, parse_records(ole.openstream(entry).read(), has_header=False))
                if comp.component is not None:
                    parts[comp.name.upper()] = comp
        order = [self.header.get(f"LIBREF{i}", "").upper() for i in range(self.header.int("COMPCOUNT"))]
        rank = {name: k for k, name in enumerate(order)}
        self.components = sorted(parts.values(), key=lambda c: (rank.get(c.name.upper(), len(rank)), c.name))

    def fonts(self):
        return fonts_of(self.header)


class LibComponent:
    """One library component, seen the way the renderer sees a sheet: `objects` (the component record at 0),
    owner(), is_shown() for the chosen `part` (1-based) and display `mode` (0 = normal), fonts() and images."""
    is_library = True

    def __init__(self, lib, recs):
        self.lib, self.objects, self.extra = lib, recs, []
        self.component = recs[0] if recs and recs[0].type == "1" else None
        c = self.component or Record()
        self.name = c.get("LIBREFERENCE", "?")
        self.description = c.get("COMPONENTDESCRIPTION", "")
        # PartCount is stored one higher than the number of parts (A, B, ...).
        self.part_count = max(1, c.int("PARTCOUNT", 2) - 1)
        self.mode_count = max(1, c.int("DISPLAYMODECOUNT", 1))
        self.part, self.mode = 1, 0
        self.images = lib.images
        self.sheet = lib.header  # fonts, colours and grid settings of the library editor

    def all_objects(self):
        return self.objects

    def owner(self, rec):
        if rec is self.component:
            return None
        idx = rec.int("OWNERINDEX", 0)  # no OwnerIndex: the component's own primitive
        return self.objects[idx] if 0 <= idx < len(self.objects) else None

    def is_shown(self, rec):
        if rec is self.component:
            return True
        if rec.int("OWNERPARTID", -1) not in (-1, self.part):
            return False
        return rec.int("OWNERPARTDISPLAYMODE", 0) == self.mode

    def fonts(self):
        return fonts_of(self.lib.header)

    def designators(self):
        return {0: next((r.get("TEXT", "") for r in self.objects if r.type == "34"), "")}

    def part_name(self):
        """The part's letter (A, B, ...) for a multi-part component, else ''."""
        return chr(ord("A") + self.part - 1) if self.part_count > 1 else ""


def open_file(path):
    """A SchDoc or a SchLib, whichever the file is."""
    ole = olefile.OleFileIO(path)
    header = parse_records(ole.openstream("FileHeader").read())[:1]
    kind = header[0].get("HEADER", "") if header else ""
    ole.close()
    return SchLib(path) if "Library" in kind else SchDoc(path)


def colorref(value, default=0):
    """Win32 COLORREF (0x00BBGGRR) -> (r, g, b)."""
    try:
        c = int(value)
    except (TypeError, ValueError):
        c = default
    return (c & 255, (c >> 8) & 255, (c >> 16) & 255)
