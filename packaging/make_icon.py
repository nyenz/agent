"""Draw FanOut's icon (navy tile, one tangerine point fanning out to three) and save every size the
installers need. Run from the repo root:  python packaging/make_icon.py   (needs Pillow; not a runtime dependency)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent / "icons"
NAVY_TOP, NAVY_BOTTOM = (0x1A, 0x3F, 0x80), (0x0B, 0x22, 0x50)
TANGERINE, CREAM = (0xF2, 0x6A, 0x0D), (0xFF, 0xFF, 0xFC)
S = 1024  # drawn large, then scaled down for crisp edges


def tile() -> Image.Image:
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    for y in range(S):
        t = y / (S - 1)
        row = tuple(round(a + (b - a) * t) for a, b in zip(NAVY_TOP, NAVY_BOTTOM, strict=True))
        for x in range(S):
            px[x, y] = row
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    # faint light edge just inside the tile, brightest at the top (the "glass edge")
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle((52, 52, S - 52, S - 52), radius=198, outline=(255, 255, 255, 46), width=6)
    fade = Image.linear_gradient("L").resize((S, S)).point(lambda v: 255 - v)
    edge.putalpha(Image.composite(edge.getchannel("A"), Image.new("L", (S, S), 0), fade))
    return Image.alpha_composite(img, edge)


def mark(img: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    src = (300, 512)
    ends = [(724, 300), (724, 512), (724, 724)]
    for end in ends:
        d.line([src, end], fill=(*CREAM, 235), width=46)
    for x, y in ends:
        d.ellipse((x - 62, y - 62, x + 62, y + 62), fill=CREAM)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((src[0] - 130, src[1] - 130, src[0] + 130, src[1] + 130), fill=(*TANGERINE, 120))
    layer = Image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)), layer)
    ImageDraw.Draw(layer).ellipse((src[0] - 96, src[1] - 96, src[0] + 96, src[1] + 96), fill=TANGERINE)
    return Image.alpha_composite(img, layer)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    art = mark(tile())
    art.resize((512, 512), Image.LANCZOS).save(OUT / "fanout.png")
    art.resize((256, 256), Image.LANCZOS).save(OUT / "fanout-256.png")
    art.save(OUT / "fanout.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    art.save(OUT / "fanout.icns")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
