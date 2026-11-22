# Duetto — 桌面听歌搭子（表情原型）

把听歌时的敲击与手势，变成桌面上一个有反应的活物。

硬件还没打样，先用 Python 把交互和形象调好，代码按"可直译成 Arduino"的方式组织。

**全程纯代码绘制，不加载任何图片。**

---

## 两个版本

| 版本 | 命令 | 说明 |
|---|---|---|
| **小猫头鹰**（形象版） | `python3 duetto_sim.py` | 带外圈灯带，模拟双人视界 |
| 抽象眼睛（几何版） | `python3 eyes_sim.py` | 最早的原型，纯眼睛 |

两个版本共用同一套动画状态机 `eyes_core.py`，只是渲染层不同。

## 按键

| 交互 | 按键 | 猫头鹰的反应 |
|---|---|---|
| 轻敲 | `F` / `Space` / `←` | 眯眼点头，眉羽微扬 |
| 重击 | `J` / `Enter` / `→` | 沉得更低、眉羽压低、眼睛瞪大 |
| 9 宫格 | `T Y U` / `G H J` / `B N M` | **整只鸟转头**（不是转眼）+ 平移 |
| 上滑彩蛋 | 1 秒内 `B/N/M` → `G/H/J` → `T/Y/U` | 笑眼、张嘴、星星爆发 |
| 退出 | `Esc` | |

---

## 形象设计：为什么是猫头鹰，眼睛怎么画

设计前先做了竞品调研，完整报告在 [`docs/duetto-eye-design-research.md`](docs/duetto-eye-design-research.md)。几条直接改变了设计的结论：

### 1. 不画「眼白 + 瞳孔」，画实心发光块

Cozmo 和 Vector 都是单色填充形状，Anki 直接把 Eve 的眼睛搬过来。**小屏上画眼白会立刻显脏、显小**。所以猫头鹰的眼睛是两块实心琥珀色，没有眼白、没有瞳圈、没有高光点。

### 2. 猫头鹰的眼睛不能转，所以滑动是「转头」

真实猫头鹰的眼球固定在眼窝里，只能转头。这反而帮了忙——**舵机有了存在理由**：

- 左右滑动 → `head_x` 平移 + `head_tilt` 旋转（整张脸一起动）
- 屏幕上不画瞳孔位移，眼睛本身不动

### 3. 一次只动一条边

`eye_shape()` 里三个独立通道，不同时变形：

| 通道 | 效果 | 用于 |
|---|---|---|
| `eye_open` | 上缘高度 | 眯眼 / 瞪大 / 眨眼 |
| `smile` | 上缘中段上凸 | 笑眼 |
| `angry` | 上缘整体倾斜 | 专注 / 生气 |

### 4. 猫头鹰靠三样东西立住

- **面盘**：带心形凹口的浅色区域，最标志性
- **眉羽**：3 根由粗到细的羽片叠出层次。用一条实心线会读成人类的眉毛
- **喙**：倒三角，把两只眼睛连成一张脸

眼距约 1.3 倍眼宽（调研建议 0.6–0.8 倍间隙），比人的眼睛更宽更靠上。

### 5. 配色

| 用途 | 值 |
|---|---|
| 屏幕底 | `#14100C` 暖褐黑 |
| 眼睛 | `#FFD98A` 琥珀 |
| 眉羽 | `#6C543E` |
| 喙 | `#E29646` |
| 灯带（我） | `#FFB86B` 暖 |
| 灯带（对方） | `#6FD3FF` 冷 |

---

## 双人视界（核心差异点）

调研里**没有在竞品中发现同类做法**：灯带左右各 12 颗独立寻址，左边是「我」，右边是「对方」。

| 状态 | 灯带 |
|---|---|
| 待机 | 左右同色暖白（当作"面盘"） |
| 看向左侧 | 左暖亮 + 右冷暗 |
| 看向右侧 | 左冷暗 + 右暖亮 |
| 彩蛋 | 左右融为一体（暖黄高亮） |

`duetto_states.light_for()` 算颜色。屏幕和灯带是**两条独立通道**，表情不等灯带和舵机到位——这是 Disney 研究里 "show channel" 的思路。

---

## 文件结构

```
eyes_core.py            动画状态机（纯标准库，无绘图依赖）
eyes_render.py          几何版渲染（Tkinter / Pillow）
eyes_sim.py             几何版模拟器

duetto_owl.py           猫头鹰形象（几何 + 配色）
duetto_render.py        猫头鹰渲染（Tkinter / Pillow）
duetto_states.py        状态机 → 猫头鹰参数 + 灯带颜色
duetto_sim.py           猫头鹰模拟器（带灯带）

render_frames.py        无头预览，导出 GIF / 拼图
docs/                   竞品调研报告
arduino/duetto_owl/     猫头鹰 Arduino 版
arduino/eyes_arduino/   几何版 Arduino 版
```

三层架构，目的是让动画逻辑能原样搬到 Arduino：

```
输入（键盘 / 按钮 / 触摸焊盘）
   ↓
eyes_core.EyeStateMachine     只有浮点数，update(dt) + build_frame()
   ↓  duetto_states  ← 适配层（几何版跳过这一层）
duetto_owl.OwlParams          一帧的形象参数
   ↓
duetto_render                  Tkinter | Pillow | Arduino GFX
```

坐标全部归一化到 **-1..1**（原点居中、半径 1 = 屏幕半径），换分辨率只改 `SCREEN`。

---

## 无头预览（不开窗口看效果）

```bash
python3 render_frames.py            # 几何版 GIF
python3 preview_all.py              # 猫头鹰版 GIF + 对照图
```

依赖 Pillow。适合没有显示器的环境，或者想直接发图给人看。

---

## Arduino

已装好：

- **Arduino IDE 2.2.0** — `/Applications/Arduino IDE.app`
- **arduino-cli 1.6.0** — `~/bin/arduino-cli`（可命令行编译验证）

代码在 [`arduino/duetto_owl/duetto_owl.ino`](arduino/duetto_owl/duetto_owl.ino)，逻辑与 Python 版逐行对应，文件末尾有对照表。

硬件建议：

- **屏幕**：GC9A01 240×240 圆形 IPS
- **主控**：ESP32-S3（RP2040 也能跑，但更慢）
- **灯带**：WS2812B × 24（左右各 12）
- **舵机**：SG90 / MG90S，左右摆头

> ⚠️ **帧率是硬约束**：Adafruit 用同一块 GC9A01A 实测，ESP32-S3 画**双眼只有约 20 FPS**（单眼 39 FPS）。所以代码按 20 FPS 设计，单次动作控制在 8 帧内。提速建议见 .ino 文件末尾。

---

## 调参速查

| 位置 | 作用 |
|---|---|
| `eyes_core.py` 的 `LIGHT_TAP` / `HEAVY_TAP` | 敲击的时长、下沉、压扁、回弹 |
| `eyes_core.py` 的 `SwipeDetector.window` | 上滑判定时间窗口 |
| `duetto_owl.py` 的 `OwlParams` | 眼睛大小、眼距、眉羽 |
| `duetto_owl.py` 的 `eye_shape()` | 眼睛的形变规则 |
| `duetto_states.py` 的 `state_to_owl()` | 状态机 → 表情的映射强度 |
| `duetto_states.py` 的 `light_for()` | 灯带颜色逻辑 |
