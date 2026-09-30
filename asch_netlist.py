#!/usr/bin/env python3
"""Extracts the netlist of an Altium Designer schematic (.SchDoc) and writes it as a Calay netlist.

    python asch_netlist.py Main.SchDoc                     -> netlist on stdout
    python asch_netlist.py Main.SchDoc -o Main.NET         -> to a file (Netlist Merge / any Calay reader)
    python asch_netlist.py Main.SchDoc --compare board.NET -> which nets differ from another Calay netlist
    python asch_netlist.py Main.SchDoc --dump              -> every record, one per line (format exploration)
    python asch_netlist.py Parts.SchLib --dump             -> the same for every component of a library

A schematic stores no netlist, only the drawing, so the connectivity is worked out the way Altium compiles a sheet
-- the same rules as the app's reader (src/SchDocImport.cpp), so this is also an independent check of it:
  - a pin connects at its outer end (Location + PinLength in the direction it points); only the pins of each
    component's placed part and display mode count; a hidden pin joins the net named by its HiddenNetName;
  - a wire connects wherever a connection point lands on it: an end, a corner, or anywhere along a segment
    (a pin end, another wire's end, a net label, a power port, a port end); wires that merely cross do not;
  - net labels, power ports and ports name their net; the same name (case-insensitive) is the same net.
    Names: a net label wins, then a power port, then a port; an unnamed net is Net<ref>_<pin>.
Buses, sheet symbols / entries and harnesses are not followed.

Needs: pip install olefile   (see requirements.txt)
"""
import argparse
import re
import sys
from collections import defaultdict

from asch_lib import SCALE, RECORD_NAMES, open_file

DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


