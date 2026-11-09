#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 桌面\\图片\\AQ行号-行号_姓名_日期.png 批量插入金山文档在线表格对应 AQ 单元格

用法: python kdocs_upload.py [起始行] [结束行]     （默认 257~505）
流程：先整段读在线表格 AP 列（含"退费/未缴"的行跳过）和 AQ 列（已有图片的行跳过），
只传漏掉的行：名称框跳转 AQ行（回读校验）→ 插入 → 图片 →
单元格图片 → 本地 → 选文件 → 轮询回读 DISPIMG 确认。失败重试一次。
跳过逻辑以在线表格实时状态为准；无本地图片的行自动跳过。
进度写入 _kdocs_upload_done.json，可中断续跑。无本地图片的行自动跳过。
"""

from playwright.sync_api import sync_playwright
import json
import os
import re
import sys
import time

IMG_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "图片")
DONE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_kdocs_upload_done.json")
ROW_START = int(sys.argv[1]) if len(sys.argv) > 1 else 257
ROW_END = int(sys.argv[2]) if len(sys.argv) > 2 else 505


def load_done():
    try:
        with open(DONE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_done(d):
    with open(DONE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def goto(page, ref, tries=3):
    """名称框跳转单元格，回读名称框值校验；不成功重试。返回是否到位。"""
    for _ in range(tries):
        try:
            page.click('input.edit-box', timeout=5000)
            page.keyboard.press("Control+a")
            page.keyboard.type(ref, delay=25)
            page.keyboard.press("Enter")
            time.sleep(1)
            if page.evaluate("() => document.querySelector('input.edit-box').value") == ref:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def cell_text(page):
    """读当前选中单元格内容（剪贴板）。"""
    page.keyboard.press("Control+c")
    time.sleep(0.6)
    return page.evaluate("() => navigator.clipboard.readText().catch(e => '')")


def insert_one(page, ref, img_path):
    """往 ref 单元格插入单元格图片。返回 True=已有图或插入成功，False=失败。"""
    if not goto(page, ref):
        return False
    if "DISPIMG" in cell_text(page):
        return True  # 已有图片，不重复插
    if not page.locator('button.kd-button-dropdown:has-text("图片"):visible').count():
        page.locator('div.tab-btn:has-text("插入")').first.click(timeout=4000)
        time.sleep(0.8)
    page.locator('button.kd-button-dropdown:has-text("图片"):visible').first.click(timeout=4000)
    time.sleep(0.8)
    page.locator('.ks-menu-item:has-text("单元格图片"):visible').first.click(timeout=4000)
    time.sleep(0.8)
    with page.expect_file_chooser(timeout=10000) as fc_info:
        page.locator('.ks-menu-item:has-text("本地"):visible').first.click(timeout=4000)
    fc_info.value.set_files(img_path)
    for _ in range(10):
        time.sleep(2)
        if "DISPIMG" in cell_text(page):
            page.keyboard.press("Escape")  # 关掉可能弹出的图片预览
            time.sleep(0.5)
            return True
    page.keyboard.press("Escape")
    time.sleep(0.5)
    return False


def scan_column(page, col, row_start, row_end):
    """整段读在线表格某一列，返回 {行号: 文本}。"""
    page.click('input.edit-box')
    page.keyboard.press("Control+a")
    page.keyboard.type(f"{col}{row_start}:{col}{row_end}", delay=15)
    page.keyboard.press("Enter")
    time.sleep(2)
    page.keyboard.press("Control+c")
    time.sleep(1)
    v = page.evaluate("() => navigator.clipboard.readText().catch(e => '')")
    return {row_start + i: ln.strip() for i, ln in enumerate(v.split("\n"))}


def scan_refund_rows(page, row_start, row_end):
    """整段读在线表格 AP 列，返回标记了退费/未缴的行号集合。"""
    ap = scan_column(page, "AP", row_start, row_end)
    return {r for r, t in ap.items() if "退费" in t or "未缴" in t}


def scan_has_image_rows(page, row_start, row_end):
    """整段读在线表格 AQ 列，返回已有图片（DISPIMG）的行号集合。"""
    aq = scan_column(page, "AQ", row_start, row_end)
    return {r for r, t in aq.items() if "DISPIMG" in t}


def main():
    row_img = {}
    for f in os.listdir(IMG_DIR):
        m = re.match(r"AQ(\d+)-(\d+)_.*\.png$", f)
        if m:
            for r in range(int(m.group(1)), int(m.group(2)) + 1):
                row_img[r] = os.path.join(IMG_DIR, f)
    done = load_done()
    failed = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "kdocs.cn" in pg.url:
                    page = pg
                    break
        if not page:
            print("未找到金山文档页面")
            return
        page.bring_to_front()
        try:
            page.evaluate("() => { window.alert = () => {}; window.confirm = () => true; }")
        except Exception:
            pass

        refund = scan_refund_rows(page, ROW_START, ROW_END)
        print(f"已退费/未缴行（跳过不传图）: {sorted(refund) if refund else '无'}")
        has_img = scan_has_image_rows(page, ROW_START, ROW_END)
        print(f"在线已有图片 {len(has_img)} 行（跳过）")

        # 只传漏掉的：本地有图 + 非退费 + 在线无图（以在线实际状态为准，不依赖历史缓存）
        rows = [r for r in range(ROW_START, ROW_END + 1)
                if r in row_img and r not in refund and r not in has_img]
        print(f"待上传 {len(rows)} 行")
        for i, r in enumerate(rows, 1):
            ref = f"AQ{r}"
            ok = False
            for attempt in range(2):
                try:
                    ok = insert_one(page, ref, row_img[r])
                except Exception as e:
                    print(f"  [{i}/{len(rows)}] {ref} 异常: {str(e)[:80]}")
                    time.sleep(2)
                if ok:
                    break
                print(f"  [{i}/{len(rows)}] {ref} 第{attempt + 1}次失败，重试")
                time.sleep(2)
            if ok:
                done[str(r)] = os.path.basename(row_img[r])
                save_done(done)
                print(f"[{i}/{len(rows)}] {ref} ✓ {os.path.basename(row_img[r])}")
            else:
                failed.append(r)
                print(f"[{i}/{len(rows)}] {ref} ✗ 上传失败")

    print("\n完成。失败行:", failed if failed else "无")


if __name__ == "__main__":
    main()
