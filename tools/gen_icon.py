"""生成应用图标（PNG + ICO）。"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

SIZE = 1024


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(size: int):
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    start = (47, 122, 95)
    end = (217, 154, 78)
    for y in range(size):
        draw.line([(0, y), (size, y)], fill=lerp(start, end, y / (size - 1)))
    return image


def main() -> None:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    body = gradient(SIZE).convert("RGBA")
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [56, 56, SIZE - 56, SIZE - 56], radius=220, fill=255
    )
    canvas.paste(body, (0, 0), mask)

    draw = ImageDraw.Draw(canvas)
    # 白色日历卡片
    draw.rounded_rectangle(
        [238, 246, 786, 794], radius=86, fill=(255, 255, 255, 246)
    )
    # 卡片顶部色带
    band = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    band_draw = ImageDraw.Draw(band)
    band_draw.rounded_rectangle(
        [238, 246, 786, 424], radius=86, fill=(37, 95, 73, 255)
    )
    band_draw.rectangle([238, 340, 786, 424], fill=(37, 95, 73, 255))
    canvas = Image.alpha_composite(canvas, band)
    draw = ImageDraw.Draw(canvas)
    # 顶部小圆点装饰
    for x in (330, 438, 546, 654):
        draw.ellipse([x, 300, x + 24, 324], fill=(255, 255, 255, 170))
    # 日历格子
    colors = [
        (124, 107, 246), (63, 211, 196), (245, 182, 92),
        (242, 119, 154), (90, 169, 248), (143, 153, 178),
    ]
    index = 0
    for row in range(2):
        for col in range(3):
            x = 306 + col * 142
            y = 490 + row * 128
            draw.rounded_rectangle(
                [x, y, x + 92, y + 72], radius=22, fill=colors[index] + (235,)
            )
            index += 1
    # 右下角勾选
    draw.ellipse([640, 660, 744, 764], fill=(47, 122, 95, 255))
    draw.line([(670, 712), (690, 732), (720, 692)], fill=(255, 255, 255, 255), width=15)

    canvas = canvas.resize((512, 512), Image.LANCZOS)
    png_path = ASSETS / "lumina.png"
    canvas.save(png_path)
    ico_path = ASSETS / "lumina.ico"
    canvas.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                 (128, 128), (256, 256)])
    print("saved", png_path)
    print("saved", ico_path)


if __name__ == "__main__":
    main()
