"""
duetto_owl.py — Duetto「小猫头鹰」形象原型

设计思路
--------
圆屏只有 240×240，图案必须极简。猫头鹰的辨识度来自三样东西：

  1. **面盘**（facial disc）—— 两团不对称的浅色区域，是猫头鹰最标志性的特征
  2. **眉羽**（brow feathers）—— 斜向的眉毛，直接决定"表情"的喜怒哀乐
  3. **喙**（beak）—— 一个小小的倒三角，把两只眼睛"连"成一张脸

眼睛本身沿用之前调好的蛋形 + 瞳孔 + 高光，但因为有了面盘和眉羽，
同样的眼睛会看起来"像鸟"而不是"像机器人"。

三种手势对应三组表情：
  - 敲击（轻/重）→ 点头 + 眯眼 + 眉羽上扬
  - 左右滑动     → 整只鸟侧头（面盘+喙一起动），眉羽一高一低
  - 上滑         → 眉羽炸开、眼睛瞪圆、喙张开（欢呼）

这个文件只放"形象"相关的绘制，动画状态机仍然复用 eyes_core。
坐标依旧是 -1..1 归一化。
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (把仓库根目录加进 sys.path)

import math
from dataclasses import dataclass
from typing import List, Tuple

from eyes_core import (
    Arc, DrawList, Ellipse, Polygon, clamp, lerp,
    ease_in_out_sine,
)

# ---------------------------------------------------------------------------
# 配色：小猫头鹰
# ---------------------------------------------------------------------------

# 屏幕底色：深靛蓝，像夜里的树林
BG = (12, 10, 8)

# 面盘：偏暖的米白，带一点点黄
DISC = (94, 74, 54)        # 面盘：对比度 2.36:1（原 1.42 等于没画出来）
DISC_SHADE = (78, 61, 44)

# 羽毛 / 身体：深灰蓝
FEATHER = (46, 54, 74)
FEATHER_DARK = (30, 36, 52)

# 眉羽：比面盘深一点的褐
BROW = (124, 99, 74)       # 只比面盘略深：眉是脸的延伸，不该抢眼睛

# 眼睛
EYE_CORE = (255, 217, 138)     # 眼睛实心块：琥珀色
PUPIL = (30, 22, 14)
IRIS = (255, 217, 138)
GLINT = (255, 255, 255)

# 喙：暖橙
BEAK = (192, 138, 78)
BEAK_DARK = (138, 99, 56)

# 情绪高亮色（给彩蛋用）
HYPE_WARM = (255, 210, 120)
HYPE_COOL = (140, 220, 255)


# ---------------------------------------------------------------------------
# 几何辅助
# ---------------------------------------------------------------------------


def ellipse_pts(cx: float, cy: float, rx: float, ry: float,
                n: int = 40, phase: float = 0.0) -> List[Tuple[float, float]]:
    """椭圆采样成多边形。"""
    pts = []
    for i in range(n):
        a = 2.0 * math.pi * i / n + phase
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return pts


def disc_shape(cx: float, cy: float, rx: float, ry: float,
               notch: float = 0.0, n: int = 44) -> List[Tuple[float, float]]:
    """
    面盘：一个椭圆，但顶部中间有个凹口（猫头鹰的"心形"面盘）。

    notch=0 → 普通椭圆
    notch=1 → 顶部中间凹陷到底部
    """
    pts = []
    for i in range(n):
        a = 2.0 * math.pi * i / n - math.pi / 2.0   # 从正上方开始
        x = math.cos(a)
        y = math.sin(a)
        # 顶部的凹陷：只影响上半部分，且越靠中间越深
        if y < 0:
            k = max(0.0, 1.0 - abs(x) / 0.55)      # 中间为 1，两侧为 0
            y *= (1.0 - notch * k * k)
        pts.append((cx + rx * x, cy + ry * y))
    return pts


def eye_shape(cx: float, cy: float, rx: float, ry: float, open_k: float,
              smile: float = 0.0, angry: float = 0.0, heart: float = 0.0,
              n: int = 22):
    """
    眼睛轮廓（实心块，无眼白无瞳圈）。

    三个独立通道，对应调研里"一次只动一条边"的原则：
      open_k  上缘高度   1=正圆  <1 上缘下压(困/眯)  >1 上缘上抬(瞪大)
      smile   上缘弧度   正=上缘上凸(笑)  负=下压
      angry   上缘倾斜   正=内低外高(生气)

    猫头鹰还需要一个"鹰式"下缘：下缘略微上收，让眼睛不是纯椭圆。
    """
    k = clamp(open_k, 0.0, 1.0)
    if k <= 0.05:
        return []

    # 心形：在普通轮廓上做径向形变，heart=0 时完全不影响
    if heart > 0.02:
        return _heart_shape(cx, cy, rx, ry, heart, n)

    # 上缘的基准高度（相对 ry）：k=1 → -1（满圆），k=0 → +0.6（几乎闭合）
    y_top = -1.0 + (1.0 - k) * 1.60
    # 猫头鹰的下缘略收，不是完整椭圆
    y_bot = 1.0

    pts = []
    # 下半圈：右 → 左
    for i in range(n + 1):
        t = i / n
        a = math.pi * t                       # 0 → π
        x = math.cos(a)
        y = math.sin(a)
        pts.append((cx + rx * x, cy + ry * (y * y_bot)))

    # 上缘：左 → 右，叠加上凸/倾斜
    for i in range(n + 1):
        t = i / n
        xn = -1.0 + 2.0 * t
        ell = -math.sqrt(max(0.0, 1.0 - xn * xn))
        y = max(ell, y_top)
        # smile：中间上凸（负 y 方向）
        y -= smile * 0.30 * math.sin(math.pi * t)
        # angry：整体倾斜（内侧低、外侧高）
        y += angry * 0.28 * xn * (1.0 - 0.4 * k)
        pts.append((cx + rx * xn, cy + ry * min(y, y_bot)))

    return pts


def _heart_shape(cx: float, cy: float, rx: float, ry: float,
                 heart: float, n: int = 36):
    """
    心形轮廓。用极坐标心形曲线采样，再和圆做插值，
    这样 heart 从 0 到 1 是平滑变形，不会跳。
    """
    pts = []
    for i in range(n):
        t = 2.0 * math.pi * i / n - math.pi / 2.0   # 从正上方开始
        # 心形极坐标：r = 1 - sin(t) 的经典变形
        hx = 16 * math.sin(t) ** 3
        hy = -(13 * math.cos(t) - 5 * math.cos(2 * t)
               - 2 * math.cos(3 * t) - math.cos(4 * t))
        hl = math.hypot(hx, hy) or 1e-6
        hxn, hyn = hx / 17.0, hy / 17.0             # 归一化到约 -1..1
        # 圆
        cxn, cyn = math.cos(t), math.sin(t)
        xn = cxn + (hxn - cxn) * heart
        yn = cyn + (hyn - cyn) * heart
        pts.append((cx + rx * xn, cy + ry * yn))
    return pts


def taper_stroke(p0, p1, w0, w1, n: int = 6) -> List[Tuple[float, float]]:
    """把一条线做成两端粗细不同的"羽状"多边形。眉羽用它。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ln = math.hypot(dx, dy) or 1e-6
    nx, ny = -dy / ln, dx / ln
    top, bot = [], []
    for i in range(n + 1):
        t = i / n
        px = p0[0] + dx * t
        py = p0[1] + dy * t
        w = w0 + (w1 - w0) * t
        top.append((px + nx * w, py + ny * w))
        bot.append((px - nx * w, py - ny * w))
    return top + bot[::-1]


