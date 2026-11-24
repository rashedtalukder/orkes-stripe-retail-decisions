# Diagrams

- `architecture.svg` / `architecture.png` (2x, 2880×1600): the blog's architecture diagram.
- Edit `build_architecture_diagram.py`, then regenerate:

```bash
python3 docs/diagrams/build_architecture_diagram.py
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --user-data-dir="$(mktemp -d)" --force-device-scale-factor=2 --window-size=1440,800 \
  --screenshot=docs/diagrams/architecture.png "file://$PWD/docs/diagrams/architecture.svg"
```

Any SVG renderer works; Chrome is used here only because it's commonly installed.
