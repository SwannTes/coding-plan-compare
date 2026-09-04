#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""孕妇建档一览表收集 + 协同系统补录一体化脚本

阶段A（fyweb 深圳市妇幼保健管理信息系统）：
1. 报表 → 社康用表 → 孕妇建档一览表
2. 建档日期填入起止日期（命令行参数指定月份，如 `python collect_pregnancy_register.py 7`
   → 当年 7 月整月；留空默认本月1号至今天），点"查询"
3. 翻页遍历，逐行点操作列"显示"揭码（手机号码默认打码，揭码互斥，必须点一行读一行），
   收集 保健号/姓名/手机号码/末次月经/初检颜色/预产期/户籍类型

阶段B（udrhip 孕产妇协同管理信息系统，龙岗区全民健康信息平台 SSO 免登）：
4. 进入 妇保 → 产前跟踪 → 产前随访跟踪，清空"发现日期"筛选（否则按姓名查不全）
5. 逐个姓名查询：已在管（结果>0条）→ 跳过
6. 不在管的回 fyweb 补齐资料：
   - 孕期保健→孕妇建档：查保健号，点"显示"揭码身份证（出生日期由身份证第7-14位得出）
   - 孕期保健→初检：查保健号，点"国家打印"，读隐藏表格 #initial-check-national-table
     的 孕次/产次(阴道分娩+剖宫产次数之和)/预产期/丈夫手机
7. 回 udrhip 点"新增人员"，填写"新增待随访跟踪人员"表单并保存：
   发现日期=当天，发现方式=院部下转；出生日期/预产期/发现孕周由表单自动带出（禁用项）
8. 保存后复查该姓名已在管
   → 孕妇建档一览表_YYYY-MM-DD至YYYY-MM-DD.xlsx（存桌面，含 协同管理状态 列）

关键机制（实测趟出来的）：
fyweb（iView/View UI）：
- 报表子表点击后 URL 不变，按页面特征（建档日期 label）判断是否在页
- 建档页有三组 开始/结束 日期输入，填日期必须限定在"建档日期"表单项内
- 操作列是 fixed-right 固定列，可见按钮在 .ivu-table-fixed-right 的 .ivu-table-fixed-body
- "国家打印"走 Lodop 插件：点前 stub 掉 PREVIEW（不弹预览窗），并覆写 window.alert
  （Lodop 的 alert 会把 playwright 驱动弄崩）；打印数据渲染进隐藏表格，与 Lodop 无关
- "档案浏览/初检浏览"是自定义 .form-modal 弹窗，残留 DOM 会拦截点击且 Vue 已认为它
  关闭（点取消/X 无效），每次真实点击前直接 display:none 清掉
udrhip（Element UI）：
- 下拉层会动画/瞬关，选项用 JS click；开下拉用真实点击（有的 select 监听 mousedown，
  JS click 唤不起，兜底补发 mousedown）；开下拉前先点弹窗标题收掉残留下拉层
- 日期面板不能用 Esc 关（会把整个新增弹窗关掉），点弹窗标题收
- 保存后弹窗关闭有延迟，轮询放长到 20 秒
- 依赖 openpyxl 写 Excel（pip install --user openpyxl）
- 失败的步骤只警告不中断，脚本末尾统一汇总
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

FYWEB_HOST = "10.130.20.249"            # 妇幼保健管理信息系统（只按主机名匹配，不限端口：见过 8661 和 28661）
UDRHIP_MARK = "/udrhip/"                # 孕产妇协同管理信息系统
PORTAL_MARK = "172.17.9.215:8289"       # 龙岗区全民健康信息平台（站点导航）

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
    // 结果表格主体：可见且不在 fixed 固定列副本里的 .ivu-table-body
    const tableBody = () => Array.from(document.querySelectorAll('.ivu-table-body'))
        .find(b => onScreen(b) && !b.closest('.ivu-table-fixed, .ivu-table-fixed-right'));
"""

# udrhip 弹窗内的表单助手（Element UI）
UDRHIP_DLG_JS = r"""
    const dlg = () => Array.from(document.querySelectorAll('.el-dialog')).find(onScreen);
    const dlgFormItem = label => {
        const d = dlg();
        if (!d) return null;
        return Array.from(d.querySelectorAll('.el-form-item'))
            .find(fi => (fi.querySelector('.el-form-item__label')?.textContent || '')
                         .trim().replace('*', '') === label);
    };
