#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
chaoxing-video — 学习通视频刷课引擎
===================================
核心设计：宁慢勿死。所有动作向真人特征靠拢，任何不确定信号一律停机。
全程只调 playbackRate，绝不篡改 currentTime。

用法:
    python run.py              # 正常刷课
    python run.py --login      # 只做登录，保存登录态
    python run.py --scan       # 扫描全课程任务点状态
    python run.py --dry        # 只校验配置
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
PROFILE_DIR = BASE / "profile"
STATE_PATH = BASE / "state.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

STRONG_AUTH = {"_uid", "UID", "uf", "_d"}  # fid 只是学校 ID，不是登录凭证

START_TS = datetime.now()
LOG_FILE = LOG_DIR / f"run_{START_TS.strftime('%Y%m%d_%H%M%S')}.log"


# ---------- 日志 ----------

def log(msg, level="INFO"):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {level:<5} {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def rnd(rng):
    return random.uniform(float(rng[0]), float(rng[1]))


# ---------- 配置 ----------

def load_cfg():
    if not CFG_PATH.exists():
        log(f"配置文件不存在: {CFG_PATH}", "FATAL")
        sys.exit(2)

    raw = CFG_PATH.read_text(encoding="utf-8")
    cfg = json.loads(raw)

    url = cfg.get("course_url", "")
    if "〔" in url or "〕" in url:
        log("course_url 里还有占位符，先填真实值（或跑 python setup.py）", "FATAL")
        sys.exit(2)

    for key in ("courseId", "clazzid", "chapterId"):
        if not re.search(rf"{key}=\d+", url, re.I):
            log(f"course_url 缺少参数 {key}", "FATAL")
            sys.exit(2)

    # 超星合法上限就是 2.0，超过强行压回
    rate = float(cfg.get("playback_rate", 2.0))
    if rate > 2.0:
        log(f"倍速 {rate} 超出安全档，压回 2.0", "WARN")
        cfg["playback_rate"] = 2.0

    return cfg


# ---------- 浏览器 ----------

def launch(pw, cfg):
    log("启动浏览器（持久化配置目录）")

    # 反后台降频的关键开关
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--no-first-run",
        "--lang=zh-CN",
        # 反后台节流：Chromium 默认把后台 tab 定时器降到 1 次/分钟
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
        # 反媒体自动暂停
        "--disable-background-media-suspend",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-ipc-flooding-protection",
    ]

    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=bool(cfg.get("headless", False)),
        slow_mo=int(cfg.get("slow_mo_ms", 0)),
        viewport={"width": 1366, "height": 768},
        args=args,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )

    # 注入登录态。
    # 注意：launch_persistent_context 不接受 storage_state，必须手动 add_cookies
    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            clean = []
            for c in st.get("cookies", []):
                ck = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ".chaoxing.com"),
                    "path": c.get("path", "/"),
                }
                ss = c.get("sameSite")
                if ss in ("Strict", "Lax", "None"):
                    ck["sameSite"] = ss
                if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
                    ck["expires"] = float(c["expires"])
                if c.get("httpOnly") is not None:
                    ck["httpOnly"] = bool(c["httpOnly"])
                if c.get("secure") is not None:
                    ck["secure"] = bool(c["secure"])
                clean.append(ck)
            if clean:
                ctx.add_cookies(clean)
                auth = st.get("_auth_keys") or []
                log(f"已注入 {len(clean)} 个 cookie（认证: {auth or '无'}）")
        except Exception as e:
            log(f"注入 cookie 失败: {e}", "WARN")
    else:
        log("未找到 state.json，先跑 python run.py --login", "WARN")

    # 注入保活脚本
    ctx.add_init_script(KEEPALIVE_JS)

    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    if cfg.get("minimize_on_start"):
        try:
            time.sleep(1.5)
            cdp = ctx.new_cdp_session(page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds",
                     {"windowId": wid, "bounds": {"windowState": "minimized"}})
            log("窗口已最小化")
        except Exception as e:
            log(f"最小化失败（不影响运行）: {e}", "WARN")

    return ctx, page


# 页面内保活脚本
KEEPALIVE_JS = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = {runtime: {}};
window.__cxKeepAlive = true;

// 1) 可见性永远报 visible
try {
    Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
    Object.defineProperty(document, 'visibilityState',
        {get: () => 'visible', configurable: true});
    Object.defineProperty(document, 'webkitHidden', {get: () => false, configurable: true});
    Object.defineProperty(document, 'webkitVisibilityState',
        {get: () => 'visible', configurable: true});
} catch (e) {}

