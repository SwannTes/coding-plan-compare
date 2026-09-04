#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自动叫号脚本（常驻）：每5秒刷新HIS待诊列表，按挂号流水号从小到大自动叫下一位患者。

循环逻辑：刷新列表（同工具栏「刷新(F4)」）→ 跳过已叫过号的 → 选中流水号最小的患者
（没有流水号的现场挂号按挂号时间排在有号的后面）→ 点「叫号(F2)」→ 处理弹窗。每轮最多叫一位。
已叫过号的判断：流水号单元格绿色（页面图例：绿色=已叫过号），或就诊号码在本脚本记录里
（_called_ids.json，按天重置）——没有流水号的行绿色不可靠，只能靠记录避免重复叫。
医生打开病历/切模块时列表被遮住也能叫号（JS 点击不要求按钮可见）；只有模态弹窗
（带遮罩的确认框）开着时才停手，且只自动处理「重新叫号」弹窗（点否），其他不碰。
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


def parse_args():
    """命令行参数（面板传入）：argv[1]=叫号间隔秒数（非法/空回退5），
    argv[2]=是否叫慢病患者（"0"/"不叫"/"否"等=不叫；空或缺省=叫）。"""
    interval = 5.0
    if len(sys.argv) > 1:
        try:
            n = float(sys.argv[1])
            if n >= 3:
                interval = n
        except ValueError:
            pass
    call_chronic = True
    if len(sys.argv) > 2:
        call_chronic = sys.argv[2].strip().lower() not in ("0", "不叫", "否", "no", "false")
    return interval, call_chronic


INTERVAL, CALL_CHRONIC = parse_args()


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
    """点击文字匹配 keyword 的按钮（JS 点击，不要求可见——医生打开病历/切模块时
    工具栏会被遮住或隐藏，但按钮还在 DOM 里，服务端照常处理）。
    keyword 支持 "精确|兜底" 形式：优先点带 F 键标记的（如 叫号(F2)），
    避免点到别的模块里的同名按钮。"""
    return page.evaluate("""
        (kw) => {
            const [exact, loose] = kw.split('|');
            const btns = [...document.querySelectorAll('button')];
            let btn = btns.find(b => b.textContent.includes(exact));
            if (!btn && loose) btn = btns.find(b => b.textContent.includes(loose));
            if (!btn) return false;
            btn.click();
            return true;
        }
    """, keyword)


# 待诊表格定位：表头同时含「流水号」「就诊号码」的那个 grid。
# 医生打开病历时列表会被遮住（不可见）但 DOM 数据还在——不看可见性：
# 优先选可见的，其次选行数多的（隐藏缓存副本靠后）。
GRID_FIND_JS = """
    const grids = [...document.querySelectorAll('.x-grid3')].filter(g => {
        const headers = [...g.querySelectorAll('.x-grid3-hd-inner')].map(h => (h.textContent || '').trim());
        return headers.includes('流水号') && headers.includes('就诊号码');
    });
    if (!grids.length) return null;
    grids.sort((a, b) => {
        const av = a.getBoundingClientRect().width > 0 ? 0 : 1;
        const bv = b.getBoundingClientRect().width > 0 ? 0 : 1;
        if (av !== bv) return av - bv;
        return b.querySelectorAll('.x-grid3-row').length - a.querySelectorAll('.x-grid3-row').length;
    });
    return grids[0];
"""

# 读出待诊表格所有行。已叫过号标志：流水号单元格文字绿色（页面图例：绿色=已叫过号）。
READ_ROWS_JS = """
() => {
    const g = (() => {""" + GRID_FIND_JS + """})();
    if (!g) return null;
    const headers = [...g.querySelectorAll('.x-grid3-hd-inner')].map(h => (h.textContent || '').trim());
    const iLsh = headers.indexOf('流水号');
    const iName = headers.indexOf('姓名');
    const iGh = headers.indexOf('挂号时间');
    const iJzh = headers.indexOf('就诊号码');
    const iGrp = headers.indexOf('人群分类');
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
            group: iGrp >= 0 ? cellText(iGrp) : '',
            lshColor: tds[iLsh] ? (tds[iLsh].style.color || '') : '',
            selected: row.className.includes('x-grid3-row-selected'),
        });
    });
    return rows;
}
"""

