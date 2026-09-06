#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""妇保病历检查脚本（全院）

流程（在 auto_fill_monthly_check.py 基础上改：本人 → 全院，去掉病历未完善部分）：
1. 关闭所有已打开的"就诊历史记录"标签页（查询条件在服务端是粘滞的，
   旧面板里残留的筛选清不掉，必须开全新面板才是干净状态）
2. 点击"统计分析" → "就诊历史记录"（打开全新面板）→ 点"全院"（jzls=1）
3. 挂号时间填入起止日期（命令行参数指定月份，如 `python auto_fill_fubao_check.py 7`
   → 当年 7 月整月；留空默认本月 1 号至今天）
4. 女性 14-49 岁：键盘输入最小年龄14、最大年龄49
   4.1 下拉选"填写末次月经=未填写" → 搜索爬取
       → 妇保-末次月经未填写_女14-49_YYYY-MM.xlsx
   4.2 下拉选"填写末次月经=全部、末次月经延迟35天=已延迟" → 搜索爬取，
       逐条调 loadClinicInfo 接口读取病历正文（主诉/现病史/辅助检查/体格检查/既往史）
       和检验结果（jy_data，含血清β-HCG数值），按内容分类：
       表一 已怀孕：血HCG≥10阳性/5-10弱阳性（检验结果优先）；胎心音/宫高/停经N周/
           孕N周/尿HCG阳性 等文本证据
           → 妇保-月经延迟-表一已怀孕_YYYY-MM.xlsx
       其余（无说明 + 有说明）合并输出，靠"判定依据"列区分：
           有说明指标如 血HCG<5阴性/否认怀孕/拒查HCG/已绝经/哺乳期/尿HCG阴性
           → 妇保-月经延迟_YYYY-MM.xlsx
       （分类规则用 2026-07 全院 20 条真实延迟病历逐一验证过）
   4.3 清空年龄/末次月经/就诊类型筛选，查询字段选择器选「诊断查询」，
       逐个输入关键词（月经延长/停经/孕/妊娠/胚胎/试管/黄体/流产/先兆/人流/药流/辅助），
       结果合并去重 → 妇保-诊断含孕_YYYY-MM.xlsx

关键机制（继承自 monthly 脚本，都是实测趟出来的）：
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
- 全院数据量大，翻页上限放宽到 400 页（约 1 万行）
- 依赖 openpyxl 写 Excel（pip install --user openpyxl）
- 失败的步骤只警告不中断，脚本末尾统一汇总，方便人工补操作
"""

from playwright.sync_api import sync_playwright
from datetime import date, datetime
import calendar
import json
import os
import re
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
        if (++guard > 400) return {error: '翻页次数超限', collected: Object.keys(seen).length};
    }
    return {total, rows: Object.values(seen)};
}"""

