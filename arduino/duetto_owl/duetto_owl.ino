/*
 * duetto_owl.ino — Duetto 猫头鹰形象（Arduino 版）
 *
 * 与 Python 版严格对应：
 *   duetto_owl.py     →  buildOwlFrame() / eyeShape() / 眉羽 / 喙
 *   duetto_states.py  →  stateToOwl()
 *   eyes_core.py      →  UpdateState() / SwipeFeed() / TapChannels()
 *
 * 硬件
 *   - GC9A01 1.28" 圆形 IPS，240×240，SPI
 *   - 灯带：WS2812B × N，绕屏幕一圈（左右各一半 = 双人视界）
 *   - 舵机：SG90 / MG90S，左右摆头
 *   - 输入：两个敲击按钮 + 9 宫格（先串口模拟）
 *
 * 依赖库
 *   - TFT_eSPI
 *   - Adafruit NeoPixel
 *   - ESP32Servo（ESP32）或 Servo（RP2040）
 *
 * 重要：帧率
 *   Adafruit 在同一块 GC9A01A 上实测 ESP32-S3 画双眼只有约 20 FPS。
 *   本文件按 20 FPS 设计，单次动作控制在 8 帧内说完。
 *   → 用 spr.setColorDepth(16) + 脏矩形局部推送，别整屏重画。
 */

#include <TFT_eSPI.h>
#include <Adafruit_NeoPixel.h>
#include <ESP32Servo.h>
#include <math.h>

// ===========================================================================
// 屏幕
// ===========================================================================

#define SCREEN_W 240
#define SCREEN_H 240
static const float CXF = SCREEN_W / 2.0f;
static const float CYF = SCREEN_H / 2.0f;
static const float RF = SCREEN_W / 2.0f;   // 归一化半径 = 1.0

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite spr = TFT_eSprite(&tft);

// ===========================================================================
// 灯带（双人视界）
// ===========================================================================

#define LED_PIN 4
#define LED_COUNT 24            // 左右各 12
Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

// ===========================================================================
// 舵机
// ===========================================================================

#define SERVO_PIN 13
Servo headServo;
static const int SERVO_CENTER = 90;

// ===========================================================================
// 配色（RGB565）—— 对应 duetto_owl.py
// ===========================================================================

#define C_BG        0x1061   // 暖褐黑 #14100C
#define C_DISC      0x39A4   // 面盘暗部 #3A2D22
#define C_EYE       0xFE71   // 琥珀 #FFD98A
#define C_BROW      0x6C2B   // 眉羽 #6C543E
#define C_BROWSHADE 0x59A5
#define C_BEAK      0xE4AC   // #E29646
#define C_BEAKDARK  0xAA6E   // #AA682C
#define C_MOUTH     0x2942   // 口腔深色 #341E12

// ===========================================================================
// 缓动 / 弹簧
// ===========================================================================

static inline float clampf(float x, float lo, float hi) {
  return x < lo ? lo : (x > hi ? hi : x);
}
static inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
static inline float easeOutCubic(float t) {
  t = clampf(t, 0, 1); return 1.0f - powf(1.0f - t, 3.0f);
}
static inline float easeInOutSine(float t) {
  t = clampf(t, 0, 1); return -(cosf(M_PI * t) - 1.0f) / 2.0f;
}

struct Spring {
  float pos = 0, vel = 0, target = 0, k = 190, d = 24;
  void step(float dt) {
    float a = (target - pos) * k - vel * d;
    vel += a * dt;
    pos += vel * dt;
  }
};

// ===========================================================================
// 敲击参数（对应 eyes_core.LIGHT_TAP / HEAVY_TAP）
// ===========================================================================

struct TapCfg { float dur, dip, squash, pop, bounce; };
static const TapCfg LIGHT_TAP = {0.38f, 0.26f, 0.46f, 1.10f, 0.42f};
static const TapCfg HEAVY_TAP = {0.80f, 0.40f, 0.56f, 1.60f, 0.75f};

enum TapKind { TAP_LIGHT, TAP_HEAVY };

