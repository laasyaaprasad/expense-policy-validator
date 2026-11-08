# Diagram sources

`architecture.html` and `aws-deployment.html` (with `style.css`) draw `docs/architecture-clean.png` and
`docs/aws-deployment-clean.png`. After editing one, render it again at 3x with headless Chrome:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --force-device-scale-factor=3 --window-size=680,1200 --screenshot=docs/architecture-clean.png "file://$PWD/docs/diagrams/architecture.html"
```

Use `--window-size=680,900` and the AWS file names for the AWS diagram. The Mermaid versions live in `README.md` and
`docs/aws-design.md`; keep both in step.
