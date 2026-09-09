"""Generate assets/mimir.ico and assets/mimir.png (run once; needs Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets"
BG = (24, 24, 37)
BARS = [(137, 180, 250), (203, 166, 247), (148, 226, 213)]
PEAK = (249, 226, 175)


def render(size: int) -> Image.Image:
    s = size * 8                                     # draw large, downsample for smooth edges
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.22, fill=BG)
    pad = s * 0.18
    gap = s * 0.06
    n = len(BARS)
    w = (s - 2 * pad - gap * (n - 1)) / n
    heights = [0.38, 0.62, 0.86]
    for i, (color, h) in enumerate(zip(BARS, heights)):
        x0 = pad + i * (w + gap)
        y0 = s - pad - (s - 2 * pad) * h
        d.rounded_rectangle((x0, y0, x0 + w, s - pad), radius=w * 0.25, fill=color)
    # peak marker above the tallest bar
    cx = pad + 2 * (w + gap) + w / 2
    cy = s - pad - (s - 2 * pad) * 0.86 - s * 0.075
    r = s * 0.045
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=PEAK)
    return im.resize((size, size), Image.LANCZOS)


OUT.mkdir(exist_ok=True)
sizes = [16, 24, 32, 48, 64, 128, 256]
images = [render(sz) for sz in sizes]
images[-1].save(OUT / "mimir.png")
images[-1].save(OUT / "mimir.ico", format="ICO", sizes=[(sz, sz) for sz in sizes],
               append_images=images[:-1])
print("wrote", OUT / "mimir.ico", OUT / "mimir.png")
