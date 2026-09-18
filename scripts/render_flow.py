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
    "primary":   "#4060d0",   # ค่าของทีม (flow-template.md ตารางสี บรรทัด 224)
    "primary_d": "#334DA6",   # เฉดเข้มของ primary
    "ink":       "#17181C",
    "muted":     "#6B7280",
    "paper":     "#FFFFFF",
    "paper_2":   "#F7F8FB",
    "yes":       "#1b991e",   # ค่าของทีม (F-06) — ตัวอักษร Yes
    "no":        "#b82121",   # ค่าของทีม (F-06) — ตัวอักษร No
    "perm":      "#ffce2c",   # ค่าของทีม (F-07) — เส้น permutation
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
# F-14 (docs/flow/flow-template.md): "Diamond ใน user flow = สี่เหลี่ยมจัตุรัส
# ทุกอันขนาดเท่ากัน · ข้อความยาวขึ้นให้สเกลสมมาตร ห้ามยืดเป็นทรงแบน"
DIAMOND_MIN     = (280, 280)    # ขนาดพื้น (w, h) — จัตุรัส, ยืดพร้อมกันทุกอันเมื่อข้อความยาว
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

# กฎจุดเชื่อมของทีมพูดถึง "รูปแบบเส้น" ที่ตาเห็น ไม่ใช่ความหมายของเส้น:
# เส้นทึบ (ปกติ / Yes / No) หน้าตาเหมือนกัน จึงใช้จุดเดียวกันได้ — เป็นจุดรวมเส้น
# เส้นประน้ำเงิน (ย้อนกลับ) กับเส้นประเหลือง (permutation) เป็นรูปแบบอื่น ต้องไปมุมอื่น
STYLE = {EDGE_SOLID: "solid", EDGE_YES: "solid", EDGE_NO: "solid",
         EDGE_BACK: "dashed", EDGE_PERM: "perm"}
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


AUTHOR_RE = re.compile(r"\[AI-([A-Za-z0-9_.-]+)\]\s*(.*)")


def authorship(comment):
    """คอมเมนต์ท้ายบรรทัดที่ขึ้นต้นด้วย [AI-xx] = บรรทัดนี้มาจากข้อเสนอของ AI

    คอมเมนต์อื่นไม่นับเป็น authorship — ของดีไซเนอร์ถือเป็นค่าตั้งต้นเสมอ
    """
    m = AUTHOR_RE.search(comment or "")
    if not m:
        return {"author": "human"}
    return {"author": "ai", "ai_id": m.group(1), "note": m.group(2).strip()}


def author_fields(d):
    """หยิบเฉพาะ field authorship ที่มีจริง — ของเดิมที่ไม่มีจะได้ human เป็นค่าตั้งต้น"""
    out = {"author": d.get("author", "human")}
    for k in ("ai_id", "note"):
        if d.get(k):
            out[k] = d[k]
    return out


def parse(path):
    meta = {"FLOW": "", "GOAL": "", "OWNER": "", "DATE": "", "NOTE": ""}
    spacing = {"edge": 500.0, "label": 100.0}
    nodes, edges, perms, order = {}, [], [], []
    closed = []

    with open(path, encoding="utf-8") as fh:
        raw = fh.readlines()

    for ln, line in enumerate(raw, 1):
        if line.strip().startswith("#"):
            line, comment = "", ""
        else:
            line, _, comment = line.partition("#")
            line, comment = line.rstrip(), comment.strip()
        if not line.strip():
            continue
        who = authorship(comment)
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
        if key == "CLOSED":
            nid, _, why = rest.partition(" ")
            closed.append((nid, why.strip() or "ปิดแล้ว", ln))
            continue
        if key == "PERM":
            nid, _, text = rest.partition(" ")
            perms.append((nid, text.strip() or "มี Permutation (Edge Case)", ln, who))
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
            nodes[nid] = {"id": nid, "kind": kind, "label": label.strip(),
                          "line": ln, **who}
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
        edges.append({"src": src, "dst": dst, "type": etype, "label": label,
                      "line": ln, **who})

    # PERM sugar -> a real note node + a perm edge, so it lays out like anything else
    for nid, text, ln, who in perms:
        if nid not in nodes:
            raise FlowError(f"บรรทัด {ln}: PERM ชี้ไป node '{nid}' ที่ไม่มีอยู่")
        note = f"{nid}__perm"
        nodes[note] = {"id": note, "kind": "PERMNOTE", "label": text,
                       "line": ln, **who}
        order.append(note)
        edges.append({"src": nid, "dst": note, "type": EDGE_PERM, "label": "",
                      "line": ln, **who})
        nodes[nid]["has_perm"] = True

    for nid, why, ln in closed:
        if nid not in nodes:
            raise FlowError(f"บรรทัด {ln}: CLOSED ชี้ไป node '{nid}' ที่ไม่มีอยู่")
        nodes[nid]["closed"] = why

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
    """เลือกจุดเชื่อมตามกฎของทีม โดยแยกสองทิศคนละเกณฑ์

    **ทางออก** — เส้นแต่ละประเภทต้องออกคนละด้าน เพราะทางออกของข้าวหลามตัดต้องอ่านออกว่า
    เส้นไหนคือ Yes เส้นไหนคือ No ถ้าซ้อนจุดกันจะเหลือเส้นเดียวที่มีป้ายทับกัน

    **ทางเข้า** — เส้นรูปแบบเดียวกันรวมจุดกันได้ (เป็นจุดรวมเส้น) เพราะทุกเส้นที่เข้ามา
    หมายถึง "ไหลเข้ากล่องนี้" เหมือนกันหมด ส่วนเส้นคนละรูปแบบ (ประน้ำเงิน / ประเหลือง)
    ต้องไปเกาะด้านอื่นของกล่อง

    การขยับจุดบนด้านเดิมใช้เป็นทางออกสุดท้ายเมื่อทั้งสี่ด้านถูกจองไปหมดแล้ว
    """
    reg = used.setdefault(node["id"], {})        # (side, slot) -> (group, way)
    group = etype if way == "out" else STYLE[etype]
    mine = (group, way)

    def groups_on(side):
        return {v for k, v in reg.items() if k[0] == side}

    for side in pref[etype]:                     # จุดที่เป็นของเราอยู่แล้ว
        for slot in (0, 1, 2):
            if reg.get((side, slot)) == mine:
                return side, slot
    for side in pref[etype]:                     # ด้านที่ยังไม่มีใครใช้
        if not groups_on(side):
            reg[(side, 0)] = mine
            return side, 0
    for side in pref[etype]:                     # ด้านที่มีแต่พวกเดียวกัน
        if groups_on(side) <= {mine}:
            for slot in (0, 1, 2):
                if (side, slot) not in reg:
                    reg[(side, slot)] = mine
                    return side, slot
    for side in pref[etype]:                     # ทางออกสุดท้าย
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


# data-* มีไว้ให้ตัวแก้ผังใน .html เท่านั้น — .svg/.png ที่ส่งต่อให้ Figma/dev
# ต้องสะอาดเหมือนเดิม จึงถอดออกก่อนเขียนไฟล์ภาพ
DATA_ATTR_RE = re.compile(
    r'\s(?:data-(?:e|from|to|op|pts|dist|lw|id|kind|x|y|w|h))="[^"]*"')


