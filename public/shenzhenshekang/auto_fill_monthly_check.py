#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""个人每月病历检查脚本

流程：
1. 关闭所有已打开的"就诊历史记录"标签页（查询条件在服务端是粘滞的，
   旧面板里残留的筛选清不掉，必须开全新面板才是干净状态）
2. 点击"统计分析" → "就诊历史记录"（打开全新面板）→ 点"本人"（jzls=3）
3. 挂号时间填入起止日期（命令行参数指定月份，如 `python auto_fill_monthly_check.py 7`
   → 当年 7 月整月；留空默认本月 1 号至今天）
4. 第一部分：直接搜索当月全部患者，翻页爬取，筛出"是否完善病历=否"，
   保存 Excel 到桌面（病历未完善患者_YYYY-MM.xlsx）
5. 第二部分（女性 14-49 岁）：键盘输入最小年龄14、最大年龄49
   5.1 下拉选"填写末次月经=未填写" → 搜索爬取 → 末次月经未填写_女14-49_YYYY-MM.xlsx
   5.2 下拉选"填写末次月经=全部、末次月经延迟35天=已延迟" → 搜索爬取
       → 末次月经已延迟35天_女14-49_YYYY-MM.xlsx

关键机制（都是实测趟出来的）：
- 系统的"导出"按钮不可用，改为直接读结果表格的 Ext store 数据（含全部字段）。
  翻页必须走分页工具栏的 moveNext（带游标校验），store.load({params:{start}}) 无效
- 系统会同时打开多个"就诊历史记录"标签页（字段 id/name 冲突），
  一切操作限定在"当前活动标签页"的面板内进行（li.x-mytab-strip-active 的 id
  形如 标签条id__面板id）
- 放大镜搜索按钮必须 Playwright 真实鼠标点击（合成 click 无反应）
- 下拉框必须通过 UI 操作设置（真实点击箭头 → 真实点击选项）：Ext setValue 匹配不上
  store 记录，显示文本不更新；且筛选条件在服务端粘滞，旧值不会被空值覆盖
- 页面上有多个下拉列表层且选项有重名（两个下拉都有"全部"），
  点选项时按所属下拉的"签名选项"（如 已填写/已延迟）定位正确的列表
- 年龄输入框用真实键盘输入（点击 → 全选 → 输入 → Tab）
- 日期用 Ext API setValue 写入（直接改 DOM value 不会更新组件内部值）
- 参数指定的月份大于当前月份时自动取上一年（如 1 月查去年 12 月）
- 依赖 openpyxl 写 Excel（pip install --user openpyxl）
- 失败的步骤只警告不中断，脚本末尾统一汇总，方便人工补操作
"""

from playwright.sync_api import sync_playwright
from datetime import datetime
import calendar
import json
import os
import sys
import time

FAILED_STEPS = []

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

# 输出 Excel 的列：store 字段名 -> 中文表头
FIELDS = [
    ("GHSJ", "挂号时间"),
    ("YSDM_text", "就诊医生"),
    ("BRXM", "姓名"),
    ("BRXB_text", "性别"),
    ("CSNY", "年龄"),
    ("BRXZ_text", "性质"),
    ("JFLX", "缴费类型"),
    ("ZYZD", "病人诊断"),
    ("KSDM_text", "就诊科室"),
    ("ISPERFECT", "是否完善病历"),
    ("MZHM", "门诊号码"),
]

# 下拉框 -> 签名选项（用于在多个下拉列表层里认出属于它的那个）
COMBO_SIGNATURE = {"hasMCYJ": "已填写", "hasMCYJYC": "已延迟"}

# 当前活动标签页的面板定位 + 面板内组件查找（多标签页字段冲突，必须限定面板）
PANEL_JS = r"""
    const onScreen = el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        if (r.x < -100 || r.y < -100) return false;
        let a = el;
        while (a && a !== document.body) {
            if (window.getComputedStyle(a).display === 'none') return false;
            a = a.parentElement;
        }
        return true;
    };
    const panelEl = () => {
        // 活动标签的 class 是 x-mytab-strip-active（有的版本是 x-mytab-strip-act）
        const tab = document.querySelector('li.x-mytab-strip-active, li.x-mytab-strip-act');
        if (!tab || tab.id.indexOf('__') < 0) return null;
        const panel = Ext.getCmp(tab.id.split('__')[1]);
        return panel && panel.el && panel.el.dom ? panel.el.dom : null;
    };
    // 结果表格：列模型里含 ISPERFECT（是否完善病历）的 GridPanel，
    // 页面上还有其他表格（患者列表等），按行数找会拿错
    const findGrid = () => {
        const pel = panelEl();
        if (!pel) return null;
        let grid = null;
        Ext.ComponentMgr.all.each(c => {
            if (grid) return;
            if (c instanceof Ext.grid.GridPanel && c.getColumnModel && c.el && c.el.dom
                && pel.contains(c.el.dom)
                && (c.getColumnModel().config || []).some(col => col.dataIndex === 'ISPERFECT')) {
                grid = c;
            }
        });
        return grid;
    };
    // 面板内的下拉框组件（hasMCYJ=填写末次月经，hasMCYJYC=末次月经延迟35天）
    const findCombo = name => {
        const pel = panelEl();
        if (!pel) return null;
        let found = null;
        Ext.ComponentMgr.all.each(c => {
            if (found) return;
            if (c instanceof Ext.form.ComboBox && c.el && c.el.dom && pel.contains(c.el.dom)
                && (c.name === name || c.hiddenName === name || c.el.dom.name === name)) {
                found = c;
            }
        });
        return found;
    };
