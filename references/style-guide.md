# Style guide — หน้าตาแบบทีม UXUI

## Tokens (ค่าตั้งต้น)

| token | ค่า | ใช้กับ |
|---|---|---|
| `primary` | `#4060d0` | พื้นของทุกกล่อง (Process, Diamond, Terminator, Link chip) + เส้นโยง |
| `ink` | `#17181C` | หัวเรื่อง / ข้อความบนพื้นขาว |
| `muted` | `#6B7280` | header ย่อย, legend, ป้ายกลาง |
| `paper` / `paper_2` | `#FFFFFF` / `#F7F8FB` | พื้นกล่อง / พื้นหลังหน้า |
| `yes` | `#1b991e` | ป้าย Yes |
| `no` | `#b82121` | ป้าย No |
| `perm` | `#ffce2c` | เส้นประ + กล่อง note ของ Permutation |
| `hairline` | `#E4E7EE` | เส้นคั่น card |

ทุกกล่องเป็นพื้นน้ำเงินทึบ ตัวอักษรขาว — แยกประเภทด้วย **รูปทรง** ไม่ใช่สี
(วงรี = เริ่ม/จบ · สี่เหลี่ยม = หน้า/ขั้นตอน · ข้าวหลามตัด = เงื่อนไข · chip = Link To Flow)
กล่อง note ของ Permutation เป็นพื้นเหลืองอ่อนเส้นประ เพราะมันคือ *คำอธิบาย* ไม่ใช่ขั้นของ flow

ตัวอักษร **Sarabun** (400/600/700) ทั้งไทยและอังกฤษ — node label 16px semibold,
ป้ายเส้นและ note 13px bold, หัวเรื่อง 20px bold
เลือก Sarabun เพราะเป็นฟอนต์ที่ทีมใช้ในไฟล์ Figma อยู่แล้ว วรรณยุกต์ไทยไม่ชนกันที่ 16px

**สีเน้นใช้ประหยัด** — ทั้งใบมีสีเน้นได้แค่ Yes/No และเส้น Permutation
ถ้าระบายสีทุกกล่องเพื่อ "แยกประเภท" แปลว่าใช้รูปทรงผิด (ดู flow-rules §1)
ไม่มีเงา ไม่มี gradient ไม่มี glow — diagram ที่มีเงาอ่านยากขึ้นและพิมพ์ออกมาเทา

## ดึง token ของโปรเจกต์มาใช้

ถ้าโปรเจกต์มี `design.md` หรือ `tokens.json` (จาก `design-builder` / `design-export-dtcg`)
ให้ map แบบนี้ก่อนใช้ค่า default: `color.primary.default` → `primary`,
`color.success` → `yes`, `color.danger` → `no`, `color.warning` → `perm`,
`color.text.primary` → `ink`, `color.surface` → `paper`
เขียนไฟล์ `flow-tokens.json` ไว้ในโปรเจกต์ แล้วส่งเข้า renderer — **อย่าไปแก้ `TOKENS`
ใน `scripts/render_flow.py`** เพราะไฟล์นั้นเป็นของทีมทั้งทีม (symlink ไปที่ repo กลาง)
แก้แล้วกระทบทุกโปรเจกต์

```json
{ "primary": "#1E40AF", "perm": "#F59E0B" }
```
```bash
python3 scripts/render_flow.py flow.flow --tokens flow-tokens.json
```

และอย่าแก้ CSS ใน HTML ที่ออกมาแล้ว เพราะ render ครั้งถัดไปทับทิ้งหมด

## เชื่อมกับ writing skill ของโปรเจกต์

ก่อนเสนอแก้ข้อความในกล่อง ให้อ่านกฎของโปรเจกต์นั้นตามลำดับนี้ เจออันไหนก่อนใช้อันนั้น

1. `docs/writing/*.md` หรือ `docs/brand/voice-tone.md` ใน repo ของโปรเจกต์
2. memory/rules ของโปรเจกต์ (เช่น กฎ UX writing ของลอตเตอรี่พลัส — terminology ที่ล็อกไว้)
3. skill กลาง: `ux-writer` (microcopy ทั่วไป), `modal-writer` (โทนภาษาไทยทางการ)
4. ค่าตั้งต้นใน flow-rules §4

ข้อความในกล่องต้องใช้ terminology เดียวกับที่โปรเจกต์ล็อกไว้ ถ้าใน flow เรียก "กระเป๋าเงิน"
แต่ใน UI เรียก "Wallet" ตรงนี้คือ R1 ที่ต้องรายงาน ไม่ใช่เรื่องเล็ก — flow เป็นเอกสารที่
dev กับ QA ใช้อ้างชื่อหน้าจอ

## ส่งต่อ

- ไฟล์ HTML เปิดได้ทุกเครื่องไม่ต้องลงอะไร ส่งแนบใน Jira / Confluence / Lark ได้เลย
- `.flow` + `.meta.json` เก็บลง repo คู่กัน — `.flow` ให้คนแก้ `.meta.json` ให้เครื่องอ่าน
- เอาขึ้น Figma: วาดมือตาม HTML (connector ผ่าน MCP ยังทำไม่ได้) ใช้ Sarabun,
  ยึดพิกัดจาก `.meta.json` เพื่อให้ระยะตรงกับที่ตรวจไว้
- Wireflow (ต่อ wireframe เข้ากับ flow) และ prototype/mock data **ไม่อยู่ในใบนี้** —
  เป็นการ์ดแยก ส่ง `.flow` + `.meta.json` ให้ใบนั้นต่อ
