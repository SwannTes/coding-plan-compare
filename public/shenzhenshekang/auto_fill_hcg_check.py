#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""血尿HCG检查脚本（统计分析 → 诊疗项目统计）

流程：
1. 关闭所有已打开的"诊疗项目统计"标签页（查询条件在服务端粘滞，
   旧面板里残留的筛选清不掉，必须开全新面板才是干净状态）
2. 点击"统计分析" → "诊疗项目统计"（模块 li id = CIC_module_IVC20，实测）
3. 开始/结束时间填入起止日期（命令行参数指定月份，如
   `python auto_fill_hcg_check.py 5` → 当年 5 月整月；留空默认上一个自然月，
   与 fubao/monthly 脚本一致；也兼容 2026-05 这种 yyyy-mm 写法）
4. 循环项目名列表 ITEM_NAMES：清空并输入项目名 → 点"查询" →
   翻页爬取全部结果行 → 打印到控制台
5. 关联患者信息（collect_patient_info）：收费行只有姓名+开单日期，没有病人编号，
   自动打开"就诊历史记录"（全院、同一月份），按姓名逐个搜索爬取就诊记录，
   按 姓名+日期 匹配后：年龄取就诊记录的 CSNY；完整电话按 EMPIID 走
   phis.simpleQuery(MPI_DemographicInfo)（store 里电话是脱敏值）；
   检查结果调 loadClinicInfo(type=5) 读 jy_data，提取 HCG 项目并判定——
   血HCG数值 ≥10 阳性、5-10 可疑阳性、<5 阴性；尿妊娠等定性结果按文本（阴性/阳性/弱阳性）
6. 全部项目的结果导出到桌面一个 Excel（血尿HCG检查_起至止.xlsx），
   每个项目一个 sheet（sheet 名用项目名，非法字符替换、超长截断），
   列 = 收费列 + 年龄/电话号码/检查结果/判定

关键机制（沿用 auto_fill_monthly_check.py 实测趟出来的方案）：
- 系统的"导出"按钮不可用，改为直接读结果表格的 Ext store 数据（含全部字段）。
  本面板的分页工具栏不是 grid.getBottomToolbar()（那是个空 toolbar），
  而是面板内一个带 moveNext/cursor/pageSize 的独立组件，按"store 与 grid 相同"认出它；
  翻页必须走它的 moveNext（带游标校验），store.load({params:{start}}) 无效
- 系统会同时打开多个同名标签页（字段 id/name 冲突），
  一切操作限定在"当前活动标签页"的面板内进行（li.x-mytab-strip-active 的 id
  形如 标签条id__面板id）
- 查询按钮必须 Playwright 真实鼠标点击（JS 合成 click 无反应）：
  JS 定位后打 data-kimi-click="1" 标记，再 page.locator 点击；
  注意面板里还有 button.excel（导出）和 button.print（打印），只点 button.query
- 日期用 Ext API setValue 写入（直接改 DOM value 不会更新组件内部值）；
  本面板日期字段 name 是 KSSJ/JSSJ（不是就诊历史记录的 startDate/endDate），
  组件 id 是 ext-comp-* 不稳定的，按 name 定位
- 项目名称文本框 name=XMMC，用真实键盘输入（点击 → 全选 → 输入 → Tab），
  再回读 el.value 校验
- 项目名称匹配方式是"前缀匹配"（实测：输"尿妊娠"能匹配"尿妊娠试验-金标法"，
  输"绒毛膜"匹配不到"血清人绒毛膜促性腺激素测定-化学发光法"），
  所以血HCG要用前缀"血清人绒毛膜"，实际匹配到的项目名以结果 FYMC 为准
- 每次查询前在 store 上挂 load 钩子、点查询后等钩子触发，
  保证读到的是本次查询的新数据，不会拿到上一个项目的旧结果
- 结果 store 没有序号类唯一键，按整行 JSON 去重兜底
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

# ===== 查询条件（按需修改） =====
# 月份由命令行参数指定（与 fubao/monthly 脚本一致，见 month_range 说明），此处只有项目名列表
# 项目名列表（前缀匹配，见文件头说明）：尿HCG + 血HCG
ITEM_NAMES = ["尿妊娠", "血清人绒毛膜"]

