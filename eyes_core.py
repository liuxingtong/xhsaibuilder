"""
eyes_core.py — 圆屏「眼睛」动画内核（纯标准库，无第三方依赖）

设计原则
--------
1. 只有状态机 + 浮点数参数。不碰任何绘图库、不碰任何系统时钟。
   → 时间由外部 `update(dt)` 喂进来，所以 PC 版和 Arduino 版可以跑同一份逻辑。
2. 对外只暴露 `EyeStateMachine.build_frame()` 返回的 DrawList，
   DrawList 里只有三种图元：ellipse(椭圆) / polygon(多边形) / arc(弧)，
   它们分别能直译成 Tkinter、Pillow、Arduino 的 GFX 画法。
3. 所有坐标都是「归一化屏幕坐标」，范围 -1..1，原点在屏幕中心，y 轴向下，
   圆屏边缘就是单位圆。这样换 128/240/454 分辨率只改一个 CORNER 常量。

三种交互
--------
① 敲击（轻敲 / 重击键）→ 点头反馈：眼睛眯一下 + 整体上下位移 + 瞳孔缩放
② 9 宫格键（左中右 × 上中下）→ 眼睛水平/垂直平移，带平滑缓动；极左/极右时眼睛形状变化
③ 1 秒内 BNM→GHJ→TYU 三段快速上滑 → 触发「特别嗨」表情

渲染分层
--------
LOVOT 的公开设计访谈里说，他们的眼睛是「虹膜、瞳孔、眼皮在内共 6 层」叠加出来的，
靠层与层之间的明度差造出「被吸进去的深度感」。这里用同样的思路，
但把层数裁到圆屏跑得动的程度（见 `EyeDesign`）：

    眼白 glow      眼圈（iris）    虹膜渐变 iris → 瞳孔 pupil
    下缘反光 inner 内圈 highlights  高光（主光 + 点缀）

注意方向：这里**故意没有画眼白和瞳孔的强对比**。
Cozmo / Vector 这类屏幕表情都是"单色实心块"，因为小圆屏上画白眼球 + 黑瞳孔
看起来像个摄像头。用的是「深色圆盘 + 亮一圈虹膜 + 白高光」的画法。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 屏幕常量
# ---------------------------------------------------------------------------

#: 圆屏分辨率（像素）。改这一行就能适配 128×128 / 240×240 / 454×454。
SCREEN = 240


# ---------------------------------------------------------------------------
# 缓动函数
# ---------------------------------------------------------------------------


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t: float, s: float = 1.9) -> float:
    """带一点点过冲的回弹，眼睛移动到位时会有"啵"的手感。"""
    t = clamp(t, 0.0, 1.0)
    t -= 1.0
    return t * t * ((s + 1.0) * t + s) + 1.0


def ease_in_out_sine(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return -(math.cos(math.pi * t) - 1.0) / 2.0


def spring_step(current: float, target: float, vel: float, dt: float,
                stiffness: float, damping: float) -> Tuple[float, float]:
    """临界阻尼弹簧的单步积分，用来做「平滑过去」的跟随。"""
    accel = (target - current) * stiffness - vel * damping
    vel += accel * dt
    current += vel * dt
    return current, vel


# ---------------------------------------------------------------------------
# 绘制指令（平台无关）
# ---------------------------------------------------------------------------


@dataclass
class Ellipse:
    """
    归一化椭圆。cx/cy 中心，rx/ry 半轴。

    mood 是 **24bit RGB 打包成的整数**（见 rgb_key）。
    用整数而不是元组，是因为 Arduino 版可以直接存成 uint32_t，
    不用为"颜色"单独做一个结构体；同一个界面也就一种颜色表达方式。
    """

    cx: float
    cy: float
    rx: float
    ry: float
    mood: int = 0


@dataclass
class Polygon:
    """归一化多边形，points 为 (x, y) 列表。用于眼皮覆盖、心形、弯眼笑等。"""

    points: List[Tuple[float, float]]
    mood: int = 0


@dataclass
class Arc:
    """
    归一化圆弧/扇形（用于高光、汗滴、爆发光芒）。
    start/end 为角度（度，0 度指向 +x，顺时针递增，和 Tkinter/GFX 一致）。
    """

    cx: float
    cy: float
    rx: float
    ry: float
    start: float
    end: float
    width: float


def ellipse_polygon(cx: float, cy: float, rx: float, ry: float,
                    n: int = 28, squash_bottom: float = 0.0) -> List[Tuple[float, float]]:
    """把椭圆采样成多边形；squash_bottom>0 时下半部分压扁（做"被压到的眼皮"）。"""
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        x = cx + rx * math.cos(a)
        y = cy + ry * math.sin(a)
        if squash_bottom > 0.0 and y > cy:
            y = cy + (y - cy) * (1.0 - squash_bottom)
        pts.append((x, y))
    return pts


@dataclass
class DrawList:
    """一帧画面。按顺序绘制，后画的盖住先画的。"""

    items: List[object] = field(default_factory=list)

    def add(self, item: object) -> None:
        self.items.append(item)


# ---------------------------------------------------------------------------
# 敲击事件
# ---------------------------------------------------------------------------

LIGHT = "light"
HEAVY = "heavy"

#: 轻敲：快速点头 + 眯眼
LIGHT_TAP = dict(
    dur=0.38,        # 总时长
    dip=0.26,        # 向下位移幅度（归一化，正数=向下）
    squash=0.46,     # 最大压扁比例
    pop=1.10,        # 瞳孔最大缩放
    bounce=0.42,     # 回弹过冲比例（向上弹多高）
    pupil_kick=0.0,  # 瞳孔额外位移
)

#: 重击：更嗨 —— 更长、更深、带晃动和张大眼睛
HEAVY_TAP = dict(
    dur=0.80,
    dip=0.40,
    squash=0.56,
    pop=1.60,
    bounce=0.75,
    pupil_kick=0.06,
)


@dataclass
class TapEvent:
    t: float = 0.0
    dur: float = 0.0
    dip: float = 0.0
    squash: float = 0.0
    pop: float = 1.0
    bounce: float = 0.0
    pupil_kick: float = 0.0

    @property
    def active(self) -> bool:
        return self.t < self.dur


def make_tap(kind: str) -> TapEvent:
    cfg = HEAVY_TAP if kind == HEAVY else LIGHT_TAP
    return TapEvent(t=0.0, **cfg)


# ---------------------------------------------------------------------------
# 9 宫格
# ---------------------------------------------------------------------------

#: 9 宫格键 → (列, 行)，列 -1=左 0=中 1=右，行 -1=上 0=中 1=下
KEY_GRID = {
    "t": (-1, -1), "y": (0, -1), "u": (1, -1),
    "g": (-1, 0),  "h": (0, 0),  "j": (1, 0),
    "b": (-1, 1),  "n": (0, 1),  "m": (1, 1),
}

KEY_ROW_NAME = {-1: "上", 0: "中", 1: "下"}
KEY_COL_NAME = {-1: "左", 0: "中", 1: "右"}

#: 左中右三行各自的「形状个性」
#: stretch 越大越横向拉伸（极左/极右看起来像在"推"出去）
COLUMN_STYLE = {
    -1: dict(stretch=1.14, tilt=-13.0, openness=1.04),
    0: dict(stretch=1.00, tilt=0.0, openness=1.00),
    1: dict(stretch=1.14, tilt=13.0, openness=1.04),
}

ROW_STYLE = {
    -1: dict(openness=1.10),  # 上排：眼睛睁大（看起来在往上看）
    0: dict(openness=1.00),
    1: dict(openness=0.90),   # 下排：眼睛微眯（看起来在往下看）
}

#: 行位置对整体垂直偏移的系数。row_f 约定：-1=上，+1=下，y 轴向下，
#: 所以整体位移 = row_f * ROW_SHIFT（正数向下）。
ROW_SHIFT = 0.20

#: 左右位置对整对眼睛水平平移的系数。
#: 经验值：小于 0.15 才像"整体move"，超过就会变成两眼互相靠拢/分开。
EYE_PAN_X = 0.30


# ---------------------------------------------------------------------------
# 眼睛设计（配色 + 形状个性）
# ---------------------------------------------------------------------------


@dataclass
class EyeDesign:
    """
    一套眼睛设计 = 配色 + 若干形状参数。

    思路直接从 LOVOT 的分层访谈里来：一套设计真正变的是
    **层与层之间的明度差** 和 **高光位置**，而不是"画什么形状"。
    所以这里把颜色单独拆出来，形状只留几个有物理含义的系数。

    字段速查
    --------
    glow        眼白外圈，比背景亮一点点，做出"球体嵌进屏幕"的感觉
    eye_white   眼白本体。**不是纯白** —— 是设计主色的淡版，这样整套是同一个色系
    iris_outer  虹膜外缘色
    iris_inner  虹膜靠瞳孔那一圈。它和 iris_outer 之间的过渡就是"彩虹渐变"
    pupil       瞳孔。必须比 iris_inner 更暗，否则看不出瞳孔
    lid         眼皮。**必须比虹膜暗**，否则眯眼时看不出"盖住了"
    sheen       主高光
    needle      指针 / 刻度辅助色（钟表、罗盘这类特殊表情用）
    spark       彩蛋星星
    tint        本设计的整体色相，共振时的心形 / 光环从这里取色

    形态参数（这才是"一套设计看上去不一样"的真正来源）
    ------------------------------------------------
    参考 LOVOT 的 25 套眼睛：它们之间的差别主要不在色相，而在
        · 眼型（圆的 / 竖长的 / 扁的 / 杏仁的）
        · 虹膜占眼眶多大、瞳孔占虹膜多大 → 决定"眼神是呆还是灵"
        · 高光有几个、放在哪 → 决定"是聪明、是可爱、还是无辜"
        · 眼白露多少 → 决定"是卡通还是写实"

    所以这里把形态做成可组合参数：

    sclera      眼型：round / tall / wide / almond
    iris_ratio  虹膜 / 眼眶 半径比（0.66 小眼睛 ～ 0.92 几乎全是虹膜）
    pupil_ratio 瞳孔 / **虹膜** 半径比（不是占眼眶！）。
                这一条最容易被搞错：如果按占眼眶算，虹膜只会剩下一圈边，
                看起来就是"黑色圆盘镶了道金边"，而不是一只眼睛。
    hi_style    高光样式：classic / above / triple / none
                  classic  左上大光 + 右下小光（默认，最"可爱"）
                  above    正上方单光（竖长眼配这个会显得聪明、成熟）
                  triple   三个小闪点（手绘感、湿润感）
                  none     无主光，只留边缘反光（安静、神秘）
    openness    竖向张开系数（眨眼 / 情绪用）
    width       横向系数
    marquee     虹膜里跑马灯的强度
    gaze_kick   看侧面时虹膜被推的比例
    """

    key: str
    name: str
    glow: Tuple[int, int, int]
    eye_white: Tuple[int, int, int]
    iris_outer: Tuple[int, int, int]
    iris_inner: Tuple[int, int, int]
    pupil: Tuple[int, int, int]
    lid: Tuple[int, int, int]
    sheen: Tuple[int, int, int] = (255, 255, 255)
    needle: Tuple[int, int, int] = (210, 222, 240)
    spark: Tuple[int, int, int] = (255, 226, 120)
    tint: Tuple[int, int, int] = (255, 200, 150)
    sclera: str = "round"
    #: 虹膜纹理：solid 纯色 / radial 渐变环 / starfield 星点 / marble 云纹
    iris_pattern: str = "solid"
    iris_ratio: float = 0.80
    pupil_ratio: float = 0.50
    hi_style: str = "classic"
    openness: float = 1.00
    width: float = 1.00
    marquee: float = 0.22
    gaze_kick: float = 0.06


def _rgb_to_hsv(c: Tuple[int, int, int]) -> Tuple[float, float, float]:
    r, g, b = (v / 255.0 for v in c)
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 1e-6:
        h = 0.0
    elif mx == r:
        h = ((g - b) / d) % 6.0
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return h * 60.0, (0.0 if mx < 1e-6 else d / mx), mx


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    h = h % 360.0
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    seg = int(h // 60.0)
    r, g, b = [(c, x, 0.0), (x, c, 0.0), (0.0, c, x),
               (0.0, x, c), (x, 0.0, c), (c, 0.0, x)][seg % 6]
    return (int(round((r + m) * 255)), int(round((g + m) * 255)),
            int(round((b + m) * 255)))


def hue_shift(c: Tuple[int, int, int], deg: float) -> Tuple[int, int, int]:
    """
    换色相，但**保留原色的明度**。

    这一条很关键：背景、眼白、瞳孔之间的明度关系不能变，
    否则一转色相，眼睛就糊进背景里了（这个坑踩过）。
    近中性色（白高光、黑瞳孔）不参与旋转。
    """
    h, s, v = _rgb_to_hsv(c)
    if s < 0.10:
        return c
    return _hsv_to_rgb(h + deg, s, v)


def mix_rgb(a: Tuple[int, int, int], b: Tuple[int, int, int],
            t: float) -> Tuple[int, int, int]:
    t = clamp(t, 0.0, 1.0)
    return (int(round(a[0] + (b[0] - a[0]) * t)),
            int(round(a[1] + (b[1] - a[1]) * t)),
            int(round(a[2] + (b[2] - a[2]) * t)))


def rgb_key(c: Tuple[int, int, int]) -> int:
    """RGB → 24bit 整数。DrawList 里所有颜色都用这个形式，方便逐帧比较。"""
    return (int(c[0]) << 16) | (int(c[1]) << 8) | int(c[2])


def blend_design(a: EyeDesign, b: EyeDesign, t: float) -> EyeDesign:
    """
    两套设计之间逐字段插值。

    颜色是**线性插值**，所以在两套设计之间切换时，中间色的明度也保持连续 ——
    不会出现"切到一半突然变暗"的闪烁。
    """
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    out = EyeDesign(**{k: getattr(a, k) for k in a.__dataclass_fields__})
    for f in a.__dataclass_fields__:
        va, vb = getattr(a, f), getattr(b, f)
        if isinstance(va, tuple) and len(va) == 3:
            setattr(out, f, mix_rgb(va, vb, t))
        elif isinstance(va, float):
            setattr(out, f, lerp(va, vb, t))
        elif isinstance(va, int):
            setattr(out, f, int(round(lerp(va, vb, t))))
    out.key = b.key if t > 0.5 else a.key
    out.name = b.name if t > 0.5 else a.name
    return out


#: 背景。所有设计的明度都是相对它调的，改这个要重新检查对比度。
BG_TOP = (14, 18, 30)
BG_BOT = (6, 8, 14)


EYE_DESIGNS: Dict[str, EyeDesign] = {}


def _reg(d: EyeDesign) -> EyeDesign:
    EYE_DESIGNS[d.key] = d
    return d


#: 默认：暖金虹膜。最上镜的一套，日常 / 主形象用它。
_REG_CLASSIC = _reg(EyeDesign(
    key="classic", name="暖金 · classic",
    glow=(30, 34, 46), eye_white=(252, 248, 238),
    iris_outer=(200, 120, 26), iris_inner=(126, 62, 22),
    pupil=(12, 10, 15), lid=(46, 36, 30),
    sheen=(255, 255, 255), needle=(255, 232, 190),
    spark=(255, 214, 120), tint=(255, 196, 132),
    sclera="round", iris_ratio=0.74, pupil_ratio=0.50, hi_style="classic",
    marquee=0.14,
))

#: 青碧：大虹膜 + 小瞳孔 + 无主高光 → 安静、神秘。对应 LOVOT 的「ロンロン」。
_REG_MINT = _reg(EyeDesign(
    key="mint", name="青碧 · dusk",
    glow=(22, 36, 42), eye_white=(238, 248, 248),
    iris_outer=(32, 150, 154), iris_inner=(14, 64, 76),
    pupil=(8, 14, 18), lid=(28, 40, 44),
    sheen=(236, 255, 252), needle=(200, 240, 236),
    spark=(150, 240, 220), tint=(140, 226, 220),
    sclera="round", iris_pattern="radial", iris_ratio=0.8, pupil_ratio=0.40, hi_style="none",
    marquee=0.30,
))

#: 樱粉：扁眼 + 三闪点 → 手绘水彩、湿润感。对应「ふわふわ」。
_REG_ROSE = _reg(EyeDesign(
    key="rose", name="樱粉 · bloom",
    glow=(46, 30, 38), eye_white=(254, 244, 244),
    iris_outer=(214, 96, 116), iris_inner=(110, 42, 62),
    pupil=(18, 9, 16), lid=(54, 32, 42),
    sheen=(255, 255, 255), needle=(255, 226, 230),
    spark=(255, 190, 210), tint=(255, 158, 180),
    sclera="wide", iris_ratio=0.7, pupil_ratio=0.56, hi_style="triple",
    marquee=0.34,
))

#: 霜蓝：杏仁眼 → 机敏、像猫。
_REG_FROST = _reg(EyeDesign(
    key="frost", name="霜蓝 · frost",
    glow=(20, 30, 48), eye_white=(242, 248, 255),
    iris_outer=(70, 150, 232), iris_inner=(22, 50, 96),
    pupil=(9, 13, 25), lid=(24, 34, 54),
    sheen=(255, 255, 255), needle=(214, 232, 255),
    spark=(160, 216, 255), tint=(132, 190, 255),
    sclera="almond", iris_ratio=0.74, pupil_ratio=0.46, hi_style="classic",
    marquee=0.22,
))

#: 靛紫：竖长眼 + 三闪点 → 专注、有点怯。
_REG_INDIGO = _reg(EyeDesign(
    key="indigo", name="靛紫 · しんしん",
    glow=(28, 26, 46), eye_white=(246, 244, 255),
    iris_outer=(104, 96, 196), iris_inner=(28, 22, 62),
    pupil=(11, 9, 19), lid=(32, 28, 52),
    sheen=(255, 255, 255), needle=(224, 220, 255),
    spark=(196, 180, 255), tint=(178, 168, 255),
    sclera="tall", iris_ratio=0.76, pupil_ratio=0.52, hi_style="triple",
    marquee=0.20,
))

#: 暗金：竖长 + 大虹膜 + 小瞳孔 + 顶置高光 → 成熟、聪明。对应「こうこう」。
_REG_EMBER = _reg(EyeDesign(
    key="ember", name="暗金 · こうこう",
    glow=(40, 32, 22), eye_white=(252, 244, 228),
    iris_outer=(176, 116, 30), iris_inner=(84, 46, 16),
    pupil=(9, 7, 7), lid=(38, 28, 20),
    sheen=(255, 250, 236), needle=(255, 220, 168),
    spark=(255, 208, 132), tint=(255, 186, 118),
    sclera="tall", iris_ratio=0.82, pupil_ratio=0.36, hi_style="above",
    marquee=0.26,
))

#: おとおと：大瞳孔 + 圆眼 → 最"无辜"。LOVOT 里弟弟那套。
_REG_OTONOTO = _reg(EyeDesign(
    key="otonoto", name="朱红 · おとおと",
    glow=(42, 26, 26), eye_white=(254, 246, 244),
    iris_outer=(196, 78, 58), iris_inner=(96, 32, 26),
    pupil=(16, 8, 8), lid=(50, 30, 28),
    sheen=(255, 255, 255), needle=(255, 220, 205),
    spark=(255, 176, 140), tint=(255, 156, 128),
    sclera="round", iris_ratio=0.78, pupil_ratio=0.58, hi_style="classic",
    marquee=0.12,
))

#: ほしぼし：虹膜里撒一圈星点。对应 LOVOT 的星空眼。
_REG_HOSHI = _reg(EyeDesign(
    key="hoshi", name="星空 · ほしぼし",
    glow=(24, 24, 48), eye_white=(246, 246, 255),
    iris_outer=(72, 84, 168), iris_inner=(18, 18, 52),
    pupil=(8, 8, 16), lid=(28, 28, 52),
    sheen=(255, 255, 255), needle=(220, 226, 255),
    spark=(180, 200, 255), tint=(150, 176, 255),
    sclera="round", iris_pattern="starfield",
    iris_ratio=0.78, pupil_ratio=0.46, hi_style="quad",
    marquee=0.24,
))

#: ロンロン：没有主高光，靠云纹和下缘月光发光。
_REG_RONRON = _reg(EyeDesign(
    key="ronron", name="墨玉 · ロンロン",
    glow=(22, 28, 26), eye_white=(240, 248, 244),
    iris_outer=(56, 96, 84), iris_inner=(10, 20, 18),
    pupil=(6, 8, 8), lid=(24, 34, 30),
    sheen=(226, 244, 236), needle=(190, 230, 214),
    spark=(140, 230, 200), tint=(128, 216, 190),
    sclera="wide", iris_pattern="marble",
    iris_ratio=0.84, pupil_ratio=0.42, hi_style="ring",
    marquee=0.30,
))

#: キュキュ：杏仁眼 + 小瞳孔 + 顶光 → 猫。LOVOT 里「キュキュ」那套。
_REG_KUKU = _reg(EyeDesign(
    key="kuku", name="琥珀 · キュキュ",
    glow=(40, 32, 22), eye_white=(254, 248, 236),
    iris_outer=(214, 150, 38), iris_inner=(70, 44, 12),
    pupil=(8, 8, 8), lid=(42, 32, 20),
    sheen=(255, 252, 240), needle=(255, 230, 180),
    spark=(255, 214, 120), tint=(255, 190, 110),
    sclera="almond", iris_ratio=0.70, pupil_ratio=0.34, hi_style="above",
    marquee=0.18,
))

DEFAULT_DESIGN = "classic"

#: 9 宫格里每个位置对应的设计。
#: 三列走冷→暖（霜蓝 → 暖金 → 樱粉），三行走亮→暗（青碧 → 暖金 → 靛紫），
#: 让"往哪看"这件事用颜色也能读出来，不止靠位移。
COLUMN_DESIGN = {-1: "frost", 0: "classic", 1: "rose"}
ROW_DESIGN = {-1: "mint", 0: "classic", 1: "indigo"}

#: 敲击强度对应的设计：越敲越暖越亮，最后到暗金（"嗨"）。
TAP_DESIGN = {LIGHT: "classic", HEAVY: "ember"}


def design_by_key(key: str) -> EyeDesign:
    return EYE_DESIGNS.get(key, _REG_CLASSIC)


# ---------------------------------------------------------------------------
# 上滑彩蛋检测
# ---------------------------------------------------------------------------


@dataclass
class SwipeSample:
    t: float
    row: int
    col: int


@dataclass
class SwipeDetector:
    """
    检测「很短的时间内，先按在下面一排，再中间一排，再上面一排」的向上滑。
    检查顺序：每次按键进来，从最近往前找，看能否还原 row 递减的三段序列。
    """

    window: float = 1.0     # 整段上滑要发生在多少秒内
    max_gap: float = 0.55   # 相邻两段的最大间隔
    history: List[SwipeSample] = field(default_factory=list)

    def reset(self) -> None:
        self.history.clear()

    def feed(self, t: float, row: int, col: int) -> bool:
        """
        喂入一次按键。返回 True 表示完成了一次「下→中→上」的快速上滑。

        判定：在 history 里按时间顺序找一段连续子序列，它的 row 依次出现
        1（下）→ 0（中）→ -1（上），且整段跨度 <= window。
        """
        self.history.append(SwipeSample(t, row, col))
        cutoff = t - self.window
        self.history = [s for s in self.history if s.t >= cutoff]

        # 按时间正序扫，维护当前"进行到第几段"
        stage = 0          # 0=还没开始 1=已按下排 2=已按中排
        stage_t = 0.0
        for sample in self.history:
            if stage == 0:
                if sample.row == 1:
                    stage, stage_t = 1, sample.t
            elif stage == 1:
                if sample.row == 0 and (sample.t - stage_t) <= self.max_gap:
                    stage = 2
                elif sample.row == 1:
                    stage_t = sample.t          # 重新从下排开始
            elif stage == 2:
                if sample.row == -1 and (sample.t - stage_t) <= self.window:
                    self.history.clear()
                    return True
                if sample.row == 1:
                    stage, stage_t = 1, sample.t
                elif sample.row == 0:
                    stage_t = sample.t
        return False


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


@dataclass
class EyeStateMachine:
    """所有动画状态。外部每帧调用 update(dt)，然后 build_frame()。"""

    # 位置（临界阻尼弹簧跟随）
    target_col: int = 0
    target_row: int = 0
    pos_col: float = 0.0
    vel_col: float = 0.0
    pos_row: float = 0.0
    vel_row: float = 0.0

    # 敲击
    tap_ev: Optional[TapEvent] = None
    tap_count: int = 0
    last_tap_kind: str = LIGHT

    # 呼吸 / 待机
    clock: float = 0.0
    blink_in: float = 3.0
    blink_t: float = -1.0
    blink_len: float = 0.16

    # 上滑彩蛋
    hype: float = 0.0        # 0..1 强度
    hype_t: float = 0.0      # 已经持续时长
    hype_len: float = 2.6
    sparkle_seed: int = 1

    # 待机时的随机微瞟（让它活着感强一点）
    idle_look_x: float = 0.0
    idle_look_y: float = 0.0
    idle_next: float = 2.5

    # 双人共振（由外部判定后写入，不是自己触发的）
    # 这是"两个人"层面的状态，单机看不到，所以单独一个通道，
    # 不混进 hype（hype 是自己的上滑彩蛋）。
    bond: float = 0.0          # 0..1 共振强度
    bond_t: float = 0.0
    bond_side: int = 0         # -1 看向左邻机 / +1 看向右邻机 / 0 居中
    bond_len: float = 3.5

    # ---- 表情（让眼睛"活"起来的主要手段）------------------------------
    # 情绪用一根轴表达，而不是状态枚举：0=困、0.5=平静、1=兴奋。
    # 敲击 / 上滑 / 共振都只是往这根轴上"打一脚"，然后自己缓慢落回去。
    mood: float = 0.5
    mood_target: float = 0.5
    joy: float = 0.0           # 0..1 开心（弯眼笑）
    surprise: float = 0.0      # 0..1 惊讶（瞪大 + 高光变小 + 嘴不动，靠眼睛表达）
    blush: float = 0.0         # 0..1 害羞（颊部红晕 + 视线躲闪）

    # 轮播：几套设计是"会动"的（虹膜跑马灯 / 钟表 / 罗盘 / 雨）
    showcase: float = 0.0      # 总进度，整数部分 = 第几套
    showcase_on: bool = False
    showcase_dur: float = 4.0
    showcase_keys: List[str] = field(default_factory=list)

    # 视线自由游走（saccade）。人看东西不是一个点，是不断小幅跳；
    # 只加这一点点，观感差别非常大 —— 完全静止 = 死的，会跳 = 活的。
    eye_px: float = 0.0
    eye_py: float = 0.0
    target_px: float = 0.0
    target_py: float = 0.0
    eye_vx: float = 0.0
    eye_vy: float = 0.0
    saccade_in: float = 1.2

    # 视线跟随（PC 版接鼠标；硬件版接 PIR / 摄像头给的角度）
    look_at_x: Optional[float] = None
    look_at_y: Optional[float] = None
    look_strength: float = 1.0

    detector: SwipeDetector = field(default_factory=SwipeDetector)

    # ---- 输入 -------------------------------------------------------------

    def tap(self, kind: str) -> None:
        """轻敲/重击。重击会打断并覆盖掉正在播放的轻敲。"""
        if kind == HEAVY or self.tap_ev is None or not self.tap_ev.active:
            self.tap_ev = make_tap(kind)
            self.tap_count += 1
            self.last_tap_kind = kind
        else:
            # 连续快速轻敲：叠加一点力度，形成渐进的节奏感
            self.tap_count += 1
            boost = min(0.35, 0.08 * self.tap_count)
            self.tap_ev.t = 0.0
            self.tap_ev.dip *= 1.0 + boost
            self.tap_ev.squash = min(0.9, self.tap_ev.squash * (1.0 + boost))

        # 敲击把情绪顶上去：轻敲是小开心，重击是"被吓一跳"
        if kind == HEAVY:
            self.mood_target = min(1.0, self.mood_target + 0.45)
            self.surprise = min(1.0, self.surprise + 0.60)
            self.joy = max(0.0, self.joy - 0.25)
        else:
            self.mood_target = min(1.0, self.mood_target + 0.18)
            self.joy = min(1.0, self.joy + 0.40)

    def poke(self, strength: float = 1.0, shy: bool = False) -> None:
        """
        「被摸头」这类小互动（硬件上是触摸片；PC 上是点一下眼睛）。

        和敲击不一样：不点头，而是**弯眼笑**；shy=True 时还会脸红 + 视线躲开。
        """
        s = clamp(strength, 0.0, 1.0)
        self.joy = min(1.0, self.joy + 0.80 * s)
        self.mood_target = min(1.0, self.mood_target + 0.30 * s)
        if shy:
            self.blush = min(1.0, self.blush + 0.90 * s)
            self.saccade_in = min(self.saccade_in, 0.10)

    def start_showcase(self, keys: Optional[List[str]] = None,
                       dur: float = 4.0) -> None:
        """开始轮播一圈眼睛设计（演示 / 展览用）。"""
        self.showcase_keys = list(keys) if keys else list(EYE_DESIGNS)
        self.showcase_dur = max(0.8, dur)
        self.showcase = 0.0
        self.showcase_on = bool(self.showcase_keys)

    def stop_showcase(self) -> None:
        self.showcase_on = False
        self.showcase = 0.0

    def set_look_at(self, x: Optional[float], y: Optional[float] = None,
                    strength: float = 1.0) -> None:
        """让眼睛看某个归一化坐标点（None = 交还给自由游走）。"""
        self.look_at_x, self.look_at_y = x, y
        self.look_strength = clamp(strength, 0.0, 1.0)

    def press_grid(self, key: str) -> Optional[str]:
        """9 宫格按键。返回 'hype' 表示触发了上滑彩蛋。"""
        key = key.lower()
        if key not in KEY_GRID:
            return None
        col, row = KEY_GRID[key]
        prev_col, prev_row = self.target_col, self.target_row
        self.target_col, self.target_row = col, row
        # 视线换目标时给一下"跳"的冲量，不要让它匀速滑过去 —— 匀速很假
        if (col, row) != (prev_col, prev_row):
            self._kick_saccade(col - prev_col, row - prev_row)

        if self.detector.feed(self.clock, row, col):
            self.trigger_hype()
            return "hype"

        # 每按一下格子，也给一点点"点一下"的反馈，手感不至于太干
        if self.tap_ev is None or not self.tap_ev.active:
            t = make_tap(LIGHT)
            t.dip *= 0.22
            t.squash *= 0.22
            t.pop = 1.0 + (t.pop - 1.0) * 0.4
            t.dur *= 0.70
            self.tap_ev = t
        return None

    def release_grid(self) -> None:
        """松开所有 9 宫格键 → 回到中间。"""
        self.target_col, self.target_row = 0, 0

    def _kick_saccade(self, dcol: int, drow: int) -> None:
        """换注视目标时给眼球一点冲量，产生真实的"跳视"加速曲线。"""
        self.saccade_in = min(self.saccade_in, 0.35)
        self.eye_vx += dcol * 0.22
        self.eye_vy += drow * 0.18

    def trigger_bond(self, side: int = 0) -> None:
        """
        触发双人共振。

        side: 邻机在屏幕的哪一侧（-1 左 / +1 右），决定眼睛往哪边偏，
              形成"互相看着对方"的效果。
        """
        self.bond = 1.0
        self.bond_t = 0.0
        self.bond_side = side
        # 共振永远是最开心的一刻
        self.joy = 1.0
        self.mood_target = 1.0
        self.blush = min(1.0, self.blush + 0.6)

    def trigger_hype(self) -> None:
        self.hype = 1.0
        self.hype_t = 0.0
        self.sparkle_seed = random.randint(1, 9999)
        self.mood_target = 1.0
        self.joy = min(1.0, self.joy + 0.7)
        self.surprise = min(1.0, self.surprise + 0.8)

    # ---- 更新 -------------------------------------------------------------

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)  # 防止窗口拖动/卡顿造成参数爆炸
        self.clock += dt

        # 位置弹簧：stiffness/damping 决定"平滑过去"的快慢
        self.pos_col, self.vel_col = spring_step(
            self.pos_col, float(self.target_col), self.vel_col, dt, 190.0, 24.0)
        self.pos_row, self.vel_row = spring_step(
            self.pos_row, float(self.target_row), self.vel_row, dt, 160.0, 22.0)

        # 敲击推进
        if self.tap_ev is not None:
            self.tap_ev.t += dt
            if not self.tap_ev.active:
                self.tap_ev = None
                self.tap_count = 0

        # 眨眼
        if self.blink_t >= 0.0:
            self.blink_t += dt
            if self.blink_t >= self.blink_len:
                self.blink_t = -1.0
                self.blink_in = random.uniform(2.4, 6.5)
        else:
            self.blink_in -= dt
            if self.blink_in <= 0.0:
                self.blink_t = 0.0

        # 彩蛋衰减
        if self.hype > 0.0:
            self.hype_t += dt
            if self.hype_t >= self.hype_len:
                self.hype = 0.0
                self.hype_t = 0.0

        # 共振衰减
        if self.bond > 0.0:
            self.bond_t += dt
            if self.bond_t >= self.bond_len:
                self.bond = 0.0
                self.bond_t = 0.0
                self.bond_side = 0

        # 待机微瞟
        self.idle_next -= dt
        if self.idle_next <= 0.0:
            self.idle_next = random.uniform(2.0, 5.0)
            self.idle_look_x = random.uniform(-0.045, 0.045)
            self.idle_look_y = random.uniform(-0.035, 0.035)

        self._update_mood(dt)
        self._update_saccade(dt)

        # 轮播
        if self.showcase_on:
            self.showcase += dt / self.showcase_dur
            n = len(self.showcase_keys)
            if n == 0:
                self.showcase_on = False
            elif self.showcase >= n:
                self.showcase = float(n) - 1e-4   # 停在最后一帧，不要突然跳回第一套
                self.showcase_on = False

    # ---- 表情更新 ---------------------------------------------------------

    def _update_mood(self, dt: float) -> None:
        """情绪轴 + 三个表情通道的衰减。全部一阶指数衰减，参数好调。"""
        # 情绪轴：往目标靠，同时目标本身慢慢落回平静
        self.mood += (self.mood_target - self.mood) * min(1.0, dt * 6.0)
        self.mood_target += (0.5 - self.mood_target) * min(1.0, dt * 0.55)

        # 开心：来得快，走得慢（余韵）
        self.joy *= math.exp(-dt / 1.15)
        # 惊讶：走得最快
        self.surprise *= math.exp(-dt / 0.42)
        # 害羞：最慢
        self.blush *= math.exp(-dt / 2.4)
        for name in ("joy", "surprise", "blush"):
            v = getattr(self, name)
            if v < 1e-3:
                setattr(self, name, 0.0)

        # 情绪高的时候眨眼更频繁、更短
        if self.blink_t < 0.0:
            lo, hi = (1.4, 3.8) if self.mood > 0.66 else (2.4, 6.5)
            self.blink_in = min(self.blink_in, random.uniform(lo, hi))

    def _update_saccade(self, dt: float) -> None:
        """
        眼球自由游走。

        两层：
          1. 一个低频的随机目标点（每秒换 1~2 次）
          2. 一个带惯性的一阶跟随，让它"跳"而不是匀速滑
        兴奋时跳得更勤更大，困的时候几乎不动。
        """
        self.saccade_in -= dt
        if self.saccade_in <= 0.0:
            amp = 0.030 + 0.045 * clamp(self.mood, 0.0, 1.0)
            # 害羞 / 别扭的时候往斜下方躲
            if self.blush > 0.3:
                self.target_px = random.uniform(-0.05, 0.05)
                self.target_py = random.uniform(0.02, 0.06)
            else:
                self.target_px = random.uniform(-amp, amp)
                self.target_py = random.uniform(-amp * 0.8, amp * 0.8)
            self.saccade_in = random.uniform(0.55, 1.7) / (0.6 + self.mood)

        # 外部指定了注视点就压过去（鼠标 / 传感器）
        if self.look_at_x is not None:
            self.target_px = clamp(self.look_at_x, -1.0, 1.0) * 0.2 * self.look_strength
            self.target_py = clamp(self.look_at_y or 0.0, -1.0, 1.0) * 0.12 * self.look_strength

        # 弹簧但很轻：眼睛动得快、落得稳
        self.eye_px, self.eye_vx = spring_step(
            self.eye_px, self.target_px, self.eye_vx, dt, 320.0, 30.0)
        self.eye_py, self.eye_vy = spring_step(
            self.eye_py, self.target_py, self.eye_vy, dt, 300.0, 28.0)

    # ---- 当前该用哪套设计 -------------------------------------------------

    def current_design(self) -> EyeDesign:
        """
        把「位置 / 情绪 / 彩蛋 / 轮播」揉成一套最终生效的设计。

        优先级（从高到低）：
          轮播 > 共振 > 上滑彩蛋 > 敲击 > 9宫格位置 > 默认
        越靠前的越"特殊"，越不会被打断。
        """
        # 轮播：现在是第几套 + 段内进度（用来算"换了多少"）
        if self.showcase_keys:
            i = int(self.showcase)
            if 0 <= i < len(self.showcase_keys):
                return design_by_key(self.showcase_keys[i])

        base = _pos_design(self.pos_col, self.pos_row)

        if self.bond > 0.05:
            return blend_design(base, _REG_ROSE, 0.55 + 0.45 * clamp(self.bond, 0.0, 1.0))
        if self.hype > 0.0:
            return blend_design(base, _REG_EMBER, 0.7)
        if self.tap_ev is not None and self.tap_ev.active:
            target = design_by_key(TAP_DESIGN[self.last_tap_kind])
            p = clamp(self.tap_ev.t / self.tap_ev.dur, 0.0, 1.0)
            # 敲击的瞬间切过去，最后 30% 再退回来
            k = 1.0 if p < 0.6 else max(0.0, (p - 0.6) / 0.4)
            return blend_design(base, target, k)
        return base

    # ---- 计算各通道 -------------------------------------------------------

    def _tap_channels(self) -> Tuple[float, float, float, float]:
        """返回 (垂直位移, 压扁比例, 瞳孔缩放, 水平抖动)。"""
        if self.tap_ev is None:
            return 0.0, 0.0, 1.0, 0.0
        tp = self.tap_ev
        p = clamp(tp.t / tp.dur, 0.0, 1.0)

        if self.last_tap_kind == HEAVY:
            # 重击：快速砸下 → 回弹过冲 → 稳定
            if p < 0.22:
                k = ease_out_cubic(p / 0.22)
                dip = tp.dip * k
                squash = tp.squash * k
            elif p < 0.55:
                k = ease_in_out_sine((p - 0.22) / 0.33)
                dip = lerp(tp.dip, -tp.dip * tp.bounce, k)
                squash = lerp(tp.squash, -tp.squash * 0.18, k)
            else:
                k = ease_out_cubic((p - 0.55) / 0.45)
                dip = lerp(-tp.dip * tp.bounce, 0.0, k)
                squash = lerp(-tp.squash * 0.18, 0.0, k)
            # 嗨起来的左右晃（幅度随进度衰减）
            wobble = math.sin(self.tap_ev.t * 30.0) * 0.058 * (1.0 - p) ** 1.5
            pop = 1.0 + (tp.pop - 1.0) * math.sin(math.pi * min(1.0, p * 1.25))
        else:
            # 轻敲：一个干净的下沉 + 小回弹
            if p < 0.35:
                k = ease_out_cubic(p / 0.35)
                dip = tp.dip * k
                squash = tp.squash * k
            else:
                k = ease_out_cubic((p - 0.35) / 0.65)
                dip = lerp(tp.dip, -tp.dip * tp.bounce, k)
                squash = lerp(tp.squash, 0.0, k)
            wobble = 0.0
            pop = 1.0 + (tp.pop - 1.0) * math.sin(math.pi * p)

        # 结束前把位移收回 0，避免最后跳变
        if p > 0.92:
            f = (p - 0.92) / 0.08
            dip *= (1.0 - f)
            squash *= (1.0 - f)

        return dip, squash, pop, wobble

    def _blink_amount(self) -> float:
        if self.blink_t < 0.0:
            return 0.0
        p = clamp(self.blink_t / self.blink_len, 0.0, 1.0)
        return math.sin(math.pi * p)

    # ---- 出帧 -------------------------------------------------------------

    def build_frame(self) -> DrawList:
        dl = DrawList()
        col_f = self.pos_col
        row_f = self.pos_row

        # 列/行的插值风格：在 -1/0/1 之间做分段平滑
        style = _blend_style(col_f, row_f)

        dip, squash, pop, wobble = self._tap_channels()
        blink = self._blink_amount()
        hype = self.hype
        bond = self.bond

        # 共振包络：前 0.25s 冲上去，之后缓慢回落
        if bond > 0.0:
            bp = clamp(self.bond_t / self.bond_len, 0.0, 1.0)
            bond_env = bond * (1.0 - ease_in_out_sine(clamp((bp - 0.25) / 0.75, 0, 1)) * 0.55)
        else:
            bond_env = 0.0

        joy = clamp(self.joy, 0.0, 1.0)
        surprise = clamp(self.surprise, 0.0, 1.0)
        blush = clamp(self.blush, 0.0, 1.0)
        mood = clamp(self.mood, 0.0, 1.0)

        # 上滑彩蛋对整体姿态的叠加
        hype_pulse = 1.0
        if hype > 0.0:
            env = hype * (1.0 - clamp(self.hype_t / self.hype_len, 0.0, 1.0) * 0.25)
            hype_pulse = 1.0 + 0.06 * math.sin(self.clock * 12.0) * env

        # 整体垂直偏移：敲击点头 + 行位置 + 彩蛋跳动
        base_y = row_f * ROW_SHIFT
        if hype > 0.0:
            base_y += math.sin(self.clock * 9.0) * 0.045 * hype
        if bond_env > 0.0:
            # 上浮 + 轻快的上下跳动
            base_y -= 0.055 * bond_env
            base_y += math.sin(self.clock * 7.0) * 0.022 * bond_env
        # 精神力低的"垂头"和开心时的上浮
        base_y += (1.0 - mood) * 0.020 - joy * 0.012
        center_y = base_y + dip

        # 眼睛的水平中心：左右位置主要由「两眼中心整体平移」表达，
        # 平移量要比两眼间距小得多，否则看起来像两只眼睛在互相靠拢。
        eye_dx = col_f * EYE_PAN_X
        eye_dx += self.idle_look_x * (1.0 - abs(col_f))
        eye_dx += wobble
        # 视线游走（saccade）不在这里做 —— 移动整只眼睛看起来像"眼睛在滑",
        # 正确做法是**只让瞳孔在眼白里转**，见 _draw_eye 里的 gaze 参数。
        saccade_k = 1.0 - min(1.0, abs(col_f) + abs(row_f))
        # 共振：整对眼睛往邻机方向偏，形成"对望"
        eye_dx += self.bond_side * 0.085 * bond_env

        # 两眼间距与尺寸
        spread = 0.290 * style["spread"] * (1.0 + 0.06 * bond_env)
        eye_rx = 0.164 * style["stretch"] * (1.0 + 0.26 * hype + 0.55 * bond_env)
        eye_ry = 0.208 * style["openness"] * pop * (1.0 + 0.38 * hype + 0.55 * bond_env)

        # 惊讶：瞪大（高度上得比宽度多，才像"睁眼"而不是"吹气球"）
        eye_rx *= 1.0 + 0.10 * surprise
        eye_ry *= 1.0 + 0.30 * surprise
        # 困：上下压缩
        eye_ry *= lerp(0.82, 1.04, mood)

        # 眨眼 + 敲击压扁合并
        squash_total = clamp(squash + blink, 0.0, 0.98)
        eye_ry *= (1.0 - squash_total)
        eye_ry = max(eye_ry, 0.012)

        # 倾斜通道：双眼同一个倾斜方向（同一侧眉毛抬起），而不是左右镜像
        tilt = style["tilt"] * (0.4 + 0.6 * abs(col_f))
        tilt += math.sin(self.clock * 1.7) * 1.5 * abs(col_f)  # 轻微呼吸

        design = self.current_design()
        inner_rot = self.clock * (1.5 + 3.0 * mood)   # 虹膜跑马灯 / 指针角度

        # 星星眼的强度包络：0.25s 冲满，之后按 hype 进度回落
        hype_env = 0.0
        if hype > 0.0:
            prog = clamp(self.hype_t / self.hype_len, 0.0, 1.0)
            rise = clamp(self.hype_t / 0.25, 0.0, 1.0)
            fall = clamp((prog - 0.45) / 0.55, 0.0, 1.0)
            hype_env = rise * (1.0 - ease_in_out_sine(fall))

        # ---- 眼睛本体 ----------------------------------------------------
        eye_boxes = []
        for side in (-1, 1):
            # 外眼角跟着瞪大微调，避免惊讶时两眼光学中心散开
            cx = eye_dx + side * spread
            cy = center_y
            near = 1.0 if side * col_f > 0 else 0.0
            size_k = 1.0 + (0.12 * near - 0.07 * (1 - near)) * abs(col_f)
            rx_, ry_ = eye_rx * size_k, eye_ry * size_k

            # 上滑彩蛋：眼睛变成**星星眼**。
            # 和共振变心形用同一套插值机制（形状库），只是目标形状不同。
            # 强度分两段：先冲到 1（0.25s），尾段再慢慢化回普通眼睛。
            if hype_env > 0.05:
                # 星星眼要比普通眼睛**大一圈**，才有"炸开"的感觉
                grow = 1.10 + 0.22 * clamp(hype_env, 0, 1)
                shape_pts = blend_shape("round", "star", clamp(hype_env, 0, 1),
                                        cx, cy, rx_ * grow, ry_ * grow)
                # 星星用固定的亮金 —— 不要从 design.iris_outer 混，
                # 因为那会随 9 宫格位置变，星星眼的颜色就会跟着跳（看起来在闪）
                star_col = mix_rgb((255, 210, 90), (255, 246, 190),
                                   clamp(hype_env, 0, 1) * 0.6)
                dl.add(Polygon(shape_pts, mood=rgb_key(star_col)))
                eye_boxes.append((cx, cy, rx_, ry_))
                continue

            # 共振：眼睛直接变成心形（最醒目、最不会看错的表情）
            if bond_env > 0.03:
                k = clamp(bond_env, 0.0, 1.0)
                blended = blend_shape("round", "heart", k,
                                      cx, cy + ry_ * 0.02,
                                      rx_ * 1.00, ry_ * 1.02)
                dl.add(Polygon(blended, mood=rgb_key(mix_rgb(design.tint, _HEART, k))))
                eye_boxes.append((cx, cy, rx_, ry_))
                continue

            # 瞳孔朝向 = 9宫格方向（强）+ 自由游走（弱）
            gx = clamp(col_f * 0.62 + self.eye_px * 1.9 * saccade_k, -1.0, 1.0)
            gy = clamp(row_f * 0.45 + self.eye_py * 1.9 * saccade_k, -1.0, 1.0)
            self._draw_eye(dl, cx, cy, rx_, ry_, side, design, inner_rot,
                           squash_total, surprise, joy, blush, mood, gx, gy)
            eye_boxes.append((cx, cy, rx_, ry_))

        # ---- 颊红晕（害羞）：画在眼睛下方外侧，避开眼睛本体 ---------------
        if blush > 0.02:
            for side in (-1, 1):
                bx = eye_dx + side * (spread + eye_rx * 1.25)
                by = center_y + eye_ry * 1.02
                r = 0.034 * (0.6 + 0.7 * blush)
                c = mix_rgb(design.glow, _BLUSH, 0.45 + 0.55 * blush)
                dl.add(Ellipse(bx, by, r * 1.7, r, mood=rgb_key(c)))

        if bond_env > 0.01:
            _draw_bond_ring(dl, bond_env, self.clock, eye_dx, center_y)

        if hype > 0.0:
            _draw_sparkles(dl, hype, self.clock, self.sparkle_seed)

        # 轮播时每套设计的名字（只用矩形，Arduino 上能直接画）
        if self.showcase_keys:
            i = int(self.showcase)
            if 0 <= i < len(self.showcase_keys):
                frac = self.showcase - i
                a = min(1.0, frac * 6.0, (1.0 - frac) * 6.0)
                _draw_label(dl, EYE_DESIGNS_NAME(self.showcase_keys[i]), a,
                            design.sheen)

        return dl

    # ---- 单只眼睛（分层绘制）----------------------------------------------

    def _draw_eye(self, dl: DrawList, cx: float, cy: float, rx: float, ry: float,
                  side: int, d: EyeDesign, inner_rot: float,
                  squash: float, surprise: float, joy: float,
                  blush: float, mood: float,
                  gx: float = 0.0, gy: float = 0.0) -> None:
        """
        一只眼睛，从外到内分层画。

        比例模型（这是像不像"眼睛"的关键）：
            眼白      = 整个椭圆
            虹膜      = 眼白 × iris_ratio            （默认 0.80）
            瞳孔      = **虹膜** × pupil_ratio        （默认 0.50 → 占眼白 0.40）
            高光      = 瞳孔上的小圆

        注意瞳孔是相对**虹膜**算的。早先按相对眼白算，0.58 的瞳孔配上
        0.88 的虹膜，虹膜只剩薄薄一圈边 —— 看起来是"黑盘子镶金边"，
        根本不像眼睛。这是这次重做的核心修正。

        内眼角 / 跑马灯 / 高光的细节都是为了"湿润感"和"深度"，不是装饰。

        绘制顺序（移植 Arduino 时照抄，别调换）：
          1 眼白外圈   2 眼白本体   3 虹膜外圈   4 虹膜内圈（渐变）
          5 虹膜内亮环 6 瞳孔       7 高光       8 下缘月光
          9 弯眼笑弧  10 眼皮
        """
        col = rgb_key

        # ---- 眼型：只改椭圆长短轴，四种眼型差别明显且实现便宜 ----------
        srx, sry = sclera_dims(d.sclera, rx, ry)

        # 1) 眼白外圈：比眼白暗一点点，做出球体嵌进屏幕的边界
        dl.add(Ellipse(cx, cy, srx * 1.08, sry * 1.08, mood=col(d.glow)))

        # 2) 眼白本体
        dl.add(Ellipse(cx, cy, srx, sry, mood=col(d.eye_white)))

        # ---- 视线 ----------------------------------------------------
        # 只让虹膜和瞳孔在眼白里平移。而且**瞳孔比虹膜走得更多**：
        # 这是 GROOVE X 动画师文章里明确提到的一条
        # （虹彩と瞳孔では移動距離が異なり、瞳孔の方が進行方向に対してより移動する）。
        # 少了这一条，两只黑球同步平移，看起来就很"假"。
        ir = clamp(d.iris_ratio, 0.55, 0.95)
        irx, iry = srx * ir, sry * ir
        # 虹膜最多能挪到"刚好不露出背景"
        max_sx = max(0.0, srx - irx)
        max_sy = max(0.0, sry - iry)
        gx = clamp(gx, -1.0, 1.0)
        gy = clamp(gy, -1.0, 1.0)
        ix = cx + gx * max_sx
        iy = cy + gy * max_sy

        pr = clamp(d.pupil_ratio * (1.0 - 0.18 * surprise), 0.24, 0.86)
        prx, pry = irx * pr, iry * pr
        # 瞳孔再额外多走 30%，但不超过虹膜的余量
        extra = 0.30
        px_ = ix + gx * (irx - prx) * extra
        py_ = iy + gy * (iry - pry) * extra

        # 3) 虹膜外圈（深）：让虹膜的边界从眼白里"立"起来
        dl.add(Ellipse(ix, iy, irx, iry, mood=col(d.iris_outer)))

        # 4) 虹膜内圈（浅）：外深内浅 = 中心亮，人眼就是这么有纵深的
        dl.add(Ellipse(ix, iy, irx * 0.90, iry * 0.90, mood=col(d.iris_inner)))

        # 4.5) 虹膜纹理。四种，都是在虹膜环上再叠几层 —— 便宜且 Arduino 能画。
        self._draw_iris_pattern(dl, ix, iy, irx, iry, prx, pry, d, inner_rot)

        # 5) 虹膜内亮环：贴着瞳孔外缘的一圈亮边，像光透过角膜
        rim = mix_rgb(d.iris_inner, d.iris_outer, 0.62)
        dl.add(Ellipse(px_, py_, prx * 1.18, pry * 1.18, mood=col(rim)))

        # 6) 瞳孔
        dl.add(("pupil", px_, py_, prx, pry))

        # 7) 高光。四种样式，这是"性格"差别最大的地方：
        #    classic 可爱 / above 聪明 / triple 湿润手绘 / none 静谧
        self._draw_highlights(dl, px_, py_, prx, pry, d, inner_rot)

        # 8) 下缘月光：正下方一小段弧，给眼球玻璃质感。
        #    只取 50°..130°，取大了会变成横贯眼睛的一道亮杠，读起来像嘴。
        if d.hi_style != "none":
            dl.add(("crescent", px_, py_, prx * 1.16, pry * 1.16,
                    col(mix_rgb(d.sheen, d.iris_outer, 0.55)), 50.0, 130.0))

        # 9) 弯眼笑：开心时瞳孔里浮起一道上弯的弧
        if joy > 0.03:
            dl.add(("smile_arc", px_, py_, prx * 0.92, pry * 0.92, joy,
                    col(d.sheen)))

        # 10) 眼皮：压扁时盖在最上面
        if squash > 0.03:
            _draw_lid(dl, cx, cy, srx, sry, squash, d)

    def _draw_iris_pattern(self, dl: DrawList, ix: float, iy: float,
                           irx: float, iry: float, prx: float, pry: float,
                           d: EyeDesign, rot: float) -> None:
        """
        虹膜纹理。四种：

          solid     不画（纯渐变，最干净）
          radial    再叠两圈同心环，把"外深内浅"的过渡做细 → 有纵深
          starfield 虹膜环上撒一圈小亮点 → 对应 LOVOT 的「ほしぼし」星空眼
          marble    两个偏心椭圆叠出云纹 → 对应「ロンロン」那种带花纹的黑目
        """
        if d.iris_pattern == "radial":
            # 两道中间环，让 0.90 到 1.00 之间的渐变不那么"硬"
            for k, t in ((0.955, 0.30), (0.905, 0.55)):
                c = mix_rgb(d.iris_outer, d.iris_inner, t)
                dl.add(Ellipse(ix, iy, irx * k, iry * k, mood=rgb_key(c)))

        elif d.iris_pattern == "starfield":
            # 固定的 10 个点，长度按黄金角散开，避免规则排列
            n = 13
            for i in range(n):
                a = i * 2.399963 + rot * 0.15
                rr = 0.84 + 0.11 * ((i * 7) % 5) / 4.0
                dx = math.cos(a) * irx * rr
                dy = math.sin(a) * iry * rr
                r = max(0.005, irx * (0.048 + 0.016 * (i % 3)))
                c = mix_rgb(d.sheen, d.iris_outer, 0.18 + 0.10 * (i % 3))
                dl.add(Ellipse(ix + dx, iy + dy, r, r, mood=rgb_key(c)))

        elif d.iris_pattern == "marble":
            # 两片偏心椭圆，颜色比虹膜亮一档 → 读起来像云纹/大理石
            for k, (ox, oy, sc) in enumerate((
                (-0.18, -0.30, 0.34),
                (0.24, 0.20, 0.26),
            )):
                c = mix_rgb(d.iris_inner, d.iris_outer, 0.58)
                dl.add(Ellipse(ix + irx * ox, iy + iry * oy,
                               irx * sc, iry * sc * 0.62, mood=rgb_key(c)))

    def _draw_highlights(self, dl: DrawList, px_: float, py_: float,
                         prx: float, pry: float, d: EyeDesign,
                         inner_rot: float) -> None:
        """
        高光。四种样式，位置是真的有讲究的：
          classic  左上大光 + 右下小光 → 最"可爱"，因为像在看镜头
          above    正上方单光         → 竖长眼配它显聪明、成熟
          triple   三个小闪点         → 手绘感、湿润感最强
          none     不画主光           → 安静、神秘，靠下缘月光提供亮度
        """
        s = d.sheen
        col = rgb_key

        # 高光跟着瞳孔走（光源在环境里，但近距离高光基本随眼球）
        hx, hy = px_, py_

        if d.hi_style == "above":
            r = prx * 0.34
            dl.add(Ellipse(hx, hy - pry * 0.52, r, r, mood=col(s)))
            dl.add(Ellipse(hx + prx * 0.30, hy + pry * 0.58,
                           prx * 0.10, prx * 0.10, mood=col(s)))

        elif d.hi_style == "triple":
            # 三个大小递减的闪点，沿一条弧排开 —— 手绘感的关键是**不等距、不等大**
            for i, (fx, fy, fr) in enumerate((
                (0.30, -0.52, 0.26),
                (0.55, -0.20, 0.13),
                (-0.10, 0.55, 0.11),
            )):
                r = prx * fr * (1.0 + 0.08 * math.sin(inner_rot * 2.0 + i))
                dl.add(Ellipse(hx + prx * fx, hy + pry * fy, r, r, mood=col(s)))

        elif d.hi_style == "quad":
            # 四个小闪点，上下左右偏一点 —— 最"晶亮"，适合星空/水感
            for i, (fx, fy, fr) in enumerate((
                (0.00, -0.56, 0.22),
                (0.50, -0.10, 0.15),
                (0.10, 0.56, 0.13),
                (-0.44, -0.02, 0.11),
            )):
                r = prx * fr
                dl.add(Ellipse(hx + prx * fx, hy + pry * fy, r, r, mood=col(s)))

        elif d.hi_style == "ring":
            # 顶部一道柔和窗光（是"面"，不是"点"）。
            # 圆环状高光看着像单片眼镜，不自然 —— 真实眼球上的反光是弧形。
            dl.add(("arc", hx, hy, prx * 1.55, pry * 1.55, 215.0, 325.0,
                    col(mix_rgb(s, d.iris_outer, 0.40)), 0.018))

        elif d.hi_style == "none":
            # 没有主光：只留一点点眼角反光，避免整只眼"死黑"
            dl.add(Ellipse(hx - prx * 0.42, hy - pry * 0.44,
                           prx * 0.10, prx * 0.10,
                           mood=col(mix_rgb(s, d.iris_inner, 0.55))))

        else:   # classic
            r = prx * 0.30
            dl.add(Ellipse(hx + prx * 0.34, hy - pry * 0.44, r, r, mood=col(s)))
            dl.add(Ellipse(hx + prx * 0.06, hy + pry * 0.56,
                           prx * 0.13, prx * 0.13, mood=col(s)))

#: 辅助色：颊红晕、共振心形
_BLUSH = (255, 138, 160)
_HEART = (255, 156, 176)


def _pos_design(col_f: float, row_f: float) -> EyeDesign:
    """9 宫格位置 → 设计。列和行各插一次，再相乘成两维效果。"""
    def pick(mapping: Dict[int, str], v: float) -> str:
        if v <= 0.0:
            a, b, t = mapping[-1], mapping[0], clamp(v + 1.0, 0.0, 1.0)
        else:
            a, b, t = mapping[0], mapping[1], clamp(v, 0.0, 1.0)
        return b if ease_in_out_sine(t) > 0.5 else a

    col_key = pick(COLUMN_DESIGN, col_f)
    row_key = pick(ROW_DESIGN, row_f)
    # 两个方向都想切色的时候，以列（左右）为主 —— 左右比上下更容易被看到
    if abs(col_f) < 0.25:
        return design_by_key(row_key)
    return design_by_key(col_key)


def _draw_lid(dl: DrawList, cx: float, cy: float, rx: float, ry: float,
              squash: float, d: EyeDesign) -> None:
    """
    上眼皮：贴着眼睛上缘盖下来一条，盖住的比例 = squash。

    约束（踩过坑，别改）：
      - 横向严格限制在眼睛自身范围内 (|x-cx| <= rx)，
        超出会在眼睛两角糊出一块色斑。
      - 盖下来的深度最多 0.98*ry，眼睛永远剩一条缝，不会整个消失。
      - 颜色取设计里的 lid，它比虹膜暗，所以"盖住了"这件事看得出来。
    """
    depth = clamp(squash, 0.0, 0.98)
    n = 22
    pts = []
    # 下沿：椭圆上缘往下压 depth
    for i in range(n + 1):
        t = i / n
        xn = -1.0 + 2.0 * t
        arch = math.sqrt(max(0.0, 1.0 - xn * xn))
        pts.append((cx + rx * xn, cy - ry * arch + 2.0 * ry * arch * depth))
    # 上沿：原路返回，贴着椭圆上缘 —— 这样整块永远在眼睛内部，
    # 不会在头顶糊出一个矩形（踩过坑）。
    for i in range(n, -1, -1):
        t = i / n
        xn = -1.0 + 2.0 * t
        arch = math.sqrt(max(0.0, 1.0 - xn * xn))
        pts.append((cx + rx * xn, cy - ry * arch))
    dl.add(Polygon(pts, mood=rgb_key(d.lid)))


def EYE_DESIGNS_NAME(key: str) -> str:
    return EYE_DESIGNS[key].name if key in EYE_DESIGNS else key


#: 5x7 点阵字库。只包含轮播要用到的字符（A-Z / 0-9 / · / 空格）。
#: 用点阵而不是矢量字，是因为 Arduino 上可以逐点 fillRect，不用带字体文件。
_GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def _draw_label(dl: DrawList, text: str, alpha: float, color) -> None:
    """
    圆屏底部一行 5x7 点阵字。alpha 用来做淡入淡出。

    只发"点"图元（每个点是一个小方块），不放字形贴图 ——
    Arduino 上就是一个 fillRect 循环，不用带任何字体资源。

    间距说明：px 是**点的大小**，格距取 1.15*px 让点之间留一点缝，
    这样 5x7 的格子还能看出字，但整体又不至于散开
    （之前格距取到 1.35 反而让一整个字挤成一块白）。
    """
    if alpha <= 0.02:
        return
    text = "".join(ch if ch in _GLYPHS else " " for ch in text.upper())[:16]
    px = 0.0165
    adv = px * 6.2          # 一个字前进的横向距离（5 列 + 1 列字距）
    total = len(text) * adv
    x0 = -total / 2.0 + adv / 2.0
    y0 = 0.735
    c = rgb_key(mix_rgb(BG_TOP, color, alpha))
    for i, ch in enumerate(text):
        g = _GLYPHS.get(ch)
        if not g:
            continue
        ox = x0 + i * adv
        for row_i, row in enumerate(g):
            for col_i, bit in enumerate(row):
                if bit != "1":
                    continue
                dl.add(("dot", ox + (col_i - 2) * px,
                        y0 + (row_i - 3) * px, px * 0.44))


def _blend_style(col_f: float, row_f: float) -> dict:
    """把 -1/0/1 的连续位置映射成形状参数。"""
    def one(col: int) -> dict:
        return COLUMN_STYLE[col]

    if col_f <= 0.0:
        a, b, t = one(-1), one(0), clamp(col_f + 1.0, 0.0, 1.0)
    else:
        a, b, t = one(0), one(1), clamp(col_f, 0.0, 1.0)
    t = ease_in_out_sine(t)

    out = {k: lerp(a[k], b[k], t) for k in a}
    # 两眼间距基本恒定（否则平移会被间距变化抵消，看起来像抖动）
    out["spread"] = 1.0

    if row_f <= 0.0:
        ra, rb, rt = ROW_STYLE[-1], ROW_STYLE[0], clamp(row_f + 1.0, 0.0, 1.0)
    else:
        ra, rb, rt = ROW_STYLE[0], ROW_STYLE[1], clamp(row_f, 0.0, 1.0)
    rt = ease_in_out_sine(rt)
    out["openness"] *= lerp(ra["openness"], rb["openness"], rt)
    return out


# ---------------------------------------------------------------------------
# 形状库
# ---------------------------------------------------------------------------
#
# 每一套设计只决定**颜色**，形状单独拿出来，两者正交。
# 所有形状都返回同样点数的极坐标轮廓 (角度 t, 半径比例 s)，这样才能两两插值：
#
#     轮廓 = [ (t_i, s_i) ... ]    t_i 是角度，s_i 是"半径是标准椭圆的几倍"
#
# 插值时只对 s 做线性插值，角度固定 —— 保证几何上连续，不会出现
# "心形绕一圈"那种自交。
#
# Arduino 版照抄这套参数即可：全部是 sin/cos 的组合，没有查表。

#: 轮廓采样点数。40 够圆，Arduino 上也不至于太慢。
SHAPE_N = 64

#: 形状名字（也是轮播 / 调试用的 key）
SHAPES = ("round", "star", "heart", "cat", "squint", "wave", "sparkle")


def _star_radius(t: float, points: int = 5, inner: float = 0.38) -> float:
    """
    五角星。

    屏幕坐标里 t=0 指右、t=pi/2 指**下**。
    想让一个尖朝上，锥角要落在 t = -pi/2。
    """
    # 从"正上方"算起的角度
    a = t + math.pi / 2.0
    k = abs(math.cos(points * 0.5 * a)) ** 2.6   # 指数越大尖越尖
    return inner + (1.0 - inner) * k


def heart_points(cx: float, cy: float, rx: float, ry: float,
                 n: int = SHAPE_N) -> List[Tuple[float, float]]:
    """
    心形轮廓，直接用参数方程采样（不做极坐标转换）。

    极坐标表达心形做不出上方那个凹口 —— 那个凹口要求半径在很短的角度
    区间里突变，平滑函数做不到，硬做出来就是一个圆团（试过两版）。

    参数式（经典心形）：
        x = 16 sin^3(u)
        y = -(13 cos u - 5 cos 2u - 2 cos 3u - cos 4u)
    y 取负是因为屏幕坐标 y 向下，而心形公式里 y 向上。
    自然范围约 x∈[-16,16]、y∈[-17,12]，统一缩放到 ±1。
    """
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        u = 2.0 * math.pi * i / n
        hx = 16.0 * math.sin(u) ** 3
        hy = -(13.0 * math.cos(u) - 5.0 * math.cos(2 * u)
               - 2.0 * math.cos(3 * u) - math.cos(4 * u))
        pts.append((cx + rx * (hx / 17.0), cy + ry * (hy / 17.0)))
    return pts


def _cat_radius(t: float) -> float:
    """
    猫眼/杏仁：**在 shape_polygon 里靠 y 方向压扁实现**，半径恒为 1。

    之前用"左右半径大、上下半径小"来做，结果上下收缩到 0.34 时
    左右两端断开成两半 —— 半径法不能把某个方向压到太小。
    压 y 更直白，而且永远单连通。
    """
    return 1.0


def _wave_radius(t: float) -> float:
    """波浪边，6 个起伏。"""
    return 1.0 + 0.075 * math.sin(6.0 * t)


def _sparkle_radius(t: float) -> float:
    """
    四角闪光：四个方向拉出长尖，对角线收进去。

    用 cos(2t) 做周期，四个尖分别落在 0 / 90 / 180 / 270 度。
    """
    k = abs(math.cos(2.0 * t))
    # 指数越大，四个尖越细长、对角越凹 —— 看起来才是"闪"，不是加号
    return 0.12 + 0.88 * (k ** 4.5)


def _squint_radius(t: float) -> float:
    """
    眯眼：整体压扁成一条缝，但**保持单连通**（不能裂成两半）。

    之前用 0.30 + 0.70*|sin| 会在左右两端缩到 0.30 而中间不缩，
    视觉上断成两块。这里改成"上下压扁"：让 y 方向整体乘一个小系数，
    半径始终 > 0。
    """
    return 1.0


def shape_profile(name: str) -> List[Tuple[float, float]]:
    """返回某个形状的极坐标轮廓 [(角度, 半径比例)]。"""
    out: List[Tuple[float, float]] = []
    for i in range(SHAPE_N):
        t = 2.0 * math.pi * i / SHAPE_N
        if name == "squint":
            # 眯眼靠 y 压扁实现，半径保持 1（见 shape_polygon 里的 ys）
            s = 1.0
        elif name == "star":
            s = _star_radius(t)
        elif name == "cat":
            s = _cat_radius(t)
        elif name == "wave":
            s = _wave_radius(t)
        elif name == "sparkle":
            s = _sparkle_radius(t)
        else:                      # round
            s = 1.0
        out.append((t, s))
    return out


#: 心形等需要查表的形状的缓存
SHAPE_LUT: Dict[str, object] = {}

#: 预计算，避免每帧重算三角函数（240 帧 × 2 眼 × 40 点 = 每秒 19k 次，没必要）
SHAPE_CACHE: Dict[str, List[Tuple[float, float]]] = {}


def _profile(name: str) -> List[Tuple[float, float]]:
    if name not in SHAPE_CACHE:
        SHAPE_CACHE[name] = shape_profile(name)
    return SHAPE_CACHE[name]


#: 眼型 → (横向系数, 竖向系数)。只改椭圆长短轴，便宜、Arduino 好移植。
SCLERA_DIMS = {
    "round":    (1.00, 1.00),
    "tall":     (0.86, 1.16),   # 竖长：成熟、聪明
    "wide":     (1.12, 0.90),   # 扁圆：可爱
    "almond":   (1.10, 0.86),   # 杏仁：机敏
    "teardrop": (1.00, 1.10),   # 水滴：温柔
    "half":     (1.10, 0.72),   # 半闭：困、慵懒
}


def sclera_dims(shape: str, rx: float, ry: float) -> Tuple[float, float]:
    sx, sy = SCLERA_DIMS.get(shape, (1.0, 1.0))
    return rx * sx, ry * sy


def shape_polygon(name: str, cx: float, cy: float, rx: float, ry: float,
                  squash_top: float = 0.0) -> List[Tuple[float, float]]:
    """把形状轮廓变成一个可以直接画的多边形。

    squash_top > 0 时把上半部压扁（眨眼用），和椭圆版的 squash_bottom 一个意思。
    """
    if name == "heart":
        return heart_points(cx, cy, rx, ry)
    ys = {"squint": 0.30, "cat": 0.52}.get(name, 1.0)
    pts: List[Tuple[float, float]] = []
    for t, s in _profile(name):
        x = cx + rx * s * math.cos(t)
        y = cy + ry * s * ys * math.sin(t)
        if squash_top > 0.0 and y < cy:
            y = cy + (y - cy) * (1.0 - squash_top)
        pts.append((x, y))
    return pts


def blend_shape(a: str, b: str, t: float, cx: float, cy: float,
                rx: float, ry: float) -> List[Tuple[float, float]]:
    """
    两个形状之间插值。t=0 完全是 a，t=1 完全是 b。

    只插半径、不插角度 —— 角度对所有形状都一样，所以永远不会自交。
    """
    t = clamp(t, 0.0, 1.0)
    # 心形是参数式采样的，点数和极坐标路径一致（都是 SHAPE_N），
    # 所以可以直接把它当成一组点参与线性插值。
    if a == "heart" or b == "heart":
        ha = heart_points(cx, cy, rx, ry)
        hb = heart_points(cx, cy, rx, ry)
        pa2 = _profile(a) if a != "heart" else None
        pb2 = _profile(b) if b != "heart" else None
        out = []
        for i in range(SHAPE_N):
            if a == "heart":
                ax, ay = ha[i]
            else:
                ang, s = pa2[i]
                ax, ay = cx + rx * s * math.cos(ang), cy + ry * s * math.sin(ang)
            if b == "heart":
                bx, by = hb[i]
            else:
                ang, s = pb2[i]
                bx, by = cx + rx * s * math.cos(ang), cy + ry * s * math.sin(ang)
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        return out

    pa, pb = _profile(a), _profile(b)
    _ys = {"squint": 0.30, "cat": 0.52}
    ya = _ys.get(a, 1.0)
    yb = _ys.get(b, 1.0)
    ys = ya + (yb - ya) * t
    pts: List[Tuple[float, float]] = []
    for i in range(SHAPE_N):
        ang, sa = pa[i]
        _, sb = pb[i]
        s = sa + (sb - sa) * t
        pts.append((cx + rx * s * math.cos(ang),
                    cy + ry * s * ys * math.sin(ang)))
    return pts


def _draw_bond_ring(dl: DrawList, amount: float, clock: float,
                    cx: float, cy: float) -> None:
    """
    共振光环：从眼睛中心向四周扩散的两道同心环。
    用多边形拼（Arduino 上可以用 fillCircle 叠画）。
    """
    for k in (0, 1):
        phase = (clock * 0.9 + k * 0.5) % 1.0
        # 半径收到 0.92 以内：超过 1.0 的部分在真实圆屏上根本不存在，
        # 让环"跑出屏幕"在 PC 预览里看着是自然的，上机就变成像素消失了。
        r = 0.42 + 0.50 * phase
        alpha = (1.0 - phase) * amount
        if alpha < 0.05 or r > 0.93:
            continue
        ring_pts = []
        n = 40
        for i in range(n):
            a = 2.0 * math.pi * i / n
            ring_pts.append((cx + r * math.cos(a), cy + r * math.sin(a) * 0.92))
        dl.add(("bond_ring", ring_pts, alpha))


def _draw_sparkles(dl: DrawList, hype: float, clock: float, seed: int) -> None:
    """
    彩蛋期间的爆发星光。

    分两拨：
      · 近处一圈大的（慢慢转），负责"热闹"
      · 远处一圈小的（转得快），负责"闪"
    数量从 14 提到 30，尺寸也放大 —— 之前太稀疏，几乎看不出来。
    """
    rng = random.Random(seed)
    for i in range(30):
        big = i < 18
        a0 = rng.uniform(0, math.tau)
        # 起始半径抬到眼睛外面：星星眼本身已经很大（~0.29 半径），
        # 星火压在上面会糊成一片，看不出是"周围的星光"
        r0 = rng.uniform(0.30, 0.46) if big else rng.uniform(0.46, 0.54)
        speed = rng.uniform(0.35, 0.8) if big else rng.uniform(0.9, 1.8)
        phase = (clock * speed + rng.uniform(0, 1)) % 1.0
        rr = 0.58 + r0 * phase
        cx = math.cos(a0) * rr
        cy = math.sin(a0) * rr * 0.96
        base = 0.042 if big else 0.021
        # 淡入淡出：两端小、中间大，不会突然出现/消失
        env = math.sin(math.pi * phase) ** 0.6
        size = base * env * hype
        if size > 0.002 and rr < 0.97:
            dl.add(("spark", cx, cy, size, a0 + clock * speed, phase))


# ---------------------------------------------------------------------------
# 自检：不依赖任何 GUI，直接跑一小段看数值是否合理
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sm = EyeStateMachine()
    print("screen:", SCREEN)
    for i in range(60):
        sm.update(1 / 60)
    print("idle frame items:", len(sm.build_frame().items))

    sm.tap(HEAVY)
    ys = []
    for i in range(50):
        sm.update(1 / 60)
        ys.append(round(sm._tap_channels()[0], 3))
    print("heavy tap dip curve:", ys[:24])

    sm.press_grid("t")
    cols = []
    for i in range(30):
        sm.update(1 / 60)
        cols.append(round(sm.pos_col, 2))
    print("grid to left col:", cols[:16])

    sm.press_grid("b")
    sm.update(1 / 60)
    sm.press_grid("h")
    sm.update(1 / 60)
    r = sm.press_grid("t")
    print("swipe B->H->T detected:", r, "hype:", sm.hype)
