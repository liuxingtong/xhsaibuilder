"""
eyes_render.py — 把 eyes_core 的 DrawList 翻译成 Tkinter 画布 / Pillow 图像

这个文件是「唯一」知道怎么把归一化坐标变成具体像素的地方。
移植到 Arduino 时，你只需要照着 TkRenderer 里那三个分支写 GFX 调用即可：

    Ellipse  -> display.fillEllipse()  / fillCircle()
    Polygon  -> display.fillTriangle() 拼，或 Adafruit GFX 的 fillPolygon
    spark    -> 一个小菱形/十字，或者干脆用 fillCircle 缩小版

坐标系：归一化 -1..1，原点在屏幕中心，y 向下，半径 1 = 屏幕半径。
"""

from __future__ import annotations

import math
from typing import Iterable, Tuple

from eyes_core import Arc, DrawList, Ellipse, Polygon

# ---------------------------------------------------------------------------
# 配色（后面 Arduino 版直接抄这组 RGB565）
# ---------------------------------------------------------------------------

BLACK = (0, 0, 0)
WHITE = (245, 248, 255)
EYE_WHITE = (238, 242, 250)
PUPIL = (26, 32, 48)
GLINT = (255, 255, 255)
LID = (12, 16, 28)          # 眼皮：比黑屏略亮，能看出盖住的形状
SPARK = (255, 226, 120)
SPARK2 = (140, 220, 255)

# 屏幕背景（深色，模拟圆屏关机时的黑底带一点蓝）
BG_TOP = (14, 18, 30)
BG_BOT = (6, 8, 14)


