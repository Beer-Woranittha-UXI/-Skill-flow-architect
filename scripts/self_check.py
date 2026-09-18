#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_check.py — ตรวจ .flow ตามกฎทีม (ISO 5807 + Amm template) แล้วรายงานเป็น
R0 / R1 / R2 ตาม Team Rule 11.

    R0  ต้องแก้ก่อนส่ง (ผิดกฎ flowchart ตรง ๆ — อ่านผิดความหมายได้)
    R1  ควรแก้ (อ่านออกแต่ไม่ตรงมาตรฐานทีม)
    R2  ข้อสังเกต (คุณภาพ copy / ความสวยงาม)

ต้องมี: Python 3.9+ เท่านั้น (stdlib ล้วน) ไม่ต้องลง package · import จาก render_flow.py
ที่อยู่โฟลเดอร์เดียวกัน

Usage:
    python3 self_check.py flow.flow                 # ตรวจ source
    python3 self_check.py flow.flow --meta f.meta.json   # + ตรวจ geometry ที่ render แล้ว
    python3 self_check.py flow.flow --json

exit 1 เมื่อเจอ R0 — ใช้เป็น gate ก่อน hand-off ได้
สคริปต์นี้ "ไม่แก้ให้" โดยเจตนา: มันบอกว่าอะไรผิด แล้วดีไซเนอร์เป็นคนตัดสินใจแก้
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_flow import (  # noqa: E402
    EDGE_BACK, EDGE_NO, EDGE_PERM, EDGE_SOLID, EDGE_YES, FlowError, SHAPE, parse,
)

# หางคำถามที่ template ของ Amm กำหนด (references/flow-rules.md §2 กฎ S6)
DEC_TAIL = ("ใช่หรือไม่?", "ใช่หรือไม่")
# คำเชื่อมที่บอกว่าข้าวหลามตัดอันเดียวถามหลายเรื่อง — ตรวจหลังตัดหางคำถามออก
# ไม่งั้น "หรือ" ใน "ใช่หรือไม่?" จะติดทุกอัน
MULTI = ["และ", "หรือ", " and ", " or ", "&"]
OK_LABELS = {"yes", "no"}   # ป้ายทางออกที่ใช้ได้ มีแค่สองคำนี้ (กฎ S11)


def add(rows, sev, code, msg, where=""):
    rows.append({"sev": sev, "code": code, "msg": msg, "where": where})