# 批量读取病历正文+检验结果：对每行调系统内部接口 loadClinicInfo（就诊历史记录
# 展开行用的就是它），type=5 时返回 jy_data（检验结果）/jc_data（检查结果）。
# 入参 clinicId/brid 必须传字符串（传数字后端报 Integer cannot be cast to String）；
# 必须带 jgid，否则不返回 jy_data；跨年数据要带 q_YEAR（病历按年分表）
# 返回 {GHXH: {text, mcyj, jy, error}}
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
            const b = j.ms_bcjl;
            if (!b) { out[key] = {text: "", mcyj: "", jy: [], error: "病历为空(code=" + res.code + ")"}; continue; }
            const parts = [];
            for (const k of ["ZSXX", "XBS", "FZJC", "TGJC", "MCYJDESC", "JWS"]) {
                if (b[k]) parts.push(String(b[k]));
            }
            out[key] = {text: parts.join("\n"), mcyj: b.MCYJ || "", jy: j.jy_data || [],
                        plan: (j.gljh || []).map(x => String(x.GLJH || '')).join('\n'),
                        sec: {ZSXX: b.ZSXX || "", XBS: b.XBS || "", FZJC: b.FZJC || "",
                              TGJC: b.TGJC || "", MCYJDESC: b.MCYJDESC || "", JWS: b.JWS || ""}};
        } catch (e) {
            out[key] = {text: "", mcyj: "", jy: [], error: String(e)};
        }
    }
    return out;
}"""

# ========== 月经延迟病历三分类规则（用 2026-07 全院 20 条真实延迟病历逐条验证） ==========
# 表一 已怀孕（确证）：以下任一命中即算。注意都要避开否认/拒查语境：
#   胎心音、宫高（产检查体）；尿HCG(+)/阳性、尿妊娠阳性；停经N周/孕N周（孕周表述）；
#   血HCG数值≥5（5-10弱阳，≥10阳性）；非否认语境的"怀孕/妊娠"
# 表三 有说明（排除怀孕或其他解释）：否认怀孕/妊娠、拒查HCG、已绝经/闭经、哺乳期、
#   尿HCG阴性、血HCG<5、排除怀孕
# 表二 无说明：以上都不命中（既往史模板里的"否认高血压…"等不算说明）
PREG_PATTERNS = [
    (r"胎心音", "胎心音"),
    (r"宫高", "宫高"),
    (r"尿\s*[Hh][Cc][Gg]\s*[（(]\s*\+", "尿HCG(+)"),
    (r"尿\s*[Hh][Cc][Gg]\s*阳性", "尿HCG阳性"),
    (r"尿妊娠.{0,4}阳性", "尿妊娠阳性"),
    (r"停经\s*\d+\s*\+?\s*周", "停经数周"),
    (r"孕\s*\d+\s*\+?\s*周", "孕周"),
]
PREG_WORD = re.compile(r"(?<!否认)(?<!排除)(?<!无)(?<!疑)(?:怀孕|妊娠|受孕)")
EXPLAIN_PATTERNS = [
    (r"否认怀孕", "否认怀孕"),
    (r"否认妊娠", "否认妊娠"),
    (r"拒.{0,4}[Hh][Cc][Gg]", "拒查HCG"),
    (r"已绝经|绝经\s*\d*\s*年|已闭经|闭经\s*\d*\s*年", "已绝经/闭经"),
    (r"哺乳期", "哺乳期"),
    (r"尿\s*[Hh][Cc][Gg]\s*阴性|尿\s*[Hh][Cc][Gg]\s*[（(]\s*-", "尿HCG阴性"),
    (r"尿妊娠.{0,4}阴性", "尿妊娠阴性"),
    (r"未怀孕|排除怀孕|排除妊娠|无怀孕", "排除怀孕"),
]


def classify_record(text, jy_data=None):
    """月经延迟病历分类，返回 (表号, 判定依据)。表1=已怀孕 表2=无说明 表3=有说明。
    检验结果（jy_data）是客观证据，优先于病历文本。"""
    # 0. 检验结果里的 HCG：数值≥5 → 表一（5-10 弱阳，≥10 阳性）；<5 → 表三（阴性说明）
    for item in jy_data or []:
        label = str(item.get("ITEMNAME") or "") + str(item.get("EXAMITEMNAME") or "")
        if "HCG" not in label.upper() and "绒毛膜" not in label:
            continue
        unit = item.get("RESULTUNIT") or ""
        try:
            val = float(str(item.get("TESTRESULT") or "").strip())
        except ValueError:
            # 数值缺失时看结果说明
            msg = str(item.get("RESULTMESSAGE") or "")
            if "阳" in msg:
                return 1, f"血HCG{msg}"
            if "阴" in msg:
                return 3, f"血HCG{msg}"
            continue
        if val >= 10:
            return 1, f"血HCG={val}{unit}(≥10阳性)"
        if val >= 5:
            return 1, f"血HCG={val}{unit}(5-10弱阳性)"
        return 3, f"血HCG={val}{unit}(<5阴性)"
    text = (text or "").strip()
    if not text:
        return 2, "病历无相关内容"
    # 血HCG数值：≥5 阳性→表一；<5 阴性→表三。跳过否认/拒查语境的 HCG 字样，
    # 数值要求带单位（mIU/ml 等），避免误吃"拒查HCG；20260728血常规"里的日期数字
    for m in re.finditer(r"[Hh][Cc][Gg]", text):
        before = text[max(0, m.start() - 5):m.start()]
        if re.search(r"[拒否未无阴]", before):
            continue
        nm = re.match(r"[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(?:mIU|MIU|miu|mIu|IU/L|iu/)",
                      text[m.end():m.end() + 20])
        if nm:
            val = float(nm.group(1))
            if val >= 5:
                return 1, f"血HCG={nm.group(1)}"
            return 3, f"血HCG={nm.group(1)}(<5阴性)"
    # 表一：怀孕确证
    for pat, label in PREG_PATTERNS:
        m = re.search(pat, text)
        if m:
            return 1, f"{label}({m.group(0)})"
    m = PREG_WORD.search(text)
    if m:
        s = text[max(0, m.start() - 6):m.end() + 6].replace("\n", " ")
        return 1, f"提到{m.group(0)}(…{s}…)"
    # 表三：有说明
    for pat, label in EXPLAIN_PATTERNS:
        m = re.search(pat, text)
        if m:
            return 3, f"{label}({m.group(0)})"
    return 2, "未见相关说明"


def fetch_and_classify(page, rows, desc):
    """批量读取每行的病历正文+检验结果并分类，返回 {表号: [行...]}。
    每行附加 _MCYJ（末次月经）、_JYJG（检验结果摘要）、_EVIDENCE（判定依据）供输出。"""
    tables = {1: [], 2: [], 3: []}
    if not rows:
        return tables
    slim = [{k: r.get(k) for k in ("GHXH", "BRBH", "JZXH", "JGID", "GHSJ")} for r in rows]
    try:
        records = page.evaluate(FETCH_RECORDS_JS, slim)
    except Exception as e:
        print(f"  [失败] {desc}：读取病历正文异常 {e}")
        FAILED_STEPS.append(desc + "(读病历)")
        for r in rows:
            r["_EVIDENCE"] = "病历读取失败"
            tables[2].append(r)
        return tables
    read_fail = 0
    for r in rows:
        rec = records.get(str(r.get("GHXH"))) or {}
        r["_MCYJ"] = rec.get("mcyj") or ""
        # 检验结果摘要列：项目=结果单位
        jy = rec.get("jy") or []
        r["_JYJG"] = "；".join(
            f"{it.get('ITEMNAME') or ''}={it.get('TESTRESULT') or ''}{it.get('RESULTUNIT') or ''}"
            for it in jy if it.get("TESTRESULT"))[:200]
        if rec.get("error"):
            read_fail += 1
            r["_EVIDENCE"] = "病历读取失败:" + str(rec["error"])[:50]
            tables[2].append(r)
            continue
        no, evidence = classify_record(rec.get("text", ""), jy)
        r["_EVIDENCE"] = evidence
        tables[no].append(r)
    print(f"  [成功] {desc}：表一已怀孕 {len(tables[1])} 条，表二无说明 {len(tables[2])} 条，"
          f"表三有说明 {len(tables[3])} 条" + (f"（{read_fail} 条病历读取失败）" if read_fail else ""))
    if read_fail:
        FAILED_STEPS.append(f"{desc}({read_fail}条病历读取失败)")
    return tables


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


def clear_field(page, field_name, desc):
    """清空文本输入框：Ext setValue('')，组件级操作，不要求元素可见
    （搜索/读完病历后表单行可能被收起，真实键盘定位会找不到）。"""
    ok = run_step(page, "() => {" + PANEL_JS + f"""
        let found = false;
        Ext.ComponentMgr.all.each(c => {{
            if (found) return;
            if (c && c.el && c.el.dom && (c.name === {json.dumps(field_name)} || c.el.dom.name === {json.dumps(field_name)})
                && c.setValue) {{
                c.setValue('');
                found = String(c.getValue ? c.getValue() : '') === '';
            }}
        }});
        return found;
    }}""", desc, timeout=5, quiet=True)
    if ok:
        print(f"  [成功] {desc}")
    else:
        print(f"  [失败] {desc}")
        FAILED_STEPS.append(desc)
    return ok


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


def save_excel(rows, filename, desc, fields=None):
    """把爬取的行写成 xlsx 存到桌面。fields 可追加分类脚本算出来的列（末次月经/判定依据）。"""
    if rows is None:
        return
    fields = fields or FIELDS
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "名单"
        ws.append([h for _, h in fields])
        for row in rows:
            ws.append([row.get(k) if row.get(k) is not None else "" for k, _ in fields])
        path = os.path.join(DESKTOP, filename)
        wb.save(path)
        print(f"  [成功] {desc}：{len(rows)} 条 → {path}")
    except Exception as e:
        print(f"  [失败] {desc}：写 Excel 出错 {e}")
        FAILED_STEPS.append(desc + "(写Excel)")


# ========== 诊断含孕病历书写质控规则 ==========
def parse_lmp(text):
    """从末次月经字段/文本里解析日期，支持 2026-02-19、2026/02/19、2026年2月19日。"""
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def extract_weeks(text):
    """提取孕周：孕28周 / 孕28+3周 / 停经28周 都算。"""
    m = re.search(r"(?:孕|停经)\s*(\d{1,2})\s*(?:\+\s*\d+)?\s*周", text or "")
    return int(m.group(1)) if m else None


# 宫底位置大致刻度（用户给的孕月对照表换算）：1盆腔/耻骨上 2脐耻之间 3脐下
# 4平脐 5脐上 6脐剑之间 7剑突下。长词先匹配（"脐剑之间"先于"脐上"）
FUNDUS_PATTERNS = [
    (r"脐.{0,2}剑突之间|脐剑之间", 6, "脐与剑突之间"),
    (r"剑突下", 7, "剑突下"),
    (r"脐上", 5, "脐上"),
    (r"平脐|脐平", 4, "平脐"),
    (r"脐下", 3, "脐下"),
    (r"脐.{0,2}耻.{0,3}之间", 2, "脐耻之间"),
    (r"耻骨联合上|盆腔", 1, "耻骨联合上/盆腔"),
]


def fundus_expected(wk):
    """该孕周宫底位置的合理刻度集合（按用户给的孕月对照表）。"""
    if wk is None or wk < 16:
        return None
    if wk <= 19:
        return {1, 2, 3}
    if wk <= 23:
        return {3}
    if wk <= 27:
        return {4, 5}
    if wk <= 31:
        return {5, 6}
    if wk <= 35:
        return {6}
    if wk <= 39:
        return {7}
    return {6}


def qc_pregnancy_record(row, rec):
    """一份怀孕病历的书写质控，返回 dict（各检查项结果+结论）。
    检查项：1.孕周一致性（末次月经计算 vs 主诉 vs 初步诊断）
            2.转诊记录（超范围产检/诊疗必须体现转诊上级医院）
            3.孕周≥16周：胎心音记录；宫底/腹部描述符合孕周"""
    sec = rec.get("sec") or {}
    zsxx, tgjc = sec.get("ZSXX", ""), sec.get("TGJC", "")
    text = rec.get("text", "")
    out = {}
    # ---- 1. 孕周一致性 ----
    lmp = parse_lmp(rec.get("mcyj")) or parse_lmp(sec.get("MCYJDESC")) or parse_lmp(text)
    out["末次月经"] = lmp.strftime("%Y-%m-%d") if lmp else ""
    visit = parse_lmp(str(row.get("GHSJ") or ""))
    calc_wk = ((visit - lmp).days // 7) if (lmp and visit) else None
    zs_wk = extract_weeks(zsxx)
    zd_wk = extract_weeks(str(row.get("ZYZD") or ""))
    out["计算孕周"] = f"{calc_wk}周" if calc_wk is not None else ""
    out["主诉孕周"] = f"{zs_wk}周" if zs_wk is not None else ""
    out["诊断孕周"] = f"{zd_wk}周" if zd_wk is not None else ""
    wks = [w for w in (calc_wk, zs_wk, zd_wk) if w is not None]
    if len(wks) < 2:
        out["孕周核对"] = "需复核(信息不全)"
    elif max(wks) - min(wks) == 0:
        out["孕周核对"] = "一致"
    elif max(wks) - min(wks) == 1:
        out["孕周核对"] = "需复核(相差1周)"
    else:
        out["孕周核对"] = "不合格(孕周不一致)"
    # ---- 2. 转诊记录（管理计划在接口的 gljh 字段，不在病历正文里） ----
    m = re.search(r"转诊|转上级|上级医院|转院", text + "\n" + (rec.get("plan") or ""))
    out["转诊记录"] = f"有({m.group(0)})" if m else "不合格(无转诊记录)"
    # ---- 3. 孕周≥16周：胎心音 + 宫底/腹部描述 ----
    wk = calc_wk or zd_wk or zs_wk
    if wk is None or wk < 16:
        out["胎心音"] = "孕周<16，不要求"
        out["宫底描述"] = "孕周<16，不要求"
    else:
        exam = tgjc or text
        m = re.search(r"胎心[音率]?\s*\d+|胎心[音率]?[:：]?\s*\d+|胎心[音率]", exam)
        if m:
            out["胎心音"] = f"已记录({m.group(0).strip()})"
        else:
            m2 = re.search(r"未听清|未闻及|听不到|拒绝|拒听", exam)
            if m2 and re.search(r"建议|监测|上级", exam):
                out["胎心音"] = f"有说明({m2.group(0)})"
            elif m2:
                out["胎心音"] = f"需复核({m2.group(0)}但无建议)"
            else:
                out["胎心音"] = "不合格(≥16周无胎心音记录)"
        pos = None
        for pat, lv, label in FUNDUS_PATTERNS:
            if re.search(pat, exam):
                pos = (lv, label)
                break
        if not pos:
            if re.search(r"腹膨隆|宫高|宫底", exam):
                # "腹膨隆如孕月"这类定性描述也算有腹部描述（用户确认过这种写法可以）
                out["宫底描述"] = "有描述(腹膨隆/宫高，未量化位置)"
            else:
                out["宫底描述"] = "不合格(≥16周无宫底/腹部描述)"
        else:
            exp = fundus_expected(wk)
            if exp and pos[0] in exp:
                out["宫底描述"] = f"符合孕周({pos[1]})"
            elif exp:
                out["宫底描述"] = f"需复核(宫底{pos[1]}与孕{wk}周不符)"
            else:
                out["宫底描述"] = f"有描述({pos[1]})"
    # ---- 结论 ----
    vals = [out["孕周核对"], out["转诊记录"], out["胎心音"], out["宫底描述"]]
    if any(v.startswith("不合格") for v in vals):
        out["结论"] = "不合格"
    elif any(v.startswith("需复核") for v in vals):
        out["结论"] = "需复核"
    else:
        out["结论"] = "合格"
    return out


QC_FIELDS = [("GHSJ", "挂号时间"), ("YSDM_text", "就诊医生"), ("BRXM", "姓名"),
             ("ZYZD", "病人诊断"), ("MZHM", "门诊号码"),
             ("_Q_LMP", "末次月经"), ("_Q_CALC", "计算孕周"), ("_Q_ZS", "主诉孕周"),
             ("_Q_ZD", "诊断孕周"), ("_Q_WEEK", "孕周核对"), ("_Q_REFER", "转诊记录"),
             ("_Q_FHR", "胎心音"), ("_Q_FUNDUS", "宫底描述"), ("_Q_RESULT", "结论")]


def qc_pregnancy_records(page, rows, desc):
    """对诊断含孕名单逐条读病历正文做书写质控，返回标注后的行。"""
    slim = [{k: r.get(k) for k in ("GHXH", "BRBH", "JZXH", "JGID", "GHSJ")} for r in rows]
    try:
        records = page.evaluate(FETCH_RECORDS_JS, slim)
    except Exception as e:
        print(f"  [失败] {desc}：读取病历正文异常 {e}")
        FAILED_STEPS.append(desc + "(读病历)")
        return None
    ok_cnt = 0
    for r in rows:
        rec = records.get(str(r.get("GHXH"))) or {}
        if rec.get("error"):
            qc = {"末次月经": "", "计算孕周": "", "主诉孕周": "", "诊断孕周": "",
                  "孕周核对": "需复核(病历读取失败)", "转诊记录": "", "胎心音": "",
                  "宫底描述": "", "结论": "需复核"}
        else:
            qc = qc_pregnancy_record(r, rec)
        r["_Q_LMP"] = qc["末次月经"]
        r["_Q_CALC"] = qc["计算孕周"]
        r["_Q_ZS"] = qc["主诉孕周"]
        r["_Q_ZD"] = qc["诊断孕周"]
        r["_Q_WEEK"] = qc["孕周核对"]
        r["_Q_REFER"] = qc["转诊记录"]
        r["_Q_FHR"] = qc["胎心音"]
        r["_Q_FUNDUS"] = qc["宫底描述"]
        r["_Q_RESULT"] = qc["结论"]
        if qc["结论"] == "合格":
            ok_cnt += 1
    n_bad = sum(1 for r in rows if r["_Q_RESULT"] == "不合格")
    n_check = sum(1 for r in rows if r["_Q_RESULT"] == "需复核")
    print(f"  [成功] {desc}：合格 {ok_cnt} 条，需复核 {n_check} 条，不合格 {n_bad} 条")
    return rows


# 延迟分类三表的输出列：基础列 + 末次月经 + 检验结果 + 判定依据
DELAY_FIELDS = FIELDS + [("_MCYJ", "末次月经"), ("_JYJG", "检验结果"), ("_EVIDENCE", "判定依据")]


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


def fubao_check():
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

        # ========== 3. 点击全院（活动面板内，jzls=1） ==========
        print("8. 点击全院...")
        run_click(page, "() => {" + PANEL_JS + r"""
            const pel = panelEl();
            if (!pel) return false;
            const target = Array.from(pel.querySelectorAll('input[type="radio"][name="jzls"]'))
                .find(r => r.value === '1' && onScreen(r));
            if (!target) return false;
            target.setAttribute('data-kimi-click', '1');
            return true;
        }""", "选择全院", verify_js="() => {" + PANEL_JS + r"""
            const pel = panelEl();
            if (!pel) return false;
            const target = Array.from(pel.querySelectorAll('input[type="radio"][name="jzls"]'))
                .find(r => r.value === '1' && onScreen(r));
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

        # ========== 5. 女性 14-49 岁，末次月经两种组合 ==========
        # 年龄用真实键盘输入；下拉框用 UI 真实操作（点箭头→点选项）。
        # 末次月经字段只对女性患者存在，输出时再按性别=女过滤一次兜底
        print("10. 填写年龄范围 14-49...")
        age_ok = type_field(page, "minYear", "14", "最小年龄=14")
        age_ok = type_field(page, "maxYear", "49", "最大年龄=49") and age_ok

        print("11. 末次月经未填写（全院 女14-49）...")
        combo_ok = pick_combo(page, "hasMCYJ", "未填写", "填写末次月经=未填写")
        if age_ok and combo_ok and search_and_wait(page, "搜索 末次月经未填写"):
            rows = scrape_all(page, "爬取 末次月经未填写")
            if rows is not None:
                female = [r for r in rows if str(r.get("BRXB_text") or "").strip() == "女"]
                save_excel(female, f"妇保-末次月经未填写_女14-49_{ym}.xlsx", "末次月经未填写名单")

        print("12. 末次月经已延迟35天（全院 女14-49，读病历正文+检验结果分类）...")
        combo_ok = pick_combo(page, "hasMCYJ", "全部", "填写末次月经=全部")
        combo_ok = pick_combo(page, "hasMCYJYC", "已延迟", "末次月经延迟35天=已延迟") and combo_ok
        if combo_ok and search_and_wait(page, "搜索 末次月经已延迟35天"):
            rows = scrape_all(page, "爬取 末次月经已延迟35天")
            if rows is not None:
                female = [r for r in rows if str(r.get("BRXB_text") or "").strip() == "女"]
                tables = fetch_and_classify(page, female, "延迟病历分类")
                save_excel(tables[1], f"妇保-月经延迟-表一已怀孕_{ym}.xlsx",
                           "表一 已怀孕", fields=DELAY_FIELDS)
                # 表二（无说明）和表三（有说明）合并输出，靠"判定依据"列区分
                save_excel(tables[2] + tables[3], f"妇保-月经延迟_{ym}.xlsx",
                           "表二+表三 合并名单", fields=DELAY_FIELDS)

        # ========== 6. 诊断查询=孕（先清空前几步用过的筛选条件） ==========
        print("13. 诊断查询=孕（清空年龄/末次月经/就诊类型筛选）...")
        # 第12步读病历正文可能让活动标签页跑偏，先把「就诊历史记录」标签页激活回来
        run_click(page, locate(r"""
            const tab = Array.from(document.querySelectorAll('li[class*="x-mytab-strip"]'))
                .find(li => (li.textContent || '').includes('就诊历史记录') && onScreen(li));
            if (!tab) return false;
            tab.setAttribute('data-kimi-click', '1');
            return true;
        """), "激活就诊历史记录标签页", timeout=8, quiet=True, record=False)
        time.sleep(1)
        # 前面分类读取病历后表单可能处于过渡状态，先 Escape 收弹层再等一下
        page.keyboard.press("Escape")
        time.sleep(1)
        # 年龄清空用 Ext setValue（第12步后表单行可能不可见，键盘定位会失败）；
        # 三个下拉恢复"全部"（这个系统里"全部"就是空值，getValue 返回 ''）
        clr_ok = clear_field(page, "minYear", "清空最小年龄")
        clr_ok = clear_field(page, "maxYear", "清空最大年龄") and clr_ok
        clr_ok = pick_combo(page, "hasMCYJ", "全部", "填写末次月经=全部") and clr_ok
        clr_ok = pick_combo(page, "hasMCYJYC", "全部", "末次月经延迟35天=全部") and clr_ok
        clr_ok = pick_combo(page, "jzlx", "全部", "就诊类型=全部") and clr_ok
        # 「诊断查询」是查询字段选择器的一个选项：选择器没有语义 name，
        # 用"值输入框（门诊号码=MZHM/诊断查询=ZYZD）左边紧邻的下拉"定位它，选「诊断查询」
        zd_ok = run_click(page, "() => {" + PANEL_JS + r"""
            const pel = panelEl();
            if (!pel) return false;
            const valInp = Array.from(pel.querySelectorAll('input'))
                .find(i => ['MZHM', 'ZYZD'].includes(i.name) && onScreen(i));
            if (!valInp) return false;
            let combo = null;
            Ext.ComponentMgr.all.each(c => {
                if (combo) return;
                if (c instanceof Ext.form.ComboBox && c.el && c.el.dom && pel.contains(c.el.dom)) {
                    const rc = c.el.dom.getBoundingClientRect();
                    const vr = valInp.getBoundingClientRect();
                    if (Math.abs(rc.y - vr.y) < 20 && rc.x < vr.x && vr.x - (rc.x + rc.width) < 150) combo = c;
                }
            });
            if (!combo) return false;
            const wrap = combo.el.dom.closest('.x-form-field-wrap');
            const trig = wrap ? wrap.querySelector('.x-form-trigger') : null;
            if (!trig) return false;
            trig.setAttribute('data-kimi-click', '1');
            return true;
        }""", "展开查询字段选择器", timeout=10, quiet=True, record=False)
        if zd_ok:
            time.sleep(0.8)
            zd_ok = run_click(page, locate(r"""
                const lists = Array.from(document.querySelectorAll('.x-combo-list')).filter(l => {
                    const st = window.getComputedStyle(l);
                    const r = l.getBoundingClientRect();
                    return st.display !== 'none' && r.width > 0 && r.x > -100;
                });
                const mine = lists.find(l => Array.from(l.querySelectorAll('.x-combo-list-item'))
                    .some(i => (i.textContent || '').trim() === '诊断查询'));
                if (!mine) return false;
                const item = Array.from(mine.querySelectorAll('.x-combo-list-item'))
                    .find(i => (i.textContent || '').trim() === '诊断查询');
                if (!item) return false;
                item.setAttribute('data-kimi-click', '1');
                return true;
            """), "选择诊断查询", timeout=5, quiet=True, record=False)
            time.sleep(0.5)
        # 选择器切到「诊断查询」后，后面的输入框 name 变成 ZYZD。
        # 多个关键词逐个查询，结果合并去重（GHXH 挂号序号）后存同一个 Excel
        KEYWORDS = ["月经延长", "停经", "孕", "妊娠", "胚胎", "试管",
                    "黄体", "流产", "先兆", "人流", "药流", "辅助"]
        all_rows, seen_gxh = [], set()
        if clr_ok and zd_ok:
            for kw in KEYWORDS:
                if not type_field(page, "ZYZD", kw, f"诊断查询={kw}"):
                    continue
                if not search_and_wait(page, f"搜索 诊断含{kw}"):
                    continue
                rows = scrape_all(page, f"爬取 诊断含{kw}")
                if not rows:
                    continue
                for r in rows:
                    key = str(r.get("GHXH") or "") or (str(r.get("MZHM") or "") + str(r.get("GHSJ") or ""))
                    if key and key not in seen_gxh:
                        seen_gxh.add(key)
                        all_rows.append(r)
            save_excel(all_rows, f"妇保-诊断含孕_{ym}.xlsx",
                       f"诊断关键词筛查名单（{len(KEYWORDS)}个关键词合并去重）")

        # ========== 7. 诊断含孕病历书写质控 ==========
        # 对名单里每份病历：核对孕周一致性（末次月经/主诉/初步诊断）、转诊记录、
        # ≥16周的胎心音和宫底描述，逐项给结论
        if all_rows:
            print("14. 病历书写质控（诊断含孕名单，读病历正文逐项核对）...")
            qc_rows = qc_pregnancy_records(page, all_rows, "病历书写质控")
            if qc_rows:
                save_excel(qc_rows, f"妇保-诊断含孕-病历质控_{ym}.xlsx",
                           "病历质控名单", fields=QC_FIELDS)

        # ========== 汇总 ==========
        print("=" * 40)
        if FAILED_STEPS:
            print(f"完成，但有 {len(FAILED_STEPS)} 个步骤未成功，请手动检查：")
            for s in FAILED_STEPS:
                print(f"  - {s}")
        else:
            print("全部步骤执行成功！")


if __name__ == "__main__":
    fubao_check()