SELECT_ROW_JS = """
({idx, clickOnly}) => {
    const g = (() => {""" + GRID_FIND_JS + """})();
    if (!g) return null;
    const row = g.querySelectorAll('.x-grid3-row')[idx];
    if (!row) return null;
    if (clickOnly) { row.click(); return true; }
    const r = row.getBoundingClientRect();
    return {x: r.x + 30, y: r.y + r.height / 2, visible: r.width > 0};
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


# 弹窗处理：只碰和「叫号」有关的弹窗——「重新叫号」点否（绝不重复叫）；
# 其他叫号提示点确定；和叫号无关的弹窗（可能是医生正在看的）一律不点，只读出文字。
DIALOG_JS = """
(handle) => {
    for (const win of document.querySelectorAll('.x-window, .x-msg-box')) {
        const r = win.getBoundingClientRect();
        if (r.width === 0 || getComputedStyle(win).visibility === 'hidden') continue;
        const text = (win.textContent || '').trim().slice(0, 200);
        if (!text.includes('叫号')) return {clicked: null, text, skipped: true};
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

# 只有真正的模态弹窗（带遮罩 .ext-el-mask，或消息框 .x-msg-box）才算"弹窗开着"。
# 病历浏览等普通 .x-window 不带遮罩，不算——否则看病历时脚本整天都不敢叫号。
MODAL_CHECK_JS = """
() => {
    const visible = el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && getComputedStyle(el).display !== 'none'
               && getComputedStyle(el).visibility !== 'hidden';
    };
    const maskOn = [...document.querySelectorAll('.ext-el-mask')].some(visible);
    for (const win of document.querySelectorAll('.x-msg-box')) {
        if (visible(win)) return (win.textContent || '').trim().slice(0, 200);
    }
    if (maskOn) {
        for (const win of document.querySelectorAll('.x-window')) {
            if (visible(win)) return (win.textContent || '').trim().slice(0, 200);
        }
        return '（有遮罩层但未识别到弹窗）';
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

    # 0b. 模态弹窗（带遮罩/消息框）开着时不点按钮：只有「重新叫号」会自动点否，
    # 其他模态弹窗（可能是医生正在操作的）不碰，跳过本轮。
    # 病历浏览等普通窗口不带遮罩，不影响叫号。
    modal = page.evaluate(MODAL_CHECK_JS)
    if modal:
        if "重新叫号" in modal:
            page.evaluate(DIALOG_JS, True)
            return "idle", "关闭了一个遗留的「重新叫号」弹窗"
        return "busy", f"界面有弹窗未关闭，本轮跳过：{modal[:50]}"

    # 1. 刷新待诊列表（等同点「刷新(F4)」。按钮被病历窗口遮住/所在面板隐藏时
    # 也能点到：JS 点击不要求可见，服务端照常处理）
    if not click_toolbar_button(page, "刷新(F4)|刷新"):
        return "busy", "当前页面没有刷新按钮（可能不在门诊诊疗模块），本轮跳过"
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
    if not CALL_CHRONIC:
        # 面板取消了「叫慢病患者」：高血压/糖尿病人群分类的跳过不叫
        todo = [r for r in todo if "高血压" not in r["group"] and "糖尿病" not in r["group"]]
    if not todo:
        return "idle", f"待诊 {len(waiting)} 人，均已叫过号"

    # 3. 按挂号流水号从小到大排序（无流水号的排最后，按挂号时间），取第一位
    todo.sort(key=lambda r: (lsh_number(r["liushui"]) is None,
                             lsh_number(r["liushui"]) or 0, r["guahao"]))
    target = todo[0]

    # 4. 选中该行（JS 点击失败且行可见时才用真实鼠标兜底）
    page.evaluate(SELECT_ROW_JS, {"idx": target["idx"], "clickOnly": True})
    time.sleep(0.4)
    rows2 = page.evaluate(READ_ROWS_JS)
    if not rows2[target["idx"]]["selected"]:
        rect = page.evaluate(SELECT_ROW_JS, {"idx": target["idx"], "clickOnly": False})
        if rect and rect.get("visible"):
            page.mouse.click(rect["x"], rect["y"])
            time.sleep(0.4)

    # 5. 点「叫号(F2)」
    if not click_toolbar_button(page, "叫号(F2)|叫号"):
        return "error", "没找到「叫号」按钮"
    time.sleep(1.2)

    desc = f"{target['name']}（流水号 {target['liushui'] or '空'}，挂号时间 {target['guahao']}）"

    # 6. 处理叫号相关弹窗（如有）：「是否重新叫号」点否避免重复叫号；
    # 与叫号无关的弹窗不碰（可能是医生正在操作的）
    dialog = page.evaluate(DIALOG_JS, True)
    if dialog and not dialog.get("skipped"):
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
    log(f"自动叫号已启动，每 {INTERVAL:g} 秒刷新一次（今天已记录叫过 {len(called_ids)} 人，"
        f"{'叫' if CALL_CHRONIC else '不叫'}慢病患者）")
    while True:  # 外层：Playwright 驱动崩溃（如原生 alert 导致的 driver 崩溃）时整体重建
        try:
            run_loop(called_ids)
        except Exception:
            log("Playwright 驱动崩溃，5 秒后自动重建："
                + traceback.format_exc().splitlines()[-1])
            time.sleep(5)


def run_loop(called_ids):
    browser = None
    last_msg = None
    last_err_log = 0.0
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
                err = traceback.format_exc().splitlines()[-1]
                if "Connection closed" in err or "Target crashed" in err:
                    raise  # 驱动级故障，内层救不回来，抛给外层重建 playwright
                # 页面刷新/Chrome重启等瞬断，重置连接下轮重试
                browser = None
                status, msg = "error", "连接异常：" + err
            # 只记状态变化，避免日志刷屏；但错误状态每 5 分钟重复记一次，防止"静默死亡"
            if status != "idle" and (msg != last_msg or time.time() - last_err_log > 300):
                log(msg)
                last_err_log = time.time()
            last_msg = msg
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
