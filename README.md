# chaoxing-video

> 学习通（超星）视频任务点自动化工具 — 2 倍速播放 + 任务点校验 + 反后台挂机

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-green)](https://playwright.dev/python/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## ⚠️ 先读这个

**如果你正在调试或使用这个工具，请务必先看 [docs/PITFALLS.md](docs/PITFALLS.md)。**

那里面记录了 23 个真实踩过的坑，包括：

- 为什么「重播」会导致页面被踢出去
- 为什么 93.5% 仍不算完成
- 为什么测验低分提交后**永远无法重做**
- 为什么直播回放用网页端刷不动
- 为什么 `aria-hidden` 会让所有点击失效

每一条都是实际调试出来的，能帮你省掉至少 6 小时的试错。

---

## 这是什么

这是我拿真实课程反复调试出来的学习通视频进度自动化脚本。它的设计哲学只有一句话：

> **宁慢勿死。**

市面上绝大多数刷课脚本死在两个地方：一是用 `video.currentTime` 直接跳进度（超星会比对心跳次数，必被抓），二是开一堆并发把账号搞崩。这个项目从第一行代码起就避开这两条路 —— **全程只调 `playbackRate`，严格单线程串行，每节刷完必须验证任务点真的降了才算成功。**

最终实测：一门 133 节的课程，视频任务点全部清零，无一节作废。

---

## 核心特性

| 特性 | 说明 |
|---|---|
| **真实 2 倍速** | 只调 `playbackRate = 2.0`，这是超星自带的合法上限。**从不篡改 `currentTime`** |
| **任务点校验** | 每节刷完读目录里的 `jobCount`，必须从 2 变成 1（或降到 0）才算完成；没降就自动重刷 |
| **反后台降频** | 切标签页 / 最小化 / 移开鼠标，视频继续播（14 个 Chromium 启动参数 + 页面级保活注入） |
| **自动跳过测验** | 检测到章节测验自动跳过，不点进去、不答题（做题交给 [chaoxing-quiz](../chaoxing-quiz)） |
| **异常即停** | 页面出现「异常 / 作废 / 验证码 / 人脸识别」任一可见信号，立刻停机 |
| **断点续播修正** | 起点 > 10 秒自动重新加载从 0 播，避免「只看了后半段」导致时长不足 90% |
| **真人节奏** | 节间随机停 3–8 秒，随机鼠标移动 / 滚动，消除零间隔连跳特征 |
| **登录态持久化** | 扫码一次，`state.json` 存 30 个 cookie，之后重启不用再扫 |

---

## 快速开始

### 1. 环境准备

```bash
# 需要 Python 3.9+
python --version

# 安装依赖
pip install playwright
playwright install chromium
```

### 2. 配置

复制示例配置：

```bash
cp config.example.json config.json
```

然后二选一：

**方式 A — 自动抓取（推荐）**

```bash
python setup.py
```

脚本会打开浏览器，你扫码登录后点进课程任意一章，它会自动把 `courseId / clazzid / chapterId / enc` 写进 `config.json`。

**方式 B — 手动填写**

进课程任意一章，地址栏里的 URL 长这样：

```
https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudy?chapterId=〔章节ID〕&courseId=〔课程ID〕&clazzid=〔班级ID〕&enc=〔签名〕
```

整条复制进 `config.json` 的 `course_url`。

> ⚠️ **`enc` 参数绝对不能丢**。超星新版用它做签名校验，丢了会提示「enc校验失败」。

### 3. 登录

```bash
python run.py --login
```

扫码。**手机上的「确认登录」必须点**，只扫码不确认永远登不进去。登录成功后会生成 `state.json`。

### 4. 扫描进度（可选）

```bash
python run.py --scan
```

输出：

```
章节总数: 133  已完成: 94  待刷: 39
待刷章节:
  3.9      jobCount=2
  13.1     jobCount=2
  ...
```

### 5. 开刷

```bash
python run.py
```

---

## 任务点（jobCount）是什么

学习通目录里每节后面有个小圆角标：

```html
<span class="roundpointStudent orange01 a002 jobCount">2</span>
```

数字含义：

| jobCount | 含义 |
|---|---|
| `2` | 视频 + 章节测验 **都没完成** |
| `1` | 视频已看完，**只剩章节测验** |
| `0` / 绿勾 / 无角标 | **全部完成** |

所以「刷课成功」的标准不是视频播完了，而是**这个数字从 2 降到 1**。本项目每节结束都会检查这一点，没降就重刷（默认最多 3 次）。

---

## 配置项详解

```jsonc
{
  "course_url": "https://...",       // 课程地址（含 enc 签名）

  "playback_rate": 2.0,              // 倍速。硬上限 2.0，超过会被强制压回
  "muted": true,                     // 静音播放

  "chapter_gap_sec": [3, 8],         // 节间随机停留秒数区间
  "mouse_noise_sec": [2, 5],         // 鼠标噪声持续秒数区间
  "keepalive_interval_sec": [8, 15], // 心跳轮询间隔

  "daily_max_minutes": 480,          // 单次运行时长上限（分钟）
  "max_chapters_per_run": 80,        // 单次运行节数上限

  "video_only": true,                // 只跑视频，遇到测验自动跳过
  "minimize_on_start": false,        // 启动后自动最小化窗口

  "job_verify_timeout_sec": 90,      // 任务点校验等待时间
  "job_retry_max": 3,                // 任务点未降时最多重刷次数

  "force_from_start": true,          // 起点靠后时强制从 0 重播
  "restart_threshold_sec": 10,       // 超过这个秒数就触发重播

  "safety": {
    "stop_on_anomaly": true,         // 检测到异常立即停机
    "stop_on_quiz": true,            // 检测到测验跳过
    "min_video_progress_ratio": 0.995 // 进度达到 99.5% 即算完成
  }
}
```

**关于 `minimize_on_start`**：超星页面明确写了「观看时不可离开或将页面最小化」。虽然本项目的注入脚本能拦住 `visibilitychange` / `blur` 事件（实测最小化 25 秒视频照跑不误），但**如果老师开了严格监考模式，仍有可能被记录**。想要最稳，就保持 `false`，让窗口正常待着，你可以切到别的窗口但别最小化。

---

## 反后台挂机是怎么实现的

这是本项目最关键的技术点。普通脚本一最小化就停，因为 Chromium 有两层限制：

### 第一层：浏览器进程级降频

Chromium 默认会把后台标签页的定时器降到 **1 次/分钟**，并冻结渲染。学习通播放器的心跳是 `setInterval` 驱动的，一降频就会被判定为「未在观看」。

解决办法是这些启动参数：

```
--disable-background-timer-throttling           # 不定时器降频
--disable-backgrounding-occluded-windows        # 被遮挡的窗口不降优先级
--disable-renderer-backgrounding                # 渲染进程不降级
--disable-features=CalculateNativeWinOcclusion  # 不算窗口遮挡
--disable-features=IntensiveWakeUpThrottling    # 不强制 1 分钟唤醒上限
--disable-background-media-suspend              # 后台不暂停媒体
```

### 第二层：页面级事件监听

超星播放器自己会监听 `visibilitychange` 和 `blur`，一触发就 `video.pause()`。所以还要在页面里注入拦截脚本：

```javascript
// 1. 可见性永远报 visible
Object.defineProperty(document, 'hidden', {get: () => false});
Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});

// 2. 吞掉相关事件（捕获阶段就拦住）
['visibilitychange', 'blur', 'pagehide', 'freeze'].forEach(t => {
    window.addEventListener(t, e => {
        e.stopImmediatePropagation();
        e.stopPropagation();
    }, true);
});

// 3. hasFocus 永远 true
document.hasFocus = () => true;

// 4. 无条件锁速（800ms 轮询）
setInterval(() => {
    const v = document.querySelector('video');
    if (v && Math.abs(v.playbackRate - 2.0) > 0.01) v.playbackRate = 2.0;
    if (v && v.paused && !v.ended) v.play().catch(() => {});
}, 800);

// 5. 拦截 playbackRate 的 setter
const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'playbackRate');
Object.defineProperty(HTMLMediaElement.prototype, 'playbackRate', {
    set(val) {
        if (Math.abs(val - 2.0) > 0.01) return d.set.call(this, 2.0);
        return d.set.call(this, val);
    }
});

// 6. 拦 pause()，只允许自然结束
const origPause = HTMLMediaElement.prototype.pause;
HTMLMediaElement.prototype.pause = function () {
    if (!this.ended && this.readyState > 2) return;
    return origPause.apply(this, arguments);
};
```

**实测结果**（本地播放真实 MP4，切后台 + 最小化 25 秒）：

| 指标 | 期望 | 实测 |
|---|---|---|
| 播放时长增量 | ≥ 46.2s（25s × 2x） | **46.62s** ✅ |
| `visibilitychange` 事件 | 0 | **0** ✅ |
| `blur` 事件 | 0 | **0** ✅ |
| `pause` 事件 | 0 | **0** ✅ |
| `playbackRate` | 2.0 | **2.0** ✅ |

---

## 踩过的坑（这个项目 90% 的价值在这里）

这些都是真实调试中撞到的，不是查文档能查出来的。

### 1. `enc` 签名参数丢失 → 「enc校验失败」

第一版我为了「规范化」URL，把它重拼成了标准格式，结果把 `enc` 参数扔了，页面直接白屏报「enc校验失败」。

超星新版用 `enc` 做课程级签名，**同一条 URL 换 `chapterId` 可以复用同一个 `enc`**，但绝不能丢。

### 2. `launch_persistent_context` 不接受 `storage_state`

我一开始想用 `storage_state=` 直接注入登录态，报 `TypeError: unexpected keyword argument`。

持久化上下文本来就用 `user_data_dir` 存状态，再传 `storage_state` 是冲突的。正确做法是 `ctx.add_cookies()` 手动注入。

### 3. `fid` 不是登录凭证

我一开始把 `fid` 也算进认证 cookie，结果脚本永远判定「已登录」，实际页面一直被跳回登录页。

`fid` 只是**学校 ID**（默认 12），未登录状态也存在。真正的认证 cookie 是 `_uid / UID / uf / _d` 四个。

### 4. 「验证码」误报 → 脚本一启动就停机

超星页面内嵌了大量隐藏的预制弹窗：

```html
<div class="AlertCon02" id="validate" style="display:none">请输入验证码：</div>
<div class="AlertCon02" id="chapterVerificationCode" style="display:none">为保障您的账号安全，请输入验证码</div>
```

我第一版用 `page.content()` 全文匹配，一进来就看到 8 个「验证码」，立刻误判停机。

坑在于：**父级 `display:none` 时，子元素自身的 `display` 仍然是 `block`**。所以不能只看自身的 computedStyle，必须用 `getBoundingClientRect()` 的真实尺寸 + `el.checkVisibility()`：

```javascript
const r = el.getBoundingClientRect();
if (r.width <= 0 || r.height <= 0) continue;   // 被祖先隐藏了
if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
```

### 5. 「章节测验」导航按钮被当成弹题

页面顶部常驻有「视频 / 章节测验」两个 tab，其中「章节测验」是 `#dct2`。我第一版用文本匹配找题目，一进页面就命中这个 tab，判定为「有弹题」然后跳过。

修正后只在**视频 frame 内部**找题目容器，或找 **z-index ≥ 100 的真模态层**。

### 6. 下一节按钮在视口外 → 点不到

底部「下一节」按钮的坐标是 `[520, 902, 102, 42]`，而窗口高度只有 900px。它在视口外，Playwright 直接点会失败。

我试过 `PCount.next()`（页面自带的导航函数），但它读的是 `onclick` 属性里的旧参数，调了不跳。

**最终方案：点右侧目录里的章节项**。目录永远可见可点，而且和真人操作完全一致，最自然。

### 7. 从中间续播 → 观看时长不足 90%

超星的完成条件是「观看时长 ≥ 总时长的 90%」。如果某个视频你之前看到 601 秒中断了，脚本从 601 秒继续，那实际只看了 198 秒，达不到要求。

修正：**起点 > 10 秒时，重新加载视频源从 0 播**。注意不是拖进度条（那会被记录 drag 行为），而是 `v.removeAttribute('src'); v.load(); v.src = src; v.load();` —— 等价于刷新播放器。

### 8. 任务点基线取错了 → 永远判定失败

第一版我在**视频播完之后**才读 `jobCount` 作为基线，那时已经是 1 了，然后拿 1 和 1 比，永远「没变化」。

修正：**开刷前读基线**，刷完再比。2 → 1 才算成功。如果开刷前就是 1，说明视频早刷过了，直接判通过、不重刷。

### 9. `readyState = 0` 被判死 → 整节跳过

有极少数章节（我遇到的是 3.9），播放器首次挂载时 `load()` 没被正确触发，`video.readyState` 卡在 0，`duration` 永远是 `NaN`。

脚本看到这种情况直接判 `no_video` 跳过。这个漏洞让 3.9 连续两天刷不动。

修正：**先尝试修复，再判定失败**：

```javascript
const src = v.currentSrc || v.src;
v.src = src; v.muted = true; v.load();
// 等最多 10 秒看 readyState 是否 >= 1
```

实测重载后 `readyState` 立刻变 4，`duration` = 1320.362s，正常播放。

### 10. `aria-hidden` 挡住 JS click

这条在做题项目里遇到的，但同样适用于任何点击操作：

超星的选项 `ul` 带 `aria-hidden="true"`，所以 `li.click()` 这种 JS 调用**被浏览器可访问性层直接吞掉**，checkbox 仍是 `false`，hidden input 仍是空。

只有 **Playwright 的真实鼠标事件**（或直接调页面业务函数 `addcheck(qid)`）才能触发。

---

## 防作废的完整链路

每一节都走这一套：

```
1. 记录基线        → jobCount = 2
2. 检查视频起点    → 起点 > 10s 就重载从 0 播
3. 2 倍速播放      → 最小化也继续
4. 每 8-15s 心跳   → 查健康状态、倍速、暂停、进度
5. 视频结束        → 校验 jobCount
6. 2 → 1 判定成功  → 没变则重刷（最多 3 次）
7. 随机停 3-8 秒   → 鼠标移动 + 滚动
8. 点目录进下一节
```

外加这些保险：

- **倍速硬上限**：`load_cfg()` 里拦截，> 2.0 直接压回
- **绝不碰 `currentTime`**
- **严格单线程**，不并发课程
- **异常即停**：可见文本出现「异常/作废/验证码/人脸识别/请重新登录」→ 立刻停机且不自动重试
- **进度卡死处理**：90 秒不动 → 先自愈（force_play）→ 再点击播放按钮 → 都不行才判 stall
- **单次上限**：480 分钟 / 80 节，到点自动停

---

## 命令行参数

```bash
python run.py              # 正常刷课
python run.py --login      # 只做登录，保存 state.json
python run.py --scan       # 扫描全课程任务点状态，不刷
python run.py --dry        # 只校验配置
```

---

## 目录结构

```
chaoxing-video/
├── run.py                  # 主引擎
├── setup.py                # 配置向导（扫码 + 自动抓参数）
├── config.json             # 你的配置（.gitignore 已排除）
├── config.example.json     # 配置模板
├── state.json              # 登录态（.gitignore 已排除）
├── profile/                # 浏览器持久化数据（.gitignore 已排除）
└── logs/
    └── run_YYYYMMDD_HHMMSS.log
```

---

## 常见问题

**Q: 提示「enc校验失败」怎么办？**
`course_url` 里的 `enc` 参数丢了或过期了。重跑 `python setup.py` 重新抓一次。

**Q: 提示「未找到 state.json」？**
先跑 `python run.py --login`。

**Q: 一直停在「等待刷新」？**
任务点没降。可能是视频时长不够（脚本会重刷），也可能是那节的视频本身有问题。看日志里有没有 `readyState=0`，有的话就是第 9 条坑。

**Q: 能不能用 3 倍速？**
不能。超星合法上限就是 2.0，脚本会强制压回。改更高倍速只会让播放器上报异常。

**Q: 会不会被封号？**
本项目的设计就是在模拟真人：真实 2 倍速、随机节奏、单线程、不跳进度。但任何自动化都有风险，**建议每天控制在 4-6 小时以内**，别一天刷完整门课。

**Q: 做完视频了，章节测验怎么办？**
用配套项目 [chaoxing-quiz](../chaoxing-quiz)。

---

## 特殊章节类型

除了普通视频，超星还有几种特殊任务点，处理方式完全不同：

### 直播回放

```html
<!-- frame: ananas/modules/live/index.html -->
<div class="live-tit">繁荣互联网条件下新大众文艺</div>
<span class="liveBtn live_blue">可回放</span>
你已观看 0 分钟（时长每5分钟更新一次），当前观看进度为 0%
```

**特征**：
- 没有 `<video>` 元素
- 点「可回放」会打开新标签页 `zhibo.chaoxing.com/xxx`
- 进度每 5 分钟才更新一次
- **网页端挂着可能不计时**（实测 4 小时一直停在 10.75%）

**建议**：直播回放用**手机 App** 看最稳，网页自动化这条路走不通。

### 阅读任务

```html
<!-- frame: mooc-ans/coursedata/readjobv2/show -->
您的阅读总时长: 0.0 分钟(当天阅读时长，次日更新)
习近平法治思想学习纲要
[去阅读]
```

**特征**：
- 没有 video，没有 duration
- 需要点「去阅读」打开阅读器
- **阅读时长次日才更新**，当天看不出效果

### 多分讲章节

```html
<!-- 顶部 tab -->
<span id="dct1" class="c1 currents">第一讲</span>
<span id="dct2" class="c2">第二讲</span>
```

**特征**：
- 每讲是独立的 `chapterId`
- 切 tab 是 AJAX，URL 变但页面不刷新
- 切 tab 后 frame 重建，要重新找 video

---

## 配套项目

- **[chaoxing-quiz](../chaoxing-quiz)** — 章节测验自动提交，把 jobCount 从 1 变成 0

---

## 免责声明

本项目仅供学习 Playwright 自动化、浏览器对抗技术使用。使用者需自行承担使用后果，作者不对任何账号处罚、学业影响承担责任。请遵守所在学校的相关规定。

---

## License

MIT