struct TapState {
  TapKind kind = TAP_LIGHT;
  float t = 0;
  bool active = false;
};

// ===========================================================================
// 9 宫格
// ===========================================================================

struct GridKey { char key; int col, row; };
static const GridKey KEY_GRID[] = {
  {'t', -1, -1}, {'y', 0, -1}, {'u', 1, -1},
  {'g', -1,  0}, {'h', 0,  0}, {'j', 1,  0},
  {'b', -1,  1}, {'n', 0,  1}, {'m', 1,  1},
};
static const int KEY_COUNT = 9;

// ===========================================================================
// 上滑检测
// ===========================================================================

struct SwipeSample { unsigned long t; int row; };
static const int HIST_MAX = 16;
SwipeSample hist[HIST_MAX];
int histLen = 0;
static const unsigned long SWIPE_WINDOW = 1000;
static const unsigned long SWIPE_GAP = 550;

void swipeReset() { histLen = 0; }

bool swipeFeed(unsigned long now, int row) {
  if (histLen >= HIST_MAX) {
    for (int i = 1; i < HIST_MAX; i++) hist[i - 1] = hist[i];
    histLen--;
  }
  hist[histLen++] = {now, row};

  int start = 0;
  while (start < histLen && (now - hist[start].t) > SWIPE_WINDOW) start++;
  if (start > 0) {
    for (int i = start; i < histLen; i++) hist[i - start] = hist[i];
    histLen -= start;
  }

  int stage = 0;
  unsigned long stageT = 0;
  for (int i = 0; i < histLen; i++) {
    int r = hist[i].row;
    unsigned long t = hist[i].t;
    if (stage == 0) {
      if (r == 1) { stage = 1; stageT = t; }
    } else if (stage == 1) {
      if (r == 0 && (t - stageT) <= SWIPE_GAP) stage = 2;
      else if (r == 1) stageT = t;
    } else {
      if (r == -1 && (t - stageT) <= SWIPE_WINDOW) { swipeReset(); return true; }
      if (r == 1) { stage = 1; stageT = t; }
      else if (r == 0) stageT = t;
    }
  }
  return false;
}

// ===========================================================================
// 状态
// ===========================================================================

struct State {
  Spring col, row;
  TapState tap;
  TapKind lastKind = TAP_LIGHT;
  int tapCount = 0;
  float clock = 0;

  float blinkIn = 3.0f, blinkT = -1;
  static constexpr float BLINK_LEN = 0.14f;

  float hype = 0, hypeT = 0;
  static constexpr float HYPE_LEN = 2.6f;
  uint32_t seed = 1;

  float idleX = 0, idleNext = 2.5f;
};

State S;

// 猫头鹰参数（对应 duetto_owl.OwlParams）
struct OwlParams {
  float headX = 0, headY = 0, headTilt = 0;
  float eyeRx = 0.185f, eyeRy = 0.225f;
  float eyeOpen = 1.0f, eyeGap = 1.0f;
  float smile = 0, angry = 0;
  float browLift = 0, browAsym = 0, browFlare = 0;
  float beakOpen = 0;
  float hype = 0, clock = 0;
  uint32_t seed = 1;
};

// ===========================================================================
// 输入事件
// ===========================================================================

void doTap(TapKind kind) {
  if (kind == TAP_HEAVY || !S.tap.active) {
    S.tap.kind = kind;
    S.tap.t = 0;
    S.tap.active = true;
    S.tapCount++;
    S.lastKind = kind;
  } else {
    S.tapCount++;
    float boost = min(0.35f, 0.08f * S.tapCount);
    S.tap.t = 0;
    const TapCfg &c = (S.lastKind == TAP_HEAVY) ? HEAVY_TAP : LIGHT_TAP;
    S.tap.dip = c.dip * (1 + boost);
    S.tap.squash = min(0.9f, c.squash * (1 + boost));
  }
}

void triggerHype() {
  S.hype = 1.0f;
  S.hypeT = 0;
  S.seed = micros();
}

