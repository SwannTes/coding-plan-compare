#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""产后访视名单收集脚本（深圳市妇幼保健管理信息系统 fyweb）

流程：
1. 连接调试Chrome，找到 fyweb 页面（需已登录，登录页则提示手动登录）
2. 点击顶部"妇保" → 左侧"分娩与访视" → "产后访视"（已在该页则跳过）
3. 选择日期类型下拉为"分娩日期"（必须真实点开下拉选一次：显示值和查询实际
   生效值可能不一致，不选的话结果条数不对），再填分娩日期起止
   （命令行参数指定月份，如 `python collect_postpartum_visits.py 7`
   → 当年 7 月整月；留空默认本月）
4. 访视状态选"已访视" → 点"查询"
5. 翻页爬取结果表格的 保健号+姓名，按保健号去重
6. 左侧"孕期保健" → "孕妇建档"，逐个保健号查询，读取"建卡医院"列：
   非本院建档的产妇无需下一步操作，案例结束
7. 对本院建档的产妇：左侧"孕期保健" → "初检"，输入保健号查询，
   点操作列"国家打印"，扫描隐藏的打印表格 #initial-check-national-table
   检查产检项目漏项（复选/单选组一个都没勾、或值格为空都算漏项）
   点"国家打印"前会临时 stub 掉 Lodop 的 PREVIEW（不弹打印预览窗口，
   比弹出来再点关闭干净），查完全部产妇后恢复原样
   → 产后访视-已访视名单_YYYY-MM.xlsx（存桌面，含建卡医院、是否本院建档、产检漏项列）

关键机制（实测趟出来的）：
- fyweb 是 iView(view-design) Vue 应用，无 iframe，不用 ExtJS
- 表单项没有 name 属性，按 label 文本定位（.ivu-form-item-label）
- 日期输入框可直接真实键盘输入（点击 → 全选 → 输入 → Enter 提交）
- iView 下拉（.ivu-select）：点选择框展开 → 点 .ivu-select-dropdown 里的选项
- 结果表格无固定列副本，直接取唯一可见的 .ivu-table-body；
  姓名单元格内含隐藏副本（textContent 会出现两遍），按空白切分去重
- 同一产妇可能有多条访视记录（访视次数不同），输出按保健号去重
- 翻页用 .ivu-page-next，禁用（ivu-page-disabled）即到末页
- 孕妇建档逐个查询时，必须等结果行里的保健号变成目标值再读
  （查询返回前表格还停留在上一个人的结果）；"暂无数据"是 colspan 单格提示行
- "国家打印"走 Lodop 打印插件（预览窗口由插件弹出，不在 CDP 页面列表里），
  但打印数据在点击时已渲染进隐藏的 #initial-check-national-table；
  必须先用 JS el.click()（真实鼠标点击会让 Lodop 的 alert 崩掉 playwright 驱动），
  并提前覆写 window.alert 吞掉 Lodop 的"有窗口已打开"提示
- 表格行操作列是 fixed-right 固定列，可见副本在 .ivu-table-fixed-body 里
  （不是 .ivu-table-body），主表同名单元格是隐藏的——所以操作用 JS click 最稳
- 漏项扫描规则：标签格 = text-align:center 且无 input；复选/单选按"子标签:"分组，
  组内无任何勾选算漏项；纯文本值格去掉子标签后为空算漏项；
  转诊=无 时 转诊原因/机构及科室合法留空不算漏项
- 依赖 openpyxl 写 Excel（pip install --user openpyxl）
- 失败的步骤只警告不中断，脚本末尾统一汇总，方便人工补操作
"""

from playwright.sync_api import sync_playwright
from datetime import datetime
import calendar
import json
import os
import re
import sys
import time

FAILED_STEPS = []

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

# 本院建卡医院名称（孕妇建档结果"建卡医院"列与它完全一致才算本院建档）；
# 留空则不判定，只记录建卡医院
OWN_HOSPITAL = "深圳市龙岗区人民医院"

BASE_JS = r"""
    const onScreen = el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        let a = el;
        while (a && a !== document.body) {
            const st = window.getComputedStyle(a);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            a = a.parentElement;
        }
        return true;
    };
    // 按 label 文本找表单项（控件没有 name 属性）
    const formItem = label => Array.from(document.querySelectorAll('.ivu-form-item'))
        .find(fi => (fi.querySelector('.ivu-form-item-label')?.textContent || '').trim() === label
                    && onScreen(fi));
    // 结果表格主体：唯一可见的 .ivu-table-body（弹窗里的表格不可见）
    const tableBody = () => Array.from(document.querySelectorAll('.ivu-table-body'))
        .find(b => onScreen(b));