def check_source(nodes, order, edges):
    rows = []
    out = {n: [] for n in nodes}
    inc = {n: [] for n in nodes}
    for e in edges:
        out[e["src"]].append(e)
        inc[e["dst"]].append(e)

    starts = [n for n in order if nodes[n]["kind"] == "START"]
    ends = [n for n in order if nodes[n]["kind"] == "END"]
    if len(starts) > 1:
        add(rows, "R1", "S1", f"มี START {len(starts)} จุด — flowchart ทั่วไปควรมี 1 จุด "
            "(หลาย START ใช้กับกระบวนการซับซ้อนเท่านั้น)", ", ".join(starts))
    if not ends:
        add(rows, "R1", "S2", "ยังไม่มี END — flow ต้องบอกว่าจบที่ไหน")

    BACK_OK = ("PAGE", "START")
    for e in edges:
        if e["type"] != EDGE_BACK:
            continue
        k = nodes[e["dst"]]["kind"]
        if k not in BACK_OK:
            add(rows, "R0", "S14", f'เส้นย้อนกลับชี้ไปที่ {k} — "ย้อนกลับไปหน้าก่อนหน้า" '
                "ต้องชี้ไปที่หน้าจอ (PAGE/START) เท่านั้น ข้าวหลามตัดเป็นเงื่อนไขที่ระบบเช็คเอง "
                "ผู้ใช้กดย้อนกลับไปที่นั่นไม่ได้ — ให้ชี้ไปหน้าจอก่อนหน้าจริง ๆ",
                f'{e["src"]}→{e["dst"]}')

    dec_labels = {}
    for nid in order:
        n = nodes[nid]
        kind, label = n["kind"], n["label"]
        outs = [e for e in out[nid] if e["type"] != EDGE_PERM]

        if kind == "DEC":
            branches = [e for e in outs if e["type"] != EDGE_BACK] + \
                       [e for e in outs if e["type"] == EDGE_BACK]
            if len(branches) < 2:
                add(rows, "R0", "S3", f"Decision มีทางออก {len(branches)} ทาง — "
                    "ต้องมี 2 ทาง (Yes + No) และทุกทางต้องมีป้าย", nid)
            unlabeled = [e for e in branches if not e["label"]]
            if unlabeled:
                add(rows, "R0", "S4", f"ทางออก {len(unlabeled)} เส้นของ Decision ไม่มีป้าย "
                    "(Yes/No หรือเงื่อนไขที่ชัดเจน)", nid)
            if len(branches) > 2:
                add(rows, "R0", "S5", f"Decision แตก {len(branches)} ทาง — ข้าวหลามตัดมีทางออกได้ "
                    "2 ทางคือ Yes กับ No เท่านั้น เงื่อนไขที่เหลือให้แยกเป็น Decision อันถัดไปต่อกัน", nid)
            if not label.endswith(DEC_TAIL):
                add(rows, "R0", "S6", "ข้อความ Decision ต้องจบด้วย “ใช่หรือไม่?” เท่านั้น → "
                    f"ปัจจุบัน: {label!r}", nid)
            stem = label
            for tail in DEC_TAIL:
                if stem.endswith(tail):
                    stem = stem[: -len(tail)]
                    break
            for m in MULTI:
                if m in f" {stem} ":
                    add(rows, "R1", "S7", f"หนึ่งข้าวหลามตัดถามได้หนึ่งเรื่อง — เจอ {m.strip()!r} "
                        "ในคำถาม ให้แยกเป็นหลาย Decision", nid)
                    break
            key = re.sub(r"\s+", "", label)
            if key in dec_labels:
                add(rows, "R0", "S8", f"คำถาม Decision ซ้ำกับ {dec_labels[key]} — ISO 5807 "
                    "กำหนดให้แต่ละเงื่อนไขมีชื่อไม่ซ้ำ ถ้าเป็นเงื่อนไขเดียวกันให้โยงกลับ node เดิม", nid)
            else:
                dec_labels[key] = nid
            plain = [e for e in outs if e["type"] == EDGE_SOLID and not e["label"]]
            if plain:
                add(rows, "R0", "S13", f"Diamond มีเส้นออกแบบไม่มีป้าย {len(plain)} เส้น — "
                    "เส้นที่ออกจากข้าวหลามตัดต้องเป็น Yes / No หรือเงื่อนไขที่มีป้ายเท่านั้น", nid)
            labs = {e["label"].lower() for e in branches if e["label"]}
            odd = sorted(l for l in labs if l not in OK_LABELS)
            if odd:
                add(rows, "R0", "S11", f"ป้ายทางออกใช้ได้แค่ Yes กับ No — เจอ {', '.join(odd)} "
                    "(True/False หรือ ต่ำ/ปกติ/สูง ใช้ไม่ได้ ให้แยกเป็นหลาย Decision ต่อกัน)", nid)
        else:
            if label.endswith(DEC_TAIL):
                add(rows, "R1", "S6b", "ข้อความลงท้าย “ใช่หรือไม่?” แต่ไม่ใช่ Decision — "
                    "ถ้าเป็นเงื่อนไขให้เปลี่ยนเป็น DEC", nid)

        if kind in ("PAGE",):
            words = len([w for w in re.split(r"\s+", label) if w])
            if len(label) > 42:
                add(rows, "R2", "S9", f"ข้อความยาว {len(label)} ตัวอักษร — Process ควรเป็นวลี "
                    "“กริยา + กรรม” สั้น ๆ ประมาณ 3–5 คำ", nid)
            elif words > 6:
                add(rows, "R2", "S9", f"ข้อความ {words} คำ — Process ควรอยู่ราว 3–5 คำ", nid)

        if not outs and kind not in ("END", "LINK", "PERMNOTE"):
            add(rows, "R1", "S10", "กล่องนี้ไม่มีเส้นออกและไม่ใช่ END — flow ค้างอยู่ตรงนี้", nid)
        if kind == "LINK" and not inc[nid]:
            add(rows, "R1", "S12", "Link To Flow ไม่มีเส้นเข้า — ไม่รู้ว่าเข้ามาจากไหน", nid)

    # terminology consistency: ข้อความเดียวกันต้องเป็น node เดียวกัน
    seen = {}
    for nid in order:
        key = (nodes[nid]["kind"], re.sub(r"\s+", "", nodes[nid]["label"]))
        if key in seen:
            add(rows, "R1", "S8b", f"ข้อความซ้ำกับ {seen[key]} — ใช้สัญลักษณ์และชื่อเรียกให้เหมือนกัน "
                "ทั้ง flow: ถ้าเป็นหน้าเดียวกันให้โยงกลับ node เดิม", nid)
        else:
            seen[key] = nid

    # reachability
    reach, stack = set(), list(starts) or [order[0]]
    while stack:
        cur = stack.pop()
        if cur in reach:
            continue
        reach.add(cur)
        stack += [e["dst"] for e in out[cur]]
    for nid in order:
        if nid not in reach:
            add(rows, "R1", "S10b", "เดินจาก START มาไม่ถึงกล่องนี้", nid)
    return rows