void pressGrid(int idx) {
  if (idx < 0 || idx >= KEY_COUNT) return;
  const GridKey &g = KEY_GRID[idx];
  S.col.target = g.col;
  S.row.target = g.row;

  if (swipeFeed(millis(), g.row)) { triggerHype(); return; }

  if (!S.tap.active) {
    S.tap.kind = TAP_LIGHT;
    S.tap.t = 0;
    S.tap.active = true;
    S.tap.dip = LIGHT_TAP.dip * 0.22f;
    S.tap.squash = LIGHT_TAP.squash * 0.22f;
  }
}

void releaseGrid() {
  S.col.target = 0;
  S.row.target = 0;
}

// ===========================================================================
// 更新
// ===========================================================================

void updateState(float dt) {
  if (dt > 0.05f) dt = 0.05f;
  S.clock += dt;
  S.col.step(dt);
  S.row.step(dt);

  if (S.tap.active) {
    S.tap.t += dt;
    float dur = (S.tap.kind == TAP_HEAVY) ? HEAVY_TAP.dur : LIGHT_TAP.dur;
    if (S.tap.t >= dur) { S.tap.active = false; S.tapCount = 0; }
  }

  if (S.blinkT >= 0) {
    S.blinkT += dt;
    if (S.blinkT >= State::BLINK_LEN) {
      S.blinkT = -1;
      S.blinkIn = random(300, 700) / 100.0f;
    }
  } else {
    S.blinkIn -= dt;
    if (S.blinkIn <= 0) S.blinkT = 0;
  }

  if (S.hype > 0) {
    S.hypeT += dt;
    if (S.hypeT >= State::HYPE_LEN) { S.hype = 0; S.hypeT = 0; }
  }

  S.idleNext -= dt;
  if (S.idleNext <= 0) {
    S.idleNext = random(300, 600) / 100.0f;
    S.idleX = random(-40, 40) / 1000.0f;
  }
}

// 敲击通道（对应 eyes_core._tap_channels）
void tapChannels(float &dip, float &squash, float &pop) {
  if (!S.tap.active) { dip = 0; squash = 0; pop = 1.0f; return; }
  const TapCfg &c = (S.tap.kind == TAP_HEAVY) ? HEAVY_TAP : LIGHT_TAP;
  float p = clampf(S.tap.t / c.dur, 0, 1);

  if (S.tap.kind == TAP_HEAVY) {
    if (p < 0.22f) {
      float k = easeOutCubic(p / 0.22f);
      dip = c.dip * k; squash = c.squash * k;
    } else if (p < 0.55f) {
      float k = easeInOutSine((p - 0.22f) / 0.33f);
      dip = lerpf(c.dip, -c.dip * c.bounce, k);
      squash = lerpf(c.squash, -c.squash * 0.18f, k);
    } else {
      float k = easeOutCubic((p - 0.55f) / 0.45f);
      dip = lerpf(-c.dip * c.bounce, 0, k);
      squash = lerpf(-c.squash * 0.18f, 0, k);
    }
    pop = 1.0f + (c.pop - 1.0f) * sinf(M_PI * min(1.0f, p * 1.25f));
  } else {
    if (p < 0.35f) {
      float k = easeOutCubic(p / 0.35f);
      dip = c.dip * k; squash = c.squash * k;
    } else {
      float k = easeOutCubic((p - 0.35f) / 0.65f);
      dip = lerpf(c.dip, -c.dip * c.bounce, k);
      squash = lerpf(c.squash, 0, k);
    }
    pop = 1.0f + (c.pop - 1.0f) * sinf(M_PI * p);
  }
  if (p > 0.92f) {
    float f = (p - 0.92f) / 0.08f;
    dip *= (1 - f);
    squash *= (1 - f);
  }
}

