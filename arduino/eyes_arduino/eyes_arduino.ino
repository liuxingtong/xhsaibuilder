/*
 * eyes_arduino.ino — 圆屏「眼睛」原型（Arduino 版）
 *
 * 逻辑与 eyes_core.py 一一对应，移植时保持同样的状态机、同样的参数。
 * 差别只有两处：
 *   1. 时间用 millis() / dt 自己算，而不是 python 的 update(dt)
 *   2. 绘制用 TFT_eSPI 的 GFX 原语，坐标从 -1..1 归一化换成像素
 *
 * 硬件假设
 *   - GC9A01 1.28" 圆形 IPS，240×240，SPI 接口
 *   - 两个敲击输入：轻敲按钮、重击按钮（也可以换成压电片 + 阈值）
 *   - 9 宫格：先用串口/键盘输入模拟，后面换成 9 个电容触摸焊盘
 *
 * 依赖库（Arduino Library Manager 里装）
 *   - TFT_eSPI          （显示）
 *   - 无需其他库
 *
 * 目标板：ESP32 / ESP32-S3 / RP2040 都行。UNO 跑 240×240 全刷会偏慢，
 *        建议改用 128×128 圆屏，或只做"脏矩形"局部刷新（见文末 TODO）。
 */

#include <TFT_eSPI.h>
#include <math.h>

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite spr = TFT_eSprite(&tft);   // 离屏缓冲，避免闪烁

// ---------------------------------------------------------------------------
// 屏幕常量
// ---------------------------------------------------------------------------

static const int SCREEN = 240;          // 圆屏分辨率；换成 128 就改这里
static const int CX = SCREEN / 2;
static const int CY = SCREEN / 2;
static const float R = SCREEN / 2.0f;   // 归一化半径

// ---------------------------------------------------------------------------
// 配色（RGB565）
// ---------------------------------------------------------------------------

#define C_BG      0x0861   // 深蓝黑背景
#define C_EYEWHITE 0xEF5D  // 眼白
#define C_PUPIL   0x18E3   // 瞳孔
#define C_GLINT   0xFFFF   // 高光
#define C_LID     0x10C2   // 眼皮
#define C_SPARK   0xFDC0   // 彩蛋星星（暖黄）
#define C_SPARK2  0x5E5F   // 彩蛋星星（冷蓝）

// ---------------------------------------------------------------------------
// 缓动
// ---------------------------------------------------------------------------

static inline float clampf(float x, float lo, float hi) {
  return x < lo ? lo : (x > hi ? hi : x);
}
static inline float lerpf(float a, float b, float t) {
  return a + (b - a) * t;
}
static inline float easeOutCubic(float t) {
  t = clampf(t, 0, 1);
  return 1.0f - powf(1.0f - t, 3.0f);
}
static inline float easeInOutSine(float t) {
  t = clampf(t, 0, 1);
  return -(cosf(M_PI * t) - 1.0f) / 2.0f;
}

// 临界阻尼弹簧：和 python 的 spring_step 完全一致
struct Spring {
  float pos = 0, vel = 0, target = 0;
  float k = 190.0f, d = 24.0f;
  void step(float dt) {
    float accel = (target - pos) * k - vel * d;
    vel += accel * dt;
    pos += vel * dt;
  }
};

// ---------------------------------------------------------------------------
// 敲击参数：和 eyes_core.py 的 LIGHT_TAP / HEAVY_TAP 保持一致
// ---------------------------------------------------------------------------

struct TapCfg {
  float dur, dip, squash, pop, bounce;
};

static const TapCfg LIGHT_TAP = {0.38f, 0.26f, 0.46f, 1.10f, 0.42f};
static const TapCfg HEAVY_TAP = {0.80f, 0.40f, 0.56f, 1.60f, 0.75f};

enum TapKind { TAP_LIGHT, TAP_HEAVY };

struct TapState {
  TapKind kind = TAP_LIGHT;
  float t = 0;
  bool active = false;
};

// ---------------------------------------------------------------------------
// 9 宫格
// ---------------------------------------------------------------------------

// 列 -1=左 0=中 1=右，行 -1=上 0=中 1=下
struct GridKey { char key; int col, row; };