// 2) 吞掉 visibilitychange / blur / pagehide
['visibilitychange','webkitvisibilitychange','blur','pagehide','freeze','mozvisibilitychange']
  .forEach(t => {
    window.addEventListener(t, e => {
        e.stopImmediatePropagation(); e.stopPropagation();
    }, true);
    document.addEventListener(t, e => {
        e.stopImmediatePropagation(); e.stopPropagation();
    }, true);
  });

// 3) hasFocus 永远 true
try { document.hasFocus = () => true; } catch (e) {}

// 4) 无条件锁速 + 被暂停就自己播
window.__cxRate = 2.0;
window.__cxMute = true;
window.__cxLockRate = true;
window.__cxSelfHeal = setInterval(() => {
    if (!window.__cxKeepAlive) return;
    const v = document.querySelector('video');
    if (!v) return;
    try {
        if (window.__cxLockRate &&
            Math.abs(v.playbackRate - window.__cxRate) > 0.01) {
            v.playbackRate = window.__cxRate;
        }
        if (v.paused && !v.ended && v.readyState >= 2) {
            v.play().catch(() => {});
        }
        if (window.__cxMute && v.muted !== true) { v.muted = true; }
    } catch (e) {}
}, 800);

// 5) 拦 playbackRate setter，播放器想改回 1.0 直接拦
try {
    const proto = HTMLMediaElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, 'playbackRate');
    if (d && d.set) {
        Object.defineProperty(proto, 'playbackRate', {
            configurable: true,
            get: d.get,
            set: function (val) {
                if (window.__cxLockRate && window.__cxRate &&
                    Math.abs(val - window.__cxRate) > 0.01) {
                    return d.set.call(this, window.__cxRate);
                }
                return d.set.call(this, val);
            }
        });
    }
} catch (e) {}

// 6) 拦 pause()，只允许 ended 自然停
try {
    const proto = HTMLMediaElement.prototype;
    const origPause = proto.pause;
    proto.pause = function () {
        if (window.__cxKeepAlive && !this.ended && this.readyState > 2) return;
        return origPause.apply(this, arguments);
    };
} catch (e) {}

