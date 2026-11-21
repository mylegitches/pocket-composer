"""Turn an input (PDF, photo, or folder of photos) into one 300-dpi PNG per page."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
PDF_EXTS = {".pdf"}
DPI = 300
# Photos are scaled so the long side is about a letter page at 300 dpi.
PHOTO_LONG_SIDE = 3300


def is_input(path: Path) -> bool:
    if path.is_dir():
        return any(is_input(p) for p in path.iterdir() if p.is_file())
    return path.suffix.lower() in IMAGE_EXTS | PDF_EXTS


def _open_photo(path: Path) -> Image.Image:
    if path.suffix.lower() in {".heic", ".heif"}:
        from pillow_heif import register_heif_opener  # optional dependency

        register_heif_opener()
    im = ImageOps.exif_transpose(Image.open(path))
    im = im.convert("L")
    scale = PHOTO_LONG_SIDE / max(im.size)
    if scale < 0.9 or scale > 1.1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return im


def render(source: Path, out_dir: Path) -> list[Path]:
    """Write pages as out_dir/p001.png ... and return their paths (cached)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("p[0-9][0-9][0-9].png"))
    if existing:
        return existing

    files = sorted(p for p in source.iterdir() if p.is_file()) if source.is_dir() else [source]
    pages: list[Path] = []
    for f in files:
        ext = f.suffix.lower()
        if ext in PDF_EXTS:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(f))
            for i in range(len(pdf)):
                im = pdf[i].render(scale=DPI / 72, grayscale=True).to_pil().convert("L")
                pages.append(_save(im, out_dir, len(pages) + 1))
        elif ext in IMAGE_EXTS:
            pages.append(_save(_open_photo(f), out_dir, len(pages) + 1))
    return pages


def _save(im: Image.Image, out_dir: Path, n: int) -> Path:
    path = out_dir / f"p{n:03d}.png"
    im.save(path, optimize=False)
    return path
