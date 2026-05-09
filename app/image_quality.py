from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError


def analyze_image(path: Path) -> dict:
    """Return lightweight quality metrics without requiring OpenCV."""
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            gray = img.convert("L")
            thumb = gray.resize((min(320, width), max(1, int(min(320, width) * height / max(width, 1)))))
            edges = thumb.filter(ImageFilter.FIND_EDGES)
            edge_var = ImageStat.Stat(edges).var[0]
            brightness = ImageStat.Stat(thumb).mean[0]
            overexposed = sum(1 for px in thumb.getdata() if px > 245) / max(1, thumb.width * thumb.height)

            min_edge = min(width, height)
            resolution_score = min(1.0, min_edge / 900)
            blur_score = float(edge_var)
            blur_component = min(1.0, blur_score / 900)
            glare_component = max(0.0, 1.0 - overexposed * 2)
            brightness_component = 1.0 - min(1.0, abs(brightness - 170) / 170)
            quality = round((resolution_score * 0.35) + (blur_component * 0.35) + (glare_component * 0.2) + (brightness_component * 0.1), 4)

            return {
                "ok": True,
                "width_px": width,
                "height_px": height,
                "quality_score": quality,
                "blur_score": round(blur_score, 4),
                "glare_score": round(overexposed, 4),
                "boundary_confidence": None,
                "warnings": quality_warnings(width, height, quality, overexposed),
            }
    except (UnidentifiedImageError, OSError):
        return {
            "ok": False,
            "width_px": None,
            "height_px": None,
            "quality_score": None,
            "blur_score": None,
            "glare_score": None,
            "boundary_confidence": None,
            "warnings": ["File is not a readable image. It may still be valid for a document OCR provider."],
        }


def quality_warnings(width: int, height: int, quality: float, glare_score: float) -> list[str]:
    warnings: list[str] = []
    if min(width, height) < 900:
        warnings.append("Image resolution is below the preferred 900px short-edge threshold.")
    if quality < 0.45:
        warnings.append("Image quality is low; extraction may require review or a rescan.")
    elif quality < 0.65:
        warnings.append("Image quality is marginal; extraction will continue with lower confidence.")
    if glare_score > 0.2:
        warnings.append("Possible glare or overexposure detected.")
    return warnings
