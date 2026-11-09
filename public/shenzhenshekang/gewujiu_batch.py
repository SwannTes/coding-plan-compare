#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""隔物灸记账明细 批量处理（流程同 gewujiu_step.py，单浏览器会话）

用法: python gewujiu_batch.py [起始行] [结束行] [表格路径]
  默认处理 254~505 行、桌面上的隔物灸记账明细表；表格换了位置/名字就传第3个参数。
- 已填写（AM 非空）的就诊自动跳过；判定缓存在 _step_cache.json，可中断续跑
- 费用不一致 / 无挂号 / 无隔物灸医嘱 / 无法套模板 → 桌面/隔物灸_费用不一致待审核.xlsx
- 其余：截图 + 写回 AM/AP/AQ；WPS 占用导致写回失败的，最后统一补写
"""

from playwright.sync_api import sync_playwright
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gewujiu_step as G
from fill_gewujiu_rows import classify

ROW_START, ROW_END = 254, 505


def read_all_visits(row_start, row_end):
    from openpyxl import load_workbook
    wb = load_workbook(G.SRC, data_only=True)
    ws = wb["明细"]
    visits = []
    r = row_start
    while r <= row_end:
        name = str(ws.cell(r, 2).value or "").strip()
        date = str(ws.cell(r, 10).value or "")[:10]
        if not name or not date:
            r += 1
            continue
        rows = [r]
        r += 1
        while r <= row_end and str(ws.cell(r, 2).value or "").strip() == name \
                and str(ws.cell(r, 10).value or "")[:10] == date:
            rows.append(r)
            r += 1
        filled = ws.cell(rows[0], 39).value is not None
        visits.append({"name": name, "date": date,
                       "sfzh": str(ws.cell(rows[0], 3).value or ""),
                       "rows": rows,
                       "qtys": [ws.cell(x, 24).value for x in rows],
                       "amts": [ws.cell(x, 36).value for x in rows],
                       "filled": filled})
    return visits


def review_and_cache(visit, doctor, zjje, jfje, reason, rows_tag, cache):
    G.add_review(visit, doctor, zjje, jfje, reason)
    cache[rows_tag] = {"status": "review", "reason": reason, "visit": visit}
    G.save_cache(cache)


def process(page, visit, rows_tag, cache):
    recs = G.search_patient(page, visit["name"], visit["date"])
    if recs is None:
        print("  搜索失败，跳过（下轮重试）")
        return
    cands = [c for c in recs if str(c.get("GHSJ") or "")[:10] == visit["date"]
             and G.sfzh_match(visit["sfzh"], str(c.get("SFZH") or ""))]
    if not cands:
        review_and_cache(visit, "", None, None, "就诊历史记录中未找到当天挂号", rows_tag, cache)
        return
    for c in cands:
        print(f"  挂号 {c['GHSJ']} 医生={c['YSDM']} 总金额={c['ZJJE']} 缴费={c['JFJE']}")
    paid = [c for c in cands if abs((c.get("ZJJE") or 0) - (c.get("JFJE") or 0)) < 0.005]
    if not paid:
        c0 = cands[0]
        review_and_cache(visit, c0["YSDM"], c0["ZJJE"], c0["JFJE"],
                         f"收费不一致 总金额{c0['ZJJE']}≠缴费{c0['JFJE']}", rows_tag, cache)
        return
    record, hit = "", None
    for cand in paid:
        text = G.click_row_and_get_record(page, cand["GHXH"])
        if "隔物灸法" in text:
            record, hit = text, cand
            break
    if hit is None:
        review_and_cache(visit, cands[0]["YSDM"], cands[0]["ZJJE"], cands[0]["JFJE"],
                         "已缴清挂号中未找到隔物灸医嘱（或病历读取失败）", rows_tag, cache)
        return
    status, tpl, note = classify(record)
    print(f"  判定: {status} | {note}")
    if status == "review":
        review_and_cache(visit, hit["YSDM"], hit["ZJJE"], hit["JFJE"], note, rows_tag, cache)
        return
    shot = G.shoot_print_preview(page, visit["name"], visit["date"], rows_tag)
    if not shot:
        print("  截图失败，跳过（下轮重试）")
        return
    print(f"  截图: {shot}")
    cache[rows_tag] = {"status": status, "tpl": tpl, "shot": shot, "visit": visit, "note": note}
    G.save_cache(cache)
    G.do_write(rows_tag)


def main():
    G.SRC = G.find_src(sys.argv[3] if len(sys.argv) > 3 else None)
    start_row = int(sys.argv[1]) if len(sys.argv) > 1 else ROW_START
    end_row = int(sys.argv[2]) if len(sys.argv) > 2 else ROW_END
    if G.wps_open():
        print("!! 表格正在 WPS 中打开，写回会失败。请先关闭再跑。")
        return
    visits = [v for v in read_all_visits(start_row, end_row) if not v["filled"]]
    cache = G.load_cache()
    todo_browser, todo_write = [], []
    for v in visits:
        tag = f"{v['rows'][0]}-{v['rows'][-1]}"
        c = cache.get(tag)
        if c and c["status"] in ("fill", "wgui"):
            todo_write.append(tag)          # 已有判定，直接补写
        elif c and c["status"] == "review":
            continue                        # 已进审核表
        else:
            todo_browser.append((v, tag))   # 需要走浏览器
    print(f"未填就诊 {len(visits)} 个：需浏览器处理 {len(todo_browser)}，直接补写 {len(todo_write)}")

    for tag in todo_write:
        G.do_write(tag)

    if todo_browser:
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
                print("未找到社康系统页面")
                return
            page.bring_to_front()
            G.close_preview()
            # 禁掉页面 JS 弹窗：alert 会卡住真实点击，且 Playwright 自动关闭弹窗
            # 存在竞态 bug 会导致整个驱动崩溃（已踩过）
            try:
                page.evaluate("() => { window.alert = () => {}; window.confirm = () => true;"
                              " window.prompt = () => ''; }")
            except Exception:
                pass
            cache = G.load_cache()
            for i, (v, tag) in enumerate(todo_browser, 1):
                print(f"\n[{i}/{len(todo_browser)}] 行{tag}: {v['name']} {v['date']}")
                try:
                    process(page, v, tag, cache)
                except Exception:
                    print("  异常，跳过：")
                    traceback.print_exc()
                    try:
                        G.close_preview()
                    except Exception:
                        pass
                time.sleep(0.5)

    print("\n批量处理完成")


if __name__ == "__main__":
    main()
