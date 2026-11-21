#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键配置向导：扫码登录 + 自动抓课程参数 + 写回 config.json
=========================================================
流程：
  1. 打开学习通登录页
  2. 你扫码登录（记得手机上点「确认登录」）
  3. 自动跳课程列表
  4. 你在列表里点进某门课任意一章
  5. 脚本从地址栏抓 courseId / clazzid / chapterId / enc，写回 config.json

用法: python setup.py
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
PROFILE_DIR = BASE / "profile"

STRONG_AUTH = {"_uid", "UID", "uf", "_d"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def launch(pw):
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled",
              "--no-first-run", "--no-default-browser-check", "--lang=zh-CN"],
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
    """)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def has_auth(ctx):
    return {c["name"] for c in ctx.cookies()} & STRONG_AUTH


def extract_params(url):
    """从 URL 抽参数，兼容多种写法。"""
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    flat = {k.lower(): v[0] for k, v in qs.items() if v}
    out = {}
    mapping = {
        "courseId": ("courseid", "courseid2"),
        "clazzid": ("clazzid", "classid"),
        "chapterId": ("chapterid", "knowledgeid"),
    }
    for canonical, aliases in mapping.items():
        for a in aliases:
            if a in flat and re.fullmatch(r"\d+", flat[a] or ""):
                out[canonical] = flat[a]
                break
    # enc 是签名参数，超星新版必需
    if "enc" in flat:
        out["enc"] = flat["enc"]
    return out


def main():
    if not CFG_PATH.exists():
        log(f"找不到 {CFG_PATH}")
        sys.exit(2)

    with sync_playwright() as pw:
        ctx, page = launch(pw)

        log("打开学习通登录页...")
        page.goto("https://passport2.chaoxing.com/login",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        if has_auth(ctx):
            log(f"检测到已有登录态: {sorted(has_auth(ctx))}")
        else:
            log("=" * 58)
            log("请扫码登录：")
            log("  ① 手机学习通 App → 扫一扫")
            log("  ② 扫屏幕右侧二维码")
            log("  ③ 【必须】手机上点「确认登录」")
            log("=" * 58)

            deadline = time.time() + 420
            while time.time() < deadline:
                page.wait_for_timeout(2000)
                got = has_auth(ctx)
                if got:
                    log(f"登录成功 ✓ {sorted(got)}")
                    break
                left = int(deadline - time.time())
                if left % 30 < 3:
                    log(f"   等待中... 剩余 {left}s")
            else:
                log("超时。重跑本脚本")
                ctx.close()
                return

        for target in ("https://mooc1.chaoxing.com/visit/courses",
                       "https://i.chaoxing.com/base"):
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                if "passport" not in page.url.lower():
                    break
            except Exception:
                continue

        log("=" * 58)
        log("现在请在浏览器里点进你要刷的那门课，再点开任意一章")
        log("（点进播放页即可，脚本会自动抓参数）")
        log("最多等 8 分钟")
        log("=" * 58)

        found = {}
        deadline = time.time() + 480
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            for pg in ctx.pages:
                try:
                    u = pg.url
                except Exception:
                    continue
                if not u or u == "about:blank":
                    continue
                p = extract_params(u)
                if "courseId" in p and "chapterId" in p:
                    found = p
                    found["_url"] = u
                    log(f"抓到参数: {p}")
                    break
            if found:
                break

        if not found:
            log("没抓到完整参数。手动复制地址栏 URL 填进 config.json 也行")
            ctx.close()
            return

        if "clazzid" not in found:
            try:
                html = page.content()
                m = re.search(r"clazzid['\"]?\s*[:=]\s*['\"]?(\d+)", html, re.I)
                if m:
                    found["clazzid"] = m.group(1)
                    log(f"从页面补到 clazzid={m.group(1)}")
            except Exception:
                pass

        if "clazzid" not in found:
            log("还缺 clazzid，请把这条 URL 贴出来: " + found["_url"])
            ctx.close()
            return

        url = found["_url"]
        has_enc = "enc=" in url
        # 原样保留 URL（enc 签名不能丢），只做最小规范化
        standard = re.sub(r"https://mooc1-\d+\.chaoxing\.com",
                          "https://mooc1.chaoxing.com", url).split("#")[0]

        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        cfg["course_url"] = standard
        cfg["_raw_url"] = url
        CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

        log("=" * 58)
        log("✓ 已写回 config.json")
        log(f"  courseId  = {found['courseId']}")
        log(f"  clazzid   = {found['clazzid']}")
        log(f"  chapterId = {found['chapterId']}")
        log(f"  enc       = {'有（已保留）' if has_enc else '无'}")
        log("下一步：python run.py")
        log("=" * 58)

        page.wait_for_timeout(4000)
        ctx.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("已中断")
