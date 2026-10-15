"""Generate the FileForge multi-size Windows icon."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "fileforge.ico"


def make_layer(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = int(18 * scale)
    radius = int(56 * scale)
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=(57, 119, 246, 255),
    )

    # White document with a folded corner.
    left, top = int(70 * scale), int(53 * scale)
    right, bottom = int(184 * scale), int(204 * scale)
    fold = int(35 * scale)
    points = [
        (left, top),
        (right - fold, top),
        (right, top + fold),
        (right, bottom),
        (left, bottom),
    ]
    draw.polygon(points, fill=(255, 255, 255, 255))
    draw.polygon(
        [(right - fold, top), (right - fold, top + fold), (right, top + fold)],
        fill=(194, 215, 255, 255),
    )

    line_width = max(1, int(10 * scale))
    for y, width in ((118, 78), (145, 78), (172, 52)):
        y_px = int(y * scale)
        draw.rounded_rectangle(
            (int(88 * scale), y_px, int((88 + width) * scale), y_px + line_width),
            radius=max(1, line_width // 2),
            fill=(57, 119, 246, 255),
        )
    return image


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    layers = [make_layer(size) for size in (16, 24, 32, 48, 64, 128, 256)]
    layers[-1].save(OUTPUT, format="ICO", sizes=[(s.width, s.height) for s in layers])
    print(f"Generated {OUTPUT}")


if __name__ == "__main__":
    main()