@dataclass
class OwlParams:
    """一帧的所有形象参数，由动画状态机填。"""

    # 整只鸟的姿态
    head_x: float = 0.0       # 水平平移
    head_y: float = 0.0       # 垂直平移
    head_tilt: float = 0.0    # 侧头角度（度）

    # 待机微动（叠加在 head_* 上，让静止时也"活着"）
    idle_x: float = 0.0
    idle_y: float = 0.0

    # 眼睛
    eye_rx: float = 0.185
    eye_ry: float = 0.225
    eye_open: float = 1.0     # 0=闭, 1=正常, >1=瞪大
    eye_gap: float = 0.85     # 眼距倍数（猫头鹰要比人紧凑）
    smile: float = 0.0        # 上缘上凸（开心）
    angry: float = 0.0        # 上缘倾斜（生气/专注）

    # 眉羽
    brow_lift: float = 0.0    # 整体上扬量（负=压低/生气）
    brow_asym: float = 0.0    # 左右不对称（侧头时一高一低）
    brow_flare: float = 0.0   # 炸开（0..1，彩蛋用）

    # 喙
    beak_open: float = 0.0    # 0=合, 1=张开

    # 彩蛋
    hype: float = 0.0
    clock: float = 0.0
    spark_seed: int = 1


# ---------------------------------------------------------------------------
# 绘制
# ---------------------------------------------------------------------------