"""

# 点搜索前在 store 上挂 load 钩子（真实点击由 run_click 完成，evaluate 期间无法点击）
HOOK_SEARCH_JS = "() => {" + PANEL_JS + r"""
    const g = findGrid();
    if (!g) return false;
    window.__searchDone = false;
    g.getStore().on('load', () => { window.__searchDone = true; });
    return true;
}"""

# 翻页爬取全部结果：走分页工具栏 moveNext（游标校验），store.load({params:{start}}) 无效；
# 按 GHXH（挂号序号）去重兜底，同一行不会因翻页抖动重复入表
SCRAPE_JS = "async () => {" + PANEL_JS + r"""
    const grid = findGrid();
    if (!grid) return {error: '未找到结果表格组件'};
    const store = grid.getStore();
    const bbar = grid.getBottomToolbar();
    if (!bbar) return {error: '未找到分页工具栏'};
    // 先回第一页（搜索后本来就在第一页，多退少补）
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
        if (++guard > 50) return {error: '翻页次数超限', collected: Object.keys(seen).length};
    }
    return {total, rows: Object.values(seen)};
}"""


def run_step(page, js, desc, timeout=10, quiet=False, record=True):
    """轮询执行JS（查找并操作，JS返回真值表示成功），直到成功或超时。
    成功后等待1秒，给系统反应时间。record=False 时超时不计入失败汇总（用于可重试的子步骤）。"""
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
            print(f"  [超时] {desc} —— 未找到目标，请手动处理")
            if record:
                FAILED_STEPS.append(desc)
            return False
        time.sleep(0.3)


def run_click(page, locate_js, desc, verify_js=None, timeout=10, double=False, quiet=False, record=True):
    """轮询执行 locate_js 定位目标元素。locate_js 找到目标时给它打上临时标记
    data-kimi-click="1" 并返回 true，找不到返回 false。点击由 Playwright locator
    完成（真实鼠标事件 isTrusted=true，且自动滚动入视口、等待元素稳定、检测
    接收事件、失败自动重试）。
    提供 verify_js 时，点击后轮询 verify_js 确认生效，未生效会重新定位点击。
    record=False 时超时不计入失败汇总（用于可重试的子步骤）。"""
    # 每轮先清旧标记：上轮点击后元素可能被销毁重建，残留标记会导致 locator 点错
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
                target = page.locator('[data-kimi-click="1"]').first
                if double:
                    target.dblclick(timeout=3000)
                else:
                    target.click(timeout=3000)
                clicked = True
            except Exception:
                pass
            try:
                page.evaluate(clear_mark_js)
            except Exception:
                pass
            if not clicked:
                # locator 点击失败（超时/被遮挡/不可点）不能算成功，继续轮询重试
                pass
            elif verify_js is None:
                if not quiet:
                    print(f"  [成功] {desc}")
                time.sleep(1)
                return True
            else:
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
            print(f"  [超时] {desc} —— 未找到目标，请手动处理")
            if record:
                FAILED_STEPS.append(desc)
            return False
        time.sleep(0.3)


def locate(body):
    """把定位语句包成 () => { ... } 函数，并注入 onScreen 助手。
    页面上大量隐藏窗口/模板副本（ExtJS 关闭是移到 -10000 而不是销毁），
    所有定位都要做"屏幕上可见"过滤。"""
    return "() => {" + r"""
    const onScreen = el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        if (r.x < -100 || r.y < -100) return false;
        let a = el;
        while (a && a !== document.body) {
            if (window.getComputedStyle(a).display === 'none') return false;
            a = a.parentElement;
        }
        return true;
    };
