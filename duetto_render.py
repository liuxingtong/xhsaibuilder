"""
duetto_render.py — Duetto 猫头鹰形象渲染器（Pillow / Tkinter 通用）

把 duetto_owl.build_owl_frame() 的 DrawList 画出来。
这里集中处理所有"绘制细节"（裁剪、圆屏遮罩、配色），
形象本身只描述几何。

想换形象（比如从猫头鹰换成别的），只改 duetto_owl.py，这个文件基本不用动。
"""

from __future__ import annotations

import math

from eyes_core import clamp

LID_EDGE = (214, 204, 184)
from duetto_owl import (
    BG, BEAK, BEAK_DARK, BROW, DISC, DISC_SHADE, FEATHER, FEATHER_DARK,
    GLINT, HYPE_COOL, HYPE_WARM, IRIS, PUPIL, EYE_CORE,
)


def _rgba(c, a=255):
    return (c[0], c[1], c[2], a)


# ---------------------------------------------------------------------------
# Pillow 渲染（预览 / 导出）
# ---------------------------------------------------------------------------


class OwlPillowRenderer:
    def __init__(self, size: int = 240, super_sample: int = 3):
        from PIL import Image
        self.size = size
        self.ss = super_sample
        self.big = size * super_sample
        self.c = self.big / 2.0
        self.r = self.big / 2.0
        self._Image = Image

    def px(self, v: float) -> float:
        return self.c + v * self.r

    def render(self, dl, bg=BG, circle=True):
        Image = self._Image
        from PIL import ImageDraw, ImageFilter

        img = Image.new("RGB", (self.big, self.big), bg)

        # 1) 眼睛单独一层：因为要按眼睑形状裁剪
        # 注意：alpha 层如果半透明，叠到面盘上会发灰。
        # 所以所有眼睛图元都用不透明色，透明度只由 mask 控制。
        eye_layer = Image.new("RGBA", (self.big, self.big), (0, 0, 0, 0))
        ed = ImageDraw.Draw(eye_layer)
        solid = ImageDraw.Draw(img)

        # 2) 面盘（先画在底图上）
        for it in dl.items:
            k = it[0]
            if k == "owl_disc":
                solid.polygon([(self.px(x), self.px(y)) for x, y in it[1]], fill=DISC)
            elif k == "owl_disc_shade":
                solid.polygon([(self.px(x), self.px(y)) for x, y in it[1]], fill=DISC)

        # 3) 眼睛（画到独立层）
        for it in dl.items:
            k = it[0]
            if k == "owl_eye":
                _, outline = it
                ed.polygon([(self.px(x), self.px(y)) for x, y in outline], fill=_rgba(EYE_CORE))

        # 4) 眼睛层直接合成（轮廓已含眼睑形状，无需再裁剪）
        img = Image.alpha_composite(img.convert("RGBA"), eye_layer).convert("RGB")
        solid = ImageDraw.Draw(img)

        # 5) 眉羽 / 喙 / 星星
        for it in dl.items:
            k = it[0]
            if k == "owl_brow":
                pts = [(self.px(x), self.px(y)) for x, y in it[1]]
                layer = it[2] if len(it) > 2 else 0
                shade = BROW if layer == 0 else (
                    (BROW[0] - 12, BROW[1] - 10, BROW[2] - 8) if layer == 1 else
                    (BROW[0] - 24, BROW[1] - 20, BROW[2] - 16))
                solid.polygon(pts, fill=shade)
            elif k == "owl_beak":
                solid.polygon([(self.px(x), self.px(y)) for x, y in it[1]], fill=it[2])
            elif k == "owl_spark":
                _, cx, cy, s, warm = it
                col = HYPE_WARM if warm else HYPE_COOL
                x, y = self.px(cx), self.px(cy)
                s2 = s * self.r
                solid.polygon([
                    (x, y - s2 * 2.2), (x + s2 * 0.7, y - s2 * 0.2),
                    (x + s2 * 2.2, y), (x + s2 * 0.7, y + s2 * 0.2),
                    (x, y + s2 * 2.2), (x - s2 * 0.7, y + s2 * 0.2),
                    (x - s2 * 2.2, y), (x - s2 * 0.7, y - s2 * 0.2)], fill=col)

        img = img.resize((self.size, self.size), Image.LANCZOS)
        if circle:
            img = _circle_mask(img)
        return img

    def _erase_lid(self, md, cx, cy, rx, ry, open_k):
        """
        在 mask 上抹掉上眼睑覆盖的区域。

        裁剪区域 = 椭圆以内的部分 ∩ y < 眼睑弧线。
        眼睑弧线：两端贴着椭圆左右顶点，中间向下凸，
        向下凸的深度随 open_k 变小而变大——所以眯眼时边缘是弯的。
        """
        keep = clamp(open_k, 0.02, 1.0)
        cy_px = self.px(cy)
        ry_px = ry * self.r
        rx_px = rx * self.r
        cx_px = self.px(cx)

        # 眼睑下沿在眼椭圆内的相对高度：keep=1 → -1(顶部)，keep=0 → +0.75
        y_rel = -0.98 + (1.0 - keep) * 1.78

        n = 26
        arc = []
        for i in range(n + 1):
            t = i / n
            xn = -1.0 + 2.0 * t                 # -1..1
            # 椭圆上该 x 处的上边界
            top_rel = -math.sqrt(max(0.0, 1.0 - xn * xn))
            # 向下凸的弧度：中间更深
            bow = math.sin(math.pi * t) ** 0.8
            y_rel_here = y_rel + bow * 0.30
            y_rel_final = max(top_rel, y_rel_here)
            arc.append((cx_px + rx_px * xn, cy_px + ry_px * y_rel_final))

        pts = arc + [(cx_px + rx_px, -self.big), (cx_px - rx_px, -self.big)]
        md.polygon(pts, fill=0)

    def _arc_clip(self, cx, cy, rx, ry, y_edge_px, is_top):
        """
        抹掉眼睛在 y_edge 之外的部分。

        多边形的下边界（is_top 时）是一条弧：
        从椭圆左下角起，沿椭圆走，走到 y = y_edge 就停，
        再横向连到右边界。这样眼皮边缘是弯的，同时不会吃掉眼睛主体。
        """
        n = 24
        far = -self.big * 2 if is_top else self.big * 3
        # 找到 y_edge 对应的椭圆参数角
        yn = max(-1.0, min(1.0, (y_edge_px - self.px(cy)) / (ry * self.r)))
        if is_top:
            a_edge = math.pi - math.asin(yn) if yn <= 1 else 0.0
            # 从 180° 走到 a_edge（asin 给的是 -90..90）
            a_end = math.pi - math.asin(max(-1.0, min(1.0, yn)))
            a_start = math.pi
        else:
            a_start = 0.0
            a_end = math.asin(max(-1.0, min(1.0, yn)))

        pts = []
        for i in range(n + 1):
            a = a_start + (a_end - a_start) * (i / n)
            x = cx + rx * math.cos(a)
            y = cy + ry * math.sin(a)
            pts.append((self.px(x), self.px(y)))
        pts.append((self.px(cx + rx), y_edge_px))
        pts.append((self.px(cx + rx), far))
        pts.append((self.px(cx - rx), far))
        pts.append((self.px(cx - rx), y_edge_px))
        return pts

    def _fill_ellipse(self, d, cx, cy, rx, ry, color) -> None:
        d.ellipse([self.px(cx) - rx * self.r, self.px(cy) - ry * self.r,
                   self.px(cx) + rx * self.r, self.px(cy) + ry * self.r],
                  fill=_rgba(color))


