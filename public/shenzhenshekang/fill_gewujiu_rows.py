#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""隔物灸记账明细 254~505 行自动填写

处理规则（与口头约定一致）：
1. 收费总金额(ZJJE) != 缴费金额(JFJE) → 不填，进 Desktop/隔物灸_待人工审核.xlsx
2. 治疗计划无部位艾灸也无穴位 → 诊断/主诉/既往史含 感冒/咳嗽/鼻鼽/痛痹/腰痛/胃痞
   → 按疾病模板填；否则 col39=违规 col42=认可违规
3. 治疗计划有穴位（督脉/膀胱经/夹脊/阿是穴/经验穴等）→ "病历中已涉及相关穴位，
   涉及数量达到XX穴…"（颈肩腰背=73，腹=39，腰背+腹=112）
4. 治疗计划只有部位 → 按部位+诊断套参考模板（表内 2~1266 行已填样例）

每个成功处理的就诊：打印病历(F5) → 屏幕截图（含翻页）→ Desktop/病历截图/，
图片名 "行号段_姓名_日期_pN.png"。结果缓存 _gewujiu_fill_cache.json，可中断续跑。
最后把 col39~42 写回原 xlsx（zip 级定点改写，保留 WPS 嵌入图片）。
"""

from playwright.sync_api import sync_playwright
from PIL import ImageGrab
import ctypes
from ctypes import wintypes
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_gewujiu_own import PANEL_JS, run_step, run_click, locate, search_and_wait

BASE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
SHOT_DIR = os.path.join(DESKTOP, "病历截图")
CACHE_FILE = os.path.join(BASE, "_gewujiu_fill_cache.json")
DECISIONS_FILE = os.path.join(BASE, "_gewujiu_fill_decisions.json")
SRC_XLSX = None  # main 里定位

ROW_START, ROW_END = 254, 505
DATE_START, DATE_END = "2024-09-20", "2024-10-26"

user32 = ctypes.windll.user32

# ---------------- 模板（全部取自原表已填参考行，服务中心版） ----------------
TAIL = "治疗穴位数量大于收费数量，隔物灸法根据《麓园社区健康服务中心隔物灸操作流程及规范》操作。"
GENERIC_ACU = "病历中已涉及相关穴位，涉及数量达到{n}穴，治疗穴位数量大于收费数量，隔物灸法根据《麓园社区健康服务中心隔物灸操作流程及规范》操作。"

T73 = {
    "感冒": "患者诊断感冒，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散风解表；膀胱经主表，宣肺解表、祛风散寒以治感冒。涉及穴位共73个，" + TAIL,
    "咳嗽": "患者诊断咳嗽，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散风解表；膀胱经主表，宣肺解表、祛风散寒以治咳嗽。涉及穴位共73个，" + TAIL,
    "鼻鼽": "患者诊断鼻鼽，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散风解表；膀胱经主表，宣肺通窍、祛风散寒以治鼻鼽。涉及穴位共73个，" + TAIL,
    "痛痹": "患者诊断痛痹，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散寒以疗痛痹，膀胱经主筋所生病，可以通经活络以疗痛痹。涉及穴位共73个，" + TAIL,
    "腰痛": "患者诊断腰痛，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能补肾强腰；膀胱经主筋所生病，可以通经活络以疗腰痛。涉及穴位共73个，" + TAIL,
    "头痛": "患者诊断头痛，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散寒以疗痛痹，膀胱经主筋所生病，可以通经活络以疗痛痹。涉及穴位共73个，" + TAIL,
    "失眠": "患者诊断失眠，予腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能燮理阴阳；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗失眠。涉及穴位共73个，" + TAIL,
    "不寐病": "患者诊断不寐病，予腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能燮理阴阳；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗失眠。涉及穴位共73个，" + TAIL,
    "月经后期": "患者诊断月经后期，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散寒，调理冲任；膀胱经为背俞穴所在，可以调理阴阳、疏肝理气以疗月经类病。涉及穴位共73个，" + TAIL,
    "月经过少": "患者诊断月经过少，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散寒，调理冲任；膀胱经为背俞穴所在，可以调理阴阳、疏肝理气以疗月经类病。涉及穴位共73个，" + TAIL,
    "月经稀少": "患者诊断月经稀少，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能温阳散寒，调理冲任；膀胱经为背俞穴所在，可以调理阴阳、疏肝理气以疗月经类病。涉及穴位共73个，" + TAIL,
    "急性腰扭伤": "患者诊断急性腰扭伤，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能补肾强腰；膀胱经主筋所生病，可以通经活络以疗腰痛。涉及穴位共73个，" + TAIL,
    "湿疹": "患者诊断湿疹，予颈肩、腰背部督脉、双侧膀胱经隔物灸。督脉为阳脉之海，总督一身阳经，能利水渗湿以疗湿疹，膀胱经主筋所生病，可以通经活络以疗湿疮。涉及穴位共73个，" + TAIL,
}
T39 = {
    "胃痞": "患者诊断胃痞病，在腹部进行施灸，涉及腹部穴位共39个，消食导滞，" + TAIL,
    "胃痞病": "患者诊断胃痞病，在腹部进行施灸，涉及腹部穴位共39个，消食导滞，" + TAIL,
    "腹痛": "患者诊断腹痛，在腹部进行施灸，涉及腹部穴位共37个，健脾和胃，调畅中焦气机，帮助邪气排出。治疗穴位数量大于收费数量，隔物灸法根据《麓园社区健康服务中心隔物灸操作流程及规范》操作。",
    "腹胀": "患者诊断腹胀，在腹部进行施灸，涉及腹部穴位共39个，消食导滞，" + TAIL,
    "痛经": "患者诊断痛经，在腹部进行施灸，涉及腹部穴位共39个，活血化瘀、调理冲任，" + TAIL,
    "月经后期": "患者诊断月经后期，在腹部进行施灸，涉及腹部穴位共39个，活血化瘀、调理冲任，" + TAIL,
    "月经过少": "患者诊断月经过少，在腹部进行施灸，涉及腹部穴位共39个，活血化瘀、调理冲任，" + TAIL,
    "慢性胃炎": "患者诊断慢性胃炎，在腹部进行施灸，涉及腹部穴位共39个，消食导滞，" + TAIL,
    "呕吐": "该患者诊断为呕吐，在腹部进行施灸，涉及腹部穴位共39个，消食导滞，" + TAIL,
    "消化不良": "患者诊断消化不良，在腹部进行施灸，涉及腹部穴位共37个，健脾和胃，调畅中焦气机，帮助邪气排出。治疗穴位数量大于收费数量，隔物灸法根据《麓园社区健康服务中心隔物灸操作流程及规范》操作。",
}
T112 = {
    "胃痞": "患者诊断胃痞，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗胃痞。涉及穴位共112个，" + TAIL,
    "胃痞病": "患者诊断胃痞，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗胃痞。涉及穴位共112个，" + TAIL,
    "脘痞": "患者诊断脘痞，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗脘痞。涉及穴位共112个，" + TAIL,
    "痞气": "患者诊断痞气，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗脘痞。涉及穴位共112个，" + TAIL,
    "胃痛": "患者诊断胃痛，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗胃痛。涉及穴位共112个，" + TAIL,
    "腹痛": "患者诊断腹痛，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗腹痛。涉及穴位共112个，" + TAIL,
    "腹胀": "患者诊断腹胀，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗脾胃。涉及穴位共112个，" + TAIL,
    "呕吐": "患者诊断呕吐，予腰背部督脉、双侧膀胱、腹部隔物灸。督脉为阳脉之海，总督一身阳经，能升阳以健脾胃；膀胱经为背俞穴所在，阴病治阳，可以温养五脏六腑以疗脾胃。涉及穴位共112个，" + TAIL,
}

DISEASE_KEYS = ["急性腰扭伤", "胃肠功能紊乱", "消化不良", "慢性胃炎", "月经后期", "月经过少",
                "月经稀少", "月经类病", "不寐病", "鼻鼽", "感冒", "咳嗽", "痛痹", "腰痛",
                "胃痞病", "胃痞", "脘痞", "痞气", "腹痛", "腹胀", "胃痛", "呕吐", "痛经",
                "失眠", "头痛", "湿疹", "眩晕"]
RULE2_SIX = {"感冒": T73["感冒"], "咳嗽": T73["咳嗽"], "鼻鼽": T73["鼻鼽"],
             "痛痹": T73["痛痹"], "腰痛": T73["腰痛"], "胃痞": T39["胃痞"]}
ACU_SYSTEMS = ["督脉", "膀胱经", "夹脊", "阿是穴", "经验穴", "经验效穴"]
PART_RE = re.compile(r"(颈肩腰背部|颈肩腰背|颈肩|颈腰|腰背|腰骶|颈|肩|腰|背|腹|膝|腿|踝)"
                     r"[^，。；：\n]{0,15}?(?:艾灸|隔物灸)")


def seg(text, start_kw, end_kws):
    i = text.find(start_kw)
    if i < 0:
        return ""
    j = len(text)
    for kw in end_kws:
        k = text.find(kw, i + len(start_kw))
        if 0 <= k < j:
            j = k
    return text[i:j]


def find_disease(text):
    for k in DISEASE_KEYS:
        if k in text:
            return k
    return None


def classify(record_text):
    """返回 (status, col42_template, note)。status: fill / wgui / review"""
    plan_m = re.search(r"治疗计划[：:\s]*([^\n]+)", record_text)
    plan = plan_m.group(1) if plan_m else ""
    diag = seg(record_text, "初步诊断", ["处理措施", "管理计划"])
    zs = seg(record_text, "主诉", ["现病史", "症状"])
    wjs = seg(record_text, "既往史", ["体格检查", "体温", "实验室"])
    disease = find_disease(diag) or find_disease(zs) or find_disease(wjs)

    has_system = any(k in plan for k in ACU_SYSTEMS)
    part_m = PART_RE.search(plan)
    part = part_m.group(0) if part_m else ""

    if has_system:
        if "腹" in plan:
            n = 112 if ("督脉" in plan or "膀胱" in plan) else 39
        else:
            n = 73
        return "fill", GENERIC_ACU.format(n=n), f"穴位类({n}穴) 治疗计划: {plan[:60]}"
    if part_m:
        if "腹" in part and re.search(r"颈|肩|腰|背|督|膀胱", part):
            fam, n = T112, 112
        elif "腹" in part:
            fam, n = T39, 39
        elif re.search(r"颈|肩|腰|背", part):
            fam, n = T73, 73
        else:
            return "review", None, f"非常见部位[{part}] 治疗计划: {plan[:60]}"
        if disease and disease in fam:
            return "fill", fam[disease], f"部位类({n}穴,{disease}) 治疗计划: {plan[:60]}"
        return "review", None, f"部位[{part}]无匹配模板(诊断:{disease or diag[:40]})"
    # 治疗计划部位、穴位都没有 → 规则2 六病
    for k, tpl in RULE2_SIX.items():
        if k in diag or k in zs or k in wjs:
            return "fill", tpl, f"疾病类({k}) 诊断: {diag[:60].strip()}"
    return "wgui", "认可违规", f"无部位无穴位非六病 诊断: {diag[:60].strip()} 主诉: {zs[:30]}"


# ---------------- Windows 弹窗（打印预览）操作 ----------------

def enum_windows():
    res = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                res.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True
    user32.EnumWindows(cb, 0)
    return res


def find_preview():
    wins = [w for w in enum_windows() if "打印预览" in w[1]]
    return wins[0] if wins else None


def wait_preview(timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        w = find_preview()
        if w:
            return w
        time.sleep(0.5)
    return None


def os_click(x, y):
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def close_preview():
    w = find_preview()
    if not w:
        return True
    hwnd, _, rect = w
    # 优先点工具栏"关闭"按钮（窗口左上角起 494,42）
    os_click(rect[0] + 494, rect[1] + 42)
    deadline = time.time() + 5
    while time.time() < deadline:
        if not find_preview():
            return True
        time.sleep(0.4)
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE 兜底
    time.sleep(1)
    return find_preview() is None


def screenshot_record(name, date, rows_tag):
    """对当前打印预览逐页截图，返回保存的文件名列表。"""
    w = find_preview()
    if not w:
        return []
    hwnd, _, rect = w
    user32.ShowWindow(hwnd, 3)  # 最大化，坐标才稳定
    time.sleep(0.8)
    w = find_preview()
    rect = w[2]
    bbox = (max(rect[0], 0), max(rect[1], 0), min(rect[2], 1920), min(rect[3], 1080))
    next_xy = (bbox[0] + 259, bbox[1] + 42)   # 下页
    saved = []
    prev_crop = None
    for i in range(1, 9):
        img = ImageGrab.grab(bbox=bbox)
        crop = img.crop((600, 100, 1300, 950))
        if prev_crop is not None and crop.tobytes() == prev_crop.tobytes():
            break  # 下页没反应，已到末页
        prev_crop = crop
        fn = f"{rows_tag}_{name}_{date}_p{i}.png"
        img.save(os.path.join(SHOT_DIR, fn))
        saved.append(fn)
        os_click(*next_xy)
        time.sleep(1.2)
    return saved


# ---------------- 浏览器侧 ----------------

SCRAPE_DAY_JS = "async () => {" + PANEL_JS + r"""
    const grid = findGrid();
    if (!grid) return {error: '未找到结果表格组件'};
    const store = grid.getStore();
    const bbar = grid.getBottomToolbar();
    if (!bbar) return {error: '未找到分页工具栏'};
    if (bbar.cursor !== 0) {
        await new Promise(res => {
            const h = () => { store.un('load', h); res(); };
            store.on('load', h);
            bbar.moveFirst();
            setTimeout(res, 8000);
        });
    }
    const total = store.getTotalCount();
    const seen = {};
    let guard = 0;
    while (true) {
        store.each(rec => { seen[rec.data.GHXH] = rec.data; });
        if (bbar.cursor + bbar.pageSize >= total) break;
        const before = bbar.cursor;
        let advanced = false;
        for (let attempt = 0; attempt < 3 && !advanced; attempt++) {
            await new Promise(res => {
                const h = () => { store.un('load', h); res(); };
                store.on('load', h);
                bbar.moveNext();
                setTimeout(res, 8000);
            });
            advanced = bbar.cursor > before;
        }
        if (!advanced) return {error: '翻页失败(cursor=' + before + ')', collected: Object.keys(seen).length};
        if (++guard > 400) return {error: '翻页次数超限', collected: Object.keys(seen).length};
    }
    const keep = ['GHXH','BRXM','SFZH','GHSJ','YSDM_text','ZJJE','JFJE'];
    const rows = Object.values(seen).map(d => {
        const o = {};
        keep.forEach(k => { o[k] = d[k]; });
        return o;
    });
    return {total, rows};
}"""

RECORD_TEXT_JS = "() => {" + PANEL_JS + r"""
    const pel = panelEl();
    if (!pel) return null;
    // 病历显示在 grid 行的展开区(.x-grid3-row-body)。同一患者多个挂号时
    // DOM 里会残留上一条挂号的隐藏病历，必须取"当前选中行"的展开区
    const grid = findGrid();
    if (grid) {
        const sel = grid.getSelectionModel().getSelected();
        if (sel) {
            const idx = grid.getStore().indexOf(sel);
            const rowEl = grid.getView().getRow(idx);
            const body = rowEl ? rowEl.querySelector('.x-grid3-row-body') : null;
            if (body && (body.textContent || '').indexOf('主诉') >= 0) {
                return body.innerText || body.textContent || '';
            }
        }
    }
    // 兜底：取可见的展开区
    const bodies = Array.from(pel.querySelectorAll('.x-grid3-row-body'));
    const vis = bodies.filter(el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && window.getComputedStyle(el).display !== 'none'
            && (el.textContent || '').indexOf('主诉') >= 0;
    });
    if (vis.length) return vis[vis.length - 1].innerText || vis[vis.length - 1].textContent || '';
    return null;
}"""

SELECT_ROW_JS = "(ghxh) => {" + PANEL_JS + r"""
    const grid = findGrid();
    if (!grid) return false;
    const store = grid.getStore();
    const idx = store.findExact('GHXH', ghxh);
    if (idx < 0) return false;
    grid.getSelectionModel().selectRow(idx);
    return true;
}"""

MARK_PRINT_BTN_JS = "() => {" + PANEL_JS + r"""
    const pel = panelEl();
    if (!pel) return false;
    let btn = null;
    Ext.ComponentMgr.all.each(c => {
        if (btn) return;
        if (c instanceof Ext.Button && c.el && c.el.dom && pel.contains(c.el.dom)
            && (c.text || '').indexOf('打印病历') >= 0) btn = c;
    });
    if (!btn) return false;
    btn.el.dom.setAttribute('data-kimi-click', '1');
    return true;
}"""


def read_visits():
    """读原表 254~505 未填行，返回 visits: [{name, sfzh, date, rows, qtys, amts}]"""
    from openpyxl import load_workbook
    files = [f for f in os.listdir(DESKTOP) if "隔物灸" in f and "记账明细表" in f
             and f.endswith(".xlsx") and not f.startswith("~$")]
    global SRC_XLSX
    SRC_XLSX = os.path.join(DESKTOP, files[0])
    wb = load_workbook(SRC_XLSX, data_only=True)
    ws = wb["明细"]
    groups = {}
    for r in range(ROW_START, ROW_END + 1):
        if ws.cell(r, 39).value is not None or ws.cell(r, 42).value is not None:
            continue
        name = str(ws.cell(r, 2).value or "").strip()
        date = str(ws.cell(r, 10).value or "")[:10]
        if not name or not date:
            continue
        key = (name, date, str(ws.cell(r, 3).value or ""))
        g = groups.setdefault(key, {"name": name, "date": date, "sfzh": key[2],
                                    "rows": [], "qtys": [], "amts": []})
        g["rows"].append(r)
        g["qtys"].append(ws.cell(r, 24).value)
        g["amts"].append(ws.cell(r, 36).value)
    visits = sorted(groups.values(), key=lambda v: (v["date"], v["rows"][0]))
    print(f"待处理就诊 {len(visits)} 个（{visits[0]['date']} ~ {visits[-1]['date']}）")
    return visits


def sfzh_match(full, masked):
    if not masked or not full or "*" not in masked:
        return True
    return full[:6] == masked[:6] and full[-6:] == masked[-6:]


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def main():
    os.makedirs(SHOT_DIR, exist_ok=True)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    visits = read_visits()
    cache = load_json(CACHE_FILE, {})
    todo = [v for v in visits if f"{v['name']}|{v['date']}|{v['sfzh']}" not in cache]
    if limit:
        todo = todo[:limit]
    print(f"本次处理 {len(todo)} 个（已缓存 {len(visits) - len(todo) if not limit else '?'} 个）")

    results = load_json(DECISIONS_FILE, {})
    if not todo:
        print("无待处理，直接输出")
    else:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            page = None
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if "172.17.8.14:8780" in pg.url:
                        page = pg
                        break
                if page:
                    break
            if not page:
                print("错误：未找到社康系统页面")
                return
            page.bring_to_front()
            close_preview()  # 清场

            # 确保全院模式
            run_step(page, "() => {" + PANEL_JS + r"""
                const pel = panelEl();
                if (!pel) return false;
                const r1 = Array.from(pel.querySelectorAll('input[type="radio"][name="jzls"]'))
                    .find(r => r.value === '1' && onScreen(r));
                if (!r1) return false;
                if (!r1.checked) r1.click();
                return true;
            }""", "选择全院", quiet=True)

            # 一次性搜整个区间
            run_step(page, "() => {" + PANEL_JS + f"""
                const pel = panelEl();
                if (!pel) return false;
                const setDate = (id, val) => {{
                    const el = pel.querySelector('#' + id);
                    if (!el) return false;
                    const c = Ext.getCmp(el.id);
                    if (c && c.setValue) {{ c.setValue(val); return c.getRawValue() === val; }}
                    return false;
                }};
                return setDate('startDate', '{DATE_START}') && setDate('endDate', '{DATE_END}');
            }}""", f"填写挂号时间 {DATE_START}~{DATE_END}")
            if not search_and_wait(page, "搜索整个区间", timeout=60):
                print("搜索失败，终止")
                return
            r = page.evaluate(SCRAPE_DAY_JS)
            if not r or "rows" not in r or len(r["rows"]) != r["total"]:
                print(f"爬取失败: {r.get('error') if r else '无返回'}")
                return
            grid_rows = r["rows"]
            print(f"区间内全院挂号共 {len(grid_rows)} 条")
            by_name = {}
            for rec in grid_rows:
                by_name.setdefault(str(rec.get("BRXM") or "").strip(), []).append(rec)

            for vi, v in enumerate(todo, 1):
                key = f"{v['name']}|{v['date']}|{v['sfzh']}"
                rows_tag = f"{v['rows'][0]}-{v['rows'][-1]}"
                print(f"[{vi}/{len(todo)}] {v['name']} {v['date']} 行{rows_tag}")
                cands = [rec for rec in by_name.get(v["name"], [])
                         if str(rec.get("GHSJ") or "")[:10] == v["date"]
                         and sfzh_match(v["sfzh"], str(rec.get("SFZH") or ""))]
                entry = None
                if not cands:
                    entry = {"status": "review", "reason": "就诊历史记录中未找到当天挂号",
                             "doctor": "", "zjje": "", "jfje": ""}
                else:
                    paid = [c for c in cands
                            if abs((c.get("ZJJE") or 0) - (c.get("JFJE") or 0)) < 0.005]
                    if not paid:
                        c0 = cands[0]
                        entry = {"status": "review",
                                 "reason": f"收费不一致 总金额{c0.get('ZJJE')}≠缴费{c0.get('JFJE')}",
                                 "doctor": c0.get("YSDM_text"), "zjje": c0.get("ZJJE"),
                                 "jfje": c0.get("JFJE")}
                    else:
                        # 逐个已缴清挂号看病历，找含隔物灸法的那个
                        for cand in paid:
                            ok = page.evaluate(SELECT_ROW_JS, cand["GHXH"])
                            if not ok:
                                continue
                            time.sleep(1)
                            close_preview()
                            if not run_click(page, MARK_PRINT_BTN_JS, "打印病历", timeout=8, quiet=True):
                                continue
                            if not wait_preview(12):
                                print("  预览未弹出")
                                continue
                            time.sleep(1.5)
                            try:
                                text = page.evaluate(RECORD_TEXT_JS) or ""
                            except Exception:
                                text = ""
                            if "隔物灸法" not in text:
                                close_preview()
                                continue  # 这个挂号没有隔物灸，试下一个
                            shots = screenshot_record(v["name"], v["date"], rows_tag)
                            close_preview()
                            status, tpl, note = classify(text)
                            entry = {"status": status, "col42": tpl, "note": note,
                                     "doctor": cand.get("YSDM_text"),
                                     "zjje": cand.get("ZJJE"), "jfje": cand.get("JFJE"),
                                     "shots": shots,
                                     "reason": "" if status != "review" else note}
                            break
                        if entry is None:
                            c0 = cands[0]
                            entry = {"status": "review",
                                     "reason": "已缴清挂号中未找到隔物灸医嘱（或病历读取失败）",
                                     "doctor": c0.get("YSDM_text"), "zjje": c0.get("ZJJE"),
                                     "jfje": c0.get("JFJE")}
                entry.update({"name": v["name"], "date": v["date"], "sfzh": v["sfzh"],
                              "rows": v["rows"], "qtys": [str(q) for q in v["qtys"]],
                              "amts": [str(a) for a in v["amts"]]})
                cache[key] = entry
                results[key] = entry
                save_json(CACHE_FILE, cache)
                save_json(DECISIONS_FILE, results)
                print(f"  → {entry['status']}: {entry.get('note') or entry.get('reason')}")

    write_outputs(list(results.values()))


def write_outputs(entries):
    """写 待人工审核.xlsx（含自动填写明细 sheet），并把 fill/wgui 结果写回原表。"""
    from openpyxl import Workbook
    reviews = [e for e in entries if e["status"] == "review"]
    fills = [e for e in entries if e["status"] in ("fill", "wgui")]
    wb = Workbook()
    ws = wb.active
    ws.title = "待人工审核"
    ws.append(["行号", "姓名", "身份证号", "费用日期", "就诊医生", "收费总金额", "缴费金额", "原因"])
    for e in sorted(reviews, key=lambda x: x["rows"][0]):
        ws.append([f"{e['rows'][0]}-{e['rows'][-1]}", e["name"], e["sfzh"], e["date"],
                   e.get("doctor") or "", e.get("zjje") or "", e.get("jfje") or "",
                   e.get("reason") or ""])
    ws2 = wb.create_sheet("自动填写明细")
    ws2.append(["行号", "姓名", "费用日期", "就诊医生", "判定", "col42填写内容", "依据摘录", "截图"])
    for e in sorted(fills, key=lambda x: x["rows"][0]):
        ws2.append([f"{e['rows'][0]}-{e['rows'][-1]}", e["name"], e["date"],
                    e.get("doctor") or "", "不违规" if e["status"] == "fill" else "违规(认可违规)",
                    e.get("col42") or "", e.get("note") or "", ",".join(e.get("shots") or [])])
    out = os.path.join(DESKTOP, "隔物灸_待人工审核.xlsx")
    wb.save(out)
    print(f"\n待审核 {len(reviews)} 个 / 自动填写 {len(fills)} 个 → {out}")

    # 写回原表（WPS 打开时跳过，避免损坏）
    if any(f.startswith("~$") and "隔物灸" in f for f in os.listdir(DESKTOP)):
        print("原表正在 WPS 中打开，请先关闭再重跑本脚本完成写回（数据已缓存，重跑很快）")
        return
    write_back(fills)


def write_back(fills):
    """zip 级定点改写原表 col39~42，保留 WPS 嵌入图片等其他一切内容。"""
    import shutil
    import zipfile
    from xml.sax.saxutils import escape
    backup = os.path.join(BASE, "_backup_记账明细表.xlsx")
    if not os.path.exists(backup):
        shutil.copy2(SRC_XLSX, backup)
        print(f"原表已备份到 {backup}")

    with zipfile.ZipFile(SRC_XLSX) as z:
        names = z.namelist()
        # 找"明细"sheet 对应的 xml
        wbxml = z.read("xl/workbook.xml").decode("utf-8")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        m = re.search(r'<sheet[^>]*name="明细"[^>]*r:id="(rId\d+)"', wbxml)
        rid = m.group(1)
        m2 = re.search(r'<Relationship[^>]*Id="' + rid + r'"[^>]*Target="([^"]+)"', rels)
        sheet_path = "xl/" + m2.group(1).lstrip("/")
        sheet = z.read(sheet_path).decode("utf-8")
        items = {n: z.read(n) for n in names}

    # 组织每行要写的内容: {row: {col_letter: (kind, value)}}
    plan = {}
    for e in fills:
        if e["status"] == "fill":
            for r in e["rows"]:
                plan.setdefault(r, {})["AM"] = ("s", "不违规")
                plan[r]["AP"] = ("s", e["col42"])
        else:  # wgui 认可违规
            for r, qty, amt in zip(e["rows"], e["qtys"], e["amts"]):
                plan.setdefault(r, {})["AM"] = ("s", "违规")
                plan[r]["AN"] = ("n", qty)
                plan[r]["AO"] = ("n", amt)
                plan[r]["AP"] = ("s", "认可违规")

    def col_idx(letters):
        n = 0
        for ch in letters:
            n = n * 26 + ord(ch) - 64
        return n

    def set_cell(rowxml, rowno, col, kind, value):
        ref = f"{col}{rowno}"
        if kind == "s":
            content = f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
        else:
            content = f'<c r="{ref}"><v>{value}</v></c>'
        m = re.search(r'<c r="' + ref + r'"[^>]*(/>|>.*?</c>)', rowxml)
        if m:
            return rowxml[:m.start()] + content + rowxml[m.end():]
        # 按列序插入
        target = col_idx(col)
        insert_at = len(rowxml) - len("</row>")
        for cm in re.finditer(r'<c r="([A-Z]+)\d+"', rowxml):
            if col_idx(cm.group(1)) > target:
                insert_at = cm.start()
                break
        return rowxml[:insert_at] + content + rowxml[insert_at:]

    for rowno, cells in plan.items():
        m = re.search(r'<row r="' + str(rowno) + r'"[^>]*>.*?</row>', sheet)
        if not m:
            print(f"  警告: 第{rowno}行未找到")
            continue
        rowxml = m.group(0)
        for col, (kind, value) in cells.items():
            rowxml = set_cell(rowxml, rowno, col, kind, value)
        sheet = sheet[:m.start()] + rowxml + sheet[m.end():]

    tmp = SRC_XLSX + ".tmp"
    items[sheet_path] = sheet.encode("utf-8")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, items[n])
    os.replace(tmp, SRC_XLSX)
    print(f"已写回原表 {len(plan)} 行（col39~42）: {SRC_XLSX}")


if __name__ == "__main__":
    main()
