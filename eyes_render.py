"""
eyes_render.py — 把 eyes_core 的 DrawList 翻译成 Tkinter 画布 / Pillow 图像

这个文件是「唯一」知道怎么把归一化坐标变成具体像素的地方。
移植到 Arduino 时，照着 TkRenderer 里那几个分支写 GFX 调用即可：

    Ellipse            -> display.fillEllipse()  / fillCircle()
    Polygon            -> fillTriangle() 拼，或 GFX 的 fillPolygon
    ("pupil", ...)     -> 黑圆 + 白色小圆点
    ("crescent", ...)  -> 下半圆的弧线，drawArc(..., 20, 160)
    ("arc", ...)       -> drawArc，画眼睛上方的皮褶
    ("smile_arc", ...) -> 弯眼笑：一道下弧
    ("dot", ...)       -> fillRect，轮播时的点阵名字

颜色约定：图元自带的 `mood` 字段是 **24bit 整数**（0xRRGGBB）。
Tkinter 要字符串 "#rrggbb"，Pillow 要 (r, g, b)，都在这里转，核心层不管。

坐标系：归一化 -1..1，原点在屏幕中心，y 向下，半径 1 = 屏幕半径。
"""

from __future__ import annotations

import math
from typing import Tuple

from eyes_core import BG_BOT, BG_TOP, Arc, DrawList, Ellipse, Polygon

# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------
#
# 注意：**眼睛的颜色已经不在这个文件里了**。
# 每套 EyeDesign 自带全套颜色，由 eyes_core 决定，这里只负责画。

#: 兜底色：图元没带颜色时用它（一般是黑，保证不出现意外亮色）
BLACK = (0, 0, 0)