def _circle_mask(img):
    from PIL import Image, ImageDraw
    w, h = img.size
    big = 4
    mask = Image.new("L", (w * big, h * big), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, w * big - 1, h * big - 1], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    out = Image.new("RGB", (w, h), (0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


# ---------------------------------------------------------------------------
# Tkinter 渲染
# ---------------------------------------------------------------------------


def hexc(c) -> str:
    return "#%02x%02x%02x" % (c[0], c[1], c[2])


class OwlTkRenderer:
    """
    Tkinter 版。Tkinter 没有裁剪，所以眼睑用一个"面盘色 + 眼白"的
    多边形把眼睛上沿盖住——和 Arduino 上的做法一致。
    """

    def __init__(self, canvas, size: int):
        self.canvas = canvas
        self.size = size
        self.c = size / 2.0
        self.r = size / 2.0
        self._bg_items = []

    def px(self, v: float) -> float:
        return self.c + v * self.r

    def render(self, dl) -> None:
        c = self.canvas
        c.delete("owl")
        tag = "owl"

        # 背景圆
        c.create_oval(0, 0, self.size, self.size, fill=hexc(BG), outline="", tags=tag)

        for it in dl.items:
            k = it[0]
            if k == "owl_disc":
                self._poly(it[1], DISC, tag)
            elif k == "owl_disc_shade":
                self._poly(it[1], DISC, tag)
            elif k == "owl_eye":
                _, outline = it
                self._poly(outline, EYE_CORE, tag)
            elif k == "owl_brow":
                layer = it[2] if len(it) > 2 else 0
                shade = BROW if layer == 0 else (
                    (BROW[0] - 12, BROW[1] - 10, BROW[2] - 8) if layer == 1 else
                    (BROW[0] - 24, BROW[1] - 20, BROW[2] - 16))
                self._poly(it[1], shade, tag)
            elif k == "owl_beak":
                self._poly(it[1], it[2], tag)
            elif k == "owl_spark":
                _, cx, cy, s, warm = it
                col = HYPE_WARM if warm else HYPE_COOL
                x, y = self.px(cx), self.px(cy)
                s2 = s * self.r
                c.create_polygon(
                    x, y - s2 * 2.2, x + s2 * 0.7, y - s2 * 0.2,
                    x + s2 * 2.2, y, x + s2 * 0.7, y + s2 * 0.2,
                    x, y + s2 * 2.2, x - s2 * 0.7, y + s2 * 0.2,
                    x - s2 * 2.2, y, x - s2 * 0.7, y - s2 * 0.2,
                    fill=hexc(col), outline="", tags=tag)

    def _ellipse(self, cx, cy, rx, ry, color, tag):
        self.canvas.create_oval(
            self.px(cx) - rx * self.r, self.px(cy) - ry * self.r,
            self.px(cx) + rx * self.r, self.px(cy) + ry * self.r,
            fill=hexc(color), outline="", tags=tag)

    def _poly(self, pts, color, tag):
        flat = []
        for x, y in pts:
            flat.extend((self.px(x), self.px(y)))
        self.canvas.create_polygon(flat, fill=hexc(color), outline="", tags=tag)

    def _clip_arc(self, cx, cy, rx, ry, lid_y_px, color, tag):
        """
        用"面盘色"盖住眼睛上沿。边界是一条弧（不是直线），
        所以眯眼时眼皮是弯的，和 Pillow 版一致。
        """
        n = 24
        pts = []
        for i in range(n + 1):
            t = i / n
            a = math.pi - math.pi * t          # 180° -> 0°，椭圆上半圈
            x = self.px(cx) + rx * self.r * math.cos(a)
            y = self.px(cy) + ry * self.r * math.sin(a)
            pts.append((x, max(y, lid_y_px)))
        pts.append((self.px(cx) + rx * self.r, -20))
        pts.append((self.px(cx) - rx * self.r, -20))
        flat = []
        for x, y in pts:
            flat.extend((x, y))
        self.canvas.create_polygon(flat, fill=hexc(color), outline="", tags=tag)
