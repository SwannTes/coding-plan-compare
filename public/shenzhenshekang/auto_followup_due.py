#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""产前随访批量登记（第1-7次到期）

扫"产前随访跟踪"全部页：第1~7次随访列中，红色"待跟踪"角标且截止日期距今
<=7 天（含已过期）的单元格，逐个点击登记并保存：

  第1次13周前 : 方式=电话，早孕期文案（NT/唐筛/无创/叶酸），本次随访完成=是
  第2次16-20周: 方式=短信，中孕期文案（中唐/无创减免/预约四维/家庭医生），=是
  第3次21-24周: 方式=短信，四维彩超+OGTT 文案，本次随访完成=是
  第4次25-28周: 方式=短信，内容同第3次文案，本次随访完成=是
  第5次29-32周: 方式=电话，晚孕期文案（自数胎动/预防早产），=是
  第6次33-36周: 方式=短信，晚孕期文案，本次随访完成=是
  第7次37-40周: 方式=短信，近足月文案，本次随访完成=否，需要继续跟踪

用法：
  python auto_followup_due.py          # 扫全部页，登记到期随访
  python auto_followup_due.py setdate  # 只刷新"发现日期"筛选，不登记
  python auto_followup_due.py bjh      # 收集近一年未分娩孕妇名单（Excel存桌面）；
                                       # 无保健号的去 fyweb 初检查询，初检日期晚于
                                       # 末次月经才认定本孕次已初检，回填保健号到协同系统

登记前会先把"发现日期"刷成 去年今天~今天 并点查询（留空会查不全）。
前置：先运行 慢阻肺问卷/启动调试Chrome并打开网址.py 启动调试 Chrome。
平台未登录时会自动打开登录页等你手动登录（SSO 会话保留在 C:\\ChromeDebug，
一般只需登录一次）。

注意：平台点"孕产妇协同管理信息系统"后新标签页先落在 loginHis（SSO token
交接页），实测只要有 CDP 客户端连着它就一直不跳转，彻底断开连接后约 30 秒
内才跳转——所以脚本点完会主动断开、每 20 秒重连检查一次。
"""

from playwright.sync_api import sync_playwright
import os
import sys
import time
from datetime import date, datetime

# 直接双击运行时控制台是 GBK，✓/✗ 等符号打不出来会崩，换成可替换模式
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(errors="replace")
    except Exception:
        pass

FAILED_STEPS = []

UDRHIP_MARK = "/udrhip/"                 # 孕产妇协同管理信息系统
PORTAL_HOST = "172.17.9.215"             # 龙岗区全民健康信息平台
LOGIN_URL = "http://172.17.9.215:10041/udaam-ui/login"
LOGIN_WAIT = 180                         # 等手动登录的最长秒数

DUE_DAYS = 7  # 距截止日期 <= 7 天（含已过期）就登记

TEXT_1ST = ("建议完善早孕相关检查及免费项目，嘱定期产检，完善NT，唐筛，无创DNA检查，"
            "免费领取叶酸至孕12周，均衡营养，预防流产，不适随诊。")
TEXT_2ND = ("您好，因您就诊及居住地等原因，我们麓园社康中心将负责您孕期随访及健康管理。"
            "您目前为中孕期，当前孕周需完善的检查包括中唐及无创基因检测，"
            "其中21、18、13-三体综合征基因筛查（外周血高通量基因检测）（12周-22周+6天）："
            "政府减免300元，剩余部分可生育保险或者个人自费支付。"
            "当前孕周注意提前预约四维彩超(21-24周)。"
            "饮食上注意补铁补钙，保持心情愉快，合理运动，控制体重增长，每月产检1次，"
            "产检后请及时到产科前台录入产检信息。"
            "如出现腹痛、阴道流血等异常情况，及时就诊。"
            "如您未签约家庭医生，可携带身份证或社保卡到本社康中心签约，"
            "获取免费家庭医生全生命周期管理。"
            "本社康中心电话28904167，如有疑问可致电，也请注意接听本单位来电！")
TEXT_4TH = ("嘱加强营养，完善四维彩超、OGTT，嘱定期产检，听诊胎心音，"
            "补铁补钙，保持心情愉快，合理运动，监测血压，不适随诊。")
TEXT_MID = ("嘱定期产检，自数胎动计数，补铁补钙，保持心情愉快，合理运动，"
            "控制体重增长，监测血压，不适随诊，预防早产。")
TEXT_TERM = ("当前孕周近足月，饮食上注意补铁补钙，保持心情愉快，合理运动，"
             "控制体重增长，自行监测胎动情况，初产妇出现规律宫缩"
             "（阵痛每隔5-6分钟，阵痛持续30秒以上）、经产妇阵痛间隔10-15分钟，"
             "或出现阴道流水、流血等立即住院。")

# 列名 → 填写配置
CONFIG = {
    "第1次13周前":  {"method": "电话", "text": TEXT_1ST, "choice": "是"},
    "第2次16-20周": {"method": "短信", "text": TEXT_2ND, "choice": "是"},
    "第3次21-24周": {"method": "短信", "text": TEXT_4TH, "choice": "是"},
    "第4次25-28周": {"method": "短信", "text": TEXT_4TH, "choice": "是"},
    "第5次29-32周": {"method": "电话", "text": TEXT_MID, "choice": "是"},
    "第6次33-36周": {"method": "短信", "text": TEXT_MID, "choice": "是"},
    "第7次37-40周": {"method": "短信", "text": TEXT_TERM, "choice": "否，需要继续跟踪"},
}

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
"""

DLG_JS = BASE_JS + r"""
    const dlg = () => Array.from(document.querySelectorAll('.el-dialog')).find(onScreen);
    const dlgFormItem = label => {
        const d = dlg();
        if (!d) return null;
        return Array.from(d.querySelectorAll('.el-form-item'))
            .find(fi => (fi.querySelector('.el-form-item__label')?.textContent || '')
                         .trim().replace('*', '') === label);
    };
"""

