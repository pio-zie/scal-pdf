from dataclasses import dataclass
from pathlib import Path
import io
import math
import random

JPEG_QUALITY = 75
# Kąt jest skalibrowany na dłuższy bok A4. Na większym arkuszu ten sam
# stopień zjada więcej treści, więc obrót jest ścinany do 2 mm na dłuższym boku.
A4_LONG_MM = 297.0
MAX_EDGE_SHIFT_MM = 2.0
MM_PER_PT = 25.4 / 72.0


@dataclass(frozen=True)
class RasterSettings:
    dpi: int
    angle: float
    blur: float
    noise: float
    random_angle: bool
    shift_x_mm: float
    shift_y_mm: float
    random_shift: bool


DEFAULT_RASTER = RasterSettings(
    dpi=150,
    angle=0.3,
    blur=0.6,
    noise=4,
    random_angle=True,
    shift_x_mm=2.0,
    shift_y_mm=2.0,
    random_shift=True,
)


def _cap_rotation(angle_deg: float, width_pt: float, height_pt: float) -> float:
    """Na arkuszu dłuższym niż A4 zmniejsza kąt, żeby koniec dłuższego boku ruszył się o co najwyżej 2 mm."""
    if not angle_deg:
        return 0.0
    long_mm = max(width_pt, height_pt) * MM_PER_PT
    if long_mm <= A4_LONG_MM:
        return angle_deg
    sin_cap = min(1.0, (2.0 * MAX_EDGE_SHIFT_MM) / long_mm)
    max_deg = math.degrees(math.asin(sin_cap))
    magnitude = min(abs(angle_deg), max_deg)
    return math.copysign(magnitude, angle_deg)


def _page_rotation(angle_deg: float, random_angle: bool, width_pt: float, height_pt: float) -> float:
    if random_angle:
        limit = abs(_cap_rotation(abs(angle_deg), width_pt, height_pt))
        if not limit:
            return 0.0
        return random.uniform(-limit, limit)
    return _cap_rotation(angle_deg, width_pt, height_pt)


def _page_shift_mm(mm: float, random_shift: bool) -> float:
    if random_shift:
        amplitude = abs(mm)
        if not amplitude:
            return 0.0
        return random.uniform(-amplitude, amplitude)
    return mm


def _shift_image(img, dx_px: float, dy_px: float):
    from PIL import Image

    if not dx_px and not dy_px:
        return img
    shifted = Image.new("RGB", img.size, (255, 255, 255))
    shifted.paste(img, (int(round(dx_px)), int(round(dy_px))))
    return shifted


def _degrade(img, rotation: float, blur: float, noise: float, shift_x_px: float = 0.0, shift_y_px: float = 0.0) -> bytes:
    import numpy as np
    from PIL import Image, ImageFilter

    img = img.convert("RGB")

    if rotation:
        img = img.rotate(
            rotation,
            resample=Image.BICUBIC,
            expand=False,
            fillcolor=(255, 255, 255),
        )

    img = _shift_image(img, shift_x_px, shift_y_px)

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    if noise:
        arr = np.asarray(img).astype(np.int16)
        noise_arr = np.random.normal(0, noise, arr.shape).astype(np.int16)
        arr = np.clip(arr + noise_arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _rasterize(
    src_path: Path,
    out_path: Path,
    settings: RasterSettings,
    progress=None,
) -> None:
    import pymupdf
    from PIL import Image

    src = pymupdf.open(src_path)
    try:
        out = pymupdf.open()
        try:
            total = src.page_count
            for index, page in enumerate(src, start=1):
                if progress is not None:
                    progress(index, total)
                pix = page.get_pixmap(dpi=settings.dpi, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                rotation = _page_rotation(
                    settings.angle,
                    settings.random_angle,
                    page.rect.width,
                    page.rect.height,
                )
                shift_x_px = _page_shift_mm(settings.shift_x_mm, settings.random_shift) * settings.dpi / 25.4
                shift_y_px = _page_shift_mm(settings.shift_y_mm, settings.random_shift) * settings.dpi / 25.4
                degraded = _degrade(img, rotation, settings.blur, settings.noise, shift_x_px, shift_y_px)
                new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(page.rect, stream=degraded)

            # Pusty słownik nic nie czyści; nowy dokument i tak dostaje producer PyMuPDF.
            out.set_metadata(
                {
                    "format": "",
                    "title": "",
                    "author": "",
                    "subject": "",
                    "keywords": "",
                    "creator": "",
                    "producer": "",
                    "creationDate": "",
                    "modDate": "",
                    "trapped": "",
                }
            )
            out.del_xml_metadata()
            out.save(out_path, garbage=4, deflate=True)
        finally:
            out.close()
    finally:
        src.close()
