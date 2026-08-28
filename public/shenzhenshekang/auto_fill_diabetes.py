#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
糖尿病患者随访自动填写脚本

流程：
1. 连接调试Chrome，收集病历首页数据（BMI、血糖、年龄）
2. 点击患者右侧的糖尿病按钮
3. 点击左侧文件夹最末尾的日期，进入随访问卷
4. 复制随访记录、设置下次随访日期、引用共享数据
5. 填写随访方式、目标体重（BMI≥24时）、足背动脉、血糖、随访分类
   （空腹≥7.0或随机/餐后2h≥11.0时选控制不满意，并填转诊：原因=血糖控制不满意，
   机构及科别=龙岗区人民医院内分泌科）、用药情况
6. 保存问卷，处理健康教育弹窗

说明：
- 每一步都会轮询等待目标元素出现后再操作，超时10~15秒判定失败
- 每个步骤成功后等待1秒，给系统反应时间
- 失败的步骤只警告不中断，脚本末尾统一汇总，方便人工补操作
- 点击操作统一使用 Playwright 真实鼠标事件（isTrusted=true），合成 el.click() 在老 ExtJS 上不生效
"""

from playwright.sync_api import sync_playwright
from datetime import datetime
import calendar
import json
import time

FAILED_STEPS = []


def run_step(page, js, desc, timeout=10):
    """轮询执行JS（查找并操作，JS返回真值表示成功），直到成功或超时。
    成功后等待1秒，给系统反应时间。"""
    deadline = time.time() + timeout
    while True:
        try:
            ok = page.evaluate(js)
        except Exception:
            ok = False
        if ok:
            print(f"  [成功] {desc}")
            time.sleep(1)
            return True
        if time.time() >= deadline:
            print(f"  [超时] {desc} —— 未找到目标，请手动处理")
            FAILED_STEPS.append(desc)
            return False
        time.sleep(0.3)


def run_click(page, locate_js, desc, verify_js=None, timeout=10, double=False):
    """轮询执行 locate_js 定位目标元素。locate_js 找到目标时给它打上临时标记
    data-kimi-click="1" 并返回 true，找不到返回 false。点击由 Playwright locator
    完成（真实鼠标事件 isTrusted=true，且自动滚动入视口、等待元素稳定、检测
    接收事件、失败自动重试——比"JS 先算坐标再按坐标点击"稳定，老 ExtJS
    的行选择/弹窗/下拉才认，合成 el.click() 不生效）。
    提供 verify_js 时，点击后轮询 verify_js 确认生效，未生效会重新定位点击。"""
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
                # locator 点击失败（超时/被遮挡/不可点）不能算成功，
                # 继续轮询重试，避免"实际没点上却打印成功"
                pass
            elif verify_js is None:
                print(f"  [成功] {desc}")
                time.sleep(1)
                return True
            time.sleep(0.5)
            try:
                if page.evaluate(verify_js):
                    print(f"  [成功] {desc}")
                    time.sleep(1)
                    return True
            except Exception:
                pass
        if time.time() >= deadline:
            print(f"  [超时] {desc} —— 未找到目标，请手动处理")
            FAILED_STEPS.append(desc)
            return False
        time.sleep(0.3)


def fill_diabetes():
    print("1. 开始启动...")
    with sync_playwright() as p:
        print("2. 尝试连接Chrome...")
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception:
            print("错误：连接失败。请先运行 启动调试Chrome并打开网址.py 并登录系统。")
            return
        print("3. 连接成功!")

        # 找到目标页面
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

        # ========== 收集病历首页数据 ==========
        patient_name = page.evaluate("""
            () => {
                const h2 = document.querySelector('h2.fleft');
                return h2 ? h2.textContent.trim() : '';
            }
        """)
        print(f"患者姓名 = {patient_name}")

        # 体重 - 首页字段ID为 W（高血压脚本同样用这个ID），
        # 需在 BMI 之前采集，后面 BMI>=24 时要用 体重-2 填目标体重
        weight_raw = page.evaluate(
            "() => { const el = document.getElementById('W'); return el ? el.value : ''; }")
        try:
            weight = float(str(weight_raw).split('/')[0])
        except (TypeError, ValueError):
            weight = None
            print(f"  [警告] 体重 无法解析（{weight_raw!r}），将跳过目标体重处理")
        print(f"体重 = {weight}")

        bmi_raw = page.evaluate(
            "() => { const el = document.getElementById('BMI'); return el ? el.value : ''; }")
        try:
            bmi = float(bmi_raw)
        except (TypeError, ValueError):
            bmi = None
            print(f"  [警告] BMI 无法解析（{bmi_raw!r}），将跳过体质指数处理")
        print(f"BMI = {bmi}")

        # 门诊病历首页的血糖字段是 FBS / P2HPG / PBS（全大写ID），
        # fbs_XXX 之类带后缀的是随访问卷里的字段，此处还未打开
        fbs = page.evaluate(
            "() => { const inp = document.getElementById('FBS'); return inp ? inp.value : ''; }")
        p2h = page.evaluate(
            "() => { const inp = document.getElementById('P2HPG'); return inp ? inp.value : ''; }")
        pbs = page.evaluate(
            "() => { const inp = document.getElementById('PBS'); return inp ? inp.value : ''; }")
        print(f"空腹血糖 = {fbs}，餐后2小时 = {p2h}，随机血糖 = {pbs}")

        # 解析数值，用于判断随访分类：
        # 空腹 >= 7.0 或 随机 >= 11.0 或 餐后2h >= 11.0 → 控制不满意
        def _num(v):
            try:
                return float(str(v).strip())
            except (TypeError, ValueError):
                return None

        fbs_n, p2h_n, pbs_n = _num(fbs), _num(p2h), _num(pbs)
        control_unsatisfied = (
            (fbs_n is not None and fbs_n >= 7.0)
            or (p2h_n is not None and p2h_n >= 11.0)
            or (pbs_n is not None and pbs_n >= 11.0)
        )
        print(f"随访分类判定: {'控制不满意' if control_unsatisfied else '控制满意'}")

        # 血糖优先级：空腹 > 餐后2小时 > 随机
        blood_sugar_value = None
        for v in (fbs, p2h, pbs):
            if v and v.strip():
                blood_sugar_value = v.strip()
                break
        print(f"选定血糖值: {blood_sugar_value}")

        age_text = page.evaluate("""
            () => {
                const ps = document.querySelectorAll('p');
                for (let p of ps) {
                    const txt = p.textContent || '';
                    if (txt.includes('年') && txt.includes('龄')) {
                        const match = txt.match(/(\\d+)岁/);
                        if (match) return match[1];
                    }
                }
                return null;
            }
        """)
        if age_text:
            age = int(age_text)
        else:
            age = 49
            print("  [警告] 未取到年龄，按默认 49 岁处理")
        print(f"年龄 = {age}")

        # ========== 1. 点击糖尿病按钮 ==========
        print("5. 点击糖尿病按钮...")
        run_click(page, """
            () => {
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
                const imgs = document.querySelectorAll('img[title="糖尿病"]');
                for (let img of imgs) {
                    const a = img.closest('a');
                    if (a && onScreen(a)) {
                        a.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "点击糖尿病按钮")

        # ========== 2. 点击左侧文件夹最末尾的日期 ==========
        print("6. 选择随访问卷（左侧记录列表最后一行）...")
        # 左侧随访记录是一个窄的x-grid3表格（行内有 yyyy-mm-dd 日期单元格），
        # 不能按"糖尿病患者随访"文字找panel——那会匹配到整个页面，误点其他链接
        run_click(page, r"""
            () => {
                const grids = Array.from(document.querySelectorAll('.x-grid3'));
                const candidates = [];
                for (let g of grids) {
                    const r = g.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    let anc = g, hidden = false;
                    while (anc && anc !== document.body) {
                        if (window.getComputedStyle(anc).display === 'none') { hidden = true; break; }
                        anc = anc.parentElement;
                    }
                    if (hidden) continue;
                    const rows = Array.from(g.querySelectorAll('.x-grid3-row'));
                    if (rows.length === 0) continue;
                    let dateRows = 0;
                    for (let row of rows) {
                        const cells = row.querySelectorAll('.x-grid3-cell-inner');
                        for (let c of cells) {
                            if (/^20\d{2}-\d{2}-\d{2}$/.test((c.textContent || '').trim())) { dateRows++; break; }
                        }
                    }
                    if (dateRows >= 2) candidates.push({g, r, rows});
                }
                if (candidates.length === 0) return false;
                // 取最靠左的候选表格 = 左侧随访记录列表，点击最后一行
                candidates.sort((a, b) => a.r.x - b.r.x || a.r.width - b.r.width);
                const target = candidates[0];
                const lastRow = target.rows[target.rows.length - 1];
                lastRow.setAttribute('data-kimi-click', '1');
                return true;
            }
        """, "选择随访问卷", timeout=15)

        # ========== 3. 点击复制随访记录 ==========
        print("7. 点击复制随访记录...")
        run_click(page, """
            () => {
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
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.textContent.includes('复制随访记录') && onScreen(btn)) {
                        btn.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "复制随访记录", timeout=15)

        # ========== 4. 设置下次随访日期（3个月后，日期超出月末则取月末） ==========
        print("8. 设置下次随访日期...")
        now = datetime.now()
        month = now.month + 3
        year = now.year
        if month > 12:
            month -= 12
            year += 1
        day = min(now.day, calendar.monthrange(year, month)[1])
        next_date = f"{year}-{month:02d}-{day:02d}"
        print(f"下次随访日期: {next_date}")

        # 注意：页面DOM里可能存在多个同名 nextDate_ 输入框（其他问卷模板的隐藏副本），
        # 必须过滤出可见的那个，否则会填到隐藏输入框上，看起来就像没点中
        # 先用真实鼠标点击聚焦输入框，再用 JS 写值
        run_click(page, """
            () => {
                const inputs = Array.from(document.querySelectorAll('input[name^="nextDate_"]'));
                const dateInput = inputs.find(inp => {
                    const r = inp.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return false;
                    let anc = inp;
                    while (anc && anc !== document.body) {
                        if (window.getComputedStyle(anc).display === 'none') return false;
                        anc = anc.parentElement;
                    }
                    return true;
                });
                if (!dateInput) return false;
                dateInput.setAttribute('data-kimi-click', '1');
                return true;
            }
        """, "设置下次随访日期")
        run_step(page, f"""
            () => {{
                const inputs = Array.from(document.querySelectorAll('input[name^="nextDate_"]'));
                const dateInput = inputs.find(inp => {{
                    const r = inp.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return false;
                    let anc = inp;
                    while (anc && anc !== document.body) {{
                        if (window.getComputedStyle(anc).display === 'none') return false;
                        anc = anc.parentElement;
                    }}
                    return true;
                }});
                if (!dateInput) return false;
                dateInput.focus();
                dateInput.select();
                dateInput.value = {json.dumps(next_date)};
                dateInput.style.color = '#000';
                for (const t of ['input', 'change', 'blur']) {{
                    dateInput.dispatchEvent(new Event(t, {{ bubbles: true }}));
                }}
                // 值真正写入才算成功，否则重试
                return dateInput.value === {json.dumps(next_date)};
            }}
        """, "设置下次随访日期")

        # ========== 5. 点击引用共享数据，并确认弹窗 ==========
        print("9. 点击引用共享数据...")
        run_click(page, """
            () => {
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
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.textContent.includes('引用共享数据') && onScreen(btn)) {
                        btn.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "引用共享数据")

        print("9.1 确认个人基本通用信息弹窗（可能有多个，循环确认）...")
        # 注意：
        # 1) 整个主程序本身就是一个全屏 .x-window.x-window-maximized，弹窗是它的后代，
        #    所以必须排除主窗口，并按弹窗自己的标题栏文字匹配，
        #    否则会误点主窗口里的其他"确定(F1)"按钮
        # 2) 引用共享数据后这类弹窗可能连续出现多个（多组数据逐一确认），
        #    必须循环点确定，直到连续两次检查都没有可见弹窗为止
        click_91_js = r"""
            () => {
                const wins = Array.from(document.querySelectorAll('.x-window'));
                const visible = wins.filter(w => {
                    if (w.classList.contains('x-window-maximized')) return false;
                    const s = window.getComputedStyle(w);
                    const r = w.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden'
                        && r.width > 0 && r.height > 0;
                });
                if (visible.length === 0) return false;
                // 优先按窗口标题栏文字匹配
                let target = visible.find(w => {
                    const h = w.querySelector('.x-window-header-text');
                    return h && h.textContent.includes('个人基本通用信息');
                });
                // 其次：内容匹配且不是其他匹配窗口的祖先（取最内层）
                if (!target) {
                    const matches = visible.filter(w => w.textContent.includes('个人基本通用信息'));
                    target = matches.find(w => !matches.some(o => o !== w && w.contains(o)));
                }
                if (!target) return false;
                const btns = target.querySelectorAll('button');
                for (let b of btns) {
                    const txt = (b.textContent || '').replace(/[\s ]/g, '');
                    if (txt.indexOf('确定') === 0) {
                        b.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """
        deadline_91 = time.time() + 25
        clicks_91 = 0
        quiet_91 = 0
        ok_91 = False
        # 每轮先清旧标记，避免残留标记导致 locator 点错元素
        clear_91_js = ("() => document.querySelectorAll('[data-kimi-click]')"
                       ".forEach(e => e.removeAttribute('data-kimi-click'))")
        while time.time() < deadline_91:
            page.evaluate(clear_91_js)
            if page.evaluate(click_91_js):
                # locator 点击"确定"：真实鼠标事件，自动滚动入视口/等待稳定
                try:
                    page.locator('[data-kimi-click="1"]').first.click(timeout=3000)
                except Exception:
                    pass
                page.evaluate(clear_91_js)
                clicks_91 += 1
                quiet_91 = 0
                if clicks_91 >= 6:  # 防止无限弹窗
                    break
                time.sleep(1.2)
            elif clicks_91 > 0:
                quiet_91 += 1
                if quiet_91 >= 2:  # 连续两次检查都没有弹窗，确认完毕
                    ok_91 = True
                    break
                time.sleep(1.0)
            else:
                time.sleep(0.5)  # 等待第一个弹窗出现
        if ok_91:
            print(f"  [成功] 确认个人基本通用信息（共点击 {clicks_91} 次确定）")
        else:
            print("  [超时] 确认个人基本通用信息 —— 请手动处理弹窗")
            FAILED_STEPS.append("确认个人基本通用信息")

        # ========== 6. 随访方式：门诊 ==========
        # 该字段是 name="visitWay" 的数值型单选（1=门诊 2=家庭 3=电话），ID后缀随表单
        # 实例变化（如 visitWay_1_I8MAG），按 name + value=1 + 可见性过滤定位。
        # 不能按文本"门诊"找——会匹配到隐藏input（其相邻文本含"门诊"），点了没用
        print("10. 选择随访方式：门诊...")
        run_click(page, """
            () => {
                const radios = Array.from(document.querySelectorAll('input[type="radio"][name="visitWay"]'));
                const target = radios.find(r => {
                    if (r.value !== '1') return false;
                    let a = r;
                    while (a && a !== document.body) {
                        if (window.getComputedStyle(a).display === 'none') return false;
                        a = a.parentElement;
                    }
                    return true;
                });
                if (!target) return false;
                target.setAttribute('data-kimi-click', '1');
                return true;
            }
        """, "随访方式-门诊", verify_js="""
            () => {
                const radios = Array.from(document.querySelectorAll('input[type="radio"][name="visitWay"]'));
                const target = radios.find(r => {
                    if (r.value !== '1') return false;
                    let a = r;
                    while (a && a !== document.body) {
                        if (window.getComputedStyle(a).display === 'none') return false;
                        a = a.parentElement;
                    }
                    return true;
                });
                return target ? target.checked : false;
            }
        """)

        # ========== 7. 目标体重：BMI >= 24 时填 体重-2 ==========
        # 目标体重字段ID为 targetWeight_XXX（后缀随表单实例变化，同 fbs_XXX 的命名规律），
        # 用 id前缀 + 可见性过滤定位；文档里另有 id="targetW" 的隐藏模板副本，
        # getElementById('targetW') 会拿到它，写了也无效，必须避开。
        # 回读用 parseFloat 比较：ExtJS 数字框失焦后会把 66 格式化成 66.0，严格相等会误判失败
        if bmi is not None and bmi >= 24:
            if weight is not None:
                target_weight = int(weight - 2)
                print(f"11. BMI={bmi} >= 24，目标体重填 {target_weight}（体重{weight}-2）...")
                run_step(page, f"""
                    () => {{
                        const visOk = el => {{
                            const r = el.getBoundingClientRect();
                            if (r.width === 0 || r.height === 0) return false;
                            let a = el;
                            while (a && a !== document.body) {{
                                if (window.getComputedStyle(a).display === 'none') return false;
                                a = a.parentElement;
                            }}
                            return true;
                        }};
                        const el = Array.from(document.querySelectorAll('input[id^="targetWeight_"]')).find(visOk)
                                || Array.from(document.querySelectorAll('input[name="targetWeight"]')).find(visOk);
                        if (!el) return false;
                        el.value = {json.dumps(str(target_weight))};
                        el.style.color = '#000';
                        for (const t of ['input', 'change', 'blur']) {{
                            el.dispatchEvent(new Event(t, {{ bubbles: true }}));
                        }}
                        // 值真正写入才算成功，否则重试
                        return parseFloat(el.value) === {target_weight};
                    }}
                """, f"目标体重填 {target_weight}")
            else:
                print("11. BMI>=24 但未取到体重，跳过目标体重填写")

        # ========== 8. 足背动脉搏动：触及正常 ==========
        # 该字段是 name="pulsation" 的数值型单选（1=触及正常 2=触及减弱 3=触及消失），
        # 没有 value='触及正常' 的输入框；按 value=1 + 可见性过滤点击
        print("12. 选择足背动脉搏动：触及正常...")
        run_click(page, """
            () => {
                const radios = Array.from(document.querySelectorAll('input[type="radio"][name="pulsation"]'));
                const target = radios.find(r => {
                    if (r.value !== '1') return false;
                    let anc = r;
                    while (anc && anc !== document.body) {
                        if (window.getComputedStyle(anc).display === 'none') return false;
                        anc = anc.parentElement;
                    }
                    return true;
                });
                if (!target) return false;
                target.setAttribute('data-kimi-click', '1');
                return true;
            }
        """, "足背动脉-触及正常", verify_js="""
            () => {
                const radios = Array.from(document.querySelectorAll('input[type="radio"][name="pulsation"]'));
                const target = radios.find(r => {
                    if (r.value !== '1') return false;
                    let anc = r;
                    while (anc && anc !== document.body) {
                        if (window.getComputedStyle(anc).display === 'none') return false;
                        anc = anc.parentElement;
                    }
                    return true;
                });
                return target ? target.checked : false;
            }
        """)

        # ========== 9. 辅助检查-血糖：填入空腹，清空随机 ==========
        # 问卷字段ID后缀随表单实例变化（如 fbs_GFAI4），用 id前缀 + 可见性过滤定位，
        # 避开首页的 FBS（大写）和建卡页的 ext-comp-XXXX 隐藏输入框
        if blood_sugar_value:
            print(f"13. 填入血糖值 {blood_sugar_value}...")
            run_step(page, f"""
                () => {{
                    const visOk = el => {{
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) return false;
                        let a = el;
                        while (a && a !== document.body) {{
                            if (window.getComputedStyle(a).display === 'none') return false;
                            a = a.parentElement;
                        }}
                        return true;
                    }};
                    const fbsInp = Array.from(document.querySelectorAll('input[id^="fbs_"]')).find(visOk);
                    const pbsInp = Array.from(document.querySelectorAll('input[id^="pbs_"]')).find(visOk);
                    if (!fbsInp && !pbsInp) return false;
                    if (fbsInp) {{
                        fbsInp.value = {json.dumps(blood_sugar_value)};
                        fbsInp.style.color = "#000";
                        fbsInp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                    if (pbsInp) {{
                        pbsInp.value = "";
                        pbsInp.style.color = "#000";
                        pbsInp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                    return !fbsInp || fbsInp.value === {json.dumps(blood_sugar_value)};
                }}
            """, "填入辅助检查血糖")
        else:
            print("13. 病历首页无血糖值，跳过辅助检查血糖填写")

        # ========== 10. 随访分类：按血糖值判定 ==========
        # ID后缀同样随表单实例变化（如 visitType_1_GFAI4），用前缀 + 可见性过滤
        # 控制满意=visitType_1_*，控制不满意=visitType_2_*
        visit_type_value = '2' if control_unsatisfied else '1'
        visit_type_desc = '控制不满意' if control_unsatisfied else '控制满意'
        print(f"14. 选择随访分类：{visit_type_desc}...")
        run_click(page, f"""
            () => {{
                const inps = Array.from(document.querySelectorAll('input[id^="visitType_{visit_type_value}_"]'));
                const inp = inps.find(el => {{
                    let a = el;
                    while (a && a !== document.body) {{
                        if (window.getComputedStyle(a).display === 'none') return false;
                        a = a.parentElement;
                    }}
                    return true;
                }});
                if (!inp) return false;
                inp.setAttribute('data-kimi-click', '1');
                return true;
            }}
        """, f"随访分类-{visit_type_desc}", verify_js=f"""
            () => {{
                const inps = Array.from(document.querySelectorAll('input[id^="visitType_{visit_type_value}_"]'));
                const inp = inps.find(el => {{
                    let a = el;
                    while (a && a !== document.body) {{
                        if (window.getComputedStyle(a).display === 'none') return false;
                        a = a.parentElement;
                    }}
                    return true;
                }});
                return inp ? (inp.checked || inp.type !== 'radio') : false;
            }}
        """)

        # ========== 10.5 控制不满意时填写转诊信息 ==========
        # 转诊原因、机构及科别是可见的文本输入框，ID后缀与 visitType 相同
        # （如 referralReason_QXKMX / referralOffice_QXKMX），
        # 文档里另有隐藏模板副本（id="referralReason" 无后缀等），必须用 id前缀+可见性过滤
        if control_unsatisfied:
            print("14.1 填写转诊信息：原因=血糖控制不满意，机构及科别=龙岗区人民医院内分泌科...")
            run_step(page, """
                () => {
                    const visOk = el => {
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) return false;
                        let a = el;
                        while (a && a !== document.body) {
                            if (window.getComputedStyle(a).display === 'none') return false;
                            a = a.parentElement;
                        }
                        return true;
                    };
                    const reasonInp = Array.from(document.querySelectorAll('input[id^="referralReason_"]')).find(visOk);
                    const officeInp = Array.from(document.querySelectorAll('input[id^="referralOffice_"]')).find(visOk);
                    if (!reasonInp && !officeInp) return false;
                    if (reasonInp) {
                        reasonInp.value = "血糖控制不满意";
                        reasonInp.style.color = "#000";
                        for (const t of ['input', 'change', 'blur']) {
                            reasonInp.dispatchEvent(new Event(t, { bubbles: true }));
                        }
                    }
                    if (officeInp) {
                        officeInp.value = "龙岗区人民医院内分泌科";
                        officeInp.style.color = "#000";
                        for (const t of ['input', 'change', 'blur']) {
                            officeInp.dispatchEvent(new Event(t, { bubbles: true }));
                        }
                    }
                    return (!reasonInp || reasonInp.value === "血糖控制不满意")
                        && (!officeInp || officeInp.value === "龙岗区人民医院内分泌科");
                }
            """, "填写转诊原因及机构科别")

        # ========== 11. 用药情况：打开并保存 ==========
        print("15. 打开用药情况...")
        run_click(page, """
            () => {
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
                const inputs = document.querySelectorAll('input');
                for (let inp of inputs) {
                    if (inp.value && inp.value.includes('用药情况') && onScreen(inp)) {
                        inp.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "打开用药情况")

        print("15.1 点击用药情况保存按钮...")
        # 页面上同时存在多个"用药情况"窗口（ExtJS 关闭是移到 (-10000,-10000) 而不是销毁），
        # 每个窗口里都有"保存(F1)"按钮。getBoundingClientRect 对屏幕外窗口仍返回非零宽高，
        # 只查宽高会标中隐藏窗口的按钮导致 locator 点击超时。必须先定位屏幕上可见的
        # "用药情况"窗口（标题栏匹配 + onScreen 排除屏幕外），再在它内部找保存按钮
        run_click(page, r"""
            () => {
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
                const win = Array.from(document.querySelectorAll('.x-window')).find(w => {
                    const h = w.querySelector('.x-window-header-text');
                    return h && h.textContent.trim() === '用药情况' && onScreen(w);
                });
                if (!win) return false;
                // 方法1: 窗口内 class含save 且文本为"保存 (F1)"的按钮
                const buttons = win.querySelectorAll('button.x-btn-text.save');
                for (let btn of buttons) {
                    const txt = (btn.textContent || '').replace(/[\s ]/g, '');
                    if (txt.indexOf('保存') === 0 && onScreen(btn)) {
                        btn.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                // 方法2: 窗口内 onclick 包含 zzdSaveBtn
                const onclickBtn = win.querySelector('button[onclick*="zzdSaveBtn"]');
                if (onclickBtn && onScreen(onclickBtn)) {
                    onclickBtn.setAttribute('data-kimi-click', '1');
                    return true;
                }
                return false;
            }
        """, "用药情况-保存", timeout=15)

        # ========== 12. 点击确定（保存问卷） ==========
        print("16. 点击确定，保存问卷...")
        run_click(page, r"""
            () => {
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
                const allBtns = document.querySelectorAll('button.x-btn-text.save');
                for (let b of allBtns) {
                    const txt = (b.textContent || '').replace(/[\s ]/g, '');
                    if (txt === '确定(F1)' && onScreen(b) && !b.disabled) {
                        b.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "保存问卷-确定 (F1)")

        # ========== 13. 健康教育弹窗 ==========
        print("17. 处理健康教育...")
        run_click(page, """
            () => {
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
                const img = document.getElementById("importDiaHER");
                if (img && onScreen(img)) {
                    img.setAttribute('data-kimi-click', '1');
                    return true;
                }
                return false;
            }
        """, "打开健康教育")

        # 列表中E11.900有多条（如“糖尿病（视网膜病变）”），
        # 必须匹配：健康处方名称=糖尿病、疾病名称=2型糖尿病、疾病编码=E11.900 的那一条
        run_click(page, """
            () => {
                const vis = el => {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return false;
                    let a = el;
                    while (a && a !== document.body) {
                        if (window.getComputedStyle(a).display === 'none') return false;
                        a = a.parentElement;
                    }
                    return true;
                };
                const seen = new Set();
                const rows = [];
                document.querySelectorAll('td, div, span').forEach(el => {
                    if (el.children.length === 0 && (el.textContent || '').includes('E11.900')) {
                        const row = el.closest('tr');
                        if (row && !seen.has(row) && vis(row)) { seen.add(row); rows.push(row); }
                    }
                });
                for (const row of rows) {
                    const tds = Array.from(row.querySelectorAll('td'))
                        .map(td => (td.textContent || '').replace(/\\s+/g, '').trim());
                    const all = tds.join('|');
                    // 处方名称列精确等于“糖尿病”（排除“糖尿病（视网膜病变）”）
                    if (tds.includes('糖尿病') && all.includes('2型糖尿病') && all.includes('E11.900')) {
                        row.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "选择E11.900健康教育处方", timeout=15, double=True)

        run_click(page, """
            () => {
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
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.textContent.includes('调入') && onScreen(btn)) {
                        btn.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "调入健康教育处方")

        # 页面上有多个"确定(F1)"按钮（问卷保存、健康教育保存等），
        # 且ExtJS把关闭的窗口移到(-10000,-10000)而不是display:none，
        # 旧逻辑只查宽高会点中屏幕外/其他面板的按钮。
        # 改法：以"打印健康处方(F2)"为锚点，点它旁边那个确定(F1)，并排除屏幕外元素
        run_click(page, """
            () => {
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
                const ref = Array.from(document.querySelectorAll('button'))
                    .find(b => (b.textContent || '').includes('打印健康处方') && onScreen(b));
                if (!ref) return false;
                // 向上找同时包含"打印健康处方"和"确定(F1)"的最近容器（约7层到工具栏行）
                let box = ref;
                for (let i = 0; i < 10 && box; i++) {
                    box = box.parentElement;
                    if (!box) break;
                    const ok = Array.from(box.querySelectorAll('button.x-btn-text.save'))
                        .find(b => (b.textContent || '').replace(/\\s+/g, '') === '确定(F1)' && onScreen(b));
                    if (ok) {
                        ok.setAttribute('data-kimi-click', '1');
                        return true;
                    }
                }
                return false;
            }
        """, "健康教育-确定 (F1)")

        # ========== 汇总 ==========
        print("=" * 40)
        if FAILED_STEPS:
            print(f"完成，但有 {len(FAILED_STEPS)} 个步骤未成功，请手动检查：")
            for s in FAILED_STEPS:
                print(f"  - {s}")
        else:
            print("全部步骤执行成功！")


if __name__ == "__main__":
    fill_diabetes()