SCAN_JS = r"""
(colNames) => {
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
    // 主表格（不含 fixed 列副本）
    const table = Array.from(document.querySelectorAll('.el-table__body-wrapper'))
        .find(w => onScreen(w) && !w.closest('.el-table__fixed, .el-table__fixed-right'));
    if (!table) return {rows: []};
    const headerTable = document.querySelector('.el-table__header-wrapper');
    const heads = Array.from(headerTable.querySelectorAll('th')).map(th => th.textContent.trim());
    const colIdx = colNames.map(n => heads.indexOf(n));
    const nameIdx = heads.indexOf('姓名');
    const out = [];
    for (const tr of table.querySelectorAll('tr')) {
        const tds = tr.querySelectorAll('td');
        if (tds.length < 25) continue;   // 表头 28 列含滚动条占位列，行只有 27 列
        const name = (tds[nameIdx] || {}).textContent?.trim();
        colNames.forEach((cn, k) => {
            const td = tds[colIdx[k]];
            if (!td) return;
            const delta = td.querySelector('.delta');
            if (!delta) return;   // 无角标=无需随访
            const color = delta.style.borderColor || '';
            if (!(color.includes('255, 0, 0') || color === 'red')) return;   // 只处理红=待跟踪
            const m = td.textContent.match(/\d{4}-\d{2}-\d{2}/);
            if (!m) return;
            out.push({name, col: cn, date: m[0]});
        });
    }
    return {rows: out};
}
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


def run_click(page, locate_js, desc, verify_js=None, timeout=10, quiet=False, record=True):
    """locate_js 找到目标时打 data-kimi-click="1" 标记并返回 true，点击由 Playwright
    locator 完成（真实鼠标事件）。"""
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
    return "() => {" + BASE_JS + body + "\n}"


def connect_chrome():
    """连接调试 Chrome，返回 (playwright实例, browser)；失败返回 (None, None)。"""
    try:
        p = sync_playwright().start()
        return p, p.chromium.connect_over_cdp("http://localhost:9222")
    except Exception:
        return None, None


def all_pages(browser):
    for ctx in browser.contexts:
        for pg in ctx.pages:
            yield pg


def find_portal(browser):
    """已登录的平台导航页。udaam-ui 下既有登录页也挂着协同系统应用本身，
    平台导航页在 regionportal-ui（如 172.17.9.215:8289），用排除 udaam-ui 来区分。"""
    for pg in all_pages(browser):
        try:
            if PORTAL_HOST in pg.url and "udaam-ui" not in pg.url:
                return pg
        except Exception:
            pass
    return None


def ensure_portal(browser):
    """找到已登录的平台页；没有就打开登录页，等手动登录。"""
    portal = find_portal(browser)
    if portal:
        print(f"  平台页已登录: {portal.url}")
        return portal
    print(f"  平台未登录，打开登录页: {LOGIN_URL}")
    pg = browser.contexts[0].new_page()
    pg.goto(LOGIN_URL)
    print(f"  请在浏览器里完成登录（最长等 {LOGIN_WAIT} 秒）...")
    deadline = time.time() + LOGIN_WAIT
    while time.time() < deadline:
        portal = find_portal(browser)
        if portal:
            print(f"  [成功] 登录完成: {portal.url}")
            return portal
        time.sleep(1)
    print("  [超时] 等待登录超时，请登录后重新运行本脚本")
    return None


def open_udrhip(p, browser, portal):
    """点"孕产妇协同管理信息系统"，等它在新标签页打开。已开着就直接复用。
    返回 (playwright实例, browser, 协同系统page)；失败返回 None。
    注意：中途会断开 CDP 再重连，返回的 p/browser 可能是新连接。"""
    for pg in all_pages(browser):
        try:
            if UDRHIP_MARK in pg.url and "login" not in pg.url:
                print(f"  协同系统页面已打开: {pg.url}")
                return p, browser, pg
        except Exception:
            pass
    if not run_click(portal, locate("""
        const el = Array.from(document.querySelectorAll('div.content_title, a, span, div'))
            .find(e => (e.textContent || '').trim() === '孕产妇协同管理信息系统' && onScreen(e));
        if (!el) return false;
        el.setAttribute('data-kimi-click', '1');
        return true;
    """), "点击孕产妇协同管理信息系统", timeout=15):
        return None
    # 新标签页先落在 loginHis（SSO token 交接页）。实测：只要有 CDP 客户端连着，
    # 交接页就一直不跳转（连 400 秒都不动）；彻底断开后约 30 秒内跳转完成。
    # 所以点完就断开，之后每 20 秒重连扫一次，最长约 3 分钟。
    print("  SSO 登录交接中（loginHis），断开连接等它跳转，每 20 秒检查一次...")
    p.stop()
    for round_no in range(1, 10):
        time.sleep(20)
        p, browser = connect_chrome()
        if not browser:
            print(f"  第{round_no}次检查：重连 Chrome 失败，20 秒后再试")
            continue
        for pg in all_pages(browser):
            try:
                if UDRHIP_MARK in pg.url and "login" not in pg.url:
                    print(f"  [成功] 协同系统已打开: {pg.url}")
                    return p, browser, pg
            except Exception:
                pass
        print(f"  第{round_no}次检查：还没跳过来，继续等")
        p.stop()
    print("  [超时] 协同系统页面未能打开，请手动进入后重试")
    FAILED_STEPS.append("打开协同系统")
    return None


def udrhip_goto_followup(page):
    """进入 妇保 → 产前跟踪 → 产前随访跟踪。"""
    try:
        if "PrenatalFollowUp" in page.url:
            print("  已在产前随访跟踪页面")
            return True
    except Exception:
        pass
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


def udrhip_set_discovery_date(page):
    """"发现日期"起止填成 去年今天~今天（留空是错的：系统按发现日期过滤，
    不填范围会查不全）。填完回读校验。"""
    today = date.today()
    start = today.replace(year=today.year - 1)
    values = [start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")]
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
            FAILED_STEPS.append("设置发现日期")
            return False
        page.locator('[data-kimi-click="1"]').first.click()
        page.evaluate("() => document.querySelectorAll('[data-kimi-click]').forEach(e => e.removeAttribute('data-kimi-click'))")
        page.keyboard.press("Control+a")
        page.keyboard.type(values[idx], delay=30)
        page.keyboard.press("Enter")   # Element UI 日期框回车确认
        page.keyboard.press("Tab")     # 收掉日期面板
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
    ok = vals == values
    print(f"  设置发现日期: {vals} {'✓' if ok else '✗ 应为 ' + str(values)}")
    if not ok:
        FAILED_STEPS.append("设置发现日期")
    return ok


def clear_other_filters(page):
    """清空「发现日期」以外的筛选输入框（姓名/身份证号码/保健号/任务日期/建册日期等），
    避免上次查询残留的值（比如姓名）把结果过滤掉。
    只动可编辑的文本框：只读下拉（状态）、分页跳转框不碰。"""
    cleared = page.evaluate("() => {" + BASE_JS + r"""
        // 发现日期的起止两个输入框要保留（刚填好）
        const keep = new Set();
        const label = Array.from(document.querySelectorAll('span, label, div'))
            .find(e => (e.textContent || '').trim() === '发现日期' && e.children.length === 0);
        if (label) {
            let box = label;
            for (let k = 0; k < 8 && box.parentElement; k++) {
                box = box.parentElement;
                const inputs = box.querySelectorAll('input[placeholder="开始时间"], input[placeholder="结束时间"]');
                if (inputs.length === 2) { inputs.forEach(i => keep.add(i)); break; }
            }
        }
        // Vue/Element UI：用原生 setter 改值再发事件，组件状态才会同步
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        const names = [];
        document.querySelectorAll('input').forEach(inp => {
            if (keep.has(inp) || !onScreen(inp) || inp.readOnly || inp.type !== 'text' || !inp.value) return;
            let name = inp.placeholder || '';
            let box = inp;
            for (let k = 0; k < 6 && box.parentElement; k++) {
                box = box.parentElement;
                const lab = box.querySelector('label, .el-form-item__label');
                if (lab && lab.textContent.trim()) { name = lab.textContent.trim(); break; }
            }
            names.push(name + '(' + inp.value + ')');
            setter.call(inp, '');
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
        });
        return names;
    }""")
    if cleared:
        print(f"  已清空残留筛选: {', '.join(cleared)}")
    else:
        print("  其他筛选项本来就是空的")
    return True


def scan_due(page):
    """当前页符合条件的单元格：红角标 且 截止距今<=DUE_DAYS。"""
    today = date.today()
    data = page.evaluate(SCAN_JS, list(CONFIG))
    hits = []
    for r in data.get("rows", []):
        try:
            gap = (datetime.strptime(r["date"], "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if gap <= DUE_DAYS:
            hits.append({"name": r["name"], "col": r["col"], "date": r["date"], "gap": gap})
    return hits


def fill_and_save(page, hit):
    """点单元格 → 填弹窗 → 保存。返回 True/False。"""
    name, col, d = hit["name"], hit["col"], hit["date"]
    cfg = CONFIG[col]
    desc = f"{name} {col}({d})"

    # 收掉残留的 sfPop 气泡（会拦截点击），再点日期单元格
    page.keyboard.press("Escape")
    page.evaluate("() => {" + BASE_JS + """
        document.querySelectorAll('.el-popover.sfPop').forEach(pp => {
            if (onScreen(pp)) pp.style.display = 'none';
        });
    }""")
    time.sleep(0.5)
    try:
        page.locator(
            f'tr:has-text("{name}") .el-popover__reference:has-text("{d}"):visible'
        ).first.click(timeout=10000)
    except Exception as e:
        print(f"  [失败] {desc} 单元格点击失败: {type(e).__name__}")
        return False
    time.sleep(2)
    opened = page.evaluate("() => {" + DLG_JS + "return !!dlg();}")
    if not opened:
        print(f"  [失败] {desc} 弹窗未打开")
        return False
    title = page.evaluate("() => {" + DLG_JS + "return dlg().textContent.slice(0, 80);}")
    if name not in title:
        print(f"  [失败] {desc} 弹窗姓名不符: {title!r}")
        page.keyboard.press("Escape")
        return False

    # 随访方式（真实点击开下拉，JS 点选项）
    page.evaluate("() => {" + DLG_JS + """
        dlgFormItem('随访方式：').querySelector('.el-select').setAttribute('data-kimi-click', '1');
    }""")
    page.locator('[data-kimi-click="1"]').first.click()
    page.evaluate("() => document.querySelectorAll('[data-kimi-click]')"
                  ".forEach(e => e.removeAttribute('data-kimi-click'))")
    time.sleep(1)
    picked = page.evaluate("(m) => {" + BASE_JS + """
        const dd = Array.from(document.querySelectorAll('.el-select-dropdown')).find(onScreen);
        if (!dd) return 'no-dropdown';
        const item = Array.from(dd.querySelectorAll('.el-select-dropdown__item'))
            .find(i => i.textContent.trim() === m);
        if (!item) return 'no-option';
        item.click();
        return 'ok';
    }""", cfg["method"])
    time.sleep(0.5)
    val = page.evaluate("() => {" + DLG_JS + """
        return dlgFormItem('随访方式：').querySelector('input').value;
    }""")
    if val != cfg["method"]:
        print(f"  [失败] {desc} 随访方式选不上({picked})，当前值 {val!r}，不保存")
        page.keyboard.press("Escape")
        return False

    # 随访结果（原生 setter + input 事件）
    ok = page.evaluate("(text) => {" + DLG_JS + """
        const ta = dlgFormItem('随访结果：').querySelector('textarea');
        if (!ta) return false;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
        setter.call(ta, text);
        ta.dispatchEvent(new Event('input', {bubbles: true}));
        return ta.value === text;
    }""", cfg["text"])
    if not ok:
        print(f"  [失败] {desc} 随访结果写入失败，不保存")
        page.keyboard.press("Escape")
        return False

    # 本次随访完成
    page.evaluate("(choice) => {" + DLG_JS + """
        const fi = dlgFormItem('本次随访完成：');
        const radio = Array.from(fi.querySelectorAll('.el-radio'))
            .find(r => r.textContent.trim() === choice);
        if (radio) radio.click();
    }""", cfg["choice"])
    time.sleep(0.5)
    checked = page.evaluate("() => {" + DLG_JS + """
        const fi = dlgFormItem('本次随访完成：');
        const r = fi.querySelector('.el-radio.is-checked');
        return r ? r.textContent.trim() : null;
    }""")
    if checked != cfg["choice"]:
        print(f"  [失败] {desc} 完成状态选不上(当前 {checked!r})，不保存")
        page.keyboard.press("Escape")
        return False

    # 保存，等弹窗关闭
    page.evaluate("() => {" + DLG_JS + """
        const d = dlg();
        Array.from(d.querySelectorAll('button'))
            .find(bb => bb.textContent.replace(/\\s+/g, '') === '保存').click();
    }""")
    for _ in range(40):
        time.sleep(0.5)
        if not page.evaluate("() => {" + DLG_JS + "return !!dlg();}"):
            print(f"  [成功] {desc} 方式={cfg['method']} 完成={cfg['choice']} 已保存")
            return True
    print(f"  [失败] {desc} 保存后弹窗未关闭")
    return False


def connect_and_goto_followup():
    """连接调试 Chrome 并进到 产前随访跟踪 页。返回 (p, browser, page)，失败返回 None。"""
    print("1. 连接调试Chrome（localhost:9222）...")
    p, browser = connect_chrome()
    if not browser:
        print("错误：连接失败。请先运行 慢阻肺问卷/启动调试Chrome并打开网址.py")
        return None
    # 协同系统页面已开着就直接用（平台导航页关了也不影响）
    udrhip = None
    for pg in all_pages(browser):
        try:
            if UDRHIP_MARK in pg.url and "login" not in pg.url:
                udrhip = pg
                break
        except Exception:
            pass
    if udrhip:
        print(f"2. 协同系统页面已打开，直接使用: {udrhip.url}")
    else:
        print("2. 检查平台登录状态...")
        portal = ensure_portal(browser)
        if not portal:
            p.stop()
            return None
        print("3. 进入 孕产妇协同管理信息系统...")
        result = open_udrhip(p, browser, portal)
        if not result:
            p.stop()
            return None
        p, browser, udrhip = result  # open_udrhip 可能断开重连过，用返回的新连接
        time.sleep(3)
    print("4. 进入 产前跟踪 → 产前随访跟踪...")
    if not udrhip_goto_followup(udrhip):
        print("错误：进不了产前随访跟踪页面")
        p.stop()
        return None
    return p, browser, udrhip


def main():
    only_date = len(sys.argv) > 1 and sys.argv[1] == "setdate"
    bjh_mode = len(sys.argv) > 1 and sys.argv[1] == "bjh"
    ctx = connect_and_goto_followup()
    if not ctx:
        return
    p, browser, udrhip = ctx
    try:
        print("5. 设置发现日期为 去年今天~今天...")
        udrhip_set_discovery_date(udrhip)
        print("5b. 清空其他筛选项（姓名/身份证/保健号等）...")
        clear_other_filters(udrhip)
        if only_date:
            print("=" * 40)
            print("setdate 模式：只刷新筛选，不做登记。完成！" if not FAILED_STEPS
                  else f"有步骤未成功: {FAILED_STEPS}")
            return

        if bjh_mode:
            fyweb = find_fyweb(browser)
            if not fyweb:
                print("错误：没找到 fyweb 妇幼保健系统页面（10.130.20.249），"
                      "请先打开 https://10.130.20.249:28661/fyweb/#/home 并登录")
                FAILED_STEPS.append("fyweb页面未打开")
                return
            # 启动时就检查 fyweb 登录态，别等跑到查询那步才发现掉线
            if "/login" in fyweb.url:
                print("错误：fyweb 登录已失效（停在登录页），请先在浏览器里登录再运行")
                FAILED_STEPS.append("fyweb登录失效")
                return
            # 名单收集前确保当前查询条件已生效
            udrhip.keyboard.press("Escape")
            time.sleep(0.5)
            run_click(udrhip, locate("""
                const btn = Array.from(document.querySelectorAll('button, .el-button'))
                    .find(b => b.textContent.replace(/\\s+/g, '') === '查询' && onScreen(b));
                if (!btn) return false;
                btn.setAttribute('data-kimi-click', '1');
                return true;
            """), "点击查询", timeout=10, quiet=True, record=False)
            time.sleep(3)
            run_bjh(udrhip, fyweb)
            return

        # 填完日期后日期面板可能还开着，会挡住查询按钮（点击被拦截表现为"未找到目标"）
        udrhip.keyboard.press("Escape")
        time.sleep(0.5)
        run_click(udrhip, locate("""
            const btn = Array.from(document.querySelectorAll('button, .el-button'))
                .find(b => b.textContent.replace(/\\s+/g, '') === '查询' && onScreen(b));
            if (!btn) return false;
            btn.setAttribute('data-kimi-click', '1');
            return true;
        """), "点击查询", timeout=10)
        time.sleep(3)

        # 回到第 1 页再开始扫
        udrhip.evaluate("() => {" + BASE_JS + """
            const li = Array.from(document.querySelectorAll('.el-pagination .number'))
                .find(x => x.textContent.trim() === '1' && onScreen(x));
            if (li) li.click();
        }""")
        time.sleep(3)

        print("6. 扫描全部页，登记到期随访...")
        done, failed = [], []
        for page_no in range(1, 40):
            while True:
                hits = scan_due(udrhip)
                hits = [h for h in hits
                        if (h["name"], h["col"], h["date"]) not in failed]
                if not hits:
                    break
                h = hits[0]  # 一次处理一个，保存后重扫（DOM 会刷新）
                print(f"  第{page_no}页 待登记: {h['name']} {h['col']} 截止{h['date']}"
                      f"（{'已过期' if h['gap'] < 0 else str(h['gap']) + '天后'}）")
                if fill_and_save(udrhip, h):
                    done.append(h)
                else:
                    failed.append((h["name"], h["col"], h["date"]))
                time.sleep(2)
            nxt = udrhip.evaluate("() => {" + BASE_JS + """
                const btn = document.querySelector('.el-pagination .btn-next');
                if (!btn || btn.disabled || !onScreen(btn)) return false;
                btn.click();
                return true;
            }""")
            if not nxt:
                break
            time.sleep(3)

        print("=" * 40)
        print(f"登记完成: 成功 {len(done)} 条，失败 {len(failed)} 条")
        for h in done:
            print(f"  ✓ {h['name']} {h['col']} {h['date']}")
        for name, col, d in failed:
            print(f"  ✗ {name} {col} {d} —— 请手动处理")
            FAILED_STEPS.append(f"{name}-{col}")
        if FAILED_STEPS:
            print(f"另有 {len(FAILED_STEPS)} 个步骤未成功，详见上方日志")
        else:
            print("全部步骤执行成功！")
    finally:
        try:
            p.stop()
        except Exception:
            pass


# ==================== bjh 模式：补录保健号 ====================

FYWEB_MARK = "10.130.20.249"  # 妇幼保健管理信息系统（不限端口）
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def find_fyweb(browser):
    for pg in all_pages(browser):
        try:
            if FYWEB_MARK in pg.url and "/login" not in pg.url:
                return pg
        except Exception:
            pass
    return None


def fyweb_goto_chujian(page):
    """fyweb 进入 孕期保健 → 初检。判定标准：有「身份证号码」查询表单项且表头有「初检日期」
    （首页工作台也有"初检日期"列，单看表头会误判）。"""
    on_page = locate("""
        const hasIdInput = Array.from(document.querySelectorAll('.ivu-form-item')).some(fi => {
            const lab = fi.querySelector('.ivu-form-item-label, label');
            return lab && lab.textContent.trim().replace(/[:：]/g, '') === '身份证号码'
                && fi.querySelector('input') && onScreen(fi);
        });
        const hasHead = Array.from(document.querySelectorAll('.ivu-table-header th'))
            .some(th => th.textContent.trim() === '初检日期' && onScreen(th));
        return hasIdInput && hasHead;
    """)
    try:
        if page.evaluate(on_page):
            print("  已在 fyweb 初检页面")
            return True
    except Exception:
        pass
    run_click(page, locate("""
        const sub = Array.from(document.querySelectorAll('.ivu-menu-submenu-title'))
            .find(d => d.textContent.trim() === '孕期保健' && onScreen(d));
        if (!sub) return false;
        sub.setAttribute('data-kimi-click', '1');
        return true;
    """), "展开孕期保健", timeout=10)
    ok = run_click(page, locate("""
        const item = Array.from(document.querySelectorAll('.ivu-menu-item'))
            .find(li => li.textContent.trim() === '初检' && onScreen(li));
        if (!item) return false;
        item.setAttribute('data-kimi-click', '1');
        return true;
    """), "点击初检", verify_js=on_page, timeout=15)
    time.sleep(1)
    return ok


def fyweb_query_chujian(page, idcard, name):
    """初检页：清空查询条件（含日期范围）→ 输身份证号码 → 查询 → 读结果。
    返回 [(保健号, 初检日期)]；查询没生效/登录失效/结果姓名不符返回 None（绝不用旧数据误判）。"""
    if "/login" in page.url:
        print("  [失败] fyweb 登录已失效（停在登录页），请手动登录后重试")
        FAILED_STEPS.append("fyweb登录失效")
        return None
    # 重置清空表单（组件级清空）
    run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button'))
            .find(b => b.textContent.replace(/\\s+/g, '') === '重置' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), "fyweb 重置查询表单", timeout=8, quiet=True, record=False)
    time.sleep(1)
    # 重置可能清不掉日期范围（初检日期 开始/结束），再兜底清一遍查询区的残留输入
    page.evaluate("() => {" + BASE_JS + r"""
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        document.querySelectorAll('.ivu-form-item input, .ivu-date-picker input').forEach(inp => {
            if (!onScreen(inp) || inp.readOnly && !inp.closest('.ivu-date-picker')) return;
            if (!inp.value) return;
            setter.call(inp, '');
            inp.dispatchEvent(new Event('input', {bubbles: true}));
        });
    }""")
    time.sleep(0.5)
    ok = page.evaluate("(idcard) => {" + BASE_JS + r"""
        // 表单项 label = 身份证号码 的输入框
        const items = Array.from(document.querySelectorAll('.ivu-form-item'));
        for (const fi of items) {
            const lab = fi.querySelector('.ivu-form-item-label, label');
            if (!lab || lab.textContent.trim().replace(/[:：]/g, '') !== '身份证号码') continue;
            const inp = fi.querySelector('input');
            if (!inp || !onScreen(inp)) continue;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, idcard);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            return inp.value === idcard;
        }
        return false;
    }""", idcard)
    if not ok:
        print("  [失败] fyweb 身份证号码输入框未找到或写入失败")
        return None
    run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button'))
            .find(b => b.textContent.replace(/\\s+/g, '') === '查询' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), "fyweb 点击查询", timeout=8, quiet=True, record=False)
    # 等加载层消失、表格刷新
    run_step(page, locate("""
        return !Array.from(document.querySelectorAll('.ivu-spin-fix, .ivu-spin')).find(onScreen);
    """), "fyweb 查询结果加载", timeout=15, quiet=True, record=False)
    time.sleep(1)
    # 登录失效弹窗（查询时才发现 session 过期的情况）
    expired = page.evaluate("() => {" + BASE_JS + r"""
        return Array.from(document.querySelectorAll('.ivu-modal, .v-transfer-dom'))
            .some(m => onScreen(m) && m.textContent.includes('登录失效'));
    }""")
    if expired or "/login" in page.url:
        print("  [失败] fyweb 登录已失效，请在浏览器里重新登录后重试")
        FAILED_STEPS.append("fyweb登录失效")
        return None
    rows = fyweb_read_chujian(page)
    # 校验结果确实属于本次查询的人（防止查询没生效、读到上一个人的旧结果）
    if rows and any(r["name"] and r["name"] != name for r in rows):
        print(f"  [失败] fyweb 结果姓名({rows[0]['name']})与 {name} 不符，查询可能未生效")
        return None
    return rows


def fyweb_read_chujian(page):
    """读初检结果行：[(保健号, 初检日期, 姓名)]。首列保健号，日期列用正则抓。"""
    rows = page.evaluate("() => {" + BASE_JS + r"""
        const out = [];
        const seen = new Set();
        document.querySelectorAll('.ivu-table-body tr').forEach(tr => {
            if (!onScreen(tr)) return;
            const tds = tr.querySelectorAll('td');
            if (tds.length < 3) return;
            const bjh = (tds[0].textContent || '').trim();
            const name = (tds[1].textContent || '').split('\n')[0].trim();
            let dt = '';
            for (const td of tds) {
                const m = (td.textContent || '').match(/\d{4}-\d{2}-\d{2}/);
                if (m) { dt = m[0]; break; }
            }
            const key = bjh + '|' + dt;
            if (!bjh || !dt || seen.has(key)) return;
            seen.add(key);
            out.push({bjh, date: dt, name});
        });
        return out;
    }""")
    return rows or []


def udrhip_collect_pregnant(page):
    """翻页收集产前随访跟踪名单：姓名/保健号/证件号码/末次月经。
    证件号码在列表里可能掩码，仅作展示；补录时从修改弹窗读完整号码。"""
    # 先回第 1 页、读出总数，按总数翻页，避免翻页中途读漏
    page.evaluate("() => {" + BASE_JS + """
        const li = Array.from(document.querySelectorAll('.el-pagination .number'))
            .find(x => x.textContent.trim() === '1' && onScreen(x));
        if (li) li.click();
    }""")
    time.sleep(2.5)
    total = page.evaluate("() => {" + BASE_JS + r"""
        const t = document.querySelector('.el-pagination__total');
        const m = t ? (t.textContent.match(/共\s*(\d+)\s*条/) || [])[1] : null;
        return m ? parseInt(m) : 0;
    }""")
    out, seen = [], set()
    for page_no in range(1, 40):
        # 等本页数据稳定（加载层消失）
        page.evaluate("() => {" + BASE_JS + """
            return !Array.from(document.querySelectorAll('.el-table__loading, .el-loading-mask'))
                .find(onScreen);
        }""")
        rows = page.evaluate("() => {" + BASE_JS + r"""
            const table = Array.from(document.querySelectorAll('.el-table__body-wrapper'))
                .find(w => onScreen(w) && !w.closest('.el-table__fixed, .el-table__fixed-right'));
            if (!table) return [];
            const headerTable = document.querySelector('.el-table__header-wrapper');
            const heads = Array.from(headerTable.querySelectorAll('th')).map(th => th.textContent.trim());
            const iB = heads.indexOf('保健号'), iN = heads.indexOf('姓名');
            const iID = heads.indexOf('证件号码'), iL = heads.indexOf('末次月经');
            const iR = heads.indexOf('备注');
            const res = [];
            for (const tr of table.querySelectorAll('tr')) {
                const tds = tr.querySelectorAll('td');
                if (tds.length < 25) continue;
                const txt = i => (tds[i] ? tds[i].textContent.trim().replace(/\s+/g, '') : '');
                res.push({name: txt(iN), bjh: txt(iB), id: txt(iID), lmp: txt(iL), remark: txt(iR)});
            }
            return res;
        }""")
        for r in rows:
            key = (r["name"], r["lmp"], r["bjh"])
            if r["name"] and key not in seen:
                seen.add(key)
                out.append(r)
        # Vue 数据补充：完整身份证号（列表里是掩码）、最后一次已完成随访是第几次
        vue_rows = page.evaluate("() => {" + BASE_JS + r"""
            const t = document.querySelector('.el-table');
            if (!t || !t.__vue__) return [];
            const data = (t.__vue__.store && t.__vue__.store.states.data) || [];
            return data.map(r => {
                const nodes = (r.fyPrenatalPeopleNodeList || [])
                    .filter(n => n.followUpStatus === '2' && n.udDeleteFlag !== '1');
                const last = nodes.length ? nodes[nodes.length - 1] : null;
                return {name: r.woMaName || '', bjh: r.womaHealthno || '',
                        id: r.womaCardId || '', lmp: r.womaLastMenstrualTime || '',
                        lastSort: last ? parseInt(last.sort) : 0};
            });
        }""") or []
        vmap = {(v["name"], v["lmp"], v["bjh"]): v for v in vue_rows}
        for r in rows:
            v = vmap.get((r["name"], r["lmp"], r["bjh"]))
            if v:
                if v["id"]:
                    r["id"] = v["id"]          # 完整身份证号（表格里是掩码）
                r["lastSort"] = v["lastSort"]  # 0 = 还没有已完成随访
        print(f"  第{page_no}页：{len(rows)} 行（累计 {len(out)}/{total or '?'}）")
        if total and len(out) >= total:
            break
        nxt = page.evaluate("() => {" + BASE_JS + """
            const btn = document.querySelector('.el-pagination .btn-next');
            if (!btn || btn.disabled || !onScreen(btn)) return false;
            btn.click();
            return true;
        }""")
        if not nxt:
            break
        time.sleep(2.5)
    if total and len(out) < total:
        print(f"  [警告] 收集 {len(out)} 条 < 总数 {total} 条，可能有漏，请人工核对")
        FAILED_STEPS.append("名单收集不完整")
    # 回第 1 页
    page.evaluate("() => {" + BASE_JS + """
        const li = Array.from(document.querySelectorAll('.el-pagination .number'))
            .find(x => x.textContent.trim() === '1' && onScreen(x));
        if (li) li.click();
    }""")
    time.sleep(2)
    return out


def udrhip_last_followup_text(page, name, lmp, sort):
    """金标准核对：点开该孕妇第 sort 次随访（最近一次已完成）的气泡，读随访记录文本。
    返回文本（含 随访结果，如"已分娩"）；读不到返回 ''。"""
    # 收掉残留气泡，表格横向滚到最右让后面的随访列可见
    page.keyboard.press("Escape")
    page.evaluate("() => {" + BASE_JS + """
        document.querySelectorAll('.el-popover.sfPop').forEach(pp => {
            if (onScreen(pp)) pp.style.display = 'none';
        });
        document.querySelectorAll('.el-table__body-wrapper').forEach(w => w.scrollLeft = w.scrollWidth);
    }""")
    time.sleep(0.5)
    rect = page.evaluate("(args) => {" + BASE_JS + r"""
        const [name, lmp, sort] = args;
        const table = Array.from(document.querySelectorAll('.el-table__body-wrapper'))
            .find(w => onScreen(w) && !w.closest('.el-table__fixed, .el-table__fixed-right'));
        if (!table) return null;
        const heads = Array.from(document.querySelectorAll('.el-table__header-wrapper th'))
            .map(th => th.textContent.trim());
        const idx = heads.findIndex(h => h.startsWith('第' + sort + '次'));
        if (idx < 0) return null;
        for (const tr of table.querySelectorAll('tr')) {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 25) continue;
            const iN = heads.indexOf('姓名'), iL = heads.indexOf('末次月经');
            if ((tds[iN].textContent || '').trim() !== name) continue;
            if (lmp && (tds[iL].textContent || '').trim() !== lmp) continue;
            const ref = tds[idx].querySelector('.el-popover__reference');
            if (!ref) return null;
            const r = ref.getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        return null;
    }""", [name, lmp, sort])
    if not rect:
        return ''
    page.mouse.click(rect["x"], rect["y"])
    text = ''
    for _ in range(6):
        time.sleep(0.5)
        text = page.evaluate("() => {" + BASE_JS + """
            const pop = Array.from(document.querySelectorAll('.el-popover')).find(onScreen);
            return pop ? pop.textContent.replace(/\\s+/g, ' ') : '';
        }""")
        if text:
            break
    page.keyboard.press("Escape")
    time.sleep(0.3)
    return text or ''


def udrhip_filter_by_name(page, name):
    """用「姓名」筛选框过滤列表；name 为空串=清除筛选。返回是否操作成功。"""
    ok = page.evaluate("(name) => {" + BASE_JS + r"""
        const inp = Array.from(document.querySelectorAll('input'))
            .find(i => i.placeholder === '请输入姓名' && onScreen(i));
        if (!inp) return false;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(inp, name);
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        return true;
    }""", name)
    if not ok:
        return False
    time.sleep(0.5)
    run_click(page, locate("""
        const btn = Array.from(document.querySelectorAll('button, .el-button'))
            .find(b => b.textContent.replace(/\\s+/g, '') === '查询' && onScreen(b));
        if (!btn) return false;
        btn.setAttribute('data-kimi-click', '1');
        return true;
    """), f"姓名筛选查询({name or '全部'})", timeout=10, quiet=True, record=False)
    time.sleep(3)
    return True


def udrhip_open_edit_dialog(page, name, lmp):
    """在当前筛选结果里找到 姓名+末次月经 匹配的行，点「修改」打开弹窗。"""
    ok = page.evaluate("(args) => {" + BASE_JS + r"""
        const [name, lmp] = args;
        const table = Array.from(document.querySelectorAll('.el-table__body-wrapper'))
            .find(w => onScreen(w) && !w.closest('.el-table__fixed, .el-table__fixed-right'));
        if (!table) return 'no-table';
        const headerTable = document.querySelector('.el-table__header-wrapper');
        const heads = Array.from(headerTable.querySelectorAll('th')).map(th => th.textContent.trim());
        const iN = heads.indexOf('姓名'), iL = heads.indexOf('末次月经');
        for (const tr of table.querySelectorAll('tr')) {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 25) continue;
            if ((tds[iN].textContent || '').trim() !== name) continue;
            if (lmp && (tds[iL].textContent || '').trim() !== lmp) continue;
            const btn = Array.from(tr.querySelectorAll('button'))
                .find(bt => bt.textContent.trim() === '修改');
            if (!btn) return 'no-btn';
            btn.click();   // 操作列在固定列副本里，Playwright 判定不可见，直接 JS 点击
            return 'ok';
        }
        return 'no-row';
    }""", [name, lmp])
    if ok != "ok":
        print(f"  [失败] 找不到 {name} 的修改按钮({ok})")
        return False
    # 等弹窗打开并校验姓名（注意：姓名在 input 的 value 里，不在 textContent 里）
    for _ in range(12):
        time.sleep(0.5)
        val = page.evaluate("() => {" + DLG_JS + """
            const d = dlg();
            if (!d) return null;
            const fi = dlgFormItem('姓名');
            const inp = fi ? fi.querySelector('input') : null;
            return inp ? inp.value.trim() : '';
        }""")
        if val == name:
            return True
        if val:  # 弹窗开了但名字不符 = 开错人了，关掉报失败，绝不往下写
            print(f"  [失败] {name} 弹窗姓名不符: {val!r}")
            udrhip_dialog_close(page, save=False)
            return False
    print(f"  [失败] {name} 修改弹窗未打开")
    page.keyboard.press("Escape")   # 兜底：别留着弹窗影响下一个
    return False


def udrhip_dialog_read(page):
    """读修改弹窗里的 证件号码/末次月经/保健号（完整值，列表里是掩码）。"""
    return page.evaluate("() => {" + DLG_JS + r"""
        const read = label => {
            const fi = dlgFormItem(label);
            if (!fi) return '';
            const inp = fi.querySelector('input, textarea');
            return inp ? inp.value.trim() : '';
        };
        return {id: read('证件号码'), lmp: read('末次月经'), bjh: read('保健号')};
    }""")


def udrhip_dialog_close(page, save=False):
    """关闭修改弹窗：save=True 点保存并等弹窗关闭；否则点取消。"""
    label = "保存" if save else "取消"
    page.evaluate("(label) => {" + DLG_JS + """
        const d = dlg();
        if (!d) return;
        const btn = Array.from(d.querySelectorAll('button'))
            .find(bb => bb.textContent.replace(/\\s+/g, '') === label);
        if (btn) btn.click();
    }""", label)
    for _ in range(20):
        time.sleep(0.5)
        if not page.evaluate("() => {" + DLG_JS + "return !!dlg();}"):
            return True
    return False


def run_bjh(udrhip, fyweb):
    """补录保健号主流程：收集名单 → 无保健号的去 fyweb 初检查询 → 匹配上才回填。"""
    import openpyxl

    print("6. 收集产前随访跟踪全部名单（近一年发现、未分娩）...")
    women = udrhip_collect_pregnant(udrhip)
    print(f"  共收集 {len(women)} 条记录")
    if not women:
        print("  [失败] 名单为空")
        FAILED_STEPS.append("收集名单")
        return

    # 分类（优先级从上到下）：
    #   备注含"已分娩" = 已分娩（妊娠结束，不算未分娩孕妇）
    #   保健号 LS 开头 或 备注含"流产" = 妊娠终止（流产等，系统标记）
    #   无保健号 = 待补录
    for w in women:
        remark = w.get("remark", "")
        if "已分娩" in remark:
            w["kind"] = "已分娩"
        elif w["bjh"].startswith("LS") or "流产" in remark:
            w["kind"] = "妊娠终止(流产)"
        elif not w["bjh"]:
            w["kind"] = "无保健号"
        else:
            w["kind"] = "正常"
    # 金标准复核：备注没标记的（正常/无保健号），点开最后一次已完成随访的气泡，
    # 随访结果写"已分娩"/"流产"的以随访记录为准（备注可能漏标）。
    # 用姓名筛选把每个人切到当前页，才能点到她的随访单元格。
    for w in women:
        if w["kind"] not in ("正常", "无保健号") or not w.get("lastSort"):
            continue
        if not udrhip_filter_by_name(udrhip, w["name"]):
            continue
        text = udrhip_last_followup_text(udrhip, w["name"], w["lmp"], w["lastSort"])
        if "已分娩" in text:
            w["kind"] = "已分娩"
            print(f"  金标准修正: {w['name']} 第{w['lastSort']}次随访记录=已分娩（备注未标）")
        elif "已流产" in text or "稽留流产" in text:
            # 注意不能只看"流产"：早孕随访文案里有"预防流产"的固定话术
            w["kind"] = "妊娠终止(流产)"
            print(f"  金标准修正: {w['name']} 第{w['lastSort']}次随访记录=流产（备注未标）")
    udrhip_filter_by_name(udrhip, "")   # 清掉姓名筛选
    targets = [w for w in women if w["kind"] == "无保健号"]
    print(f"  正常待产 {sum(1 for w in women if w['kind']=='正常')} 人，"
          f"已分娩 {sum(1 for w in women if w['kind']=='已分娩')} 人，"
          f"妊娠终止(流产) {sum(1 for w in women if w['kind']=='妊娠终止(流产)')} 人，"
          f"无保健号待补录 {len(targets)} 人")

    # 先存收集名单
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "产前跟踪名单"
    ws.append(["姓名", "保健号", "证件号码", "末次月经", "备注", "类别"])
    for w in women:
        ws.append([w["name"], w["bjh"], w["id"], w["lmp"], w.get("remark", ""), w["kind"]])
    list_path = os.path.join(DESKTOP, f"近一年产前跟踪名单_{stamp}.xlsx")
    wb.save(list_path)
    print(f"  名单已存: {list_path}")

    results = []
    for w in targets:
        name, lmp = w["name"], w["lmp"]
        print(f"===== {name}（末次月经 {lmp}）=====")
        res = {"name": name, "lmp": lmp, "id": "", "fyweb_date": "", "bjh": "", "result": ""}
        # 1. 姓名筛选 → 打开修改弹窗读完整身份证号
        if not udrhip_filter_by_name(udrhip, name):
            res["result"] = "失败: 姓名筛选失败"
            results.append(res); continue
        if not udrhip_open_edit_dialog(udrhip, name, lmp):
            res["result"] = "失败: 修改弹窗未打开"
            results.append(res); continue
        info = udrhip_dialog_read(udrhip)
        # 优先用收集时从 Vue 数据拿到的完整身份证（列表掩码），弹窗的作兜底
        idcard = w["id"] if w.get("id") and "*" not in w["id"] else info["id"]
        dlg_lmp = info["lmp"]
        res["id"] = idcard
        if not idcard or "*" in idcard:
            res["result"] = "失败: 弹窗里身份证仍掩码，无法查询"
            udrhip_dialog_close(udrhip, save=False)
            results.append(res); continue
        if dlg_lmp and lmp and dlg_lmp != lmp:
            # 弹窗末次月经和列表不一致 = 可能开错了人，坚决不写
            res["result"] = f"失败: 弹窗末次月经({dlg_lmp})与列表({lmp})不符"
            udrhip_dialog_close(udrhip, save=False)
            results.append(res); continue

        # 2. fyweb 初检查询（弹窗保持打开，查完回来直接填）
        if not fyweb_goto_chujian(fyweb):
            res["result"] = "失败: fyweb 进不了初检页"
            udrhip_dialog_close(udrhip, save=False)
            results.append(res); continue
        records = fyweb_query_chujian(fyweb, idcard, name)
        if records is None:
            res["result"] = "失败: fyweb 查询失败（详见上方日志）"
            udrhip_dialog_close(udrhip, save=False)
            results.append(res); continue
        print(f"  fyweb 初检记录 {len(records)} 条: " +
              (", ".join(f"{r['bjh']}({r['date']})" for r in records) or "无"))

        # 3. 初检日期 > 末次月经 = 本次怀孕的初检，取它的保健号
        lmp_date = datetime.strptime(lmp, "%Y-%m-%d").date() if lmp else None
        match = None
        if lmp_date:
            after = [r for r in records
                     if datetime.strptime(r["date"], "%Y-%m-%d").date() > lmp_date]
            if after:
                match = sorted(after, key=lambda r: r["date"])[0]
        if not match:
            res["result"] = "本孕次未初检，不录入"
            print(f"  {name} 末次月经 {lmp}，初检记录都在此之前（或没有），不录入")
            udrhip_dialog_close(udrhip, save=False)
            results.append(res); continue

        # 4. 回填保健号并保存
        bjh = match["bjh"]
        res["fyweb_date"] = match["date"]
        res["bjh"] = bjh
        ok = udrhip.evaluate("(bjh) => {" + DLG_JS + r"""
            const fi = dlgFormItem('保健号');
            if (!fi) return false;
            const inp = fi.querySelector('input');
            if (!inp) return false;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, bjh);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            return inp.value === bjh;
        }""", bjh)
        if not ok:
            res["result"] = "失败: 保健号写入弹窗失败"
            udrhip_dialog_close(udrhip, save=False)
            results.append(res); continue
        if udrhip_dialog_close(udrhip, save=True):
            res["result"] = f"已补录（初检日期 {match['date']}）"
            print(f"  [成功] {name} 已补录保健号 {bjh}")
        else:
            res["result"] = "失败: 保存后弹窗未关闭，请人工核对"
            FAILED_STEPS.append(f"{name}-补录保存")
        results.append(res)
        time.sleep(1)

    # 清除姓名筛选
    if targets:
        udrhip_filter_by_name(udrhip, "")

    # 补录结果存 Excel
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = "补录保健号结果"
    ws2.append(["姓名", "证件号码", "末次月经", "初检日期(fyweb)", "补录保健号", "处理结果"])
    for r in results:
        ws2.append([r["name"], r["id"], r["lmp"], r["fyweb_date"], r["bjh"], r["result"]])
    res_path = os.path.join(DESKTOP, f"补录保健号结果_{stamp}.xlsx")
    wb2.save(res_path)

    print("=" * 40)
    print(f"补录保健号完成: 待补录 {len(targets)} 人")
    for r in results:
        print(f"  {r['name']}: {r['result']}")
    print(f"结果已存: {res_path}")


if __name__ == "__main__":
    main()