def check_coverage(nodes, order, edges):
    """E1/E2 — เคสพิเศษ (edge case · error state · empty state) ยังไม่ถูกปิด

    ตรวจได้เท่าที่เห็นจากโครงสร้าง: หน้าไหนยังไม่มี PERM ผูกอยู่
    ส่วนเนื้อในว่าเคสที่ขาดคืออะไร เป็นหน้าที่ของ §4 ในรายงาน ไม่ใช่ของเครื่อง
    """
    rows = []
    pages = [n for n in order if nodes[n]["kind"] == "PAGE"]
    if not pages:
        return rows
    has_perm = {n for n in order if nodes[n].get("has_perm")}

    if not has_perm:
        add(rows, "R1", "E1",
            f"ทั้งใบมี {len(pages)} หน้า แต่ยังไม่มีหน้าไหนผูกเคสพิเศษไว้เลย — "
            "ไล่ดู edge case / error state / empty state ทีละหน้าก่อนส่ง "
            "(ใช้ PERM <id> <เคส>)")
        return rows

    # หน้าที่ผู้ใช้ถูกส่งมาเพราะเงื่อนไขไม่ผ่าน = ทางพลาด มักมีเคสค้างมากที่สุด
    fail_pages = [e["dst"] for e in edges
                  if e["type"] == EDGE_NO and nodes[e["dst"]]["kind"] == "PAGE"]
    naked = [n for n in dict.fromkeys(fail_pages) if n not in has_perm]
    if naked:
        add(rows, "R2", "E2",
            f"หน้าทางพลาด {len(naked)} หน้ายังไม่มีเคสพิเศษ — "
            "หน้าที่ผู้ใช้ถูกส่งมาเพราะเงื่อนไขไม่ผ่าน มักมีทั้งกรณีทำไม่สำเร็จซ้ำ "
            "และกรณีข้อมูลว่าง", ", ".join(naked))
    return rows


AI_LIMIT = 0.40


