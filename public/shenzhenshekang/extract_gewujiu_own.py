#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""隔物灸记账明细 —— 提取本人（登录医生）看诊的行

流程：
1. 读桌面《…"隔物灸"记账明细表.xlsx》，按"费用发生时间"的月份范围（如 2024-06 ~ 2025-04）
2. 连接调试 Chrome（localhost:9222）的社康系统，关掉旧的"就诊历史记录"标签页，
   统计分析 → 就诊历史记录 开全新面板 → 点"本人"（jzls=3）
3. 逐月填挂号时间 → 搜索 → 翻页爬取全部记录（姓名/挂号时间/就诊医生/身份证号），
   每月爬完校验 行数==总数，结果落到 _gewujiu_scrape_cache.json（中断重跑自动续）
4. 按 姓名+就诊日期 匹配记账明细行（同名时用身份证号前6+后6兜底），
   命中的整行复制到新 Excel（追加"就诊日期""就诊医生"两列）存桌面

机制同 auto_fill_monthly_check.py（store 翻页爬取、活动面板限定、放大镜真实点击）。
"""

from playwright.sync_api import sync_playwright
import glob
import json
import os
import sys
import time

FAILED_STEPS = []

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gewujiu_scrape_cache.json")
DOCTOR = "谢时望"  # 期望的就诊医生（本人），爬取后校验，不符会警告

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
        const tab = document.querySelector('li.x-mytab-strip-active, li.x-mytab-strip-act');
        if (!tab || tab.id.indexOf('__') < 0) return null;
        const panel = Ext.getCmp(tab.id.split('__')[1]);
        return panel && panel.el && panel.el.dom ? panel.el.dom : null;
    };
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

HOOK_SEARCH_JS = "() => {" + PANEL_JS + r"""
    const g = findGrid();
    if (!g) return false;
    window.__searchDone = false;
    g.getStore().on('load', () => { window.__searchDone = true; });
    return true;
}"""

SCRAPE_JS = "async () => {" + PANEL_JS + r"""
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
        if (++guard > 50) return {error: '翻页次数超限', collected: Object.keys(seen).length};
    }
    const keep = ['GHXH','BRXM','SFZH','GHSJ','YSDM_text','KSDM_text','MZHM','ZYZD'];
    const rows = Object.values(seen).map(d => {
        const o = {};
        keep.forEach(k => { o[k] = d[k]; });
        return o;
    });
    return {total, rows};
}"""


def run_step(page, js, desc, timeout=10, quiet=False, record=True):
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


def run_click(page, locate_js, desc, verify_js=None, timeout=10, quiet=False, record=True):
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
            if not clicked:
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


def scrape_all(page, desc):
    """翻页爬取当前搜索结果全部行；行数与总数不一致视为失败（宁缺勿错）。"""
    try:
        r = page.evaluate(SCRAPE_JS)
    except Exception as e:
        print(f"  [失败] {desc}：爬取异常 {e}")
        FAILED_STEPS.append(desc)
        return None
    if not r or "rows" not in r:
        print(f"  [失败] {desc}：{r.get('error') if r else '无返回'}"
              + (f"（已取 {r.get('collected')} 行）" if r and r.get('collected') else ""))
        FAILED_STEPS.append(desc)
        return None
    if len(r["rows"]) != r["total"]:
        print(f"  [失败] {desc}：爬取 {len(r['rows'])} 行 ≠ 总数 {r['total']}，数据不完整，本月需重跑")
        FAILED_STEPS.append(desc + "(行数不符)")
        return None
    print(f"  [成功] {desc}：爬取 {len(r['rows'])} 行")
    return r["rows"]


def read_billing_rows():
    """读桌面隔物灸记账明细表，返回 (headers, rows, month_keys)。
    rows: [{name, sfzh, date, cells}]，date 取费用发生时间前 10 位。"""
    from openpyxl import load_workbook
    files = [f for f in glob.glob(os.path.join(DESKTOP, "*隔物灸*记账明细表*.xlsx"))
             if not os.path.basename(f).startswith("~$")]
    if not files:
        print("错误：桌面找不到隔物灸记账明细表 xlsx")
        sys.exit(1)
    path = files[0]
    print(f"读取记账明细: {path}")
    wb = load_workbook(path, data_only=True)
    ws = wb["明细"]
    headers = [ws.cell(1, c).value for c in range(1, 44)]
    rows = []
    for r in range(2, ws.max_row + 1):
        name = ws.cell(r, 2).value
        fee_time = ws.cell(r, 10).value
        if not name or not fee_time:
            continue
        date = str(fee_time)[:10]
        cells = [ws.cell(r, c).value for c in range(1, 44)]
        rows.append({"name": str(name).strip(), "sfzh": str(ws.cell(r, 3).value or ""),
                     "date": date, "cells": cells, "rowno": r})
    months = sorted({row["date"][:7] for row in rows})
    print(f"  共 {len(rows)} 行，月份范围 {months[0]} ~ {months[-1]}")
    return headers, rows, months


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def sfzh_match(full, masked):
    """系统里身份证号中间打码（前6+后6可见），用于同名消歧。masked 为空视为通过。"""
    if not masked or not full or "*" not in masked:
        return True
    return full[:6] == masked[:6] and full[-6:] == masked[-6:]


def main():
    headers, bill_rows, months = read_billing_rows()
    cache = load_cache()
    todo = [m for m in months if m not in cache]
    print(f"需爬取 {len(todo)} 个月份（已缓存 {len(months) - len(todo)} 个）: {', '.join(todo) or '无'}")

    if todo:
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
            except Exception:
                print("错误：连接 Chrome 调试端口失败，请先启动调试 Chrome 并登录社康系统")
                return
            page = None
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if "172.17.8.14:8780" in pg.url:
                        page = pg
                        break
                if page:
                    break
            if not page:
                print("错误：未找到社康系统页面（172.17.8.14:8780）")
                return
            print(f"当前页面: {page.title()}")

            # 关旧的就诊历史记录标签页（筛选条件服务端粘滞，必须全新面板）
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
                except Exception:
                    break
                finally:
                    page.evaluate("() => document.querySelectorAll('[data-kimi-click]')"
                                  ".forEach(e => e.removeAttribute('data-kimi-click'))")
                time.sleep(1)

            # 统计分析 → 就诊历史记录（全新面板）
            run_click(page, locate(r"""
                const links = Array.from(document.querySelectorAll('a')).filter(a =>
                    (a.textContent || '').trim() === '统计分析' && onScreen(a));
                if (!links.length) return false;
                links[0].setAttribute('data-kimi-click', '1');
                return true;
            """), "点击统计分析", timeout=15)
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
            time.sleep(2)

            # 点本人（jzls=3）
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

            # 逐月搜索爬取
            import calendar
            for m in todo:
                y, mo = int(m[:4]), int(m[5:7])
                start_date = f"{y}-{mo:02d}-01"
                end_date = f"{y}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"
                print(f"--- {m}（{start_date} ~ {end_date}）---")
                if not run_step(page, "() => {" + PANEL_JS + f"""
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
                }}""", f"填写挂号时间 {m}"):
                    continue
                if not search_and_wait(page, f"搜索 {m}"):
                    continue
                rows = scrape_all(page, f"爬取 {m}")
                if rows is not None:
                    cache[m] = rows
                    save_cache(cache)

    # ========== 匹配 + 写新 Excel ==========
    if any(m not in cache for m in months):
        missing = [m for m in months if m not in cache]
        print(f"\n警告：以下月份没有爬到完整数据，未纳入匹配：{', '.join(missing)}")

    visits = {}  # 姓名 -> [(date, sfzh_masked, doctor)]
    doctors = set()
    for m, rows in cache.items():
        if m not in months:
            continue
        for rec in rows:
            name = str(rec.get("BRXM") or "").strip()
            date = str(rec.get("GHSJ") or "")[:10]
            doc = str(rec.get("YSDM_text") or "").strip()
            doctors.add(doc)
            if name and date:
                visits.setdefault(name, []).append((date, str(rec.get("SFZH") or ""), doc))
    total_visits = sum(len(v) for v in visits.values())
    print(f"\n本人就诊记录共 {total_visits} 条，涉及 {len(visits)} 名患者")
    if doctors != {DOCTOR}:
        print(f"警告：爬到的就诊医生不是预期的 {DOCTOR}：{sorted(doctors)}")

    matched, not_mine, date_mismatch = [], [], []
    for row in bill_rows:
        cand = visits.get(row["name"])
        if not cand:
            not_mine.append(row)
            continue
        hit = any(d == row["date"] and sfzh_match(row["sfzh"], mz) for d, mz, _ in cand)
        if hit:
            matched.append(row)
        else:
            if any(sfzh_match(row["sfzh"], mz) for _, mz, _ in cand):
                date_mismatch.append(row)  # 本人看过这个患者，但记账日期对不上挂号日期
            else:
                not_mine.append(row)

    print(f"记账明细 {len(bill_rows)} 行：属于{DOCTOR} {len(matched)} 行，"
          f"非本人 {len(not_mine)} 行，日期对不上 {len(date_mismatch)} 行")
    for row in date_mismatch[:10]:
        print(f"  [日期不符] 行{row['rowno']} {row['name']} 记账日期{row['date']} "
              f"本人挂号日期: {sorted({d for d, mz, _ in visits[row['name']] if sfzh_match(row['sfzh'], mz)})}")

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "本人记账明细"
    ws.append(headers[:-1] + ["就诊日期", "就诊医生"])  # 去掉"病例截图"列（DISPIMG公式无法复制）
    for row in matched:
        ws.append(["" if i == 42 else c for i, c in enumerate(row["cells"][:-1])]
                  + [row["date"], DOCTOR])
    out_path = os.path.join(DESKTOP, f"隔物灸记账明细_{DOCTOR}.xlsx")
    wb.save(out_path)
    print(f"\n已保存: {out_path}（{len(matched)} 行）")

    print("=" * 40)
    if FAILED_STEPS:
        print(f"完成，但有 {len(FAILED_STEPS)} 个步骤未成功：")
        for s in FAILED_STEPS:
            print(f"  - {s}")
    else:
        print("全部步骤执行成功！")


if __name__ == "__main__":
    main()
