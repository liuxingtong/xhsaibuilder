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

    形状个性
    --------
    openness     竖向张开系数
    width        横向系数
    pupil_ratio  瞳孔 / 眼眶 半径比。越小虹膜越宽
    marquee      虹膜里高光跑马灯的强度
    gaze_kick    看向侧面时，虹膜往那个方向"被推"的比例
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
    openness: float = 1.00
    width: float = 1.00
    pupil_ratio: float = 0.52
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
    glow=(34, 36, 50), eye_white=(250, 246, 236),
    iris_outer=(238, 152, 44), iris_inner=(104, 58, 24),
    pupil=(13, 11, 17), lid=(46, 36, 30),
    sheen=(255, 255, 255), needle=(255, 232, 190),
    spark=(255, 214, 120), tint=(255, 196, 132),
    openness=1.00, pupil_ratio=0.58, marquee=0.16,
))

#: 青碧：低饱和冷色，安静耐看。在"往上看"时出现。
_REG_MINT = _reg(EyeDesign(
    key="mint", name="青碧 · dusk",
    glow=(24, 38, 44), eye_white=(236, 246, 246),
    iris_outer=(54, 176, 178), iris_inner=(16, 58, 68),
    pupil=(9, 15, 19), lid=(28, 40, 44),
    sheen=(255, 255, 255), needle=(200, 240, 236),
    spark=(150, 240, 220), tint=(140, 226, 220),
    openness=1.08, pupil_ratio=0.6, marquee=0.20,
))

#: 樱粉：手绘水彩感，最"可爱"的一套。往右看 / 共振预备时用。
_REG_ROSE = _reg(EyeDesign(
    key="rose", name="樱粉 · bloom",
    glow=(46, 30, 38), eye_white=(252, 240, 240),
    iris_outer=(234, 124, 140), iris_inner=(92, 38, 56),
    pupil=(17, 9, 15), lid=(54, 32, 42),
    sheen=(255, 255, 255), needle=(255, 226, 230),
    spark=(255, 190, 210), tint=(255, 158, 180),
    openness=1.03, pupil_ratio=0.54, marquee=0.28,
))

#: 霜蓝：往左看。偏冷偏亮，和樱粉形成明确的方向对比。
_REG_FROST = _reg(EyeDesign(
    key="frost", name="霜蓝 · frost",
    glow=(22, 32, 50), eye_white=(240, 246, 255),
    iris_outer=(96, 168, 240), iris_inner=(24, 46, 90),
    pupil=(9, 13, 25), lid=(24, 34, 54),
    sheen=(255, 255, 255), needle=(214, 232, 255),
    spark=(160, 216, 255), tint=(132, 190, 255),
    openness=1.05, pupil_ratio=0.57, marquee=0.24,
))

#: 靛紫：往下看 / 害羞。瞳孔占比最大，看起来安静专注。
_REG_INDIGO = _reg(EyeDesign(
    key="indigo", name="靛紫 · しんしん",
    glow=(28, 26, 46), eye_white=(242, 240, 252),
    iris_outer=(126, 114, 210), iris_inner=(28, 22, 60),
    pupil=(11, 9, 19), lid=(32, 28, 52),
    sheen=(255, 255, 255), needle=(224, 220, 255),
    spark=(196, 180, 255), tint=(178, 168, 255),
    openness=0.95, pupil_ratio=0.63, marquee=0.18,
))

