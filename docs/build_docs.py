"""Build the self-contained report.html and slides.html (images embedded) into submission/.

Run:  python docs/build_docs.py
Screenshots come from docs/screenshots/*.png; on macOS they are shrunk to JPEG with `sips` to keep files small.
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
SRC, SHOTS = DOCS / "src", DOCS / "screenshots"
OUT = DOCS.parent / "submission"
TMP = Path(tempfile.mkdtemp())


def data_uri(name: str) -> str:
    png = SHOTS / f"{name}.png"
    if shutil.which("sips"):
        jpg = TMP / f"{name}.jpg"
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "72", "-Z", "1500", str(png), "--out", str(jpg)],
                       check=True, capture_output=True)
        return "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()
    return "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()


def build(name: str) -> None:
    html = (SRC / name).read_text(encoding="utf-8")
    svg = (SRC / "diagram.svg").read_text(encoding="utf-8")
    html = html.replace("{{svg:diagram}}", svg)
    html = re.sub(r"\{\{img:([\w-]+)\}\}", lambda m: data_uri(m.group(1)), html)
    assert "{{" not in html, f"unfilled placeholder in {name}"
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(html, encoding="utf-8")
    print(f"wrote submission/{name} ({len(html) / 1e6:.1f} MB)")


build("report.html")
build("slides.html")
