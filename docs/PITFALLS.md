# 实战踩坑全记录

> 这份文档记录了在真实课程上调试这个工具时遇到的所有问题。
> 每一条都是真金白银换来的，不是查文档能查出来的。

---

## 目录

- [一、结构篇：先搞清课程长什么样](#一结构篇先搞清课程长什么样)
- [二、登录与鉴权](#二登录与鉴权)
- [三、视频播放](#三视频播放)
- [四、任务点判定](#四任务点判定)
- [五、直播与阅读（特殊任务点）](#五直播与阅读特殊任务点)
- [六、测验与答题](#六测验与答题)
- [七、页面稳定性](#七页面稳定性)
- [八、汇总表](#八汇总表)

---

## 一、结构篇：先搞清课程长什么样

### 1.1 同一门课可能有完全不同的页面结构

这是最大的坑。我遇到三种完全不同的课程结构，**代码必须自适应，不能假设**。

#### 结构 A：单视频 + 章节测验

```
顶部 tab: [视频] [章节测验]
jobCount = 2  →  视频 + 测验都没做
jobCount = 1  →  视频完成，剩测验
jobCount = 0  →  全完成

frame: ananas/modules/video/index.html
测验:  ananas/modules/work/doHomeWorkNew
```

这是最"标准"的结构，`chaoxing-quiz` 就是为它写的。

#### 结构 B：多分讲（第一讲 / 第二讲）

```
顶部 tab: [第一讲] [第二讲]
每个 tab 是独立的 chapterId，独立视频

jobCount = 2  →  两讲都没看
jobCount = 1  →  看了一讲
jobCount = 0  →  全看完
```

**关键坑**：每点一个 tab，`chapterId` 会变，URL 会变，但**页面不刷新**（是 AJAX 切换）。所以：

- 不能靠 URL 判断当前在哪个 tab
- 切 tab 后 frame 会重建，必须重新 `find_video_frame`
- 切 tab 后要等 8-12 秒让新视频加载

#### 结构 C：课程视频 + 章节测验（但 jobCount 语义不同）

```
jobCount = 2  →  视频 + 测验两个任务点
jobCount = 1  →  只完成了一个（通常是视频）
jobCount = 0  →  都完成

注意：这里有"总分制"，测验分数不够任务点不给
```

**这门课的关键**：测验要**及格**（60 分）才算完成，不是"提交了就算"。低分提交后**没有重做入口**。

### 1.2 三种入口 URL

| 入口 | URL 特征 | 说明 |
|---|---|---|
| 课程列表 | `/visit/courses` | 登录后跳转处 |
| 中间页 | `/visit/stucoursemiddle?courseid=` | 部分课程，可能弹人脸 |
| 学生课程页 | `/mooc-ans/mycourse/studentcourse?courseId=` | **最稳，优先用这个** |
| 学习页 | `/mooc-ans/mycourse/studentstudy?chapterId=` | 真正刷课的地方 |

**关键教训**：`stucoursemiddle` 是中间页，很多课在这里会弹人脸采集或课程须知。**能直接走 `studentcourse` 就不要走中间页。**

你的浏览器里是什么 URL，就用什么 URL。抓参数时照抄整条地址。

### 1.3 人脸识别门

有些课在进学习页前必须人脸采集：

```html
<div class="maskDiv">
  <div class="popDiv faceCollectQrPop">
    <div class="popHead">人脸信息采集</div>
    <p>请使用手机APP采集人脸信息后继续学习</p>
    <img id="fcqrimg" src="...">
  </div>
</div>
```

**处理方式**：
1. 检测弹窗是否存在（用 `checkVisibility`，别看 `display`）
2. 截图二维码 `#fcqrimg` 给用户扫
3. **窗口保持开启，无限等待** —— 人脸会话绑定浏览器进程，进程一退就失效
4. 检测到 URL 变成 `studentstudy?...chapterId=` 就说明通过

**踩过的坑**：人脸等待设置了 120 秒超时，超时后进程退出，用户得重新扫。改成无限轮询后解决。

---

## 二、登录与鉴权

### 2.1 `fid` 不是登录凭证

```python
# ❌ 错误：fid 是学校 ID（默认 12），未登录也存在
if names & {"_uid", "UID", "fid", "uf", "_d"}:
    return True

# ✅ 正确：只认这四个
STRONG_AUTH = {"_uid", "UID", "uf", "_d"}
```

### 2.2 session cookie 关不掉浏览器就丢

新账号的 `_uid / UID / _d / uf` 往往是 **session cookie**（`expires = -1`），**浏览器进程一关就消失**。

**这导致一个硬约束**：扫码登录 + 刷课必须在**同一个进程**里完成。分两次运行，登录态就丢了。

**解决方案**：
- 要么做成一体化脚本（`allinone.py`）
- 要么让用户在手机 App 里勾选「下次自动登录」，让 cookie 持久化

### 2.3 `launch_persistent_context` 不支持 `storage_state`

```python
# ❌ 报错：TypeError: unexpected keyword argument 'storage_state'
ctx = pw.chromium.launch_persistent_context(
    user_data_dir=str(PROFILE),
    storage_state=str(STATE_PATH),   # 不支持
)

# ✅ 手动注入 cookie
ctx = pw.chromium.launch_persistent_context(user_data_dir=str(PROFILE), ...)
st = json.loads(STATE_PATH.read_text())
clean = []
for c in st["cookies"]:
    ck = {"name": c["name"], "value": c["value"],
          "domain": c.get("domain", ".chaoxing.com"),
          "path": c.get("path", "/")}
    if c.get("sameSite") in ("Strict", "Lax", "None"):
        ck["sameSite"] = c["sameSite"]
    if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
        ck["expires"] = float(c["expires"])
    clean.append(ck)
ctx.add_cookies(clean)
```

### 2.4 `enc` 签名参数不能丢

新版超星 URL 带 `enc=xxx`：

```
.../studentstudy?chapterId=123&courseId=456&clazzid=789&enc=〔签名〕...
```

**这个参数是课程级签名**，同一个 `enc` 换 `chapterId` 可以复用，但**丢了就会「enc校验失败」**。

```python
# ❌ 错误：自己拼标准格式，把 enc 扔了
standard = f"https://mooc1.chaoxing.com/mycourse/studentstudy?courseId={cid}&clazzid={cl}&chapterId={ch}"

# ✅ 正确：沿用原始 URL，只做最小规范化
standard = re.sub(r"https://mooc1-\d+\.chaoxing\.com",
                  "https://mooc1.chaoxing.com", url).split("#")[0]
```

---

## 三、视频播放

### 3.1 只调 `playbackRate`，绝不碰 `currentTime`

```javascript
// ❌ 死路：超星比对心跳次数，跳变必被抓
video.currentTime = video.duration - 5;

// ✅ 唯一安全的路：只调倍速
video.playbackRate = 2.0;
```

超星合法上限就是 **2.0 倍**，超过会被判异常。

### 3.2 强行锁倍速

播放器会自己把倍速改回 1.0。要在两层拦：

```javascript
// 第一层：800ms 轮询
setInterval(() => {
    if (Math.abs(v.playbackRate - 2.0) > 0.01) v.playbackRate = 2.0;
}, 800);

// 第二层：拦截 setter
const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'playbackRate');
Object.defineProperty(HTMLMediaElement.prototype, 'playbackRate', {
    set(val) {
        if (Math.abs(val - 2.0) > 0.01) return d.set.call(this, 2.0);
        return d.set.call(this, val);
    }
});
```

### 3.3 `readyState = 0` 的坑

有些章节播放器首次挂载时 `load()` 没被正确触发：

```
readyState = 0     ← 元数据都没加载
duration   = NaN
```

**修复**：重设 src 再 load

```javascript
const src = video.currentSrc || video.src;
video.src = src;
video.muted = true;
video.load();
// 等 10 秒看 readyState 是否 >= 1
```

实测：3.9 这一节连续两天刷不动，就是卡在 `readyState=0`。加上这段后立刻正常，`duration=1320.362s`。

### 3.4 反后台降频（14 个 Chromium 参数）

Chromium 默认把后台标签页定时器降到 1 次/分钟，学习通心跳就断了：

```python
args = [
    "--disable-background-timer-throttling",      # 定时器不降频
    "--disable-backgrounding-occluded-windows",   # 遮挡窗口不降优先级
    "--disable-renderer-backgrounding",           # 渲染进程不降级
    "--disable-features=CalculateNativeWinOcclusion",  # 不算窗口遮挡
    "--disable-features=IntensiveWakeUpThrottling",    # 不强制 1 分钟唤醒
    "--disable-background-media-suspend",         # 后台不暂停媒体
    "--autoplay-policy=no-user-gesture-required", # 免手势自动播放
]
```

### 3.5 页面级保活注入

播放器自己监听 `visibilitychange` 和 `blur`，一触发就 `pause()`：

```javascript
// 可见性永远报 visible
Object.defineProperty(document, 'hidden', {get: () => false});
Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});

// 捕获阶段就吞掉事件
['visibilitychange', 'webkitvisibilitychange', 'blur', 'pagehide', 'freeze']
  .forEach(t => {
    window.addEventListener(t, e => {
        e.stopImmediatePropagation();
        e.stopPropagation();
    }, true);
    document.addEventListener(t, e => {
        e.stopImmediatePropagation();
        e.stopPropagation();
    }, true);
  });

// hasFocus 永远 true
document.hasFocus = () => true;

// 拦 pause()
const origPause = HTMLMediaElement.prototype.pause;
HTMLMediaElement.prototype.pause = function () {
    if (!this.ended && this.readyState > 2) return;   // 静默忽略
    return origPause.apply(this, arguments);
};
```

**实测数据**（本地 MP4，切后台 + 最小化 25 秒）：

| 指标 | 期望 | 实测 |
|---|---|---|
| 播放时长增量 | ≥ 46.2s（25s × 2x） | **46.62s** ✅ |
| `visibilitychange` 事件 | 0 | **0** ✅ |
| `blur` 事件 | 0 | **0** ✅ |
| `pause` 事件 | 0 | **0** ✅ |
| `playbackRate` | 2.0 | **2.0** ✅ |

### 3.6 千万不要「重播」

这是最痛的坑。想从 0 重播，写了：

```javascript
// ❌ 触发超星风控，页面被踢回首页
v.pause();
v.removeAttribute('src');
v.load();
v.src = src;
v.load();
```

后果：页面**直接重定向**到 `i.mooc.chaoxing.com/space/index`，整个学习页没了。

```
[12:17:48] 页面跳走: https://i.mooc.chaoxing.com/space/index?ws=1&t=...
```

**正确做法：无论起点在哪，直接续播。**

观看时长由服务端累计，从 300 秒续播一样能凑够时长。不需要从头看。

---

## 四、任务点判定

### 4.1 `jobCount` 的真实语义

```html
<span class="roundpointStudent orange01 a002 jobCount">2</span>
```

| 值 | 语义（结构 A） | 语义（结构 C） |
|---|---|---|
| `2` | 视频 + 测验未做 | 两个任务点都未做 |
| `1` | 视频完成，剩测验 | 完成一个 |
| `0` / 绿勾 / 无角标 | 全完成 | 全完成 |

**关键**：`1` **不代表完成**，`0` 才算。

### 4.2 基线必须开刷前读

```python
# ❌ 错误：播完才读基线，那时已经是 1，跟 1 比永远"没变化"
result = learn_one(page)
baseline = get_job_count(page, ch_no)   # 已经是 1 了

# ✅ 正确：开刷前读
baseline = get_job_count(page, ch_no)   # 2
result = learn_one(page)
job_count_cleared(page, ch_no, baseline)  # 2 -> 1 算完成
```

### 4.3 90% 可能不够，要 100%

超星写着「观看时长 ≥ 总时长 90%」，但实测：

```
1.1  cur=472.98s / dur=505.77s = 93.5%
     结果：jobCount 仍是 1
```

**所以目标是 100%，不是 90%。**

```python
TARGET_RATIO = 0.999   # 目标 99.9%
```

---

## 五、直播与阅读（特殊任务点）

### 5.1 直播的结构

```html
<!-- frame: ananas/modules/live/index.html -->
<div class="live-tit">繁荣互联网条件下新大众文艺</div>
预告时间：09-29 14:30  主播：思政课26秋季  <span class="liveBtn live_blue">可回放</span>
观看时长达到总时长的90%可完成任务点。
你已观看 0 分钟（时长每5分钟更新一次），当前观看进度为 0%
```

**关键点**：

1. **没有 `<video>` 元素**。所以 `find_video_frame` 找不到，别用视频逻辑处理。
2. **按钮文字是「可回放」**，不是「回放」或「播放」。关键字要覆盖。
3. **点「可回放」会打开新标签页** `zhibo.chaoxing.com/xxx`，视频在那边播。
4. **进度每 5 分钟才更新一次**，轮询看不出实时变化。
5. **session cookie 可能在回放页失效**，导致中途跳走。

### 5.2 直播处理流程

```
1. 找 live frame
2. 读当前进度（正则匹配「你已观看 X 分钟」「观看进度为 Y%」）
3. 点「可回放」按钮（.liveBtn）
4. 检测新标签页（zhibo.chaoxing.com）
5. 在新页面里播放 video
6. 回到主页面轮询 live frame 的进度文本
7. 达到 90% 完成
```

代码：

```python
# 点按钮
clicked = frame.evaluate("""()=>{
    const kws = ['可回放','进入回放','回放','播放','开始观看'];
    for(const kw of kws){
        for(const el of document.querySelectorAll('*')){
            if((el.innerText||'').trim() !== kw) continue;
            const r=el.getBoundingClientRect();
            if(r.width<=0||r.height<=0) continue;
            el.click();
            return kw;
        }
    }
    return '';
}""")

# 找新标签页
play_page = None
for p in ctx.pages:
    if "zhibo.chaoxing.com" in p.url:
        play_page = p
        break

# 读进度
txt = frame.evaluate("()=>(document.body.innerText||'')")
seen = re.search(r"已观看\s*(\d+(?:\.\d+)?)\s*分钟", txt)
pct  = re.search(r"观看进度为\s*(\d+(?:\.\d+)?)\s*%", txt)
```

### 5.3 直播的实测结果

我跑了一下午，8.1 的进度**一直停在 10.75%**：

```
[14:30] 进度 10.75%（6.25 分钟）
[15:30] 进度 10.75%（6.25 分钟）
[16:30] 进度 10.75%（6.25 分钟）
[17:23] 进度 10.75%（6.25 分钟）
[17:28] 超时，最后进度 10.75%
```

**结论**：超星直播的时长记录**可能只认前台真实观看**。后台挂着、最小化、切窗口都不计时。

**建议**：直播回放**用手机 App 看最稳**。网页端自动化这条路没走通。

### 5.4 阅读任务

```html
<!-- frame: ananas/modules/read/indexV2.html -->
<!-- frame: mooc-ans/coursedata/readjobv2/show -->

阅读
您的阅读总时长: 0.0 分钟(当天阅读时长，次日更新), 点击去阅读，可完成阅读任务点
习近平法治思想学习纲要
[去阅读]
```

**关键点**：

1. 没有 video，也没有 duration
2. 需要点「去阅读」打开阅读器
3. **阅读时长当天不更新，次日才刷新** —— 所以当天看不出效果
4. 需要滚动页面制造阅读痕迹

**处理方式**：

```python
# 点「去阅读」
frame.evaluate("""()=>{
    for(const kw of ['去阅读','开始阅读']){
        for(const el of document.querySelectorAll('a,button,span,div')){
            if((el.innerText||'').trim() !== kw) continue;
            const r=el.getBoundingClientRect();
            if(r.width<=0||r.height<=0) continue;
            el.click();
            return kw;
        }
    }
    return '';
}""")

# 停留 + 滚动
while elapsed < max_minutes:
    target.evaluate("() => window.scrollBy(0, 300 + Math.random()*400)")
    time.sleep(25)
```

**注意**：阅读时长是**次日更新**的。所以当天做完看不到效果，要等第二天刷新页面才能确认。

---

## 六、测验与答题

### 6.1 `aria-hidden` 挡住 JS click

这是选择题最难的地方。超星的选项 `ul` 带 `aria-hidden="true"`：

```html
<ul class="Zy_ulTop" tabindex="-1" aria-hidden="true">
  <li role="radio" aria-label="A 80；25选择">
    <label class="fl before"><input type="radio" name="answerXXX" value="A">&nbsp;&nbsp;A</label>
    <a class="fl after">80；25</a>
  </li>
</ul>
```

**实测对比**：

| 方式 | 结果 |
|---|---|
| `el.click()`（JS） | ❌ `cbs: [false,false,false,false]`, `hidden: ''` |
| `label.click()` | ❌ 无效 |
| `input.click()` | ❌ 无效 |
| **Playwright 真实点击** | ✅ `cbs: [true,false,false,false]`, `hidden: 'A'` |
| 调 `addcheck(qid)` | ✅ 有效 |

**原因**：`aria-hidden` 让浏览器在可访问性层面隐藏元素，合成 click 被吞掉。

**正确做法**：

```python
# 方式 A：Playwright 真实点击（首选）
loc = frame.locator(".TiMu").nth(q - 1).locator("li").nth(li_idx)
loc.scroll_into_view_if_needed(timeout=3000)
loc.click(timeout=4000, force=True)

# 方式 B：兜底，调页面业务函数
frame.evaluate("""({qid, liIdx}) => {
    const t = document.querySelector('.TiMu[data="' + qid + '"]');
    const li = t.querySelectorAll('li')[liIdx];
    const m = (li.getAttribute('onclick') || '').match(/addcheck\\((\\d+)\\)/i);
    if (m && typeof addcheck === 'function') addcheck(m[1]);
}""", {"qid": qid, "liIdx": li_idx})
```

### 6.2 纯多选题的 li 里没有 input

```html
<!-- 单选题：li 里有 input -->
<li role="radio">
  <label><input type="radio" value="A"> A</label>
</li>

<!-- 多选题：li 里没有 input，只有 onclick -->
<li role="checkbox" class="before-after-checkbox" onclick="addcheck(405887733);">
  <label></label>
</li>
```

**所以筛选不能用 `li.querySelector('input[type=checkbox]')`**，会得到空数组。

**正确判定**：

```javascript
const checkLis = allLis.filter(li => {
    const role = (li.getAttribute('role') || '').toLowerCase();
    if (role === 'checkbox') return true;
    if (/before-after-checkbox/.test((li.className||'').toString())) return true;
    if (/addcheck/i.test(li.getAttribute('onclick') || '')) return true;
    return false;
});
```

### 6.3 选项字母三路兜底

题目选项字母可能藏在三个地方：

```javascript
// 路径 1：aria-label 开头 "A xxx选择"
const aria = li.getAttribute('aria-label') || '';
const m = aria.match(/^([A-H])[\s.、]/);

// 路径 2：label 文字里的字母
const lbl = li.querySelector('label');
const m2 = (lbl.innerText || '').match(/([A-H])/);

// 路径 3：input 的 value
const inp = li.querySelector('input[type=radio], input[type=checkbox]');
if (/^[A-H]$/.test(inp.value)) 用 inp.value;

// 路径 4：按顺序推断（兜底）
letter = String.fromCharCode(65 + idx);
```

之前只用了路径 3，遇到纯多选题就全部提取失败（`作答 0/2 题`）。

### 6.4 判断题要单独处理

超星的判断题：

```html
<div class="TiMu singleQuesId" data="406039340">
  <div class="Zy_TItle">
    <div class="clearfix">【判断题】新时代党的建设取得历史性成就...</div>
  </div>
  <!-- 没有 li，没有 input！ -->
</div>
```

**判断题没有选项**，答案是「正确/错误」二选一。如果当单选题处理，会提取不到选项，导致作废。

**正确判定**：

```javascript
const declared = (t.innerText || '').match(/【(.+?)】/)?.[1] || '';
if (declared.includes('判断')) {
    type = 'judge';   // 特殊处理
}
```

### 6.5 测验分不够不给任务点

有些课设置「总分制」：

```
jobCount = 2  →  视频 + 测验
测验必须及格（60分）才消掉任务点
```

**致命坑**：低分提交后**没有重做入口**。

```html
<div class="Py_answer">
  <span>我的答案：</span>
  <i class="fr cuo" aria-label="答案错误"></i>
  <span>得分：<span style="color:#db2727;">0.0分</span></span>
</div>
```

页面显示批改结果，**没有任何「重做」按钮**。

**结论**：**第一次提交必须拿够分**，没有第二次机会。所以要么用 AI 认真答，要么干脆别提交。

### 6.6 两种测验接口

| 接口 | 说明 |
|---|---|
| `doHomeWorkNew` | 部分章节 |
| `selectWorkQuestionYiPiYue` | 大部分章节 |

`find_quiz_frame` 必须两个都认：

```python
keys = ("doHomeWorkNew", "selectWorkQuestionYiPiYue", "/work/", "doHomeWork")
for f in page.frames:
    if any(k in f.url for k in keys):
        if f.locator(".TiMu").count() > 0:
            return f
```

之前只认 `doHomeWorkNew`，导致 30 节里只有 1 节能识别。

---

## 七、页面稳定性

### 7.1 `TargetClosedError` 会打断一切

```
playwright._impl._errors.TargetClosedError:
  Page.wait_for_timeout: Target page, context or browser has been closed
```

**原因**：浏览器进程被外部关闭（手动关窗口、系统休眠、其他程序干预）。

**修复**：所有等待都套一层

```python
def safe_wait(page, ms):
    """安全等待。页面/浏览器被关掉时不抛异常。"""
    try:
        page.wait_for_timeout(ms)
        return True
    except Exception:
        return False
```

### 7.2 隐藏容器的「假文本」

超星页面内嵌大量隐藏预制弹窗：

```html
<div class="AlertCon02" id="validate" style="display:none">请输入验证码：</div>
<div class="AlertCon02" id="chapterVerificationCode" style="display:none">
  为保障您的账号安全，请输入验证码
</div>
```

用 `page.content()` 全文匹配会命中 8 处「验证码」，误判停机。

**关键坑**：父级 `display:none` 时，子元素自身 `display` 仍是 `block`。所以不能只看自身的 computedStyle。

**正确做法**：

```javascript
const r = el.getBoundingClientRect();
if (r.width <= 0 || r.height <= 0) continue;   // 被祖先隐藏了

if (el.checkVisibility) {
    if (!el.checkVisibility({
        checkOpacity: true,
        checkVisibilityCSS: true,
        contentVisibilityAuto: true,
    })) continue;
}
```

### 7.3 导航栏的「章节测验」不是弹题

页面顶部常驻 tab：

```html
<span id="dct2" class="c2">章节测验</span>
```

按钮位置 `[522, 162, 120, 37]`，是**栏目入口**，不是弹题。

用文本匹配会立刻命中它，导致每节都判「有弹题」然后跳过。

**正确做法**：只在视频 frame 内部找题目容器，或找 **z-index ≥ 100 的真模态层**。

### 7.4 下一节按钮在视口外

底部「下一节」按钮坐标 `[520, 902, 102, 42]`，窗口高度只有 900。

**方案对比**：

| 方案 | 结果 |
|---|---|
| Playwright 直接点 | ❌ 元素在视口外 |
| `scrollIntoView` 再点 | ⚠️ 有时被遮挡 |
| `PCount.next()` | ❌ 读的是 onclick 里的旧参数，调了不跳 |
| **点目录里的章节项** | ✅ 永远可见可点，最自然 |

---

## 八、汇总表

### 8.1 所有踩过的坑

| # | 坑 | 症状 | 修复 |
|---|---|---|---|
| 1 | `enc` 签名丢失 | 「enc校验失败」白屏 | 沿用原始 URL，不重拼 |
| 2 | `storage_state` 不支持 | TypeError | 手动 `add_cookies` |
| 3 | `fid` 当成登录凭证 | 永远「已登录」，页面被踢 | 只认 `_uid/UID/uf/_d` |
| 4 | session cookie 丢 | 分进程运行登录态消失 | 一体化脚本 |
| 5 | 隐藏验证码误报 | 一启动就停机 | `getBoundingClientRect` + `checkVisibility` |
| 6 | 导航栏「章节测验」误判 | 每节都被跳过 | 只查 frame 内 / 模态层 |
| 7 | 下一节按钮在视口外 | 点不到 | 点目录项 |
| 8 | `readyState=0` 判死 | 3.9 连续两天刷不动 | 重设 src + load |
| 9 | 任务点基线取错 | 变 1 后仍判「没变化」 | 开刷前读基线 |
| 10 | 重播触发风控 | 页面重定向到首页 | 删掉重播，直接续播 |
| 11 | 90% 不够 | 93.5% 仍 jobCount=1 | 目标 100% |
| 12 | `aria-hidden` 挡 click | 选择题永远选不上 | Playwright 真实点击 |
| 13 | 多选题 li 无 input | 提取到空数组 | 按 role/class/onclick 判 |
| 14 | 选项字母三路都没采 | 作答 0/N 题 | 四路兜底 |
| 15 | 判断题当单选 | 提取失败 | 识别 `【判断题】` |
| 16 | 两种测验接口 | 30 节只认得 1 节 | 两个关键字都认 |
| 17 | 测验低分无重做 | 任务点永久卡住 | **必须一次拿够分** |
| 18 | `TargetClosedError` | 脚本崩溃 | `safe_wait` 包装 |
| 19 | 直播按钮文字 | 「可回放」没匹配 | 关键字要全 |
| 20 | 直播开新标签页 | 主页面进度不动 | 检测 `zhibo.chaoxing.com` |
| 21 | 直播进度 5 分钟一更 | 轮询看不出变化 | 接受，只等最终值 |
| 22 | 直播只认前台 | 一直停在 10.75% | **建议手机 App 看** |
| 23 | 阅读时长次日更新 | 当天看不出效果 | 等第二天验证 |

### 8.2 时间成本

| 阶段 | 耗时 | 完成度 |
|---|---|---|
| 调试基本流程 | 约 6 小时 | 0 节 |
| 第一门课（133 节） | 约 12 小时 | 133/133 |
| 第二门课（20 节） | 约 3 小时 | 20/20 |
| 第三门课（31 节） | 约 6 小时 | 25/31 |

### 8.3 最核心的三条

1. **只调 `playbackRate`，绝不碰 `currentTime`** —— 这是唯一安全的加速方式
2. **绝不重播（`removeAttribute('src')` + `load()`）** —— 会被风控踢出去
3. **测验必须一次拿够分** —— 低分提交没有重做机会，任务点永久卡住

---

## License

本文档随项目一同以 MIT 协议发布。