// 7) play() 被拒（自动播放策略）时静音重试
try {
    const proto = HTMLMediaElement.prototype;
    const origPlay = proto.play;
    proto.play = function () {
        const p = origPlay.apply(this, arguments);
        if (p && p.catch) {
            p.catch(() => {
                this.muted = true;
                setTimeout(() => { origPlay.call(this).catch(() => {}); }, 300);
            });
        }
        return p;
    };
} catch (e) {}
"""


# ---------- 登录 ----------

def is_logged_in(page):
    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if st.get("_auth_keys"):
                live = {c["name"] for c in page.context.cookies()}
                if live & set(st["_auth_keys"]):
                    return True
        except Exception:
            pass

    names = {c["name"] for c in page.context.cookies()}
    if names & STRONG_AUTH:
        return True

    try:
        page.goto("https://i.chaoxing.com/base", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
        return not ("passport" in page.url.lower() or "login" in page.url.lower())
    except PWTimeout:
        return False


def do_login(ctx, page):
    """扫码登录并保存 state.json。"""
    names = {c["name"] for c in ctx.cookies()}
    if names & STRONG_AUTH:
        log(f"已有登录态: {sorted(names & STRONG_AUTH)}")
    else:
        page.goto("https://passport2.chaoxing.com/login",
                  wait_until="domcontentloaded", timeout=60000)
        log("=" * 56)
        log("请扫码登录：")
        log("  ① 手机学习通 App → 扫一扫")
        log("  ② 扫屏幕右侧二维码")
        log("  ③ 【必须】手机上点「确认登录」")
        log("=" * 56)

        deadline = time.time() + 480
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            got = {c["name"] for c in ctx.cookies()} & STRONG_AUTH
            if got:
                page.wait_for_timeout(2500)
                if is_logged_in(page):
                    log(f"登录成功 ✓ cookie: {sorted(got)}")
                    break
            left = int(deadline - time.time())
            if left % 30 < 3:
                log(f"   等待中... 剩余 {left}s")
        else:
            log("超时。重跑即可", "FATAL")
            return False

    # 访问一次需要鉴权的页面，让服务端下发完整 cookie
    for url in ("https://i.chaoxing.com/base",
                "https://mooc1.chaoxing.com/visit/courses"):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
        except Exception:
            pass

    ctx.storage_state(path=str(STATE_PATH))

    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    data["_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["_cookie_names"] = sorted({c["name"] for c in data.get("cookies", [])})
    data["_auth_keys"] = sorted({c["name"] for c in data.get("cookies", [])
                                 if c["name"] in STRONG_AUTH})
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"✓ 登录态已保存: {len(data.get('cookies', []))} 个 cookie，"
        f"认证 {data.get('_auth_keys')}")
    return bool(data.get("_auth_keys"))


# ---------- 视频 ----------

def find_video_frame(page, timeout_sec=30):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for f in page.frames:
            try:
                if f.locator("video").count() > 0:
                    return f
            except Exception:
                continue
        page.wait_for_timeout(1000)
    return None


def read_video_state(frame):
    try:
        return frame.evaluate("""() => {
            const v = document.querySelector('video');
            if (!v) return {};
            return {
                ok: true,
                paused: v.paused,
                ended: v.ended,
                current: v.currentTime,
                duration: v.duration,
                rate: v.playbackRate,
                readyState: v.readyState,
                src: (v.currentSrc || v.src || '').slice(0, 120),
            };
        }""")
    except Exception:
        return {}


def force_play(frame, rate, muted):
    try:
        frame.evaluate("""({rate, muted}) => {
            window.__cxRate = rate;
            window.__cxMute = muted;
            const v = document.querySelector('video');
            if (!v) return false;
            v.muted = muted;
            if (Math.abs(v.playbackRate - rate) > 0.01) v.playbackRate = rate;
            if (v.paused) v.play().catch(() => {});
            return true;
        }""", {"rate": rate, "muted": muted})
        return True
    except Exception as e:
        log(f"force_play 失败: {e}", "WARN")
        return False


def restart_video_from_zero(frame, page):
    """重新加载视频源从 0 播。

    不拖进度条（超星会记录 drag 并判异常），重新 load() 等价于刷新章节。
    """
    try:
        r = frame.evaluate("""() => {
            const v = document.querySelector('video');
            if (!v) return {ok:false, why:'no-video'};
            try {
                const src = v.currentSrc || v.src;
                v.pause();
                v.removeAttribute('src');
                v.load();
                v.src = src;
                v.load();
                v.muted = true;
                v.playbackRate = window.__cxRate || 2.0;
                const p = v.play();
                if (p && p.catch) p.catch(()=>{});
                return {ok:true};
            } catch (e) {
                return {ok:false, why:String(e).slice(0,100)};
            }
        }""")
        if r.get("ok"):
            log("已重新加载视频源，从 0 播放")
            page.wait_for_timeout(4000)
            st = read_video_state(frame)
            log(f"重载后 cur={st.get('current',0):.1f}s dur={st.get('duration')}")
            return True
    except Exception as e:
        log(f"restart 异常: {e}", "WARN")
    return False


# ---------- 页面健康 ----------

def check_page_health(page):
    """只检查真实可见的元素。

    坑：超星页面内嵌大量隐藏的验证码/对话框，
    直接 page.content() 全文匹配必然误报。
    父级 display:none 时子元素自身 display 仍是 block，
    所以必须用 getBoundingClientRect + checkVisibility。
    """
    hard_stop = ("验证码", "人脸识别", "请重新登录", "enc校验失败", "校验失败")
    anomaly = ("异常", "作废", "任务点已过期")

    try:
        visible = page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('*')) {
                const tag = el.tagName;
                if (['SCRIPT','STYLE','NOSCRIPT','HEAD','TITLE','META','LINK'].includes(tag)) continue;
                if (el.children.length > 6) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                if (el.checkVisibility) {
                    try {
                        if (!el.checkVisibility({
                            checkOpacity: true, checkVisibilityCSS: true,
                            contentVisibilityAuto: true,
                        })) continue;
                    } catch (e) {}
                }
                if (r.bottom < 0 || r.top > window.innerHeight ||
                    r.right < 0 || r.left > window.innerWidth) continue;
                const t = (el.innerText || '').trim();
                if (t && t.length < 80) out.push(t);
                if (out.length > 3000) break;
            }
            return out;
        }""")
    except Exception:
        return "page_unreadable"

    joined = " | ".join(visible)
    for token in hard_stop + anomaly:
        if token in joined:
            return token

    # 模态弹层单独查
    try:
        found = page.evaluate("""() => {
            const sels = '.layui-layer, .LayerTopic, .examLayer, .mask, .dialog, .popup';
            for (const el of document.querySelectorAll(sels)) {
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                if (r.width < 100 || r.height < 60) continue;
                if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                if (parseFloat(cs.opacity || '1') < 0.1) continue;
                const t = (el.innerText || '').trim();
                for (const kw of ['验证码','人脸识别','异常','作废','请重新登录']) {
                    if (t.includes(kw)) return kw;
                }
            }
            return '';
        }""")
        if found:
            return found
    except Exception:
        pass

    return "ok"