// 状态 → 猫头鹰参数（对应 duetto_states.state_to_owl）
void stateToOwl(OwlParams &o) {
  float dip, squash, pop;
  tapChannels(dip, squash, pop);

  float blink = 0;
  if (S.blinkT >= 0) blink = sinf(M_PI * clampf(S.blinkT / State::BLINK_LEN, 0, 1));

  float colF = S.col.pos, rowF = S.row.pos;

  // 转头代替转眼
  o.headX = colF * 0.115f;
  o.headTilt = colF * 9.0f;
  o.headY = rowF * 0.10f + dip * 0.85f;

  float lid = clampf(squash * 1.55f + blink * 1.25f, 0, 1);
  float open = 1.0f - lid;
  if (pop > 1.0f) open = 1.0f + (pop - 1.0f) * 0.55f;
  o.eyeOpen = open;

  o.browLift = clampf(dip * 1.9f, -0.10f, 0.14f);
  o.browAsym = -colF * 0.030f;
  o.browFlare = clampf(S.hype, 0, 1);

  o.smile = clampf(S.hype * 0.85f, 0, 1);
  if (S.lastKind == TAP_HEAVY && dip > 0.05f) {
    o.smile = 0;
    o.angry = clampf(dip * 1.2f, 0, 0.9f);
  }

  o.beakOpen = clampf(S.hype * 1.1f, 0, 1);
  o.hype = S.hype;
  o.clock = S.clock;
  o.seed = S.seed;
}

// ===========================================================================
// 绘制
// ===========================================================================

// 屏幕中心坐标系 → 像素
static inline int PX(float x, float ox) { return (int)lroundf(CXF + x * RF + ox * RF); }
static inline int PY(float y, float oy) { return (int)lroundf(CYF + y * RF + oy * RF); }

// 眼睛轮廓（对应 duetto_owl.eye_shape）
// 返回顶点数，pts 写入屏幕像素坐标
int eyeShape(float cx, float cy, float rx, float ry, float openK,
             float smile, float angry, int16_t *px, int16_t *py, int maxPts) {
  float k = clampf(openK, 0, 1);
  if (k <= 0.05f) return 0;

  float yTop = -1.0f + (1.0f - k) * 1.60f;
  float yBot = 1.0f;

  int n = 22;
  int idx = 0;
  // 下半圈
  for (int i = 0; i <= n && idx < maxPts; i++) {
    float t = (float)i / n;
    float a = M_PI * t;
    px[idx] = PX(cx + rx * cosf(a), 0);
    py[idx] = PY(cy + ry * sinf(a) * yBot, 0);
    idx++;
  }
  // 上缘
  for (int i = 0; i <= n && idx < maxPts; i++) {
    float t = (float)i / n;
    float xn = -1.0f + 2.0f * t;
    float ell = -sqrtf(max(0.0f, 1.0f - xn * xn));
    float y = max(ell, yTop);
    y -= smile * 0.30f * sinf(M_PI * t);
    y += angry * 0.28f * xn * (1.0f - 0.4f * k);
    px[idx] = PX(cx + rx * xn, 0);
    py[idx] = PY(cy + ry * min(y, yBot), 0);
    idx++;
  }
  return idx;
}

// 旋转（对应 python 的 place()）
void rotatePt(float x, float y, float deg, float ox, float oy, float &rx, float &ry) {
  float r = deg * M_PI / 180.0f;
  float c = cosf(r), s = sinf(r);
  rx = x * c - y * s + ox;
  ry = x * s + y * c + oy;
}

void fillPoly(int16_t *xs, int16_t *ys, int n, uint16_t color) {
  // 用 TFT_eSprite 的 fillTriangle 扇形拼（n 点多边形，凸/星形都适用）
  if (n < 3) return;
  int x0 = xs[0], y0 = ys[0];
  for (int i = 1; i < n - 1; i++)
    spr.fillTriangle(x0, y0, xs[i], ys[i], xs[i + 1], ys[i + 1], color);
}

