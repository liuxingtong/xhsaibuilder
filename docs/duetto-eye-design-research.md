# Duetto 形象设计与视觉语言调研

> 所有数据来自公开网页（链接见第 5 节）。无公开数据处已明确标注。

## 1. 竞品速览

| 产品 | 形态 | 屏幕 | 眼睛画法 | 签名动作 | 价格 |
|---|---|---|---|---|---|
| **Vector** | 抽象几何 | 彩色 IPS | 单色发光几何块（青绿），无眼白/瞳孔，靠轮廓变形 | 眨眼、求 fist bump | $249.99 [IEEE](https://robotsguide.com/robots/vector) |
| **Cozmo** | 抽象几何 | 128×64 单色 OLED，隔行防烧屏 | 橙色单色像素块，Eve 式极简图形 | 眯眼「不爽」、抬臂 | $180 [Core77](https://www.core77.com/posts/54328) |
| **Eilik** | 拟物小人+双臂 | 1.54″ **128×64** OLED | 屏幕脸；表情高度依赖肢体与音效 | 被举高「怕高」、群体同步舞 | ¥1299 [RoboAtlas](https://www.roboatlas.ai/zh-CN/products/eilik) |
| **EMO** | 拟物小狗 | 屏幕脸（尺寸**无公开数据**） | 屏幕表情 + 可变色灯带 | 桌面游走、跳舞 | $279 [RoboAtlas](https://www.roboatlas.ai/zh-CN/products/emo-emo) |
| **LOOI** | 抽象，手机当脸 | 借手机屏 | 全屏动画 | 无聊时「擦屏」、待机「做梦」 | ~$179 [PocketAnimus](https://pocketanimus.com/guides/looi-robot/) |
| **Moflin** | 无屏纯毛发 | 无 | 无眼睛 | 2 轴头动、鸣叫 | $429 [RoboAtlas](https://www.roboatlas.ai/zh-CN/products/moflin) |
| **Ropet KAMOMO** | 毛绒+屏幕眼 | 小屏（**尺寸无公开数据**） | expressive screen eyes，App 换眼型 | 注视追随 | $339–349 [RoboAtlas](https://www.roboatlas.ai/en-US/products/ropet) |
| **Jibo** | 抽象，单只大圆眼 | 圆形屏（**一块**） | 单眼，白屏当画布 | 转体、单眼变色 | $899 [IEEE](https://robotsguide.com/robots/jibo) |
| **Moxie** | 拟物圆头 | 屏幕脸 | 屏幕眼（设计者明确弃机械眼选屏幕） | 眼神交流 | 硬件+订阅 [DesignWanted](https://designwanted.com/humanizing-tech-yves-behar-moxie-embodied/) |
| **Aibi** | 拟物小球 | 屏幕脸 | 屏幕动画眼，配件可变猫 | 360° 转台 | **无公开数据** [RoboPhilosophy](https://www.robophilosophy.com/archives/3566) |
| **Ballie** | 概念滚动球 | 用**投影**投界面 | 无眼睛 | 跟随人 | 推迟上市，估 >$2000 [环球网](https://m.163.com/dy/article/K3F6E3NB0514R9OJ.html) |
| **Bubo-2T** | 拟物猫头鹰（开源） | 无屏 | **机械上下眼睑**+NeoPixel 眼环 | 眨眼、张嘴 | DIY [KevsRobots](https://www.kevsrobots.com/blog/bubo-2t.html) |

**共性**：屏幕都很小（128×64 或 1.5″ 级），没人靠分辨率取胜，靠**轮廓变形 + 节奏**。

## 2. 眼睛造型语言：8 条原则

1. **不做「眼白+瞳孔」，做「实心几何块」。** Cozmo/Vector 是单色填充形状，Anki 直接借 Eve 的眼睛「连颜色和美学一起」（[Core77](https://www.core77.com/posts/54328)）。小屏画眼白会显脏显小。
2. **一个 8 点多边形承载所有表情，切换=逐点插值；眨眼=插值成细矩形再弹回。** Vector 风格开源实现已验证（[PEL](https://github.com/ggldnl/Procedural-Expression-Library)），可零成本移植到 240×240。
3. **一次只动一条边。** 上缘上凸=开心；上缘下压=困；下缘抬尖=不屑/疑惑。两边同动会读不懂。
4. **偏移瞳孔比移动整眼更安全。** 240px 上平移几十像素就出界；在眼内偏移瞳孔制造「看向」。
5. **高光 0–1 个。** 放瞳孔左上并随视线同向偏移；2 个以上是噪点。暗示暗底：1 个 4–6px 柔边圆。
6. **眼距 ≈ 1.0–1.4 倍眼宽**（据产品图目测归纳，**厂商未公开精确比例**）。240px 上两眼各 56–64px、间隙 24–32px 是好起点。
7. **待机必须有呼吸与随机微动。** Moflin 无屏却成立，证明「活着」可由非屏幕通道承担；你有舵机，待机应每 3–6s 有一次 2–3px 微动或随机眨眼。
8. **单眼 vs 双眼是品牌决策。** Jibo 选单眼：更适合当「画布」，双眼像玩具，且用户要**触摸面板**，生物式眼睛没人忍心戳（[机器人新闻](http://www.irobotnews.com/news/articleView.html?idxno=6430)）——这条直接关系到你的敲击手势。

## 3. 抽象 vs 拟物

- **抽象**（Vector/Cozmo/Jibo）：靠光与运动建立辨识度，模具便宜、避免恐怖谷、表情自由度最高。代价是动画质量要求极高——Vector 团队有 ex-Pixar 成员，这是隐性成本。
- **拟物**（EMO/Eilik/Ropet/Bubo）：第一眼就可爱，可用毛布触感弥补小屏。代价是模具与装配贵（Eilik 用 4 舵机），外形锁死眼位与运动范围。
- **做猫头鹰**：Bubo-2T 证明「猫头鹰 + 机械眼睑 + NeoPixel 眼环」成立。要点是**眼周「面盘」交给灯带**，屏幕只画瞳孔与眼皮——既像鸟又保住屏幕灵活性。风险：中文语境偏夜行呆萌，需靠灯色拉调性。
- **结论**：抽象的单位成本与迭代速度占优。**建议抽象几何为主线 + 灯带做局部拟物符号**，不要一上来做全模具。

## 4. 对 Duetto 的建议

**三条硬约束**
- **帧率**：同一块 GC9A01A，ESP32-S3 画**单眼 39 FPS、双眼仅 20 FPS**；RP2040 双眼 13 FPS（[Adafruit](https://learn.adafruit.com/adafruit-monster-eyes)）。你一张圆屏画两眼，按 20 FPS 设计，**单次动作 8 帧内说完**。
- **烧屏**：Cozmo 用隔行扫描逐帧交替防烧屏（[SDK](https://github.com/anki/cozmo-python-sdk/blob/5d7fba4129b67f1ff34adec13c3a1421e295f2a7/src/cozmo/oled_face.py)）。GC9A01 是 LCD 风险小，但固定表情仍建议加 ±1px 极慢漂移。
- **表演通道解耦**：Disney 把扬声器、发光眼、头灯定义为 "show" 功能，不影响动力学、只负责表达（[Core77](https://www.core77.com/posts/133071)）。屏幕/灯带/舵机应三条独立通道，表情不等舵机到位。

**时长与缓动**（**无任何厂商公开精确数据**，以下为工程建议）

| 动作 | 时长 | 缓动 |
|---|---|---|
| 眨眼 | 120–160ms（闭 60 + 开 60–100） | 闭 ease-in / 开 ease-out，禁线性；随机 3–7s |
| 轻点头 | 300–400ms | 下沉 ease-out + 过冲 10–20% 弹簧 |
| 重击 | 600–800ms | 过冲 60–75% + 尾部一次阻尼振荡 |
| 转头看 | 250–350ms | 临界阻尼弹簧，禁 lerp |
| 待机微动 | 每次 1.5–2.5s，间隔 3–6s | 正弦，幅度 2–3px |

**情绪爆发**：LOOI 的「Looi Dreams」是被评测反复称赞的设计（[PocketAnimus](https://pocketanimus.com/guides/looi-robot/)）。Duetto 上滑触发时，让**灯带先亮（0–120ms）、屏幕后跟（120ms 起）**，制造「先发光再睁眼」的因果感。

### 方向 A「双子」（抽象几何，主线）

- 眼睛：填充**圆角矩形**（非圆），近黑底上近乎纯白 #F5F7FA；无眼白无瞳圈；上缘弧切表达笑/困；1 个高光。
- 颜色：底 #0B0C10，眼 #F5F7FA，强调色仅二色：暖琥珀 #FFB86B / 冷静青 #6FD3FF。
- **双人视界**：灯带左右各 12 颗独立寻址——左=「我」、右=「对方」。左暖右冷=两人情绪不同步；同色=同频。竞品中**未发现**同类做法，是 Duetto 最强差异点。
- 手势：敲击→双眼同眯 + 左右灯带同亮（共鸣）；左滑→视线偏左，左亮右暗（只看你）；上滑→瞳孔 1.4×、双眼上移、灯带 0.6s 左右对流（爆发）。

### 方向 B「猫头鹰」（拟物符号，限定版）

- 眼睛：**屏幕只画「瞳」**——两个大圆瞳孔占屏约 70%；**眼周面盘由绕屏一圈的灯带完成**。既像鸟，又保住屏幕灵活性。
- 鸟感关键：眼大、几乎无眼白、双眼共面；眼距压到 **0.6–0.8 倍眼宽**；**瞳孔几乎不左右移动**（猫头鹰眼不能转）——所以滑动时**是转头不是转眼**，让舵机当主角。
- 颜色：底 #14100C 暖褐黑；眼 #FFD98A 琥珀；灯带 #FFC46B 暖白常亮作面盘。
- 手势：敲击→快眨眼 + 头下沉，灯带闪暖白（受惊）；左滑→舵机转头 ±18°，瞳孔不偏移，只亮被转向侧；上滑→屏内瞳孔缩成细缝（鹰式眯眼）+ 灯带由内向外扫光 + 抬头。

### 方向 C「单眼」（备选）

单眼 + 白屏画布，绘制成本减半（单眼 39 FPS vs 双眼 20 FPS）。且单眼天然适配触摸/敲击。

**决策**：主线 A（迭代快、成本低、双人视界最清晰），猫头鹰 B 作限定版验证拟物溢价。

## 5. 参考链接

[Vector/IEEE](https://robotsguide.com/robots/vector) ｜ [Cozmo 眼型来源](https://www.core77.com/posts/54328) ｜ [Cozmo SDK](https://github.com/anki/cozmo-python-sdk/blob/5d7fba4129b67f1ff34adec13c3a1421e295f2a7/src/cozmo/oled_face.py) ｜ [程序化表情库](https://github.com/ggldnl/Procedural-Expression-Library) ｜ [Adafruit Monster Eyes（帧率）](https://learn.adafruit.com/adafruit-monster-eyes) ｜ [ESP32-CAM-EYES](https://github.com/mabuware/ESP32-CAM-EYES) ｜ [Eilik](https://www.roboatlas.ai/zh-CN/products/eilik) ｜ [EMO](https://www.roboatlas.ai/zh-CN/products/emo-emo) ｜ [LOOI](https://pocketanimus.com/guides/looi-robot/) ｜ [Moflin](https://www.roboatlas.ai/zh-CN/products/moflin) ｜ [Ropet](https://www.roboatlas.ai/en-US/products/ropet) ｜ [Jibo 单眼理由](http://www.irobotnews.com/news/articleView.html?idxno=6430) ｜ [Moxie](https://designwanted.com/humanizing-tech-yves-behar-moxie-embodied/) ｜ [Aibi](https://www.robophilosophy.com/archives/3566) ｜ [Ballie](https://m.163.com/dy/article/K3F6E3NB0514R9OJ.html) ｜ [Disney show 通道](https://www.core77.com/posts/133071) ｜ [Bubo-2T](https://www.kevsrobots.com/blog/bubo-2t.html) ｜ [Eyemech](https://www.kevsrobots.com/blog/eyemech.html) ｜ [Luxo/Pinokio](https://newatlas.com/pinokio-lamp-pixar-luxo-jr/25299/)（动作节奏细节抓取被拦截，**未取得精确数据**）