def has_quiz(frame, page=None):
    """检测是否真的弹出了答题界面。

    坑：超星顶部导航本来就有「章节测验」标签按钮，
    那是常驻栏目入口，绝不能当弹题证据。
    """
    quiz_selectors = [".TiMu", ".questionLi", ".mark_item", ".ans-ccs",
                      ".topic-item", "#questionDiv", ".LayerTopic", ".examLayer"]

    if frame is not None:
        try:
            for sel in quiz_selectors:
                loc = frame.locator(sel)
                if loc.count() == 0:
                    continue
                try:
                    if loc.first.is_visible():
                        return True
                except Exception:
                    continue
        except Exception:
            pass

    if page is not None:
        try:
            found = page.evaluate("""() => {
                const bad = ['章节测验','课堂练习','请回答','本题','单选题','多选题',
                             '判断题','填空题','提交答案','开始答题'];
                const sels = '.layui-layer, .LayerTopic, .examLayer, .mask, .dialog, .ant-modal';
                for (const l of document.querySelectorAll(sels)) {
                    const r = l.getBoundingClientRect();
                    if (r.width < 100 || r.height < 60) continue;
                    const cs = getComputedStyle(l);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    if (parseFloat(cs.opacity || '1') < 0.1) continue;
                    const z = parseInt(cs.zIndex || '0', 10);
                    if (z < 100) continue;
                    const t = (l.innerText || '').trim();
                    if (!t) continue;
                    for (const kw of bad) if (t.includes(kw)) return kw;
                }
                return '';
            }""")
            if found:
                return True
        except Exception:
            pass

    return False


# ---------- 目录 / 任务点 ----------

def get_job_count(page, chapter_no=None):
    """读目录里每节的 jobCount。

    DOM: <span class="roundpointStudent orange01 a002 jobCount">2</span>
    2 = 视频 + 测验都没做；1 = 只剩测验；0/绿勾 = 全完成
    """
    try:
        data = page.evaluate("""() => {
            const root = document.querySelector('.right') || document;
            const out = {};
            root.querySelectorAll('a').forEach(a => {
                const t = (a.innerText || '').trim();
                const m = t.match(/^(\\d+[.\\-]\\d+)\\s*/);
                if (!m) return;
                const no = m[1];

                let box = a;
                for (let i = 0; i < 4 && box; i++, box = box.parentElement) {
                    const badge = box.querySelector('.jobCount, .roundpointStudent');
                    if (badge) {
                        const txt = (badge.innerText || '').trim();
                        const isDone = /icon_ok|finish|complete|\\bok\\b/i.test(
                            (badge.className || '') + ' ' + (badge.innerHTML || ''));
                        out[no] = {
                            count: /^\\d+$/.test(txt) ? parseInt(txt, 10) : (isDone ? 0 : 0),
                            raw: txt,
                            done: isDone,
                        };
                        return;
                    }
                }
                out[no] = {count: 0, raw: '', done: true};
            });
            return out;
        }""")

        if chapter_no:
            return {chapter_no: data.get(chapter_no, {"count": -1, "raw": "?", "done": False})}
        return data
    except Exception as e:
        log(f"读取 jobCount 失败: {e}", "WARN")
        return {}


def current_chapter_no(page):
    try:
        return page.evaluate("""() => {
            const root = document.querySelector('.right') || document;
            const items = [];
            root.querySelectorAll('a').forEach(a => {
                const t = (a.innerText || '').trim();
                const m = t.match(/^(\\d+[.\\-]\\d+)\\s*/);
                if (!m) return;
                const rc = a.getBoundingClientRect();
                if (rc.width <= 0 || rc.height <= 0) return;
                items.push({a, no: m[1]});
            });
            for (const it of items) {
                let p = it.a;
                for (let k = 0; k < 5 && p; k++, p = p.parentElement) {
                    const cls = (p.className || '').toString();
                    const bg = getComputedStyle(p).backgroundColor || '';
                    if (/\\bcur|active|current|selected\\b/i.test(cls)) return it.no;
                    if (/rgb\\(\\s*(1[0-9]{2}|2[0-9]{2}),\\s*(1[5-9][0-9]|2[0-9]{2})/.test(bg)) {
                        return it.no;
                    }
                }
            }
            return '';
        }""")
    except Exception:
        return ""


