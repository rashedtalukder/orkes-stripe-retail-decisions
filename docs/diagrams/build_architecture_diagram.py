"""Generate the blog diagrams in docs/diagrams/. Re-run after edits:
    python3 docs/diagrams/build_architecture_diagram.py
then render PNGs at 2x (see docs/diagrams/README.md).

Sizing follows the Orkes blog layout (measured on orkes.io/blog posts): the article column is 892 CSS px wide and
in-body images fill it, so the body diagram is designed at 892 px wide with >= 12 px text and exported at 2x
(1784 px). Hero images on the blog are 1.91:1 (rendered 892x467 from ~1800x942 sources), so the hero is designed at
900x471 and exported at 2x (1800x942).
"""

from pathlib import Path
from xml.sax.saxutils import escape as esc

HERE = Path(__file__).resolve().parent
FONT = "ui-monospace, 'JetBrains Mono', SFMono-Regular, Menlo, Consolas, monospace"
C = {  # (fill, stroke) per role, following the architecture-diagram design system
    "orkes": ("rgba(6, 78, 59, 0.45)", "#34d399"),     # Conductor system tasks / platform
    "worker": ("rgba(8, 51, 68, 0.45)", "#22d3ee"),    # Python worker tasks
    "ai": ("rgba(76, 29, 149, 0.45)", "#a78bfa"),      # model interpretation
    "policy": ("rgba(120, 53, 15, 0.4)", "#fbbf24"),   # business rule / policy data
    "money": ("rgba(136, 19, 55, 0.45)", "#fb7185"),   # guarded side effect
    "ext": ("rgba(30, 41, 59, 0.6)", "#94a3b8"),       # external systems / inputs
}


