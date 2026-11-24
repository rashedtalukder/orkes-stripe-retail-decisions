"""Generate the blog architecture diagram (SVG) for docs/diagrams/. Re-run after edits:
    python3 docs/diagrams/build_architecture_diagram.py
Render a PNG with any SVG renderer, e.g. headless Chrome (see docs/diagrams/README.md).
"""

from pathlib import Path
from xml.sax.saxutils import escape as esc

W, H = 1440, 800
OUT = Path(__file__).resolve().parent / "architecture.svg"

C = {  # (fill, stroke) per role, following the architecture-diagram design system
    "orkes": ("rgba(6, 78, 59, 0.45)", "#34d399"),     # Conductor system tasks
    "worker": ("rgba(8, 51, 68, 0.45)", "#22d3ee"),    # Python worker tasks
    "ai": ("rgba(76, 29, 149, 0.45)", "#a78bfa"),      # model interpretation
    "policy": ("rgba(120, 53, 15, 0.4)", "#fbbf24"),   # business rule / policy data
    "money": ("rgba(136, 19, 55, 0.45)", "#fb7185"),   # guarded side effect
    "ext": ("rgba(30, 41, 59, 0.6)", "#94a3b8"),       # external systems / inputs
}
FONT = "ui-monospace, 'JetBrains Mono', SFMono-Regular, Menlo, Consolas, monospace"
parts: list[str] = []


def box(x, y, w, h, role, title, lines=(), dashed=False, title_size=13):
    fill, stroke = C[role]
    dash = ' stroke-dasharray="6,4"' if dashed else ""
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="#0f172a"/>')
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}" stroke-width="1.6"{dash}/>')
    parts.append(f'<text x="{x + w / 2}" y="{y + 22}" text-anchor="middle" fill="#f8fafc" font-size="{title_size}" font-weight="700">{esc(title)}</text>')
    for i, ln in enumerate(lines):
        parts.append(f'<text x="{x + w / 2}" y="{y + 41 + i * 15}" text-anchor="middle" fill="#cbd5e1" font-size="10.5">{esc(ln)}</text>')


