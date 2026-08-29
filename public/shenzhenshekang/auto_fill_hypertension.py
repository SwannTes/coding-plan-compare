#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
高血压患者随访自动填写脚本

流程：
1. 收集病历首页数据（血压、脉搏、体重）
2. 点击高血压按钮，进入随访弹窗
3. 采集医防融合问卷信息（如无则从健康检查表采集）
4. 填写高血压随访问卷
5. 保存
"""

from playwright.sync_api import sync_playwright
import time
import re

def pick_combo_item(page, trigger_xpath, match_text):
    """真实鼠标点击 ExtJS 下拉框的触发箭头，等待选项列表出现后点击包含 match_text 的选项。
    注意：该系统的自定义下拉（mycombox）只有真实点击触发箭头才会加载选项，
    直接 expand()/setValue() 不生效。返回选中的选项文本，未选中返回 None。"""
    try:
        trig = page.locator(trigger_xpath).first
        trig.scroll_into_view_if_needed()
        trig.click()
        for _ in range(20):
            if page.locator('.x-combo-list:visible .x-combo-list-item').count() > 0:
                break
            time.sleep(0.3)
        items = page.locator('.x-combo-list:visible .x-combo-list-item')
        for i in range(items.count()):
            t = items.nth(i).text_content().strip()
            if match_text.upper() in t.upper():
                items.nth(i).click()
                return t
        page.keyboard.press('Escape')
        return None
    except Exception as e:
        print(f"  下拉选择失败({match_text})：{e}")
        return None

def fill_hypertension():
    print("1. 开始启动...")
    with sync_playwright() as p:
        print("2. 尝试连接 Chrome...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
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

        print(f"4. 当前页面标题：{page.title()}")

        # ========== 收集病历首页数据 ==========
        print("5. 收集病历首页数据...")

        # 患者姓名
        patient_name = page.evaluate("""
            () => {
                const h2 = document.querySelector('h2.fleft');
                return h2 ? h2.textContent.trim() : '';
            }
        """)
        print(f"患者姓名 = {patient_name}")

        # 血压 - 使用 SSY(收缩压) 和 SZY(舒张压)
        ssy = page.evaluate("() => { const el = document.getElementById('SSY'); return el ? el.value : ''; }")
        szy = page.evaluate("() => { const el = document.getElementById('SZY'); return el ? el.value : ''; }")
        bp_text = f"{ssy}/{szy}" if ssy and szy else ""
        print(f"血压 = {bp_text}")

        # 脉搏 - 使用 P
        pulse_text = page.evaluate("() => { const el = document.getElementById('P'); return el ? el.value : ''; }")
        print(f"脉搏 = {pulse_text}")

        # 体重 - 使用 W
        weight_text = page.evaluate("() => { const el = document.getElementById('W'); return el ? el.value : ''; }")
        print(f"体重 = {weight_text}")

        # BMI - 从体重和身高计算，或从页面获取
        weight_kg = float(weight_text.split('/')[0]) if weight_text else 0
        # 尝试从多个可能的 ID 获取身高
        height_cm = page.evaluate("""
            () => {
                // 尝试多个可能的身高 ID
                const ids = ['HEIGHT', 'height', 'HEIGHT1', 'Stature'];
                for (const id of ids) {
                    const el = document.getElementById(id);
                    if (el && el.value) return el.value;
                }
                // 尝试查找包含"身高"的 label 附近的输入框
                const labels = document.querySelectorAll('label');
                for (const label of labels) {
                    if (label.textContent.includes('身高')) {
                        const input = label.querySelector('input');
                        if (input && input.value) return input.value;
                    }
                }
                return '';
            }
        """)
        height_m = float(height_cm) / 100 if height_cm else 1.7  # 默认身高 1.7m
        bmi = weight_kg / (height_m ** 2) if height_m > 0 else 22
        print(f"BMI = {bmi:.1f} (体重:{weight_kg}kg, 身高:{height_cm or '默认'}cm)")

        # 辅助检查 - 血糖
        fasting = page.evaluate("() => { const el = document.getElementById('FBS'); return el ? el.value : ''; }")
        after_meal = page.evaluate("() => { const el = document.getElementById('P2HPG'); return el ? el.value : ''; }")
        random_blood = page.evaluate("() => { const el = document.getElementById('PBS'); return el ? el.value : ''; }")
        print(f"空腹血糖 = {fasting}")
        print(f"餐后2小时血糖 = {after_meal}")
        print(f"随机血糖 = {random_blood}")

        # 处方 - 收集患者今日开的药（药名/频次/用量/数量/用法）
        prescription = page.evaluate("""
            () => {
                const meds = [];
                document.querySelectorAll('table.BL_ul tr').forEach(tr => {
                    const cells = tr.querySelectorAll('td');
                    if (cells.length !== 6) return;
                    const name = cells[1].textContent.replace(/\\u00a0/g, ' ').trim();
                    if (!name.includes('/')) return;  // 排除“一般诊疗费”等非药品行
                    meds.push({
                        name: name,
                        freq: cells[2].textContent.trim(),
                        dose: cells[3].textContent.trim(),
                        qty: cells[4].textContent.trim(),
                        usage: cells[5].textContent.trim()
                    });
                });
                return meds;
            }
        """)
        print("今日处方药品：")
        for m in prescription:
            print(f"  {m['name']}  {m['freq']}  {m['dose']}  {m['qty']}  {m['usage']}")

        # ========== 点击高血压档案按钮 ==========
        # 注意：页面中存在两组页头（隐藏的缓存页头 #header2 排在前面），
        # 必须点击可见的那个图标；且用真实鼠标点击（合成 click 可能不触发）
        print("6. 点击高血压档案按钮...")
        gxy_clicked = False
        try:
            gxy_imgs = page.locator('img[title="高血压"]:visible')
            if gxy_imgs.count() > 0:
                gxy_imgs.first.click()
                gxy_clicked = True
        except Exception as e:
            print(f"  高血压图标点击异常：{e}")
        if not gxy_clicked:
            page.evaluate("""
                () => {
                    const imgs = document.querySelectorAll('img[title="高血压"]');
                    for (let img of imgs) {
                        const r = img.getBoundingClientRect();
                        if (r.width === 0 || r.x < 0) continue;  // 跳过隐藏的缓存页头
                        const a = img.closest('a');
                        if (a) { a.click(); return; }
                        img.click();
                        return;
                    }
                }
            """)
        time.sleep(3)

        # ========== 点击高血压患者随访弹窗 ==========
        print("7. 点击高血压患者随访弹窗...")
        page.evaluate("""
            () => {
                const tabs = document.querySelectorAll('li.x-tab-strip-closable');
                for (let tab of tabs) {
                    const textSpan = tab.querySelector('span.x-tab-strip-text');
                    if (textSpan && textSpan.textContent.includes('高血压患者随访')) {
                        const a = tab.querySelector('a.x-tab-right');
                        if (a) a.click();
                        return;
                    }
                }
            }
        """)
        time.sleep(3)

        # ========== 收集数据：先尝试医防融合问卷，否则从健康检查表收集 ==========
        print("8. 收集随访问卷数据...")

        # 选择最近的有"医防融合"标志的问卷
        # 注意：ExtJS 行选择/数据加载依赖完整的鼠标事件序列（mousedown/mouseup/click），
        # 合成 el.click() 只能改高亮、不会触发数据加载，因此先用 JS 找日期，再用 Playwright 真实鼠标点击行
        selected_date = page.evaluate("""
            () => {
                // 查找所有行
                const rows = document.querySelectorAll('div.x-grid3-row');
                let maxDate = '';

                for (let row of rows) {
                    // 检查这一行是否有"医防融合"标志
                    const cells = row.querySelectorAll('td.x-grid3-cell');
                    if (cells.length >= 2) {
                        const typeCell = cells[0].querySelector('div.x-grid3-cell-inner');
                        const dateCell = cells[1].querySelector('div.x-grid3-cell-inner');

                        if (typeCell && typeCell.textContent.trim() === '医防融合' && dateCell) {
                            const text = dateCell.textContent.trim();
                            if (text.match(/^\\d{4}-\\d{2}-\\d{2}$/) && text > maxDate) {
                                maxDate = text;
                            }
                        }
                    }
                }
                return maxDate;
            }
        """)

        yrfh_available = bool(selected_date)
        personal_info = None
        waist = None
        medication = None

        if yrfh_available:
            print(f"已选择医防融合问卷日期：{selected_date}")

            # 用真实鼠标点击目标行，并轮询确认问卷数据已加载（最多重试 3 次）
            data_loaded = False
            for attempt in range(3):
                row = page.locator(
                    f'div.x-grid3-row:has(div.x-grid3-cell-inner:text-is("医防融合"))'
                    f':has(div.x-grid3-cell-inner:text-is("{selected_date}"))'
                ).first
                if row.count() == 0:
                    print(f"警告：未定位到 {selected_date} 的问卷行")
                    break
                row.click()
                # 轮询最多 8 秒，等待 _jzls_visit 字段出现
                for _ in range(16):
                    data_loaded = page.evaluate(
                        "() => document.querySelectorAll('[id$=\"_jzls_visit\"]').length > 0"
                    )
                    if data_loaded:
                        break
                    time.sleep(0.5)
                if data_loaded:
                    break
                print(f"第 {attempt + 1} 次点击后数据未加载，重试...")
                time.sleep(1)

            if not data_loaded:
                print("警告：问卷数据未加载成功，后续采集可能为空")
            time.sleep(2)

            # 收集个人史（吸烟、饮酒、运动、摄盐情况）
            personal_info = page.evaluate("""
                () => {
                    const getText = (id) => {
                        const el = document.getElementById(id);
                        return el ? el.textContent.trim() : '';
                    };
                    const getRadioValue = (name) => {
                        const radios = document.querySelectorAll(`input[name="${name}"]`);
                        for (let r of radios) {
                            if (r.checked) return r.value;
                        }
                        return '';
                    };

                    // 吸烟情况判断：优先 smokingHistory_jzls_visit，兜底任意 smokingHistory* 单选
                    let smokingHistory = getRadioValue('smokingHistory_jzls_visit');
                    if (!smokingHistory) {
                        const r = document.querySelector('input[name^="smokingHistory"]:checked');
                        if (r) smokingHistory = r.value;
                    }
                    let smoking = '';
                    let smokeCount = '';
                    let targetSmokeCount = '';

                    // 从可见页面文本提取“日吸烟量（支）:13”“目标日吸烟量（支）:7”，
                    // 兼容冒号/括号/空格等格式差异（innerText 自动跳过隐藏的缓存节点）。
                    // 第一个“日吸烟量”匹配即当前值（“目标日吸烟量”排在其后）
                    const anchor = document.querySelector('[id$="_jzls_visit"]');
                    let scope = document.body;
                    if (anchor) {
                        let el = anchor;
                        while (el && el !== document.body) {
                            if ((el.innerText || '').includes('日吸烟量')) { scope = el; break; }
                            el = el.parentElement;
                        }
                    }
                    const scopeText = scope.innerText || '';
                    const smokeMatch = scopeText.match(/日吸烟量[^0-9]{0,10}(\\d+)/);
                    const targetSmokeMatch = scopeText.match(/目标日吸烟量[^0-9]{0,10}(\\d+)/);

                    if (smokingHistory && smokingHistory !== '3') {
                        // 吸烟的人（0=几乎每天，1=偶尔，2=已戒烟）
                        smoking = smokingHistory;
                        if (smokeMatch) smokeCount = smokeMatch[1];
                        if (targetSmokeMatch) targetSmokeCount = targetSmokeMatch[1];
                    } else if (smokingHistory === '3') {
                        // 不吸烟的人
                        smoking = '3';
                    } else if (smokeMatch) {
                        // 单选未读到但有日吸烟量，按“几乎每天”处理
                        smoking = '0';
                        smokeCount = smokeMatch[1];
                        if (targetSmokeMatch) targetSmokeCount = targetSmokeMatch[1];
                    }

                    // 饮酒量
                    const drinkCount = getText('drinkCount_jzls_visit');
                    // 运动（次/周 分钟/次）
                    const trainTimes = getText('trainTimesWeek_jzls_visit');
                    const trainMinute = getText('trainMinute_jzls_visit');
                    // 摄盐情况
                    const salt = getText('salt_jzls_visit');
                    // 心理调整
                    const psychology = getText('newPsychologyChange_jzls_visit');
                    // 遵医行为
                    const obeyDoctor = getText('newObeyDoctor_jzls_visit');
                    // 最近7天内是否吸烟（yes/no）
                    const smokingWSeven = getRadioValue('smokingWSeven_jzls_visit');

                    return {
                        smoking,
                        smokeCount,
                        targetSmokeCount,
                        smokingWSeven,
                        drinkCount,
                        trainTimes,
                        trainMinute,
                        salt,
                        psychology,
                        obeyDoctor
                    };
                }
            """)
            print(f"吸烟：{personal_info['smoking']}")
            print(f"日吸烟量：{personal_info['smokeCount']}支")
            print(f"目标日吸烟量：{personal_info.get('targetSmokeCount', '')}支")
            print(f"日饮酒量：{personal_info['drinkCount']}两")
            print(f"运动：{personal_info['trainTimes']}次/周 {personal_info['trainMinute']}分钟/次")
            print(f"摄盐情况：{personal_info['salt']}")
            print(f"心理调整：{personal_info['psychology']}")
            print(f"遵医行为：{personal_info['obeyDoctor']}")

            # 收集腰围
            waist = page.evaluate("""
                () => {
                    // 查找腰围信息，格式为 "腰围:88cm"（冒号前后可能有空白）
                    const labels = document.querySelectorAll('label, lable');
                    for (let label of labels) {
                        const text = label.textContent;
                        const match = text.match(/腰围\\s*[：:]\\s*(\\d+)/);
                        if (match) return match[1] + 'cm';
                    }
                    return '';
                }
            """)
            print(f"腰围 = {waist}")

            # 收集目前用药
            medication = page.evaluate("""
                () => {
                    const table = document.getElementById('chisMedicineTTr2_jzls_visit');
                    if (!table) return '';
                    const rows = table.querySelectorAll('tr');
                    const meds = [];
                    // 跳过第一行表头，从第二行开始
                    for (let i = 1; i < rows.length; i++) {
                        const cells = rows[i].querySelectorAll('td');
                        if (cells.length >= 4) {
                            const name = cells[0].textContent.trim();
                            const dose = cells[1].textContent.trim();
                            const freq = cells[2].textContent.trim();
                            meds.push(name + ' ' + dose + ' ' + freq);
                        }
                    }
                    return meds.join('\\n');
                }
            """)
            print(f"目前用药 = \\n{medication}")
        else:
            print("未找到医防融合问卷，从健康检查表收集数据...")

            # ========== 点击左侧树形菜单的健康体检表 (id=A010101) ==========
            print("8.1 点击健康体检表（树形菜单）...")
            page.evaluate("""
                () => {
                    // 查找树形菜单中的健康检查表节点（ext:tree-node-id="A010101"）
                    const nodes = document.querySelectorAll('.x-tree-node-el');
                    for (let node of nodes) {
                        const treeId = node.getAttribute('ext:tree-node-id');
                        if (treeId === 'A010101') {
                            const anchor = node.querySelector('a.x-tree-node-anchor');
                            if (anchor) anchor.click();
                            console.log('Clicked 健康体检表 tree node: ' + treeId);
                            return;
                        }
                    }
                    console.log('未找到健康体检表树形节点');
                }
            """)
            time.sleep(3)

            # ========== 激活健康体检表 tab ==========
            print("8.2 激活健康体检表 Tab...")
            page.evaluate("""
                () => {
                    const tabs = document.querySelectorAll('li.x-tab-strip-closable');
                    for (let tab of tabs) {
                        const textSpan = tab.querySelector('span.x-tab-strip-text');
                        if (textSpan && (textSpan.textContent.includes('健康检查表') || textSpan.textContent.includes('健康体检表'))) {
                            const a = tab.querySelector('a.x-tab-right');
                            if (a) a.click();
                            return;
                        }
                    }
                }
            """)
            time.sleep(2)

            # ========== 选择第一行记录（最近的体检） ==========
            print("8.3 选择最近的体检记录...")
            page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('div.x-grid3-row');
                    if (rows.length > 0) {
                        rows[0].click();
                    }
                }
            """)
            time.sleep(2)

            # ========== 从健康检查表收集数据 ==========
            print("8.4 从健康检查表收集数据...")
            health_exam_data = page.evaluate("""
                () => {
                    const getValue = (id) => {
                        const el = document.getElementById(id);
                        return el ? (el.value || '') : '';
                    };
                    const getRadioValue = (name) => {
                        // 支持 name 前缀匹配
                        const radios = document.querySelectorAll(`input[name^="${name}"]:checked`);
                        return radios.length > 0 ? radios[0].value : '';
                    };
                    const getRadioValueExact = (name) => {
                        // 精确匹配 name
                        const radios = document.querySelectorAll(`input[name="${name}"]:checked`);
                        return radios.length > 0 ? radios[0].value : '';
                    };

                    // 腰围 - 查询所有 waistline_ 开头的字段，获取第一个非空值
                    let waist = '';
                    const waistInputs = document.querySelectorAll('input[id^="waistline_"]');
                    for (let inp of waistInputs) {
                        if (inp.value && inp.value.trim() !== '') {
                            waist = inp.value.trim();
                            break;
                        }
                    }

                    // 运动频率 - 使用 name 前缀匹配（因为后缀可能变化）
                    let trainTimes = '2';
                    let trainMinute = '30';
                    const exerciseFreqRaw = getRadioValue('physicalExerciseFrequency');
                    if (exerciseFreqRaw === '1') { trainTimes = '7'; trainMinute = '60'; }
                    else if (exerciseFreqRaw === '2') { trainTimes = '3'; trainMinute = '30'; }
                    else if (exerciseFreqRaw === '3') { trainTimes = '1'; trainMinute = '30'; }
                    else if (exerciseFreqRaw === '4') { trainTimes = '0'; trainMinute = '0'; }

                    // 吸烟状况 - 使用 name 前缀匹配
                    const smokingRaw = getRadioValue('wehtherSmoke');
                    let smoking = '3';
                    if (smokingRaw === '1') smoking = '3';  // 从不
                    else if (smokingRaw === '2') smoking = '2';  // 已戒烟
                    else if (smokingRaw === '3') smoking = '0';  // 吸烟

                    // 日吸烟量 - 查找 smokes_ 开头的字段
                    let smokeCount = '';
                    if (smokingRaw === '3') {
                        const smokeCountEl = document.querySelector('input[id^="smokes_"]');
                        if (smokeCountEl && smokeCountEl.value) smokeCount = smokeCountEl.value;
                    }

                    // 饮酒量 - 从 drinkCount 获取
                    let drinkCount = getValue('drinkCount');
                    if (!drinkCount || drinkCount === '') drinkCount = '0';

                    // 摄盐情况 - 精确匹配 name="salt"
                    const saltRaw = getRadioValueExact('salt');
                    let salt = '1';
                    if (saltRaw === '1') salt = '1';  // 轻
                    else if (saltRaw === '2') salt = '2';  // 中
                    else if (saltRaw === '3') salt = '3';  // 重

                    // 心理调整 - 精确匹配 name="newPsychologyChange"
                    const psychologyRaw = getRadioValueExact('newPsychologyChange');
                    let psychology = '1';
                    if (psychologyRaw === '1') psychology = '1';  // 良好
                    else if (psychologyRaw === '2') psychology = '2';  // 一般
                    else if (psychologyRaw === '3') psychology = '3';  // 差

                    // 遵医行为 - 精确匹配 name="newObeyDoctor"
                    const obeyDoctorRaw = getRadioValueExact('newObeyDoctor');
                    let obeyDoctor = '1';
                    if (obeyDoctorRaw === '1') obeyDoctor = '1';  // 良好
                    else if (obeyDoctorRaw === '2') obeyDoctor = '2';  // 一般
                    else if (obeyDoctorRaw === '3') obeyDoctor = '3';  // 差

                    // 目前用药 - 查询所有 name 包含 medicine_ 的字段（排除 medicineYield）
                    const medications = [];
                    for (let i = 1; i <= 20; i++) {
                        // 查找药品名称 - name 格式为 medicine_i_XXX
                        const nameInput = document.querySelector(`input[name^="medicine_${i}_"]`);
                        if (nameInput && nameInput.value && nameInput.value.trim() !== '') {
                            const medName = nameInput.value.trim();
                            // 查找剂量 - id 格式为 eachDose_i_XXX（name 没有后缀）
                            const doseInput = document.querySelector(`input[id^="eachDose_${i}_"]`);
                            const dose = doseInput && doseInput.value ? doseInput.value.trim() : '';
                            // 查找用药频次
                            let freq = '';
                            const freqRadios = document.querySelectorAll(`input[name^="medicineYield${i}"]:checked`);
                            if (freqRadios.length > 0) {
                                const freqVal = freqRadios[0].value;
                                if (freqVal === '1') freq = '每日 1 次';
                                else if (freqVal === '2') freq = '每日 2 次';
                                else if (freqVal === '3') freq = '每日 3 次';
                            }
                            // 组合用药信息
                            let medInfo = medName;
                            if (dose) medInfo += ' ' + dose;
                            if (freq) medInfo += ' ' + freq;
                            medications.push(medInfo);
                        }
                    }

                    return {
                        smokingWSeven: smoking === '0' ? 'yes' : 'no',  // 吸烟者最近7天视为有吸烟
                        rawSmokingHistory: smoking,
                        smoking: smoking,
                        smokeCount: smokeCount,
                        drinkCount: drinkCount,
                        trainTimes: trainTimes,
                        trainMinute: trainMinute,
                        salt: salt,
                        psychology: psychology,
                        obeyDoctor: obeyDoctor,
                        waist: waist,
                        medications: medications.join('\\n')
                    };
                }
            """)
            personal_info = health_exam_data
            waist = health_exam_data.get('waist', '') + 'cm' if health_exam_data.get('waist') and health_exam_data.get('waist') != '' else None
            medication = health_exam_data.get('medications', '')

            print(f"从健康检查表收集：腰围={waist}, 运动={personal_info['trainTimes']}次/周 {personal_info['trainMinute']}分钟/次")
            print(f"  吸烟={personal_info['smoking']}, 日吸烟量={personal_info['smokeCount']}支，饮酒={personal_info['drinkCount']}两")
            print(f"  摄盐={personal_info['salt']}, 心理={personal_info['psychology']}, 遵医={personal_info['obeyDoctor']}")
            if medication:
                print(f"  目前用药：{medication.replace(chr(10), ', ')}")

            # ========== 关闭健康检查表弹窗，返回 ==========
            print("8.5 关闭健康检查表弹窗...")
            page.evaluate("""
                () => {
                    // 点击关闭按钮
                    const closeBtns = document.querySelectorAll('a.x-tab-strip-close');
                    if (closeBtns.length > 0) {
                        closeBtns[closeBtns.length - 1].click();
                    }
                }
            """)
            time.sleep(2)

        # ========== 点击病历首页弹窗 ==========
        # 先检测是否已在高血压随访问卷中（患者可能已手动打开问卷且填了一半），
        # 已在问卷中则跳过打开问卷的步骤，避免重复点击把已选选项取消掉。
        # 注意：前面的步骤已把页面切到档案/随访弹窗，问卷可能在后台标签或被挡住的弹窗里，
        # 因此只要问卷字段存在于 DOM 就尝试激活其所在标签/弹窗，再确认可见
        form_present = page.evaluate("""
            () => {
                const el = document.getElementById('newCurrentSymptoms_9');
                if (!el) return false;
                const win = el.closest('.x-window');
                if (win && typeof Ext !== 'undefined') {
                    // 问卷在 Ext 弹窗里：显示并置前
                    try {
                        const cmp = Ext.getCmp(win.id);
                        if (cmp && cmp.show) cmp.show();
                        if (cmp && cmp.toFront) cmp.toFront();
                    } catch (e) {}
                } else {
                    // 问卷在标签页里：激活对应标签（li id 格式为 标签面板id__面板id）
                    const panel = el.closest('.x-panel');
                    if (panel && panel.id) {
                        const tab = document.querySelector('li[id$="__' + panel.id + '"]');
                        const a = tab ? tab.querySelector('a.x-tab-right') : null;
                        if (a) a.click();
                    }
                }
                return true;
            }
        """)
        already_in_form = False
        if form_present:
            time.sleep(1)
            already_in_form = page.evaluate("""
                () => {
                    const el = document.getElementById('newCurrentSymptoms_9');
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const st = getComputedStyle(el);
                    return r.width > 0 && st.display !== 'none' && st.visibility !== 'hidden';
                }
            """)
        if already_in_form:
            print("9. 检测到已在高血压随访问卷中，跳过打开问卷步骤")
        else:
            print("9. 点击病历首页弹窗...")
            page.evaluate("""
                () => {
                    const li = document.getElementById('ext-comp-2278__ext-comp-2281');
                    if (li) {
                        const a = li.querySelector('a.x-tab-right');
                        if (a) a.click();
                        return;
                    }
                    const allTabs = document.querySelectorAll('li[id^="ext-comp-"]');
                    for (let tab of allTabs) {
                        const textSpan = tab.querySelector('span.x-tab-strip-text');
                        if (textSpan && textSpan.textContent.includes('病历首页')) {
                            const a = tab.querySelector('a.x-tab-right');
                            if (a) a.click();
                            return;
                        }
                    }
                }
            """)
            time.sleep(3)

        # ========== 点击高血压随访按钮 ==========
        if not already_in_form:
            print("10. 点击高血压随访按钮...")
            page.evaluate("""
                () => {
                    const li = document.getElementById('KQGXYYW');
                    if (li) {
                        const a = li.querySelector('a');
                        if (a) a.click();
                    }
                }
            """)
            time.sleep(3)

            # 点击"是否需要完成高血压问卷？"弹窗中的确定
            # 注意：页面中存在多个文本为"确定"的按钮，其中隐藏的"本季度尚未随访"弹窗里的排在前面，
            # 全局顺序查找会先点到隐藏按钮（无效），必须定位到可见弹窗内的确定按钮
            confirm_clicked = ''
            for _ in range(10):
                confirm_clicked = page.evaluate("""
                    () => {
                        const wins = document.querySelectorAll('.x-window-dlg, .x-window');
                        for (const w of wins) {
                            const st = getComputedStyle(w);
                            const r = w.getBoundingClientRect();
                            if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                            const textEl = w.querySelector('.ext-mb-text');
                            if (!textEl || !textEl.textContent.includes('高血压问卷')) continue;
                            for (const b of w.querySelectorAll('button')) {
                                if (b.textContent.trim() === '确定') {
                                    b.click();
                                    return 'clicked_in_dialog';
                                }
                            }
                            return 'no_confirm_btn';
                        }
                        return '';
                    }
                """)
                if confirm_clicked:
                    break
                time.sleep(0.5)
            if not confirm_clicked:
                print("警告：未找到'是否需要完成高血压问卷？'弹窗，未能点击确定")
            time.sleep(3)

        # ========== 填写高血压随访问卷 ==========
        print("12. 填写高血压随访问卷...")

        # 症状 - 无症状 (newCurrentSymptoms_9)
        page.evaluate("() => { const inp = document.getElementById('newCurrentSymptoms_9'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 新发疾病情况 - 无 (pastHistory_02_1101)
        page.evaluate("() => { const inp = document.getElementById('pastHistory_02_1101'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 个人史 - 吸烟 (smokingHistory: 0=几乎每天，1=偶尔，2=已戒烟，3=从不吸烟)
        if personal_info and personal_info.get('smoking'):
            page.evaluate(f"""
                () => {{
                    const radios = document.querySelectorAll('input[name="smokingHistory"]');
                    for (let r of radios) {{
                        if (r.value === '{personal_info['smoking']}' && !r.checked) {{
                            r.click();
                            return;
                        }}
                    }}
                }}
            """)
        time.sleep(0.5)

        # 个人史 - 最近7天内吸烟了吗 (smokingWSeven: yes/no)
        # 优先用医防融合问卷的值；没有则按吸烟状态推断（几乎每天/偶尔=是，已戒烟/从不=否）
        if personal_info:
            wseven = personal_info.get('smokingWSeven') or ''
            if wseven not in ('yes', 'no'):
                wseven = 'yes' if personal_info.get('smoking') in ('0', '1') else 'no'
            page.evaluate(f"""
                () => {{
                    const inp = document.getElementById('smokingWSeven_{wseven}');
                    if (inp && !inp.checked) inp.click();
                }}
            """)
        time.sleep(0.5)

        # 个人史 - 日饮酒量 (drinkCount)
        if personal_info and personal_info.get('drinkCount'):
            page.evaluate(f"""
                () => {{
                    const el = document.getElementById('drinkCount');
                    if (el) {{
                        el.value = '{personal_info['drinkCount']}';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
        time.sleep(0.5)

        # 个人史 - 日吸烟量 (smokeCount)
        if personal_info and personal_info.get('smokeCount'):
            page.evaluate(f"""
                () => {{
                    const el = document.getElementById('smokeCount');
                    if (el) {{
                        el.value = '{personal_info['smokeCount']}';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
        time.sleep(0.5)

        # 个人史 - 目标日吸烟量 (targetSmokeCount)：
        # 患者有吸烟时，目标 = 现吸烟量 - 5（减量控制在 3~5 支），最小为 0
        target_smoke = ''
        if personal_info:
            sc = str(personal_info.get('smokeCount') or '')
            if sc.isdigit() and int(sc) > 0:
                target_smoke = str(max(int(sc) - 5, 0))
        if target_smoke:
            page.evaluate(f"""
                () => {{
                    const el = document.getElementById('targetSmokeCount');
                    if (el) {{
                        el.value = '{target_smoke}';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
        time.sleep(0.5)

        # 个人史 - 运动 (trainTimesWeek, trainMinute)
        if personal_info and personal_info.get('trainTimes'):
            page.evaluate(f"""
                () => {{
                    const el1 = document.getElementById('trainTimesWeek');
                    const el2 = document.getElementById('trainMinute');
                    if (el1) {{
                        el1.value = '{personal_info['trainTimes']}';
                        el1.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                    if (el2) {{
                        el2.value = '{personal_info['trainMinute']}';
                        el2.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
            # 目标运动量：如果运动量≥7/60 则不变，否则 X+2/XX+30
            train_times = int(personal_info['trainTimes']) if str(personal_info['trainTimes']).isdigit() else 0
            train_minute = int(personal_info['trainMinute']) if str(personal_info['trainMinute']).isdigit() else 0
            if train_times >= 7 and train_minute >= 60:
                target_times = train_times
                target_minute = train_minute
            else:
                target_times = min(train_times + 2, 7)
                target_minute = min(train_minute + 30, 60)
            page.evaluate(f"""
                () => {{
                    const el1 = document.getElementById('targetTrainTimesWeek');
                    const el2 = document.getElementById('targetTrainMinute');
                    if (el1) {{
                        el1.value = '{target_times}';
                        el1.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                    if (el2) {{
                        el2.value = '{target_minute}';
                        el2.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
        time.sleep(0.5)

        # 个人史 - 目标日饮酒量 (targetDrinkCount)
        # 如果日饮酒量＞0，则目标饮酒量=饮酒量 -1，最小值 0
        if personal_info and personal_info.get('drinkCount'):
            try:
                drink_count = float(personal_info['drinkCount'])
                if drink_count > 0:
                    target_drink = max(drink_count - 1, 0)
                else:
                    target_drink = 0
                page.evaluate(f"""
                    () => {{
                        const el = document.getElementById('targetDrinkCount');
                        if (el) {{
                            el.value = '{target_drink}';
                            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }}
                """)
            except ValueError:
                pass
        time.sleep(0.5)

        # 个人史 - 摄盐情况 (salt: 1=轻，2=中，3=重)
        if personal_info and personal_info.get('salt'):
            salt_value = ''
            if '轻' in str(personal_info['salt']): salt_value = '1'
            elif '中' in str(personal_info['salt']): salt_value = '2'
            elif '重' in str(personal_info['salt']): salt_value = '3'
            else: salt_value = str(personal_info['salt'])
            if salt_value:
                page.evaluate(f"""
                    () => {{
                        const inp = document.getElementById('salt_{salt_value}');
                        if (inp && !inp.checked) inp.click();
                    }}
                """)
        time.sleep(0.5)

        # 目标摄盐情况 - 默认选择"轻" (new_targetSalt_1)
        page.evaluate("() => { const inp = document.getElementById('new_targetSalt_1'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 心理调整 (newPsychologyChange: 1=良好，2=一般，3=差)
        if personal_info and personal_info.get('psychology'):
            psych_value = ''
            if '良好' in str(personal_info['psychology']): psych_value = '1'
            elif '一般' in str(personal_info['psychology']): psych_value = '2'
            elif '差' in str(personal_info['psychology']): psych_value = '3'
            else: psych_value = str(personal_info['psychology'])
            if psych_value:
                page.evaluate(f"""
                    () => {{
                        const inp = document.getElementById('newPsychologyChange_{psych_value}');
                        if (inp && !inp.checked) inp.click();
                    }}
                """)
        time.sleep(0.5)

        # 遵医行为 (newObeyDoctor: 1=良好，2=一般，3=差)
        if personal_info and personal_info.get('obeyDoctor'):
            obey_value = ''
            if '良好' in str(personal_info['obeyDoctor']): obey_value = '1'
            elif '一般' in str(personal_info['obeyDoctor']): obey_value = '2'
            elif '差' in str(personal_info['obeyDoctor']): obey_value = '3'
            else: obey_value = str(personal_info['obeyDoctor'])
            if obey_value:
                page.evaluate(f"""
                    () => {{
                        const inp = document.getElementById('newObeyDoctor_{obey_value}');
                        if (inp && !inp.checked) inp.click();
                    }}
                """)
        time.sleep(0.5)

        # 体格检查 - 第一次血压 (systolicP_F / diastolicP_F)
        if bp_text:
            bp_parts = bp_text.split('/')
            if len(bp_parts) == 2:
                page.evaluate(f"""
                    () => {{
                        const sysEl = document.getElementById('systolicP_F');
                        const diaEl = document.getElementById('diastolicP_F');
                        if (sysEl) {{
                            sysEl.value = '{bp_parts[0]}';
                            sysEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                        if (diaEl) {{
                            diaEl.value = '{bp_parts[1]}';
                            diaEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }}
                """)
        time.sleep(0.5)

        # 血压 - 收缩压/舒张压 (SSY / SZY)，数值与第一次血压相同
        if bp_text:
            bp_parts = bp_text.split('/')
            if len(bp_parts) == 2:
                page.evaluate(f"""
                    () => {{
                        const ssyEl = document.getElementById('SSY');
                        const szyEl = document.getElementById('SZY');
                        if (ssyEl) {{
                            ssyEl.value = '{bp_parts[0]}';
                            ssyEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                        if (szyEl) {{
                            szyEl.value = '{bp_parts[1]}';
                            szyEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }}
                """)
        time.sleep(0.5)

        # 心率 (HEARTRATE1)
        if pulse_text:
            page.evaluate(f"""
                () => {{
                    const el = document.getElementById('HEARTRATE1');
                    if (el) {{
                        el.value = '{pulse_text}';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
        time.sleep(0.5)

        # 目标体重 (targetW) - BMI≥24 则 -2，否则保持原样精确到 1 位小数
        if weight_text:
            original_weight = float(weight_text.split('/')[0]) if '/' in weight_text else float(weight_text)
            if bmi >= 24:
                target_weight = int(original_weight - 2)
            else:
                target_weight = round(original_weight, 1)
            page.evaluate(f"""
                () => {{
                    const el = document.getElementById('targetW');
                    if (el) {{
                        el.value = '{target_weight}';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
        time.sleep(0.5)

        # 腰围 (waistline) - 数值无单位
        if waist:
            waist_value = waist.replace('cm', '')
            page.evaluate(f"""
                () => {{
                    const el = document.getElementById('waistline');
                    if (el) {{
                        el.value = '{waist_value}';
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)
        time.sleep(0.5)

        # 辅助检查 - 有当天血糖则勾选“血糖”并填数值，否则选“无”
        has_sugar = bool((fasting and fasting.strip()) or (after_meal and after_meal.strip()) or (random_blood and random_blood.strip()))
        if has_sugar:
            print(f"12.1 填写辅助检查血糖：空腹={fasting} 餐后2h={after_meal} 随机={random_blood}")
            page.evaluate(f"""
                () => {{
                    // 勾选“血糖”（checkbox 是隐藏的，JS click 可触发勾选并启用输入框）
                    const cb = document.getElementById('visitExamination_1');
                    if (!cb) return;
                    if (!cb.checked) cb.click();
                    // 注意：病历首页也有 FBS/P2HPG/PBS 同 id 字段，必须限定在辅助检查表格内查找
                    const table = cb.closest('table');
                    const setVal = (id, val) => {{
                        if (!val) return;
                        const inp = table ? table.querySelector('#' + id) : null;
                        if (inp) {{
                            inp.disabled = false;
                            inp.value = val;
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }};
                    setVal('FBS', '{fasting}');
                    setVal('P2HPG', '{after_meal}');
                    setVal('PBS', '{random_blood}');
                }}
            """)
        else:
            page.evaluate("() => { const inp = document.getElementById('visitExamination_0'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 诊断 - 高血压病 (diagnosis_0201)
        page.evaluate("() => { const inp = document.getElementById('diagnosis_0201'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 随访分类 - 控制满意 (newVisitEvaluate_1)
        page.evaluate("() => { const inp = document.getElementById('newVisitEvaluate_1'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 药物不良反应 - 无 (adverseDrugReaction_1)
        page.evaluate("() => { const inp = document.getElementById('adverseDrugReaction_1'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 转诊 - 否 (Referral_n)
        page.evaluate("() => { const inp = document.getElementById('Referral_n'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 本人就诊 - 是 (selfVisit_y)
        page.evaluate("() => { const inp = document.getElementById('selfVisit_y'); if (inp && !inp.checked) inp.click(); }")
        time.sleep(0.5)

        # 目前用药 - 先填既往问卷收集的药品，再填今日处方中药名不重复的药品
        # 用法默认"按说明书口服"，依从性默认"规律服药（80%以上）"
        med_entries = []
        if medication and medication.strip():
            for line in medication.split('\n'):
                parts = line.split()
                if not parts:
                    continue
                med_entries.append({
                    'name': parts[0],
                    'dose': parts[1] if len(parts) > 1 else '',
                    'freq': ' '.join(parts[2:]) if len(parts) > 2 else ''
                })
        # 今日处方：药名取“/”前的部分，跳过与既往用药同名的药品
        existing_names = {e['name'] for e in med_entries}
        for pm in prescription:
            base_name = pm['name'].split('/')[0].strip()
            if base_name in existing_names:
                print(f"  处方药品“{base_name}”既往用药已有，跳过")
                continue
            med_entries.append({'name': base_name, 'dose': pm['dose'], 'freq': pm['freq']})
            existing_names.add(base_name)

        if med_entries:
            print(f"填写目前用药：{', '.join(e['name'] for e in med_entries)}")
            # 先清空表格中残留的行（含上次运行已填的），下面会按最新清单重新填，避免重复
            page.evaluate("""
                () => {
                    const tbl = document.getElementById('chisMedicineTTr2');
                    if (!tbl) return;
                    [...tbl.querySelectorAll('input[name^="drugNames"]')].forEach(inp => {
                        const tr = inp.closest('tr');
                        const del = tr ? [...tr.querySelectorAll('a')].find(a => a.textContent.includes('删除')) : null;
                        if (del) del.click();
                    });
                }
            """)
            time.sleep(0.5)
            for entry in med_entries:
                med_name = entry['name']
                med_dose = entry['dose']
                # 频次：兼容 "QD   每日1次"（问卷/处方）和 "每日 1 次"（体检表）两种收集格式
                freq_text = entry['freq'].replace(' ', '')
                freq_key = ''
                m = re.match(r'^([A-Za-z]+)', freq_text)
                if m:
                    freq_key = m.group(1).lower()
                elif '每日1次' in freq_text:
                    freq_key = 'qd'
                elif '每日2次' in freq_text:
                    freq_key = 'bid'
                elif '每日3次' in freq_text:
                    freq_key = 'tid'

                # 点击"增加"前记录现有行数
                prev_count = page.evaluate("""
                    () => {
                        const tbl = document.getElementById('chisMedicineTTr2');
                        return tbl ? tbl.querySelectorAll('input[name^="drugNames"]').length : -1;
                    }
                """)
                if prev_count < 0:
                    print("  警告：未找到目前用药表格")
                    break
                page.evaluate("""
                    () => {
                        const btn = document.getElementById('chisAddButton1')
                                 || document.querySelector('button.chisAddMedical');
                        if (btn) btn.click();
                    }
                """)
                time.sleep(1)
                # 注意：该“增加”按钮的点击事件被绑定了两次，点一次会新增两行——
                # 取第一个新行填写（返回其真实序号），多余的新空行立即删除，否则无法保存
                row_idx = page.evaluate("""
                    (prevCount) => {
                        const tbl = document.getElementById('chisMedicineTTr2');
                        if (!tbl) return -1;
                        const inputs = [...tbl.querySelectorAll('input[name^="drugNames"]')];
                        const news = inputs.slice(prevCount);
                        if (!news.length) return -1;
                        for (let i = 1; i < news.length; i++) {
                            const tr = news[i].closest('tr');
                            const del = tr ? [...tr.querySelectorAll('a')].find(a => a.textContent.includes('删除')) : null;
                            if (del) del.click();
                        }
                        const m = news[0].name.match(/drugNames(\\d+)/);
                        return m ? parseInt(m[1]) : -1;
                    }
                """, prev_count)
                if row_idx < 0:
                    print("  警告：新增用药行失败")
                    continue
                time.sleep(1)

                # 填药名和单次剂量
                page.evaluate(f"""
                    () => {{
                        // 药名（远程下拉，直接设原始文本值）
                        const drugInp = document.querySelector('input[name="drugNames{row_idx}"]');
                        if (drugInp) {{
                            const c = Ext.getCmp(drugInp.id);
                            if (c && c.setValue) c.setValue('{med_name}');
                            else drugInp.value = '{med_name}';
                        }}
                        // 单次剂量
                        const doseInp = document.getElementById('eachDose{row_idx}');
                        if (doseInp && '{med_dose}') {{
                            doseInp.value = '{med_dose}';
                            doseInp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        }}
                    }}
                """)
                time.sleep(0.5)

                # 频次/用法/依从性：自定义下拉只有真实点击触发箭头才会加载选项，
                # expand()/setValue() 不生效，必须模拟真人点击选择
                freq_match = freq_key.upper() if freq_key else 'QD'
                picked_freq = pick_combo_item(
                    page,
                    f'xpath=//div[@id="div_frequency{row_idx}"]//img[contains(@class,"x-form-trigger")]',
                    freq_match)
                time.sleep(0.5)
                picked_usage = pick_combo_item(
                    page,
                    f'xpath=//div[@id="div_usage{row_idx}"]//img[contains(@class,"x-form-trigger")]',
                    '按说明书口服')
                time.sleep(0.5)
                picked_comp = pick_combo_item(
                    page,
                    f'xpath=//input[@name="drugNames{row_idx}"]/ancestor::tr[1]/td[5]//img[contains(@class,"x-form-trigger")]',
                    '规律服药')
                print(f"  已填写第 {row_idx + 1} 行：药名={med_name} 剂量={med_dose} 频次={picked_freq} 用法={picked_usage} 依从性={picked_comp}")
                time.sleep(0.5)

        # ========== 保存 ==========
        # 注意：页面中有多个“保存”元素（如初步诊断旁的保存按钮），全局找 button 会先点错；
        # 真正的保存是顶部工具栏的 li.topBtn#SV，且用真实鼠标点击更可靠
        print("11. 保存...")
        save_result = ''
        try:
            sv = page.locator('li.topBtn#SV').first
            if sv.count() > 0 and sv.is_visible():
                sv.click()
                save_result = 'clicked_SV_toolbar'
        except Exception as e:
            print(f"  工具栏保存按钮点击异常：{e}")
        if not save_result:
            save_result = page.evaluate("""
                () => {
                    // 兜底：点击可见的、文本仅为“保存”的按钮
                    for (const btn of document.querySelectorAll('button, li.topBtn, a')) {
                        if (btn.textContent.trim() !== '保存') continue;
                        const st = getComputedStyle(btn);
                        const r = btn.getBoundingClientRect();
                        if (st.display !== 'none' && st.visibility === 'visible' && r.width > 0) {
                            btn.click();
                            return 'clicked_visible_save';
                        }
                    }
                    return '';
                }
            """)
        if not save_result:
            print("警告：未找到保存按钮")
        time.sleep(2)

        print("完成!")


if __name__ == "__main__":
    fill_hypertension()