class Canvas:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def box(self, x, y, w, h, role, title, lines=(), dashed=False, ts=14, ls=12, align="middle", line_gap=17):
        fill, stroke = C[role]
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        tx = x + w / 2 if align == "middle" else x + 14
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#0f172a"/>')
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.6"{dash}/>')
        self.parts.append(f'<text x="{tx}" y="{y + ts + 10}" text-anchor="{align}" fill="#f8fafc" font-size="{ts}" font-weight="700">{esc(title)}</text>')
        for i, ln in enumerate(lines):
            self.parts.append(f'<text x="{tx}" y="{y + ts + 12 + (i + 1) * line_gap}" text-anchor="{align}" fill="#cbd5e1" font-size="{ls}">{esc(ln)}</text>')

    def path(self, d, color="#94a3b8", dashed=False, width=1.8):
        dash = ' stroke-dasharray="5,4"' if dashed else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" marker-end="url(#arrow)"{dash}/>')

    def text(self, x, y, s, size=12, color="#94a3b8", weight="400", anchor="start"):
        self.parts.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" fill="{color}" font-size="{size}" font-weight="{weight}">{esc(s)}</text>')

    def svg(self):
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">
<defs>
  <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/></pattern>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#cbd5e1"/></marker>
</defs>
<rect width="{self.w}" height="{self.h}" fill="#020617"/>
<rect width="{self.w}" height="{self.h}" fill="url(#grid)"/>
{chr(10).join(self.parts)}
</svg>
"""


def body() -> str:
    """Top-to-bottom architecture for the 892 px article column."""
    c = Canvas(892, 900)
    L, LW = 36, 420            # left column: the workflow
    R, RW = 520, 340           # right column: external systems and governance
    CX = L + LW / 2            # flow line x
    TH, STEP = 72, 92          # task height and row step
    rows = [148 + i * STEP for i in range(6)]

    # arrows first (behind boxes)
    c.path(f"M {CX} 92 L {CX} {rows[0]}")
    for y in rows[:-1]:
        c.path(f"M {CX} {y + TH} L {CX} {y + STEP}", "#e2e8f0")
    c.path(f"M {R} {rows[0] + 36} L {L + LW} {rows[0] + 36}", "#a78bfa")                       # model -> classify
    c.path(f"M {R} {rows[1] + 36} L {L + LW} {rows[1] + 36}", "#94a3b8")                       # stripe -> facts
    c.path(f"M {L + LW} {rows[2] + 36} L {R} {rows[2] + 36}", "#a78bfa", dashed=True)         # normalize -> routing
    c.path(f"M {R} {rows[3] + 36} L {L + LW} {rows[3] + 36}", "#fbbf24")                       # rule file -> BR
    c.path(f"M {CX - 30} {rows[5] + TH} C {CX - 30} 692, 136 690, 136 708")                     # switch -> other
    c.path(f"M {CX + 30} {rows[5] + TH} C {CX + 30} 692, 356 690, 356 708", "#fb7185")         # switch -> refund
    c.path(f"M 456 751 L {R} 751", "#fb7185")                                                   # refund -> stripe

    # input
    c.box(L, 20, LW, 72, "ext", "Customer case", ["form: claim type (rules-only mode)", "chat / email: free text (AI mode)"])

    # Orkes boundary
    c.parts.append(f'<rect x="20" y="112" width="452" height="696" rx="12" fill="none" stroke="#34d399" stroke-width="1.5" stroke-dasharray="8,4"/>')
    c.text(36, 136, "Orkes Conductor workflow", 13, "#34d399", "700")

    # workflow tasks
    c.box(L, rows[0], LW, TH, "ai", "1 · classify intent  (SIMPLE worker)", ["AI mode only: one of 6 labels, no authority"])
    c.box(L, rows[1], LW, TH, "worker", "2 · fetch facts  (SIMPLE worker)", ["duplicate? proration? refunded? cents"])
    c.box(L, rows[2], LW, TH, "orkes", "3 · normalize intent  (JSON_JQ)", ["invalid or unknown label → UNKNOWN"])
    c.box(L, rows[3], LW, TH, "policy", "4 · BUSINESS_RULE  (FIRE_FIRST)", ["decision + rule ID + policy version"])
    c.box(L, rows[4], LW, TH, "orkes", "5 · finalize  (JSON_JQ)", ["no matching rule → HUMAN_REVIEW"])
    c.box(L, rows[5], LW, TH, "orkes", "6 · SWITCH on decision", ["only APPROVE_REFUND reaches money"])
    c.box(L, 708, 200, 86, "ext", "other outcomes", ["explain charge", "investigate", "human review"], ts=13)
    c.box(256, 708, 200, 86, "money", "execute refund", ["dry-run by default", "2 opt-ins", "idempotency key"], ts=13)

    # right column: externals, optional routing, governance
    c.box(R, rows[0], RW, TH, "ai", "Intent model (pluggable)", ["TypeSafe Jev · Claude Haiku"])
    c.box(R, rows[1], RW, TH, "ext", "Stripe (sandbox)", ["verified facts, read-only"])
    c.box(R, rows[2] - 8, RW, TH + 16, "ai", "Optional: confidence routing", ["accept · ask Claude / GPT-6 Luna", "· or open a HUMAN task"], dashed=True)
    c.box(R, rows[3], RW, TH, "policy", "Rule file in Git", ["CSV pinned by commit + SHA-256"])
    c.box(R, rows[4], RW, 164, "orkes", "Governance (Orkes Conductor)", [
        "· execution history & audit trail", "· versioned workflows", "· pinned, testable rules",
        "· access control & app keys", "· retries, timeouts, dry-run", "· secrets & env variables"], align="start", line_gap=19)
    c.box(R, 715, RW, 72, "money", "Stripe refund (sandbox)", ["only after APPROVE_REFUND"])

    # legend
    legend = [("orkes", "Conductor task / platform"), ("worker", "worker task"), ("ai", "AI interpretation"),
              ("policy", "business policy (data)"), ("money", "guarded side effect"), ("ext", "external system / input")]
    for i, (role, label) in enumerate(legend):
        x = 36 + (i % 3) * 290
        y = 834 + (i // 3) * 26
        fill, stroke = C[role]
        c.parts.append(f'<rect x="{x}" y="{y}" width="22" height="14" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        c.text(x + 30, y + 12, label, 12, "#cbd5e1")
    return c.svg()


def hero() -> str:
    """1.91:1 hero for the post header and social previews."""
    c = Canvas(900, 471)
    c.text(40, 74, "Rules decide. AI interprets.", 34, "#f8fafc", "700")
    c.text(40, 116, "Orkes Conductor runs it.", 34, "#34d399", "700")
    c.text(40, 152, "Add AI to your business rules instead of replacing them with prompts.", 15, "#94a3b8")
    steps = [("ext", "Customer", ["message"]), ("ai", "AI model", ["intent label"]),
             ("ext", "Stripe", ["verified facts"]), ("policy", "Business Rule", ["decision + ID"]),
             ("money", "Action", ["refund, explain", "or human"])]
    bw, gap, x0, y0 = 152, 21, 40, 214
    for i, (role, t, lines) in enumerate(steps):
        x = x0 + i * (bw + gap)
        if i:
            c.path(f"M {x - gap} {y0 + 44} L {x} {y0 + 44}", "#e2e8f0", width=2.2)
        c.box(x, y0, bw, 92, role, t, lines, ts=16, ls=14, line_gap=19)
    c.box(40, 340, 820, 58, "orkes", "Orkes Conductor: durable workflow · audit trail · versioned, governed policy", [], ts=15)
    c.text(40, 438, "Billing-support reference app · Stripe sandbox · TypeSafe Jev & Claude Haiku", 13, "#64748b")
    return c.svg()


if __name__ == "__main__":
    for name, fn in [("architecture.svg", body), ("architecture-hero.svg", hero)]:
        (HERE / name).write_text(fn())
        print(f"wrote {HERE / name}")
