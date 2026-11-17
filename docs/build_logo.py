"""Draw the Agen logo used by the getting-started PDF."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
MARK = HERE / "agen-mark.png"
LOCKUP = HERE / "agen-logo.png"
SVG = HERE / "agen-logo.svg"

# The file-icon A, scaled from the 16px editor mark.
OUTER = [(8, 2.15), (13.15, 13.2), (11.1, 13.2), (10.1, 10.4), (5.9, 10.4), (4.9, 13.2), (2.85, 13.2)]
HOLE = [(8, 5.7), (6.55, 8.9), (9.45, 8.9)]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(rf"C:\Windows\Fonts\{name}", size)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)


def _mark(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = int(size * 0.06)
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (pad, pad + int(size * 0.03), size - pad, size - pad + int(size * 0.03)),
        radius=int(size * 0.22),
        fill=(22, 16, 70, 90),
    )
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(int(size * 0.03))))

    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    top = (158, 140, 255)
    bottom = (92, 68, 224)
    pixels = tile.load()
    for y in range(size):
        color = _mix(top, bottom, y / (size - 1))
        for x in range(size):
            pixels[x, y] = color
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=int(size * 0.22),
        fill=255,
    )
    tile.putalpha(mask)
    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle(
        (pad + int(size * 0.08), pad + int(size * 0.06), size - pad - int(size * 0.08), int(size * 0.42)),
        radius=int(size * 0.16),
        fill=(255, 255, 255, 36),
    )
    tile.alpha_composite(gloss)
    image.alpha_composite(tile)

    scale = size / 16 * 0.72
    ox = (size - 16 * scale) / 2
    oy = (size - 16 * scale) / 2 + size * 0.01

    def points(shape: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(ox + x * scale, oy + y * scale) for x, y in shape]

    letter = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(letter)
    draw.polygon(points(OUTER), fill=255)
    draw.polygon(points(HOLE), fill=0)
    white = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    white.putalpha(letter)
    image.alpha_composite(white)
    return image


def _lockup() -> Image.Image:
    mark = _mark(512)
    font = _font(210, bold=True)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), "Agen", font=font)
    text_w = box[2] - box[0]
    text_h = box[3] - box[1]
    gap = 36
    canvas = Image.new("RGBA", (512 + gap + text_w + 8, 512), (0, 0, 0, 0))
    canvas.alpha_composite(mark, (0, 0))
    y = (512 - text_h) // 2 - box[1]
    ImageDraw.Draw(canvas).text((512 + gap - box[0], y), "Agen", font=font, fill=(255, 255, 255, 255))
    return canvas


def _svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="210" viewBox="0 0 640 210">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#9E8CFF"/>
      <stop offset="1" stop-color="#5C44E0"/>
    </linearGradient>
  </defs>
  <rect x="8" y="8" width="194" height="194" rx="44" fill="url(#tile)"/>
  <path fill="#fff" fill-rule="evenodd" d="M105 28.2 169.4 168.6h-26.2l-12.8-35.8H77.6L64.6 168.6H38.4L105 28.2Zm0 45.4-18.6 41h37.2l-18.6-41Z"/>
  <text x="230" y="132" fill="#162A4A" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="108" font-weight="700">Agen</text>
</svg>
"""


def main() -> None:
    mark = _mark()
    mark.save(MARK)
    _lockup().save(LOCKUP)
    SVG.write_text(_svg(), encoding="utf-8")
    print(MARK)
    print(LOCKUP)


if __name__ == "__main__":
    main()