void drawBeak(const OwlParams &o, float tiltRad, float hx, float hy) {
  float bx = 0, by = 0.300f;
  float openK = clampf(o.beakOpen, 0, 1);

  auto place = [&](float x, float y, float &ox, float &oy) {
    rotatePt(x, y, o.headTilt, hx, hy, ox, oy);
  };

  int16_t xs[8], ys[8];
  if (openK > 0.02f) {
    float gap = 0.030f + 0.075f * openK;
    float ax, ay;
    // 口腔
    place(-0.062f, by, ax, ay);     xs[0] = PX(ax, 0); ys[0] = PY(ay, 0);
    place( 0.062f, by, ax, ay);     xs[1] = PX(ax, 0); ys[1] = PY(ay, 0);
    place( 0.0f, by + gap + 0.035f, ax, ay); xs[2] = PX(ax, 0); ys[2] = PY(ay, 0);
    fillPoly(xs, ys, 3, C_MOUTH);

    // 上喙
    place(-0.082f, by, ax, ay);     xs[0] = PX(ax, 0); ys[0] = PY(ay, 0);
    place( 0.082f, by, ax, ay);     xs[1] = PX(ax, 0); ys[1] = PY(ay, 0);
    place( 0.0f, by + gap, ax, ay); xs[2] = PX(ax, 0); ys[2] = PY(ay, 0);
    fillPoly(xs, ys, 3, C_BEAK);

    // 下喙
    place(-0.070f, by + gap, ax, ay);        xs[0] = PX(ax, 0); ys[0] = PY(ay, 0);
    place( 0.070f, by + gap, ax, ay);        xs[1] = PX(ax, 0); ys[1] = PY(ay, 0);
    place( 0.0f, by + gap + 0.105f, ax, ay); xs[2] = PX(ax, 0); ys[2] = PY(ay, 0);
    fillPoly(xs, ys, 3, C_BEAKDARK);
  } else {
    float ax, ay;
    place(-0.088f, by, ax, ay);         xs[0] = PX(ax, 0); ys[0] = PY(ay, 0);
    place( 0.088f, by, ax, ay);         xs[1] = PX(ax, 0); ys[1] = PY(ay, 0);
    place( 0.0f, by + 0.175f, ax, ay);  xs[2] = PX(ax, 0); ys[2] = PY(ay, 0);
    fillPoly(xs, ys, 3, C_BEAK);
  }
}

void drawBrows(const OwlParams &o, float hx, float hy) {
  // 对应 python：每侧 3 根由粗到细的羽片
  struct Layer { float off, wmul, shorten; };
  static const Layer LAYERS[3] = {{0.0f, 1.00f, 0.0f},
                                  {0.052f, 0.62f, 0.16f},
                                  {0.098f, 0.40f, 0.32f}};
  for (int side = -1; side <= 1; side += 2) {
    float lift = o.browLift + side * o.browAsym;
    float flare = o.browFlare;
    for (int li = 0; li < 3; li++) {
      const Layer &L = LAYERS[li];
      float baseX = side * (0.575f - L.off * 0.15f);
      float baseY = -0.345f - lift * 0.55f - L.off * 0.85f;
      float tipX = side * (0.125f + L.shorten + 0.040f * flare);
      float tipY = -0.250f - lift - L.off * 1.35f - 0.045f * flare * (1.0f - L.shorten);
      float w0 = (0.078f + 0.026f * flare) * L.wmul;
      float w1 = (0.016f + 0.010f * flare) * L.wmul;

      float ax, ay, bx, by;
      rotatePt(baseX, baseY, o.headTilt, hx, hy, ax, ay);
      rotatePt(tipX, tipY, o.headTilt, hx, hy, bx, by);

      // 羽片 = 四边形（两端不同宽度）
      float dx = bx - ax, dy = by - ay;
      float len = sqrtf(dx * dx + dy * dy);
      if (len < 1e-5f) continue;
      float nx = -dy / len, ny = dx / len;
      int16_t xs[4], ys[4];
      xs[0] = PX(ax + nx * w0, 0); ys[0] = PY(ay + ny * w0, 0);
      xs[1] = PX(bx + nx * w1, 0); ys[1] = PY(by + ny * w1, 0);
      xs[2] = PX(bx - nx * w1, 0); ys[2] = PY(by - ny * w1, 0);
      xs[3] = PX(ax - nx * w0, 0); ys[3] = PY(ay - ny * w0, 0);
      fillPoly(xs, ys, 4, li == 0 ? C_BROW : C_BROWSHADE);
    }
  }
}