def build_owl_frame(p: OwlParams) -> DrawList:
    dl = DrawList()
    hx = p.head_x + p.idle_x
    hy = p.head_y + p.idle_y
    tilt = math.radians(p.head_tilt)

    def place(x: float, y: float) -> Tuple[float, float]:
        """按侧头角度旋转一个点（绕屏幕中心）。"""
        c, s = math.cos(tilt), math.sin(tilt)
        return (x * c - y * s + hx, x * s + y * c + hy)

    # ---- 1. 面盘 ----
    disc_rx, disc_ry = 0.86, 0.80
    notch = 0.26
    dpts = [place(*pt) for pt in disc_shape(0, 0, disc_rx, disc_ry, notch)]
    dl.add(("owl_disc", dpts))

    # 面盘内侧暗部，制造"碗"的体积感（上深下浅会显得眼睛陷进去）
    inner = [place(*pt) for pt in disc_shape(0, -0.01, disc_rx * 0.90, disc_ry * 0.90, notch * 1.1)]
    dl.add(("owl_disc_shade", inner))

    # ---- 2. 眼睛 ----
    # 按调研结论：**不画眼白+瞳孔**，画实心发光块（Cozmo/Vector 的做法）。
    # 形状由一个"可插值的轮廓"描述 —— 上缘表达情绪，下缘表达注视。
    # 猫头鹰的眼睛不能转动，所以"看向哪里"完全交给 head_x / head_tilt（转头），
    # 瞳孔只做极小幅度的缩放，不做位移。
    # 彩蛋时两眼微微靠拢（"共鸣"的视觉隐喻）
    eye_dx = 0.255 * p.eye_gap * (1.0 - 0.14 * clamp(p.hype, 0.0, 1.0))
    for side in (-1, 1):
        ex, ey = place(side * eye_dx, 0.0)
        open_k = clamp(p.eye_open, 0.0, 1.6)

        # 彩蛋后期：眼睛短暂变成心形（共鸣的庆祝）
        heart = 0.0
        if p.hype > 0.45:
            # 用时间做一次脉冲，不要一直心形
            ph = (p.clock * 1.6) % 1.0
            heart = max(0.0, math.sin(math.pi * min(1.0, ph * 2.2))) * (p.hype - 0.45) / 0.55
        outline = eye_shape(ex, ey, p.eye_rx, p.eye_ry, open_k,
                            smile=p.smile, angry=p.angry, heart=heart)
        if outline:
            dl.add(("owl_eye", outline))

    # ---- 3. 眉羽 ----
    # 猫头鹰的眉是"一束羽毛"，所以用 3 根由粗到细的羽片叠出层次，
    # 而不是一条实心线（实心线会读成人类的眉毛）。
    for side in (-1, 1):
        lift = p.brow_lift + side * p.brow_asym
        flare = p.brow_flare
        for k, (off, wmul, shorten) in enumerate(
                ((0.000, 1.00, 0.00), (0.052, 0.62, 0.16), (0.098, 0.40, 0.32))):
            base_x = side * (0.575 - off * 0.15)
            base_y = -0.345 - lift * 0.55 - off * 0.85
            tip_x = side * (0.125 + shorten + 0.040 * flare)
            tip_y = -0.250 - lift - off * 1.35 - 0.045 * flare * (1.0 - shorten)
            # 夹紧：眉羽不能飞出脸外
            tip_x = clamp(tip_x, -0.62, 0.62)
            tip_y = clamp(tip_y, -0.52, 0.10)
            w0 = (0.078 + 0.026 * flare) * wmul
            w1 = (0.016 + 0.010 * flare) * wmul
            p0 = place(base_x, base_y)
            p1 = place(tip_x, tip_y)
            dl.add(("owl_brow", taper_stroke(p0, p1, w0, w1, 8), k))

    # ---- 4. 喙 ----
    beak_y = 0.300
    open_k = clamp(p.beak_open, 0.0, 1.0)
    if open_k > 0.02:
        # 张嘴：上喙 + 下喙，中间留出深色口腔，张得越大越明显
        gap = 0.030 + 0.075 * open_k
        mouth = [place(-0.062, beak_y), place(0.062, beak_y),
                 place(0.0, beak_y + gap + 0.035)]
        dl.add(("owl_beak", mouth, (52, 30, 18)))
        up = [place(-0.082, beak_y), place(0.082, beak_y), place(0.0, beak_y + gap)]
        lo = [place(-0.070, beak_y + gap), place(0.070, beak_y + gap),
              place(0.0, beak_y + gap + 0.105)]
        dl.add(("owl_beak", up, BEAK))
        dl.add(("owl_beak", lo, BEAK_DARK))
    else:
        beak = [place(-0.088, beak_y), place(0.088, beak_y), place(0.0, beak_y + 0.175)]
        dl.add(("owl_beak", beak, BEAK))

    # ---- 5. 彩蛋星星 ----
    if p.hype > 0.01:
        _draw_owl_sparks(dl, p)

    return dl