""" + body + "\n}"


def search_and_wait(page, desc, timeout=30):
    """在当前活动面板内点放大镜搜索并等结果加载完成。
    放大镜必须真实鼠标点击（合成 click 无反应），所以拆成：挂 load 钩子 → 真实点击 → 等钩子。"""
    if not run_step(page, HOOK_SEARCH_JS, desc + "-挂钩子", timeout=10, quiet=True):
        print(f"  [失败] {desc} —— 面板/表格未就绪")
        FAILED_STEPS.append(desc)
        return False
    if not run_click(page, "() => {" + PANEL_JS + r"""
        const pel = panelEl();
        if (!pel) return false;
        const btn = pel.querySelector('button.query');
        if (!btn || !onScreen(btn)) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    }""", desc, timeout=10, quiet=True):
        print(f"  [失败] {desc} —— 搜索按钮点击失败")
        FAILED_STEPS.append(desc)
        return False
    if not run_step(page, "() => window.__searchDone === true", desc + "-加载", timeout=25, quiet=True):
        print(f"  [超时] {desc} —— 结果未加载，请手动处理")
        FAILED_STEPS.append(desc)
        return False
    print(f"  [成功] {desc}")
    return True


def pick_combo(page, combo_name, item_text, desc):
    """通过 UI 真实操作设置下拉框：点箭头展开 → 在属于该下拉的列表里点选项 → 回读校验。

    注意两个坑（实测踩过）：
    - 页面上有多个下拉列表层，残留层即使下拉已收起也"可见"（ExtJS 只是移走不销毁），
      所以展开状态以组件的 isExpanded() 为准，不以列表层可见性为准
    - 两个下拉的选项有重名（都有"全部"），按签名选项（已填写/已延迟）认出属于
      当前下拉的列表
    失败整体重试 3 次，只记一次失败汇总。"""
    signature = COMBO_SIGNATURE.get(combo_name, item_text)
    IS_EXPANDED_JS = "() => {" + PANEL_JS + f"""
        const c = findCombo({json.dumps(combo_name)});
        return c && c.isExpanded ? c.isExpanded() : false;
    }}"""
    for attempt in range(3):
        # 1. 未展开则点触发箭头展开（残留列表层可能造成"看起来开着"，以 isExpanded 为准）
        try:
            expanded = page.evaluate(IS_EXPANDED_JS)
        except Exception:
            expanded = False
        if not expanded:
            run_click(page, "() => {" + PANEL_JS + f"""
                const c = findCombo({json.dumps(combo_name)});
                if (!c) return false;
                const wrap = c.el.dom.closest('.x-form-field-wrap');
                const trig = wrap ? wrap.querySelector('.x-form-trigger') : null;
                if (!trig || !onScreen(trig)) return false;
                trig.setAttribute('data-kimi-click', '1');
                return true;
            }}""", desc + "-开下拉", timeout=5, quiet=True, record=False)
            time.sleep(0.8)
        # 2. 在签名列表里点选项
        if run_click(page, locate(f"""
            const sig = {json.dumps(signature)};
            const lists = Array.from(document.querySelectorAll('.x-combo-list')).filter(l => {{
                const st = window.getComputedStyle(l);
                const r = l.getBoundingClientRect();
                return st.display !== 'none' && r.width > 0 && r.x > -100;
            }});
            const mine = lists.find(l => Array.from(l.querySelectorAll('.x-combo-list-item'))
                .some(i => (i.textContent || '').trim() === sig));
            if (!mine) return false;
            const item = Array.from(mine.querySelectorAll('.x-combo-list-item'))
                .find(i => (i.textContent || '').trim() === {json.dumps(item_text)});
            if (!item) return false;
            item.setAttribute('data-kimi-click', '1');
            return true;
        """), desc + "-选项", timeout=4, quiet=True, record=False):
            # 3. 回读确认。注意："全部"选项的 value 是 0，这个系统把它当"清空"处理——
            # 显示成灰色占位文字"全部"（emptyText），getValue() 返回 ''，getRawValue() 也是 ''，
            # 所以不能按显示文本校验，要按 value 校验（全部: ''/0，其余: 1/2）
            expected = {"全部": ["", "0", "0"], "已填写": ["1", "1"],
                        "未填写": ["2", "2"], "已延迟": ["1", "1"], "未延迟": ["2", "2"]}
            if run_step(page, "() => {" + PANEL_JS + f"""
                const c = findCombo({json.dumps(combo_name)});
                if (!c) return false;
                return {json.dumps(expected.get(item_text, [item_text]))}
                    .map(String).includes(String(c.getValue()));
            }}""", desc, timeout=3, quiet=True, record=False):
                print(f"  [成功] {desc}")
                return True
        time.sleep(0.5)
    print(f"  [失败] {desc} —— 3 次尝试后仍未成功，请手动处理")
    FAILED_STEPS.append(desc)
    return False


def type_field(page, field_name, text, desc):
    """真实键盘输入到面板内的输入框：点击 → 全选 → 输入 → Tab 提交。"""
    if not run_click(page, "() => {" + PANEL_JS + f"""
        const pel = panelEl();
        if (!pel) return false;
        const el = Array.from(pel.querySelectorAll('input[name={json.dumps(field_name)}]')).find(onScreen);
        if (!el) return false;
        el.setAttribute('data-kimi-click', '1');
        return true;
    }}""", desc + "-聚焦", timeout=10, quiet=True):
        print(f"  [失败] {desc} —— 输入框定位失败")
        FAILED_STEPS.append(desc)
        return False
    page.keyboard.press("Control+a")
    page.keyboard.type(text, delay=30)
    page.keyboard.press("Tab")
    time.sleep(0.5)
    ok = run_step(page, "() => {" + PANEL_JS + f"""
        const pel = panelEl();
        if (!pel) return false;
        const el = Array.from(pel.querySelectorAll('input[name={json.dumps(field_name)}]')).find(onScreen);
        return el ? el.value === {json.dumps(text)} : false;
    }}""", desc, timeout=5, quiet=True)
    if ok:
        print(f"  [成功] {desc}")
    else:
        print(f"  [失败] {desc}")
        FAILED_STEPS.append(desc)
    return ok


def scrape_all(page, desc):
    """翻页爬取当前搜索结果的全部行，失败返回 None。"""
    try:
        r = page.evaluate(SCRAPE_JS)
    except Exception as e:
        print(f"  [失败] {desc}：爬取异常 {e}")
        FAILED_STEPS.append(desc)
        return None
    if not r or "rows" not in r:
        print(f"  [失败] {desc}：{r.get('error') if r else '无返回'}")
        FAILED_STEPS.append(desc)
        return None
    print(f"  [成功] {desc}：爬取 {len(r['rows'])} 行（总数 {r['total']}）")
    return r["rows"]


def save_excel(rows, filename, desc):
    """把爬取的行写成 xlsx 存到桌面。"""
    if rows is None:
        return
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "名单"
        ws.append([h for _, h in FIELDS])
        for row in rows:
            ws.append([row.get(k) if row.get(k) is not None else "" for k, _ in FIELDS])
        path = os.path.join(DESKTOP, filename)
        wb.save(path)
        print(f"  [成功] {desc}：{len(rows)} 条 → {path}")
    except Exception as e:
        print(f"  [失败] {desc}：写 Excel 出错 {e}")
        FAILED_STEPS.append(desc + "(写Excel)")


def month_range(month_arg=""):
    """返回起止日期 ('yyyy-mm-01', 'yyyy-mm-止日')。
    month_arg 为空：本月 1 号到今天（如 8 月 20 日运行 → 08-01 至 08-20）；
    为 1-12：该年该月整月，月份大于当前月份时取上一年（如 1 月查去年 12 月）。"""
    now = datetime.now()
    if month_arg:
        month = int(month_arg)
        if not 1 <= month <= 12:
            raise ValueError(f"月份必须是 1-12，收到: {month_arg!r}")
        year = now.year - 1 if month > now.month else now.year
        last_day = calendar.monthrange(year, month)[1]
        end = f"{year}-{month:02d}-{last_day:02d}"
    else:
        year, month = now.year, now.month
        end = now.strftime("%Y-%m-%d")
    return f"{year}-{month:02d}-01", end


def monthly_check():
    print("1. 开始启动...")
    with sync_playwright() as p:
        print("2. 尝试连接Chrome...")
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception:
            print("错误：连接失败。请先运行 启动调试Chrome并打开网址.py 并登录系统。")
            return
        print("3. 连接成功!")

        target_url = "172.17.8.14:8780"
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if target_url in pg.url:
                    page = pg
                    break
            if page:
                break
        if not page:
            print(f"错误：未找到包含 {target_url} 的页面")
            return
        print(f"4. 当前页面标题: {page.title()}")

        # ========== 1. 关闭所有已打开的"就诊历史记录"标签页 ==========
        # 查询条件在服务端/会话层面粘滞，旧面板残留的筛选（年龄、末次月经）用空值清不掉，
        # 必须开全新面板才是干净状态。没有旧标签是正常情况，直接探测不报错
        print("5. 关闭旧的就诊历史记录标签页...")
        closed = 0
        LOCATE_CLOSE = locate(r"""
            const tab = Array.from(document.querySelectorAll('li.x-mytab-strip-closable'))
                .find(li => (li.textContent || '').includes('就诊历史记录') && onScreen(li));
            if (!tab) return false;
            const close = tab.querySelector('a.x-mytab-strip-close');
            if (!close) return false;
            close.setAttribute('data-kimi-click', '1');
            return true;
        """)
        for _ in range(5):  # 最多关 5 个，防止异常死循环
            try:
                found = page.evaluate(LOCATE_CLOSE)
            except Exception:
                found = False
            if not found:
                break
            try:
                page.locator('[data-kimi-click="1"]').first.click(timeout=3000)
                closed += 1
            except Exception:
                break
            finally:
                page.evaluate("() => document.querySelectorAll('[data-kimi-click]')"
                              ".forEach(e => e.removeAttribute('data-kimi-click'))")
            time.sleep(1)
        if closed:
            print(f"  [成功] 关闭了 {closed} 个旧标签页")
        else:
            print("  （没有旧标签页，跳过）")

        # ========== 2. 点击统计分析 → 就诊历史记录（全新面板） ==========
        print("6. 点击统计分析...")
        run_click(page, locate(r"""
            const links = Array.from(document.querySelectorAll('a')).filter(a =>
                (a.textContent || '').trim() === '统计分析' && onScreen(a));
            if (!links.length) return false;
            links[0].setAttribute('data-kimi-click', '1');
            return true;
        """), "点击统计分析", timeout=15)

        print("7. 点击就诊历史记录...")
        # 菜单项 LI id 为 CIC_module_CIC02（模块编码稳定）；兜底按文本匹配，
        # 但要排除已打开的标签页（x-mytab）里同名的那个
        run_click(page, locate(r"""
            let link = null;
            const li = document.getElementById('CIC_module_CIC02');
            if (li) link = li.querySelector('a') || li;
            if (!link || !onScreen(link)) {
                link = Array.from(document.querySelectorAll('a')).find(a =>
                    (a.textContent || '').trim() === '就诊历史记录' && onScreen(a)
                    && !a.closest('.x-mytab-strip, [class*="x-mytab"]'));
            }
            if (!link || !onScreen(link)) return false;
            link.setAttribute('data-kimi-click', '1');
            return true;
        """), "点击就诊历史记录", timeout=15)
        # 面板加载较慢，多等一会
        time.sleep(2)

        # ========== 3. 点击本人（活动面板内，jzls=3） ==========
        print("8. 点击本人...")
        run_click(page, "() => {" + PANEL_JS + r"""
            const pel = panelEl();
            if (!pel) return false;
            const target = Array.from(pel.querySelectorAll('input[type="radio"][name="jzls"]'))
                .find(r => r.value === '3' && onScreen(r));
            if (!target) return false;
            target.setAttribute('data-kimi-click', '1');
            return true;
        }""", "选择本人", verify_js="() => {" + PANEL_JS + r"""
            const pel = panelEl();
            if (!pel) return false;
            const target = Array.from(pel.querySelectorAll('input[type="radio"][name="jzls"]'))
                .find(r => r.value === '3' && onScreen(r));
            return target ? target.checked : false;
        }""")

        # ========== 4. 挂号时间：命令行参数指定月份，默认本月至今 ==========
        month_arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
        try:
            start_date, end_date = month_range(month_arg)
        except ValueError as e:
            print(f"参数错误：{e}")
            sys.exit(1)
        ym = start_date[:7]  # 输出文件名里的年月，如 2026-07
        print(f"9. 填写挂号时间: {start_date} 至 {end_date} ...")
        # 日期必须用 Ext API setValue 写入（直接改 DOM value 不会更新组件内部值，
        # 搜索会拿旧日期）；字段定位限定在活动面板内
        run_step(page, "() => {" + PANEL_JS + f"""
            const pel = panelEl();
            if (!pel) return false;
            const setDate = (id, val) => {{
                const el = pel.querySelector('#' + id);
                if (!el) return false;
                const c = Ext.getCmp(el.id);
                if (c && c.setValue) {{ c.setValue(val); return c.getRawValue() === val; }}
                el.value = val;
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return el.value === val;
            }};
            return setDate('startDate', {json.dumps(start_date)})
                && setDate('endDate', {json.dumps(end_date)});
        }}""", "填写挂号时间")

        # ========== 第一部分：病历未完善患者名单 ==========
        # 全新面板没有任何筛选残留，直接搜索就是当月全部患者。
        # 搜索前显式确认四个筛选选项为空（最小/最大年龄、填写末次月经、末次月经延迟35天），
        # 避免任何残留条件污染第一部分的全量结果
        print("10. 第一部分：搜索当月全部患者...")
        run_step(page, "() => {" + PANEL_JS + r"""
            const pel = panelEl();
            if (!pel) return false;
            const val = sel => { const el = pel.querySelector(sel); return el ? (el.value || '').trim() : null; };
            const empty = v => v === '' || v === '全部';
            return empty(val('input[name="minYear"]')) && empty(val('input[name="maxYear"]'))
                && empty(val('#hasMCYJ')) && empty(val('#hasMCYJYC'));
        }""", "确认年龄/末次月经筛选为空")
        if search_and_wait(page, "搜索当月患者"):
            rows = scrape_all(page, "爬取当月患者")
            if rows is not None:
                bad = [r for r in rows if str(r.get("ISPERFECT") or "").strip() == "否"]
                save_excel(bad, f"病历未完善患者_{ym}.xlsx", "病历未完善患者名单")

        # ========== 第二部分：女性 14-49 岁，末次月经两种组合 ==========
        # 年龄用真实键盘输入；下拉框用 UI 真实操作（点箭头→点选项）。
        # 末次月经字段只对女性患者存在，输出时再按性别=女过滤一次兜底
        print("11. 第二部分-1：女14-49 末次月经未填写...")
        age_ok = type_field(page, "minYear", "14", "最小年龄=14")
        age_ok = type_field(page, "maxYear", "49", "最大年龄=49") and age_ok
        combo_ok = pick_combo(page, "hasMCYJ", "未填写", "填写末次月经=未填写")
        if age_ok and combo_ok and search_and_wait(page, "搜索 末次月经未填写"):
            rows = scrape_all(page, "爬取 末次月经未填写")
            if rows is not None:
                female = [r for r in rows if str(r.get("BRXB_text") or "").strip() == "女"]
                save_excel(female, f"末次月经未填写_女14-49_{ym}.xlsx", "末次月经未填写名单")

        print("12. 第二部分-2：女14-49 末次月经已延迟35天...")
        combo_ok = pick_combo(page, "hasMCYJ", "全部", "填写末次月经=全部")
        combo_ok = pick_combo(page, "hasMCYJYC", "已延迟", "末次月经延迟35天=已延迟") and combo_ok
        if combo_ok and search_and_wait(page, "搜索 末次月经已延迟35天"):
            rows = scrape_all(page, "爬取 末次月经已延迟35天")
            if rows is not None:
                female = [r for r in rows if str(r.get("BRXB_text") or "").strip() == "女"]
                save_excel(female, f"末次月经已延迟35天_女14-49_{ym}.xlsx", "末次月经已延迟35天名单")

        # ========== 汇总 ==========
        print("=" * 40)
        if FAILED_STEPS:
            print(f"完成，但有 {len(FAILED_STEPS)} 个步骤未成功，请手动检查：")
            for s in FAILED_STEPS:
                print(f"  - {s}")
        else:
            print("全部步骤执行成功！")


if __name__ == "__main__":
    monthly_check()
