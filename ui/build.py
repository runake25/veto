#!/usr/bin/env python3
"""Inline CSS, JS and the IBM Plex woff2 files into a single offline index.html."""
from __future__ import annotations

import base64
from pathlib import Path

BUILD = Path(__file__).resolve().parent
SRC = BUILD / "src"
ROOT = BUILD.parent
ASSETS = BUILD / "assets"

FACES = [
    ("ibm-plex-sans", "IBM Plex Sans", 400, "sans400.woff2"),
    ("ibm-plex-sans", "IBM Plex Sans", 500, "sans500.woff2"),
    ("ibm-plex-sans", "IBM Plex Sans", 600, "sans600.woff2"),
    ("ibm-plex-mono", "IBM Plex Mono", 400, "mono400.woff2"),
]


def font_css() -> str:
    out = []
    for _slug, family, weight, filename in FACES:
        path = ASSETS / filename
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        out.append(
            "@font-face{font-family:\"%s\";font-style:normal;font-weight:%d;"
            "font-display:block;src:url(data:font/woff2;base64,%s) format(\"woff2\")}"
            % (family, weight, b64)
        )
    return "\n".join(out)


def main() -> None:
    html = (SRC / "index.html").read_text(encoding="utf-8")
    css = (SRC / "app.css").read_text(encoding="utf-8")
    js = "\n".join(
        (SRC / name).read_text(encoding="utf-8") for name in ("data.js", "app.js")
    )

    for token in ("</script", "</style"):
        if token in js.lower() or token in css.lower():
            raise SystemExit(f"refusing to inline: {token} found in an asset")

    html = html.replace("/*FONTS*/", font_css())
    html = html.replace("/*CSS*/", css)
    html = html.replace("/*JS*/", js)

    out = ROOT / "index.html"
    out.write_text(html, encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"wrote {out} ({kb:.1f} KB)")


if __name__ == "__main__":
    main()
