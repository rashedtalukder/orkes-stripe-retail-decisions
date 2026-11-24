# Diagrams

Sized for the Orkes blog layout, measured on published orkes.io posts: the article column is **892 CSS px** wide
and in-body images fill it; hero images are **1.91:1** (shown at 892×467).

| File | Design size | Exported PNG (2x) | Use |
|---|---|---|---|
| `architecture.png` / `.svg` | 892 × 900 | 1784 × 1800 | In-body architecture diagram (text ≥ 12 px at column width) |
| `architecture-hero.png` / `.svg` | 900 × 471 | 1800 × 942 | Post hero and social preview (1.91:1) |

Suggested caption for the body diagram: *The model labels intent; Stripe supplies verified facts; the pinned
Business Rule decides; only APPROVE_REFUND can reach the guarded refund task. Dashed = optional pattern, not benchmarked.*

Regenerate after editing `build_architecture_diagram.py`:

```bash
python3 docs/diagrams/build_architecture_diagram.py
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"   # any SVG renderer works
"$CH" --headless=new --hide-scrollbars --user-data-dir="$(mktemp -d)" --force-device-scale-factor=2 \
  --window-size=892,900 --screenshot=docs/diagrams/architecture.png "file://$PWD/docs/diagrams/architecture.svg"
"$CH" --headless=new --hide-scrollbars --user-data-dir="$(mktemp -d)" --force-device-scale-factor=2 \
  --window-size=900,471 --screenshot=docs/diagrams/architecture-hero.png "file://$PWD/docs/diagrams/architecture-hero.svg"
```
