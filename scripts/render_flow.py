#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_flow.py — .flow (DSL) -> self-contained HTML + inline SVG flowchart.

Deterministic renderer for the UXUI team flow template (Amm layout).
It renders ONLY what the designer wrote in the .flow file. It never invents
nodes or edges — that is the whole point of the Flow Architect skill.

ต้องมี: Python 3.9+ เท่านั้น (stdlib ล้วน — argparse/json/math/os/re) ไม่ต้องลง package

Usage:
    python3 render_flow.py input.flow [-o output.html] [--tokens tokens.json]

Geometry locked by team spec (see references/geometry.md):
    rect      padding 16 top/bottom, 24 left/right, radius 16
    diamond   identical size for every diamond in one flow (scales up as a
              whole when the longest question needs more room), radius 16
    edges     minimum path length 500px (SPACING edge=)
    Yes/No    label centered 100px along the path from the exit point
    anchors   same edge type may share an anchor; different edge types must
              leave from a different side
"""

import argparse
import json
import math
import os
import re
import sys

# ---------------------------------------------------------------- tokens ----

TOKENS = {
    "primary":   "#3B5BDB",
    "primary_d": "#2F49AF",
    "ink":       "#17181C",
    "muted":     "#6B7280",
    "paper":     "#FFFFFF",
    "paper_2":   "#F7F8FB",
    "yes":       "#16A34A",
    "no":        "#DC2626",
    "perm":      "#F5B400",
    "hairline":  "#E4E7EE",
}

# ค่าที่ทีมกำหนด (references/flow-rules.md §3) — เปลี่ยนแล้ว self_check.py จะฟ้อง G5/G4
RADIUS      = 16      # มุมของทุกกล่อง
PAD_Y       = 16      # padding บน-ล่าง
PAD_X       = 24      # padding ซ้าย-ขวา
ELBOW_R     = 16      # รัศมีมุมฉากมนของเส้น ใช้ค่าเดียวกับมุมกล่องให้ดูเป็นชุดเดียว

# ค่าที่เลือกจากการ render ไฟล์จริงแล้วดูว่าอ่านออกที่ zoom 50-100%
FS_NODE     = 16      # ขนาดตัวอักษรในกล่อง — 16px คือจุดที่วรรณยุกต์ไทยไม่ชนกัน
FS_SUB      = 13      # ป้ายเส้น + note
LINE_H      = 24      # ระยะบรรทัด = 1.5 เท่าของ FS_NODE
RECT_MIN_TEXT_W = 152  # กล่องแคบสุดที่ยังดูเป็นกล่อง ไม่ใช่ป้าย
RECT_MAX_TEXT_W = 360  # กว้างกว่านี้สายตาไล่บรรทัดไม่ทัน ต้องตัดบรรทัด
DIAMOND_ASPECT  = 1.45          # สัดส่วน กว้าง/สูง ของข้าวหลามตัด ตาม template ของ Amm
DIAMOND_MIN     = (300, 207)    # ขนาดพื้น (w, h) — ยืดขึ้นพร้อมกันทุกอันเมื่อข้อความยาว
STUB            = 60            # ช่วงตรงก่อนเลี้ยวมุมแรก เผื่อที่ให้หัวลูกศรและจุดต้นทาง
MARGIN          = 140           # ขอบ canvas ขั้นต่ำ (layout ขยายเป็น 0.45*gap ถ้า gap กว้างกว่า)

EDGE_SOLID = "solid"   # normal link: tap -> record page -> continue
EDGE_BACK  = "back"    # normal link that returns to the previous page
EDGE_YES   = "yes"
EDGE_NO    = "no"
EDGE_PERM  = "perm"    # this page has permutations (edge cases)

# anchor preference per edge type, so different types never share a side
# flow เดินจากซ้ายไปขวา: ไปต่อ = ออกทางขวา, แตก branch = ออกทางล่าง
OUT_PREF = {
    EDGE_SOLID: ["E", "S", "N", "W"],
    EDGE_YES:   ["E", "S", "N", "W"],
    EDGE_NO:    ["S", "E", "N", "W"],
    EDGE_BACK:  ["N", "S", "W", "E"],
    EDGE_PERM:  ["S", "N", "E", "W"],
}
IN_PREF = {
    EDGE_SOLID: ["W", "N", "S", "E"],
    EDGE_YES:   ["W", "N", "S", "E"],
    EDGE_NO:    ["W", "N", "S", "E"],
    EDGE_BACK:  ["E", "S", "N", "W"],      # ย้อนกลับ = วิ่งมาจากขวา เข้าด้านขวาของหน้าก่อนหน้า
    EDGE_PERM:  ["W", "N", "S", "E"],
}
DEC_IN = ("W", 0)   # ทางเข้า Diamond รวมที่ปลายซ้ายเสมอ — Diamond มีเส้นออกได้แค่ Yes/No
SLOT_FRAC = {0: 0.0, 1: 0.5, 2: -0.5}   # fanned attach points on one side
DIRV = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}

SHAPE = {  # node kind -> shape
    "START": "oval", "END": "oval",
    "PAGE": "rect", "PROCESS": "rect", "STEP": "rect", "ACTION": "rect",
    "DEC": "diamond",
    "LINK": "chip",
    "PERMNOTE": "note",
}
ALIAS = {"PROCESS": "PAGE", "STEP": "PAGE", "ACTION": "PAGE", "DECISION": "DEC",
         "TERMINATOR": "START"}


# ------------------------------------------------------------ text metrics ----

_THAI_ZERO = set(
    [0x0E31] + list(range(0x0E34, 0x0E3B)) + list(range(0x0E47, 0x0E4F))
)
_THAI_LEAD = set("เแโใไ")
_THAI_FOLLOW = set("ะาำๆฯ")


def char_w(ch, fs):
    o = ord(ch)
    if o in _THAI_ZERO:
        return 0.0
    if 0x0E00 <= o <= 0x0E7F:
        return 0.58 * fs
    if ch == " ":
        return 0.28 * fs
    if ch.isdigit() or ch.isupper():
        return 0.60 * fs
    if ch in "iljt.,'|!":
        return 0.28 * fs
    return 0.52 * fs


def text_w(s, fs=FS_NODE):
    return sum(char_w(c, fs) for c in s)


def wrap(s, max_w, fs=FS_NODE):
    """Greedy wrap. Latin breaks on spaces; Thai has none, so it breaks on
    character boundaries while refusing to split a cluster — a leading vowel
    stays with its consonant and trailing vowels/marks never start a line."""

    def hard_break(chunk):
        """split chunk into pieces that each fit max_w"""
        out = []
        while text_w(chunk, fs) > max_w:
            cut = len(chunk)
            while cut > 1 and text_w(chunk[:cut], fs) > max_w:
                cut -= 1
            # never leave a leading vowel dangling, never start a line with a
            # trailing vowel or a combining mark
            guard = 0
            while cut > 1 and guard < 6:
                prev, nxt = chunk[cut - 1], chunk[cut] if cut < len(chunk) else ""
                if prev in _THAI_LEAD or nxt in _THAI_FOLLOW or (nxt and ord(nxt) in _THAI_ZERO):
                    cut -= 1
                    guard += 1
                else:
                    break
            out.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            out.append(chunk)
        return out

    lines, cur = [], ""
    for w in s.split(" "):
        cand = (cur + " " + w).strip()
        if cur and text_w(cand, fs) > max_w:
            lines.extend(hard_break(cur))
            cur = w
        else:
            cur = cand
    if cur:
        lines.extend(hard_break(cur))
    return [l for l in lines if l] or [""]


# -------------------------------------------------------------- DSL parser ----

EDGE_RE = re.compile(
    r"^\s*(?P<src>[\w\-.]+)\s*(?P<op>-\[[^\]]*\]->|--?[^>\s]*-?>|\.\.>|~>)\s*(?P<dst>[\w\-.]+)\s*(?:#.*)?$"
)


class FlowError(Exception):
    pass


def parse(path):
    meta = {"FLOW": "", "GOAL": "", "OWNER": "", "DATE": "", "NOTE": ""}
    spacing = {"edge": 500.0, "label": 100.0}
    nodes, edges, perms, order = {}, [], [], []

    with open(path, encoding="utf-8") as fh:
        raw = fh.readlines()

    for ln, line in enumerate(raw, 1):
        line = line.split("#")[0].rstrip() if not line.strip().startswith("#") else ""
        if not line.strip():
            continue
        head, _, rest = line.strip().partition(" ")
        key = head.upper()
        rest = rest.strip()

        if key in meta:
            meta[key] = rest
            continue
        if key == "SPACING":
            for tok in rest.replace(",", " ").split():
                k, _, v = tok.partition("=")
                if k in spacing and v:
                    spacing[k] = float(v)
            continue
        if key == "PERM":
            nid, _, text = rest.partition(" ")
            perms.append((nid, text.strip() or "มี Permutation (Edge Case)", ln))
            continue
        if key in ALIAS or key in SHAPE:
            kind = ALIAS.get(key, key)
            nid, _, label = rest.partition(" ")
            if not nid:
                raise FlowError(f"บรรทัด {ln}: {key} ต้องมี id")
            if not label.strip():
                raise FlowError(f"บรรทัด {ln}: node {nid} ยังไม่มีข้อความ")
            if nid in nodes:
                raise FlowError(f"บรรทัด {ln}: id '{nid}' ซ้ำ")
            nodes[nid] = {"id": nid, "kind": kind, "label": label.strip(), "line": ln}
            order.append(nid)
            continue

        m = EDGE_RE.match(line)
        if not m:
            raise FlowError(f"บรรทัด {ln}: อ่านไม่ออก -> {line.strip()!r}")
        op, src, dst = m.group("op"), m.group("src"), m.group("dst")
        label = ""
        if op == "->":
            etype = EDGE_SOLID
        elif op == "..>":
            etype = EDGE_BACK
        elif op == "~>":
            etype = EDGE_PERM
        else:
            label = op.strip("-[]>").strip()
            low = label.lower()
            if low in ("yes", "y", "ใช่"):
                etype, label = EDGE_YES, "Yes"
            elif low in ("no", "n", "ไม่", "ไม่ใช่"):
                etype, label = EDGE_NO, "No"
            else:
                etype = EDGE_SOLID
        edges.append({"src": src, "dst": dst, "type": etype, "label": label, "line": ln})

    # PERM sugar -> a real note node + a perm edge, so it lays out like anything else
    for nid, text, ln in perms:
        if nid not in nodes:
            raise FlowError(f"บรรทัด {ln}: PERM ชี้ไป node '{nid}' ที่ไม่มีอยู่")
        note = f"{nid}__perm"
        nodes[note] = {"id": note, "kind": "PERMNOTE", "label": text, "line": ln}
        order.append(note)
        edges.append({"src": nid, "dst": note, "type": EDGE_PERM, "label": "", "line": ln})
        nodes[nid]["has_perm"] = True

    for e in edges:
        for side in ("src", "dst"):
            if e[side] not in nodes:
                raise FlowError(f"บรรทัด {e['line']}: ไม่รู้จัก node '{e[side]}'")
    if not any(n["kind"] == "START" for n in nodes.values()):
        raise FlowError("ยังไม่มี START — flow ต้องมีจุดเริ่มต้น 1 จุด")

    return meta, spacing, nodes, order, edges


# ------------------------------------------------------------------ sizing ----

def size_nodes(nodes):
    """Diamonds all end up the same size: measure every question, then scale the
    whole rhombus up until the longest one fits. An inscribed centred rect fits a
    rhombus W x H when tw/W + th/H <= 1, so the scale factor is that sum."""
    dia_w, dia_h = DIAMOND_MIN
    W0, H0 = DIAMOND_MIN
    for n in nodes.values():
        if SHAPE[n["kind"]] != "diamond":
            continue
        lines = wrap(n["label"], W0 / 2 - PAD_X)
        tw = max(text_w(l) for l in lines)
        th = len(lines) * LINE_H
        k = max(1.0, (tw + 2 * PAD_X) / W0 + (th + 2 * PAD_Y) / H0)
        dia_w, dia_h = max(dia_w, W0 * k), max(dia_h, H0 * k)

    dia_w = math.ceil(dia_w / 4) * 4
    dia_h = math.ceil(dia_h / 4) * 4

    for n in nodes.values():
        shape = SHAPE[n["kind"]]
        if shape == "diamond":
            lines = wrap(n["label"], (dia_w / 2) - PAD_X)
            n.update(w=dia_w, h=dia_h, lines=lines)
            continue
        maxw = RECT_MAX_TEXT_W if shape != "note" else 300
        lines = wrap(n["label"], maxw, FS_NODE if shape != "note" else FS_SUB)
        fs = FS_NODE if shape != "note" else FS_SUB
        tw = max(max(text_w(l, fs) for l in lines), RECT_MIN_TEXT_W if shape != "chip" else 90)
        w = math.ceil((tw + 2 * PAD_X) / 4) * 4
        h = math.ceil((len(lines) * LINE_H + 2 * PAD_Y) / 4) * 4
        if shape == "oval":
            w += 24
        n.update(w=w, h=h, lines=lines)
    return dia_w, dia_h


# ------------------------------------------------------------------ layout ----

def is_forward(e):
    return e["type"] != EDGE_BACK


def layout(nodes, order, edges, spacing):
    out = {nid: [] for nid in nodes}
    for e in edges:
        out[e["src"]].append(e)

    def sort_key(e):
        return {EDGE_YES: 0, EDGE_SOLID: 1, EDGE_NO: 2, EDGE_PERM: 3, EDGE_BACK: 9}[e["type"]]
    for nid in out:
        out[nid].sort(key=sort_key)

    starts = [n for n in order if nodes[n]["kind"] == "START"] or [order[0]]

    # rank = longest forward path from a start, cycles cut by a visit stack
    rank = {}
    stack = set()

    def walk(nid, r):
        if nid in stack:
            return
        rank[nid] = max(rank.get(nid, 0), r)
        stack.add(nid)
        for e in out[nid]:
            if is_forward(e):
                walk(e["dst"], rank[nid] + 1)
        stack.discard(nid)

    for s in starts:
        walk(s, 0)
    for nid in order:
        rank.setdefault(nid, max(rank.values(), default=0) + 1)

    # column: primary exit keeps the lane, other exits open a fresh lane
    col, taken, next_free = {}, set(), 0
    for i, s in enumerate(starts):
        col[s] = next_free
        taken.add((rank[s], next_free))
        next_free += 1

    for nid in sorted(order, key=lambda n: (rank[n], col.get(n, 99))):
        if nid not in col:
            continue
        primary = True
        for e in out[nid]:
            if not is_forward(e):
                continue
            d = e["dst"]
            if d in col:
                primary = False
                continue
            if primary and (rank[d], col[nid]) not in taken:
                col[d] = col[nid]
            else:
                c = next_free
                while (rank[d], c) in taken:
                    c += 1
                col[d] = c
                next_free = max(next_free, c + 1)
            taken.add((rank[d], col[d]))
            primary = False
    for nid in order:
        if nid not in col:
            c = next_free
            next_free += 1
            col[nid] = c
            taken.add((rank[nid], c))

    gap = spacing["edge"]
    lanes = sorted(set(col.values()))
    ranks = sorted(set(rank.values()))
    # rank = ขั้นของ flow -> แกน X (ซ้ายไปขวา) / lane = branch -> แกน Y
    rank_w = {r: max([nodes[n]["w"] for n in order if rank[n] == r] or [0]) for r in ranks}
    lane_h = {c: max([nodes[n]["h"] for n in order if col[n] == c] or [0]) for c in lanes}

    # ช่องวิ่งเส้นอยู่ห่างจากกล่อง 0.35*gap จึงต้องเผื่อขอบให้มากกว่านั้น
    # ไม่งั้นเส้นที่เข้ากล่องแถวบนสุด/ซ้ายสุดจะวิ่งออกนอก canvas แล้วถูกตัดหาย
    margin = max(MARGIN, gap * 0.45)
    rank_x, x = {}, margin
    for r in ranks:
        rank_x[r] = x
        x += rank_w[r] + gap
    lane_y, y = {}, margin
    for c in lanes:
        lane_y[c] = y
        y += lane_h[c] + gap

    for nid in order:
        n = nodes[nid]
        n["rank"], n["col"] = rank[nid], col[nid]
        n["x"] = rank_x[rank[nid]] + (rank_w[rank[nid]] - n["w"]) / 2
        n["y"] = lane_y[col[nid]] + (lane_h[col[nid]] - n["h"]) / 2
        n["cx"], n["cy"] = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
        n["_ytop"], n["_ybot"] = lane_y[col[nid]], lane_y[col[nid]] + lane_h[col[nid]]
        n["_xlft"], n["_xrgt"] = rank_x[rank[nid]], rank_x[rank[nid]] + rank_w[rank[nid]]
        n["_gap"] = gap

    W = x - gap + margin
    H = y - gap + margin
    return W, H


def anchor_pt(n, side, slot=0):
    """Attach point on one side of a node. slot 0 is the middle of the side (the
    tip, on a diamond); slots 1 and 2 fan out along the same side so an extra
    edge type gets its own point instead of stacking on top of another type."""
    x, y, w, h, cx, cy = n["x"], n["y"], n["w"], n["h"], n["cx"], n["cy"]
    f = SLOT_FRAC.get(slot, 0.0)
    if SHAPE[n["kind"]] == "diamond":
        tips = {"N": (cx, y), "E": (x + w, cy), "S": (cx, y + h), "W": (x, cy)}
        if not f:
            return tips[side]
        cw = {"N": "E", "E": "S", "S": "W", "W": "N"}
        ccw = {"N": "W", "W": "S", "S": "E", "E": "N"}
        nxt = tips[cw[side] if f > 0 else ccw[side]]
        t = min(0.34, abs(f) * 0.68)
        a = tips[side]
        return (a[0] + (nxt[0] - a[0]) * t, a[1] + (nxt[1] - a[1]) * t)
    if side in ("N", "S"):
        off = f * max(0.0, w / 2 - RADIUS - 8)
        return (cx + off, y if side == "N" else y + h)
    off = f * max(0.0, h / 2 - RADIUS - 8)
    return (x if side == "W" else x + w, cy + off)


def pick_anchor(node, etype, pref, used, way):
    """Same edge type going the same way may share one attach point — that is how
    branches merge. Anything else has to move: first to another side, and only
    when every side is taken, to a fanned point on a used side."""
    reg = used.setdefault(node["id"], {})
    mine = (etype, way)
    for side in pref[etype]:                     # reuse a point already ours
        for slot in (0, 1, 2):
            if reg.get((side, slot)) == mine:
                return side, slot
    if way == "in":
        # ทางเข้าของทุกเส้นควรอยู่ด้านเดียวกัน (ด้านซ้ายใน layout ซ้าย→ขวา) เพื่อให้
        # อ่านได้ว่า "เข้าทางนี้ ออกทางนั้น" — เส้นคนละประเภทจึงกระจายเป็นจุดคนละจุด
        # บนด้านเดิม ดีกว่าย้ายไปเข้าด้านบนแล้ววิ่งตั้งผ่านแถวอื่น
        side = pref[etype][0]
        for slot in (0, 1, 2):
            if (side, slot) not in reg:
                reg[(side, slot)] = mine
                return side, slot
    for side in pref[etype]:                     # a completely free side
        if not any((side, s) in reg for s in (0, 1, 2)):
            reg[(side, 0)] = mine
            return side, 0
    for side in pref[etype]:                     # fan out on a used side
        for slot in (1, 2):
            if (side, slot) not in reg:
                reg[(side, slot)] = mine
                return side, slot
    side = pref[etype][0]
    reg[(side, 0)] = mine
    return side, 0


# ------------------------------------------------------------------ routes ----

def dedupe(pts):
    out = []
    for p in pts:
        if not out or (abs(p[0] - out[-1][0]) > 0.5 or abs(p[1] - out[-1][1]) > 0.5):
            out.append(p)
    clean = [out[0]]
    for i in range(1, len(out) - 1):
        a, b, c = clean[-1], out[i], out[i + 1]
        if (abs(a[0] - b[0]) < 0.5 and abs(b[0] - c[0]) < 0.5) or \
           (abs(a[1] - b[1]) < 0.5 and abs(b[1] - c[1]) < 0.5):
            continue
        clean.append(b)
    clean.append(out[-1])
    return clean


def route(a, da, b, db, etype, src=None, dst=None):
    """Orthogonal route. The run just before the arrow arrives always sits in the
    empty gutter immediately outside the destination, on the same side as its
    attach point — so the last leg is short and never has to cross the target or
    whatever happens to share its rank."""
    ax, ay = a
    bx, by = b
    sx, sy = DIRV[da]
    tx, ty = DIRV[db]
    p1 = (ax + sx * STUB, ay + sy * STUB)
    p2 = (bx + tx * STUB, by + ty * STUB)
    v_in = tx == 0
    gap = (src or {}).get("_gap", 500)
    pad = gap * 0.35

    def arrive_lane():
        """พิกัดของช่องว่างที่ติดกับปลายทาง ด้านเดียวกับจุดที่เส้นเข้า"""
        if not dst:
            return None
        return {"W": dst["_xlft"] - pad, "E": dst["_xrgt"] + pad,
                "N": dst["_ytop"] - pad, "S": dst["_ybot"] + pad}[db]

    lane = arrive_lane()
    mid = []
    if v_in:                       # เข้าด้านบน/ล่าง -> วิ่งแนวนอนในช่องว่างนั้นก่อน
        ly = lane if lane is not None else p2[1]
        if abs(p1[0] - p2[0]) > 0.5 or abs(p1[1] - ly) > 0.5:
            mid = [(p1[0], ly), (p2[0], ly)]
    else:                          # เข้าด้านข้าง -> วิ่งแนวตั้งในช่องว่างนั้นก่อน
        lx = lane if lane is not None else p2[0]
        if abs(p1[1] - p2[1]) > 0.5 or abs(p1[0] - lx) > 0.5:
            mid = [(lx, p1[1]), (lx, p2[1])]
    return dedupe([a, p1] + mid + [p2, b])


def seg_len(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def point_at(pts, dist):
    left = dist
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        L = math.dist(a, b)
        if L >= left:
            t = left / L if L else 0
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        left -= L
    return pts[-1]


def rounded_path(pts, r=ELBOW_R):
    d = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        r1 = min(r, math.dist(p0, p1) / 2, math.dist(p1, p2) / 2)
        v0 = ((p1[0] - p0[0]), (p1[1] - p0[1]))
        v1 = ((p2[0] - p1[0]), (p2[1] - p1[1]))
        n0 = math.hypot(*v0) or 1
        n1 = math.hypot(*v1) or 1
        a = (p1[0] - v0[0] / n0 * r1, p1[1] - v0[1] / n0 * r1)
        b = (p1[0] + v1[0] / n1 * r1, p1[1] + v1[1] / n1 * r1)
        cross = v0[0] * v1[1] - v0[1] * v1[0]
        sweep = 1 if cross > 0 else 0
        d.append(f"L {a[0]:.1f} {a[1]:.1f}")
        d.append(f"A {r1:.1f} {r1:.1f} 0 0 {sweep} {b[0]:.1f} {b[1]:.1f}")
    d.append(f"L {pts[-1][0]:.1f} {pts[-1][1]:.1f}")
    return " ".join(d)


def rounded_poly(pts, r=RADIUS):
    """Closed polygon with rounded corners — used for the decision diamond so it
    picks up the same 16px corner radius as every box in the flow."""
    n = len(pts)
    d = []
    for i in range(n):
        p0, p1, p2 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        r1 = min(r, math.dist(p0, p1) / 2, math.dist(p1, p2) / 2)
        v0 = (p1[0] - p0[0], p1[1] - p0[1])
        v1 = (p2[0] - p1[0], p2[1] - p1[1])
        n0 = math.hypot(*v0) or 1
        n1 = math.hypot(*v1) or 1
        a = (p1[0] - v0[0] / n0 * r1, p1[1] - v0[1] / n0 * r1)
        b = (p1[0] + v1[0] / n1 * r1, p1[1] + v1[1] / n1 * r1)
        sweep = 1 if (v0[0] * v1[1] - v0[1] * v1[0]) > 0 else 0
        d.append(("M" if i == 0 else "L") + f" {a[0]:.1f} {a[1]:.1f}")
        d.append(f"A {r1:.1f} {r1:.1f} 0 0 {sweep} {b[0]:.1f} {b[1]:.1f}")
    d.append("Z")
    return " ".join(d)


def hits_node(pts, nodes, skip):
    """Sample the polyline and report any box it passes through."""
    bad = set()
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        steps = max(2, int(math.dist(a, b) / 12))
        for s in range(steps + 1):
            t = s / steps
            px, py = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            for nid, n in nodes.items():
                if nid in skip:
                    continue
                if n["x"] - 4 < px < n["x"] + n["w"] + 4 and n["y"] - 4 < py < n["y"] + n["h"] + 4:
                    bad.add(nid)
    return sorted(bad)


# ------------------------------------------------------------------- shapes ----

def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def node_svg(n):
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    shape = SHAPE[n["kind"]]
    out = []
    if shape == "oval":
        out.append(f'<rect class="n-oval" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                   f'height="{h:.1f}" rx="{h/2:.1f}"/>')
    elif shape == "rect":
        out.append(f'<rect class="n-rect" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                   f'height="{h:.1f}" rx="{RADIUS}"/>')
    elif shape == "chip":
        out.append(f'<rect class="n-chip" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                   f'height="{h:.1f}" rx="8"/>')
    elif shape == "note":
        out.append(f'<rect class="n-note" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                   f'height="{h:.1f}" rx="{RADIUS}"/>')
    else:  # diamond, corner radius via stroke-linejoin round + inset path
        cx, cy = n["cx"], n["cy"]
        poly = [(cx, y), (x + w, cy), (cx, y + h), (x, cy)]
        out.append(f'<path class="n-dia" d="{rounded_poly(poly, RADIUS)}"/>')
    cls = {"oval": "t-on", "diamond": "t-on", "chip": "t-on",
           "rect": "t-on", "note": "t-note"}[shape]
    lines = n["lines"]
    fs = FS_SUB if shape == "note" else FS_NODE
    top = n["cy"] - (len(lines) - 1) * LINE_H / 2
    for i, l in enumerate(lines):
        out.append(f'<text class="{cls}" x="{n["cx"]:.1f}" y="{top + i*LINE_H:.1f}" '
                   f'font-size="{fs}">{esc(l)}</text>')
    return "\n    ".join(out)


# -------------------------------------------------------------------- render ----

def render(src, out_path=None, title=None, tokens=None):
    if tokens:
        unknown = [k for k in tokens if k not in TOKENS]
        if unknown:
            raise FlowError(f"ไม่รู้จัก token: {', '.join(unknown)} "
                            f"(ใช้ได้: {', '.join(TOKENS)})")
        TOKENS.update(tokens)
    meta, spacing, nodes, order, edges = parse(src)
    dia = size_nodes(nodes)
    W, H = layout(nodes, order, edges, spacing)

    used = {}
    checks = []
    edge_svg, label_svg, meta_edges = [], [], []

    for e in edges:
        s, t = nodes[e["src"]], nodes[e["dst"]]
        sa, ss = pick_anchor(s, e["type"], OUT_PREF, used, "out")
        if SHAPE[t["kind"]] == "diamond":
            ta, ts = DEC_IN          # ทางเข้ารวมที่ปลายซ้าย (ดู flow-rules G6)
        else:
            ta, ts = pick_anchor(t, e["type"], IN_PREF, used, "in")
        A, B = anchor_pt(s, sa, ss), anchor_pt(t, ta, ts)
        pts = route(A, sa, B, ta, e["type"], s, t)
        L = seg_len(pts)
        outside_canvas = any(px < 0 or py < 0 or px > W or py > H for px, py in pts)
        cls = {EDGE_SOLID: "e-solid", EDGE_BACK: "e-back", EDGE_YES: "e-solid",
               EDGE_NO: "e-solid", EDGE_PERM: "e-perm"}[e["type"]]
        marker = "arw-perm" if e["type"] == EDGE_PERM else "arw"
        edge_svg.append(f'<path class="{cls}" marker-end="url(#{marker})" '
                        f'd="{rounded_path(pts)}"/>')
        if e["type"] in (EDGE_SOLID, EDGE_BACK):
            edge_svg.append(f'<circle class="e-dot" cx="{A[0]:.1f}" cy="{A[1]:.1f}" r="6"/>')
        lab = None
        if e["label"]:
            lx, ly = point_at(pts, min(spacing["label"], L * 0.6))
            lw = text_w(e["label"], FS_SUB) + 20
            kls = "l-yes" if e["type"] == EDGE_YES else ("l-no" if e["type"] == EDGE_NO else "l-neutral")
            label_svg.append(
                f'<g class="edge-label"><rect x="{lx-lw/2:.1f}" y="{ly-15:.1f}" '
                f'width="{lw:.1f}" height="30" rx="8"/>'
                f'<text class="{kls}" x="{lx:.1f}" y="{ly:.1f}" font-size="{FS_SUB}">'
                f'{esc(e["label"])}</text></g>')
            lab = {"x": round(lx, 1), "y": round(ly, 1),
                   "dist_from_exit": round(min(spacing["label"], L * 0.6), 1)}
        meta_edges.append({
            "src": e["src"], "dst": e["dst"], "type": e["type"], "label": e["label"],
            "out_anchor": f"{sa}{ss}", "in_anchor": f"{ta}{ts}", "length": round(L, 1),
            "label_pos": lab, "crosses": hits_node(pts, nodes, {e["src"], e["dst"]}),
            "outside_canvas": outside_canvas,
            "line": e["line"],
        })

    flow_meta = {
        "flow": meta["FLOW"] or os.path.basename(src),
        "goal": meta["GOAL"], "owner": meta["OWNER"], "date": meta["DATE"],
        "source": os.path.basename(src),
        "spacing": {"edge_min": spacing["edge"], "label_offset": spacing["label"]},
        "geometry": {"radius": RADIUS, "pad_y": PAD_Y, "pad_x": PAD_X,
                     "diamond": {"w": dia[0], "h": dia[1]}, "elbow_r": ELBOW_R},
        "canvas": {"w": round(W), "h": round(H)},
        "nodes": [{"id": n["id"], "kind": n["kind"], "label": n["label"],
                   "x": round(n["x"], 1), "y": round(n["y"], 1),
                   "w": n["w"], "h": n["h"], "rank": n["rank"], "col": n["col"],
                   "line": n["line"]} for n in (nodes[i] for i in order)],
        "edges": meta_edges,
    }

    legend = LEGEND_HTML
    head = title or flow_meta["flow"] or "User Flow"
    svg_nodes = "\n    ".join(node_svg(nodes[i]) for i in order)
    html = HTML_TMPL.format(
        title=esc(head), flow=esc(flow_meta["flow"]),
        goal=esc(meta["GOAL"]), owner=esc(meta["OWNER"]), date=esc(meta["DATE"]),
        note=esc(meta["NOTE"]),
        W=round(W), H=round(H), legend=legend,
        edges="\n    ".join(edge_svg), labels="\n    ".join(label_svg),
        nodes=svg_nodes, meta=json.dumps(flow_meta, ensure_ascii=False, indent=1),
        counts=f"{len([n for n in order if not n.endswith('__perm')])} กล่อง · "
               f"{len([e for e in edges if e['type'] != EDGE_PERM])} เส้น",
        **{f"t_{k}": v for k, v in TOKENS.items()})

    out_path = out_path or os.path.splitext(src)[0] + ".html"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    with open(os.path.splitext(out_path)[0] + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump(flow_meta, fh, ensure_ascii=False, indent=1)
    return out_path, flow_meta


LEGEND_HTML = """
      <div class="lg">
        <div class="lg-t">Tools in Flow</div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M27 2 L52 13 L27 24 L2 13 Z" fill="var(--primary)"/></svg><span>Diamond Condition — จบประโยคด้วย “ใช่หรือไม่?”</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><rect x="2" y="5" width="50" height="16" rx="8" fill="var(--primary)"/></svg><span>Link To Flow</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><circle cx="6" cy="13" r="4" fill="var(--primary)"/><path d="M10 13 H44" stroke="var(--primary)" stroke-width="3"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>เส้นปกติ — กดแล้วไปหน้าบันทึก และไปต่อ</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><circle cx="6" cy="13" r="4" fill="var(--primary)"/><path d="M10 13 H44" stroke="var(--primary)" stroke-width="3" stroke-dasharray="7 6"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>เส้นปกติ — ย้อนกลับไปหน้าก่อนหน้า</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M2 13 H10" stroke="var(--primary)" stroke-width="3"/><text x="14" y="17" font-size="10" fill="var(--yes)" font-weight="700">Yes</text><path d="M34 13 H44" stroke="var(--primary)" stroke-width="3"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>ทางออก Diamond: Yes</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M2 13 H10" stroke="var(--primary)" stroke-width="3"/><text x="15" y="17" font-size="10" fill="var(--no)" font-weight="700">No</text><path d="M34 13 H44" stroke="var(--primary)" stroke-width="3"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>ทางออก Diamond: No</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M2 13 H50" stroke="var(--perm)" stroke-width="4" stroke-dasharray="9 7" stroke-linecap="round"/></svg><span>หน้านั้น ๆ มี Permutation (Edge Case)</span></div>
      </div>"""


HTML_TMPL = """<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --primary:{t_primary}; --primary-d:{t_primary_d}; --ink:{t_ink}; --muted:{t_muted};
    --paper:{t_paper}; --paper-2:{t_paper_2}; --yes:{t_yes}; --no:{t_no};
    --perm:{t_perm}; --hairline:{t_hairline};
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--paper-2); color:var(--ink);
    font-family:'Sarabun',-apple-system,'Helvetica Neue',sans-serif; }}
  header {{ position:sticky; top:0; z-index:5; background:var(--paper);
    border-bottom:1px solid var(--hairline); padding:20px 28px; }}
  h1 {{ font-size:20px; margin:0 0 4px; font-weight:700; }}
  .sub {{ font-size:13px; color:var(--muted); display:flex; gap:16px; flex-wrap:wrap; }}
  .sub b {{ font-weight:600; color:var(--ink); }}
  main {{ display:flex; gap:24px; align-items:flex-start; padding:24px 28px 80px; }}
  .lg {{ position:sticky; top:104px; flex:0 0 300px; background:var(--paper);
    border:1px solid var(--hairline); border-radius:16px; padding:20px 24px; }}
  .lg-t {{ font-weight:700; font-size:15px; margin-bottom:14px;
    border-bottom:2px solid var(--ink); display:inline-block; padding-bottom:2px; }}
  .lg-i {{ display:flex; gap:12px; align-items:center; margin:12px 0; font-size:12px;
    color:var(--muted); line-height:1.4; }}
  .lg-i svg {{ flex:0 0 54px; }}
  .canvas {{ flex:1 1 auto; min-width:0; overflow:auto; background:var(--paper);
    border:1px solid var(--hairline); border-radius:16px; padding:8px; }}
  .zoomwrap {{ transform-origin:0 0; width:{W}px; }}
  svg.flow {{ display:block; width:{W}px; height:{H}px; }}
  .zoom {{ display:flex; gap:8px; align-items:center; margin-bottom:12px; }}
  .zoom button {{ font-family:inherit; font-size:12px; font-weight:600;
    color:var(--primary); background:var(--paper); border:1px solid var(--primary);
    border-radius:8px; padding:6px 12px; cursor:pointer; }}
  .zoom button[aria-pressed="true"] {{ background:var(--primary); color:#fff; }}
  .n-rect, .n-oval, .n-chip, .n-dia {{ fill:var(--primary); stroke:none; }}
  .n-note {{ fill:#FFFBEC; stroke:var(--perm); stroke-width:2; stroke-dasharray:8 6; }}
  text {{ text-anchor:middle; dominant-baseline:central; font-weight:600;
    font-family:'Sarabun',sans-serif; }}
  .t-on {{ fill:#fff; }} .t-ink {{ fill:var(--ink); }} .t-note {{ fill:#8A6100; }}
  .e-solid {{ fill:none; stroke:var(--primary); stroke-width:3; }}
  .e-back {{ fill:none; stroke:var(--primary); stroke-width:3; stroke-dasharray:9 8; }}
  .e-perm {{ fill:none; stroke:var(--perm); stroke-width:4; stroke-dasharray:10 8;
    stroke-linecap:round; }}
  .e-dot {{ fill:var(--primary); }}
  .edge-label rect {{ fill:var(--paper); stroke:none; }}
  .l-yes {{ fill:var(--yes); font-weight:700; }}
  .l-no {{ fill:var(--no); font-weight:700; }}
  .l-neutral {{ fill:var(--muted); font-weight:700; }}
  @media (max-width:900px) {{ main {{ flex-direction:column; }}
    .lg {{ position:static; flex:1 1 auto; width:100%; }} }}
</style>
</head>
<body>
<header>
  <h1>{flow}</h1>
  <div class="sub">
    <span><b>เป้าหมาย:</b> {goal}</span>
    <span><b>ดีไซเนอร์:</b> {owner}</span>
    <span><b>วันที่:</b> {date}</span>
    <span>{counts}</span>
  </div>
</header>
<main>
  {legend}
  <div class="canvas">
    <div class="zoom" role="group" aria-label="ย่อ/ขยาย">
      <button data-z="fit" aria-pressed="true">พอดีจอ</button>
      <button data-z="0.5">50%</button>
      <button data-z="1">100% (ขนาดจริง)</button>
      <span style="font-size:12px;color:var(--muted)">canvas {W}×{H}px</span>
    </div>
    <div class="zoomwrap" id="zw">
    <svg class="flow" viewBox="0 0 {W} {H}" role="img" aria-labelledby="ttl dsc">
      <title id="ttl">{flow}</title>
      <desc id="dsc">User flow diagram — {goal}</desc>
      <defs>
        <marker id="arw" viewBox="0 0 12 12" refX="9" refY="6" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
          <path d="M2 1 L10 6 L2 11" fill="none" stroke="{t_primary}" stroke-width="2.4"
            stroke-linecap="round" stroke-linejoin="round"/>
        </marker>
        <marker id="arw-perm" viewBox="0 0 12 12" refX="9" refY="6" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
          <path d="M2 1 L10 6 L2 11" fill="none" stroke="{t_perm}" stroke-width="2.4"
            stroke-linecap="round" stroke-linejoin="round"/>
        </marker>
      </defs>
    {edges}
    {nodes}
    {labels}
    </svg>
    </div>
  </div>
</main>
<script>
  (function () {{
    var zw = document.getElementById('zw'), W = {W}, H = {H};
    var wrap = zw.parentElement, mode = 'fit';
    function apply() {{
      var k = mode === 'fit' ? Math.min(1, (wrap.clientWidth - 24) / W) : parseFloat(mode);
      zw.style.transform = 'scale(' + k + ')';
      zw.style.height = (H * k) + 'px';
    }}
    document.querySelectorAll('.zoom button').forEach(function (b) {{
      b.addEventListener('click', function () {{
        mode = b.dataset.z;
        document.querySelectorAll('.zoom button').forEach(function (o) {{
          o.setAttribute('aria-pressed', String(o === b));
        }});
        apply();
      }});
    }});
    addEventListener('resize', function () {{ if (mode === 'fit') apply(); }});
    apply();
  }})();
</script>
<script type="application/json" id="flow-meta">
{meta}
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="render a .flow file to HTML+SVG")
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    ap.add_argument("--title")
    ap.add_argument("--tokens", help="ไฟล์ JSON override สีของทีม/โปรเจกต์ "
                                     "เช่น {\"primary\":\"#1E40AF\"}")
    a = ap.parse_args()
    try:
        tk = json.load(open(a.tokens, encoding="utf-8")) if a.tokens else None
        out, m = render(a.src, a.out, a.title, tk)
    except FlowError as e:
        print(f"✖ {e}", file=sys.stderr)
        sys.exit(2)
    print(f"✔ {out}")
    print(f"  {len(m['nodes'])} nodes · {len(m['edges'])} edges · "
          f"canvas {m['canvas']['w']}×{m['canvas']['h']} · "
          f"diamond {m['geometry']['diamond']['w']}×{m['geometry']['diamond']['h']}")


if __name__ == "__main__":
    main()
