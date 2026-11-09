#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""隔物灸记账明细 单行处理（254~505 任务段）

用法: python gewujiu_step.py <起始行号> [表格路径]
  表格路径省略时在桌面按"隔物灸…记账明细表.xlsx"找；表格不在桌面或换了名字就显式传路径。
流程（与用户约定一致）：
1. 读该行（及同就诊的配对行）的 姓名/身份证/费用发生时间
2. 就诊历史记录：输入姓名+日期 → 搜索 → 判断 收费总金额 是否等于 缴费金额
   - 不一致 → 追加到 桌面/隔物灸_费用不一致待审核.xlsx，不填表，结束
3. 点击患者行 → 病历信息 → 收集 初步诊断/处理措施/管理计划 分析：
   - 处理措施无隔物灸法 → 人工审核列表
   - 治疗计划含穴位类(督脉/膀胱经/夹脊/阿是穴/经验穴) → "病历中已涉及相关穴位…N穴"
   - 治疗计划含 部位+艾灸 → 按部位+诊断套模板（颈肩腰背73穴/腹39穴/腰背+腹112穴）
   - 都没有 → 诊断/主诉/既往史含六病(感冒/咳嗽/鼻鼽/痛痹/腰痛/胃痞)按病套模板，否则认可违规
4. 打印病历(F5) → 屏幕截图(第一页) → 关闭弹窗 → 存 桌面/病历截图/
5. 写回表格 AM(是否违规)/AP(说明)，并把裁剪后的截图以 DISPIMG 嵌入 AQ(病例截图)
"""

from playwright.sync_api import sync_playwright
from PIL import Image, ImageGrab
import json
import os
import re
import sys
import time
import uuid
import random
import zipfile
import shutil
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fill_gewujiu_rows import (PANEL_JS, MARK_PRINT_BTN_JS, RECORD_TEXT_JS,
                               wait_preview, close_preview, classify)
from extract_gewujiu_own import run_click
import ctypes
user32 = ctypes.windll.user32

BASE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
SHOT_DIR = os.path.join(DESKTOP, "病历截图")
REVIEW_XLSX = os.path.join(DESKTOP, "隔物灸_费用不一致待审核.xlsx")
BAK = os.path.join(BASE, "_backup_记账明细表.xlsx")
SRC = None

TAIL = "治疗穴位数量大于收费数量，隔物灸法根据《麓园社区健康服务中心隔物灸操作流程及规范》操作。"


def find_src(path=None):
    """定位记账明细表：优先用命令行传入的路径；否则在桌面按文件名特征找。
    找不到时给出明确提示（表格不在桌面/被改名/被移走时不会闷死）。"""
    if path:
        if os.path.isfile(path):
            return path
        raise SystemExit(f"指定的表格不存在: {path}")
    for f in os.listdir(DESKTOP):
        if "隔物灸" in f and "记账明细表" in f and f.endswith(".xlsx") and not f.startswith("~$"):
            return os.path.join(DESKTOP, f)
    raise SystemExit("桌面找不到隔物灸记账明细表。请把表格放到桌面，"
                     "或用参数指定路径，如: python gewujiu_step.py 254 \"D:\\路径\\表.xlsx\"")


def wps_open():
    """表格是否被 WPS 打开（锁定文件在表格同目录）。"""
    if not SRC:
        return False
    lock = os.path.join(os.path.dirname(SRC), "~$" + os.path.basename(SRC))
    return os.path.exists(lock)


def read_visit(row0):
    from openpyxl import load_workbook
    wb = load_workbook(SRC, data_only=True)
    ws = wb["明细"]
    name = str(ws.cell(row0, 2).value or "").strip()
    date = str(ws.cell(row0, 10).value or "")[:10]
    sfzh = str(ws.cell(row0, 3).value or "")
    rows = [row0]
    # 配对行（同姓名同时间的相邻行）
    r = row0 + 1
    while r <= ws.max_row and str(ws.cell(r, 2).value or "").strip() == name \
            and str(ws.cell(r, 10).value or "")[:10] == date:
        rows.append(r)
        r += 1
    qtys = [ws.cell(x, 24).value for x in rows]
    amts = [ws.cell(x, 36).value for x in rows]
    return {"name": name, "date": date, "sfzh": sfzh, "rows": rows,
            "qtys": qtys, "amts": amts}


# ---------------- 浏览器 ----------------

def search_patient(page, name, date):
    """输入姓名+日期并搜索，返回当天挂号记录列表。"""
    page.bring_to_front()
    # 查询条件下拉可能是 门诊号码/姓名/性质 等，先确保切到"姓名"（BRXM）
    page.evaluate("() => {" + PANEL_JS + r"""
        const pel = panelEl();
        if (!pel) return false;
        if (pel.querySelector('input[name="BRXM"]')) return true;
        let combo = null;
        Ext.ComponentMgr.all.each(c => {
            if (combo) return;
            if (c instanceof Ext.form.ComboBox && c.el && c.el.dom && pel.contains(c.el.dom)
                && ['BRXM','MZHM','BRXZ','BRXB','YSDM','ZYZD','cflx'].includes(String(c.getValue())))
                combo = c;
        });
        if (!combo) return false;
        const store = combo.getStore();
        const idx = store.findExact('value', 'BRXM');
        if (idx < 0) return false;
        combo.setValue('BRXM');
        combo.fireEvent('select', combo, store.getAt(idx), idx);
        return !!pel.querySelector('input[name="BRXM"]');
    }""")
    ok = page.evaluate("() => {" + PANEL_JS + r"""
        const pel = panelEl();
        if (!pel) return false;
        const el = Array.from(pel.querySelectorAll('input[name="BRXM"]')).find(onScreen);
        if (!el) return false;
        el.setAttribute('data-kimi-click', '1');
        return true;
    }""")
    if not ok:
        return None
    page.locator('[data-kimi-click="1"]').first.click(timeout=3000)
    page.evaluate("() => document.querySelectorAll('[data-kimi-click]')"
                  ".forEach(e => e.removeAttribute('data-kimi-click'))")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    if name:
        page.keyboard.type(name, delay=30)
    page.keyboard.press("Tab")
    time.sleep(0.5)
    ok = page.evaluate("(d) => {" + PANEL_JS + r"""
        const pel = panelEl();
        const setDate = (id, val) => {
            const c = Ext.getCmp(pel.querySelector('#' + id).id);
            c.setValue(val); return c.getRawValue() === val;
        };
        return setDate('startDate', d) && setDate('endDate', d);
    }""", date)
    if not ok:
        return None
    page.evaluate("() => {" + PANEL_JS + r"""
        const g = findGrid();
        window.__searchDone = false;
        g.getStore().on('load', () => { window.__searchDone = true; });
        const pel = panelEl();
        pel.querySelector('button.query').setAttribute('data-kimi-click','1');
        return true;
    }""")
    page.locator('[data-kimi-click="1"]').first.click(timeout=3000)
    page.evaluate("() => document.querySelectorAll('[data-kimi-click]')"
                  ".forEach(e => e.removeAttribute('data-kimi-click'))")
    for _ in range(30):
        if page.evaluate("() => window.__searchDone === true"):
            break
        time.sleep(1)
    else:
        return None
    return page.evaluate("() => {" + PANEL_JS + r"""
        const g = findGrid();
        const rows = [];
        g.getStore().each(rec => rows.push({
            GHXH: rec.data.GHXH, BRXM: rec.data.BRXM, SFZH: rec.data.SFZH,
            GHSJ: rec.data.GHSJ, YSDM: rec.data.YSDM_text,
            ZJJE: rec.data.ZJJE, JFJE: rec.data.JFJE
        }));
        return rows;
    }""")


def sfzh_match(full, masked):
    if not masked or not full or "*" not in masked:
        return True
    return full[:6] == masked[:6] and full[-6:] == masked[-6:]


def click_row_and_get_record(page, ghxh):
    """点击指定挂号行，返回病历文本（未加载返回 ''）。
    先用 Ext selectRow+fireEvent('rowclick')（纯 JS，避免真实鼠标被弹窗卡死），
    并校验"当前选中行"的病历确实展开；未展开再退回真实鼠标点击。
    多挂号场景必须读到选中行自己的病历（同一 grid 里会残留其他挂号的隐藏病历）。"""
    ok = page.evaluate("(g) => {" + PANEL_JS + r"""
        const grid = findGrid();
        const idx = grid.getStore().findExact('GHXH', g);
        if (idx < 0) return false;
        grid.getSelectionModel().selectRow(idx);   // 打印病历按"选中行"打印，必须先选中
        grid.fireEvent('rowclick', grid, idx);     // 触发病历信息加载
        return true;
    }""", ghxh)
    if ok:
        time.sleep(2.5)
        text = page.evaluate(RECORD_TEXT_JS) or ""
        if "主诉" in text:
            return text
    # fireEvent 没展开病历（多挂号时偶发），退回真实点击
    ok = page.evaluate("(g) => {" + PANEL_JS + r"""
        const grid = findGrid();
        const idx = grid.getStore().findExact('GHXH', g);
        if (idx < 0) return false;
        const row = grid.getView().getRow(idx);
        if (!row) return false;
        row.setAttribute('data-kimi-click', '1');
        return true;
    }""", ghxh)
    if ok:
        try:
            page.locator('[data-kimi-click="1"]').first.click(timeout=3000)
        except Exception:
            pass
        page.evaluate("() => document.querySelectorAll('[data-kimi-click]')"
                      ".forEach(e => e.removeAttribute('data-kimi-click'))")
        time.sleep(2.5)
        return page.evaluate(RECORD_TEXT_JS) or ""
    return ""


def shoot_print_preview(page, name, date, rows_tag):
    """点打印病历，截第一页，关闭弹窗，返回截图路径。
    打印按钮必须真实鼠标点击（合成 click 无反应，实测）。"""
    close_preview()
    if not run_click(page, MARK_PRINT_BTN_JS, "打印病历", timeout=8, quiet=True):
        return None
    w = wait_preview(15)
    if not w:
        return None
    user32.ShowWindow(w[0], 3)
    time.sleep(1.5)
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.join(SHOT_DIR, f"{rows_tag}_{name}_{date}_p1.png")
    ImageGrab.grab().save(path)
    close_preview()
    return path


# ---------------- 人工审核列表 ----------------

def add_review(visit, doctor, zjje, jfje, reason):
    from openpyxl import Workbook, load_workbook
    if os.path.exists(REVIEW_XLSX):
        wb = load_workbook(REVIEW_XLSX)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "待审核"
        ws.append(["行号", "姓名", "身份证号", "费用日期", "就诊医生",
                   "收费总金额", "缴费金额", "原因"])
    ws.append([f"{visit['rows'][0]}-{visit['rows'][-1]}", visit["name"], visit["sfzh"],
               visit["date"], doctor or "", zjje if zjje is not None else "",
               jfje if jfje is not None else "", reason])
    wb.save(REVIEW_XLSX)
    print(f"  → 已加入人工审核表: {REVIEW_XLSX}（{reason}）")


# ---------------- 写回表格 ----------------

def write_rows(visit, status, col42, shot_path):
    """status: ok(不违规) / wgui(违规-认可违规)。截图裁剪后嵌入 AQ。"""
    if wps_open():
        print("  !! 表格正在 WPS 中打开，无法写回。请关闭后重跑本行。")
        return False
    if not os.path.exists(BAK):
        shutil.copy2(SRC, BAK)

    img = Image.open(shot_path).crop((690, 155, 1245, 960))
    crop_path = os.path.join(BASE, "_aq_tmp.png")
    img.save(crop_path)
    with open(crop_path, "rb") as f:
        img_bytes = f.read()
    W, H = img.size
    cy = round(9144000 * H / W)

    z = zipfile.ZipFile(SRC)
    items = {n: z.read(n) for n in z.namelist()}
    names = z.namelist()
    z.close()
    sheet = items["xl/worksheets/sheet1.xml"].decode("utf-8")
    ci = items["xl/cellimages.xml"].decode("utf-8")
    cirels = items["xl/_rels/cellimages.xml.rels"].decode("utf-8")

    def col_idx(letters):
        n = 0
        for ch in letters:
            n = n * 26 + ord(ch) - 64
        return n

    def upsert(rowxml, rowno, col, content):
        ref = f"{col}{rowno}"
        m = re.search(r'<c r="' + ref + r'"(?:[^>]*>(?:(?!</c>).)*?</c>|[^>]*/>)', rowxml)
        if m:
            return rowxml[:m.start()] + content + rowxml[m.end():]
        target = col_idx(col)
        at = len(rowxml) - len("</row>")
        for cm in re.finditer(r'<c r="([A-Z]+)\d+"', rowxml):
            if col_idx(cm.group(1)) > target:
                at = cm.start()
                break
        return rowxml[:at] + content + rowxml[at:]

    for i, rowno in enumerate(visit["rows"], 1):
        rid = max(int(x) for x in re.findall(r'Id="rId(\d+)"', cirels)) + 1
        imgno = max(int(x) for x in re.findall(r'media/image(\d+)\.', cirels)) + 1
        cid = max(int(x) for x in re.findall(r'<xdr:cNvPr id="(\d+)"', ci)) + 1
        dispid = "ID_" + uuid.uuid4().hex.upper()
        descr = "post_object_image_" + str(random.randint(10**9, 10**10 - 1))

        m = re.search(r'<row r="' + str(rowno) + r'"[^>]*>(?:(?!</row>).)*?</row>', sheet)
        rowxml = m.group(0)
        if status == "ok":
            rowxml = upsert(rowxml, rowno, "AM",
                            f'<c r="AM{rowno}" s="12" t="inlineStr"><is><t>不违规</t></is></c>')
            rowxml = upsert(rowxml, rowno, "AP",
                            f'<c r="AP{rowno}" s="13" t="inlineStr"><is><t xml:space="preserve">'
                            f'{escape(col42)}</t></is></c>')
        else:
            qty, amt = visit["qtys"][i - 1], visit["amts"][i - 1]
            rowxml = upsert(rowxml, rowno, "AM",
                            f'<c r="AM{rowno}" s="12" t="inlineStr"><is><t>违规</t></is></c>')
            rowxml = upsert(rowxml, rowno, "AN", f'<c r="AN{rowno}"><v>{qty}</v></c>')
            rowxml = upsert(rowxml, rowno, "AO", f'<c r="AO{rowno}"><v>{amt}</v></c>')
            rowxml = upsert(rowxml, rowno, "AP",
                            f'<c r="AP{rowno}" s="14" t="inlineStr"><is><t>认可违规</t></is></c>')
        rowxml = upsert(rowxml, rowno, "AQ",
                        f'<c r="AQ{rowno}" t="str"><f>_xlfn.DISPIMG(&quot;{dispid}&quot;,1)</f>'
                        f'<v>=DISPIMG(&quot;{dispid}&quot;,1)</v></c>')
        sheet = sheet[:m.start()] + rowxml + sheet[m.end():]

        entry = (f'<etc:cellImage><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{cid}" name="{dispid}" descr="{descr}"/>'
                 f'<xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId{rid}"/>'
                 f'<a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:xfrm>'
                 f'<a:off x="0" y="0"/><a:ext cx="9144000" cy="{cy}"/></a:xfrm>'
                 f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic></etc:cellImage>')
        ci = ci.replace("</etc:cellImages>", entry + "</etc:cellImages>", 1)
        rel = (f'<Relationship Id="rId{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
               f'Target="media/image{imgno}.png"/>')
        cirels = cirels.replace("</Relationships>", rel + "</Relationships>", 1)
        items[f"xl/media/image{imgno}.png"] = img_bytes
        names.append(f"xl/media/image{imgno}.png")

    items["xl/worksheets/sheet1.xml"] = sheet.encode("utf-8")
    items["xl/cellimages.xml"] = ci.encode("utf-8")
    items["xl/_rels/cellimages.xml.rels"] = cirels.encode("utf-8")
    tmp = SRC + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in names:
            zo.writestr(n, items[n])
    os.replace(tmp, SRC)
    z2 = zipfile.ZipFile(SRC)
    assert z2.testzip() is None
    return True


# ---------------- 主流程 ----------------

CACHE_FILE = os.path.join(BASE, "_step_cache.json")


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(c):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)


def do_write(rows_tag):
    """按缓存的判定写回表格（不碰浏览器）。"""
    c = load_cache().get(rows_tag)
    if not c:
        print("  无缓存判定")
        return False
    visit = c["visit"]
    ok = write_rows(visit, "ok" if c["status"] == "fill" else "wgui", c["tpl"], c["shot"])
    if ok:
        print(f"  已写回行{rows_tag}: {'不违规' if c['status'] == 'fill' else '违规(认可违规)'}")
        print(f"  AP: {c['tpl'][:80]}...")
    return ok


def main():
    global SRC
    SRC = find_src(sys.argv[2] if len(sys.argv) > 2 else None)
    row0 = int(sys.argv[1])
    visit = read_visit(row0)
    rows_tag = f"{visit['rows'][0]}-{visit['rows'][-1]}"
    print(f"行{rows_tag}: {visit['name']} {visit['date']}")

    cache = load_cache()
    if rows_tag in cache:
        print(f"  命中缓存判定: {cache[rows_tag]['status']}，直接写回")
        do_write(rows_tag)
        return

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

        recs = search_patient(page, visit["name"], visit["date"])
        if recs is None:
            print("  搜索失败")
            return
        cands = [c for c in recs if str(c.get("GHSJ") or "")[:10] == visit["date"]
                 and sfzh_match(visit["sfzh"], str(c.get("SFZH") or ""))]
        if not cands:
            add_review(visit, "", None, None, "就诊历史记录中未找到当天挂号")
            return
        for c in cands:
            print(f"  挂号 {c['GHSJ']} 医生={c['YSDM']} 总金额={c['ZJJE']} 缴费={c['JFJE']}")
        paid = [c for c in cands if abs((c.get("ZJJE") or 0) - (c.get("JFJE") or 0)) < 0.005]
        if not paid:
            c0 = cands[0]
            add_review(visit, c0["YSDM"], c0["ZJJE"], c0["JFJE"],
                       f"收费不一致 总金额{c0['ZJJE']}≠缴费{c0['JFJE']}")
            return

        record, hit = "", None
        for cand in paid:
            text = click_row_and_get_record(page, cand["GHXH"])
            if "隔物灸法" in text:
                record, hit = text, cand
                break
        if hit is None:
            add_review(visit, cands[0]["YSDM"], cands[0]["ZJJE"], cands[0]["JFJE"],
                       "已缴清挂号中未找到隔物灸医嘱（或病历读取失败）")
            return

        status, tpl, note = classify(record)
        print(f"  判定: {status} | {note}")
        if status == "review":
            add_review(visit, hit["YSDM"], hit["ZJJE"], hit["JFJE"], note)
            return

        shot = shoot_print_preview(page, visit["name"], visit["date"], rows_tag)
        if not shot:
            print("  截图失败，不写回")
            return
        print(f"  截图: {shot}")

        cache[rows_tag] = {"status": status, "tpl": tpl, "shot": shot, "visit": visit,
                           "note": note}
        save_cache(cache)
        do_write(rows_tag)


if __name__ == "__main__":
    main()