def check_authorship(nodes, order, edges):
    """A1 — สัดส่วนกล่องที่มาจากข้อเสนอของ AI

    Step 4 ของ SKILL.md บอกให้หยุดเมื่อเกิน 40% เดิมต้องนับมือ ตรงนี้ให้เครื่องนับแทน
    ไฟล์ที่ไม่มีคอมเมนต์ [AI-xx] เลย จะนับเป็นของดีไซเนอร์ทั้งใบ
    """
    rows = []
    boxes = [n for n in order if nodes[n]["kind"] != "PERMNOTE"]
    if not boxes:
        return rows
    ai = [n for n in boxes if nodes[n].get("author") == "ai"]
    if not ai:
        return rows
    pct = len(ai) / len(boxes)
    if pct > AI_LIMIT:
        add(rows, "R1", "A1",
            f"กล่องที่มาจากข้อเสนอของ AI {len(ai)}/{len(boxes)} = {pct:.0%} "
            f"เกิน {AI_LIMIT:.0%} — ใบนี้กำลังกลายเป็น flow ของ AI "
            "ควรถอยไปคุยโครงกับดีไซเนอร์ก่อน (SKILL.md Step 4)",
            ", ".join(ai))
    ai_e = [e for e in edges if e.get("author") == "ai"]
    if ai_e:
        add(rows, "R2", "A2",
            f"เส้นที่มาจากข้อเสนอของ AI {len(ai_e)} เส้น — ตรวจว่าอนุมัติครบแล้ว",
            ", ".join(f'{e["src"]}→{e["dst"]}' for e in ai_e))
    return rows


def check_geometry(meta):
    rows = []
    g, sp = meta["geometry"], meta["spacing"]
    if g["radius"] != 16:
        add(rows, "R0", "G5", f'corner radius = {g["radius"]} — ทีมกำหนด 16', "tokens")
    if (g["pad_y"], g["pad_x"]) != (16, 24):
        add(rows, "R0", "G5", f'padding = {g["pad_y"]}/{g["pad_x"]} — ทีมกำหนด 16 (บน-ล่าง) / 24 (ซ้าย-ขวา)', "tokens")

    kind = {n["id"]: n["kind"] for n in meta["nodes"]}
    # เทียบเป็น "รูปแบบเส้น" ที่ตาเห็น: ทึบ (ปกติ/Yes/No) · ประน้ำเงิน (ย้อนกลับ) · ประเหลือง
    STYLE = {"solid": "ทึบ", "yes": "ทึบ", "no": "ทึบ", "back": "ประน้ำเงิน", "perm": "ประเหลือง"}
    sides = {}
    for e in meta["edges"]:
        if e["length"] + 0.5 < sp["edge_min"]:
            add(rows, "R1", "G1", f'เส้นยาว {e["length"]}px — ทีมกำหนดเริ่มต้นที่ {sp["edge_min"]:.0f}px',
                f'{e["src"]}→{e["dst"]}')
        if e["label_pos"] and abs(e["label_pos"]["dist_from_exit"] - sp["label_offset"]) > 1:
            add(rows, "R2", "G2", f'ป้าย {e["label"]!r} อยู่ห่างจากจุดออก '
                f'{e["label_pos"]["dist_from_exit"]}px — ทีมกำหนด {sp["label_offset"]:.0f}px '
                "(เส้นสั้นเกินไป ให้ยืดระยะ node)", f'{e["src"]}→{e["dst"]}')
        if e.get("outside_canvas"):
            add(rows, "R0", "G8", "เส้นวิ่งออกนอกกรอบ canvas แล้วถูกตัดหาย — "
                "เพิ่มระยะขอบ (margin) หรือลดระยะเส้น (SPACING edge=)",
                f'{e["src"]}→{e["dst"]}')
        if e["crosses"]:
            add(rows, "R1", "G3", f'เส้นวิ่งทับกล่อง {", ".join(e["crosses"])} — '
                "ย้ายลำดับ branch หรือเพิ่มระยะคอลัมน์", f'{e["src"]}→{e["dst"]}')
        st = STYLE[e["type"]]
        points = [(e["src"], e["out_anchor"])]
        if not (kind.get(e["dst"]) == "DEC" and st == "ทึบ"):
            # ทางเข้า Diamond ของเส้นทึบรวมที่ปลายซ้ายโดยตั้งใจ (จุดรวมเส้น)
            points.append((e["dst"], e["in_anchor"]))
        for node, anchor in points:
            side = anchor[0]                 # ตัดหมายเลขจุดออก เหลือด้าน
            owner = sides.get((node, side))
            if owner and owner != st:
                add(rows, "R0", "G6", f'ด้าน {side} ของ {node} มีเส้นคนละรูปแบบมาเกาะ '
                    f'({owner} + {st}) — เส้นคนละรูปแบบต้องเชื่อมที่มุมอื่นของกล่อง', node)
            sides[(node, side)] = owner or st

    boxes = meta["nodes"]
    for e in meta["edges"]:
        lp = e["label_pos"]
        if not lp:
            continue
        for n in boxes:
            if n["x"] - 24 < lp["x"] < n["x"] + n["w"] + 24 and \
               n["y"] - 18 < lp["y"] < n["y"] + n["h"] + 18:
                add(rows, "R1", "G7", f'ป้าย {e["label"]!r} ไปทับกล่อง {n["id"]} — '
                    "ป้ายต้องอยู่บนพื้นที่ว่าง", f'{e["src"]}→{e["dst"]}')

    exits = {}
    for e in meta["edges"]:
        if e["type"] in ("yes", "no"):
            exits.setdefault(e["src"], {})[e["type"]] = e["out_anchor"][0]
    for node, ex in exits.items():
        if len(ex) == 2 and len(set(ex.values())) == 1:
            add(rows, "R0", "G9", f'เส้น Yes กับ No ออกจากด้าน {list(ex.values())[0]} '
                "ด้านเดียวกัน — ทางออกสองทางของข้าวหลามตัดต้องแยกด้านกัน ไม่งั้นเห็นเป็นเส้นเดียว "
                "ที่มีป้ายทับกัน", node)

    dia = {(n["w"], n["h"]) for n in meta["nodes"] if n["kind"] == "DEC"}
    if len(dia) > 1:
        add(rows, "R0", "G4", f"Diamond ขนาดไม่เท่ากัน: {sorted(dia)} — ต้องเท่ากันทุกอัน", "diamonds")
    return rows