"""


def run_step(page, js, desc, timeout=10, quiet=False, record=True):
    """轮询执行JS（JS返回真值表示成功），直到成功或超时。成功后等1秒给系统反应时间。"""
    deadline = time.time() + timeout
    while True:
        try:
            ok = page.evaluate(js)
        except Exception:
            ok = False
        if ok:
            if not quiet:
                print(f"  [成功] {desc}")
            time.sleep(1)
            return True
        if time.time() >= deadline:
            if not quiet:
                print(f"  [超时] {desc} —— 未找到目标，请手动处理")
            if record:
                FAILED_STEPS.append(desc)
            return False
        time.sleep(0.3)


def run_click(page, locate_js, desc, verify_js=None, timeout=10, quiet=False, record=True):
    """locate_js 找到目标时打 data-kimi-click="1" 标记并返回 true，
    点击由 Playwright locator 完成（真实鼠标事件，自动滚动入视口、等待稳定）。"""
    clear_mark_js = ("() => document.querySelectorAll('[data-kimi-click]')"
                     ".forEach(e => e.removeAttribute('data-kimi-click'))")
    deadline = time.time() + timeout
    while True:
        try:
            page.evaluate(clear_mark_js)
            ok = page.evaluate(locate_js)
        except Exception:
            ok = False
        if ok:
            clicked = False
            try:
                page.locator('[data-kimi-click="1"]').first.click(timeout=3000)
                clicked = True
            except Exception:
                pass
            try:
                page.evaluate(clear_mark_js)
            except Exception:
                pass
            if clicked:
                if verify_js is None:
                    if not quiet:
                        print(f"  [成功] {desc}")
                    time.sleep(1)
                    return True
                time.sleep(0.5)
                try:
                    if page.evaluate(verify_js):
                        if not quiet:
                            print(f"  [成功] {desc}")
                        time.sleep(1)
                        return True
                except Exception:
                    pass
        if time.time() >= deadline:
            if not quiet:
                print(f"  [超时] {desc} —— 未找到目标，请手动处理")
            if record:
                FAILED_STEPS.append(desc)
            return False
        time.sleep(0.3)


def locate(body):
    """把定位语句包成 () => { ... } 函数，并注入 onScreen 助手。"""
    return "() => {" + BASE_JS + body + "\n}"


def month_range(month_arg=""):
    """返回起止日期 ('yyyy-mm-01', 'yyyy-mm-月末')。
    month_arg 为空：本月；为 1-12：当年该月，
    月份大于当前月份时取上一年（如 1 月查去年 12 月）。"""
    now = datetime.now()
    if month_arg:
        month = int(month_arg)
        if not 1 <= month <= 12:
            raise ValueError(f"月份必须是 1-12，收到: {month_arg!r}")
        year = now.year - 1 if month > now.month else now.year
    else:
        year, month = now.year, now.month
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"


def clean_cell(text):
    """单元格文本去重清洗：姓名格含隐藏副本（'黄敏霜\\n黄敏霜'），按空白切分取去重后的值。"""
    parts = [s for s in re.split(r"\s+", text or "") if s]
    seen = []
    for s in parts:
        if s not in seen:
            seen.append(s)
    return " ".join(seen)


def set_date(page, placeholder, value, desc):
    """分娩日期输入框（placeholder=开始/结束）：真实键盘输入后回读校验。"""
    if not run_click(page, locate(f"""
        const inp = Array.from(document.querySelectorAll('input[placeholder={json.dumps(placeholder)}]'))
            .find(onScreen);
        if (!inp) return false;
        inp.setAttribute('data-kimi-click', '1');
        return true;
    """), desc + "-聚焦", timeout=10, quiet=True):
        print(f"  [失败] {desc} —— 输入框定位失败")
        FAILED_STEPS.append(desc)
        return False
    page.keyboard.press("Control+a")
    page.keyboard.type(value, delay=30)
    page.keyboard.press("Enter")
    time.sleep(0.5)
    ok = run_step(page, locate(f"""
        const inp = Array.from(document.querySelectorAll('input[placeholder={json.dumps(placeholder)}]'))
            .find(onScreen);
        return inp ? inp.value === {json.dumps(value)} : false;
    """), desc, timeout=5, quiet=True)
    if ok:
        print(f"  [成功] {desc}")
    else:
        print(f"  [失败] {desc}")
        FAILED_STEPS.append(desc)
    return ok


def get_select_value(page, label):
    """读 iView 单选下拉当前显示文本。"""
    try:
        return page.evaluate(locate(f"""
            const fi = formItem({json.dumps(label)});
            const sv = fi && fi.querySelector('.ivu-select-selected-value');
            return sv ? sv.textContent.trim() : null;
        """))
    except Exception:
        return None


def pick_select(page, label, option_text, desc):
    """iView 下拉：点选择框展开 → 点下拉层里的选项 → 回读校验。已是目标值则跳过。"""
    if get_select_value(page, label) == option_text:
        print(f"  [跳过] {desc}（已是 {option_text}）")
        return True
    for attempt in range(3):
        if run_click(page, locate(f"""
            const fi = formItem({json.dumps(label)});
            const sel = fi && fi.querySelector('.ivu-select');
            if (!sel) return false;
            sel.setAttribute('data-kimi-click', '1');
            return true;
        """), desc + "-开下拉", timeout=5, quiet=True, record=False):
            time.sleep(0.8)
            run_click(page, locate(f"""
                const item = Array.from(document.querySelectorAll('.ivu-select-dropdown .ivu-select-item'))
                    .find(i => i.textContent.trim() === {json.dumps(option_text)} && onScreen(i));
                if (!item) return false;
                item.setAttribute('data-kimi-click', '1');
                return true;
            """), desc + "-选项", timeout=4, quiet=True, record=False)
            time.sleep(0.5)
            if get_select_value(page, label) == option_text:
                print(f"  [成功] {desc}")
                return True
        time.sleep(0.5)
    print(f"  [失败] {desc} —— 3 次尝试后仍未成功，请手动处理")
    FAILED_STEPS.append(desc)
    return False


def pick_date_type(page, option_text, desc):
    """产后访视的日期类型下拉（分娩日期/安排访视日期…）：无 label，
    和 开始/结束 日期输入框在同一个表单项里。必须真实点开选一次——
    下拉的显示值和查询实际生效值可能不一致，不选结果条数不对。"""
    OPEN_JS = locate("""
        const startI = Array.from(document.querySelectorAll('input[placeholder="开始"]')).find(onScreen);
        const fi = startI && startI.closest('.ivu-form-item');
        const sel = fi && fi.querySelector('.ivu-select');
        if (!sel) return false;
        sel.setAttribute('data-kimi-click', '1');
        return true;
    """)
    VERIFY_JS = locate(f"""
        const startI = Array.from(document.querySelectorAll('input[placeholder="开始"]')).find(onScreen);
        const fi = startI && startI.closest('.ivu-form-item');
        const sv = fi && fi.querySelector('.ivu-select-selected-value');
        return sv ? sv.textContent.trim() === {json.dumps(option_text)} : false;
    """)
    for attempt in range(3):
        if run_click(page, OPEN_JS, desc + "-开下拉", timeout=5, quiet=True, record=False):
            time.sleep(0.8)
            run_click(page, locate(f"""
                const item = Array.from(document.querySelectorAll('.ivu-select-dropdown .ivu-select-item'))
                    .find(i => i.textContent.trim() === {json.dumps(option_text)} && onScreen(i));
                if (!item) return false;
                item.setAttribute('data-kimi-click', '1');
                return true;
            """), desc + "-选项", timeout=4, quiet=True, record=False)
            time.sleep(0.5)
            if run_step(page, VERIFY_JS, desc, timeout=3, quiet=True, record=False):
                print(f"  [成功] {desc}")
                return True
        time.sleep(0.5)
    print(f"  [失败] {desc} —— 3 次尝试后仍未成功，请手动处理")
    FAILED_STEPS.append(desc)
    return False


def lodop_stub(page):
    """临时 stub 掉 Lodop 打印插件：点"国家打印"不弹预览窗口（数据照常渲染进
    隐藏表格）。查完全部产妇后用 lodop_restore 恢复，避免影响人工打印。"""
    page.evaluate("""() => {
        if (window.__lodopSaved) return;
        window.__lodopSaved = true;
        window.__origAlert = window.alert;
        window.alert = m => { (window.__log = window.__log || []).push(String(m)); };
        for (const name of ['LODOP', 'CLODOP']) {
            const L = window[name];
            if (L && typeof L === 'object') {
                window['__orig_' + name + '_PREVIEW'] = L.PREVIEW;
                window['__orig_' + name + '_ADD'] = L.ADD_PRINT_HTM;
                L.PREVIEW = () => 0;
                L.ADD_PRINT_HTM = () => {};
            }
        }
    }""")


def lodop_restore(page):
    """恢复 Lodop 和 alert 原样（见 lodop_stub）。"""
    try:
        page.evaluate("""() => {
            if (!window.__lodopSaved) return;
            window.__lodopSaved = false;
            if (window.__origAlert) window.alert = window.__origAlert;
            for (const name of ['LODOP', 'CLODOP']) {
                const L = window[name];
                if (L && window['__orig_' + name + '_PREVIEW']) {
                    L.PREVIEW = window['__orig_' + name + '_PREVIEW'];
                    L.ADD_PRINT_HTM = window['__orig_' + name + '_ADD'];
                }
            }
        }""")
    except Exception:
        pass


def wait_table_loaded(page, desc, timeout=30):
    """等页面就绪：等到结果表格出现即可。加载层（.ivu-spin-fix）可能残留卡住，
    只额外等 5 秒，等不到只警告不算失败。"""
    ok = run_step(page, locate("return !!tableBody();"), desc, timeout=timeout, quiet=True)
    if not ok:
        print(f"  [超时] {desc} —— 页面未就绪，请手动处理")
        FAILED_STEPS.append(desc)
        return False
    run_step(page, locate("""
        return !Array.from(document.querySelectorAll('.ivu-spin-fix')).find(onScreen);
    """), desc + "-加载层消失", timeout=5, quiet=True, record=False)
    print(f"  [成功] {desc}")
    return True


def scrape_all(page, desc):
    """翻页爬取结果表格的 保健号+姓名。"""
    rows = []
    for page_no in range(1, 100):
        try:
            r = page.evaluate(locate(r"""
                const tb = tableBody();
                if (!tb) return {error: 'no table'};
                const headTb = Array.from(document.querySelectorAll('.ivu-table-header'))
                    .find(h => onScreen(h));
                const heads = headTb
                    ? Array.from(headTb.querySelectorAll('th')).map(th => th.textContent.trim())
                    : [];
                const data = Array.from(tb.querySelectorAll('tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td')).map(td => td.textContent));
                const nextBtn = document.querySelector('.ivu-page-next');
                return {heads, rows: data,
                        nextDisabled: !nextBtn || nextBtn.classList.contains('ivu-page-disabled')};
            """))
        except Exception as e:
            print(f"  [失败] {desc}：爬取异常 {e}")
            FAILED_STEPS.append(desc)
            return None
        if not r or "rows" not in r:
            print(f"  [失败] {desc}：{r.get('error') if r else '无返回'}")
            FAILED_STEPS.append(desc)
            return None
        try:
            i_bjh = r["heads"].index("保健号")
            i_xm = r["heads"].index("姓名")
        except ValueError:
            print(f"  [失败] {desc}：表头缺少 保健号/姓名 列，实际表头 {r['heads']}")
            FAILED_STEPS.append(desc)
            return None
        n = 0
        for row in r["rows"]:
            if len(row) > max(i_bjh, i_xm):
                rows.append((clean_cell(row[i_bjh]), clean_cell(row[i_xm])))
                n += 1
        print(f"  第{page_no}页：{n} 行")
        if r["nextDisabled"]:
            break
        if not run_click(page, locate("""
            const btn = document.querySelector('.ivu-page-next');
            if (!btn || btn.classList.contains('ivu-page-disabled')) return false;
            btn.setAttribute('data-kimi-click', '1');
            return true;
        """), f"翻页到第{page_no + 1}页", timeout=5, quiet=True, record=False):
            break
        wait_table_loaded(page, f"第{page_no + 1}页加载", timeout=15)
    return rows


def goto_menu(page, submenu_title, item_text, route_seg, step_no):
    """左侧菜单导航：展开子菜单（已展开则跳过，避免收起）→ 点菜单项 → 校验路由最后一段。"""
    # 菜单项已可见说明子菜单已展开，直接点；否则先点子菜单标题展开
    if not run_step(page, locate(f"""
        const item = Array.from(document.querySelectorAll('.ivu-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === {json.dumps(item_text)} && onScreen(li));
        return !!item;
    """), f"{item_text}菜单项已可见", timeout=3, quiet=True, record=False):
        run_click(page, locate(f"""
            const sub = Array.from(document.querySelectorAll('.ivu-menu-submenu-title'))
                .find(d => d.textContent.trim() === {json.dumps(submenu_title)} && onScreen(d));
            if (!sub) return false;
            sub.setAttribute('data-kimi-click', '1');
            return true;
        """), f"展开{submenu_title}", timeout=15)

    ok = run_click(page, locate(f"""
        const item = Array.from(document.querySelectorAll('.ivu-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === {json.dumps(item_text)} && onScreen(li));
        if (!item) return false;
        item.setAttribute('data-kimi-click', '1');
        return true;
    """), f"点击{item_text}",
        verify_js=f"() => location.hash.split('/').pop() === {json.dumps(route_seg)}",
        timeout=15)
    if ok:
        wait_table_loaded(page, f"{item_text}页面加载", timeout=15)
    return ok


def query_hospital(page, bjh, desc):
    """孕妇建档页查一个保健号，返回建卡医院；查无建档记录返回 ''，失败返回 None。"""
    # 1. 输入保健号（真实键盘输入）
    if not run_click(page, locate("""
        const fi = formItem('保健号');
        const inp = fi && fi.querySelector('input[type="text"]');
        if (!inp || !onScreen(inp)) return false;
        inp.setAttribute('data-kimi-click', '1');
        return true;
    """), desc + "-聚焦", timeout=10, quiet=True, record=False):
        print(f"  [失败] {desc} —— 保健号输入框定位失败")
        FAILED_STEPS.append(desc)
        return None
    page.keyboard.press("Control+a")
    page.keyboard.type(bjh, delay=30)
    time.sleep(0.3)
    # 2. 点查询
    if not run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button.ivu-btn'))
            .find(b => b.textContent.trim() === '查询' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), desc + "-查询", timeout=10, quiet=True, record=False):
        print(f"  [失败] {desc} —— 查询按钮点击失败")
        FAILED_STEPS.append(desc)
        return None
    # 3. 等结果行保健号变成目标值（返回前表格停留在上一个人的结果），或出现"暂无数据"
    hospital = None
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            r = page.evaluate(locate(f"""
                const tb = tableBody();
                if (!tb) return null;
                const spin = Array.from(document.querySelectorAll('.ivu-spin-fix')).find(onScreen);
                if (spin) return null;
                const headTb = Array.from(document.querySelectorAll('.ivu-table-header')).find(onScreen);
                if (!headTb) return null;
                const heads = Array.from(headTb.querySelectorAll('th')).map(th => th.textContent.trim());
                const iBjh = heads.indexOf('保健号'), iJk = heads.indexOf('建卡医院');
                if (iBjh < 0 || iJk < 0) return {{error: '表头缺列: ' + heads.join(',')}};
                const rows = Array.from(tb.querySelectorAll('tr'))
                    .map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.textContent))
                    // "暂无数据"是单列 colspan 提示行，不算数据行
                    .filter(tds => tds.length > Math.max(iBjh, iJk));
                if (!rows.length) return {{empty: true}};  // 暂无数据
                for (const tds of rows) {{
                    const cell = (tds[iBjh] || '').replace(/\\s+/g, '');
                    if (cell === {json.dumps(bjh)}) return {{hospital: tds[iJk] || ''}};
                }}
                return null;  // 还是上一个人的结果，继续等
            """))
        except Exception:
            r = None
        if r:
            if "error" in r:
                print(f"  [失败] {desc} —— {r['error']}")
                FAILED_STEPS.append(desc)
                return None
            if r.get("empty"):
                return ""
            return clean_cell(r["hospital"])
        time.sleep(0.3)
    print(f"  [超时] {desc} —— 结果未刷新，请手动处理")
    FAILED_STEPS.append(desc)
    return None


# 漏项扫描：在隐藏打印表格 #initial-check-national-table 上跑
# 规则：标签格 = text-align:center 且无 input；复选/单选按"子标签:"分组，组内无勾选=漏项；
# 纯文本值格（紧跟标签格）去掉子标签后为空=漏项；转诊=无 时 原因/机构及科室合法留空
SCAN_JS = r"""() => {
    const root = document.getElementById('initial-check-national-table');
    if (!root) return {error: 'no table'};
    const missing = [];
    let referralNone = false;  // 转诊=无 时，转诊原因/机构及科室合法留空
    const isCenter = td => /center/.test((td.style && td.style.textAlign) || '');
    const stripSublabels = s => s.replace(/[^\s:：]{1,12}[:：]/g, '').replace(/\s+/g, '');

    // rowspan 标签继承（妇科检查跨3行、辅助检查跨多行，子行没有自己的标签格）
    let inherited = [];  // [{left, label}]
    for (const tr of root.querySelectorAll('tr')) {
        const tds = Array.from(tr.querySelectorAll('td'));
        const rowLabelTd = tds.find(td => isCenter(td) && !td.querySelector('input'));
        const rowLabel = rowLabelTd ? rowLabelTd.textContent.trim()
            : (inherited.find(i => i.left > 0) || {}).label || '';
        tds.forEach(td => {
            const rs = parseInt(td.getAttribute('rowspan') || '1');
            if (rs > 1 && isCenter(td)) inherited.push({left: rs - 1, label: td.textContent.trim()});
        });

        for (let i = 0; i < tds.length; i++) {
            const td = tds[i];
            if (isCenter(td) && !td.querySelector('input')) continue;  // 标签格本身
            const inputs = Array.from(td.querySelectorAll('input'));
            if (!inputs.length) {
                // 纯文本值格：只检查紧跟标签格之后的（表头姓名/编号等跨列格不算）
                const prev = tds[i - 1];
                if (!prev || !isCenter(prev) || prev.querySelector('input')) continue;
                const label = prev.textContent.trim();
                if (label && !stripSublabels(td.textContent)) missing.push(label);
            } else {
                // 输入组：按"子标签:"分组（外阴:/心脏: 等），每组至少勾一个才算填写
                let curLabel = null;
                let groups = [];
                const pushGroup = () => { if (curLabel) groups.push(curLabel); };
                const walk = el => {
                    for (const child of el.childNodes) {
                        if (child.nodeType === Node.TEXT_NODE) {
                            const m = child.textContent.trim().match(/([^\s:：]{1,12})[:：]/);
                            if (m) { pushGroup(); curLabel = {label: m[1], anyChecked: false}; }
                        } else if (child.nodeType === Node.ELEMENT_NODE) {
                            if (child.tagName === 'INPUT') {
                                if (!curLabel) curLabel = {label: '', anyChecked: false};
                                if (child.checked) {
                                    curLabel.anyChecked = true;
                                    const ns = child.nextElementSibling;
                                    if (ns) curLabel.selectedText = ns.textContent.trim();
                                }
                            } else {
                                walk(child);
                            }
                        }
                    }
                };
                walk(td);
                pushGroup();
                for (const g of groups) {
                    if (g.label === '转诊' && g.selectedText === '无') referralNone = true;
                    if (!g.anyChecked) {
                        const label = g.label
                            ? (rowLabel && rowLabel !== g.label ? rowLabel + '-' + g.label : g.label)
                            : rowLabel || '(未命名组)';
                        missing.push(label);
                    }
                }
            }
        }
        inherited.forEach(i => i.left--);
    }
    const result = missing.filter(m => !(referralNone && /原因|机构及科室/.test(m)));
    return {missing: [...new Set(result)]};
}"""


def query_initial_check(page, bjh, desc):
    """初检页查一个保健号的产检漏项：查询 → 点国家打印 → 扫描隐藏打印表格。
    返回漏项列表（[] = 无漏项）；查无初检记录返回 '无初检记录'；失败返回 None。"""
    # 1. 输入保健号
    if not run_click(page, locate("""
        const fi = formItem('保健号');
        const inp = fi && fi.querySelector('input[type="text"]');
        if (!inp || !onScreen(inp)) return false;
        inp.setAttribute('data-kimi-click', '1');
        return true;
    """), desc + "-聚焦", timeout=10, quiet=True, record=False):
        print(f"  [失败] {desc} —— 保健号输入框定位失败")
        FAILED_STEPS.append(desc)
        return None
    page.keyboard.press("Control+a")
    page.keyboard.type(bjh, delay=30)
    time.sleep(0.3)
    # 2. 点查询，等结果行保健号变成目标值
    if not run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button.ivu-btn'))
            .find(b => b.textContent.trim() === '查询' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), desc + "-查询", timeout=10, quiet=True, record=False):
        print(f"  [失败] {desc} —— 查询按钮点击失败")
        FAILED_STEPS.append(desc)
        return None
    found = False
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            r = page.evaluate(locate(f"""
                const tb = tableBody();
                if (!tb) return null;
                const spin = Array.from(document.querySelectorAll('.ivu-spin-fix')).find(onScreen);
                if (spin) return null;
                const headTb = Array.from(document.querySelectorAll('.ivu-table-header')).find(onScreen);
                if (!headTb) return null;
                const heads = Array.from(headTb.querySelectorAll('th')).map(th => th.textContent.trim());
                const iBjh = heads.indexOf('保健号');
                if (iBjh < 0) return {{error: '表头缺保健号列'}};
                const rows = Array.from(tb.querySelectorAll('tr'))
                    .map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.textContent))
                    .filter(tds => tds.length > iBjh);
                if (!rows.length) return {{empty: true}};
                return {{found: rows.some(tds => (tds[iBjh] || '').replace(/\\s+/g, '') === {json.dumps(bjh)})}};
            """))
        except Exception:
            r = None
        if r:
            if "error" in r:
                print(f"  [失败] {desc} —— {r['error']}")
                FAILED_STEPS.append(desc)
                return None
            if r.get("empty"):
                return "无初检记录"
            if r.get("found"):
                found = True
                break
        time.sleep(0.3)
    if not found:
        print(f"  [超时] {desc} —— 查询结果未刷新")
        FAILED_STEPS.append(desc)
        return None

    # 3. 点"国家打印"。用 JS el.click()：真实鼠标点击会让 Lodop 的 alert 崩掉
    # playwright 驱动；window.alert 已在连接后全局覆写，这里再覆写一次兜底
    page.evaluate("() => { window.alert = m => { (window.__log = window.__log || []).push(String(m)); }; }")
    clicked = page.evaluate("""() => {
        const link = Array.from(document.querySelectorAll('span.op-button-default'))
            .find(e => e.textContent.trim() === '国家打印');
        if (!link) return false;
        link.click();
        return true;
    }""")
    if not clicked:
        print(f"  [失败] {desc} —— 国家打印链接未找到")
        FAILED_STEPS.append(desc)
        return None
    # 4. 等隐藏打印表格刷新成本人的数据（编号=保健号），再扫描漏项
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            ready = page.evaluate(f"""() => {{
                const el = document.getElementById('initial-check-national-table');
                return el ? el.innerText.includes({json.dumps(bjh)}) : false;
            }}""")
        except Exception:
            ready = False
        if ready:
            break
        time.sleep(0.3)
    else:
        print(f"  [超时] {desc} —— 打印数据未加载")
        FAILED_STEPS.append(desc)
        return None
    try:
        r = page.evaluate(SCAN_JS)
    except Exception as e:
        print(f"  [失败] {desc} —— 扫描异常 {e}")
        FAILED_STEPS.append(desc)
        return None
    if "error" in r:
        print(f"  [失败] {desc} —— {r['error']}")
        FAILED_STEPS.append(desc)
        return None
    return r["missing"]


def save_excel(rows, filename, desc):
    """把 (保健号, 姓名, 建卡医院, 是否本院建档, 产检漏项) 列表写成 xlsx 存到桌面。"""
    if rows is None:
        return
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "名单"
        ws.append(["保健号", "姓名", "建卡医院", "是否本院建档", "产检漏项"])
        for row in rows:
            ws.append(list(row))
        path = os.path.join(DESKTOP, filename)
        wb.save(path)
        print(f"  [成功] {desc}：{len(rows)} 条 → {path}")
    except Exception as e:
        print(f"  [失败] {desc}：写 Excel 出错 {e}")
        FAILED_STEPS.append(desc + "(写Excel)")


def collect():
    print("1. 开始启动...")
    with sync_playwright() as p:
        print("2. 尝试连接Chrome...")
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception:
            print("错误：连接失败。请先运行 启动调试Chrome并打开网址.py 并登录系统。")
            return
        print("3. 连接成功!")

        # 只按主机名匹配，不限端口（见过 8661 和 28661 两种端口）
        target_url = "10.130.20.249"
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if target_url in pg.url:
                    page = pg
                    break
            if page:
                break
        if not page:
            print(f"错误：未找到包含 {target_url} 的页面，请先打开 "
                  "https://10.130.20.249:28661/fyweb/#/login 并登录")
            return
        print(f"4. 当前页面标题: {page.title()}")
        if "/login" in page.url:
            print("错误：当前是登录页，请先手动登录后再运行脚本")
            return

        # ========== 1. 妇保 → 分娩与访视 → 产后访视 ==========
        # 注意路由组前缀也含 postpartumvisits（如 .../maternalinfo_woman_delivery），
        # 必须比 hash 最后一段，不能 includes
        if page.url.split("/")[-1] != "maternalinfo_woman_postpartumvisits":
            print("5. 点击妇保模块...")
            run_click(page, locate("""
                const item = Array.from(document.querySelectorAll('.main-menu .ivu-menu-item'))
                    .find(li => li.textContent.trim() === '妇保' && onScreen(li));
                if (!item) return false;
                item.setAttribute('data-kimi-click', '1');
                return true;
            """), "点击妇保模块", timeout=15)

            print("6. 进入 分娩与访视 → 产后访视...")
            goto_menu(page, "分娩与访视", "产后访视", "maternalinfo_woman_postpartumvisits", 6)
        else:
            print("5. 已在产后访视页面，跳过菜单点击")

        # ========== 2. 分娩日期：命令行参数指定月份，默认本月 ==========
        month_arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
        try:
            start_date, end_date = month_range(month_arg)
        except ValueError as e:
            print(f"参数错误：{e}")
            sys.exit(1)
        ym = start_date[:7]
        print(f"8. 选择日期类型=分娩日期，填写: {start_date} 至 {end_date} ...")
        # 下拉显示值和查询实际生效值可能不一致，必须真实点开选一次
        type_ok = pick_date_type(page, "分娩日期", "日期类型=分娩日期")
        date_ok = set_date(page, "开始", start_date, "开始日期")
        date_ok = set_date(page, "结束", end_date, "结束日期") and date_ok

        # ========== 3. 访视状态 = 已访视 ==========
        print("9. 选择访视状态=已访视...")
        status_ok = pick_select(page, "访视状态", "已访视", "访视状态=已访视")

        # ========== 4. 查询 ==========
        print("10. 点击查询...")
        query_ok = run_click(page, locate("""
            const btn = Array.from(document.querySelectorAll('button.ivu-btn'))
                .find(b => b.textContent.trim() === '查询' && onScreen(b));
            if (!btn) return false;
            btn.setAttribute('data-kimi-click', '1');
            return true;
        """), "点击查询", timeout=10)
        if query_ok:
            wait_table_loaded(page, "查询结果加载")

        # ========== 5. 翻页爬取 保健号+姓名 ==========
        print("11. 爬取结果...")
        rows = scrape_all(page, "爬取 产后访视名单") if date_ok and status_ok and query_ok and type_ok else None
        if rows is None:
            unique = []
        else:
            # 同一产妇可能有多条访视记录，按保健号去重
            seen = {}
            for bjh, xm in rows:
                seen.setdefault(bjh, xm)
            unique = sorted(seen.items())
            print(f"  访视记录 {len(rows)} 条，去重后产妇 {len(unique)} 人")

        # ========== 6. 孕期保健 → 孕妇建档：逐个查建卡医院 ==========
        # 非本院建档的产妇不需要下一步操作；查无建档记录也标出来人工核对
        results = []
        if unique:
            print("12. 进入 孕期保健 → 孕妇建档，逐个查建卡医院...")
            nav_ok = True
            if page.url.split("/")[-1] != "maternalinfo_woman_woman":
                nav_ok = goto_menu(page, "孕期保健", "孕妇建档", "maternalinfo_woman_woman", 12)
            if nav_ok:
                for bjh, xm in unique:
                    hospital = query_hospital(page, bjh, f"查建档 {xm}({bjh})")
                    if hospital is None:
                        results.append([bjh, xm, "", "查询失败", ""])
                    elif hospital == "":
                        print(f"  {xm}({bjh})：未查到建档记录")
                        results.append([bjh, xm, "", "未查到建档", ""])
                    else:
                        own = "是" if hospital == OWN_HOSPITAL else "否"
                        if not OWN_HOSPITAL:
                            own = "未配置本院名称"
                        print(f"  {xm}({bjh})：{hospital} → 本院建档:{own}")
                        results.append([bjh, xm, hospital, own, ""])
            else:
                results = [[bjh, xm, "", "未查询", ""] for bjh, xm in unique]

        # ========== 7. 本院建档的产妇：孕期保健 → 初检 → 国家打印，查产检漏项 ==========
        own_mothers = [r for r in results if r[3] == "是"]
        if own_mothers:
            print(f"13. 进入 孕期保健 → 初检，查 {len(own_mothers)} 位本院建档产妇的产检漏项...")
            nav_ok = True
            if page.url.split("/")[-1] != "maternalinfo_woman_initialcare":
                nav_ok = goto_menu(page, "孕期保健", "初检", "maternalinfo_woman_initialcare", 13)
            if nav_ok:
                # 点"国家打印"会触发 Lodop 弹预览窗，先 stub 掉，查完恢复
                lodop_stub(page)
                try:
                    for r in own_mothers:
                        bjh, xm = r[0], r[1]
                        missing = query_initial_check(page, bjh, f"查漏项 {xm}({bjh})")
                        if missing is None:
                            r[4] = "查询失败"
                        elif missing == "无初检记录":
                            print(f"  {xm}({bjh})：无初检记录")
                            r[4] = "无初检记录"
                        elif missing:
                            print(f"  {xm}({bjh})：漏项 → {'、'.join(missing)}")
                            r[4] = "、".join(missing)
                        else:
                            print(f"  {xm}({bjh})：无漏项")
                            r[4] = "无漏项"
                finally:
                    lodop_restore(page)
            else:
                for r in own_mothers:
                    r[4] = "未查询"

        if rows is not None:
            save_excel(results, f"产后访视-已访视名单_{ym}.xlsx", "已访视产妇名单")

        # ========== 汇总 ==========
        print("=" * 40)
        if FAILED_STEPS:
            print(f"完成，但有 {len(FAILED_STEPS)} 个步骤未成功，请手动检查：")
            for s in FAILED_STEPS:
                print(f"  - {s}")
        else:
            print("全部步骤执行成功！")


if __name__ == "__main__":
    collect()