def hex_color(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def unpack(v: int) -> Tuple[int, int, int]:
    """24bit 整数 → (r, g, b)。核心层用整数存颜色，这里解包。"""
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def to_px(v: float, center: float, radius: float) -> float:
    """归一化 -> 像素。"""
    return center + v * radius


def _gradient(n: int):
    """背景竖向渐变。所有渲染器共用，保证 PC / 导出图观感一致。"""
    out = []
    for i in range(n):
        t = i / (n - 1)
        out.append(tuple(int(BG_TOP[k] + (BG_BOT[k] - BG_TOP[k]) * t) for k in range(3)))
    return out


# ---------------------------------------------------------------------------
# Tkinter 渲染
# ---------------------------------------------------------------------------


class TkRenderer:
    def __init__(self, canvas, size: int, bg_tag: str = "bg"):
        self.canvas = canvas
        self.size = size
        self.c = size / 2.0
        self.r = size / 2.0
        self.bg_tag = bg_tag
        self._bg_drawn = False

    def px(self, v: float) -> float:
        return to_px(v, self.c, self.r)

    def draw_background(self) -> None:
        """
        背景只画一次，之后每帧只删除眼睛图元。

        这点很重要：之前每帧全清再重画 14 条渐变矩形，
        240×240 在 Tkinter 上会明显掉帧。
        """
        if self._bg_drawn:
            return
        n = 14
        for i, col in enumerate(_gradient(n)):
            y0 = self.size * i / n
            y1 = self.size * (i + 1) / n + 1
            self.canvas.create_rectangle(0, y0, self.size, y1,
                                         fill=hex_color(col), outline="",
                                         tags=self.bg_tag)
        self._bg_drawn = True

    def render(self, dl: DrawList) -> None:
        self.canvas.delete("eye")
        self.draw_background()
        for item in dl.items:
            self._item(item)

    # --- 图元 -------------------------------------------------------------

    def _item(self, item) -> None:
        if isinstance(item, Ellipse):
            self._ellipse(item)
        elif isinstance(item, Polygon):
            self._polygon(item)
        elif isinstance(item, tuple):
            self._special(item)

    def _ellipse(self, e: Ellipse) -> None:
        cx, cy = self.px(e.cx), self.px(e.cy)
        rx, ry = e.rx * self.r, e.ry * self.r
        self.canvas.create_oval(cx - rx, cy - ry, cx + rx, cy + ry,
                                fill=hex_color(unpack(e.mood)),
                                outline="", tags="eye")

    def _polygon(self, p: Polygon) -> None:
        flat = []
        for x, y in p.points:
            flat.extend((self.px(x), self.px(y)))
        self.canvas.create_polygon(flat, fill=hex_color(unpack(p.mood)),
                                   outline="", tags="eye")

    def _arc_pts(self, cx, cy, rx, ry, a0, a1, n=24):
        pts = []
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
        return pts

    def _special(self, item: tuple) -> None:
        kind = item[0]

        if kind == "pupil":
            _, cx, cy, rx, ry = item
            # 瞳孔用固定的深色 + 一圈更深的边，让它有"洞"的感觉
            self.canvas.create_oval(
                self.px(cx) - rx * self.r, self.px(cy) - ry * self.r,
                self.px(cx) + rx * self.r, self.px(cy) + ry * self.r,
                fill=hex_color((13, 11, 17)), outline="", tags="eye")

        elif kind == "crescent":
            _, cx, cy, rx, ry, col, a0, a1 = item
            pts = self._arc_pts(cx, cy, rx, ry, a0, a1, 18)
            flat = []
            for x, y in pts:
                flat.extend((self.px(x), self.px(y)))
            self.canvas.create_line(flat, fill=hex_color(unpack(col)),
                                    width=max(1, int(self.r * 0.013)),
                                    capstyle="round", tags="eye")

        elif kind == "arc":
            _, cx, cy, rx, ry, a0, a1, col, w = item
            pts = self._arc_pts(cx, cy, rx, ry, a0, a1, 20)
            flat = []
            for x, y in pts:
                flat.extend((self.px(x), self.px(y)))
            self.canvas.create_line(flat, fill=hex_color(unpack(col)),
                                    width=max(1, int(self.r * w)), tags="eye")

        elif kind == "smile_arc":
            _, cx, cy, rx, ry, amount, col = item
            # 向上弯的弧：中间低、两端高。只在瞳孔下半部，做"笑眼"
            n = 20
            flat = []
            for i in range(n + 1):
                t = i / n
                xn = -1.0 + 2.0 * t
                y = cy + ry * (0.15 + 0.50 * (1.0 - xn * xn)) * (0.55 + 0.45 * amount)
                flat.extend((self.px(cx + rx * xn), self.px(y)))
            self.canvas.create_line(flat, fill=hex_color(unpack(col)),
                                    width=max(2, int(self.r * 0.038 * min(1.0, 0.4 + amount))),
                                    capstyle="round", tags="eye")

        elif kind == "dot":
            _, cx, cy, r = item
            s = max(1.0, r * self.r)
            x, y = self.px(cx), self.px(cy)
            self.canvas.create_rectangle(x - s, y - s, x + s, y + s,
                                         fill="#e8eefc", outline="", tags="eye")

        elif kind in ("bond_heart", "bond_ring", "lid", "spark"):
            # 旧格式（背面兼容）：核心已经不再产生，留着免得老脚本炸
            self._legacy(item)

    def _legacy(self, item: tuple) -> None:
        kind = item[0]
        if kind in ("bond_heart", "lid"):
            _, pts = item
            flat = []
            for x, y in pts:
                flat.extend((self.px(x), self.px(y)))
            self.canvas.create_polygon(flat, fill="#ff9cb0" if kind == "bond_heart"
                                       else "#2c241e", outline="", tags="eye")
        elif kind == "bond_ring":
            _, pts, alpha = item
            flat = []
            for x, y in pts:
                flat.extend((self.px(x), self.px(y)))
            self.canvas.create_polygon(flat, fill="", outline="#ffe296",
                                       width=max(1, int(self.r * 0.012)), tags="eye")


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
        self._bg = None

    def px(self, v: float) -> float:
        return to_px(v, self.c, self.r)

    def _background(self):
        if self._bg is None:
            Image, ImageDraw = self._Image, self._ImageDraw
            img = Image.new("RGB", (self.big, self.big), BG_BOT)
            d = ImageDraw.Draw(img)
            n = 24
            for i, col in enumerate(_gradient(n)):
                d.rectangle([0, self.big * i / n, self.big,
                             self.big * (i + 1) / n + 1], fill=col)
            self._bg = img
        return self._bg.copy()

    def render(self, dl: DrawList, bg_alpha: int = 255):
        img = self._background()
        d = self._ImageDraw.Draw(img)
        for item in dl.items:
            self._item(d, item)
        img = img.resize((self.size, self.size), self._Image.LANCZOS)
        return _circle_mask(img, bg_alpha)

    # --- 图元 -------------------------------------------------------------

    def _item(self, d, item) -> None:
        if isinstance(item, Ellipse):
            self._ellipse(d, item)
        elif isinstance(item, Polygon):
            d.polygon([(self.px(x), self.px(y)) for x, y in item.points],
                      fill=unpack(item.mood))
        elif isinstance(item, tuple):
            self._special(d, item)

    def _ellipse(self, d, e: Ellipse) -> None:
        d.ellipse([self.px(e.cx) - e.rx * self.r, self.px(e.cy) - e.ry * self.r,
                   self.px(e.cx) + e.rx * self.r, self.px(e.cy) + e.ry * self.r],
                  fill=unpack(e.mood))

    def _pts(self, cx, cy, rx, ry, a0, a1, n=20):
        out = []
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            out.append((self.px(cx + rx * math.cos(a)),
                        self.px(cy + ry * math.sin(a))))
        return out

    def _special(self, d, item: tuple) -> None:
        kind = item[0]

        if kind == "pupil":
            _, cx, cy, rx, ry = item
            d.ellipse([self.px(cx) - rx * self.r, self.px(cy) - ry * self.r,
                       self.px(cx) + rx * self.r, self.px(cy) + ry * self.r],
                      fill=(13, 11, 17))

        elif kind == "crescent":
            _, cx, cy, rx, ry, col, a0, a1 = item
            pts = self._pts(cx, cy, rx, ry, a0, a1, 18)
            w = max(1, int(self.r * 0.013))
            d.line(pts, fill=unpack(col), width=w, joint="curve")
            for (x, y) in (pts[0], pts[-1]):
                rr = max(1.0, w / 2.0)
                d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=unpack(col))

        elif kind == "arc":
            _, cx, cy, rx, ry, a0, a1, col, w = item
            d.line(self._pts(cx, cy, rx, ry, a0, a1),
                   fill=unpack(col), width=max(1, int(self.r * w)))

        elif kind == "smile_arc":
            _, cx, cy, rx, ry, amount, col = item
            n = 20
            pts = []
            for i in range(n + 1):
                t = i / n
                xn = -1.0 + 2.0 * t
                y = cy + ry * (0.15 + 0.50 * (1.0 - xn * xn)) * (0.55 + 0.45 * amount)
                pts.append((self.px(cx + rx * xn), self.px(y)))
            c = unpack(col)
            w = max(2, int(self.r * 0.038 * min(1.0, 0.4 + amount)))
            d.line(pts, fill=c, width=w, joint="curve")
            for (x, y) in (pts[0], pts[-1]):
                rr = max(2.0, w / 2.0)
                d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=c)

        elif kind == "dot":
            _, cx, cy, r = item
            s = max(1.0, r * self.r)
            d.rectangle([self.px(cx) - s, self.px(cy) - s,
                         self.px(cx) + s, self.px(cy) + s], fill=(232, 238, 252))

        elif kind in ("bond_heart", "lid"):
            _, pts = item
            d.polygon([(self.px(x), self.px(y)) for x, y in pts],
                      fill=(255, 156, 176) if kind == "bond_heart" else (44, 36, 30))

        elif kind == "bond_ring":
            _, pts, alpha = item
            d.polygon([(self.px(x), self.px(y)) for x, y in pts],
                      outline=(255, 226, 150), width=max(1, int(self.r * 0.012)))

        elif kind == "spark":
            _, cx, cy, size, ang, phase = item
            col = (255, 226, 120) if (phase % 0.5) < 0.25 else (140, 220, 255)
            x, y = self.px(cx), self.px(cy)
            s = size * self.r
            d.polygon([(x, y - s * 2.2), (x + s * 0.7, y - s * 0.2),
                       (x + s * 2.2, y), (x + s * 0.7, y + s * 0.2),
                       (x, y + s * 2.2), (x - s * 0.7, y + s * 0.2),
                       (x - s * 2.2, y), (x - s * 0.7, y - s * 0.2)], fill=col)


def _circle_mask(img, bg_alpha: int = 255):
    """把方形画面裁成圆形（圆屏模拟），圆外补成透明或纯黑。"""
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