static const GridKey KEY_GRID[] = {
  {'t', -1, -1}, {'y', 0, -1}, {'u', 1, -1},
  {'g', -1,  0}, {'h', 0,  0}, {'j', 1,  0},
  {'b', -1,  1}, {'n', 0,  1}, {'m', 1,  1},
};
static const int KEY_COUNT = 9;

// 列风格 / 行风格，和 python 版对齐
struct ColStyle { float stretch, tilt, openness; };
static const ColStyle COL_STYLE[3] = {
  {1.14f, -13.0f, 1.04f},   // 左
  {1.00f,   0.0f, 1.00f},   // 中
  {1.14f,  13.0f, 1.04f},   // 右
};
static const float ROW_OPEN[3] = {1.10f, 1.00f, 0.90f};  // 上 / 中 / 下

static const float ROW_SHIFT = 0.20f;   // 行位置 → 垂直位移系数
static const float EYE_PAN_X = 0.30f;   // 列位置 → 水平位移系数

// ---------------------------------------------------------------------------
// 上滑彩蛋检测：1 秒内 下排 → 中排 → 上排
// ---------------------------------------------------------------------------

struct SwipeSample { unsigned long t; int row; };

static const int HISTORY_MAX = 16;
SwipeSample historyBuf[HISTORY_MAX];
int historyLen = 0;

static const unsigned long SWIPE_WINDOW = 1000;  // ms
static const unsigned long SWIPE_MAX_GAP = 550;  // ms

void swipeReset() { historyLen = 0; }

