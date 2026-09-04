#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""妇幼系统(fyweb)防掉线：每3分钟用 CDP 真实鼠标移动激活页面（isTrusted=true），
防止 fyweb 会话因长时间无操作过期（登录失效后脚本查不了初检）。

由 button_service 以 daemon 模式启动/停止，日志写入 button_service.log。
页面停在登录页时不做任何操作，只提示一次"登录已失效"。
"""

from playwright.sync_api import sync_playwright
import time
import traceback

FYWEB_MARK = "10.130.20.249"   # 妇幼保健管理信息系统（不限端口）
INTERVAL = 180                  # 激活间隔（秒）


def log(*args):
    print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)


def find_fyweb(browser):
    for ctx in browser.contexts:
        for pg in ctx.pages:
            try:
                if FYWEB_MARK in pg.url:
                    return pg
            except Exception:
                pass
    return None


def tick(browser):
    """返回 (状态, 消息)；状态: ok / login / error。"""
    page = find_fyweb(browser)
    if not page:
        return "error", "没找到 fyweb 页面（10.130.20.249），请先打开并登录"
    if "/login" in page.url:
        return "login", "fyweb 登录已失效（停在登录页），请手动登录"
    size = page.viewport_size or {"width": 1280, "height": 800}
    x, y = size["width"] // 2, size["height"] // 2
    page.mouse.move(x, y)
    time.sleep(0.2)
    page.mouse.move(x + 2, y + 2)
    time.sleep(0.2)
    page.mouse.move(x, y)
    return "ok", "已模拟鼠标移动防掉线"


def run_loop():
    browser = None
    last_msg = None
    with sync_playwright() as p:
        while True:
            try:
                if browser is None or not browser.is_connected():
                    browser = p.chromium.connect_over_cdp("http://localhost:9222")
                status, msg = tick(browser)
            except Exception:
                err = traceback.format_exc().splitlines()[-1]
                if "Connection closed" in err or "Target crashed" in err:
                    raise  # 驱动级故障，抛给外层重建 playwright
                browser = None
                status, msg = "error", "连接异常：" + err
            # 防掉线成功不记日志（刷屏），状态变化/异常才记
            if status != "ok" and msg != last_msg:
                log(msg)
            last_msg = msg
            time.sleep(INTERVAL)


def main():
    log(f"妇幼系统防掉线已启动，每 {INTERVAL // 60} 分钟激活一次")
    while True:  # 驱动崩溃时整体重建（HIS 原生弹窗曾把驱动搞崩过）
        try:
            run_loop()
        except Exception:
            log("Playwright 驱动崩溃，5 秒后自动重建：" + traceback.format_exc().splitlines()[-1])
            time.sleep(5)


if __name__ == "__main__":
    main()