def _draw_owl_lid(dl, ex, ey, rx, ry, cover, side, p: OwlParams, place) -> None:
    """上眼睑：从眼睛顶部往下盖，cover=1 时完全闭合。"""
    # 用椭圆的水平线切割做眼皮
    depth = cover * (ry * 2.15)
    top = ey - ry * 1.25
    bottom = ey - ry + depth
    pts = []
    n = 12
    for i in range(n + 1):
        t = i / n
        x = ex - rx * 1.05 + 2.10 * rx * t
        xn = (x - ex) / (rx * 1.05)
        arch = math.sqrt(max(0.0, 1.0 - min(1.0, abs(xn)) ** 2))
        # 眼皮下沿：中间低两端高（闭眼时是一条向下的弧）
        y = ey - ry * arch + depth * (0.6 + 0.4 * arch)
        pts.append((x, min(y, ey + ry)))
    pts = [(ex + rx * 1.1, top), (ex - rx * 1.1, top)] + pts[::-1]
    dl.add(("owl_lid", pts))


def _draw_owl_sparks(dl, p: OwlParams) -> None:
    import random
    rng = random.Random(p.spark_seed)
    n = 12
    for i in range(n):
        a0 = rng.uniform(0, math.tau)
        r0 = rng.uniform(0.50, 0.85)
        speed = rng.uniform(0.6, 1.3)
        phase = (p.clock * speed + rng.uniform(0, 1)) % 1.0
        rr = 0.45 + r0 * phase
        cx, cy = math.cos(a0) * rr, math.sin(a0) * rr
        size = 0.030 * (1.0 - phase) * p.hype
        if size > 0.003:
            warm = (phase % 0.5) < 0.25
            dl.add(("owl_spark", cx, cy, size, warm))