def job_count_cleared(page, chapter_no, baseline=None, timeout_sec=90):
    """确认视频任务点完成。

    baseline 必须由调用方在开刷前读取 —— 刷完再读就永远是同一个值。
    判定：count 比 baseline 小，或 done，或 count == 0。
    """
    log(f"校验任务点: {chapter_no} baseline={baseline}")
    deadline = time.time() + timeout_sec
    prev = None

    while time.time() < deadline:
        jc = get_job_count(page, chapter_no).get(chapter_no, {})
        cnt = jc.get("count", -1)
        done = jc.get("done", False)

        if prev is not None and cnt != prev:
            log(f"  jobCount 变化: {prev} -> {cnt}")
        prev = cnt

        if done or cnt == 0:
            log(f"  ✓ {chapter_no} 全部完成（count={cnt}）")
            return True
        if baseline is not None and baseline > 0 and cnt >= 0 and cnt < baseline:
            log(f"  ✓ {chapter_no} 视频点已完成（{baseline} -> {cnt}）")
            return True
        if baseline == 1 and cnt == 1:
            log(f"  ✓ {chapter_no} 开刷前视频点已清（baseline=1）")
            return True

        log(f"  jobCount={cnt}（基线 {baseline}），等待刷新...", "WARN")
        try:
            page.evaluate("""() => {
                try { if (typeof getTeacherNodeList === 'function') getTeacherNodeList(); } catch(e){}
                try { if (typeof refreshChapter === 'function') refreshChapter(); } catch(e){}
            }""")
        except Exception:
            pass
        page.wait_for_timeout(6000)

    log(f"  ✗ {chapter_no} 未变化（最后 {prev}，基线 {baseline}）", "WARN")
    return False


# ---------- 章节跳转 ----------

def goto_next_chapter(page):
    """进下一节。

    实测：底部「下一节」按钮在 [520,902]，常落在视口外；
    PCount.next() 用 onclick 里的旧参数，调了不跳；
    最可靠是点右侧目录里「当前节之后、未完成」的那一项。
    """
    try:
        cur_id = ""
        m = re.search(r"[?&]chapterId=(\d+)", page.url)
        if m:
            cur_id = m.group(1)

        r = page.evaluate("""(curId) => {
            const root = document.querySelector('.right') || document;
            const items = [];
            root.querySelectorAll('a').forEach(a => {
                const t = (a.innerText || '').trim();
                if (!/^\\d+[.\\-]\\d+/.test(t)) return;
                const rect = a.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return;

                let ids = [];
                const harvest = (s) => {
                    if (!s) return;
                    const mm = s.match(/(\\d{7,12})/g);
                    if (mm) ids.push(...mm);
                };
                harvest(a.getAttribute('href'));
                harvest(a.getAttribute('onclick'));
                let p = a;
                for (let k = 0; k < 4 && p; k++, p = p.parentElement) {
                    harvest(p.getAttribute('onclick'));
                    harvest(p.id);
                    for (const at of (p.attributes || [])) harvest(at.value);
                }
                items.push({a, t, ids: [...new Set(ids)]});
            });
            if (!items.length) return {ok:false, why:'no-items'};

            const isDone = (a) => {
                let p = a;
                for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
                    const cls = (p.className || '').toString();
                    if (/finish|complete|icon_ok|\\bdone\\b/i.test(cls)) return true;
                    for (const ic of p.querySelectorAll('img, i, em')) {
                        const s = (ic.className || '') + (ic.getAttribute('src') || '');
                        if (/ok|finish|complete|done|yes|blue/i.test(s)) return true;
                    }
                }
                return false;
            };

            let curIdx = -1;
            if (curId) {
                for (let i = 0; i < items.length; i++) {
                    if (items[i].ids.includes(String(curId))) { curIdx = i; break; }
                }
            }
            if (curIdx < 0) {
                for (let i = 0; i < items.length; i++) {
                    let p = items[i].a;
                    for (let k = 0; k < 5 && p; k++, p = p.parentElement) {
                        if (/^(cur\\d+|.*\\bcur\\b.*)/.test((p.id || '') + ' ' + (p.className || ''))) {
                            curIdx = i; break;
                        }
                    }
                    if (curIdx >= 0) break;
                }
            }

            const start = curIdx >= 0 ? curIdx + 1 : 0;
            let target = -1;
            for (let i = start; i < items.length; i++) {
                if (!isDone(items[i].a)) { target = i; break; }
            }
            if (target < 0 && curIdx >= 0 && curIdx + 1 < items.length) target = curIdx + 1;
            if (target < 0) return {ok:false, why:'no-next', total: items.length};

            items[target].a.scrollIntoView({block:'center'});
            return {ok:true, idx: target, text: items[target].t.slice(0,45),
                    curIdx, total: items.length};
        }""", cur_id)

        if r.get("ok"):
            log(f"目标章节: {r['text']} (索引{r['idx']}/{r['total']})")
            try:
                loc = page.locator(".right a").filter(has_text=re.compile(r"^\d+[.\-]\d+"))
                tl = loc.nth(r["idx"])
                if tl.count() > 0:
                    tl.scroll_into_view_if_needed(timeout=4000)
                    page.wait_for_timeout(400)
                    tl.click(timeout=6000)
                else:
                    raise Exception("not found")
            except Exception:
                page.evaluate("""(idx) => {
                    const root = document.querySelector('.right') || document;
                    const items = [];
                    root.querySelectorAll('a').forEach(a => {
                        const t = (a.innerText || '').trim();
                        if (!/^\\d+[.\\-]\\d+/.test(t)) return;
                        const rect = a.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) return;
                        items.push(a);
                    });
                    if (idx < items.length) items[idx].click();
                }""", r["idx"])

            page.wait_for_timeout(int(rnd([3, 5]) * 1000))
            return True
        else:
            log(f"目录定位失败: {r.get('why')}", "WARN")
    except Exception as e:
        log(f"跳转异常: {e}", "WARN")

    # 兜底：底部按钮
    for sel in ["#right2", ".orientationright", "#prevNextFocusNext"]:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            page.evaluate("""(s) => {
                const el = document.querySelector(s);
                if (el) el.scrollIntoView({block: 'end'});
            }""", sel)
            page.wait_for_timeout(600)
            if not loc.is_visible():
                continue
            loc.click(timeout=5000, force=True)
            page.wait_for_timeout(int(rnd([2, 4]) * 1000))
            return True
        except Exception:
            continue

    return False