# 输出 Excel 的列：store 字段名 -> 中文表头（面板实测列）
FIELDS = [
    ("BRXM", "病人姓名"),
    ("FYMC", "项目名称"),
    ("YBFYBM", "医保费用编码"),
    ("YLDJ", "单价"),
    ("YLSL", "数量"),
    ("HJJE", "金额"),
    ("YSXM", "申请医生"),
    ("KDRQ", "开单日期"),
    ("SFRQ", "收费日期"),
    ("ZFRQ", "作废日期"),
]

# Excel 实际输出列：收费列 + 关联就诊记录收集的患者信息
# （_NL 年龄、_DH 电话号码、_JYJG 检查结果、_PD 判定，由 collect_patient_info 回填）
EXCEL_FIELDS = FIELDS + [
    ("_NL", "年龄"),
    ("_DH", "电话号码"),
    ("_JYJG", "检查结果"),
    ("_PD", "判定"),
]

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
    // 结果表格：列模型里含 FYMC（项目名称）的 GridPanel（本面板本身就是 GridPanel）
    const findGrid = () => {
        const pel = panelEl();
        if (!pel) return null;
        let grid = null;
        Ext.ComponentMgr.all.each(c => {
            if (grid) return;
            if (c instanceof Ext.grid.GridPanel && c.getColumnModel && c.el && c.el.dom
                && pel.contains(c.el.dom)
                && (c.getColumnModel().config || []).some(col => col.dataIndex === 'FYMC')) {
                grid = c;
            }
        });
        return grid;
    };
    // 分页工具栏：getBottomToolbar() 拿到的是空 toolbar（实测踩过），
    // 真正的分页条是面板内带 moveNext 且 store 与结果表格相同的独立组件
    const findPager = grid => {
        const pel = panelEl();
        if (!pel || !grid) return null;
        let pager = null;
        Ext.ComponentMgr.all.each(c => {
            if (pager) return;
            if (typeof c.moveNext === 'function' && c.el && c.el.dom && pel.contains(c.el.dom)
                && c.store === grid.getStore()) {
                pager = c;
            }
        });
        return pager;
    };
"""

# 点查询前挂 load 钩子的逻辑已并入 search_and_wait（按面板参数化）

# 翻页爬取全部结果：走分页工具栏 moveNext（游标校验），store.load({params:{start}}) 无效；
# store 没有序号类唯一键，按整行 JSON 去重兜底，同一行不会因翻页抖动重复入表
SCRAPE_JS = "async () => {" + PANEL_JS + r"""
    const grid = findGrid();
    if (!grid) return {error: '未找到结果表格组件'};
    const store = grid.getStore();
    const bbar = findPager(grid);
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
        store.each(rec => { seen[JSON.stringify(rec.data)] = rec.data; });
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

# ========== 就诊历史记录（按姓名关联患者信息，收集年龄/电话/检查结果） ==========
# 收费行只有姓名+开单日期，没有病人编号，必须去就诊历史记录按姓名搜出就诊记录
# （拿到 JZXH/EMPIID/CSNY），再用 loadClinicInfo 读检验结果、MPI 档案读完整电话
JZLS_PANEL_JS = r"""
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
        const tab = document.querySelector('li.x-mytab-strip-active, li.x-mytab-strip-act');
        if (!tab || tab.id.indexOf('__') < 0) return null;
        const panel = Ext.getCmp(tab.id.split('__')[1]);
        return panel && panel.el && panel.el.dom ? panel.el.dom : null;
    };
    // 就诊历史记录的结果表格按 ISPERFECT 列认（区别于诊疗项目统计的 FYMC）
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
"""

# 就诊历史记录翻页爬取：分页条是 grid.getBottomToolbar()（与诊疗项目统计不同），
# 按 GHXH（挂号序号）去重兜底（与 fubao 脚本一致）
JZLS_SCRAPE_JS = "async () => {" + JZLS_PANEL_JS + r"""
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
    return {total, rows: Object.values(seen)};
}"""