class Segments:
    """Wire segments bucketed on a coarse grid, so a point only tests the segments near it."""
    CELL = 200 * SCALE

    def __init__(self):
        self.cells = defaultdict(list)

    def add(self, a, b):
        for cx in range(min(a[0], b[0]) // self.CELL, max(a[0], b[0]) // self.CELL + 1):
            for cy in range(min(a[1], b[1]) // self.CELL, max(a[1], b[1]) // self.CELL + 1):
                self.cells[(cx, cy)].append((a, b))

    def through(self, p):
        """Segments (their first point) that p lies on, within 1/100 of a sheet unit."""
        for a, b in self.cells.get((p[0] // self.CELL, p[1] // self.CELL), ()):
            if not (min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])):
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = (dx * dx + dy * dy) ** 0.5
            if length == 0:
                if p == a:
                    yield a
            elif abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / length <= SCALE / 100:
                yield a


def extract(doc):
    """{net name: [(ref, pin), ...]} for every net with at least one pin."""
    objs = doc.all_objects()
    comps = {i: o for i, o in enumerate(doc.objects) if o.type == "1"}
    refs = doc.designators()
    uf, segs = UnionFind(), Segments()
    wire_points = []
    for o in objs:
        if o.type == "27":
            pts = o.points(exact=True)
            for a, b in zip(pts, pts[1:]):
                segs.add(a, b)
                uf.union(a, b)
            wire_points += pts

    def attach(p):
        uf.find(p)
        for a in segs.through(p):
            uf.union(p, a)
        return p

    for p in wire_points:  # a wire ending on another wire (T-junction)
        attach(p)

    names, pins = [], []   # names: (node, priority, name); pins: (node, ref, pin)
    for o in objs:
        t = o.type
        if t == "2":
            owner = o.int("OWNERINDEX", -1)
            comp = comps.get(owner)
            if comp is None or not refs.get(owner):
                continue
            if o.int("OWNERPARTID", -1) not in (-1, comp.int("CURRENTPARTID", 1)):
                continue  # another part of a multi-part component
            if o.int("OWNERPARTDISPLAYMODE", 0) != comp.int("DISPLAYMODE", 0):
                continue  # another display mode
            congl = o.int("PINCONGLOMERATE")
            if congl & 4:  # hidden pin: on the net it names, if any
                if o.get("HIDDENNETNAME"):
                    node = ("hidden", len(pins))
                    names.append((node, 1, o["HIDDENNETNAME"]))
                    pins.append((node, refs[owner], o.get("DESIGNATOR", "")))
                continue
            dx, dy = DIRS[congl & 3]
            length = o.exact("PINLENGTH")
            tip = (o.exact("LOCATION.X") + dx * length, o.exact("LOCATION.Y") + dy * length)
            pins.append((attach(tip), refs[owner], o.get("DESIGNATOR", "")))
        elif t in ("25", "17"):
            p = (o.exact("LOCATION.X"), o.exact("LOCATION.Y"))
            names.append((attach(p), 0 if t == "25" else 1, o.get("TEXT", "")))
        elif t == "18":  # port: a connection point at each end, horizontal or (styles 4..7) vertical
            a = (o.exact("LOCATION.X"), o.exact("LOCATION.Y"))
            w = o.exact("WIDTH")
            b = (a[0], a[1] + w) if o.int("STYLE") >= 4 else (a[0] + w, a[1])
            uf.union(attach(a), attach(b))
            names.append((a, 2, o.get("NAME", "")))

    first = {}
    for node, _, name in names:  # same name = same net
        if name:
            key = name.upper()
            if key in first:
                uf.union(node, first[key])
            else:
                first[key] = node
    best = {}
    for node, prio, name in names:
        if name:
            root = uf.find(node)
            if root not in best or prio < best[root][0]:
                best[root] = (prio, name)
    nets = defaultdict(list)
    for node, ref, pin in pins:
        nets[uf.find(node)].append((ref, pin))
    out = {}
    for root, members in nets.items():
        name = best[root][1] if root in best else f"Net{members[0][0]}_{members[0][1]}"
        while name in out:  # two unconnected nets that happen to get the same auto name
            name += "_"
        out[name] = members
    return out


def natural(s):
    return [int(t) if t.isdigit() else t.upper() for t in re.split(r"(\d+)", s)]


def calay(nets):
    """Calay netlist text: /NAME  REF(pin) REF(pin) ...; -- nets with 2+ pins, as the app's exports."""
    lines = []
    for name in sorted(nets, key=natural):
        pins = sorted(set(nets[name]), key=lambda rp: (natural(rp[0]), natural(rp[1])))
        if len(pins) >= 2:
            lines.append(f"/{name:<10} " + " ".join(f"{r}({p})" for r, p in pins) + ";")
    return "\n".join(lines) + "\n"


def read_calay(path):
    text = open(path, encoding="latin-1").read()
    return {m.group(1): re.findall(r"([^\s()]+)\(([^()]+)\)", m.group(2)) for m in re.finditer(r"/(\S+)\s+([^;]*);", text)}


def compare(a, b, swap_ok):
    """Nets compared by their pins (names ignored). swap_ok(ref): the part's pins are interchangeable."""
    key = lambda r, p: (r, "*") if swap_ok(r) else (r, p)
    sets = lambda nets: {frozenset(key(r, p) for r, p in pins): n for n, pins in nets.items() if len(set(pins)) >= 2}
    sa, sb = sets(a), sets(b)
    same = sa.keys() & sb.keys()
    print(f"{len(same)} nets identical, {len(sa) - len(same)} only in the schematic, {len(sb) - len(same)} only in the other netlist")
    fmt = lambda s: " ".join(f"{r}({p})" for r, p in sorted(s, key=lambda rp: (natural(rp[0]), natural(rp[1]))))
    for s in sorted(sa.keys() - same, key=lambda s: natural(sa[s])):
        print(f"  schematic  /{sa[s]}: {fmt(s)}")
    for s in sorted(sb.keys() - same, key=lambda s: natural(sb[s])):
        print(f"  other      /{sb[s]}: {fmt(s)}")


def main():
    ap = argparse.ArgumentParser(description="Extract the netlist of an Altium schematic (.SchDoc) as Calay text.")
    ap.add_argument("schdoc")
    ap.add_argument("-o", "--output", help="write the Calay netlist here (default: stdout)")
    ap.add_argument("--compare", metavar="NET", help="compare with a Calay netlist (e.g. the app's export) by pins")
    ap.add_argument("--ignore-swaps", action="store_true",
                    help="with --compare: pin 1 / 2 of R, C, L parts are interchangeable")
    ap.add_argument("--dump", action="store_true", help="print every record instead (index, type, fields)")
    args = ap.parse_args()
    doc = open_file(args.schdoc)

    if args.dump:
        out = sys.stdout
        out.reconfigure(encoding="utf-8")
        print(f"header: {dict(doc.header)}")
        fields = lambda o: "|".join(f"{k}={v}" for k, v in o.items())
        if doc.is_library:  # every component's records, OwnerIndex counting from its component record
            for comp in doc.components:
                print(f"== {comp.name}")
                for i, o in enumerate(comp.objects):
                    print(i, RECORD_NAMES.get(o.type, "?"), fields(o))
            return
        for i, o in enumerate(doc.objects):
            print(i, RECORD_NAMES.get(o.type, "?"), fields(o))
        for o in doc.extra:
            print("additional", RECORD_NAMES.get(o.type, "?"), fields(o))
        return

    if doc.is_library:
        sys.exit(f"{args.schdoc} is a schematic library: it has no netlist (try --dump, or asch_render.py to draw it)")
    nets = extract(doc)
    if args.compare:
        swap = (lambda r: re.match(r"^(R|C|L)\d", r) is not None) if args.ignore_swaps else (lambda r: False)
        compare(nets, read_calay(args.compare), swap)
        return
    text = calay(nets)
    if args.output:
        with open(args.output, "w", encoding="latin-1", errors="replace", newline="\r\n") as f:
            f.write(text)
        print(f"{args.output}: {text.count(chr(10))} nets")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