# ---------- 单节学习 ----------

def learn_one(page, cfg):
    rate = float(cfg["playback_rate"])
    muted = bool(cfg.get("muted", True))

    frame = find_video_frame(page)
    if frame is None:
        return "no_video"

    st = read_video_state(frame)
    if not st.get("ok"):
        return "no_video"

    # readyState=0：播放器首次挂载时 load() 没触发，手动重设 src 救回来
    if st.get("readyState", 0) == 0:
        log("readyState=0，播放器未初始化，强制重载", "WARN")
        try:
            fixed = frame.evaluate("""async () => {
                const v = document.querySelector('video');
                if (!v) return {ok:false};
                const src = v.currentSrc || v.src || '';
                if (!src) return {ok:false, why:'no-src'};
                v.src = src; v.muted = true; v.load();
                for (let i = 0; i < 20; i++) {
                    await new Promise(r => setTimeout(r, 500));
                    if (v.readyState >= 1) break;
                }
                return {ok: v.readyState >= 1, rs: v.readyState, dur: v.duration};
            }""")
            log(f"重载结果: {fixed}")
            if fixed.get("ok"):
                page.wait_for_timeout(1000)
                st = read_video_state(frame)
        except Exception as e:
            log(f"强制重载异常: {e}", "WARN")

    duration = st.get("duration") or 0
    if not duration or duration != duration:
        log("duration 未就绪，先拉起播放...")
        force_play(frame, rate, muted)
        for i in range(20):
            page.wait_for_timeout(1000)
            st2 = read_video_state(frame)
            if st2.get("ok"):
                d2 = st2.get("duration") or 0
                if d2 and d2 == d2 and d2 > 0:
                    st, duration = st2, d2
                    log(f"元数据就绪（{i+1}s）")
                    break
                if st2.get("current", 0) > st.get("current", 0) + 0.5:
                    st = st2
                    log(f"进度已在推进（{i+1}s）")
                    break
        if not duration or duration != duration:
            duration = 0
            log("duration 不可用，改用进度推进 + ended 判定", "WARN")

    log(f"视频就绪 duration={duration:.1f}s 起点={st.get('current',0):.1f}s")

    # 起点靠后 -> 从 0 重播。
    # 超星要求观看时长 ≥ 总时长 90%，从中间续播实际观看时长不够
    if cfg.get("force_from_start", True):
        cur_start = st.get("current", 0.0) or 0.0
        threshold = float(cfg.get("restart_threshold_sec", 10))
        need = cur_start > threshold
        if duration and duration > 0 and (duration - cur_start) <= 30:
            need = False
        if need:
            log(f"起点 {cur_start:.1f}s 靠后，重新从 0 播放", "WARN")
            restart_video_from_zero(frame, page)
            st = read_video_state(frame)
            d2 = st.get("duration") or 0
            if d2 and d2 == d2 and d2 > 0:
                duration = d2

    if st.get("paused"):
        log("初始暂停，主动播放")
    force_play(frame, rate, muted)

    last_current = st.get("current", 0.0)
    last_progress_ts = time.time()
    stall_limit = 90

    while True:
        page.wait_for_timeout(int(rnd(cfg["keepalive_interval_sec"]) * 1000))

        health = check_page_health(page)
        if health != "ok":
            return f"anomaly:{health}"

        st = read_video_state(frame)
        if not st.get("ok"):
            return "frame_lost"

        cur = st.get("current", 0.0)
        dur = st.get("duration", 0.0)
        ended = st.get("ended", False)
        if dur and dur > 0:
            duration = dur

        if st.get("paused") and not ended:
            log("播放器被暂停，恢复", "WARN")
            force_play(frame, rate, muted)

        if abs(st.get("rate", rate) - rate) > 0.01:
            log(f"倍速被改为 {st.get('rate')}，回设 {rate}", "WARN")
            force_play(frame, rate, muted)

        if cfg["safety"].get("stop_on_quiz", True) and has_quiz(frame, page):
            return "quiz"

        if ended:
            return "ended"

        try:
            done_flag = frame.evaluate("""() => {
                if (window.__cxDone) return true;
                const v = document.querySelector('video');
                if (v && v.duration && v.currentTime >= v.duration - 3) return true;
                return false;
            }""")
            if done_flag:
                log("播放器标记已完成")
                return "ended"
        except Exception:
            pass

        if duration and duration > 0 and duration == duration:
            ratio = cur / duration
            if ratio >= cfg["safety"].get("min_video_progress_ratio", 0.995):
                log(f"进度 {ratio*100:.1f}% 达标")
                return "ended"

        if cur > last_current + 0.3:
            last_current = cur
            last_progress_ts = time.time()
        elif time.time() - last_progress_ts > stall_limit:
            log(f"进度卡在 {cur:.1f}s 超过 {stall_limit}s，尝试恢复", "WARN")
            force_play(frame, rate, muted)
            page.wait_for_timeout(8000)
            st2 = read_video_state(frame)
            if st2.get("ok") and st2.get("current", 0) > cur + 0.3:
                log("已恢复", "INFO")
                last_progress_ts = time.time()
            else:
                try:
                    frame.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) { v.muted = true; v.playbackRate = window.__cxRate || 2.0;
                                 v.play().catch(()=>{}); }
                        const btn = document.querySelector(
                            '.vjs-big-play-button, .pl_play, button[title*="播放"]');
                        if (btn) btn.click();
                    }""")
                    log("二次自愈：点击播放按钮", "WARN")
                    page.wait_for_timeout(10000)
                    st3 = read_video_state(frame)
                    if st3.get("ok") and st3.get("current", 0) > cur + 0.3:
                        log("二次自愈成功")
                        last_progress_ts = time.time()
                        continue
                except Exception:
                    pass
                return "stall"

        # 活动噪声：合成事件，窗口不在前台也生效
        if random.random() < 0.25:
            try:
                frame.evaluate("""() => {
                    const mk = (t, x, y) => {
                        const e = new MouseEvent(t, {bubbles:true, cancelable:true,
                                                     view:window, clientX:x, clientY:y});
                        (document.body || document.documentElement).dispatchEvent(e);
                    };
                    mk('mousemove', 200 + Math.random()*800, 200 + Math.random()*400);
                }""")
            except Exception:
                pass


# ---------- 主流程 ----------

def scan_all(page):
    """扫描并打印全课程任务点状态。"""
    data = get_job_count(page)

    def key(k):
        a, b = k.split(".")
        return (int(a), int(b))

    todo = [(k, v["count"]) for k, v in sorted(data.items(), key=lambda x: key(x[0]))
            if v["count"] > 1]
    done = [k for k, v in data.items() if v["count"] <= 1]

    log("=" * 56)
    log(f"章节总数: {len(data)}  已完成: {len(done)}  待刷: {len(todo)}")
    log("=" * 56)

    if todo:
        log("待刷章节:")
        for k, c in todo:
            log(f"  {k:<8} jobCount={c}")
    else:
        log("★ 全部章节视频任务点已处理 ★")

    return todo


def run(cfg):
    total_minutes = 0.0
    daily_cap = float(cfg.get("daily_max_minutes", 480))
    max_chapters = int(cfg.get("max_chapters_per_run", 80))

    with sync_playwright() as pw:
        ctx, page = launch(pw, cfg)

        if not is_logged_in(page):
            log("未登录。请先跑 python run.py --login", "FATAL")
            ctx.close()
            return

        log("打开课程页")
        page.goto(cfg["course_url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(int(rnd([6, 10]) * 1000))

        done = 0
        skipped_quiz = 0
        retries = {}

        for idx in range(1, max_chapters + 1):
            log(f"===== 第 {idx} 节 =====")

            pre_no = current_chapter_no(page)
            pre_jc = -1
            if pre_no:
                pre_jc = get_job_count(page, pre_no).get(pre_no, {}).get("count", -1)
                log(f"[基线] {pre_no} 开刷前 jobCount = {pre_jc}")

            t0 = time.time()
            result = learn_one(page, cfg)
            log(f"本节结果: {result}")

            elapsed = (time.time() - t0) / 60.0
            total_minutes += elapsed
            log(f"本节耗时 {elapsed:.1f} 分钟，累计 {total_minutes:.1f} 分钟")

            if result.startswith("anomaly:"):
                log("检测到账号异常，立即停机", "FATAL")
                break

            if result == "quiz":
                skipped_quiz += 1
                log(f"本节有测验，跳过（累计 {skipped_quiz} 节）", "WARN")
                page.wait_for_timeout(int(rnd(cfg["chapter_gap_sec"]) * 1000))
                if not goto_next_chapter(page):
                    log("没有下一节了", "INFO")
                    break
                continue

            if result in ("no_video", "frame_lost", "stall"):
                log(f"本节无法处理（{result}），跳过", "WARN")
                if not goto_next_chapter(page):
                    log("找不到下一节，停机", "WARN")
                    break
                continue

            done += 1

            # 任务点校验
            if result == "ended":
                ch_no = current_chapter_no(page)
                if ch_no:
                    before = pre_jc if pre_no == ch_no else \
                        get_job_count(page, ch_no).get(ch_no, {}).get("count", -1)
                    cleared = job_count_cleared(
                        page, ch_no, baseline=before,
                        timeout_sec=int(cfg.get("job_verify_timeout_sec", 90)))

                    if not cleared:
                        cnt = retries.get(ch_no, 0)
                        max_retry = int(cfg.get("job_retry_max", 3))
                        if cnt < max_retry:
                            retries[ch_no] = cnt + 1
                            log(f"{ch_no} 未清零，第 {cnt+1}/{max_retry} 次重刷", "WARN")
                            try:
                                page.evaluate("""(no) => {
                                    const root = document.querySelector('.right') || document;
                                    for (const a of root.querySelectorAll('a')) {
                                        const t = (a.innerText || '').trim();
                                        if (t.startsWith(no + ' ') || t.startsWith(no + '\\u3000')) {
                                            a.scrollIntoView({block:'center'});
                                            a.click();
                                            return;
                                        }
                                    }
                                }""", ch_no)
                                page.wait_for_timeout(int(rnd([4, 7]) * 1000))
                            except Exception as e:
                                log(f"重刷定位失败: {e}", "WARN")
                            continue
                        else:
                            log(f"{ch_no} 重刷 {max_retry} 次仍未清，跳过", "WARN")

            page.wait_for_timeout(int(rnd(cfg["chapter_gap_sec"]) * 1000))
            try:
                page.evaluate("""() => {
                    const mk = (t,x,y) => {
                        const e = new MouseEvent(t,{bubbles:true,cancelable:true,
                                                     view:window,clientX:x,clientY:y});
                        (document.body||document.documentElement).dispatchEvent(e);
                    };
                    mk('mousemove', 200 + Math.random()*800, 200 + Math.random()*400);
                    window.scrollBy(0, 60 + Math.random()*200);
                }""")
            except Exception:
                pass
            page.wait_for_timeout(int(rnd(cfg["mouse_noise_sec"]) * 1000))

            if total_minutes >= daily_cap:
                log(f"已达上限 {daily_cap} 分钟，停机", "WARN")
                break

            if not goto_next_chapter(page):
                log("没有下一节了，本次完成", "INFO")
                break

        log(f"本次完成 {done} 节视频，跳过 {skipped_quiz} 节测验，"
            f"累计 {total_minutes:.1f} 分钟")
        log(f"日志: {LOG_FILE}")
        page.wait_for_timeout(3000)
        ctx.close()


def main():
    ap = argparse.ArgumentParser(description="学习通视频刷课引擎")
    ap.add_argument("--login", action="store_true", help="只做登录并保存登录态")
    ap.add_argument("--scan", action="store_true", help="扫描全课程任务点状态")
    ap.add_argument("--dry", action="store_true", help="只校验配置")
    args = ap.parse_args()

    cfg = load_cfg()

    if args.dry:
        log("dry 模式：配置校验通过")
        log(f"  URL: {cfg['course_url'][:110]}")
        log(f"  倍速: {cfg['playback_rate']}")
        return

    if args.login or args.scan:
        with sync_playwright() as pw:
            ctx, page = launch(pw, cfg)
            if args.login:
                ok = do_login(ctx, page)
                log("登录态保存完成" if ok else "登录失败")
            else:
                if not is_logged_in(page):
                    log("未登录，先跑 --login", "FATAL")
                else:
                    page.goto(cfg["course_url"], wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(10000)
                    scan_all(page)
            ctx.close()
        return

    run(cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("手动中断", "WARN")