# 批量读取检验结果：对每条就诊记录调系统内部接口 loadClinicInfo（与 fubao 脚本相同），
# type=5 返回 jy_data（检验结果）。入参 clinicId/brid 必须传字符串；必须带 jgid；
# 跨年数据要带 q_YEAR（病历按年分表）。返回 {GHXH: {jy, error}}
FETCH_RECORDS_JS = "async (rows) => {" + r"""
    const curYear = new Date().getFullYear();
    const out = {};
    for (const r of rows) {
        const key = String(r.GHXH);
        try {
            const body = {clinicId: String(r.JZXH), jgid: String(r.JGID),
                          type: "5", brid: String(r.BRBH)};
            const year = parseInt(String(r.GHSJ || '').substring(0, 4));
            const req = {serviceId: "clinicManageService", serviceAction: "loadClinicInfo",
                         body: body};
            if (year && year !== curYear) req.q_YEAR = String(year);
            const res = phis.script.rmi.miniJsonRequestSync(req);
            const j = res.json || {};
            out[key] = {jy: j.jy_data || [], error: j.ms_bcjl ? "" : ("病历为空(code=" + res.code + ")")};
        } catch (e) {
            out[key] = {jy: [], error: String(e)};
        }
    }
    return out;
}"""

# 按 EMPIID 批量查完整电话（store 里电话是脱敏值，完整值在 EMPI 人口学信息，
# 与 fubao 脚本相同：phis.simpleQuery + MPI_DemographicInfo）
FETCH_CONTACTS_JS = "async (empiIds) => {" + r"""
    const out = {};
    for (const id of empiIds) {
        try {
            const res = phis.script.rmi.miniJsonRequestSync({
                serviceId: "phis.simpleQuery", method: "execute",
                schema: "phis.application.pix.schemas.MPI_DemographicInfo",
                cnd: ["eq", ["$", "empiId"], ["s", String(id)]],
                pageSize: 10, pageNo: 1});
            const body = (res.json || {}).body || [];
            const d = body[0];
            if (!d) continue;
            out[String(id)] = {dh: d.mobileNumber || d.contactPhone || d.phoneNumber || ""};
        } catch (e) { /* 单个失败跳过 */ }
    }
    return out;
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


def search_and_wait(page, desc, timeout=30, panel_js=None):
    """在当前活动面板内点查询按钮并等结果加载完成。
    查询按钮必须真实鼠标点击（合成 click 无反应），所以拆成：挂 load 钩子 → 真实点击 → 等钩子。
    等 load 钩子触发才返回，保证读到的是本次查询的新数据（多项目循环时不会读到旧结果）。
    注意面板里还有 button.excel（导出）和 button.print（打印），只认 button.query。
    panel_js 默认诊疗项目统计面板，传 JZLS_PANEL_JS 则操作就诊历史记录面板。"""
    pj = panel_js or PANEL_JS
    hook_js = "() => {" + pj + r"""
        const g = findGrid();
        if (!g) return false;
        window.__searchDone = false;
        g.getStore().on('load', () => { window.__searchDone = true; });
        return true;
    }"""
    if not run_step(page, hook_js, desc + "-挂钩子", timeout=10, quiet=True):
        print(f"  [失败] {desc} —— 面板/表格未就绪")
        FAILED_STEPS.append(desc)
        return False
    if not run_click(page, "() => {" + pj + r"""
        const pel = panelEl();
        if (!pel) return false;
        const btn = pel.querySelector('button.query');
        if (!btn || !onScreen(btn)) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    }""", desc, timeout=10, quiet=True):
        print(f"  [失败] {desc} —— 查询按钮点击失败")
        FAILED_STEPS.append(desc)
        return False
    if not run_step(page, "() => window.__searchDone === true", desc + "-加载", timeout=25, quiet=True):
        print(f"  [超时] {desc} —— 结果未加载，请手动处理")
        FAILED_STEPS.append(desc)
        return False
    print(f"  [成功] {desc}")
    return True


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


def scrape_all(page, desc, scrape_js=None):
    """翻页爬取当前查询结果的全部行，失败返回 None。
    scrape_js 默认诊疗项目统计的翻页逻辑，就诊历史记录传 JZLS_SCRAPE_JS。"""
    try:
        r = page.evaluate(scrape_js or SCRAPE_JS)
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


def extract_hcg_result(jy_data):
    """从检验结果（jy_data）里找 HCG 相关项目，返回 (检查结果摘要, 判定)。
    血HCG数值：>=10 阳性，5-10 可疑阳性，<5 阴性；定性结果（尿妊娠等）按文本。"""
    hits = []
    for it in jy_data or []:
        label = str(it.get("ITEMNAME") or "") + str(it.get("EXAMITEMNAME") or "")
        if "HCG" in label.upper() or "绒毛膜" in label or "妊娠" in label:
            hits.append(it)
    if not hits:
        return "", ""
    parts, verdict = [], ""
    for it in hits:
        label = (it.get("ITEMNAME") or it.get("EXAMITEMNAME") or "").strip()
        res = str(it.get("TESTRESULT") or "").strip()
        unit = str(it.get("RESULTUNIT") or "").strip()
        msg = str(it.get("RESULTMESSAGE") or "").strip()
        parts.append(f"{label}={res}{unit}" if res else (f"{label}={msg}" if msg else label))
        # 数值结果可能带不等号前缀（如 <0.20），剥掉再按阈值判定
        try:
            val = float(re.sub(r"^[<≤>≥\s]+", "", res))
        except ValueError:
            text = msg or res
            if "弱阳" in text:
                v = "弱阳性"
            elif "阳" in text:
                v = "阳性"
            elif "阴" in text:
                v = "阴性"
            else:
                v = text
        else:
            if val >= 10:
                v = f"阳性({val}{unit}≥10)"
            elif val >= 5:
                v = f"可疑阳性({val}{unit}5-10)"
            else:
                v = f"阴性({val}{unit}<5)"
        if not verdict or "阳" in v:  # 多项结果时阳性优先
            verdict = v
    return "；".join(parts)[:200], verdict


def fetch_phones(page, fee_rows, desc):
    """按收费行关联到的 _EMPIID 批量查完整电话，返回 {empiId: {dh}}。失败只警告。"""
    empi_ids = sorted({str(v.get("_EMPIID")) for v in fee_rows if v.get("_EMPIID")})
    if not empi_ids:
        return {}
    try:
        return page.evaluate(FETCH_CONTACTS_JS, empi_ids)
    except Exception as e:
        print(f"  [失败] {desc}：查电话异常 {e}")
        FAILED_STEPS.append(desc + "(查电话)")
        return {}


def pick_query_field_brxm(page, desc="查询方式=姓名"):
    """就诊历史记录的查询条件下拉默认是"门诊号码"，此时面板里没有 input[name=BRXM]；
    下拉切到"姓名"后旁边文本框的 name 才变成 BRXM（实测踩过：直接找 BRXM 输入框永远找不到）。
    下拉组件 id 是 ext-comp-* 不稳定的，按"选项里同时有 门诊号码/姓名"签名定位。"""
    FIND_JS = "() => {" + JZLS_PANEL_JS + r"""
        const pel = panelEl();
        if (!pel) return false;
        // 已经有 BRXM 输入框就不用切
        if (Array.from(pel.querySelectorAll('input[name="BRXM"]')).find(onScreen)) return 'ready';
        let found = null;
        Ext.ComponentMgr.all.each(c => {
            if (found) return;
            if (c instanceof Ext.form.ComboBox && c.el && c.el.dom && pel.contains(c.el.dom)
                && c.getStore && c.valueField === 'value') {
                const vals = [];
                c.getStore().each(r => vals.push(String(r.data.value)));
                if (vals.includes('MZHM') && vals.includes('BRXM') && onScreen(c.el.dom)) found = c;
            }
        });
        if (!found) return false;
        if (String(found.getValue()) === 'BRXM') return 'ready';
        return found.id;
    }"""
    for attempt in range(3):
        try:
            st = page.evaluate(FIND_JS)
        except Exception:
            st = False
        if st == 'ready':
            print(f"  [成功] {desc}")
            return True
        if not st:
            time.sleep(1)
            continue
        # 点触发箭头展开下拉
        run_click(page, "() => {" + JZLS_PANEL_JS + f"""
            const c = Ext.getCmp({json.dumps(st)});
            if (!c) return false;
            const wrap = c.el.dom.closest('.x-form-field-wrap');
            const trig = wrap ? wrap.querySelector('.x-form-trigger') : null;
            if (!trig || !onScreen(trig)) return false;
            trig.setAttribute('data-kimi-click', '1');
            return true;
        }}""", desc + "-开下拉", timeout=5, quiet=True, record=False)
        time.sleep(0.8)
        # 在属于该下拉的列表里点"姓名"（签名：同一列表里有"门诊号码"）
        if run_click(page, locate(r"""
            const lists = Array.from(document.querySelectorAll('.x-combo-list')).filter(l => {
                const st = window.getComputedStyle(l);
                const r = l.getBoundingClientRect();
                return st.display !== 'none' && r.width > 0 && r.x > -100;
            });
            const mine = lists.find(l => Array.from(l.querySelectorAll('.x-combo-list-item'))
                .some(i => (i.textContent || '').trim() === '门诊号码'));
            if (!mine) return false;
            const item = Array.from(mine.querySelectorAll('.x-combo-list-item'))
                .find(i => (i.textContent || '').trim() === '姓名');
            if (!item) return false;
            item.setAttribute('data-kimi-click', '1');
            return true;
        """), desc + "-选姓名", timeout=4, quiet=True, record=False):
            # 回读确认 BRXM 输入框已出现
            if run_step(page, "() => {" + JZLS_PANEL_JS + r"""
                const pel = panelEl();
                if (!pel) return false;
                return !!Array.from(pel.querySelectorAll('input[name="BRXM"]')).find(onScreen);
            }""", desc, timeout=3, quiet=True, record=False):
                print(f"  [成功] {desc}")
                return True
        time.sleep(0.5)
    print(f"  [失败] {desc} —— 3 次尝试后仍未成功，请手动把查询方式切到姓名")
    FAILED_STEPS.append(desc)
    return False


def collect_patient_info(page, results, start_date, end_date):
    """把收费行按 姓名+开单/收费日期 关联到就诊历史记录的就诊记录，
    回填 _NL（年龄）_DH（电话）_JYJG（检查结果）_PD（判定）四个字段供 Excel 输出。
    每步失败只警告不中断，行上留空或提示文字。"""
    fee_rows = []
    for _, rows in results:
        for r in rows or []:
            if str(r.get("ZFRQ") or "").strip():
                continue  # 作废单不收集
            fee_rows.append(r)
    if not fee_rows:
        return
    names = sorted({str(r.get("BRXM") or "").strip() for r in fee_rows} - {""})
    print(f"10. 关联就诊历史记录收集患者信息（{len(fee_rows)} 行收费，{len(names)} 人）...")

    # 1) 关闭旧的就诊历史记录标签页（筛选条件服务端粘滞，必须开全新面板）
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
    for _ in range(5):
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

    # 2) 统计分析 → 就诊历史记录（菜单项 li id = CIC_module_CIC02，实测稳定）
    if not run_click(page, locate(r"""
        const links = Array.from(document.querySelectorAll('a')).filter(a =>
            (a.textContent || '').trim() === '统计分析' && onScreen(a));
        if (!links.length) return false;
        links[0].setAttribute('data-kimi-click', '1');
        return true;
    """), "点击统计分析", timeout=15):
        return
    if not run_click(page, locate(r"""
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
    """), "点击就诊历史记录", timeout=15):
        return
    time.sleep(2)

    # 3) 点全院（jzls=1，本人范围会漏掉其他医生开的单）
    run_click(page, "() => {" + JZLS_PANEL_JS + r"""
        const pel = panelEl();
        if (!pel) return false;
        const target = Array.from(pel.querySelectorAll('input[type="radio"][name="jzls"]'))
            .find(r => r.value === '1' && onScreen(r));
        if (!target) return false;
        target.setAttribute('data-kimi-click', '1');
        return true;
    }""", "选择全院", verify_js="() => {" + JZLS_PANEL_JS + r"""
        const pel = panelEl();
        if (!pel) return false;
        const target = Array.from(pel.querySelectorAll('input[type="radio"][name="jzls"]'))
            .find(r => r.value === '1' && onScreen(r));
        return target ? target.checked : false;
    }""")

    # 4) 挂号时间 = 整个查询月份（日期必须用 Ext API setValue 写入）
    run_step(page, "() => {" + JZLS_PANEL_JS + f"""
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
    }}""", "填写挂号时间范围")

    # 4.1) 查询方式切到"姓名"（默认"门诊号码"，此时面板里没有 input[name=BRXM]）
    if not pick_query_field_brxm(page):
        return

    # 5) 按姓名逐个搜索爬取就诊记录（Ctrl+A 全选替换，不用手动清空上一个姓名）
    visits_by_name = {}
    for i, name in enumerate(names, 1):
        desc = f"[{i}/{len(names)}] {name}"
        if not type_field(page, "BRXM", name, f"姓名={name}"):
            continue
        if search_and_wait(page, f"搜索 {desc}", panel_js=JZLS_PANEL_JS):
            rows = scrape_all(page, f"爬取 {desc}", scrape_js=JZLS_SCRAPE_JS)
            if rows is not None:
                visits_by_name[name] = rows

    # 6) 收费行匹配就诊记录（姓名相同 + 挂号日期 = 开单或收费日期）
    matched, unmatched = [], 0
    for r in fee_rows:
        name = str(r.get("BRXM") or "").strip()
        dates = {str(r.get(k) or "")[:10] for k in ("KDRQ", "SFRQ")} - {""}
        cands = [v for v in visits_by_name.get(name, [])
                 if str(v.get("GHSJ") or "")[:10] in dates]
        if cands:
            r["_VISITS"] = cands
            matched.extend(cands)
        else:
            unmatched += 1
            r["_JYJG"] = "未匹配到就诊记录"
            r["_NL"] = r["_DH"] = r["_PD"] = ""
    if unmatched:
        print(f"  [警告] {unmatched} 行收费未匹配到就诊记录（姓名+日期对不上）")

    # 7) 批量读检验结果，提取 HCG 并判定；同一就诊记录只读一次
    uniq = {str(v.get("GHXH")): v for v in matched}
    if uniq:
        slim = [{k: v.get(k) for k in ("GHXH", "BRBH", "JZXH", "JGID", "GHSJ")}
                for v in uniq.values()]
        try:
            records = page.evaluate(FETCH_RECORDS_JS, slim)
        except Exception as e:
            print(f"  [失败] 读检验结果异常 {e}")
            FAILED_STEPS.append("读检验结果")
            records = {}
        for ghxh, v in uniq.items():
            rec = records.get(ghxh) or {}
            v["_JYJG"], v["_PD"] = extract_hcg_result(rec.get("jy") or [])
            if rec.get("error") and not v["_JYJG"]:
                v["_JYJG"] = "检验结果读取失败:" + str(rec["error"])[:40]

    # 8) 回填到收费行：候选就诊里优先取能查到 HCG 结果的那条
    for r in fee_rows:
        cands = r.pop("_VISITS", [])
        if not cands:
            continue
        pick = next((v for v in cands if v.get("_JYJG")), cands[0])
        r["_NL"] = pick.get("CSNY") or ""
        r["_JYJG"] = pick.get("_JYJG") or "该次就诊无HCG检验结果"
        r["_PD"] = pick.get("_PD") or ""
        r["_EMPIID"] = pick.get("EMPIID") or ""

    # 9) 完整电话（store 里是脱敏值，走 MPI 档案接口）
    phones = fetch_phones(page, [r for r in fee_rows if r.get("_EMPIID")], "查电话")
    for r in fee_rows:
        c = phones.get(str(r.get("_EMPIID") or "")) or {}
        r["_DH"] = c.get("dh") or ""
    filled = sum(1 for r in fee_rows if r.get("_JYJG") and "未匹配" not in r["_JYJG"])
    print(f"  [成功] 患者信息收集完成：{len(fee_rows)} 行收费，"
          f"{filled} 行查到检验结果，{sum(1 for r in fee_rows if r.get('_DH'))} 行查到电话")


def safe_sheet_name(name, used):
    """把项目名变成合法的 Excel sheet 名：替换非法字符 \\ / ? * [ ] : ，
    最长 31 字符，重名时追加序号。"""
    s = re.sub(r'[\\/?*\[\]:]', "_", name).strip() or "结果"
    s = s[:31]
    base, i = s, 2
    while s in used:
        suffix = f"_{i}"
        s = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(s)
    return s


def save_excel(results, filename, desc):
    """把各项目的爬取结果写成多 sheet 的 xlsx 存到桌面。
    results: [(项目名, 行列表或None), ...]，None 表示该项目爬取失败，跳过。"""
    try:
        from openpyxl import Workbook
        wb = Workbook()
        wb.remove(wb.active)  # 删掉默认空 sheet，全部按项目建
        used = set()
        for item_name, rows in results:
            if rows is None:
                continue
            ws = wb.create_sheet(safe_sheet_name(item_name, used))
            ws.append([h for _, h in EXCEL_FIELDS])
            for row in rows:
                ws.append([row.get(k) if row.get(k) is not None else "" for k, _ in EXCEL_FIELDS])
        if not wb.sheetnames:
            print(f"  [失败] {desc}：没有任何项目的数据可写")
            FAILED_STEPS.append(desc + "(写Excel)")
            return
        path = os.path.join(DESKTOP, filename)
        wb.save(path)
        summary = "、".join(f"{n}{len(r)}条" for n, r in results if r is not None)
        print(f"  [成功] {desc}：{summary} → {path}")
    except Exception as e:
        print(f"  [失败] {desc}：写 Excel 出错 {e}")
        FAILED_STEPS.append(desc + "(写Excel)")


def print_rows(rows):
    """把结果行打印到控制台（表头 + 每行按字段排列）。"""
    if not rows:
        print("  （无数据行）")
        return
    headers = [h for _, h in FIELDS]
    print("  " + " | ".join(headers))
    print("  " + "-" * 60)
    for row in rows:
        cells = [str(row.get(k) if row.get(k) is not None else "") for k, _ in FIELDS]
        print("  " + " | ".join(cells))


def month_range(month_arg=""):
    """返回起止日期 ('yyyy-mm-01', 'yyyy-mm-月末')。
    与 fubao/monthly 脚本一致：month_arg 为空 → 上一个自然月；为 1-12 → 该年该月，
    月份大于当前月份时取上一年（如 1 月查去年 12 月）。
    另外兼容 'yyyy-mm' 写法（如 2026-05），方便直接指定年月。
    月末日期由 calendar.monthrange 算，28/29/30/31 天自动处理。"""
    now = datetime.now()
    if month_arg:
        if re.fullmatch(r"\d{4}-\d{1,2}", month_arg):  # yyyy-mm 写法
            year, month = map(int, month_arg.split("-"))
            if not 1 <= month <= 12:
                raise ValueError(f"月份必须是 1-12，收到: {month_arg!r}")
        else:
            month = int(month_arg)
            if not 1 <= month <= 12:
                raise ValueError(f"月份必须是 1-12，收到: {month_arg!r}")
            year = now.year - 1 if month > now.month else now.year
    else:
        year, month = now.year, now.month - 1
        if month == 0:
            month = 12
            year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"


def hcg_check():
    print("1. 开始启动...")
    # 月份参数：命令行第 1 个参数（面板输入框也是传到这里），留空默认上个月
    month_arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    try:
        start_date, end_date = month_range(month_arg)
    except ValueError as e:
        print(f"参数错误：{e}")
        sys.exit(1)
    print(f"   查询月份: {start_date[:7]}（{start_date} 至 {end_date}）")
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

        # ========== 1. 关闭所有已打开的"诊疗项目统计"标签页 ==========
        # 查询条件在服务端/会话层面粘滞，旧面板残留的筛选用空值清不掉，
        # 必须开全新面板才是干净状态。没有旧标签是正常情况，直接探测不报错
        print("5. 关闭旧的诊疗项目统计标签页...")
        closed = 0
        LOCATE_CLOSE = locate(r"""
            const tab = Array.from(document.querySelectorAll('li.x-mytab-strip-closable'))
                .find(li => (li.textContent || '').includes('诊疗项目统计') && onScreen(li));
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

        # ========== 2. 点击统计分析 → 诊疗项目统计（全新面板） ==========
        print("6. 点击统计分析...")
        run_click(page, locate(r"""
            const links = Array.from(document.querySelectorAll('a')).filter(a =>
                (a.textContent || '').trim() === '统计分析' && onScreen(a));
            if (!links.length) return false;
            links[0].setAttribute('data-kimi-click', '1');
            return true;
        """), "点击统计分析", timeout=15)

        print("7. 点击诊疗项目统计...")
        # 菜单项 LI id 为 CIC_module_IVC20（模块编码实测稳定）；兜底按文本匹配，
        # 但要排除已打开的标签页（x-mytab）里同名的那个
        run_click(page, locate(r"""
            let link = null;
            const li = document.getElementById('CIC_module_IVC20');
            if (li) link = li.querySelector('a') || li;
            if (!link || !onScreen(link)) {
                link = Array.from(document.querySelectorAll('a')).find(a =>
                    (a.textContent || '').trim() === '诊疗项目统计' && onScreen(a)
                    && !a.closest('.x-mytab-strip, [class*="x-mytab"]'));
            }
            if (!link || !onScreen(link)) return false;
            link.setAttribute('data-kimi-click', '1');
            return true;
        """), "点击诊疗项目统计", timeout=15)
        # 面板加载较慢，多等一会
        time.sleep(2)

        # ========== 3. 填写日期范围（整个循环共用，只填一次） ==========
        print(f"8. 填写时间范围: {start_date} 至 {end_date} ...")
        # 日期必须用 Ext API setValue 写入（直接改 DOM value 不会更新组件内部值，
        # 查询会拿旧日期）；本面板字段 name 是 KSSJ/JSSJ，组件 id 是 ext-comp-*
        # 不稳定的，按 name 定位后取 el.id 找组件
        run_step(page, "() => {" + PANEL_JS + f"""
            const pel = panelEl();
            if (!pel) return false;
            const setDate = (name, val) => {{
                const el = Array.from(pel.querySelectorAll('input[name="' + name + '"]')).find(onScreen);
                if (!el) return false;
                const c = Ext.getCmp(el.id);
                if (c && c.setValue) {{ c.setValue(val); return c.getRawValue() === val; }}
                el.value = val;
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return el.value === val;
            }};
            return setDate('KSSJ', {json.dumps(start_date)})
                && setDate('JSSJ', {json.dumps(end_date)});
        }}""", "填写时间范围")

        # ========== 4. 循环每个项目名：输入 → 查询 → 爬取 ==========
        results = []  # [(项目名, 行列表或None)]
        for idx, item_name in enumerate(ITEM_NAMES, 1):
            print(f"9.{idx} 项目「{item_name}」...")
            if not type_field(page, "XMMC", item_name, f"项目名称={item_name}"):
                results.append((item_name, None))
                continue
            # search_and_wait 内部等 store load 钩子触发才返回，
            # 保证爬到的是本项目的新数据，不是上一个项目的旧结果
            if search_and_wait(page, f"查询 {item_name}"):
                rows = scrape_all(page, f"爬取 {item_name}")
            else:
                rows = None
            results.append((item_name, rows))
            if rows is not None:
                matched = sorted({str(r.get("FYMC") or "") for r in rows})
                print(f"  [明细] 「{item_name}」共 {len(rows)} 行，"
                      f"实际匹配项目名：{'、'.join(matched) if matched else '（无）'}")
                if rows:
                    print_rows(rows)
                else:
                    # 0 行不一定是脚本问题，明确提示人工核对条件
                    print("  [提示] 查询结果 0 行：条件和面板填写均已校验成功，"
                          "请人工确认该时间段内确实无此项目数据")

        # ========== 5. 关联就诊历史记录，收集患者年龄/电话/检查结果 ==========
        collect_patient_info(page, results, start_date, end_date)

        # ========== 6. 导出 Excel（一个文件，每项目一个 sheet） ==========
        print("11. 导出 Excel...")
        save_excel(results, f"血尿HCG检查_{start_date}至{end_date}.xlsx", "血尿HCG检查结果")

        # ========== 汇总 ==========
        print("=" * 40)
        if FAILED_STEPS:
            print(f"完成，但有 {len(FAILED_STEPS)} 个步骤未成功，请手动检查：")
            for s in FAILED_STEPS:
                print(f"  - {s}")
        else:
            print("全部步骤执行成功！")


if __name__ == "__main__":
    hcg_check()
