#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自动叫号脚本（常驻）：每5秒刷新HIS待诊列表，按挂号流水号从小到大自动叫下一位患者。

循环逻辑：刷新列表（同工具栏「刷新(F4)」）→ 跳过已叫过号的 → 选中流水号最小的患者
（没有流水号的现场挂号按挂号时间排在有号的后面）→ 点「叫号(F2)」→ 处理弹窗。每轮最多叫一位。
已叫过号的判断：流水号单元格绿色（页面图例：绿色=已叫过号），或就诊号码在本脚本记录里
（_called_ids.json，按天重置）——没有流水号的行绿色不可靠，只能靠记录避免重复叫。
当前页面不在待诊列表界面（找不到刷新按钮/表格）时静默跳过，5秒后再试。
由 button_service 以 daemon 模式启动/停止，日志写入 button_service.log。
"""

from playwright.sync_api import sync_playwright
import json
import os
import re
import sys
import time
import traceback

TARGET_URL = "172.17.8.14:8780"
CALLED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_called_ids.json")


def parse_interval():
    """叫号间隔（秒）：命令行第一个参数，面板输入框传入；非法值回退5秒。"""
    try:
        n = float(sys.argv[1])
        if n >= 3:
            return n
    except (IndexError, ValueError):
        pass
    return 5.0


INTERVAL = parse_interval()


def log(*args):
    print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)


def load_called_ids():
    """读出今天已叫过号的就诊号码集合；文件是旧日期的就丢弃重来。"""
    today = time.strftime("%Y-%m-%d")
    try:
        with open(CALLED_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == today:
            return set(data.get("ids", []))
    except (OSError, ValueError):
        pass
    return set()


def save_called_ids(ids):
    try:
        with open(CALLED_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d"), "ids": sorted(ids)}, f,
                      ensure_ascii=False)
    except OSError:
        pass


def find_his_page(browser):
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if TARGET_URL in pg.url:
                return pg
    return None


def click_toolbar_button(page, keyword):
    """点击工具栏里文字包含 keyword 的可见按钮，返回是否点到。"""
    return page.evaluate("""
        (kw) => {
            for (const btn of document.querySelectorAll('button')) {
                if (!btn.textContent.includes(kw)) continue;
                const st = getComputedStyle(btn);
                const r = btn.getBoundingClientRect();
                if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                btn.click();
                return true;
            }
            return false;
        }
    """, keyword)


# 在可见的待诊表格（表头同时含「流水号」「就诊号码」的那个）里读出所有行。
# 已叫过号的标志之一：流水号单元格文字为绿色（页面图例：绿色标记的待诊病人为已叫过号的病人）。
READ_ROWS_JS = """
() => {
    for (const g of document.querySelectorAll('.x-grid3')) {
        if (g.getBoundingClientRect().width === 0) continue;
        const headers = [...g.querySelectorAll('.x-grid3-hd-inner')].map(h => (h.textContent || '').trim());
        if (!headers.includes('流水号') || !headers.includes('就诊号码')) continue;
        const iLsh = headers.indexOf('流水号');
        const iName = headers.indexOf('姓名');
        const iGh = headers.indexOf('挂号时间');
        const iJzh = headers.indexOf('就诊号码');
        const rows = [];
        g.querySelectorAll('.x-grid3-row').forEach((row, idx) => {
            const tds = row.querySelectorAll('.x-grid3-cell');
            const cellText = i => (tds[i] ? (tds[i].textContent || '').trim() : '');
            rows.push({
                idx,
                liushui: cellText(iLsh),
                name: cellText(iName),
                guahao: cellText(iGh),
                jzh: cellText(iJzh),
                lshColor: tds[iLsh] ? (tds[iLsh].style.color || '') : '',
                selected: row.className.includes('x-grid3-row-selected'),
            });
        });
        return rows;
    }
    return null;
}
"""

SELECT_ROW_JS = """
({idx, clickOnly}) => {
    for (const g of document.querySelectorAll('.x-grid3')) {
        if (g.getBoundingClientRect().width === 0) continue;
        const headers = [...g.querySelectorAll('.x-grid3-hd-inner')].map(h => (h.textContent || '').trim());
        if (!headers.includes('流水号') || !headers.includes('就诊号码')) continue;
        const row = g.querySelectorAll('.x-grid3-row')[idx];
        if (!row) return null;
        if (clickOnly) { row.click(); return true; }
        const r = row.getBoundingClientRect();
        return {x: r.x + 30, y: r.y + r.height / 2};
    }
    return null;
}
"""


def is_green(color):
    """文字颜色偏绿 = 已叫过号（如 rgb(0, 170, 0)）。"""
    m = re.search(r"(\d+),\s*(\d+),\s*(\d+)", color or "")
    if not m:
        return False
    r, g, b = map(int, m.groups())
    return g > 120 and g > r + 30 and g > b + 30


def lsh_number(s):
    digits = re.sub(r"\D", "", s or "")
    return int(digits) if digits else None


# 弹窗处理：「重新叫号」点否（绝不重复叫）；其余提示点确定；没有匹配按钮就只读出文字
DIALOG_JS = """
(handle) => {
    for (const win of document.querySelectorAll('.x-window, .x-msg-box')) {
        const r = win.getBoundingClientRect();
        if (r.width === 0 || getComputedStyle(win).visibility === 'hidden') continue;
        const text = (win.textContent || '').trim().slice(0, 200);
        if (!handle) return {clicked: null, text};
        const want = text.includes('重新叫号') ? ['否'] : ['确定', '是'];
        for (const btn of win.querySelectorAll('button')) {
            const t = btn.textContent.trim();
            if (want.some(w => t === w || t.startsWith(w + '('))) { btn.click(); return {clicked: t, text}; }
        }
        return {clicked: null, text};
    }
    return null;
}
"""


def call_next(page, called_ids):
    """刷新并叫下一位。返回 (状态, 消息)；状态: idle / called / busy / error。"""
    # 0a. 安全检查：「待诊」复选框必须勾着，否则列表内容不对（如混入已诊/暂挂），宁可不叫
    daizhen = page.evaluate("""
        () => {
            for (const w of document.querySelectorAll('.x-form-check-wrap')) {
                if ((w.textContent || '').replace(/\\s+/g, '') === '待诊') {
                    const inp = w.querySelector('input');
                    return inp ? !!inp.checked : null;
                }
            }
            return null;
        }
    """)
    if daizhen is not True:
        return "busy", "「待诊」复选框未勾选或不在待诊界面，本轮跳过"

    # 0b. 有弹窗开着时绝不动列表：模态弹窗挡不住JS点击，贸然刷新/叫号可能在弹窗背后误操作。
    # 「重新叫号」弹窗点否关掉；其他弹窗（可能是医生正在看的）不碰，跳过本轮。
    dialog = page.evaluate(DIALOG_JS, False)
    if dialog:
        if "重新叫号" in dialog["text"]:
            page.evaluate(DIALOG_JS, True)
            return "idle", "关闭了一个遗留的「重新叫号」弹窗"
        return "busy", f"界面有弹窗未关闭，本轮跳过：{dialog['text'][:50]}"

    # 1. 刷新待诊列表（等同点「刷新(F4)」）
    if not click_toolbar_button(page, "刷新"):
        return "busy", "当前页面不是待诊列表界面（找不到刷新按钮），本轮跳过"
    time.sleep(1.2)

    # 2. 读出待诊患者（以就诊号码为唯一标识；流水号可能为空，如刚挂号的现场号）
    rows = page.evaluate(READ_ROWS_JS)
    if rows is None:
        return "busy", "没找到待诊患者表格，本轮跳过"
    waiting = [r for r in rows if r["name"] and r["jzh"]]

    def already_called(r):
        if r["jzh"] in called_ids:
            return True
        # 绿色只对有流水号的行可信；无流水号的行绿色不准（见过未叫号就是绿色的）
        return bool(r["liushui"]) and is_green(r["lshColor"])

    todo = [r for r in waiting if not already_called(r)]
    if not todo:
        return "idle", f"待诊 {len(waiting)} 人，均已叫过号"

    # 3. 按挂号流水号从小到大排序（无流水号的排最后，按挂号时间），取第一位
    todo.sort(key=lambda r: (lsh_number(r["liushui"]) is None,
                             lsh_number(r["liushui"]) or 0, r["guahao"]))
    target = todo[0]

    # 4. 选中该行（JS 点击失败时用真实鼠标事件兜底）
    page.evaluate(SELECT_ROW_JS, {"idx": target["idx"], "clickOnly": True})
    time.sleep(0.4)
    rows2 = page.evaluate(READ_ROWS_JS)
    if not rows2[target["idx"]]["selected"]:
        rect = page.evaluate(SELECT_ROW_JS, {"idx": target["idx"], "clickOnly": False})
        if rect:
            page.mouse.click(rect["x"], rect["y"])
            time.sleep(0.4)

    # 5. 点「叫号(F2)」
    if not click_toolbar_button(page, "叫号"):
        return "error", "没找到「叫号」按钮"
    time.sleep(1.2)

    desc = f"{target['name']}（流水号 {target['liushui'] or '空'}，挂号时间 {target['guahao']}）"

    # 6. 处理弹窗（如有）：「是否重新叫号」点否避免重复叫号；其余提示点确定
    dialog = page.evaluate(DIALOG_JS, True)
    if dialog:
        if "重新叫号" in dialog["text"]:
            called_ids.add(target["jzh"])
            save_called_ids(called_ids)
            return "idle", f"{target['name']} 刚已被叫过号，本轮跳过"
        if re.search(r"失败|错误|不能|无法", dialog["text"]):
            return "error", f"叫号弹窗报错：{dialog['text']}"
        time.sleep(1)

    # 7. 确认结果：有流水号的应变绿或从列表消失；无流水号的只要没报错弹窗即视为成功
    rows3 = page.evaluate(READ_ROWS_JS) or []
    now = [r for r in rows3 if r["jzh"] == target["jzh"]]
    if target["liushui"]:
        if now and not is_green(now[0]["lshColor"]):
            return "error", f"已点击叫号，但 {target['name']} 的流水号未变绿，请人工确认"
    called_ids.add(target["jzh"])
    save_called_ids(called_ids)
    return "called", f"已叫号：{desc}"


def main():
    called_ids = load_called_ids()
    log(f"自动叫号已启动，每 {INTERVAL:g} 秒刷新一次（今天已记录叫过 {len(called_ids)} 人）")
    browser = None
    last_msg = None
    with sync_playwright() as p:
        while True:
            try:
                if browser is None or not browser.is_connected():
                    browser = p.chromium.connect_over_cdp("http://localhost:9222")
                page = find_his_page(browser)
                if not page:
                    status, msg = "error", f"未找到HIS页面（{TARGET_URL}），等调试Chrome打开后自动恢复"
                else:
                    status, msg = call_next(page, called_ids)
            except Exception:
                # 页面刷新/Chrome重启等瞬断，重置连接下轮重试
                browser = None
                status, msg = "error", "连接异常：" + traceback.format_exc().splitlines()[-1]
            # 只记状态变化，避免日志刷屏
            if status != "idle" and msg != last_msg:
                log(msg)
            last_msg = msg
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