void drawFace(const OwlParams &o) {
  spr.fillSprite(C_BG);

  float hx = o.headX + (o.headX == 0 ? S.idleX * 0.5f : 0);
  float hy = o.headY;

  // ---- 面盘（心形凹口）》----
  {
    int16_t xs[52], ys[52];
    int n = 48;
    float rx = 0.86f, ry = 0.80f, notch = 0.26f;
    for (int i = 0; i < n; i++) {
      float a = 2.0f * M_PI * i / n - M_PI / 2.0f;
      float x = cosf(a), y = sinf(a);
      if (y < 0) {
        float k = max(0.0f, 1.0f - fabsf(x) / 0.55f);
        y *= (1.0f - notch * k * k);
      }
      float pxv, pyv;
      rotatePt(x * rx, y * ry, o.headTilt, hx, hy, pxv, pyv);
      xs[i] = PX(pxv, 0);
      ys[i] = PY(pyv, 0);
    }
    fillPoly(xs, ys, n, C_DISC);
  }

  // ---- 眼睛 ----
  int16_t ex_[96], ey_[96];
  float eyeDx = 0.255f * o.eyeGap;
  for (int side = -1; side <= 1; side += 2) {
    float cx, cy;
    rotatePt(side * eyeDx, 0.0f, o.headTilt, hx, hy, cx, cy);
    int n = eyeShape(cx, cy, o.eyeRx, o.eyeRy, o.eyeOpen, o.smile, o.angry,
                     ex_, ey_, 96);
    fillPoly(ex_, ey_, n, C_EYE);
  }

  // ---- 眉羽 ----
  drawBrows(o, hx, hy);

  // ---- 喙 ----
  drawBeak(o, o.headTilt * M_PI / 180.0f, hx, hy);

  // ---- 彩蛋星星 ----
  if (o.hype > 0.01f) {
    uint32_t rnd = o.seed;
    for (int i = 0; i < 12; i++) {
      rnd = rnd * 1664525u + 1013904223u; float a0 = (rnd % 1000) / 1000.0f * 2 * M_PI;
      rnd = rnd * 1664525u + 1013904223u; float r0 = 0.50f + (rnd % 1000) / 1000.0f * 0.35f;
      rnd = rnd * 1664525u + 1013904223u; float sp = 0.6f + (rnd % 1000) / 1000.0f * 0.7f;
      rnd = rnd * 1664525u + 1013904223u; float ph = fmodf(o.clock * sp + (rnd % 1000) / 1000.0f, 1.0f);
      float rr = 0.45f + r0 * ph;
      int s = (int)(0.020f * (1 - ph) * o.hype * RF);
      if (s < 1) continue;
      spr.fillCircle(PX(cosf(a0) * rr, 0), PY(sinf(a0) * rr, 0), s,
                     ((int)(ph * 2) % 2 == 0) ? 0xFE86 : 0x6E3F);
    }
  }

  // ---- 圆形遮罩 ----
  for (int y = 0; y < SCREEN_H; y++) {
    float dy = y - CYF + 0.5f;
    float halfW = sqrtf(max(0.0f, RF * RF - dy * dy));
    int x0 = (int)(CXF - halfW), x1 = (int)(CXF + halfW);
    if (x0 > 0) spr.fillRect(0, y, x0, y + 1, 0x0000);
    if (x1 < SCREEN_W) spr.fillRect(x1, y, SCREEN_W, y + 1, 0x0000);
  }

  spr.pushSprite(0, 0);
}

// ===========================================================================
// 灯带 + 舵机（三条独立通道，表情不等硬件）
// ===========================================================================

static const uint32_t LED_IDLE   = 0xFFC46B;
static const uint32_t LED_TAP_L  = 0xFFD68C;
static const uint32_t LED_TAP_H  = 0xFFAA50;
static const uint32_t LED_MINE   = 0xFFB86B;
static const uint32_t LED_THEIRS = 0x6FD3FF;
static const uint32_t LED_HYPE   = 0xFFECAA;

uint32_t dimColor(uint32_t c, float k);