def plain(frag):
    return DATA_ATTR_RE.sub("", frag)


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
    inner = "\n      ".join(out)
    # data-* คือสิ่งที่ตัวแก้ผังใน .html ใช้ — ไม่มีผลกับ .svg/.png ที่ส่งต่อ
    return (f'<g id="n-{esc(n["id"])}" class="node" data-id="{esc(n["id"])}" '
            f'data-kind="{shape}" data-x="{x:.1f}" data-y="{y:.1f}" '
            f'data-w="{w:.1f}" data-h="{h:.1f}">\n      '
            f'{inner}\n    </g>')


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

    for ei, e in enumerate(edges):
        s, t = nodes[e["src"]], nodes[e["dst"]]
        sa, ss = pick_anchor(s, e["type"], OUT_PREF, used, "out")
        if SHAPE[t["kind"]] == "diamond" and STYLE[e["type"]] == "solid":
            ta, ts = DEC_IN          # เส้นทึบทุกเส้นรวมที่ปลายซ้าย (ดู flow-rules G6)
            used.setdefault(t["id"], {})[(ta, ts)] = (STYLE[EDGE_SOLID], "in")
        else:
            ta, ts = pick_anchor(t, e["type"], IN_PREF, used, "in")
        A, B = anchor_pt(s, sa, ss), anchor_pt(t, ta, ts)
        pts = route(A, sa, B, ta, e["type"], s, t)
        L = seg_len(pts)
        outside_canvas = any(px < 0 or py < 0 or px > W or py > H for px, py in pts)
        cls = {EDGE_SOLID: "e-solid", EDGE_BACK: "e-back", EDGE_YES: "e-solid",
               EDGE_NO: "e-solid", EDGE_PERM: "e-perm"}[e["type"]]
        marker = "arw-perm" if e["type"] == EDGE_PERM else "arw"
        raw = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        op = {EDGE_SOLID: "->", EDGE_BACK: "..>", EDGE_YES: "-Yes->",
              EDGE_NO: "-No->", EDGE_PERM: "~>"}[e["type"]]
        edge_svg.append(f'<path class="{cls}" marker-end="url(#{marker})" '
                        f'data-e="{ei}" data-from="{esc(s["id"])}" '
                        f'data-to="{esc(t["id"])}" data-op="{op}" data-pts="{raw}" '
                        f'd="{rounded_path(pts)}"/>')
        if e["type"] in (EDGE_SOLID, EDGE_BACK):
            edge_svg.append(f'<circle class="e-dot" data-e="{ei}" '
                            f'cx="{A[0]:.1f}" cy="{A[1]:.1f}" r="6"/>')
        lab = None
        if e["label"]:
            lx, ly = point_at(pts, min(spacing["label"], L * 0.6))
            lw = text_w(e["label"], FS_SUB) + 20
            kls = "l-yes" if e["type"] == EDGE_YES else ("l-no" if e["type"] == EDGE_NO else "l-neutral")
            label_svg.append(
                f'<g class="edge-label" data-e="{ei}" '
                f'data-dist="{min(spacing["label"], L * 0.6):.1f}" '
                f'data-lw="{lw:.1f}">'
                f'<rect x="{lx-lw/2:.1f}" y="{ly-15:.1f}" '
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
            **author_fields(e),
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
                   "line": n["line"], **author_fields(n)}
                  for n in (nodes[i] for i in order)],
        "edges": meta_edges,
    }

    legend = LEGEND_HTML
    ledger = build_ledger(flow_meta)
    report = build_report(run_checks(meta, nodes, order, edges, flow_meta), nodes)
    head = title or flow_meta["flow"] or "User Flow"
    svg_nodes = "\n    ".join(node_svg(nodes[i]) for i in order)
    out_path = out_path or os.path.splitext(src)[0] + ".html"
    stem = os.path.splitext(out_path)[0]

    # ── ไฟล์รูปแยก สำหรับแปะ Jira / Lark / ส่ง dev ──────────────────────
    # ทำก่อน HTML เพื่อให้ exports ที่ฝังใน .html ตรงกับ .meta.json
    alt = esc(flow_meta["flow"] or "User Flow")
    tok = {f"t_{k}": v for k, v in TOKENS.items()}
    body = plain("\n".join(edge_svg) + "\n" + svg_nodes + "\n" + "\n".join(label_svg))
    svg_path = stem + ".svg"
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(SVG_TMPL.format(W=round(W), H=round(H), alt=alt,
                                 edges=plain("\n".join(edge_svg)),
                                 nodes=plain(svg_nodes),
                                 labels=plain("\n".join(label_svg)), **tok))

    # .png ต้องผ่านผืนจัตุรัสก่อน เพราะ qlmanage คืนรูปจัตุรัสเสมอ (ดู write_png)
    side = max(round(W), round(H))
    square_svg = SVG_TMPL.format(
        W=side, H=side, alt=alt,
        edges=f'<g transform="translate({(side - W) / 2:.1f},{(side - H) / 2:.1f})">'
              f"\n{body}\n</g>",
        nodes="", labels="", **tok)
    png_path = write_png(square_svg, stem + ".png", W, H, side,
                         max(1200, min(side, 4000)))
    flow_meta["exports"] = {
        "html": os.path.basename(out_path),
        "svg": os.path.basename(svg_path),
        "png": os.path.basename(png_path) if png_path else None,
    }

    html = HTML_TMPL.format(
        title=esc(head), flow=esc(flow_meta["flow"]),
        goal=esc(meta["GOAL"]), owner=esc(meta["OWNER"]), date=esc(meta["DATE"]),
        note=esc(meta["NOTE"]),
        W=round(W), H=round(H), legend=legend, ledger=ledger, report=report,
        edges="\n    ".join(edge_svg), labels="\n    ".join(label_svg),
        nodes=svg_nodes, editor_js=EDITOR_JS,
        # ต้นฉบับ .flow ฝังไว้ให้หน้าเว็บสร้างไฟล์ที่แก้แล้วให้ดาวน์โหลดได้
        # "</" ถูก escape กัน string ไปปิดแท็ก <script> เอง
        src_json=json.dumps(
            open(src, encoding="utf-8").read(), ensure_ascii=False
        ).replace("</", "<\\/"),
        src_name=esc(os.path.basename(src)),
        meta=json.dumps(flow_meta, ensure_ascii=False, indent=1),
        counts=f"{len([n for n in order if not n.endswith('__perm')])} กล่อง · "
               f"{len([e for e in edges if e['type'] != EDGE_PERM])} เส้น",
        **tok)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    with open(stem + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump(flow_meta, fh, ensure_ascii=False, indent=1)
    return out_path, flow_meta


SVG_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
  viewBox="0 0 {W} {H}" role="img" aria-label="{alt}">
<title>{alt}</title>
<style>
  text {{ text-anchor:middle; dominant-baseline:central; font-weight:600;
    font-family:'Sarabun','Noto Sans Thai','Helvetica Neue',sans-serif; }}
  .n-rect, .n-oval, .n-chip, .n-dia {{ fill:{t_primary}; stroke:none; }}
  .n-note {{ fill:#FFFBEC; stroke:{t_perm}; stroke-width:2; stroke-dasharray:8 6; }}
  .t-on {{ fill:#fff; }} .t-ink {{ fill:{t_ink}; }} .t-note {{ fill:#8A6100; }}
  .e-solid {{ fill:none; stroke:{t_primary}; stroke-width:3; }}
  .e-back {{ fill:none; stroke:{t_primary}; stroke-width:3; stroke-dasharray:9 8; }}
  .e-perm {{ fill:none; stroke:{t_perm}; stroke-width:4; stroke-dasharray:10 8;
    stroke-linecap:round; }}
  .e-dot {{ fill:{t_primary}; }}
  .edge-label rect {{ fill:{t_paper}; stroke:none; }}
  .l-yes {{ fill:{t_yes}; font-weight:700; }}
  .l-no {{ fill:{t_no}; font-weight:700; }}
  .l-neutral {{ fill:{t_muted}; font-weight:700; }}
</style>
<rect width="{W}" height="{H}" fill="{t_paper}"/>
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
"""


def write_png(square_svg, png_path, W, H, side, px):
    """แปลงผังเป็น .png ด้วย qlmanage + sips ที่ macOS แถมมาให้ (ไม่ต้องลงอะไรเพิ่ม)

    qlmanage คืนรูปเป็นสี่เหลี่ยมจัตุรัสเสมอ ถ้าส่ง SVG แนวนอนเข้าไปตรง ๆ ขวาจะโดนตัด
    จึงวางผังไว้กลางผืนจัตุรัสก่อน แล้วค่อย crop กลับเป็นอัตราส่วนจริง

    เครื่องที่ไม่มี qlmanage/sips (Windows/Linux) ข้ามไปเงียบ ๆ ไม่ถือเป็น error
    """
    import shutil
    import subprocess
    import tempfile

    if not (shutil.which("qlmanage") and shutil.which("sips")):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        sq_path = os.path.join(tmp, "square.svg")
        with open(sq_path, "w", encoding="utf-8") as fh:
            fh.write(square_svg)
        try:
            subprocess.run(["qlmanage", "-t", "-s", str(px), "-o", tmp, sq_path],
                           check=True, capture_output=True, timeout=180)
            made = [f for f in os.listdir(tmp) if f.lower().endswith(".png")]
            if not made:
                return None
            shot = os.path.join(tmp, made[0])
            scale = px / side
            subprocess.run(
                ["sips", "--cropToHeightWidth",
                 str(max(1, round(H * scale))), str(max(1, round(W * scale))), shot],
                check=True, capture_output=True, timeout=120)
            shutil.move(shot, png_path)
        except (subprocess.SubprocessError, OSError):
            return None
    return png_path


SEV_LABEL = {"R0": "ต้องแก้ก่อนส่ง", "R1": "ควรแก้", "R2": "ข้อสังเกต"}


def run_checks(hdr, nodes, order, edges, flow_meta):
    """เรียก self_check.py ตัวจริงมาใช้ — กฎมีที่เดียว ไม่เขียนซ้ำในนี้

    import ข้างในฟังก์ชันเพราะ self_check import จากไฟล์นี้ (กัน circular import)
    """
    try:
        import self_check as sc
    except ImportError:
        return None
    rows = sc.check_draft_gate(hdr, nodes, order)
    rows += sc.check_source(nodes, order, edges)
    rows += sc.check_rounds(nodes, order)
    rows += sc.check_authorship(nodes, order, edges)
    rows += sc.check_coverage(nodes, order, edges)
    rows += sc.check_geometry(flow_meta)
    rows.sort(key=lambda r: (sc.SEV_ORDER[r["sev"]], r["code"]))
    return rows


def build_report(rows, nodes):
    """บล็อกผลตรวจใต้ผัง — คลิกชื่อกล่องแล้วกระโดดไปหาบนผังได้"""
    if rows is None:
        return ""
    counts = {s: sum(1 for r in rows if r["sev"] == s) for s in ("R0", "R1", "R2")}
    pills = "".join(
        f'<span class="rp-pill rp-p{s.lower()}">{s} {counts[s]}</span>'
        for s in ("R0", "R1", "R2"))

    if not rows:
        body = '<p class="rp-ok">ผ่านทุกข้อ — ไม่มีอะไรค้าง</p>'
    else:
        items = []
        for i, r in enumerate(rows, 1):
            targets = [w.strip() for w in str(r["where"]).split(",") if w.strip()]
            chips = "".join(
                f'<button class="rp-go" data-go="n-{esc(t)}" type="button">'
                f'{esc(nodes[t]["label"])}</button>'
                for t in targets if t in nodes)
            if chips:
                where = f'<div class="rp-where">{chips}</div>'
            elif r["where"]:
                where = f'<div class="rp-where rp-plain">{esc(str(r["where"]))}</div>'
            else:
                where = ""
            rid = f'{r["code"]}-{i}'
            items.append(
                f'<li class="rp-item rp-i{r["sev"].lower()}" id="i-{rid}"'
                f' data-id="{rid}" data-code="{esc(r["code"])}"'
                f' data-msg="{esc(r["msg"])}">'
                f'<div class="rp-head"><span class="rp-sev">{r["sev"]}</span>'
                f'<span class="rp-code">{esc(r["code"])}</span>'
                f'<span class="rp-sevname">{SEV_LABEL[r["sev"]]}</span></div>'
                f'<div class="rp-msg">{esc(r["msg"])}</div>{where}'
                f'<div class="rp-dec">'
                f'<button class="rp-yes" data-id="{rid}" data-v="yes" type="button"'
                f' aria-pressed="false">รับ</button>'
                f'<button class="rp-no" data-id="{rid}" data-v="no" type="button"'
                f' aria-pressed="false">ไม่รับ</button></div></li>')
        body = ('<div class="rp-bar"><span id="rp-count"></span>'
                '<button id="rp-copy" type="button">คัดลอกผลกลับไปวางในแชท</button>'
                '<button id="rp-reset" type="button">ล้างคำตัดสิน</button></div>'
                '<ul class="rp-list">' + "".join(items) + "</ul>"
                '<textarea id="rp-out" rows="4" readonly'
                ' placeholder="กดคัดลอกแล้วข้อความจะมาอยู่ตรงนี้"></textarea>')

    return ('\n      <div class="rp">\n'
            '        <div class="rp-t">ผลตรวจกฎ</div>\n'
            f'        <div class="rp-pills">{pills}</div>\n'
            f'        {body}\n'
            '        <p class="rp-foot">รหัสกฎอ้างอิงจาก <code>flow-rules.md</code> — '
            'R0 ต้องแก้ก่อนส่ง · R1 ควรแก้ · R2 ดีไซเนอร์ตัดสินใจ</p>\n'
            '      </div>')


LEDGER_LIMIT = 0.40


def build_ledger(flow_meta):
    """บล็อกบอกที่มาของกล่อง — ใครคิด ไม่ใช่กล่องนั้นคืออะไร

    ไม่แตะ geometry และไม่เพิ่มสีลงผัง (style-guide §สีเน้นใช้ประหยัด)
    """
    boxes = [n for n in flow_meta["nodes"] if n["kind"] != "PERMNOTE"]
    ai_boxes = [n for n in boxes if n.get("author") == "ai"]
    ai_edges = [e for e in flow_meta["edges"] if e.get("author") == "ai"]
    total = len(boxes) or 1
    pct = len(ai_boxes) / total

    if not ai_boxes and not ai_edges:
        return ('\n      <div class="led">\n'
                '        <div class="led-t">ที่มาของ flow</div>\n'
                f'        <p class="led-all">ทุกกล่องทั้ง {total} กล่องมาจากดีไซเนอร์ '
                'ยังไม่มีข้อเสนอของ AI ที่ถูกอนุมัติลงผัง</p>\n'
                '      </div>')

    rows = []
    for n in ai_boxes:
        rows.append(f'<tr><td class="led-k">กล่อง</td><td>{esc(n["label"])}</td>'
                    f'<td class="led-id">{esc(n.get("ai_id", ""))}</td>'
                    f'<td>{esc(n.get("note", ""))}</td></tr>')
    for e in ai_edges:
        rows.append(f'<tr><td class="led-k">เส้น</td>'
                    f'<td>{esc(e["src"])} → {esc(e["dst"])}</td>'
                    f'<td class="led-id">{esc(e.get("ai_id", ""))}</td>'
                    f'<td>{esc(e.get("note", ""))}</td></tr>')

    warn = ""
    if pct > LEDGER_LIMIT:
        warn = ('<p class="led-warn">⚠ กล่องที่มาจากข้อเสนอของ AI เกิน '
                f'{LEDGER_LIMIT:.0%} ของทั้งใบ — ใบนี้กำลังกลายเป็น flow ของ AI '
                'ควรถอยไปคุยโครงกับดีไซเนอร์ก่อน</p>')

    return ('\n      <div class="led">\n'
            '        <div class="led-t">ที่มาของ flow</div>\n'
            f'        <p class="led-sum">ดีไซเนอร์เขียน <b>{total - len(ai_boxes)}</b> กล่อง '
            f'· มาจากข้อเสนอของ AI ที่อนุมัติแล้ว <b>{len(ai_boxes)}</b> กล่อง '
            f'(<b>{pct:.0%}</b> ของทั้งใบ) และ <b>{len(ai_edges)}</b> เส้น</p>\n'
            f'        {warn}\n'
            '        <table class="led-tb"><thead><tr><th>ชนิด</th><th>ที่ไหน</th>'
            '<th>ข้อเสนอ</th><th>เหตุผล</th></tr></thead><tbody>\n          '
            + "\n          ".join(rows)
            + '\n        </tbody></table>\n      </div>')


LEGEND_HTML = """
      <div class="lg" id="lg">
        <button type="button" class="lg-t" id="lg-toggle" aria-expanded="true"
          aria-controls="lg-body"><span>Tools in Flow</span><span class="lg-caret" aria-hidden="true">▾</span></button>
        <div class="lg-b" id="lg-body">
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><rect x="2" y="3" width="50" height="20" rx="10" fill="var(--primary)"/></svg><span>วงรี — จุดเริ่มต้นและจุดสิ้นสุด</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><rect x="2" y="3" width="50" height="20" rx="4" fill="var(--primary)"/></svg><span>สี่เหลี่ยมผืนผ้า — Process การกระทำหรือขั้นตอน (ใช้บ่อยที่สุด)</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M27 2 L52 13 L27 24 L2 13 Z" fill="var(--primary)"/></svg><span>Diamond Condition — จบประโยคด้วย “ใช่หรือไม่?”</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><rect x="2" y="5" width="50" height="16" rx="8" fill="var(--primary)"/></svg><span>Link To Flow</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><circle cx="6" cy="13" r="4" fill="var(--primary)"/><path d="M10 13 H44" stroke="var(--primary)" stroke-width="3"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>เส้นปกติ — กดแล้วไปหน้าบันทึก และไปต่อ</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><circle cx="6" cy="13" r="4" fill="var(--primary)"/><path d="M10 13 H44" stroke="var(--primary)" stroke-width="3" stroke-dasharray="7 6"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>เส้นปกติ — ย้อนกลับไปหน้าก่อนหน้า</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M2 13 H10" stroke="var(--primary)" stroke-width="3"/><text x="14" y="17" font-size="10" fill="var(--yes)" font-weight="700">Yes</text><path d="M34 13 H44" stroke="var(--primary)" stroke-width="3"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>ทางออก Diamond: Yes</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M2 13 H10" stroke="var(--primary)" stroke-width="3"/><text x="15" y="17" font-size="10" fill="var(--no)" font-weight="700">No</text><path d="M34 13 H44" stroke="var(--primary)" stroke-width="3"/><path d="M42 7 l8 6 -8 6" fill="none" stroke="var(--primary)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg><span>ทางออก Diamond: No</span></div>
        <div class="lg-i"><svg width="54" height="26" viewBox="0 0 54 26"><path d="M2 13 H50" stroke="var(--perm)" stroke-width="4" stroke-dasharray="9 7" stroke-linecap="round"/></svg><span>หน้านั้น ๆ มี Permutation (Edge Case)</span></div>
        </div>
      </div>"""


# ── ตัวแก้ผังในหน้า (ย่อ/ขยาย · ลากกล่อง · ลากเส้น · ย้อน) ──────────────
# ส่งเข้า HTML_TMPL เป็น "ค่า" ไม่ใช่ส่วนของ template — วงเล็บปีกกาจึงเขียนปกติ
# ไม่ต้อง escape · แก้ที่นี่ที่เดียว ไม่มี JS ซ่อนอยู่ที่อื่น
#
# ขอบเขตโดยตั้งใจ: การลากในหน้านี้ "ไม่" เขียนกลับลง .flow และไม่เปลี่ยน
# .meta.json — geometry ที่ใช้ตรวจ G1–G9 ยังมาจาก render_flow.py ที่เดียว
# (flow-rules.md §3) หน้านี้จึงมีปุ่มคืนตำแหน่งเดิมเสมอ
EDITOR_JS = r"""
(function () {
  var svg = document.querySelector('svg.flow');
  var zw = document.getElementById('zw');
  if (!svg || !zw) return;
  var wrap = zw.parentElement;
  var vb = (svg.getAttribute('viewBox') || '0 0 1000 1000').trim().split(/\s+/).map(Number);
  var W = vb[2], H = vb[3];
  var ELBOW = 16, MINK = 0.05, MAXK = 4;

  // ── ย่อ/ขยาย ────────────────────────────────────────────────────────
  var scale = null;                       // null = พอดีจอ
  function fitK() { return Math.min(1, (wrap.clientWidth - 24) / W); }
  function k() { return scale === null ? fitK() : scale; }
  function paintZoom() {
    var v = k();
    zw.style.transform = 'scale(' + v + ')';
    zw.style.height = (H * v) + 'px';
    var now = document.getElementById('z-now');
    if (now) now.textContent = Math.round(v * 100) + '%';
    document.querySelectorAll('.zoom button[data-z]').forEach(function (b) {
      var on = b.dataset.z === 'fit' ? scale === null : parseFloat(b.dataset.z) === scale;
      b.setAttribute('aria-pressed', String(on));
    });
  }
  function setScale(v) { scale = Math.max(MINK, Math.min(MAXK, v)); paintZoom(); }
  function step(f) { setScale(k() * f); }
  document.querySelectorAll('.zoom button[data-z]').forEach(function (b) {
    b.addEventListener('click', function () {
      scale = b.dataset.z === 'fit' ? null : parseFloat(b.dataset.z);
      paintZoom();
    });
  });
  var zi = document.getElementById('z-in'), zo = document.getElementById('z-out');
  if (zi) zi.addEventListener('click', function () { step(1.25); });
  if (zo) zo.addEventListener('click', function () { step(0.8); });
  addEventListener('resize', function () { if (scale === null) paintZoom(); });

  // ── Tools in Flow ปิด/เปิดได้ จำไว้ในเครื่อง ─────────────────────────
  var lgBtn = document.getElementById('lg-toggle');
  if (lgBtn) {
    var LKEY = 'flowlegend';
    try {
      if (localStorage.getItem(LKEY) === 'closed') lgBtn.setAttribute('aria-expanded', 'false');
    } catch (e) {}
    lgBtn.addEventListener('click', function () {
      var open = lgBtn.getAttribute('aria-expanded') !== 'true';
      lgBtn.setAttribute('aria-expanded', String(open));
      try { localStorage.setItem(LKEY, open ? 'open' : 'closed'); } catch (e) {}
    });
  }

  // ── โมเดลของผัง ─────────────────────────────────────────────────────
  var nodeEls = {}, off = {};             // off[id] = [dx, dy]
  [].forEach.call(svg.querySelectorAll('g.node'), function (g) {
    var id = g.dataset.id;
    if (!id) return;
    nodeEls[id] = g;
    off[id] = [0, 0];
  });

  var E = [];                             // E[i] = {path, hit, dot, label, pts, from, to}
  [].forEach.call(svg.querySelectorAll('path[data-e]'), function (p) {
    var i = +p.dataset.e;
    E[i] = {
      path: p, from: p.dataset.from, to: p.dataset.to,
      of: p.dataset.from, ot: p.dataset.to, op: p.dataset.op || '->',
      shaped: false,                      // true เมื่อคนจัดรูปเส้นนี้ด้วยมือแล้ว
      pts: (p.dataset.pts || '').split(' ').filter(Boolean).map(function (s) {
        var a = s.split(','); return [+a[0], +a[1]];
      }),
      dot: svg.querySelector('circle.e-dot[data-e="' + i + '"]'),
      label: svg.querySelector('g.edge-label[data-e="' + i + '"]')
    };
    var hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    hit.setAttribute('class', 'e-hit');
    hit.dataset.e = String(i);
    hit.setAttribute('d', p.getAttribute('d'));
    p.parentNode.insertBefore(hit, p);
    E[i].hit = hit;
  });

  function clonePts(ps) { return ps.map(function (q) { return [q[0], q[1]]; }); }

  // เก็บกวาดจุดก่อนวาดเสมอ — จุดซ้ำ จุดที่อยู่แนวเดียวกัน และการ "ย้อนกลับ"
  // บนแกนเดิม ถ้าปล่อยไว้ ตัวลบมุมจะวาดส่วนโค้ง 180° กลายเป็นวงกลมโป่งกลางเส้น
  function tidy(ps) {
    var p = clonePts(ps), moved = true;
    while (moved && p.length > 2) {
      moved = false;
      for (var i = 1; i < p.length - 1; i++) {
        var a = p[i - 1], b = p[i], c = p[i + 1];
        var same = Math.abs(a[0] - b[0]) < 0.5 && Math.abs(a[1] - b[1]) < 0.5;
        var cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
        if (same || Math.abs(cross) < 0.5) {   // ซ้ำ · ตรงต่อกัน · หรือย้อนกลับ
          p.splice(i, 1);
          moved = true;
          break;
        }
      }
    }
    return p;
  }

  // ── กล่อง: กรอบปัจจุบัน · จุดเกาะ 4 ด้าน · หากล่องใต้เมาส์ ────────────
  function rectOf(id) {
    var g = nodeEls[id], o = off[id] || [0, 0];
    return { x: +g.dataset.x + o[0], y: +g.dataset.y + o[1],
             w: +g.dataset.w, h: +g.dataset.h };
  }
  // จุดเกาะ: กล่องสี่เหลี่ยมมี 3 ช่องต่อด้าน (25/50/75%) เส้นหลายเส้นจะได้
  // ไม่กระจุกจุดเดียว · ข้าวหลามตัดมีช่องเดียวคือปลายแหลม เพราะ 25/75 ของ
  // กรอบจะไปตกในมุมที่ไม่มีรูปทรงอยู่จริง (และ G6 ให้เส้นทึบรวมที่ปลายซ้ายอยู่แล้ว)
  function slots(id, side) {
    var r = rectOf(id);
    var dia = nodeEls[id].dataset.kind === 'diamond';
    var ts = dia ? [0.5] : [0.25, 0.5, 0.75];
    return ts.map(function (t) {
      if (side === 'L') return [r.x, r.y + r.h * t];
      if (side === 'R') return [r.x + r.w, r.y + r.h * t];
      if (side === 'T') return [r.x + r.w * t, r.y];
      return [r.x + r.w * t, r.y + r.h];
    });
  }
  function anchors(id) {
    var out = [];
    ['L', 'R', 'T', 'B'].forEach(function (d) {
      slots(id, d).forEach(function (p) { out.push({ p: p, d: d }); });
    });
    return out;
  }
  // จุดปลายเส้นอื่น ๆ ที่เกาะกล่องนี้อยู่แล้ว — ใช้เลี่ยงไม่ให้ทับกัน
  function takenOn(id, exceptI) {
    var out = [];
    for (var i = 0; i < E.length; i++) {
      if (i === exceptI || !E[i]) continue;
      var e = E[i], p = e.pts;
      if (e.from === id) out.push(p[0]);
      if (e.to === id) out.push(p[p.length - 1]);
    }
    return out;
  }
  function nodeAt(x, y) {
    var hitId = null;
    Object.keys(nodeEls).forEach(function (id) {
      var r = rectOf(id);
      if (x >= r.x - 10 && x <= r.x + r.w + 10 &&
          y >= r.y - 10 && y <= r.y + r.h + 10) hitId = id;
    });
    return hitId;
  }
  function nearestAnchor(id, x, y) {
    var best = null, bd = Infinity;
    anchors(id).forEach(function (a) {
      var d = Math.hypot(a.p[0] - x, a.p[1] - y);
      if (d < bd) { bd = d; best = a; }
    });
    return best;
  }

  // ── เดินเส้นใหม่แบบมุมฉาก เมื่อปลายเส้นย้ายไปเกาะจุดอื่น ─────────────
  var STUB = 60;
  function isH(d) { return d === 'L' || d === 'R'; }
  function stubOf(a, d) {
    return d === 'L' ? [a[0] - STUB, a[1]] : d === 'R' ? [a[0] + STUB, a[1]] :
           d === 'T' ? [a[0], a[1] - STUB] : [a[0], a[1] + STUB];
  }
  function dirAt(ps, head) {
    var a = head ? ps[0] : ps[ps.length - 1];
    var b = head ? ps[1] : ps[ps.length - 2];
    if (Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1])) return b[0] > a[0] ? 'R' : 'L';
    return b[1] > a[1] ? 'B' : 'T';
  }
  // เลือกด้านที่เส้นควรออก-เข้า จากตำแหน่งจริงของสองกล่องในขณะนั้น
  // ไล่ซ้าย→ขวาเป็นหลักตาม F-01 ถ้าเยื้องกันแนวตั้งมากกว่าค่อยใช้บน-ล่าง
  function bestPair(ra, rb) {
    var dx = (rb.x + rb.w / 2) - (ra.x + ra.w / 2);
    var dy = (rb.y + rb.h / 2) - (ra.y + ra.h / 2);
    if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ['R', 'L'] : ['L', 'R'];
    return dy >= 0 ? ['B', 'T'] : ['T', 'B'];
  }
  function pickAnchor(id, side, exceptI) {
    var cand = slots(id, side), used = takenOn(id, exceptI), best = null, bs = -1;
    cand.forEach(function (p) {
      var far = 1e9;
      used.forEach(function (u) {
        far = Math.min(far, Math.hypot(u[0] - p[0], u[1] - p[1]));
      });
      if (far > bs) { bs = far; best = p; }     // เลือกช่องที่ห่างของเดิมที่สุด
    });
    return best ? { p: best, d: side } : null;
  }

  // ── หาเลนว่าง: ถ้าแนวที่จะใช้มีเส้นอื่นจองอยู่แล้ว เลื่อนออกทีละ 60 ─────
  function lanesUsed(exceptI, vertical) {
    var out = [];
    for (var i = 0; i < E.length; i++) {
      if (i === exceptI || !E[i]) continue;
      var p = E[i].pts;
      for (var j = 0; j < p.length - 1; j++) {
        var a = p[j], b = p[j + 1];
        if (vertical && Math.abs(a[0] - b[0]) < 0.5 && Math.abs(a[1] - b[1]) > 1) out.push(a[0]);
        if (!vertical && Math.abs(a[1] - b[1]) < 0.5 && Math.abs(a[0] - b[0]) > 1) out.push(a[1]);
      }
    }
    return out;
  }
  function freeLane(v, used) {
    function busy(t) {
      for (var i = 0; i < used.length; i++) if (Math.abs(used[i] - t) < 40) return true;
      return false;
    }
    if (!busy(v)) return v;
    for (var n = 1; n <= 8; n++) {
      if (!busy(v + n * 60)) return v + n * 60;
      if (!busy(v - n * 60)) return v - n * 60;
    }
    return v;
  }
  // เดินเส้นใหม่ทั้งเส้นให้สั้นและตรงที่สุดระหว่างสองกล่อง ณ ตำแหน่งปัจจุบัน
  function autoRoute(i) {
    var e = E[i];
    if (!e || e.from === e.to) return false;
    var pair = bestPair(rectOf(e.from), rectOf(e.to));
    var A = pickAnchor(e.from, pair[0], i), B = pickAnchor(e.to, pair[1], i);
    if (!A || !B) return false;
    e.pts = tidy(reroute(A.p, A.d, B.p, B.d, i));
    return true;
  }

  function reroute(A, dA, B, dB, exceptI) {
    var a1 = stubOf(A, dA), b1 = stubOf(B, dB), mid;
    var ex = exceptI === undefined ? -1 : exceptI;
    if (isH(dA) && isH(dB)) {
      var mx = freeLane((a1[0] + b1[0]) / 2, lanesUsed(ex, true));
      mid = [[mx, a1[1]], [mx, b1[1]]];
    } else if (!isH(dA) && !isH(dB)) {
      var my = freeLane((a1[1] + b1[1]) / 2, lanesUsed(ex, false));
      mid = [[a1[0], my], [b1[0], my]];
    } else if (isH(dA)) { mid = [[b1[0], a1[1]]]; }
    else { mid = [[a1[0], b1[1]]]; }
    var raw = [A, a1].concat(mid, [b1, B]), out = [raw[0]];
    for (var i = 1; i < raw.length; i++) {
      var q = raw[i], l = out[out.length - 1];
      if (Math.abs(q[0] - l[0]) < 0.5 && Math.abs(q[1] - l[1]) < 0.5) continue;
      out.push(q);
    }
    return out;
  }

  function roundedPath(ps, r) {
    var d = ['M ' + ps[0][0].toFixed(1) + ' ' + ps[0][1].toFixed(1)];
    for (var i = 1; i < ps.length - 1; i++) {
      var p0 = ps[i - 1], p1 = ps[i], p2 = ps[i + 1];
      var d0 = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
      var d1 = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
      var r1 = Math.min(r, d0 / 2, d1 / 2);
      if (!(r1 > 0.1)) continue;
      var v0 = [p1[0] - p0[0], p1[1] - p0[1]], v1 = [p2[0] - p1[0], p2[1] - p1[1]];
      var n0 = d0 || 1, n1 = d1 || 1;
      var a = [p1[0] - v0[0] / n0 * r1, p1[1] - v0[1] / n0 * r1];
      var b = [p1[0] + v1[0] / n1 * r1, p1[1] + v1[1] / n1 * r1];
      var sweep = (v0[0] * v1[1] - v0[1] * v1[0]) > 0 ? 1 : 0;
      d.push('L ' + a[0].toFixed(1) + ' ' + a[1].toFixed(1));
      d.push('A ' + r1.toFixed(1) + ' ' + r1.toFixed(1) + ' 0 0 ' + sweep +
             ' ' + b[0].toFixed(1) + ' ' + b[1].toFixed(1));
    }
    var last = ps[ps.length - 1];
    d.push('L ' + last[0].toFixed(1) + ' ' + last[1].toFixed(1));
    return d.join(' ');
  }

  function pointAt(ps, dist) {
    var left = dist;
    for (var i = 0; i < ps.length - 1; i++) {
      var a = ps[i], b = ps[i + 1];
      var L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (left <= L || i === ps.length - 2) {
        var t = L ? left / L : 0;
        t = Math.max(0, Math.min(1, t));
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      }
      left -= L;
    }
    return ps[0];
  }

  function drawEdge(i) {
    var e = E[i];
    if (!e) return;
    e.pts = tidy(e.pts);
    var d = roundedPath(e.pts, ELBOW);
    e.path.setAttribute('d', d);
    e.hit.setAttribute('d', d);
    if (e.dot) {
      e.dot.setAttribute('cx', e.pts[0][0].toFixed(1));
      e.dot.setAttribute('cy', e.pts[0][1].toFixed(1));
    }
    if (e.label) {
      var pos = pointAt(e.pts, +e.label.dataset.dist || 0);
      var lw = +e.label.dataset.lw || 40;
      var rect = e.label.querySelector('rect'), txt = e.label.querySelector('text');
      if (rect) {
        rect.setAttribute('x', (pos[0] - lw / 2).toFixed(1));
        rect.setAttribute('y', (pos[1] - 15).toFixed(1));
      }
      if (txt) {
        txt.setAttribute('x', pos[0].toFixed(1));
        txt.setAttribute('y', pos[1].toFixed(1));
      }
    }
  }

  // ── จุดจับปลายเส้น — โผล่เมื่อเมาส์อยู่บนเส้น ลากไปเกาะกล่องอื่นได้ ──
  var NS = 'http://www.w3.org/2000/svg';
  function mkCircle() {
    var c = document.createElementNS(NS, 'circle');
    c.setAttribute('class', 'e-h');
    c.setAttribute('r', '11');
    c.setAttribute('hidden', '');
    svg.appendChild(c);
    return c;
  }
  var hA = mkCircle(), hB = mkCircle();
  hA.dataset.end = 'head'; hB.dataset.end = 'tail';
  var bends = [];                         // จุดจับข้อศอก สร้างเท่าที่ต้องใช้
  function mkBend() {
    var r = document.createElementNS(NS, 'rect');
    r.setAttribute('class', 'e-b');
    r.setAttribute('width', '20'); r.setAttribute('height', '20');
    r.setAttribute('rx', '4');
    r.setAttribute('hidden', '');
    svg.appendChild(r);
    bends.push(r);
    return r;
  }
  var hovE = -1;
  function showHandles(i) {
    if (!E[i]) return;
    hovE = i;
    var p = E[i].pts, z = p[p.length - 1];
    hA.setAttribute('cx', p[0][0]); hA.setAttribute('cy', p[0][1]);
    hB.setAttribute('cx', z[0]); hB.setAttribute('cy', z[1]);
    hA.removeAttribute('hidden'); hB.removeAttribute('hidden');
    for (var n = 0; n < Math.max(bends.length, p.length - 2); n++) {
      var el = bends[n] || mkBend();
      if (n < p.length - 2) {
        el.setAttribute('x', p[n + 1][0] - 10);
        el.setAttribute('y', p[n + 1][1] - 10);
        el.dataset.idx = String(n + 1);
        el.removeAttribute('hidden');
      } else el.setAttribute('hidden', '');
    }
  }
  function hideHandles() {
    hovE = -1;
    hA.setAttribute('hidden', ''); hB.setAttribute('hidden', '');
    bends.forEach(function (b) { b.setAttribute('hidden', ''); });
  }
  // แทรกข้อศอกใหม่ตรงจุดที่คลิก / ลบข้อศอกที่ไม่ต้องการ
  function addBend(i, x, y) {
    var e = E[i], seg = nearestSeg(e.pts, x, y);
    e.pts = clonePts(e.pts);
    e.pts.splice(seg + 1, 0, [x, y]);
    e.pts = orthFix(e.pts);
    e.shaped = true;
  }
  function delBend(i, idx) {
    var e = E[i];
    if (idx <= 0 || idx >= e.pts.length - 1) return false;
    var p = clonePts(e.pts);
    p.splice(idx, 1);
    e.pts = tidy(orthFix(p));
    e.shaped = true;
    return true;
  }

  function paintChanges() {
    var box = document.getElementById('ed-ch');
    if (!box) return;
    var rows = [];
    for (var i = 0; i < E.length; i++) {
      var e = E[i];
      if (e && (e.from !== e.of || e.to !== e.ot)) rows.push(e);
    }
    box.hidden = !rows.length;
    var list = document.getElementById('ed-ch-list');
    if (!list) return;
    list.textContent = '';
    rows.forEach(function (e, n) {
      if (n) list.appendChild(document.createTextNode(' · '));
      var a = document.createElement('code'), b = document.createElement('code');
      a.textContent = e.of + ' ' + e.op + ' ' + e.ot;
      b.textContent = e.from + ' ' + e.op + ' ' + e.to;
      list.appendChild(a);
      list.appendChild(document.createTextNode(' → '));
      list.appendChild(b);
    });
    box.dataset.txt = rows.map(function (e) {
      return e.from + ' ' + e.op + ' ' + e.to + '   # เดิม ' + e.of + ' ' + e.op + ' ' + e.ot;
    }).join('\n');
  }
  var bCopy = document.getElementById('ed-ch-copy');
  if (bCopy) bCopy.addEventListener('click', function () {
    var t = (document.getElementById('ed-ch').dataset.txt || '');
    if (navigator.clipboard) navigator.clipboard.writeText(t);
    bCopy.textContent = 'คัดลอกแล้ว';
    setTimeout(function () { bCopy.textContent = 'คัดลอกเป็นบรรทัด .flow'; }, 1500);
  });

  // ── เตือนเมื่อเส้นทะลุกล่อง (G3) — เช็คสด ไม่ต้องรอ self_check ──────
  function segHitsBox(a, b, r) {
    var pad = 4;
    var x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
    var y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
    return x1 > r.x + pad && x0 < r.x + r.w - pad &&
           y1 > r.y + pad && y0 < r.y + r.h - pad;
  }
  function crossCount() {
    var bad = [];
    for (var i = 0; i < E.length; i++) {
      var e = E[i];
      if (!e) continue;
      var hit = false;
      Object.keys(nodeEls).forEach(function (id) {
        if (hit || id === e.from || id === e.to) return;
        var r = rectOf(id);
        for (var j = 0; j < e.pts.length - 1; j++) {
          if (segHitsBox(e.pts[j], e.pts[j + 1], r)) { hit = true; return; }
        }
      });
      if (hit) bad.push(i);
    }
    return bad;
  }
  var crossEl = document.getElementById('ed-cross');
  var bad = [];
  function paintCross() {
    bad = crossCount();
    if (!crossEl) return;
    crossEl.hidden = !bad.length;
    crossEl.textContent = bad.length ? '⚠ เส้นทะลุกล่อง ' + bad.length + ' เส้น (คลิกเพื่อไฮไลต์)' : '';
  }
  if (crossEl) crossEl.addEventListener('click', function () {
    bad.forEach(function (i) {
      E[i].path.classList.add('e-flag');
      setTimeout(function () { E[i].path.classList.remove('e-flag'); }, 1600);
    });
  });

  function draw() {
    Object.keys(off).forEach(function (id) {
      var o = off[id];
      if (o[0] || o[1]) nodeEls[id].setAttribute('transform', 'translate(' + o[0] + ',' + o[1] + ')');
      else nodeEls[id].removeAttribute('transform');
    });
    for (var i = 0; i < E.length; i++) drawEdge(i);
    paintCross();
  }

  // ── เลื่อนกล่อง: ปลายเส้นตามไปด้วย และยังตั้งฉากเหมือนเดิม ───────────
  function expand(ps) {                   // เส้นตรง 2 จุด → 4 จุด จะได้พับได้
    if (ps.length !== 2) return ps;
    var a = ps[0], b = ps[1];
    if (Math.abs(a[1] - b[1]) <= Math.abs(a[0] - b[0])) {
      var mx = (a[0] + b[0]) / 2;
      return [a, [mx, a[1]], [mx, b[1]], b];
    }
    var my = (a[1] + b[1]) / 2;
    return [a, [a[0], my], [b[0], my], b];
  }

  // ── เส้นที่คนจัดเองแล้ว: ยืดทั้งเส้นตามสัดส่วน ไม่เดินเส้นใหม่ทับของเขา ──
  // สเกลทีละแกนโดยตรึงปลายที่ไม่ได้ขยับไว้ — เส้นแนวตั้ง/แนวนอนยังตั้งฉากเหมือนเดิม
  function orthFix(ps) {
    var p = [ps[0]];
    for (var i = 1; i < ps.length; i++) {
      var a = p[p.length - 1], b = ps[i];
      if (Math.abs(a[0] - b[0]) > 0.5 && Math.abs(a[1] - b[1]) > 0.5) {
        p.push([a[0], b[1]]);             // ช่วงที่กลายเป็นเฉียง แทรกข้อศอกคืน
      }
      p.push([b[0], b[1]]);
    }
    return p;
  }
  function stretch(base, head, dx, dy) {
    var p = clonePts(base), n = p.length;
    var mv = head ? 0 : n - 1, fx = head ? n - 1 : 0;
    [0, 1].forEach(function (ax) {
      var d = ax === 0 ? dx : dy;
      var m0 = base[mv][ax], f0 = base[fx][ax];
      if (Math.abs(m0 - f0) >= 1) {       // สเกลรอบปลายที่ตรึงไว้
        var k = (m0 + d - f0) / (m0 - f0);
        for (var i = 0; i < n; i++) p[i][ax] = f0 + (base[i][ax] - f0) * k;
      } else {                            // ปลายสองข้างอยู่แนวเดียวกัน สเกลไม่ได้
        for (var j = 0; j < n; j++) if (j !== fx) p[j][ax] = base[j][ax] + d;
      }
    });
    return tidy(orthFix(p));
  }

  function moveEnd(ps, head, dx, dy) {
    var p = expand(clonePts(ps)), n = p.length;
    if (head) {
      var a = p[0], b = p[1];
      var horiz = Math.abs(a[1] - b[1]) <= Math.abs(a[0] - b[0]);
      p[0] = [a[0] + dx, a[1] + dy];
      p[1] = horiz ? [b[0], p[0][1]] : [p[0][0], b[1]];
    } else {
      var z = p[n - 1], y = p[n - 2];
      var horiz2 = Math.abs(z[1] - y[1]) <= Math.abs(z[0] - y[0]);
      p[n - 1] = [z[0] + dx, z[1] + dy];
      p[n - 2] = horiz2 ? [y[0], p[n - 1][1]] : [p[n - 1][0], y[1]];
    }
    return p;
  }

  // ── ลากเส้น: เลื่อนช่วงหนึ่งของเส้นตั้งฉาก ปลายยังเกาะกล่องเดิม ──────
  function nearestSeg(ps, x, y) {
    var best = 0, bd = Infinity;
    for (var i = 0; i < ps.length - 1; i++) {
      var a = ps[i], b = ps[i + 1];
      var vx = b[0] - a[0], vy = b[1] - a[1], L2 = vx * vx + vy * vy;
      var t = L2 ? Math.max(0, Math.min(1, ((x - a[0]) * vx + (y - a[1]) * vy) / L2)) : 0;
      var d = Math.hypot(x - (a[0] + vx * t), y - (a[1] + vy * t));
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  function shiftSeg(base, i, dx, dy) {
    var p = clonePts(base), n = p.length;
    var a = p[i], b = p[i + 1];
    var horiz = Math.abs(a[1] - b[1]) <= Math.abs(a[0] - b[0]);
    if (horiz) { p[i][1] += dy; p[i + 1][1] += dy; }
    else { p[i][0] += dx; p[i + 1][0] += dx; }
    if (i + 1 === n - 1) p.push([base[n - 1][0], base[n - 1][1]]);
    if (i === 0) p.unshift([base[0][0], base[0][1]]);
    return p;
  }

  // ── ย้อน / ทำซ้ำ ────────────────────────────────────────────────────
  var undo = [], redo = [];
  function snap() {
    return JSON.stringify({
      o: off,
      e: E.map(function (e) { return e ? { p: e.pts, f: e.from, t: e.to, s: e.shaped } : null; })
    });
  }
  function restore(s) {
    var d = JSON.parse(s);
    off = d.o;
    for (var i = 0; i < E.length; i++) {
      if (!E[i] || !d.e[i]) continue;
      E[i].pts = d.e[i].p;
      E[i].from = d.e[i].f;
      E[i].to = d.e[i].t;
      E[i].shaped = !!d.e[i].s;
      E[i].path.dataset.from = E[i].from;
      E[i].path.dataset.to = E[i].to;
    }
    draw();
    hideHandles();
    paintChanges();
    paintBtns();
  }
  var bUndo = document.getElementById('ed-undo'),
      bRedo = document.getElementById('ed-redo'),
      bReset = document.getElementById('ed-reset'),
      dirty = document.getElementById('ed-dirty');
  function paintBtns() {
    if (bUndo) bUndo.disabled = !undo.length;
    if (bRedo) bRedo.disabled = !redo.length;
    if (bReset) bReset.disabled = !undo.length && !redo.length;
    if (dirty) dirty.hidden = !undo.length;
  }
  function begin() { undo.push(snap()); redo = []; paintBtns(); }
  function doUndo() {
    if (!undo.length) return;
    redo.push(snap());
    restore(undo.pop());
  }
  function doRedo() {
    if (!redo.length) return;
    undo.push(snap());
    restore(redo.pop());
  }
  if (bUndo) bUndo.addEventListener('click', doUndo);
  if (bRedo) bRedo.addEventListener('click', doRedo);
  var base0 = snap();
  if (bReset) bReset.addEventListener('click', function () {
    if (!undo.length && !redo.length) return;
    begin();                     // คืนตำแหน่งเดิมก็ย้อนได้ ไม่ใช่ทางเดียว
    restore(base0);
  });

  // ── จับการลาก ───────────────────────────────────────────────────────
  var drag = null;
  function svgPt(ev) {
    var r = svg.getBoundingClientRect(), kk = k();
    return [(ev.clientX - r.left) / kk, (ev.clientY - r.top) / kk];
  }
  svg.addEventListener('pointerdown', function (ev) {
    var g = ev.target.closest ? ev.target.closest('g.node') : null;
    var cls = ev.target.classList;
    var hit = cls && cls.contains('e-hit') ? ev.target : null;
    var bend = cls && cls.contains('e-b') ? ev.target : null;
    if (bend && hovE >= 0) {
      ev.preventDefault();
      if (ev.altKey || ev.button === 2) {        // Alt/⌥ หรือคลิกขวา = ลบข้อศอก
        begin();
        if (delBend(hovE, +bend.dataset.idx)) { drawEdge(hovE); showHandles(hovE); }
        else undo.pop();
        paintCross(); paintBtns();
        return;
      }
      begin();
      drag = { kind: 'bend', i: hovE, idx: +bend.dataset.idx,
               base: clonePts(E[hovE].pts) };
      svg.setPointerCapture(ev.pointerId);
      return;
    }
    if (hit && (ev.altKey || ev.button === 2)) {  // Alt/⌥ หรือคลิกขวาบนเส้น = แทรกข้อศอก
      ev.preventDefault();
      var q0 = svgPt(ev);
      begin();
      addBend(+hit.dataset.e, q0[0], q0[1]);
      drawEdge(+hit.dataset.e);
      showHandles(+hit.dataset.e);
      paintCross(); paintBtns();
      return;
    }
    var handle = cls && cls.contains('e-h') ? ev.target : null;
    if (handle && hovE >= 0) {
      ev.preventDefault();
      begin();
      drag = { kind: 'end', i: hovE, head: handle.dataset.end === 'head',
               base: clonePts(E[hovE].pts), node: null, anchor: null };
      svg.setPointerCapture(ev.pointerId);
      return;
    }
    if (!g && !hit) return;
    ev.preventDefault();
    begin();
    var kk = k();
    if (g) {
      var id = g.dataset.id;
      drag = {
        kind: 'node', id: id, x: ev.clientX, y: ev.clientY,
        o: [off[id][0], off[id][1]],
        edges: E.map(function (e, i) {
          if (!e) return null;
          if (e.from !== id && e.to !== id) return null;
          return { i: i, head: e.from === id, base: clonePts(e.pts) };
        }).filter(Boolean)
      };
      g.classList.add('dragging');
    } else {
      var i2 = +hit.dataset.e, e2 = E[i2];
      var r = svg.getBoundingClientRect();
      var sx = (ev.clientX - r.left) / kk, sy = (ev.clientY - r.top) / kk;
      drag = { kind: 'edge', i: i2, x: ev.clientX, y: ev.clientY,
               seg: nearestSeg(e2.pts, sx, sy), base: clonePts(e2.pts) };
    }
    svg.setPointerCapture(ev.pointerId);
  });

  svg.addEventListener('pointermove', function (ev) {
    if (!drag) {                          // ไม่ได้ลากอยู่ — แค่โชว์จุดจับของเส้นที่ชี้อยู่
      var c = ev.target.classList;
      if (c && c.contains('e-hit')) showHandles(+ev.target.dataset.e);
      else if (!c || (!c.contains('e-h') && !c.contains('e-b'))) hideHandles();
      return;
    }
    if (drag.kind === 'bend') {
      var q1 = svgPt(ev), e1 = E[drag.i], p1 = clonePts(drag.base), k1 = drag.idx;
      var a1 = p1[k1 - 1], c1 = p1[k1 + 1];
      var preH = Math.abs(a1[1] - p1[k1][1]) <= Math.abs(a1[0] - p1[k1][0]);
      var nxtH = Math.abs(c1[1] - p1[k1][1]) <= Math.abs(c1[0] - p1[k1][0]);
      p1[k1] = [q1[0], q1[1]];
      if (preH) p1[k1 - 1] = [a1[0], q1[1]]; else p1[k1 - 1] = [q1[0], a1[1]];
      if (nxtH) p1[k1 + 1] = [c1[0], q1[1]]; else p1[k1 + 1] = [q1[0], c1[1]];
      if (k1 - 1 === 0) p1[0] = [drag.base[0][0], drag.base[0][1]];
      if (k1 + 1 === p1.length - 1) p1[p1.length - 1] = clonePts(drag.base).pop();
      e1.pts = orthFix(p1);
      e1.shaped = true;
      drawEdge(drag.i);
      showHandles(drag.i);
      return;
    }
    if (drag.kind === 'end') {
      var q = svgPt(ev);
      var id = nodeAt(q[0], q[1]);
      document.querySelectorAll('g.node.target').forEach(function (o) {
        o.classList.remove('target');
      });
      var e = E[drag.i];
      if (id) {
        nodeEls[id].classList.add('target');
        var a = nearestAnchor(id, q[0], q[1]);
        var other = drag.head ? drag.base[drag.base.length - 1] : drag.base[0];
        var od = dirAt(drag.base, !drag.head);
        e.pts = drag.head ? reroute(a.p, a.d, other, od) : reroute(other, od, a.p, a.d);
        drag.node = id;
        drag.anchor = a;
      } else {                            // ปล่อยนอกกล่องไม่ได้ — เส้นต้องแตะกล่องเสมอ (F-20)
        e.pts = clonePts(drag.base);
        drag.node = null;
      }
      drawEdge(drag.i);
      showHandles(drag.i);
      return;
    }
    var kk = k();
    var dx = (ev.clientX - drag.x) / kk, dy = (ev.clientY - drag.y) / kk;
    if (drag.kind === 'node') {
      off[drag.id] = [drag.o[0] + dx, drag.o[1] + dy];
      drag.edges.forEach(function (m) {
        // เส้นที่คนจัดเองแล้ว = ยืดตามสัดส่วน รักษารูปที่เขาวางไว้
        // เส้นที่ยังไม่เคยแตะ = เดินใหม่เองให้สั้นที่สุด
        if (E[m.i].shaped) E[m.i].pts = stretch(m.base, m.head, dx, dy);
        else if (!autoRoute(m.i)) E[m.i].pts = moveEnd(m.base, m.head, dx, dy);
        drawEdge(m.i);
      });
      var g2 = nodeEls[drag.id];
      g2.setAttribute('transform', 'translate(' + off[drag.id][0] + ',' + off[drag.id][1] + ')');
    } else {
      E[drag.i].pts = shiftSeg(drag.base, drag.seg, dx, dy);
      E[drag.i].shaped = true;
      drawEdge(drag.i);
    }
  });

  function endDrag(ev) {
    if (!drag) return;
    if (drag.kind === 'end') {
      document.querySelectorAll('g.node.target').forEach(function (o) {
        o.classList.remove('target');
      });
      var e = E[drag.i];
      if (drag.node) {
        if (drag.head) e.from = drag.node; else e.to = drag.node;
        e.path.dataset.from = e.from;
        e.path.dataset.to = e.to;
      } else {
        undo.pop();                       // ไม่ได้เปลี่ยนอะไร ไม่ต้องกินคิว undo
      }
      paintChanges();
    }
    var g3 = nodeEls[drag.id];
    if (g3) g3.classList.remove('dragging');
    if (ev && ev.pointerId !== undefined && svg.hasPointerCapture(ev.pointerId)) {
      svg.releasePointerCapture(ev.pointerId);
    }
    drag = null;
    paintCross();
    paintBtns();
  }
  svg.addEventListener('contextmenu', function (ev) {
    var c = ev.target.classList;
    if (c && (c.contains('e-hit') || c.contains('e-b'))) ev.preventDefault();
  });
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);

  // ── ดับเบิลคลิก = จัดเส้นให้สั้นและตรงที่สุด ────────────────────────
  // ที่เส้น: จัดเส้นนั้น · ที่กล่อง: จัดทุกเส้นที่ต่อกับกล่องนั้น
  svg.addEventListener('dblclick', function (ev) {
    var cls = ev.target.classList;
    var onEdge = cls && (cls.contains('e-hit') || cls.contains('e-h'));
    var g = ev.target.closest ? ev.target.closest('g.node') : null;
    var list = [];
    if (onEdge) {
      var i = cls.contains('e-h') ? hovE : +ev.target.dataset.e;
      if (i >= 0) list = [i];
    } else if (g) {
      var id = g.dataset.id;
      for (var n = 0; n < E.length; n++) {
        if (E[n] && (E[n].from === id || E[n].to === id)) list.push(n);
      }
    }
    if (!list.length) return;
    ev.preventDefault();
    begin();
    var changed = false;
    list.forEach(function (n) {
      if (autoRoute(n)) { E[n].shaped = false; drawEdge(n); changed = true; }
    });
    if (!changed) undo.pop();
    if (onEdge && list.length === 1) showHandles(list[0]);
    paintCross();
    paintBtns();
  });

  // ── คีย์ลัด ─────────────────────────────────────────────────────────
  addEventListener('keydown', function (ev) {
    var t = ev.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
    var mod = ev.metaKey || ev.ctrlKey;
    if (!mod) return;
    var key = ev.key;
    if (key === 'z' || key === 'Z') {
      ev.preventDefault();
      if (ev.shiftKey) doRedo(); else doUndo();
    } else if (key === 'y') {
      ev.preventDefault(); doRedo();
    } else if (key === '=' || key === '+') {
      ev.preventDefault(); step(1.25);
    } else if (key === '-' || key === '_') {
      ev.preventDefault(); step(0.8);
    } else if (key === '0') {
      ev.preventDefault(); scale = 1; paintZoom();
    } else if (key === '9') {
      ev.preventDefault(); scale = null; paintZoom();
    }
  });

  // ── บันทึกสิ่งที่แก้ไว้ในเครื่อง · โหลดคืนเองตอนเปิดไฟล์ใหม่ ──────────
  var srcEl = document.getElementById('flow-src');
  var SRC = '', SRCNAME = 'flow.flow';
  try {
    if (srcEl) {
      SRC = JSON.parse(srcEl.textContent);
      SRCNAME = srcEl.dataset.name || SRCNAME;
    }
  } catch (e) {}
  var SKEY = 'flowedit:' + SRCNAME + ':' + document.title;
  var bSave = document.getElementById('ed-save'),
      bDrop = document.getElementById('ed-drop'),
      sInfo = document.getElementById('ed-saved');

  function stamp(t) {
    if (!sInfo) return;
    sInfo.hidden = !t;
    sInfo.textContent = t ? 'บันทึกไว้เมื่อ ' + t : '';
    if (bDrop) bDrop.hidden = !t;
  }
  function save() {
    var when = new Date().toLocaleString('th-TH');
    try {
      localStorage.setItem(SKEY, JSON.stringify({ when: when, data: snap() }));
      stamp(when);
    } catch (e) {
      stamp('');
      if (sInfo) { sInfo.hidden = false; sInfo.textContent = 'บันทึกไม่ได้ — เบราว์เซอร์ไม่ให้เก็บข้อมูล'; }
    }
  }
  if (bSave) bSave.addEventListener('click', save);
  if (bDrop) bDrop.addEventListener('click', function () {
    try { localStorage.removeItem(SKEY); } catch (e) {}
    stamp('');
  });
  (function () {                          // โหลดของที่บันทึกไว้ ถ้ามี
    var raw = null;
    try { raw = localStorage.getItem(SKEY); } catch (e) {}
    if (!raw) return;
    try {
      var o = JSON.parse(raw);
      restore(o.data);
      undo = []; redo = [];               // ของที่บันทึกถือเป็นจุดตั้งต้นใหม่
      paintBtns();
      stamp(o.when);
    } catch (e) {}
  })();

  // ── ดาวน์โหลด .flow ที่แก้แล้ว (เฉพาะเส้นที่ถูกย้ายปลาย) ─────────────
  function reEsc(s) { return s.replace(/[.*+?^${}()|[\]\\\/-]/g, '\\$&'); }
  function updatedFlow() {
    var lines = SRC.split('\n');
    for (var i = 0; i < E.length; i++) {
      var e = E[i];
      if (!e || (e.from === e.of && e.to === e.ot)) continue;
      // จับป้ายตามที่ดีไซเนอร์พิมพ์จริง (เช่น -ไม่ใช่->) ไม่ใช่ตัวที่ normalize
      // แล้ว (-No->) ไม่งั้นบรรทัดภาษาไทยจะหาไม่เจอ
      var re = new RegExp('^(\\s*)' + reEsc(e.of) + '\\s+(\\S+)\\s+' +
                          reEsc(e.ot) + '\\s*(#.*)?$');
      for (var j = 0; j < lines.length; j++) {
        var m = lines[j].match(re);
        if (!m) continue;
        lines[j] = m[1] + e.from + ' ' + m[2] + ' ' + e.to +
          '   # ย้ายปลายเส้นในหน้า HTML — เดิม ' + e.of + ' ' + m[2] + ' ' + e.ot;
        break;
      }
    }
    return lines.join('\n');
  }
  var bDl = document.getElementById('ed-ch-dl');
  if (bDl) bDl.addEventListener('click', function () {
    if (!SRC) { bDl.textContent = 'ไม่มีต้นฉบับ .flow ฝังมาในไฟล์นี้'; return; }
    var blob = new Blob([updatedFlow()], { type: 'text/plain;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = SRCNAME.replace(/\.flow$/, '') + '-edited.flow';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  });

  addEventListener('keydown', function (ev) {
    if ((ev.metaKey || ev.ctrlKey) && (ev.key === 's' || ev.key === 'S')) {
      ev.preventDefault();
      save();
    }
  });

  paintBtns();
  paintChanges();
  paintCross();
  paintZoom();
})();
"""


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
    font-family:'Sarabun','Noto Sans Thai','IBM Plex Sans Thai',-apple-system,
      'Thonburi','Helvetica Neue',sans-serif; }}
  header {{ position:sticky; top:0; z-index:5; background:var(--paper);
    border-bottom:1px solid var(--hairline); padding:20px 28px; }}
  h1 {{ font-size:20px; margin:0 0 4px; font-weight:700; }}
  .sub {{ font-size:13px; color:var(--muted); display:flex; gap:16px; flex-wrap:wrap; }}
  .sub b {{ font-weight:600; color:var(--ink); }}
  main {{ display:flex; gap:24px; align-items:flex-start; flex-wrap:wrap;
    padding:24px 28px 80px; }}
  /* วางใต้ผัง ไม่ใช่เหนือผัง — ผังคือของหลักของหน้านี้ */
  .lg {{ flex:1 0 100%; background:var(--paper);
    border:1px solid var(--hairline); border-radius:16px; padding:20px 24px; }}
  .lg-b {{ display:grid; gap:4px 28px;
    grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); }}
  .lg-t {{ font-family:inherit; font-weight:700; font-size:15px; color:var(--ink);
    background:none; border:0; border-bottom:2px solid var(--ink); padding:0 0 2px;
    display:flex; gap:10px; align-items:center; cursor:pointer; }}
  .lg-caret {{ font-size:11px; transition:transform .15s ease; }}
  .lg-t[aria-expanded="false"] .lg-caret {{ transform:rotate(-90deg); }}
  .lg-b {{ margin-top:14px; }}
  .lg-t[aria-expanded="false"] + .lg-b {{ display:none; }}
  .lg-i {{ display:flex; gap:12px; align-items:center; margin:12px 0; font-size:12px;
    color:var(--muted); line-height:1.4; }}
  .lg-i svg {{ flex:0 0 54px; }}
  .rp {{ flex:1 0 100%; background:var(--paper); border:1px solid var(--hairline);
    border-radius:16px; padding:16px 24px; }}
  .rp-t {{ font-weight:700; font-size:15px; margin-bottom:12px;
    border-bottom:2px solid var(--ink); display:inline-block; padding-bottom:2px; }}
  .rp-pills {{ display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }}
  .rp-pill {{ font-size:12px; font-weight:700; padding:3px 10px; border-radius:999px;
    border:1px solid var(--hairline); color:var(--muted); }}
  .rp-pr0 {{ color:var(--no); border-color:var(--no); }}
  .rp-pr1 {{ color:#B26A00; border-color:#E3B872; }}
  .rp-ok {{ font-size:14px; color:var(--yes); font-weight:700; margin:0; }}
  .rp-list {{ list-style:none; margin:0; padding:0; display:flex;
    flex-direction:column; gap:10px; }}
  .rp-item {{ border-left:3px solid var(--hairline); padding:2px 0 2px 14px; }}
  .rp-ir0 {{ border-left-color:var(--no); }}
  .rp-ir1 {{ border-left-color:#E3B872; }}
  .rp-head {{ display:flex; gap:8px; align-items:baseline; flex-wrap:wrap;
    font-size:12px; }}
  .rp-sev {{ font-weight:700; }}
  .rp-code {{ font-weight:700; color:var(--primary); }}
  .rp-sevname {{ color:var(--muted); }}
  .rp-msg {{ font-size:13.5px; line-height:1.6; margin-top:2px; }}
  .rp-where {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:7px; }}
  .rp-plain {{ font-size:12px; color:var(--muted); }}
  .rp-go {{ font:inherit; font-size:12px; font-weight:600; cursor:pointer;
    background:var(--paper_2); color:var(--primary); border:1px solid var(--hairline);
    border-radius:999px; padding:3px 11px; }}
  .rp-go:hover {{ border-color:var(--primary); }}
  .rp-go:focus-visible {{ outline:2px solid var(--primary); outline-offset:2px; }}
  .rp-foot {{ font-size:12px; color:var(--muted); margin:14px 0 0; }}
  .rp-bar {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap;
    margin-bottom:12px; font-size:13px; }}
  .rp-bar button {{ font:inherit; font-size:12px; font-weight:600; cursor:pointer;
    background:var(--paper_2); border:1px solid var(--hairline); color:var(--ink);
    border-radius:8px; padding:5px 12px; }}
  .rp-bar button:hover {{ border-color:var(--primary); color:var(--primary); }}
  #rp-count {{ font-weight:700; margin-right:auto; }}
  .rp-dec {{ display:flex; gap:8px; margin-top:9px; }}
  .rp-dec button {{ font:inherit; font-size:12px; font-weight:700; cursor:pointer;
    background:var(--paper); border:1px solid var(--hairline); color:var(--muted);
    border-radius:8px; padding:4px 14px; }}
  .rp-dec button[aria-pressed="true"].rp-yes {{ background:var(--yes); color:#fff;
    border-color:var(--yes); }}
  .rp-dec button[aria-pressed="true"].rp-no {{ background:var(--no); color:#fff;
    border-color:var(--no); }}
  .rp-dec button:focus-visible {{ outline:2px solid var(--primary);
    outline-offset:2px; }}
  .rp-item.taken {{ opacity:.55; }}
  #rp-out {{ width:100%; margin-top:12px; font:inherit; font-size:12px;
    border:1px solid var(--hairline); border-radius:8px; padding:8px;
    background:var(--paper_2); color:var(--ink); resize:vertical; }}
  .node.hit rect, .node.hit path {{ stroke:var(--ink); stroke-width:6; }}
  @media (prefers-reduced-motion:no-preference) {{
    .node.hit rect, .node.hit path {{ animation:hit 1.1s ease-out; }}
    @keyframes hit {{ 0%,60% {{ stroke-opacity:1; }} 100% {{ stroke-opacity:0; }} }}
  }}
  .led {{ flex:1 0 100%; background:var(--paper); border:1px solid var(--hairline);
    border-radius:16px; padding:16px 24px; }}
  .led-t {{ font-weight:700; font-size:15px; margin-bottom:12px;
    border-bottom:2px solid var(--ink); display:inline-block; padding-bottom:2px; }}
  .led-sum, .led-all {{ font-size:13px; color:var(--muted); margin:0 0 12px; }}
  .led-sum b {{ color:var(--ink); }}
  .led-all {{ margin-bottom:0; }}
  .led-warn {{ font-size:13px; color:var(--no); font-weight:700; margin:0 0 12px; }}
  .led-tb {{ border-collapse:collapse; width:100%; font-size:13px; }}
  .led-tb th {{ text-align:left; font-size:11px; letter-spacing:.06em;
    text-transform:uppercase; color:var(--muted); font-weight:700;
    padding:0 12px 6px 0; border-bottom:1px solid var(--hairline); }}
  .led-tb td {{ padding:8px 12px 8px 0; border-bottom:1px solid var(--hairline);
    vertical-align:top; line-height:1.5; }}
  .led-tb tr:last-child td {{ border-bottom:0; }}
  .led-k {{ color:var(--muted); white-space:nowrap; }}
  .led-id {{ font-weight:700; white-space:nowrap; }}
  .canvas {{ flex:1 1 auto; min-width:0; overflow:auto; background:var(--paper);
    border:1px solid var(--hairline); border-radius:16px; padding:8px; }}
  .zoomwrap {{ transform-origin:0 0; width:{W}px; }}
  svg.flow {{ display:block; width:{W}px; height:{H}px; }}
  /* แถบเครื่องมืออยู่ใต้ผัง เต็มความกว้าง — อยู่ในกรอบผังแล้วมันโดนบีบเป็น
     คอลัมน์แคบ เพราะกรอบนั้นเป็นที่เลื่อนของ canvas ที่กว้างหลายหมื่น px */
  .zoom {{ flex:1 0 100%; display:flex; flex-wrap:wrap; gap:8px;
    align-items:center; margin:0 0 12px; }}
  .zoom button {{ font-family:inherit; font-size:12px; font-weight:600;
    color:var(--primary); background:var(--paper); border:1px solid var(--primary);
    border-radius:8px; padding:6px 12px; cursor:pointer; }}
  .zoom button[aria-pressed="true"] {{ background:var(--primary); color:#fff; }}
  .zoom button[disabled] {{ opacity:.35; cursor:default; }}
  .zoom .sep {{ width:1px; height:22px; background:var(--hairline); }}
  .zoom kbd {{ font-family:ui-monospace,Menlo,monospace; font-size:11px;
    border:1px solid var(--hairline); border-radius:5px; padding:1px 5px;
    color:var(--muted); background:var(--paper); }}
  .zoom .hint {{ font-size:12px; color:var(--muted); }}
  /* ── ตัวแก้ผังในหน้า — เลื่อนเพื่อจัดสายตา ไม่ใช่การแก้ .flow ── */
  .node {{ cursor:grab; }}
  .node.dragging {{ cursor:grabbing; }}
  /* <g> ใน SVG ไม่มีรูปทรงของตัวเอง — ถ้าปิด pointer-events ที่ลูกทั้งหมด
     กล่องจะจับไม่ได้เลย ปิดเฉพาะตัวอักษรพอ */
  .node text {{ pointer-events:none; user-select:none; }}
  .e-hit {{ fill:none; stroke:transparent; stroke-width:28;
    pointer-events:stroke; cursor:move; }}
  .e-h {{ fill:var(--paper); stroke:var(--ink); stroke-width:3; cursor:crosshair; }}
  .e-b {{ fill:var(--paper); stroke:var(--ink); stroke-width:2.5; cursor:move; }}
  .e-b[hidden] {{ display:none; }}
  /* คำเตือนของตัวแก้ผัง ไม่ใช่สีความหมายในผัง — ไม่ติดไปกับ .svg/.png */
  .zoom button.warn {{ color:var(--no); border-color:var(--no); }}
  .e-flag {{ stroke-width:16; }}
  .e-h[hidden] {{ display:none; }}
  .node.target rect, .node.target path {{ stroke:var(--ink); stroke-width:5; }}
  .ed-ch {{ flex:1 0 100%; display:flex; gap:10px; align-items:center;
    flex-wrap:wrap; font-size:12px; color:var(--muted); margin-top:8px; }}
  .ed-ch code {{ font-family:ui-monospace,Menlo,monospace; color:var(--ink);
    background:var(--paper); border:1px solid var(--hairline);
    border-radius:6px; padding:1px 6px; }}
  .dirty-note {{ font-size:12px; color:var(--perm-ink,#8a6100); }}
  .n-rect, .n-oval, .n-chip, .n-dia {{ fill:var(--primary); stroke:none; }}
  .n-note {{ fill:#FFFBEC; stroke:var(--perm); stroke-width:2; stroke-dasharray:8 6; }}
  text {{ text-anchor:middle; dominant-baseline:central; font-weight:600;
    font-family:'Sarabun','Noto Sans Thai','IBM Plex Sans Thai','Thonburi',
      sans-serif; }}
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
  <div class="zoom" role="group" aria-label="เครื่องมือผัง">
    <button id="z-out" title="ย่อ (Ctrl/⌘ -)" aria-label="ย่อ">−</button>
    <span class="hint" id="z-now">100%</span>
    <button id="z-in" title="ขยาย (Ctrl/⌘ +)" aria-label="ขยาย">+</button>
    <button data-z="fit" aria-pressed="true">พอดีจอ</button>
    <button data-z="1" title="ขนาดจริง (Ctrl/⌘ 0)">100%</button>
    <span class="sep"></span>
    <button id="ed-undo" disabled title="ย้อน (Ctrl/⌘ Z)">↶ ย้อน</button>
    <button id="ed-redo" disabled title="ทำซ้ำ (Ctrl/⌘ ⇧ Z)">↷ ทำซ้ำ</button>
    <button id="ed-reset" disabled title="คืนตำแหน่งที่สคริปต์คำนวณไว้">คืนตำแหน่งเดิม</button>
    <span class="sep"></span>
    <button id="ed-save" title="เก็บที่แก้ไว้ในเครื่อง (Ctrl/⌘ S)">💾 บันทึก</button>
    <button id="ed-drop" hidden title="ลบสิ่งที่บันทึกไว้ในเครื่อง">ล้างที่บันทึก</button>
    <span class="hint" id="ed-saved" hidden></span>
    <button class="warn" id="ed-cross" hidden></button>
    <span class="sep"></span>
    <span class="hint">canvas {W}×{H}px · ลากกล่องหรือลากเส้นเพื่อจัดสายตา
      <kbd>Ctrl/⌘ Z</kbd> <kbd>Ctrl/⌘ +</kbd> <kbd>Ctrl/⌘ -</kbd> <kbd>Ctrl/⌘ 0</kbd></span>
    <span class="hint dirty-note" id="ed-dirty" hidden>ตำแหน่งถูกเลื่อนด้วยมือแล้ว —
      อยู่แค่ในหน้านี้ ไม่ได้แก้ .flow และไม่เปลี่ยนผลตรวจ G1–G9</span>
    <div class="ed-ch" id="ed-ch" hidden>
      <b style="color:var(--ink)">เส้นที่ถูกย้ายปลาย</b>
      <span id="ed-ch-list"></span>
      <button id="ed-ch-copy">คัดลอกเป็นบรรทัด .flow</button>
      <button id="ed-ch-dl">ดาวน์โหลด .flow ที่แก้แล้ว</button>
      <span>ย้ายปลายเส้น = เปลี่ยนความหมายของ flow — ต้องเอาไปแก้ใน .flow แล้ว render ใหม่</span>
    </div>
  <div class="canvas" id="cv">
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
  </div>
  {legend}
  {report}
  {ledger}
</main>
<script>
  (function () {{
    // รับ/ไม่รับรายข้อ — จำไว้ในเครื่องผู้ใช้ แล้วคัดลอกกลับไปวางในแชทได้
    (function () {{
      var items = [].slice.call(document.querySelectorAll('.rp-item'));
      if (!items.length) return;
      var KEY = 'flowreview:' + document.title;
      var state = {{}};
      try {{ state = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }}
      catch (e) {{ state = {{}}; }}

      function paint() {{
        var y = 0, n = 0, p = 0;
        items.forEach(function (li) {{
          var v = state[li.dataset.id] || 'pending';
          li.classList.toggle('taken', v !== 'pending');
          li.querySelectorAll('.rp-dec button').forEach(function (b) {{
            b.setAttribute('aria-pressed', String(b.dataset.v === v));
          }});
          if (v === 'yes') y++; else if (v === 'no') n++; else p++;
        }});
        document.getElementById('rp-count').textContent =
          'รับ ' + y + ' · ไม่รับ ' + n + ' · ยังไม่ตัดสิน ' + p +
          ' จาก ' + items.length + ' ข้อ';
        try {{ localStorage.setItem(KEY, JSON.stringify(state)); }} catch (e) {{}}
      }}

      document.querySelectorAll('.rp-dec button').forEach(function (b) {{
        b.addEventListener('click', function () {{
          var id = b.dataset.id;
          state[id] = state[id] === b.dataset.v ? 'pending' : b.dataset.v;
          paint();
        }});
      }});

      document.getElementById('rp-copy').addEventListener('click', function () {{
        var out = ['ผลรีวิว: ' + document.title];
        items.forEach(function (li) {{
          var v = state[li.dataset.id] || 'pending';
          out.push('- ' + li.dataset.code + ' ' +
            (v === 'yes' ? 'รับ' : v === 'no' ? 'ไม่รับ' : 'ยังไม่ตัดสิน') +
            ' — ' + li.dataset.msg);
        }});
        var t = out.join('\\n');   // ต้อง escape — HTML_TMPL ไม่ใช่ raw string
        document.getElementById('rp-out').value = t;
        if (navigator.clipboard) navigator.clipboard.writeText(t);
      }});

      document.getElementById('rp-reset').addEventListener('click', function () {{
        state = {{}};
        document.getElementById('rp-out').value = '';
        paint();
      }});

      paint();
    }})();

    // คลิกชื่อกล่องในผลตรวจ แล้วเลื่อนผังไปหากล่องนั้น + กะพริบให้เห็น
    document.querySelectorAll('.rp-go').forEach(function (b) {{
      b.addEventListener('click', function () {{
        var g = document.getElementById(b.dataset.go);
        var box = document.querySelector('.canvas');
        if (!g || !box) return;
        var r = g.getBoundingClientRect(), c = box.getBoundingClientRect();
        box.scrollTo({{
          left: box.scrollLeft + (r.left - c.left) - (c.width - r.width) / 2,
          top: box.scrollTop + (r.top - c.top) - (c.height - r.height) / 2,
          behavior: 'smooth'
        }});
        document.querySelectorAll('.node.hit').forEach(function (o) {{
          o.classList.remove('hit');
        }});
        void g.getBoundingClientRect();
        g.classList.add('hit');
        setTimeout(function () {{ g.classList.remove('hit'); }}, 1200);
      }});
    }});
  }})();
</script>
<script>
{editor_js}
</script>
<script type="application/json" id="flow-src" data-name="{src_name}">
{src_json}
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
    except OSError as e:
        print(f"✖ เปิดไฟล์ไม่ได้: {e.filename or a.src} — {e.strerror}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"✖ อ่านไฟล์ token ไม่ออก: {a.tokens} — {e}", file=sys.stderr)
        sys.exit(2)
    print(f"✔ {out}")
    print(f"  {len(m['nodes'])} nodes · {len(m['edges'])} edges · "
          f"canvas {m['canvas']['w']}×{m['canvas']['h']} · "
          f"diamond {m['geometry']['diamond']['w']}×{m['geometry']['diamond']['h']}")
    ex = m.get("exports", {})
    made = [v for v in (ex.get("html"), ex.get("svg"), ex.get("png")) if v]
    print(f"  ไฟล์ที่ได้: {' · '.join(made)}")
    if not ex.get("png"):
        print("  (ไม่ได้ .png — เครื่องนี้ไม่มี qlmanage ของ macOS ใช้ .svg แทนได้)")


if __name__ == "__main__":
    main()