def arrow(x1, y1, x2, y2, color="#64748b", label=None, lx=None, ly=None, dashed=False):
    dash = ' stroke-dasharray="5,4"' if dashed else ""
    parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.6" marker-end="url(#arrow)"{dash}/>')
    if label:
        # Label beside the arrow (start-anchored), never on top of the line.
        parts.append(f'<text x="{lx if lx is not None else (x1 + x2) / 2 + 8}" y="{ly if ly is not None else (y1 + y2) / 2}" '
                     f'text-anchor="start" fill="#94a3b8" font-size="10">{esc(label)}</text>')


def path(d, color="#64748b", dashed=False):
    dash = ' stroke-dasharray="5,4"' if dashed else ""
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" marker-end="url(#arrow)"{dash}/>')


# ---------- arrows first (behind boxes) ----------
# inputs -> workflow
path("M 232 258 C 255 258, 255 372, 282 372")
path("M 232 486 C 255 486, 255 380, 282 380")
# main flow
row_y = 378
for x1, x2 in [(442, 466), (618, 642), (794, 818), (994, 1018), (1170, 1194)]:
    arrow(x1, row_y, x2, row_y, "#e2e8f0")
# externals <-> tasks
arrow(366, 150, 366, 326, "#a78bfa", "label + metadata only", 374, 280)
arrow(542, 150, 542, 326, "#94a3b8", "read-only API calls", 550, 280)
arrow(906, 150, 906, 326, "#fbbf24", "loads pinned CSV", 914, 280)
# switch -> outcomes
path("M 1270 430 L 1270 470")                     # to refund
path("M 1230 430 C 1230 455, 1120 450, 1110 470")  # to other outcomes
# refund -> stripe refund (exits boundary)
arrow(1270, 552, 1270, 612, "#fb7185", "refund (sandbox)", 1278, 590)
# optional routing pattern (dashed, from classify)
path("M 366 430 L 366 470", "#a78bfa", dashed=True)

# ---------- header ----------
parts.append(f'<text x="40" y="44" fill="#f8fafc" font-size="22" font-weight="700">Rules decide. AI interprets. Orkes Conductor runs it.</text>')
parts.append(f'<text x="40" y="68" fill="#94a3b8" font-size="12.5">{esc("Billing support reference app: Stripe facts → native BUSINESS_RULE → permitted action. The model only labels the customer" + chr(39) + "s intent.")}</text>')

# ---------- externals (top row) ----------
box(282, 88, 168, 62, "ai", "Intent model", ["Jev · Claude Haiku"])
box(458, 88, 168, 62, "ext", "Stripe (sandbox)", ["charges·invoices·refunds"])
box(796, 88, 220, 62, "policy", "Rule file in Git", ["CSV, pinned by commit + SHA-256"])

# ---------- inputs (left) ----------
box(30, 224, 202, 70, "ext", "Support form", ["structured claim type", "→ rules-only mode"])
box(30, 452, 202, 70, "ext", "Chat / email", ["free-text message", "→ AI mode"])

# ---------- Orkes workflow boundary ----------
parts.append('<rect x="262" y="178" width="1150" height="390" rx="12" fill="none" stroke="#34d399" stroke-width="1.4" stroke-dasharray="8,4"/>')
# Title sits right of the last vertical arrow (x=906) so no line crosses it.
parts.append('<text x="1398" y="204" text-anchor="end" fill="#34d399" font-size="13" font-weight="700">Orkes Conductor workflow · v1</text>')
parts.append('<text x="1398" y="222" text-anchor="end" fill="#94a3b8" font-size="10.5">billing_rules_only / billing_rules_ai</text>')
parts.append('<text x="1398" y="240" text-anchor="end" fill="#94a3b8" font-size="10.5">durable · retries &amp; timeouts · every task I/O recorded</text>')

# ---------- task row ----------
box(290, 326, 152, 104, "ai", "classify intent", ["SIMPLE worker", "AI mode only", "6 labels, no authority"], title_size=12)
box(466, 326, 152, 104, "worker", "fetch facts", ["SIMPLE worker", "duplicate? proration?", "refunded? integer cents"], title_size=12)
box(642, 326, 152, 104, "orkes", "normalize intent", ["JSON_JQ_TRANSFORM", "invalid / unknown", "→ UNKNOWN"], title_size=12)
box(818, 326, 176, 104, "policy", "BUSINESS_RULE", ["billing_support_policy", "FIRE_FIRST · rule ID", "+ policy version"], title_size=12)
box(1018, 326, 152, 104, "orkes", "finalize", ["JSON_JQ_TRANSFORM", "no match →", "HUMAN_REVIEW"], title_size=12)
box(1194, 326, 152, 104, "orkes", "SWITCH", ["only APPROVE_REFUND", "reaches money"], title_size=12)

# ---------- outcomes inside boundary ----------
box(1194, 470, 200, 82, "money", "execute refund", ["worker · dry-run by default", "2 opt-ins · idempotency key", "amount from verified facts"], title_size=12)
box(990, 470, 184, 82, "ext", "other decisions", ["EXPLAIN_CHARGE · INVESTIGATE", "HUMAN_REVIEW", "NO_ADDITIONAL_REFUND"], title_size=12)
box(290, 470, 520, 82, "ai", "Extension pattern (not in the benchmark)", [
    "routing rule on model confidence → accept · second model (e.g. Claude)",
    "· HUMAN task. Routes interpretation only; the billing rule stays authority."], dashed=True, title_size=12)

# ---------- below boundary ----------
box(262, 600, 880, 62, "orkes", "Governance with Orkes Conductor", [
    "execution history & audit trail · versioned workflows · pinned, testable rule file · access control & app keys · secrets"],
    title_size=12)
box(1160, 612, 252, 50, "money", "Stripe refund (sandbox)", [], title_size=12)

# ---------- legend (below everything, outside boundaries) ----------
lx, ly = 40, 700
parts.append(f'<text x="{lx}" y="{ly}" fill="#94a3b8" font-size="11" font-weight="700">LEGEND</text>')
legend = [("orkes", "Conductor system task / platform"), ("worker", "Python worker task"), ("ai", "AI interpretation"),
          ("policy", "Business policy (data)"), ("money", "Guarded side effect"), ("ext", "External system / input")]
for i, (role, label) in enumerate(legend):
    x = lx + (i % 3) * 330
    y = ly + 16 + (i // 3) * 26
    fill, stroke = C[role]
    parts.append(f'<rect x="{x}" y="{y}" width="22" height="14" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
    parts.append(f'<text x="{x + 30}" y="{y + 11}" fill="#cbd5e1" font-size="11">{esc(label)}</text>')
parts.append(f'<text x="1412" y="{ly + 56}" text-anchor="end" fill="#64748b" font-size="10">dashed = optional pattern, not part of the measured benchmark</text>')

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">
<defs>
  <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/></pattern>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker>
</defs>
<rect width="{W}" height="{H}" fill="#020617"/>
<rect width="{W}" height="{H}" fill="url(#grid)"/>
{chr(10).join(parts)}
</svg>
"""
OUT.write_text(svg)
print(f"wrote {OUT}")