void updateLeds() {
  uint32_t left = LED_IDLE, right = LED_IDLE;

  if (S.hype > 0.05f) {
    left = right = LED_HYPE;
  } else {
    float dip, squash, pop;
    tapChannels(dip, squash, pop);
    if (S.tap.active && dip > 0.02f) {
      left = right = (S.lastKind == TAP_HEAVY) ? LED_TAP_H : LED_TAP_L;
    } else if (S.col.pos < -0.15f) {
      left = LED_MINE; right = dimColor(LED_THEIRS, 0.35f);
    } else if (S.col.pos > 0.15f) {
      left = dimColor(LED_MINE, 0.35f); right = LED_THEIRS;
    }
  }

  int half = LED_COUNT / 2;
  for (int i = 0; i < LED_COUNT; i++)
    strip.setPixelColor(i, i < half ? left : right);
  strip.show();
}

uint32_t dimColor(uint32_t c, float k) {
  uint8_t r = ((c >> 16) & 0xFF) * k;
  uint8_t g = ((c >> 8) & 0xFF) * k;
  uint8_t b = (c & 0xFF) * k;
  return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

void updateServo() {
  // 转头：col.pos 直接映射到角度，和屏幕上的 headTilt 同源
  int angle = SERVO_CENTER + (int)(S.col.pos * 18.0f);
  headServo.write(angle);
}

// ===========================================================================
// 输入
// ===========================================================================

void readInput() {
  // TODO: 换成实际硬件
  //   敲击 → 压电片模拟阈值，或两个按钮
  //   9 宫格 → 9 个触摸焊盘
  if (Serial.available()) {
    int c = Serial.read();
    if (c == '\n' || c == '\r') { releaseGrid(); return; }
    if (c == 'F') { doTap(TAP_LIGHT); return; }
    if (c == 'J') { doTap(TAP_HEAVY); return; }
    for (int i = 0; i < KEY_COUNT; i++)
      if ((char)c == KEY_GRID[i].key) { pressGrid(i); return; }
  }
}

// ===========================================================================

void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(0);
  tft.fillScreen(C_BG);

  spr.setColorDepth(16);
  spr.createSprite(SCREEN_W, SCREEN_H);

  strip.begin();
  strip.setBrightness(120);
  strip.show();

  headServo.attach(SERVO_PIN);
  headServo.write(SERVO_CENTER);

  S.col.k = 190; S.col.d = 24;
  S.row.k = 160; S.row.d = 22;

  randomSeed(micros());
}

unsigned long lastMs = 0;
OwlParams O;

void loop() {
  unsigned long now = millis();
  float dt = (now - lastMs) / 1000.0f;
  lastMs = now;

  readInput();
  updateState(dt);
  stateToOwl(O);
  drawFace(O);
  updateLeds();
  updateServo();

  // 按 20 FPS 设计（见文件头说明）
  delay(15);
}

/*
 * ===========================================================================
 * 迁移对照表
 * ===========================================================================
 *  duetto_owl.build_owl_frame()   →  drawFace()
 *  duetto_owl.eye_shape()         →  eyeShape()
 *  duetto_owl.taper_stroke()      →  drawBrows() 里的四边形
 *  duetto_owl 的 place() 旋转     →  rotatePt()
 *  duetto_states.state_to_owl()   →  stateToOwl()
 *  duetto_states.light_for()      →  updateLeds()
 *
 * ===========================================================================
 * 性能提示（重要）
 * ===========================================================================
 * 1. 整屏 pushSprite 在 240×240 / 16bit 下约 10ms（ESP32-S3），
 *    加上画图约 20~25ms → 约 20 FPS。这和 Adafruit 的实测一致。
 * 2. 想更快：
 *    a) 记录上一帧面部包围盒，只 pushSprite 那块（脏矩形）
 *    b) 把圆形遮罩的 sqrt 换成 setup 里算好的 halfWidth[240] 查表
 *    c) 面部没动时跳过重绘（idle 时 90% 帧是相同的）
 * 3. 单眼方案能直接砍一半绘制量（Adafruit: 单眼 39 FPS vs 双眼 20 FPS），
 *    如果帧率不够，这是最便宜的选择。
 */