SEV_ORDER = {"R0": 0, "R1": 1, "R2": 2}
ICON = {"R0": "⛔", "R1": "⚠️ ", "R2": "💬"}


def main():
    ap = argparse.ArgumentParser(description="ตรวจ .flow ตามกฎ flowchart ของทีม")
    ap.add_argument("src")
    ap.add_argument("--meta", help="ไฟล์ .meta.json ที่ render_flow.py สร้าง")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        _, _, nodes, order, edges = parse(a.src)
    except FlowError as e:
        print(f"⛔ อ่านไฟล์ไม่ผ่าน: {e}", file=sys.stderr)
        sys.exit(2)

    rows = check_source(nodes, order, edges)
    rows += check_authorship(nodes, order, edges)
    rows += check_coverage(nodes, order, edges)
    meta_path = a.meta
    if not meta_path:
        guess = os.path.splitext(a.src)[0] + ".meta.json"
        meta_path = guess if os.path.exists(guess) else None
    if meta_path:
        rows += check_geometry(json.load(open(meta_path, encoding="utf-8")))

    rows.sort(key=lambda r: (SEV_ORDER[r["sev"]], r["code"]))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        counts = {s: sum(1 for r in rows if r["sev"] == s) for s in ("R0", "R1", "R2")}
        real = [n for n in order if nodes[n]["kind"] != "PERMNOTE"]
        pages = [n for n in order if nodes[n]["kind"] == "PAGE"]
        covered = [n for n in pages if nodes[n].get("has_perm")]
        cov = f" · เคสพิเศษ {len(covered)}/{len(pages)} หน้า" if pages else ""
        print(f"\nตรวจ {os.path.basename(a.src)} — "
              f'{len(real)} กล่อง / {len(edges)} เส้น   '
              f'R0={counts["R0"]}  R1={counts["R1"]}  R2={counts["R2"]}{cov}')
        print("-" * 78)
        for r in rows:
            where = f' [{r["where"]}]' if r["where"] else ""
            print(f'{ICON[r["sev"]]} {r["sev"]} {r["code"]}{where} {r["msg"]}')
        if not rows:
            print("✔ ผ่านทุกข้อ")
        print()
    sys.exit(1 if any(r["sev"] == "R0" for r in rows) else 0)


if __name__ == "__main__":
    main()