"""


def run_step(page, js, desc, timeout=10, quiet=False, record=True):
    """轮询执行JS（JS返回真值表示成功），直到成功或超时。"""
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


def run_click(page, locate_js, desc, verify_js=None, timeout=10, quiet=False, record=True,
              fyweb=False):
    """locate_js 找到目标时打 data-kimi-click="1" 标记并返回 true，点击由 Playwright
    locator 完成（真实鼠标事件）。fyweb=True 时先清理残留的 .form-modal 弹窗。"""
    clear_mark_js = ("() => document.querySelectorAll('[data-kimi-click]')"
                     ".forEach(e => e.removeAttribute('data-kimi-click'))")
    deadline = time.time() + timeout
    while True:
        try:
            if fyweb:
                hide_stale_modals(page)
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
    return "() => {" + BASE_JS + body + "\n}"


def hide_stale_modals(page):
    """fyweb 的 .form-modal 残留弹窗（Vue 状态已关闭但 DOM 还在）会拦截点击，直接隐藏。"""
    try:
        page.evaluate("""() => {
            document.querySelectorAll('.form-modal').forEach(m => {
                if (m.getBoundingClientRect().width > 0) m.style.display = 'none';
            });
        }""")
    except Exception:
        pass


def date_range(month_arg=""):
    """返回起止日期。month_arg 为空：本月1号至今天；
    为 1-12：当年该月整月，月份大于当前月份时取上一年（如 1 月查去年 12 月）。"""
    now = datetime.now()
    if month_arg:
        month = int(month_arg)
        if not 1 <= month <= 12:
            raise ValueError(f"月份必须是 1-12，收到: {month_arg!r}")
        year = now.year - 1 if month > now.month else now.year
        last_day = calendar.monthrange(year, month)[1]
        return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"
    return f"{now.year}-{now.month:02d}-01", f"{now.year}-{now.month:02d}-{now.day:02d}"


def clean_cell(text):
    """单元格文本去重清洗：部分单元格含隐藏副本，按空白切分去重。"""
    parts = [s for s in re.split(r"\s+", text or "") if s]
    seen = []
    for s in parts:
        if s not in seen:
            seen.append(s)
    return " ".join(seen)


def wait_table_loaded(page, desc, timeout=30):
    """等页面/查询就绪：等到结果表格出现即可。加载层可能残留卡住，额外等 5 秒只警告。"""
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


def set_labeled_date(page, label, placeholder, value, desc):
    """指定 label 表单项里的日期输入框：真实键盘输入后回读校验。"""
    if not run_click(page, locate(f"""
        const fi = formItem({json.dumps(label)});
        if (!fi) return false;
        const inp = Array.from(fi.querySelectorAll('input[placeholder={json.dumps(placeholder)}]'))
            .find(onScreen);
        if (!inp) return false;
        inp.setAttribute('data-kimi-click', '1');
        return true;
    """), desc + "-聚焦", timeout=10, quiet=True, fyweb=True):
        print(f"  [失败] {desc} —— 输入框定位失败")
        FAILED_STEPS.append(desc)
        return False
    page.keyboard.press("Control+a")
    page.keyboard.type(value, delay=30)
    page.keyboard.press("Enter")
    time.sleep(0.5)
    ok = run_step(page, locate(f"""
        const fi = formItem({json.dumps(label)});
        if (!fi) return false;
        const inp = Array.from(fi.querySelectorAll('input[placeholder={json.dumps(placeholder)}]'))
            .find(onScreen);
        return inp ? inp.value === {json.dumps(value)} : false;
    """), desc, timeout=5, quiet=True)
    if ok:
        print(f"  [成功] {desc}")
    else:
        print(f"  [失败] {desc}")
        FAILED_STEPS.append(desc)
    return ok


# ==================== 阶段A：fyweb 建档一览表收集 ====================

def fyweb_goto_register(page):
    """报表 → 社康用表 → 孕妇建档一览表（报表子表 URL 不变，按 建档日期 表单项判断）。"""
    on_page = locate("return !!formItem('建档日期');")
    try:
        if page.evaluate(on_page):
            print("  已在孕妇建档一览表页面")
            return True
    except Exception:
        pass
    run_click(page, locate("""
        const item = Array.from(document.querySelectorAll('.main-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === '报表' && onScreen(li));
        if (!item) return false;
        item.setAttribute('data-kimi-click', '1');
        return true;
    """), "点击报表模块", timeout=15)
    if not run_step(page, locate("""
        const item = Array.from(document.querySelectorAll('.ivu-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === '孕妇建档一览表' && onScreen(li));
        return !!item;
    """), "一览表菜单项已可见", timeout=3, quiet=True, record=False):
        run_click(page, locate("""
            const sub = Array.from(document.querySelectorAll('.ivu-menu-submenu-title'))
                .find(d => d.textContent.trim() === '社康用表' && onScreen(d));
            if (!sub) return false;
            sub.setAttribute('data-kimi-click', '1');
            return true;
        """), "展开社康用表", timeout=15)
    ok = run_click(page, locate("""
        const item = Array.from(document.querySelectorAll('.ivu-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === '孕妇建档一览表' && onScreen(li));
        if (!item) return false;
        item.setAttribute('data-kimi-click', '1');
        return true;
    """), "点击孕妇建档一览表", verify_js=on_page, timeout=15)
    if ok:
        # 就绪标准：筛选表单（建档日期）或结果表格出现其一。首次进入还没点查询，
        # 页面可能不渲染结果表格，等表格会误报超时（2026-09-02 实测）
        if run_step(page, locate("return !!formItem('建档日期') || !!tableBody();"),
                    "孕妇建档一览表加载", timeout=15, quiet=True):
            run_step(page, locate("""
                return !Array.from(document.querySelectorAll('.ivu-spin-fix')).find(onScreen);
            """), "孕妇建档一览表加载-加载层消失", timeout=5, quiet=True, record=False)
            print("  [成功] 孕妇建档一览表加载")
        else:
            print("  [超时] 孕妇建档一览表加载 —— 页面未就绪，请手动处理")
            FAILED_STEPS.append("孕妇建档一览表加载")
    return ok


def scrape_register(page, desc):
    """翻页爬取建档一览表：逐行点"显示"揭码后读各列。"""
    heads = page.evaluate(locate(r"""
        const headTb = Array.from(document.querySelectorAll('.ivu-table-header'))
            .find(h => onScreen(h) && !h.closest('.ivu-table-fixed, .ivu-table-fixed-right'));
        return headTb ? Array.from(headTb.querySelectorAll('th')).map(th => th.textContent.trim()) : [];
    """))
    try:
        i_bjh = heads.index("保健号")
        i_xm = heads.index("孕产妇姓名")
        i_sj = heads.index("手机号码")
        i_mc = heads.index("末次月经")
        i_color = heads.index("初检颜色")
        i_edd = heads.index("预产期")
        i_hk = heads.index("户籍类型")
    except ValueError:
        print(f"  [失败] {desc}：表头缺列，实际表头 {heads}")
        FAILED_STEPS.append(desc)
        return None

    rows = []
    for page_no in range(1, 200):
        n = page.evaluate(locate(
            "const tb = tableBody(); return tb ? tb.querySelectorAll('tr').length : 0;"))
        if not n:
            print(f"  {desc}：本页无数据")
            break
        for i in range(n):
            # 点 fixed-right 固定列里第 i 行的"显示"（已揭码的行按钮是"隐藏"，直接读）
            clicked = page.evaluate(locate(f"""
                const fr = document.querySelector('.ivu-table-fixed-right');
                if (!fr) return 'no-fixed-right';
                const tr = fr.querySelectorAll('tbody tr')[{i}];
                if (!tr) return 'no-row';
                const link = Array.from(tr.querySelectorAll('span, a, button'))
                    .find(e => e.textContent.trim() === '显示');
                if (!link) return 'already-shown';
                link.click();
                return 'ok';
            """))
            if clicked not in ("ok", "already-shown"):
                print(f"  [失败] {desc} 第{i + 1}行 —— 显示按钮点击失败({clicked})")
                FAILED_STEPS.append(f"{desc} 第{i + 1}行")
                continue
            time.sleep(1)
            row = page.evaluate(locate(f"""
                const tb = tableBody();
                const tr = tb && tb.querySelectorAll('tr')[{i}];
                return tr ? Array.from(tr.querySelectorAll('td')).map(td => td.textContent) : null;
            """))
            if not row or len(row) <= max(i_bjh, i_xm, i_sj, i_mc, i_color, i_edd, i_hk):
                print(f"  [失败] {desc} 第{i + 1}行 —— 读取行数据失败")
                FAILED_STEPS.append(f"{desc} 第{i + 1}行")
                continue
            rows.append({
                "bjh": clean_cell(row[i_bjh]), "name": clean_cell(row[i_xm]),
                "phone": clean_cell(row[i_sj]), "lmp": clean_cell(row[i_mc]),
                "color": clean_cell(row[i_color]), "edd": clean_cell(row[i_edd]),
                "hukou": clean_cell(row[i_hk]),
            })
        print(f"  第{page_no}页：{n} 行")
        if not run_click(page, locate("""
            const btn = document.querySelector('.ivu-page-next');
            if (!btn || btn.classList.contains('ivu-page-disabled')) return false;
            btn.setAttribute('data-kimi-click', '1');
            return true;
        """), f"翻页到第{page_no + 1}页", timeout=5, quiet=True, record=False, fyweb=True):
            break
        wait_table_loaded(page, f"第{page_no + 1}页加载", timeout=15)
    return rows


# ==================== 阶段B-1：fyweb 补资料 ====================

def fyweb_goto_menu(page, submenu, item, route_tail, desc):
    """妇保版块内导航：顶部"妇保" → 子菜单 → 菜单项。"""
    if page.url.split("/")[-1] == route_tail:
        return True
    run_click(page, locate("""
        const item = Array.from(document.querySelectorAll('.main-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === '妇保' && onScreen(li));
        if (!item) return false;
        item.setAttribute('data-kimi-click', '1');
        return true;
    """), "点击妇保模块", timeout=15, quiet=True, record=False)
    if not run_step(page, locate(f"""
        const item = Array.from(document.querySelectorAll('.ivu-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === {json.dumps(item)} && onScreen(li));
        return !!item;
    """), f"{item}菜单项已可见", timeout=3, quiet=True, record=False):
        run_click(page, locate(f"""
            const sub = Array.from(document.querySelectorAll('.ivu-menu-submenu-title'))
                .find(d => d.textContent.trim() === {json.dumps(submenu)} && onScreen(d));
            if (!sub) return false;
            sub.setAttribute('data-kimi-click', '1');
            return true;
        """), f"展开{submenu}", timeout=15, quiet=True, record=False)
    ok = run_click(page, locate(f"""
        const item = Array.from(document.querySelectorAll('.ivu-menu .ivu-menu-item'))
            .find(li => li.textContent.trim() === {json.dumps(item)} && onScreen(li));
        if (!item) return false;
        item.setAttribute('data-kimi-click', '1');
        return true;
    """), f"点击{item}",
        verify_js=f"() => location.hash.split('/').pop() === {json.dumps(route_tail)}",
        timeout=15, quiet=True, record=False)
    time.sleep(1)
    return ok


def fyweb_query_bjh(page, bjh):
    """在当前 fyweb 列表页按保健号查询。"""
    if not run_click(page, locate("""
        const fi = formItem('保健号');
        const inp = fi && fi.querySelector('input[type="text"]');
        if (!inp) return false;
        inp.setAttribute('data-kimi-click', '1');
        return true;
    """), "保健号-聚焦", timeout=10, quiet=True, record=False, fyweb=True):
        return False
    page.keyboard.press("Control+a")
    page.keyboard.type(bjh, delay=30)
    if not run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button.ivu-btn'))
            .find(b => b.textContent.trim() === '查询' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), "查询", timeout=10, quiet=True, record=False, fyweb=True):
        return False
    time.sleep(2)
    return True


def fyweb_get_idcard(page, bjh):
    """孕妇建档页查保健号 → 点"显示"揭码 → 读身份证。"""
    if not fyweb_goto_menu(page, "孕期保健", "孕妇建档", "maternalinfo_woman_woman", "孕妇建档"):
        return None
    if not fyweb_query_bjh(page, bjh):
        return None
    # 点"显示"揭码（已是"隐藏"则跳过）；用 JS click，固定列副本问题不影响
    page.evaluate("() => {" + BASE_JS + """
        const link = Array.from(document.querySelectorAll('span.op-button-default'))
            .find(e => e.textContent.trim() === '显示');
        if (link) link.click();
    }""")
    time.sleep(2)
    return page.evaluate(locate(r"""
        const tb = tableBody();
        const headTb = Array.from(document.querySelectorAll('.ivu-table-header'))
            .find(h => onScreen(h) && !h.closest('.ivu-table-fixed, .ivu-table-fixed-right'));
        if (!tb || !headTb) return null;
        const heads = Array.from(headTb.querySelectorAll('th')).map(th => th.textContent.trim());
        const i = heads.indexOf('身份证');
        const tr = tb.querySelector('tr');
        if (i < 0 || !tr) return null;
        return (tr.querySelectorAll('td')[i] || {}).textContent || null;
    }"""))


def fyweb_get_firstcheck(page, bjh):
    """初检页查保健号 → 点"国家打印" → 读隐藏表格的 孕次/产次/预产期/丈夫手机。"""
    if not fyweb_goto_menu(page, "孕期保健", "初检", "maternalinfo_woman_initialcare", "初检"):
        return None
    if not fyweb_query_bjh(page, bjh):
        return None
    n = page.evaluate(locate("""
        const tb = tableBody();
        return tb ? tb.querySelectorAll('tr').length : 0;
    """))
    if not n:
        return None
    # stub Lodop（不弹预览窗）+ 覆写 alert（Lodop 的 alert 会崩 playwright 驱动）
    page.evaluate(r"""() => {
        window.alert = m => { (window.__log = window.__log || []).push(String(m)); };
        for (const name of ['LODOP', 'CLODOP']) {
            const L = window[name];
            if (L && typeof L === 'object') { L.PREVIEW = () => 0; L.ADD_PRINT_HTM = () => {}; }
        }
    }""")
    page.evaluate("""() => {
        const link = Array.from(document.querySelectorAll('span.op-button-default'))
            .find(e => e.textContent.trim() === '国家打印');
        if (link) link.click();
    }""")
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            ready = page.evaluate(
                "(b) => { const el = document.getElementById('initial-check-national-table');"
                " return el ? el.innerText.includes(b) : false; }", bjh)
        except Exception:
            ready = False
        if ready:
            break
        time.sleep(0.3)
    else:
        return None
    return page.evaluate(r"""() => {
        const root = document.getElementById('initial-check-national-table');
        if (!root) return null;
        const grab = label => {
            const tds = Array.from(root.querySelectorAll('td'));
            for (let i = 0; i < tds.length; i++) {
                if (tds[i].textContent.trim() === label && tds[i+1])
                    return tds[i+1].textContent.trim().replace(/\s+/g, ' ');
            }
            return null;
        };
        return {孕次: grab('孕次'), 产次: grab('产次'), 预产期: grab('预产期'),
                丈夫手机: grab('丈夫手机')};
    }""")


# ==================== 阶段B-2：udrhip 查在管 + 补录 ====================

def udrhip_goto_followup(page):
    """进入 妇保 → 产前跟踪 → 产前随访跟踪。"""
    if "PrenatalFollowUp" in page.url:
        return True
    # 产前跟踪是子菜单，产前随访跟踪是菜单项；子菜单收起时先展开
    if not run_step(page, locate("""
        const item = Array.from(document.querySelectorAll('li.ant-menu-item'))
            .find(li => li.textContent.trim() === '产前随访跟踪' && onScreen(li));
        return !!item;
    """), "产前随访跟踪菜单项已可见", timeout=3, quiet=True, record=False):
        run_click(page, locate("""
            const sub = Array.from(document.querySelectorAll('.ant-menu-submenu-title'))
                .find(d => d.textContent.trim() === '产前跟踪' && onScreen(d));
            if (!sub) return false;
            sub.setAttribute('data-kimi-click', '1');
            return true;
        """), "展开产前跟踪", timeout=15)
    ok = run_click(page, locate("""
        const item = Array.from(document.querySelectorAll('li.ant-menu-item'))
            .find(li => li.textContent.trim() === '产前随访跟踪' && onScreen(li));
        if (!item) return false;
        item.setAttribute('data-kimi-click', '1');
        return true;
    """), "点击产前随访跟踪",
        verify_js="() => location.href.includes('PrenatalFollowUp')", timeout=15)
    time.sleep(2)
    return ok


def udrhip_clear_discovery_date(page):
    """清空"发现日期"起止（留着会按发现日期过滤，按姓名查不全）。只清这一对。"""
    for idx in range(2):
        ok = page.evaluate("(i) => {" + BASE_JS + r"""
            const label = Array.from(document.querySelectorAll('span, label, div'))
                .find(e => (e.textContent || '').trim() === '发现日期' && e.children.length === 0);
            if (!label) return false;
            let box = label;
            for (let k = 0; k < 8 && box.parentElement; k++) {
                box = box.parentElement;
                const inputs = box.querySelectorAll('input[placeholder="开始时间"], input[placeholder="结束时间"]');
                if (inputs.length === 2) {
                    inputs[i].setAttribute('data-kimi-click', '1');
                    return true;
                }
            }
            return false;
        }""", idx)
        if not ok:
            print("  [失败] 发现日期输入框未找到")
            FAILED_STEPS.append("清空发现日期")
            return False
        page.locator('[data-kimi-click="1"]').first.click()
        page.evaluate("() => document.querySelectorAll('[data-kimi-click]').forEach(e => e.removeAttribute('data-kimi-click'))")
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        page.keyboard.press("Tab")
        time.sleep(0.5)
    vals = page.evaluate("() => {" + BASE_JS + r"""
        const label = Array.from(document.querySelectorAll('span, label, div'))
            .find(e => (e.textContent || '').trim() === '发现日期' && e.children.length === 0);
        if (!label) return null;
        let box = label;
        for (let k = 0; k < 8 && box.parentElement; k++) {
            box = box.parentElement;
            const inputs = box.querySelectorAll('input[placeholder="开始时间"], input[placeholder="结束时间"]');
            if (inputs.length === 2) return Array.from(inputs).map(x => x.value);
        }
        return null;
    }""")
    ok = vals == ["", ""]
    print(f"  清空发现日期: {vals} {'✓' if ok else '✗'}")
    if not ok:
        FAILED_STEPS.append("清空发现日期")
    return ok


def udrhip_check(page, name):
    """按姓名查询，返回结果条数（0 = 不在管）。"""
    if not run_click(page, locate("""
        const inp = Array.from(document.querySelectorAll('input[placeholder="请输入姓名"]')).find(onScreen);
        if (!inp) return false;
        inp.setAttribute('data-kimi-click', '1');
        return true;
    """), "姓名-聚焦", timeout=10, quiet=True, record=False):
        return -1
    page.keyboard.press("Control+a")
    page.keyboard.type(name, delay=30)
    page.keyboard.press("Tab")
    if not run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button, .el-button'))
            .find(b => b.textContent.trim() === '查询' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), "查询", timeout=10, quiet=True, record=False):
        return -1
    time.sleep(2)
    try:
        total = page.evaluate(
            "() => (document.querySelector('.el-pagination__total') || {}).textContent || ''")
    except Exception:
        return -1
    m = re.search(r"共\s*(\d+)\s*条", total)
    return int(m.group(1)) if m else -1


def udrhip_add(page, person, desc):
    """新增待随访跟踪人员：填表并保存。返回 True/False。"""
    if not run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button, .el-button'))
            .find(b => b.textContent.trim() === '新增人员' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), desc + "-开弹窗", timeout=10, quiet=True, record=False):
        print(f"  [失败] {desc} —— 新增人员按钮未找到")
        FAILED_STEPS.append(desc)
        return False
    time.sleep(2)
    if not run_step(page, locate(UDRHIP_DLG_JS + "return !!dlg();"), desc + "-弹窗", timeout=10, quiet=True, record=False):
        print(f"  [失败] {desc} —— 新增弹窗未打开")
        FAILED_STEPS.append(desc)
        return False

    def read_value(label):
        return page.evaluate("(l) => {" + BASE_JS + UDRHIP_DLG_JS + """
            const fi = dlgFormItem(l);
            const inp = fi && fi.querySelector('input, textarea');
            return inp ? inp.value : null;
        }""", label)

    def is_disabled(label):
        return page.evaluate("(l) => {" + BASE_JS + UDRHIP_DLG_JS + """
            const fi = dlgFormItem(l);
            const inp = fi && fi.querySelector('input, textarea');
            return inp ? !!inp.disabled : true;
        }""", label)

    def fill_text(label, value):
        if is_disabled(label):
            print(f"    {label}: 自动带出 {read_value(label)!r}")
            return True
        ok = page.evaluate("(l) => {" + BASE_JS + UDRHIP_DLG_JS + """
            const fi = dlgFormItem(l);
            const inp = fi && fi.querySelector('input, textarea');
            return inp ? (inp.setAttribute('data-kimi-click', '1'), true) : false;
        }""", label)
        if not ok:
            print(f"    [失败] {label} 输入框未找到"); return False
        page.locator('[data-kimi-click="1"]').first.click()
        page.evaluate("() => document.querySelectorAll('[data-kimi-click]').forEach(e => e.removeAttribute('data-kimi-click'))")
        page.keyboard.press("Control+a")
        page.keyboard.type(value, delay=30)
        page.keyboard.press("Tab")
        time.sleep(0.3)
        got = read_value(label)
        print(f"    {label}: {got!r} {'✓' if got == value else '✗'}")
        return got == value

    def fill_date(label, value):
        if is_disabled(label):
            print(f"    {label}: 自动带出 {read_value(label)!r}")
            return True
        ok = page.evaluate("(l) => {" + BASE_JS + UDRHIP_DLG_JS + """
            const fi = dlgFormItem(l);
            const inp = fi && fi.querySelector('input');
            return inp ? (inp.setAttribute('data-kimi-click', '1'), true) : false;
        }""", label)
        if not ok:
            print(f"    [失败] {label} 日期框未找到"); return False
        page.locator('[data-kimi-click="1"]').first.click()
        page.evaluate("() => document.querySelectorAll('[data-kimi-click]').forEach(e => e.removeAttribute('data-kimi-click'))")
        page.keyboard.press("Control+a")
        page.keyboard.type(value, delay=30)
        page.keyboard.press("Enter")
        time.sleep(0.5)
        # 收日期面板：点弹窗标题（不能按 Esc，会把整个弹窗关掉）
        page.evaluate("() => {" + BASE_JS + UDRHIP_DLG_JS + """
            const panel = Array.from(document.querySelectorAll('.el-picker-panel')).find(onScreen);
            const d = dlg();
            if (panel && d) d.querySelector('.el-dialog__title').click();
        }""")
        time.sleep(0.3)
        got = read_value(label)
        print(f"    {label}: {got!r} {'✓' if got == value else '✗'}")
        return got == value

    def fill_select(label, option):
        for attempt in range(3):
            # 收掉残留下拉层
            page.evaluate("() => {" + BASE_JS + UDRHIP_DLG_JS + """
                const d = dlg();
                if (d) d.querySelector('.el-dialog__title').click();
            }""")
            time.sleep(0.5)
            # 真实点击开下拉（有的 select 监听 mousedown，JS click 唤不起；被挡就补 mousedown）
            ok = page.evaluate("(l) => {" + BASE_JS + UDRHIP_DLG_JS + """
                const fi = dlgFormItem(l);
                const sel = fi && fi.querySelector('.el-select');
                return sel ? (sel.setAttribute('data-kimi-click', '1'), true) : false;
            }""", label)
            if not ok:
                print(f"    [失败] {label} 下拉未找到"); return False
            try:
                page.locator('[data-kimi-click="1"]').first.click(timeout=5000)
            except Exception:
                page.evaluate("(l) => {" + BASE_JS + UDRHIP_DLG_JS + """
                    const fi = dlgFormItem(l);
                    const sel = fi && fi.querySelector('.el-select');
                    if (sel) {
                        sel.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        sel.click();
                    }
                }""", label)
            page.evaluate("() => document.querySelectorAll('[data-kimi-click]').forEach(e => e.removeAttribute('data-kimi-click'))")
            time.sleep(0.8)
            # 选项用 JS click（下拉层会动画/瞬关，真实点击等不到稳定）
            picked = page.evaluate("(o) => {" + BASE_JS + """
                const dd = Array.from(document.querySelectorAll('.el-select-dropdown')).find(onScreen);
                if (!dd) return 'no-dropdown';
                const item = Array.from(dd.querySelectorAll('.el-select-dropdown__item'))
                    .find(i => i.textContent.trim() === o);
                if (!item) return 'no-option';
                item.click();
                return 'ok';
            }""", option)
            time.sleep(0.5)
            if read_value(label) == option:
                print(f"    {label}: {option!r} ✓")
                return True
            print(f"    {label} 第{attempt + 1}次未选上({picked})，重试")
        print(f"    [失败] {label} 选 {option}")
        return False

    ok = True
    ok &= fill_text("姓名", person["name"])
    ok &= fill_text("保健号", person["bjh"])
    ok &= fill_text("证件号码", person["idcard"])
    ok &= fill_date("出生日期", person["birth"])       # 一般由证件号自动带出（禁用）
    ok &= fill_text("孕妇电话", person["phone"])
    if person.get("husband_phone"):
        ok &= fill_text("丈夫电话", person["husband_phone"])
    ok &= fill_text("孕次", person["yunci"])
    ok &= fill_text("产次", person["chanci"])
    ok &= fill_date("末次月经", person["lmp"])
    ok &= fill_date("预产期", person["edd"])           # 一般由末次月经自动带出（禁用）
    if person.get("color"):
        ok &= fill_select("妊娠风险颜色", person["color"])
    ok &= fill_date("发现日期", datetime.now().strftime("%Y-%m-%d"))
    # 发现孕周由表单按末次月经自动带出（禁用）
    ok &= fill_select("发现方式", "院部下转")
    if person.get("hukou"):
        ok &= fill_select("户籍类型", person["hukou"])
    if not ok:
        print(f"  [中止] {desc} 有字段填写失败，不保存")
        FAILED_STEPS.append(desc)
        return False

    # 保存：JS 点击有效；弹窗关闭有延迟，轮询放长
    page.evaluate("() => {" + BASE_JS + UDRHIP_DLG_JS + """
        const d = dlg();
        const btn = d && Array.from(d.querySelectorAll('button'))
            .find(b => b.textContent.replace(/\\s+/g, '') === '保存');
        if (btn) btn.click();
    }""")
    for _ in range(40):
        time.sleep(0.5)
        closed = page.evaluate("() => {" + BASE_JS + UDRHIP_DLG_JS + "return !dlg();}")
        if closed:
            print(f"  [成功] {desc} 保存成功")
            return True
    print(f"  [失败] {desc} 保存后弹窗未关闭")
    FAILED_STEPS.append(desc)
    return False


# ==================== 输出 ====================

def save_excel(people, filename, desc):
    """名单 + 协同管理状态 写成 xlsx 存到桌面。"""
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "名单"
        ws.append(["保健号", "姓名", "手机号码", "末次月经", "初检颜色", "预产期", "户籍类型", "协同管理状态"])
        for x in people:
            ws.append([x.get("bjh"), x.get("name"), x.get("phone"), x.get("lmp"),
                       x.get("color"), x.get("edd"), x.get("hukou"), x.get("status", "")])
        path = os.path.join(DESKTOP, filename)
        wb.save(path)
        print(f"  [成功] {desc}：{len(people)} 条 → {path}")
    except Exception as e:
        print(f"  [失败] {desc}：写 Excel 出错 {e}")
        FAILED_STEPS.append(desc + "(写Excel)")


# ==================== 主流程 ====================

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

        fyweb = udrhip = portal = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if FYWEB_HOST in pg.url:
                    fyweb = pg
                elif UDRHIP_MARK in pg.url and "login" not in pg.url:
                    udrhip = pg
                elif PORTAL_MARK in pg.url:
                    portal = pg
        if not fyweb or "/login" in fyweb.url:
            print(f"错误：未找到已登录的 fyweb 页面（{FYWEB_HOST}），请先打开并登录")
            return

        # udrhip 页面没开着的话，从平台导航页点"孕产妇协同管理信息系统"进入（SSO 免登）
        if not udrhip:
            if not portal:
                print("错误：未找到龙岗区全民健康信息平台页面，"
                      "请先登录 http://172.17.9.215:10041/udaam-ui/login")
                return
            print("4. 从平台导航进入 孕产妇协同管理信息系统...")
            before = {pg.url for ctx in browser.contexts for pg in ctx.pages}
            if not run_click(portal, locate("""
                const el = Array.from(document.querySelectorAll('div.content_title, a, span, div'))
                    .find(e => (e.textContent || '').trim() === '孕产妇协同管理信息系统' && onScreen(e));
                if (!el) return false;
                el.setAttribute('data-kimi-click', '1');
                return true;
            """), "点击孕产妇协同管理信息系统", timeout=15):
                return
            deadline = time.time() + 15
            while time.time() < deadline:
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        if UDRHIP_MARK in pg.url and pg.url not in before:
                            udrhip = pg
                            break
                    if udrhip:
                        break
                if udrhip:
                    break
                time.sleep(0.5)
            if not udrhip:
                print("错误：孕产妇协同管理信息系统未能打开，请手动进入后重试")
                return
            time.sleep(3)
        print(f"4. 页面就绪: fyweb + udrhip({udrhip.url.split('/')[-1]})")

        # ========== 阶段A：fyweb 建档一览表收集 ==========
        month_arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
        try:
            start_date, end_date = date_range(month_arg)
        except ValueError as e:
            print(f"参数错误：{e}")
            sys.exit(1)

        print(f"5. fyweb 孕妇建档一览表: {start_date} 至 {end_date} ...")
        people = []
        if fyweb_goto_register(fyweb):
            date_ok = set_labeled_date(fyweb, "建档日期", "开始", start_date, "建档日期-开始")
            date_ok = set_labeled_date(fyweb, "建档日期", "结束", end_date, "建档日期-结束") and date_ok
            query_ok = run_click(fyweb, locate("""
                const btn = Array.from(document.querySelectorAll('button.ivu-btn'))
                    .find(b => b.textContent.trim() === '查询' && onScreen(b));
                if (!btn) return false;
                btn.setAttribute('data-kimi-click', '1');
                return true;
            """), "点击查询", timeout=10, fyweb=True)
            if query_ok:
                wait_table_loaded(fyweb, "查询结果加载")
            if date_ok and query_ok:
                people = scrape_register(fyweb, "爬取 孕妇建档一览表") or []
        if not people:
            print("  建档一览表无数据，结束")
            return
        # 同一人可能有多条建档记录，按 保健号+末次月经 去重
        seen = {}
        for x in people:
            seen.setdefault((x["bjh"], x["lmp"]), x)
        people = sorted(seen.values(), key=lambda x: x["bjh"])
        print(f"  建档记录去重后 {len(people)} 人")

        # ========== 阶段B：udrhip 查在管 + 补录 ==========
        print("6. udrhip 进入 产前随访跟踪，清空发现日期...")
        if not udrhip_goto_followup(udrhip):
            print("错误：进不了产前随访跟踪页面")
            for x in people:
                x["status"] = "未检查"
            save_excel(people, f"孕妇建档一览表_{start_date}至{end_date}.xlsx", "孕妇建档一览表")
            return
        udrhip_clear_discovery_date(udrhip)

        for x in people:
            print(f"===== {x['name']}({x['bjh']}) =====")
            n = udrhip_check(udrhip, x["name"])
            print(f"  在管记录: {n} 条")
            if n != 0:
                x["status"] = "已在管" if n > 0 else "查询失败"
                continue
            # 不在管 → fyweb 补资料
            idcard = fyweb_get_idcard(fyweb, x["bjh"])
            if not idcard or "*" in idcard:
                print(f"  [失败] 身份证揭码失败: {idcard}")
                x["status"] = "补录失败(身份证获取失败)"
                FAILED_STEPS.append(f"{x['name']}-取身份证")
                continue
            x["idcard"] = idcard.strip()
            x["birth"] = f"{x['idcard'][6:10]}-{x['idcard'][10:12]}-{x['idcard'][12:14]}"
            fc = fyweb_get_firstcheck(fyweb, x["bjh"])
            if not fc or not fc.get("孕次"):
                print(f"  [失败] 初检记录缺失: {fc}")
                x["status"] = "补录失败(缺初检记录)"
                FAILED_STEPS.append(f"{x['name']}-初检")
                continue
            x["yunci"] = fc["孕次"]
            m = re.findall(r"(\d+)\s*次", fc.get("产次") or "")
            x["chanci"] = str(sum(int(v) for v in m)) if m else "0"
            if fc.get("预产期"):
                x["edd"] = fc["预产期"]
            x["husband_phone"] = (fc.get("丈夫手机") or "").strip()
            print(f"  资料: 孕{x['yunci']} 产{x['chanci']} 预产{x['edd']} 身份证{x['idcard'][:6]}****")
            # 回 udrhip 补录
            if udrhip_add(udrhip, x, f"补录 {x['name']}"):
                time.sleep(1)
                n2 = udrhip_check(udrhip, x["name"])
                x["status"] = "新录入" if n2 and n2 > 0 else "保存后复查未查到"
                print(f"  复查: {n2} 条 → {x['status']}")
                if n2 <= 0:
                    FAILED_STEPS.append(f"{x['name']}-复查")
            else:
                x["status"] = "补录失败"

        save_excel(people, f"孕妇建档一览表_{start_date}至{end_date}.xlsx", "孕妇建档一览表")

        # ========== 汇总 ==========
        print("=" * 40)
        stats = {}
        for x in people:
            stats[x.get("status", "?")] = stats.get(x.get("status", "?"), 0) + 1
        print("协同管理状态统计:", stats)
        if FAILED_STEPS:
            print(f"完成，但有 {len(FAILED_STEPS)} 个步骤未成功，请手动检查：")
            for s in FAILED_STEPS:
                print(f"  - {s}")
        else:
            print("全部步骤执行成功！")


if __name__ == "__main__":
    collect()
