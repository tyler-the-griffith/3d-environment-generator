"""A deliberately small PDF writer: pages with text (Helvetica) and JPEG images.

No third-party dependency so reports build on any machine that can run landviz. JPEGs are
embedded as-is (DCTDecode); callers convert other formats first (see report.py).
"""
from __future__ import annotations

import zlib
from pathlib import Path

PAGE_LANDSCAPE = (792.0, 612.0)  # US Letter, points


def _esc(text: str) -> str:
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    raise ValueError("not a JPEG")


# Average Helvetica glyph width (1/1000 em) — good enough for wrapping and centering.
def text_width(text: str, size: float, bold=False) -> float:
    return len(text) * size * (0.56 if bold else 0.52)


class Page:
    def __init__(self, doc: "Document"):
        self.doc = doc
        self.ops: list[str] = []
        self.images: dict[str, int] = {}

    def text(self, x, y, s, size=11, bold=False, color=(0.1, 0.12, 0.1)):
        font = "F2" if bold else "F1"
        r, g, b = color
        self.ops.append(f"BT {r} {g} {b} rg /{font} {size} Tf {x:.1f} {y:.1f} Td ({_esc(s)}) Tj ET")

    def wrapped(self, x, y, s, width, size=11, leading=None, bold=False, color=(0.1, 0.12, 0.1)) -> float:
        """Draw word-wrapped text; returns the y below the last line."""
        leading = leading or size * 1.35
        line = ""
        for word in s.split():
            trial = (line + " " + word).strip()
            if text_width(trial, size, bold) > width and line:
                self.text(x, y, line, size, bold, color)
                y -= leading
                line = word
            else:
                line = trial
        if line:
            self.text(x, y, line, size, bold, color)
            y -= leading
        return y

    def rect(self, x, y, w, h, fill=(0.95, 0.95, 0.93)):
        r, g, b = fill
        self.ops.append(f"{r} {g} {b} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")

    def line(self, x1, y1, x2, y2, width=0.5, color=(0.75, 0.75, 0.72)):
        r, g, b = color
        self.ops.append(f"{r} {g} {b} RG {width} w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def image(self, jpeg: Path, x, y, max_w, max_h) -> tuple[float, float]:
        """Place a JPEG fitted inside the box (x, y = bottom-left). Returns drawn (w, h)."""
        name, (iw, ih) = self.doc.add_image(jpeg)
        scale = min(max_w / iw, max_h / ih)
        w, h = iw * scale, ih * scale
        ox, oy = x + (max_w - w) / 2, y + (max_h - h) / 2
        self.images[name] = 1
        self.ops.append(f"q {w:.1f} 0 0 {h:.1f} {ox:.1f} {oy:.1f} cm /{name} Do Q")
        return w, h


class Document:
    def __init__(self, size=PAGE_LANDSCAPE, title="Report"):
        self.size = size
        self.title = title
        self.pages: list[Page] = []
        self._images: dict[str, tuple[str, bytes, int, int]] = {}

    def page(self) -> Page:
        p = Page(self)
        self.pages.append(p)
        return p

    def add_image(self, jpeg: Path):
        key = str(jpeg)
        if key not in self._images:
            data = Path(jpeg).read_bytes()
            w, h = jpeg_size(data)
            self._images[key] = (f"Im{len(self._images) + 1}", data, w, h)
        name, _d, w, h = self._images[key]
        return name, (w, h)

    def save(self, path: Path):
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        catalog = add(b"")  # placeholder, filled later
        pages_id = add(b"")
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        img_ids = {}
        for _key, (name, data, w, h) in self._images.items():
            img_ids[name] = add(
                f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
                f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
                + data + b"\nendstream")
        page_ids = []
        W, H = self.size
        for p in self.pages:
            content = zlib.compress("\n".join(p.ops).encode("latin-1"))
            cid = add(f"<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode() + content
                      + b"\nendstream")
            xo = " ".join(f"/{n} {img_ids[n]} 0 R" for n in p.images)
            page_ids.append(add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {W} {H}] /Contents {cid} 0 R "
                f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> /XObject << {xo} >> >> >>".encode()))
        objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
        kids = " ".join(f"{i} 0 R" for i in page_ids)
        objs[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
        info = add(f"<< /Title ({_esc(self.title)}) /Producer (landviz) >>".encode())

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        Path(path).write_bytes(bytes(out))
