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

登记前会先把"发现日期"刷成 去年今天~今天 并点查询（留空会查不全）。
前置：先运行 慢阻肺问卷/启动调试Chrome并打开网址.py 启动调试 Chrome。
平台未登录时会自动打开登录页等你手动登录（SSO 会话保留在 C:\\ChromeDebug，
一般只需登录一次）。

注意：平台点"孕产妇协同管理信息系统"后新标签页先落在 loginHis（SSO token
交接页），实测只要有 CDP 客户端连着它就一直不跳转，彻底断开连接后约 30 秒
内才跳转——所以脚本点完会主动断开、每 20 秒重连检查一次。
"""

from playwright.sync_api import sync_playwright
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
    ctx = connect_and_goto_followup()
    if not ctx:
        return
    p, browser, udrhip = ctx
    try:
        print("5. 设置发现日期为 去年今天~今天...")
        udrhip_set_discovery_date(udrhip)
        if only_date:
            print("=" * 40)
            print("setdate 模式：只刷新筛选，不做登记。完成！" if not FAILED_STEPS
                  else f"有步骤未成功: {FAILED_STEPS}")
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


if __name__ == "__main__":
    main()