def hex_color(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def to_px(v: float, center: float, radius: float) -> float:
    """归一化 -> 像素。"""
    return center + v * radius


# ---------------------------------------------------------------------------
# Tkinter 渲染
# ---------------------------------------------------------------------------


class TkRenderer:
    def __init__(self, canvas, size: int):
        self.canvas = canvas
        self.size = size
        self.c = size / 2.0
        self.r = size / 2.0
        self._bg_drawn = False

    def px(self, v: float) -> float:
        return to_px(v, self.c, self.r)

    def draw_background(self) -> None:
        if self._bg_drawn:
            return
        n = 14
        for i in range(n):
            t = i / (n - 1)
            col = tuple(int(BG_TOP[k] + (BG_BOT[k] - BG_TOP[k]) * t) for k in range(3))
            x0 = 0
            y0 = self.size * i / n
            y1 = self.size * (i + 1) / n + 1
            self.canvas.create_rectangle(x0, y0, self.size, y1,
                                         fill=hex_color(col), outline="")
        # 圆外遮罩：四角涂成同色（如果窗口比圆大，这里能看出是圆屏）
        self._bg_drawn = True

    def render(self, dl: DrawList) -> None:
        # 每帧全清，240×240 下 Tkinter 撑得住
        self.canvas.delete("eye")
        self.draw_background()

        for item in dl.items:
            if isinstance(item, Ellipse):
                self._ellipse(item, WHITE, "eye")
            elif isinstance(item, Polygon):
                self._polygon(item, PUPIL, "eye")
            elif isinstance(item, tuple):
                self._special(item)

    # --- 图元 -------------------------------------------------------------

    def _ellipse(self, e: Ellipse, color, tag) -> None:
        cx, cy = self.px(e.cx), self.px(e.cy)
        rx, ry = e.rx * self.r, e.ry * self.r
        self.canvas.create_oval(cx - rx, cy - ry, cx + rx, cy + ry,
                                fill=hex_color(color), outline="", tags=tag)

    def _polygon(self, p: Polygon, color, tag) -> None:
        flat = []
        for x, y in p.points:
            flat.extend((self.px(x), self.px(y)))
        self.canvas.create_polygon(flat, fill=hex_color(color), outline="", tags=tag)

    def _special(self, item: tuple) -> None:
        kind = item[0]
        tag = "eye"

        if kind == "pupil":
            _, cx, cy, rx, ry = item
            self.canvas.create_oval(
                self.px(cx) - rx * self.r, self.px(cy) - ry * self.r,
                self.px(cx) + rx * self.r, self.px(cy) + ry * self.r,
                fill=hex_color(PUPIL), outline="", tags=tag)
            # 瞳孔里的小反光，让它不死板
            gr = max(0.5, rx * self.r * 0.22)
            gx = self.px(cx) + rx * self.r * 0.30
            gy = self.px(cy) + ry * self.r * 0.32
            self.canvas.create_oval(gx - gr, gy - gr, gx + gr, gy + gr,
                                    fill=hex_color((255, 255, 255)), outline="",
                                    tags=tag)

        elif kind == "glint":
            _, cx, cy, rx, ry = item
            gr = max(0.6, min(rx, ry) * self.r * 0.9)
            self.canvas.create_oval(
                self.px(cx) - gr, self.px(cy) - gr,
                self.px(cx) + gr, self.px(cy) + gr,
                fill=hex_color(GLINT), outline="", tags=tag)

        elif kind == "lid":
            _, pts = item
            flat = []
            for x, y in pts:
                flat.extend((self.px(x), self.px(y)))
            self.canvas.create_polygon(flat, fill=hex_color(LID), outline="", tags=tag)

        elif kind == "spark":
            _, cx, cy, size, ang, phase = item
            col = SPARK if (phase % 0.5) < 0.25 else SPARK2
            x, y = self.px(cx), self.px(cy)
            s = size * self.r
            # 四角星
            self.canvas.create_polygon(
                x, y - s * 2.2, x + s * 0.7, y - s * 0.2,
                x + s * 2.2, y, x + s * 0.7, y + s * 0.2,
                x, y + s * 2.2, x - s * 0.7, y + s * 0.2,
                x - s * 2.2, y, x - s * 0.7, y - s * 0.2,
                fill=hex_color(col), outline="", tags=tag)


# ---------------------------------------------------------------------------
# Pillow 渲染（给无头预览 / 导出 GIF 用）
# ---------------------------------------------------------------------------


class PillowRenderer:
    def __init__(self, size: int = 240, super_sample: int = 3):
        from PIL import Image, ImageDraw

        self.size = size
        self.ss = super_sample
        self.big = size * super_sample
        self.c = self.big / 2.0
        self.r = self.big / 2.0
        self._Image = Image
        self._ImageDraw = ImageDraw

    def px(self, v: float) -> float:
        return to_px(v, self.c, self.r)

    def render(self, dl: DrawList, bg_alpha: int = 255):
        Image, ImageDraw = self._Image, self._ImageDraw
        img = Image.new("RGB", (self.big, self.big), BG_BOT)
        d = ImageDraw.Draw(img)

        # 背景渐变
        n = 24
        for i in range(n):
            t = i / (n - 1)
            col = tuple(int(BG_TOP[k] + (BG_BOT[k] - BG_TOP[k]) * t) for k in range(3))
            d.rectangle([0, self.big * i / n, self.big, self.big * (i + 1) / n + 1], fill=col)

        for item in dl.items:
            if isinstance(item, Ellipse):
                self._ellipse(d, item, WHITE)
            elif isinstance(item, Polygon):
                self._ellipse_poly(d, item, PUPIL)
            elif isinstance(item, tuple):
                self._special(d, item)

        img = img.resize((self.size, self.size), Image.LANCZOS)
        return _circle_mask(img, bg_alpha)

    # --- 图元 -------------------------------------------------------------

    def _ellipse(self, d, e: Ellipse, color) -> None:
        cx, cy = self.px(e.cx), self.px(e.cy)
        rx, ry = e.rx * self.r, e.ry * self.r
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)

    def _ellipse_poly(self, d, p: Polygon, color) -> None:
        d.polygon([(self.px(x), self.px(y)) for x, y in p.points], fill=color)

    def _special(self, d, item: tuple) -> None:
        kind = item[0]
        if kind == "pupil":
            _, cx, cy, rx, ry = item
            d.ellipse([self.px(cx) - rx * self.r, self.px(cy) - ry * self.r,
                       self.px(cx) + rx * self.r, self.px(cy) + ry * self.r],
                      fill=PUPIL)
            gr = max(1.0, rx * self.r * 0.22)
            gx = self.px(cx) + rx * self.r * 0.30
            gy = self.px(cy) + ry * self.r * 0.32
            d.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=(255, 255, 255))
        elif kind == "glint":
            _, cx, cy, rx, ry = item
            gr = max(1.0, min(rx, ry) * self.r * 0.9)
            d.ellipse([self.px(cx) - gr, self.px(cy) - gr,
                       self.px(cx) + gr, self.px(cy) + gr], fill=GLINT)
        elif kind == "lid":
            _, pts = item
            d.polygon([(self.px(x), self.px(y)) for x, y in pts], fill=LID)
        elif kind == "spark":
            _, cx, cy, size, ang, phase = item
            col = SPARK if (phase % 0.5) < 0.25 else SPARK2
            x, y = self.px(cx), self.px(cy)
            s = size * self.r
            d.polygon([(x, y - s * 2.2), (x + s * 0.7, y - s * 0.2),
                       (x + s * 2.2, y), (x + s * 0.7, y + s * 0.2),
                       (x, y + s * 2.2), (x - s * 0.7, y + s * 0.2),
                       (x - s * 2.2, y), (x - s * 0.7, y - s * 0.2)], fill=col)


def _circle_mask(img, bg_alpha: int = 255):
    """把方形画面裁成圆形（圆屏模拟），圆外补成透明或深灰。"""
    from PIL import Image, ImageDraw

    w, h = img.size
    mask = Image.new("L", (w * 4, h * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, w * 4 - 1, h * 4 - 1], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)

    if bg_alpha >= 255:
        out = Image.new("RGB", (w, h), (0, 0, 0))
        out.paste(img, (0, 0), mask)
        return out

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out