#: 暗金：主高光很弱，靠虹膜里的跑马灯提供亮度 —— 对应 LOVOT 的「ロンロン」。
_REG_EMBER = _reg(EyeDesign(
    key="ember", name="暗金 · ロンロン",
    glow=(40, 32, 22), eye_white=(248, 238, 220),
    iris_outer=(192, 134, 54), iris_inner=(50, 32, 20),
    pupil=(9, 7, 7), lid=(38, 28, 20),
    sheen=(255, 246, 228), needle=(255, 220, 168),
    spark=(255, 208, 132), tint=(255, 186, 118),
    openness=0.98, pupil_ratio=0.6, marquee=0.44,
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

        # ---- 眼睛本体 ----------------------------------------------------
        eye_boxes = []
        for side in (-1, 1):
            # 外眼角跟着瞪大微调，避免惊讶时两眼光学中心散开
            cx = eye_dx + side * spread
            cy = center_y
            near = 1.0 if side * col_f > 0 else 0.0
            size_k = 1.0 + (0.12 * near - 0.07 * (1 - near)) * abs(col_f)
            rx_, ry_ = eye_rx * size_k, eye_ry * size_k

            # 共振：眼睛直接变成心形（最醒目、最不会看错的表情）
            if bond_env > 0.03:
                k = clamp(bond_env, 0.0, 1.0)
                circle = ellipse_polygon(cx, cy, rx_ * 1.06, ry_ * 1.06, 40)
                heart = _heart_poly(cx, cy + ry_ * 0.02, rx_ * 0.98, ry_ * 1.02, 40)
                blended = [(lerp(circle[i][0], heart[i][0], k),
                            lerp(circle[i][1], heart[i][1], k))
                           for i in range(40)]
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

        这个顺序就是移植到 Arduino 时的绘制顺序，别调换：
          1 眼白 glow   2 眼白本体   3 虹膜外圈   4 虹膜内圈   5 瞳孔
          6 瞳孔内暗部  7 主高光     8 次高光     9 内眼角反光（可选）
         10 上皮褶      11 弯眼笑弧  12 眼皮（压扁时）

        坐标约束：所有圆都不超过 eye_white 的边界，所以眼睛永远是
        "一个完整的圆"，不会像之前那样被眼皮切掉半张脸。
        """
        col = rgb_key

        # 1) 眼白外圈：比背景亮一点点，做出"球体嵌进屏幕"
        if d.glow != d.eye_white:
            dl.add(Ellipse(cx, cy, rx * 1.10, ry * 1.10, mood=col(d.glow)))

        # 2) 眼白本体（不是白，是设计主色的淡版）
        dl.add(Ellipse(cx, cy, rx, ry, mood=col(d.eye_white)))

        # ---- 视线的实现 ---------------------------------------------
        # **只让虹膜+瞳孔在眼白里平移**，眼白本身不动。
        # 这是"眼睛看东西"和"眼睛整体在滑"的分界线，差别非常大。
        # 偏移量留出眼眶半径，保证虹膜不会整个跑出眼白。
        #
        # 关键约束：虹膜直径是眼眶的 0.88，所以左右最多只能挪 12%，
        # 上下更少 —— 挪多了会一边露出背景，看起来像眼球破了。
        pr = clamp(d.pupil_ratio * (1.0 - 0.16 * surprise), 0.30, 0.90)
        prx, pry = rx * pr, ry * pr
        ir = 0.88
        max_sx = max(0.0, rx * (1.0 - ir))
        max_sy = max(0.0, ry * (1.0 - ir))
        gxc = cx + clamp(gx, -1.0, 1.0) * max_sx
        gyc = cy + clamp(gy, -1.0, 1.0) * max_sy

        # 3) 虹膜外圈 + 4) 内圈
        #    虹膜是一个"环"：外圈深色底 + 内圈偏亮 → 就有了渐变
        dl.add(Ellipse(gxc, gyc, rx * 0.88, ry * 0.88, mood=col(d.iris_outer)))

        # 5) 虹膜内圈（靠瞳孔那一圈，"被光照透"的亮边）
        dl.add(Ellipse(gxc, gyc, rx * 0.80, ry * 0.80, mood=col(d.iris_inner)))

        # 6) 虹膜内亮边：先铺一圈比内圈更亮的细环，再让瞳孔盖住它的中段。
        #    露出来的那一圈就是"被光照透"的边 —— 深度感主要靠它。
        rim = mix_rgb(d.iris_inner, d.iris_outer, 0.62)
        dl.add(Ellipse(gxc, gyc, prx * 1.10, pry * 1.10, mood=col(rim)))

        # 7) 瞳孔。**瞳孔必须够大**，否则眼睛里"没有洞"，
        #    看起来像一颗珠子而不是眼睛。
        dl.add(("pupil", gxc, gyc, prx, pry))

        # 7) 主高光：正圆（椭圆做出来的高光看着"糊"），位置略偏上有神。
        #    高光跟着瞳孔走，但只跟一部分 —— 光是从环境来的，不完全跟眼睛走。
        hx = lerp(cx, gxc, 0.55)
        hy = lerp(cy, gyc, 0.55)
        gr = prx * 0.30
        dl.add(Ellipse(hx + prx * 0.36, hy - pry * 0.42, gr, gr,
                       mood=col(d.sheen)))

        # 8) 次高光：右下角一个小圆点，给眼睛"玻璃质感"
        gr2 = prx * 0.13
        dl.add(Ellipse(hx + prx * 0.08, hy + pry * 0.54, gr2, gr2,
                       mood=col(d.sheen)))

        # 9) 下缘反光（LOVOT 那种"底下有一圈月光"）。
        #    角度只取 50°..130°（正下方一小段），画细一点 ——
        #    取太大就成了横贯眼睛的一道亮杠，读起来像嘴。
        dl.add(("crescent", gxc, gyc, prx * 1.13, pry * 1.13,
                col(mix_rgb(d.sheen, d.iris_outer, 0.55)), 50.0, 130.0))

        # 11) 弯眼笑：开心时眼睛下缘浮起一道向上弯的弧。
        #     弧的半径比瞳孔小、位置比瞳孔低，所以它是**画在瞳孔内部的下半部**，
        #     不会横穿整只眼睛变成一条白杠（这是上一版的毛病）。
        if joy > 0.03:
            dl.add(("smile_arc", gxc, gyc, prx * 0.92, pry * 0.92, joy,
                    col(d.sheen)))

        # 12) 眼皮：只在压扁时出现，画在最上面
        if squash > 0.03:
            _draw_lid(dl, cx, cy, rx, ry, squash, d)

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


def _heart_poly(cx: float, cy: float, rx: float, ry: float,
                n: int = 40) -> List[Tuple[float, float]]:
    """
    心形轮廓（共振时眼睛变成心）。

    用经典极坐标心形曲线：
        x = 16 sin^3(t)
        y = -(13 cos t - 5 cos2t - 2 cos3t - cos4t)
    归一化到约 ±1，再乘 rx/ry。y 轴向下，所以整体不翻转。
    """
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        hx = 16.0 * math.sin(t) ** 3
        hy = -(13.0 * math.cos(t) - 5.0 * math.cos(2 * t)
               - 2.0 * math.cos(3 * t) - math.cos(4 * t))
        # 心形曲线的自然范围约 ±17 / ±17（上下略不对称），统一缩放到 ±1
        pts.append((cx + rx * (hx / 17.0), cy + ry * (hy / 17.0)))
    return pts


def _draw_bond_ring(dl: DrawList, amount: float, clock: float,
                    cx: float, cy: float) -> None:
    """
    共振光环：从眼睛中心向四周扩散的两道同心环。
    用多边形拼（Arduino 上可以用 fillCircle 叠画）。
    """
    for k in (0, 1):
        phase = (clock * 0.9 + k * 0.5) % 1.0
        r = 0.46 + 0.50 * phase
        alpha = (1.0 - phase) * amount
        if alpha < 0.05:
            continue
        ring_pts = []
        n = 40
        for i in range(n):
            a = 2.0 * math.pi * i / n
            ring_pts.append((cx + r * math.cos(a), cy + r * math.sin(a) * 0.92))
        dl.add(("bond_ring", ring_pts, alpha))


def _draw_sparkles(dl: DrawList, hype: float, clock: float, seed: int) -> None:
    """彩蛋期间的爆发光芒 / 小星星。"""
    rng = random.Random(seed)
    n = 14
    for i in range(n):
        a0 = rng.uniform(0, math.tau)
        r0 = rng.uniform(0.40, 0.78)
        speed = rng.uniform(0.55, 1.4)
        phase = (clock * speed + rng.uniform(0, 1)) % 1.0
        rr = 0.30 + r0 * phase
        cx = math.cos(a0) * rr
        cy = math.sin(a0) * rr
        size = 0.032 * (1.0 - phase) * hype
        if size > 0.002:
            dl.add(("spark", cx, cy, size, a0, phase))


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
