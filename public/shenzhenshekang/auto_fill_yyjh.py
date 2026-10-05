#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
老年人医养结合服务随访问卷自动填写脚本

流程：
1. 从病历首页收集患者姓名、血压、血糖数据
2. 点击健康档案下的医养结合服务链接（若问卷表单已打开则直接填当前表单）
3. 填写问卷并保存

注意：页面会残留隐藏的旧问卷表单（同一字段在 DOM 里有多份，仅一份可见），
且各字段的 ID 后缀可能不一致。所有元素定位必须只取可见元素、按 ID 前缀查找，
否则会填进隐藏表单或对不上字段，导致"脚本运行成功但实际什么都没填上"。
"""

from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
import json
import sys
import time


def fail(msg):
    """关键步骤失败：打印原因并以非零码退出，让面板/日志能看到失败"""
    print(f"失败：{msg}")
    sys.exit(1)


# 只匹配可见元素的 JS 助手（页面残留隐藏缓存表单，必须过滤）
JS_HELPERS = """
    const __vis = e => {
        const st = getComputedStyle(e);
        const r = e.getBoundingClientRect();
        return st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0;
    };
    const __byPrefix = p => Array.from(document.querySelectorAll('[id^="' + p + '"]')).find(__vis) || null;
"""


def js(body):
    """包装 evaluate 用的 JS 函数体，预置可见元素助手 __vis / __byPrefix"""
    return "() => {" + JS_HELPERS + body + "}"


def fill_yyjh():
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
            fail(f"未找到包含 {target_url} 的页面")

        print(f"4. 当前页面标题：{page.title()}")

        # ========== 收集患者信息 ==========
        # 获取患者姓名
        patient_name = page.evaluate("""
            () => {
                // 限定在当前活跃页头 #emr_header 内查找，避免读到隐藏缓存页头(#header2)里的旧病人
                const header = document.getElementById('emr_header');
                const h2 = (header && header.querySelector('h2.fleft')) || document.querySelector('h2.fleft');
                return h2 ? h2.textContent.trim() : '';
            }
        """)
        print(f"患者姓名 = {patient_name}")

        # 获取收缩压 (SSY)
        constriction = page.evaluate("""
            () => {
                const inp = document.getElementById('SSY');
                return inp ? inp.value : '';
            }
        """)
        print(f"收缩压 = {constriction}")

        # 获取舒张压 (SZY)
        diastolic = page.evaluate("""
            () => {
                const inp = document.getElementById('SZY');
                return inp ? inp.value : '';
            }
        """)
        print(f"舒张压 = {diastolic}")

        # 获取空腹血糖 (FBS)
        fasting = page.evaluate("""
            () => {
                const inp = document.getElementById('FBS');
                return inp ? inp.value : '';
            }
        """)
        print(f"空腹血糖 = {fasting}")

        # 获取餐后 2h 血糖 (P2HPG)
        after_meal = page.evaluate("""
            () => {
                const inp = document.getElementById('P2HPG');
                return inp ? inp.value : '';
            }
        """)
        print(f"餐后 2h 血糖 = {after_meal}")

        # 获取随机血糖 (PBS)
        random_blood = page.evaluate("""
            () => {
                const inp = document.getElementById('PBS');
                return inp ? inp.value : '';
            }
        """)
        print(f"随机血糖 = {random_blood}")

        # 如果没有收集到血糖，默认空腹血糖 5.9
        if not (fasting and fasting.strip()) and not (after_meal and after_meal.strip()) and not (random_blood and random_blood.strip()):
            fasting = '5.9'
            print("未收集到血糖数据，默认空腹血糖 5.9")

        # ========== 4.0 确保健康档案（健康标志/既往病史）已加载 ==========
        # 健康标志复选框只在"健康档案"标签被打开后才会加载进 DOM；
        # 不加载就读取会把有慢病病史的患者误判为"未选中任何疾病"。
        # 已验证：点击该标签不会隐藏已打开的问卷表单，可安全调用。
        has_mark = page.evaluate("""
            () => !!document.querySelector('input[id^="healthMark_check_"], input[id^="diseasetext_check_jb_"]')
        """)
        if not has_mark:
            print("4.0 健康标志未加载，点击'健康档案'标签...")
            page.evaluate("""
                () => { const a = document.getElementById('JKDA'); if (a) a.click(); }
            """)
            for _ in range(20):
                has_mark = page.evaluate("""
                    () => !!document.querySelector('input[id^="healthMark_check_"], input[id^="diseasetext_check_jb_"]')
                """)
                if has_mark:
                    break
                time.sleep(0.5)
        if not has_mark:
            print("警告：仍未找到健康标志复选框，既往病史读取失败（患者可能未建档），重点人群/康复指导将按'无病史'处理")

        # ========== 4.1 判断患者是否为重点人群 ==========
        # 慢病/重点疾病复选框 ID 映射 - 两处位置：healthMark_check 和 diseasetext_check_jb
        DISEASE_CHECKBOXES = {
            "0202": "高血压",
            "0203": "2 型糖尿病",
            "0207": "脑卒中",
            "0204": "冠心病",
            "0221": "脑血管病后遗症",
            "0205": "慢性阻塞性肺疾病",
            "0215": "哮喘",
            "0214": "尿毒症",
            "0208": "严重精神障碍",
            "0217": "失能",
            "0218": "失智"
        }

        print("\n=== 4.1 重点人群判断 ===")
        is_key_population = False
        matched_diseases = []

        for code, disease_name in DISEASE_CHECKBOXES.items():
            # 检查两处复选框：healthMark_check 和 diseasetext_check_jb
            # 注意：ID后缀（如 _GNUIB）每份档案都不同，且同前缀下还有 _Control_1（稳定/不稳定单选）
            # 和 _qx（取消）等干扰控件，必须用"精确代码+后缀"的正则匹配，只看checkbox
            is_checked = page.evaluate(f"""
                () => {{
                    const re = /^(healthMark_check_|diseasetext_check_jb_){code}_[A-Za-z0-9]+$/;
                    return Array.from(document.querySelectorAll('input[type="checkbox"]'))
                        .some(cb => re.test(cb.id) && cb.checked);
                }}
            """)
            if is_checked:
                is_key_population = True
                matched_diseases.append(disease_name)
                print(f"  [选中] {disease_name}")

        if is_key_population:
            print(f"结论：{patient_name}属于重点人群")
            print(f"匹配疾病：{', '.join(matched_diseases)}")
        else:
            print(f"结论：{patient_name}不属于重点人群（未选中任何慢病/重点疾病）")
        print("=" * 40)

        # ========== 5. 打开问卷表单 ==========
        # 若问卷表单已打开（例如上次没保存成功、或人工已点开），直接填当前表单，
        # 不再点链接/增加，避免重复新增记录
        form_open = page.evaluate(js("return !!__byPrefix('constriction_');"))

        if form_open:
            print("5. 问卷表单已打开，直接填写当前表单（跳过点击链接/增加）")
        else:
            # ========== 5. 点击健康档案下的医养结合服务 ==========
            print("5. 点击医养结合服务...")
            link_clicked = page.evaluate(js("""
                for (const panel of document.querySelectorAll('.x-panel')) {
                    if (!panel.textContent.includes('健康档案')) continue;
                    if (!__vis(panel)) continue;
                    for (const link of panel.querySelectorAll('a')) {
                        if (link.textContent.includes('医养结合服务')) {
                            link.click();
                            return true;
                        }
                    }
                }
                return false;
            """))
            if not link_clicked:
                fail("未找到可见的'医养结合服务'链接（患者可能无健康档案或无该服务）")
            time.sleep(3)

            # ========== 6. 查找并点击增加 ==========
            print("6. 点击增加...")
            add_clicked = page.evaluate(js("""
                for (const btn of document.querySelectorAll('button')) {
                    if (!(btn.textContent.includes('增加') && btn.textContent.includes('F1'))) continue;
                    if (!__vis(btn)) continue;
                    btn.click();
                    return true;
                }
                return false;
            """))
            if not add_clicked:
                fail("未找到可见的'增加(F1)'按钮，问卷列表可能未打开")
            time.sleep(3)

            # ========== 6.1 & 6.2 自动选择今天日期并确定 ==========
            # "增加"后弹出 ExtJS "增加计划"对话框（请选择计划日期）：
            # 直接通过 Ext 组件 API 把日期设为今天，再点击对话框内的"确定"按钮
            print("6. 自动选择今天日期并确定...")
            today_str = datetime.now().strftime("%Y-%m-%d")

            # 等待"增加计划"对话框出现（最多等 10 秒）
            dlg_ready = False
            for _ in range(20):
                dlg_ready = page.evaluate(js("""
                    for (const w of document.querySelectorAll('.x-window')) {
                        if (!__vis(w)) continue;
                        const titleEl = w.querySelector('.x-window-header-text');
                        if (titleEl && titleEl.textContent.includes('增加计划')) return true;
                    }
                    return false;
                """))
                if dlg_ready:
                    break
                time.sleep(0.5)

            if not dlg_ready:
                print("警告：未等到'增加计划'对话框")
            else:
                dlg_result = page.evaluate("""
                    (todayStr) => {
                        const wins = document.querySelectorAll('.x-window');
                        for (const w of wins) {
                            const st = getComputedStyle(w);
                            const r = w.getBoundingClientRect();
                            if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                            const titleEl = w.querySelector('.x-window-header-text');
                            if (!titleEl || !titleEl.textContent.includes('增加计划')) continue;

                            // 6.1 把计划日期设为今天：优先走 ExtJS DateField 组件 API（保证校验和内部状态同步）
                            const inp = w.querySelector('input.x-form-field');
                            if (!inp) return 'no_date_input';
                            const cmp = (window.Ext && Ext.getCmp) ? Ext.getCmp(inp.id) : null;
                            if (cmp && cmp.setValue) {
                                cmp.setValue(new Date());
                            } else {
                                inp.value = todayStr;
                                for (const t of ['input', 'change', 'blur']) {
                                    inp.dispatchEvent(new Event(t, { bubbles: true }));
                                }
                            }

                            // 6.2 点击对话框内的"确定"按钮
                            for (const b of w.querySelectorAll('button')) {
                                if (b.textContent.trim().startsWith('确定')) {
                                    b.click();
                                    return 'ok: ' + inp.value;
                                }
                            }
                            return 'no_confirm_btn';
                        }
                        return 'dialog_not_found';
                    }
                """, today_str)
                print(f"6.1/6.2 结果：{dlg_result}")
                time.sleep(2)

            # 经过增加流程后表单必须已打开，否则后续填写全部落空
            form_open = page.evaluate(js("return !!__byPrefix('constriction_');"))
            if not form_open:
                fail("问卷表单未打开（增加/选择日期未生效），终止填写")

        # ========== 7. 确认问卷表单可见 ==========
        suffix_id = page.evaluate(js("""
            const inp = __byPrefix('constriction_');
            if (!inp) return null;
            const m = inp.id.match(/constriction_(.+)/);
            return m ? m[1] : null;
        """))
        if not suffix_id:
            fail("未找到可见的问卷表单（血压输入框），终止填写")
        print(f"可见问卷后缀 ID = {suffix_id}（各字段按前缀+可见定位，不依赖此后缀一致）")

        # ========== 8. 填写血压 ==========
        print("7. 填写血压...")
        bp_done = page.evaluate(js(f"""
            const done = [];
            const c = __byPrefix('constriction_');
            if (c && {json.dumps(constriction)}) {{ c.value = {json.dumps(constriction)}; c.style.color = '#000'; done.push('收缩压'); }}
            const d = __byPrefix('diastolic_');
            if (d && {json.dumps(diastolic)}) {{ d.value = {json.dumps(diastolic)}; d.style.color = '#000'; done.push('舒张压'); }}
            return done.join('、');
        """))
        print(f"血压填写：{bp_done or '（病历首页无血压数据，跳过）'}")
        time.sleep(1)

        # ========== 9. 填写血糖 ==========
        print("8. 填写血糖...")

        # 确定血糖类型并填写
        blood_sugar_type = None
        blood_sugar_value = None

        if fasting and fasting.strip():
            blood_sugar_type = 'fasting'
            blood_sugar_value = fasting
        elif after_meal and after_meal.strip():
            blood_sugar_type = 'after_meal'
            blood_sugar_value = after_meal
        elif random_blood and random_blood.strip():
            blood_sugar_type = 'random'
            blood_sugar_value = random_blood

        if blood_sugar_type and blood_sugar_value:
            # 血糖类型 -> (复选框前缀, 数值输入框前缀)
            cb_prefix = {'fasting': 'bloodSugar_1_', 'after_meal': 'bloodSugar_2_', 'random': 'bloodSugar_3_'}[blood_sugar_type]
            mm_prefix = {'fasting': 'fasting_mmol_', 'after_meal': 'aftermeal_mmol_', 'random': 'random_mmol_'}[blood_sugar_type]
            bs_result = page.evaluate(js(f"""
                const cb = __byPrefix({json.dumps(cb_prefix)});
                if (!cb) return 'no_checkbox';
                cb.click();
                const inp = __byPrefix({json.dumps(mm_prefix)});
                if (!inp) return 'no_input';
                inp.value = {json.dumps(blood_sugar_value)};
                inp.style.color = '#000';
                return 'ok';
            """))
            if bs_result == 'ok':
                print(f"血糖已填写（{cb_prefix}* = {blood_sugar_value}）")
            else:
                print(f"警告：血糖填写失败（{bs_result}），未找到可见的 {cb_prefix}* / {mm_prefix}*")
            time.sleep(1)
        else:
            print("未收集到血糖数据")

        time.sleep(1)

        # ========== 下拉框/意见框填写助手 ==========
        def set_select(prefix, label, value='y'):
            ok = page.evaluate(js(f"""
                const sel = __byPrefix({json.dumps(prefix)});
                if (!sel) return false;
                sel.value = {json.dumps(value)};
                sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return sel.value === {json.dumps(value)};
            """))
            if not ok:
                print(f"警告：未找到可见的{label}下拉框（{prefix}*），未设置")
            time.sleep(1)
            return ok

        def set_textarea(prefix, label, text):
            ok = page.evaluate(js(f"""
                const ta = __byPrefix({json.dumps(prefix)});
                if (!ta) return false;
                ta.value = {json.dumps(text, ensure_ascii=False)};
                ta.style.color = '#000';
                return true;
            """))
            if not ok:
                print(f"警告：未找到可见的{label}输入框（{prefix}*），未填写")
            time.sleep(1)
            return ok

        # ========== 10. 护理技能指导选择是 ==========
        print("9. 护理技能指导选择是...")
        set_select('yyjh-hljnzd_', '护理技能指导')

        # 填写护理技能指导意见
        print("9.1 填写护理技能指导意见...")
        set_textarea('txt_HLYJ_', '护理技能指导意见',
            '日常生活能力护理一：\n养成进食后漱口、三餐后刷牙的好习惯，保持口腔清洁。\n\n'
            '日常生活能力护理二：\n坚持做眼保健操，避免长时间看书、看电视，保证眼睛的休息和放松。'
            '外出戴帽子和防紫外线的偏光镜。发现视力有问题及时就医。')

        # ========== 11. 保健咨询选择是 ==========
        print("10. 保健咨询选择是...")
        set_select('yyjh-bjzx_', '保健咨询')

        # 填写保健咨询意见
        print("10.1 填写保健咨询意见...")
        set_textarea('txt_BJYJ_', '保健咨询意见',
            '自我护理：增强生活自理能力，运用护理知识进行自我照料、自我调节、自我参与及自我保护等护理活动。\n\n'
            '广交朋友，努力保持与旧友的联系，积极主动地去建立新的人际网络。在家庭中，也要将自己的想法和心情'
            '直接向子女表述，与家庭成员间建立和谐的人际关系，营造和睦的家庭氛围。')

        # ========== 12. 营养改善指导选择是 ==========
        print("11. 营养改善指导选择是...")
        set_select('yyjh-yygszd_', '营养改善指导')

        # 填写营养改善指导意见
        print("11.1 填写营养改善指导意见...")
        set_textarea('txt_YYYJ_', '营养改善指导意见',
            '食物多样化，种类齐全：谷类为主、粗细搭配；每天摄入奶类、豆制品；多吃蔬菜水果和薯类；'
            '常吃适量的鱼、禽、蛋和瘦肉；增加益生菌的摄入，维持健康的肠道菌群。\n\n'
            '主动少量多次饮水，每次 50~100ml，清晨一杯温开水，睡前 1~2 小时 1 杯，不应在口渴时才饮水，'
            '养成定时饮水好习惯；饮用水首选温热的白开水，也可饮用淡茶水。')

        # ========== 11.2 康复指导（针对特定疾病患者） ==========
        # 疾病代码 -> (疾病名, "引入健康处方"弹窗里的条目名)
        # 条目名必须精确匹配（弹窗里还有"康复指导--冠心病的II期/III期"等相近条目）；
        # 脑卒中有3个分期条目，社康随访患者默认选"恢复中、后期（相对恢复期）"；
        # 骨质疏松/骨性关节病在弹窗里有条目，但健康标志里没有对应复选框，无法自动识别
        REHAB_PRESCRIPTIONS = {
            "0204": ("冠心病", "康复指导--冠心病"),
            "0207": ("脑卒中", "康复指导--脑卒中恢复中、后期（相对恢复期）"),
            "0221": ("脑血管病后遗症", "康复指导--脑血管病"),
            "0205": ("慢性阻塞性肺疾病", "康复指导--慢性阻塞性肺部疾病"),
        }

        print("\n=== 11.2 康复指导判断 ===")
        rehab_found = []  # [(疾病名, 处方条目名)]
        for code, (disease_name, presc_name) in REHAB_PRESCRIPTIONS.items():
            # 与4.1相同的前缀正则匹配：ID后缀每份档案不同，且要避开 _Control_/_qx 干扰控件
            is_checked = page.evaluate(f"""
                () => {{
                    const re = /^(healthMark_check_|diseasetext_check_jb_){code}_[A-Za-z0-9]+$/;
                    return Array.from(document.querySelectorAll('input[type="checkbox"]'))
                        .some(cb => re.test(cb.id) && cb.checked);
                }}
            """)
            if is_checked:
                rehab_found.append((disease_name, presc_name))
                print(f"  [选中] {disease_name} - 需要康复指导（{presc_name}）")

        if rehab_found:
            print(f"康复指导疾病：{'、'.join(d for d, _ in rehab_found)}")
            print("11.2 康复指导选择是，并调入对应健康处方...")

            # 康复指导选择"是"
            kf_ok = page.evaluate(js("""
                const sel = __byPrefix('yyjh-kfzd_');
                if (!sel) return false;
                sel.value = 'y';
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                return sel.value === 'y';
            """))
            if not kf_ok:
                print("警告：未找到可见的康复指导下拉框（yyjh-kfzd_*）")
            time.sleep(1)

            # 点康复指导旁边的"健康处方"图标（jkchufang图标），打开"引入健康处方"弹窗
            page.evaluate(js("""
                const img = __byPrefix('kf_importHER_');
                if (img) img.click();
            """))
            # 等弹窗出来并拿到窗口id（后面关窗要用）
            win_id = ''
            for _ in range(10):
                win_id = page.evaluate("""
                    () => {
                        for (const w of document.querySelectorAll('.x-window')) {
                            if (w.getBoundingClientRect().width === 0) continue;
                            if ((w.textContent || '').includes('引入健康处方')) return w.id;
                        }
                        return '';
                    }
                """)
                if win_id:
                    break
                time.sleep(0.5)

            if win_id:
                for disease_name, presc_name in rehab_found:
                    presc_js = json.dumps(presc_name, ensure_ascii=False)
                    # 选中条目行：ExtJS grid要发完整鼠标事件序列才选中（单独click无效）
                    # 弹窗数据是异步加载的，窗口出现≠条目已渲染，要重试等待
                    clicked = 'NOT_FOUND'
                    for _ in range(10):
                        clicked = page.evaluate(f"""
                            () => {{
                                for (const cell of document.querySelectorAll('.x-grid3-cell-inner')) {{
                                    if ((cell.textContent || '').trim() !== {presc_js}) continue;
                                    if (cell.getBoundingClientRect().width === 0) continue;
                                    const row = cell.closest('.x-grid3-row') || cell;
                                    for (const type of ['mousedown', 'mouseup', 'click']) {{
                                        for (const target of [cell, row]) {{
                                            target.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
                                        }}
                                    }}
                                    return 'CLICKED';
                                }}
                                return 'NOT_FOUND';
                            }}
                        """)
                        if clicked == 'CLICKED':
                            break
                        time.sleep(0.5)
                    if clicked != 'CLICKED':
                        print(f"  警告：弹窗里没找到'{presc_name}'条目，跳过")
                        continue
                    time.sleep(0.5)
                    # 点调入（弹窗调入后不自动关，可连续调入多个）
                    page.evaluate("""
                        () => {
                            for (const w of document.querySelectorAll('.x-window')) {
                                if (w.getBoundingClientRect().width === 0) continue;
                                if (!(w.textContent || '').includes('引入健康处方')) continue;
                                for (const b of w.querySelectorAll('button')) {
                                    if ((b.textContent || '').trim() === '调入') { b.click(); return; }
                                }
                            }
                        }
                    """)
                    time.sleep(1)
                    print(f"  已调入：{presc_name}")
                # 关闭弹窗（点右上角X无效，要走ExtJS API；
                # 必须用 hide 而非 close——close 会销毁组件，同一会话再次打开弹窗会报 JS 错误）
                page.evaluate(f"""
                    () => {{
                        try {{ Ext.getCmp('{win_id}').hide(); }} catch (e) {{}}
                    }}
                """)
                time.sleep(0.5)
            else:
                print("  警告：'引入健康处方'弹窗未打开")

            # 兜底：调入失败导致意见为空时，填固定文本
            has_text = page.evaluate(js("""
                const ta = __byPrefix('txt_KFYJ_');
                return !!(ta && ta.value.trim());
            """))
            if not has_text:
                print("  调入健康处方失败，改填固定康复指导意见")
                set_textarea('txt_KFYJ_', '康复指导意见',
                    '1.遵医嘱服药，不要随意自行停药，如需调整药物，应先咨询医生。\n2.定期复查。\n'
                    '3.控制血脂、血压及血糖。\n4.存在后遗症的患者，应在医生的指导下进行适当康复训练。\n'
                    '5.防止饮水呛咳导致肺炎。')
        else:
            # 没有需要康复指导的疾病，默认"否"
            print("无需康复指导（未选中相关疾病），默认否")
            page.evaluate(js("""
                const sel = __byPrefix('yyjh-kfzd_');
                if (sel) {
                    sel.value = 'n';
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                }
            """))
            time.sleep(0.5)
        print("=" * 40)

        # ========== 13. 设置下次服务日期 ==========
        print("12. 设置下次服务日期...")
        now = datetime.now()
        month = now.month + 6
        year = now.year
        if month > 12:
            month = month - 12
            year = year + 1
        first_day = datetime(year, month, 1)
        next_date = first_day.strftime("%Y-%m-%d")
        print(f"下次服务日期：{next_date}")

        nd_ok = page.evaluate(js(f"""
            // 使用 name 属性定位日期输入框，更稳定；只取可见的
            const dateInput = Array.from(document.querySelectorAll('input[name^="nextDate_"]')).find(__vis);
            if (!dateInput) return false;
            dateInput.focus();
            dateInput.select();
            dateInput.value = {json.dumps(next_date)};
            dateInput.style.color = '#000';
            for (const t of ['input', 'change', 'blur']) {{
                dateInput.dispatchEvent(new Event(t, {{ bubbles: true }}));
            }}
            return true;
        """))
        if not nd_ok:
            print("警告：未找到可见的下次服务日期输入框（nextDate_*），未设置")
        time.sleep(1)

        # ========== 14. 点击确定保存 ==========
        print("13. 点击确定保存...")
        # 该系统的确定按钮是 ExtJS 按钮，DOM click / 模拟鼠标点击都不会触发真正的
        # 保存逻辑，必须找到按钮对应的 Ext 组件并调用其 handler（handler 内部会以
        # scope 调用模块的 doSave）。保存结果通过 MyMessageTip 瞬时提示反馈
        # （成功/校验失败都走它），先拦截该提示用于判断结果。
        page.evaluate("""
            () => {
                window.__yyjhSaveMsg = null;
                if (window.MyMessageTip && !MyMessageTip.__yyjhPatched) {
                    const orig = MyMessageTip.msg;
                    MyMessageTip.msg = function(title, msg, f) {
                        window.__yyjhSaveMsg = (title || '') + ' | ' + (msg || '');
                        return orig.apply(this, arguments);
                    };
                    MyMessageTip.__yyjhPatched = true;
                }
            }
        """)
        save_fired = page.evaluate(js("""
            for (const btn of document.querySelectorAll('button')) {
                if (!(btn.textContent.includes('确定') && btn.textContent.includes('F1'))) continue;
                if (!__vis(btn)) continue;
                const wrap = btn.closest('.x-btn');
                const cmp = (wrap && window.Ext && Ext.getCmp) ? Ext.getCmp(wrap.id) : null;
                if (cmp && typeof cmp.handler === 'function') {
                    cmp.handler.call(cmp.scope || cmp, cmp);
                } else {
                    btn.click();
                }
                return true;
            }
            return false;
        """))
        if not save_fired:
            fail("未找到可见的'确定(F1)'保存按钮，未保存")

        # 等待保存结果提示（校验失败同步弹出，保存成功是异步返回，最多等 8 秒）
        save_msg = None
        for _ in range(16):
            save_msg = page.evaluate("() => window.__yyjhSaveMsg")
            if save_msg:
                break
            time.sleep(0.5)

        if save_msg and '保存成功' in save_msg:
            print(f"保存结果：{save_msg}")
        elif save_msg:
            fail(f"保存未成功，系统提示：{save_msg}")
        else:
            print("警告：未捕获到保存结果提示，请人工确认是否保存成功")

        print("完成!")


if __name__ == "__main__":
    fill_yyjh()