bool swipeFeed(unsigned long now, int row) {
  if (historyLen >= HISTORY_MAX) {
    for (int i = 1; i < HISTORY_MAX; i++) historyBuf[i - 1] = historyBuf[i];
    historyLen--;
  }
  historyBuf[historyLen++] = {now, row};

  // 丢弃窗口外的样本
  int start = 0;
  while (start < historyLen && (now - historyBuf[start].t) > SWIPE_WINDOW) start++;
  if (start > 0) {
    for (int i = start; i < historyLen; i++) historyBuf[i - start] = historyBuf[i];
    historyLen -= start;
  }

  // 扫描：1(下) → 0(中) → -1(上)
  int stage = 0;
  unsigned long stageT = 0;
  for (int i = 0; i < historyLen; i++) {
    int r = historyBuf[i].row;
    unsigned long t = historyBuf[i].t;
    if (stage == 0) {
      if (r == 1) { stage = 1; stageT = t; }
    } else if (stage == 1) {
      if (r == 0 && (t - stageT) <= SWIPE_MAX_GAP) stage = 2;
      else if (r == 1) stageT = t;
    } else if (stage == 2) {
      if (r == -1 && (t - stageT) <= SWIPE_WINDOW) { swipeReset(); return true; }
      if (r == 1) { stage = 1; stageT = t; }
      else if (r == 0) stageT = t;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// 主状态机
// ---------------------------------------------------------------------------

struct EyeState {
  Spring col, row;

  TapState tap;
  int tapCount = 0;
  TapKind lastKind = TAP_LIGHT;

  float clock = 0;

  // 眨眼
  float blinkIn = 3.0f;
  float blinkT = -1;
  static constexpr float BLINK_LEN = 0.16f;

  // 彩蛋
  float hype = 0;
  float hypeT = 0;
  static constexpr float HYPE_LEN = 2.6f;
  uint32_t sparkSeed = 1;

  // 待机微瞟
  float idleX = 0, idleY = 0, idleNext = 2.5f;
};

EyeState es;

void triggerHype() {
  es.hype = 1.0f;
  es.hypeT = 0;
  es.sparkSeed = micros();
}

void doTap(TapKind kind) {
  if (kind == TAP_HEAVY || !es.tap.active) {
    es.tap.kind = kind;
    es.tap.t = 0;
    es.tap.active = true;
    es.tapCount++;
    es.lastKind = kind;
  } else {
    es.tapCount++;
    float boost = min(0.35f, 0.08f * es.tapCount);
    const TapCfg &c = (es.lastKind == TAP_HEAVY) ? HEAVY_TAP : LIGHT_TAP;
    es.tap.t = 0;
    es.tap.dip = c.dip * (1 + boost);
    es.tap.squash = min(0.9f, c.squash * (1 + boost));
  }
}

void pressGrid(int idx) {
  if (idx < 0 || idx >= KEY_COUNT) return;
  const GridKey &g = KEY_GRID[idx];
  es.col.target = g.col;
  es.row.target = g.row;

  if (swipeFeed(millis(), g.row)) {
    triggerHype();
    return;
  }

  // 每按一下给一点点反馈
  if (!es.tap.active) {
    es.tap.kind = TAP_LIGHT;
    es.tap.t = 0;
    es.tap.active = true;
    es.tap.dip = LIGHT_TAP.dip * 0.22f;
    es.tap.squash = LIGHT_TAP.squash * 0.22f;
  }
}

void releaseGrid() {
  es.col.target = 0;
  es.row.target = 0;
}

// 敲击各通道：位移 / 压扁 / 瞳孔缩放
void tapChannels(float &dip, float &squash, float &pop) {
  if (!es.tap.active) { dip = 0; squash = 0; pop = 1.0f; return; }
  const TapCfg &c = (es.tap.kind == TAP_HEAVY) ? HEAVY_TAP : LIGHT_TAP;
  float p = clampf(es.tap.t / c.dur, 0, 1);

  if (es.tap.kind == TAP_HEAVY) {
    if (p < 0.22f) {
      float k = easeOutCubic(p / 0.22f);
      dip = c.dip * k;
      squash = c.squash * k;
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
      dip = c.dip * k;
      squash = c.squash * k;
    } else {
      float k = easeOutCubic((p - 0.35f) / 0.65f);
      dip = lerpf(c.dip, -c.dip * c.bounce, k);
      squash = lerpf(c.squash, 0, k);
    }
    pop = 1.0f + (c.pop - 1.0f) * sinf(M_PI * p);
  }

  // 收尾归零，避免跳变
  if (p > 0.92f) {
    float f = (p - 0.92f) / 0.08f;
    dip *= (1 - f);
    squash *= (1 - f);
  }
}

// ---------------------------------------------------------------------------
// 更新
// ---------------------------------------------------------------------------

void updateState(float dt) {
  if (dt > 0.05f) dt = 0.05f;
  es.clock += dt;

  es.col.step(dt);
  es.row.step(dt);

  if (es.tap.active) {
    es.tap.t += dt;
    if (es.tap.t >= ((es.tap.kind == TAP_HEAVY) ? HEAVY_TAP.dur : LIGHT_TAP.dur)) {
      es.tap.active = false;
      es.tapCount = 0;
    }
  }

  if (es.blinkT >= 0) {
    es.blinkT += dt;
    if (es.blinkT >= EyeState::BLINK_LEN) {
      es.blinkT = -1;
      es.blinkIn = random(240, 650) / 100.0f;
    }
  } else {
    es.blinkIn -= dt;
    if (es.blinkIn <= 0) es.blinkT = 0;
  }

  if (es.hype > 0) {
    es.hypeT += dt;
    if (es.hypeT >= EyeState::HYPE_LEN) { es.hype = 0; es.hypeT = 0; }
  }

  es.idleNext -= dt;
  if (es.idleNext <= 0) {
    es.idleNext = random(200, 500) / 100.0f;
    es.idleX = (random(-45, 45)) / 1000.0f;
    es.idleY = (random(-35, 35)) / 1000.0f;
  }
}

// ---------------------------------------------------------------------------
// 绘制（对应 python 的 build_frame）
// ---------------------------------------------------------------------------

inline int px(float v) { return (int)lroundf(CX + v * R); }
inline int pr(float v) { return (int)lroundf(v * R); }

void drawEyes() {
  float colF = es.col.pos;
  float rowF = es.row.pos;

  // --- 样式插值 ---
  // COL_STYLE 下标 0=左 1=中 2=右；colF 在 -1..1 之间做分段插值
  int ia, ib;
  float t1;
  if (colF <= 0) { ia = 0; ib = 1; t1 = clampf(colF + 1.0f, 0, 1); }
  else            { ia = 1; ib = 2; t1 = clampf(colF, 0, 1); }
  t1 = easeInOutSine(t1);
  const ColStyle &a = COL_STYLE[ia];
  const ColStyle &b = COL_STYLE[ib];
  float stretch = lerpf(a.stretch, b.stretch, t1);
  float tilt = lerpf(a.tilt, b.tilt, t1) * (0.4f + 0.6f * fabsf(colF));
  float openness = lerpf(a.openness, b.openness, t1);

  // 行 → 睁眼程度
  int ri = (rowF <= 0) ? 0 : 1;
  float t2 = (rowF <= 0) ? clampf(rowF + 1.0f, 0, 1) : clampf(rowF, 0, 1);
  t2 = easeInOutSine(t2);
  openness *= lerpf(ROW_OPEN[ri], ROW_OPEN[ri + 1], t2);

  // --- 敲击 / 眨眼 / 彩蛋 ---
  float dip, squash, pop;
  tapChannels(dip, squash, pop);

  float blink = 0;
  if (es.blinkT >= 0) blink = sinf(M_PI * clampf(es.blinkT / EyeState::BLINK_LEN, 0, 1));
  float squashTotal = clampf(squash + blink, 0, 0.98f);

  float hype = es.hype;

  float baseY = rowF * ROW_SHIFT;
  if (hype > 0) baseY += sinf(es.clock * 9.0f) * 0.045f * hype;
  float centerY = baseY + dip;

  float eyeDx = colF * EYE_PAN_X + es.idleX * (1.0f - fabsf(colF));
  float spread = 0.255f;
  float eyeRx = 0.132f * stretch * (1.0f + 0.26f * hype);
  float eyeRy = 0.186f * openness * pop * (1.0f + 0.38f * hype) * (1.0f - squashTotal);
  if (eyeRy < 0.012f) eyeRy = 0.012f;

  // --- 清屏 ---
  spr.fillSprite(C_BG);

  for (int side = -1; side <= 1; side += 2) {
    float cx = eyeDx + side * spread;
    float cy = centerY;
    float near = (side * colF > 0) ? 1.0f : 0.0f;
    float sizeK = 1.0f + (0.12f * near - 0.07f * (1 - near)) * fabsf(colF);
    float rx = eyeRx * sizeK, ry = eyeRy * sizeK;

    int ecx = px(cx), ecy = px(cy);
    int erx = pr(rx), ery = pr(ry);

    // 眼白
    spr.fillEllipse(ecx - erx, ecy - ery, erx * 2, ery * 2, C_EYEWHITE);

    // 瞳孔
    float pupilScale = 0.46f * (1 + 0.18f * fabsf(colF)) * (1 + 0.22f * hype);
    int prx = (int)(erx * pupilScale * (1 - 0.28f * squashTotal));
    int pry = (int)(ery * pupilScale * (1 + 0.08f * squashTotal));
    int pcx = px(cx + colF * 0.022f);
    int pcy = px(cy + rowF * 0.016f);
    spr.fillEllipse(pcx - prx, pcy - pry, prx * 2, pry * 2, C_PUPIL);

    // 高光
    int gr = max(1, prx / 3);
    spr.fillCircle(pcx + prx / 2, pcy - pry / 2, gr, C_GLINT);

    // 上眼皮（倾斜）
    if (fabsf(tilt) > 0.8f) drawLid(ecx, ecy, erx, ery, tilt);
  }

  if (hype > 0) drawSparkles(hype);

  // 圆形遮罩：把圆外的像素涂黑
  spr.fillRect(0, 0, SCREEN, CY - (int)R, C_BG);   // 上
  spr.fillRect(0, CY + (int)R, SCREEN, SCREEN, C_BG); // 下
  // 四角
  for (int y = 0; y < SCREEN; y++) {
    int dy = y - CY;
    int half = (int)sqrtf(max(0.0f, R * R - (float)dy * dy));
    if (CX - half > 0) spr.fillRect(0, y, CX - half, y + 1, 0x0000);
    if (CX + half < SCREEN) spr.fillRect(CX + half, y, SCREEN, y + 1, 0x0000);
  }

  spr.pushSprite(0, 0);
}

void drawLid(int cx, int cy, int rx, int ry, float tilt) {
  // 与 eyes_core._draw_lid_tilt 一致：只在眼睛自身横向范围内覆盖，
  // 下沿最多压 depth*ry（<=13%），逐列扫描填满上方。
  if (rx <= 0 || ry <= 0) return;

  float ang = tilt * (M_PI / 180.0f);
  float tanA = tanf(ang);
  float depth = min(0.13f, 0.07f + fabsf(tilt) / 90.0f * 0.06f);
  int top = cy - (int)(2.0f * ry);

  for (int dx = -rx; dx <= rx; dx++) {
    float t = (dx + rx) / (2.0f * (float)rx);   // 0..1
    float xn = (float)dx / (float)rx;           // -1..1
    float arch = sqrtf(max(0.0f, 1.0f - xn * xn));
    float yTop = -arch;                         // 单位：ry
    float lean = tanA * (t - 0.5f) * 1.4f;
    int bottom = cy + (int)lroundf((yTop + depth + lean) * ry);
    if (bottom > top) spr.fillRect(cx + dx, top, 1, bottom - top, C_LID);
  }
}

void drawSparkles(float hype) {
  uint32_t rnd = es.sparkSeed;
  const int N = 14;
  for (int i = 0; i < N; i++) {
    rnd = rnd * 1664525u + 1013904223u;
    float a0 = (rnd % 1000) / 1000.0f * 2 * M_PI;
    rnd = rnd * 1664525u + 1013904223u;
    float r0 = 0.40f + (rnd % 1000) / 1000.0f * 0.38f;
    rnd = rnd * 1664525u + 1013904223u;
    float speed = 0.55f + (rnd % 1000) / 1000.0f * 0.85f;
    rnd = rnd * 1664525u + 1013904223u;
    float phase = fmodf(es.clock * speed + (rnd % 1000) / 1000.0f, 1.0f);

    float rr = 0.30f + r0 * phase;
    float x = cosf(a0) * rr, y = sinf(a0) * rr;
    int s = (int)(0.032f * (1 - phase) * hype * R);
    if (s < 1) continue;
    uint16_t c = (fmodf(phase, 0.5f) < 0.25f) ? C_SPARK : C_SPARK2;
    spr.fillCircle(px(x), px(y), s, c);
  }
}

// ---------------------------------------------------------------------------
// 输入
// ---------------------------------------------------------------------------

// TODO: 换成你的实际输入
//   敲击：压电片读模拟值，超过阈值判定 heavy，否则 light
//   9 宫格：9 个电容触摸焊盘 / 9 个按键 / 串口命令
int readGridKey() {
  if (Serial.available()) {
    int c = Serial.read();
    if (c == '\n' || c == '\r') return -2;   // release
    for (int i = 0; i < KEY_COUNT; i++)
      if ((char)c == KEY_GRID[i].key) return i;
    if (c == 'F') { doTap(TAP_LIGHT); return -3; }
    if (c == 'J') { doTap(TAP_HEAVY); return -3; }
  }
  return -1;
}

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(0);
  tft.fillScreen(C_BG);

  spr.setColorDepth(16);
  spr.createSprite(SCREEN, SCREEN);

  es.col.k = 190.0f; es.col.d = 24.0f;
  es.row.k = 160.0f; es.row.d = 22.0f;

  randomSeed(micros());
}

unsigned long lastMs = 0;

void loop() {
  unsigned long now = millis();
  float dt = (now - lastMs) / 1000.0f;
  lastMs = now;

  int k = readGridKey();
  if (k >= 0) pressGrid(k);
  else if (k == -2) releaseGrid();

  updateState(dt);
  drawEyes();
}

/*
 * ---------------------------------------------------------------------------
 * 性能提示
 * ---------------------------------------------------------------------------
 * 240×240 全屏 pushSprite 在 ESP32 上约 8~12ms（够 30~60fps）。
 * 在 UNO 上会慢到 ~50ms，两个办法：
 *   1. 换 128×128 圆屏（改 SCREEN 常量）
 *   2. 只刷"眼睛所在的外接矩形"，而不是整屏：
 *      记录上一帧的中心/半径，算出脏矩形，只 pushSprite 那块区域。
 *
 * 圆形遮罩那一段（逐行 sqrt）每帧都要算 240 次开方，可以优化成：
 *   预计算一个 halfWidth[SCREEN] 查表，setup 里算一次即可。
 *
 * ---------------------------------------------------------------------------
 * 从 python 迁移时的对照表
 * ---------------------------------------------------------------------------
 *   eyes_core.build_frame()   ->  drawEyes()
 *   eyes_core._tap_channels() ->  tapChannels()
 *   eyes_core._blend_style()  ->  drawEyes() 开头的样式插值
 *   eyes_core._draw_lid_tilt()->  drawLid()
 *   eyes_core._draw_sparkles()->  drawSparkles()
 *   eyes_render.TkRenderer    ->  TFT_eSprite 的 GFX 调用
 *   归一化坐标 -1..1           ->  px() / pr() 两个辅助函数
 * ---------------------------------------------------------------------------
 */
