#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
老年人医养结合服务随访问卷自动填写脚本

流程：
1. 从病历首页收集患者姓名、血压、血糖数据
2. 点击健康档案下的医养结合服务链接
3. 填写问卷并保存
"""

from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
import time

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
            print(f"错误：未找到包含 {target_url} 的页面")
            return

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
            is_checked = page.evaluate(f"""
                () => {{
                    const cb1 = document.getElementById('healthMark_check_{code}_38A6N');
                    const cb2 = document.getElementById('diseasetext_check_jb_{code}_38A6N');
                    return (cb1 && cb1.checked) || (cb2 && cb2.checked);
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

        # ========== 5. 点击健康档案下的医养结合服务 ==========
        print("5. 点击医养结合服务...")
        page.evaluate("""
            () => {
                // 查找包含"健康档案"的面板
                const panels = document.querySelectorAll('.x-panel');
                for (let panel of panels) {
                    if (panel.textContent.includes('健康档案')) {
                        // 在健康档案面板下找医养结合服务链接
                        const links = panel.querySelectorAll('a');
                        for (let link of links) {
                            if (link.textContent.includes('医养结合服务')) {
                                link.click();
                                return;
                            }
                        }
                    }
                }
            }
        """)
        time.sleep(3)

        # ========== 6. 查找并点击增加 ==========
        print("6. 点击增加...")
        page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (!(btn.textContent.includes('增加') && btn.textContent.includes('F1'))) continue;
                    // 只点可见按钮，跳过隐藏/缓存面板里的同名按钮
                    const st = getComputedStyle(btn);
                    const r = btn.getBoundingClientRect();
                    if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                    btn.click();
                    return;
                }
            }
        """)
        time.sleep(3)

        # ========== 6.1 & 6.2 自动选择今天日期并确定 ==========
        # "增加"后弹出 ExtJS "增加计划"对话框（请选择计划日期）：
        # 直接通过 Ext 组件 API 把日期设为今天，再点击对话框内的"确定"按钮
        print("6. 自动选择今天日期并确定...")
        today_str = datetime.now().strftime("%Y-%m-%d")

        # 等待"增加计划"对话框出现（最多等 10 秒）
        dlg_ready = False
        for _ in range(20):
            dlg_ready = page.evaluate("""
                () => {
                    const wins = document.querySelectorAll('.x-window');
                    for (const w of wins) {
                        const st = getComputedStyle(w);
                        const r = w.getBoundingClientRect();
                        if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                        const titleEl = w.querySelector('.x-window-header-text');
                        if (titleEl && titleEl.textContent.includes('增加计划')) return true;
                    }
                    return false;
                }
            """)
            if dlg_ready:
                break
            time.sleep(0.5)

        if not dlg_ready:
            print("警告：未等到'增加计划'对话框，继续执行后续步骤")
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

        # ========== 7. 获取问卷 ID 后缀 ==========
        suffix_id = page.evaluate("""
            () => {
                // 查找包含 constriction_ 或 diastolic_ 的元素获取后缀
                const constrictionInp = document.querySelector('input[id^="constriction_"]');
                if (constrictionInp) {
                    const match = constrictionInp.id.match(/constriction_(.+)/);
                    if (match) return match[1];
                }
                // 备选：查找其他字段的后缀
                const bloodSugarInp = document.querySelector('input[id^="bloodSugar_"]');
                if (bloodSugarInp) {
                    const match = bloodSugarInp.id.match(/bloodSugar_.+_(.+)/);
                    if (match) return match[1];
                }
                return '8M4P7'; // 默认后缀
            }
        """)
        print(f"问卷后缀 ID = {suffix_id}")

        # ========== 8. 填写血压 ==========
        print("7. 填写血压...")
        page.evaluate(f"""
            () => {{
                const constrictionInp = document.getElementById('constriction_' + '{suffix_id}');
                const diastolicInp = document.getElementById('diastolic_' + '{suffix_id}');
                if (constrictionInp && '{constriction}') {{
                    constrictionInp.value = '{constriction}';
                    constrictionInp.style.color = '#000';
                }}
                if (diastolicInp && '{diastolic}') {{
                    diastolicInp.value = '{diastolic}';
                    diastolicInp.style.color = '#000';
                }}
            }}
        """)
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
            if blood_sugar_type == 'fasting':
                # 先点击空腹血糖复选框做标记
                page.evaluate(f"""
                    () => {{
                        const checkbox = document.getElementById('bloodSugar_1_' + '{suffix_id}');
                        if (checkbox) checkbox.click();
                    }}
                """)
                time.sleep(1)
                # 再点击输入框填入数值
                page.evaluate(f"""
                    () => {{
                        const inputMm = document.getElementById('fasting_mmol_' + '{suffix_id}');
                        if (inputMm) {{
                            inputMm.value = '{blood_sugar_value}';
                            inputMm.style.color = '#000';
                        }}
                    }}
                """)
            elif blood_sugar_type == 'after_meal':
                # 先点击餐后 2h 血糖复选框做标记
                page.evaluate(f"""
                    () => {{
                        const checkbox = document.getElementById('bloodSugar_2_' + '{suffix_id}');
                        if (checkbox) checkbox.click();
                    }}
                """)
                time.sleep(1)
                # 再点击输入框填入数值
                page.evaluate(f"""
                    () => {{
                        const inputMm = document.getElementById('aftermeal_mmol_' + '{suffix_id}');
                        if (inputMm) {{
                            inputMm.value = '{blood_sugar_value}';
                            inputMm.style.color = '#000';
                        }}
                    }}
                """)
            elif blood_sugar_type == 'random':
                # 先点击随机血糖复选框做标记
                page.evaluate(f"""
                    () => {{
                        const checkbox = document.getElementById('bloodSugar_3_' + '{suffix_id}');
                        if (checkbox) checkbox.click();
                    }}
                """)
                time.sleep(1)
                # 再点击输入框填入数值
                page.evaluate(f"""
                    () => {{
                        const inputMm = document.getElementById('random_mmol_' + '{suffix_id}');
                        if (inputMm) {{
                            inputMm.value = '{blood_sugar_value}';
                            inputMm.style.color = '#000';
                        }}
                    }}
                """)
        else:
            print("未收集到血糖数据")

        time.sleep(1)

        # ========== 10. 护理技能指导选择是 ==========
        print("9. 护理技能指导选择是...")
        page.evaluate(f"""
            () => {{
                const select = document.getElementById('yyjh-hljnzd_' + '{suffix_id}');
                if (select) {{
                    for (let opt of select.options) {{
                        if (opt.value === 'y') {{
                            opt.selected = true;
                            break;
                        }}
                    }}
                }}
            }}
        """)
        time.sleep(1)

        # 填写护理技能指导意见
        print("9.1 填写护理技能指导意见...")
        page.evaluate(f"""
            () => {{
                const textarea = document.getElementById('txt_HLYJ_' + '{suffix_id}');
                if (textarea) {{
                    textarea.value = '日常生活能力护理一：\\n养成进食后漱口、三餐后刷牙的好习惯，保持口腔清洁。\\n\\n日常生活能力护理二：\\n坚持做眼保健操，避免长时间看书、看电视，保证眼睛的休息和放松。外出戴帽子和防紫外线的偏光镜。发现视力有问题及时就医。';
                    textarea.style.color = '#000';
                }}
            }}
        """)
        time.sleep(1)

        # ========== 11. 保健咨询选择是 ==========
        print("10. 保健咨询选择是...")
        page.evaluate(f"""
            () => {{
                const select = document.getElementById('yyjh-bjzx_' + '{suffix_id}');
                if (select) {{
                    for (let opt of select.options) {{
                        if (opt.value === 'y') {{
                            opt.selected = true;
                            break;
                        }}
                    }}
                }}
            }}
        """)
        time.sleep(1)

        # 填写保健咨询意见
        print("10.1 填写保健咨询意见...")
        page.evaluate(f"""
            () => {{
                const textarea = document.getElementById('txt_BJYJ_' + '{suffix_id}');
                if (textarea) {{
                    textarea.value = '自我护理：增强生活自理能力，运用护理知识进行自我照料、自我调节、自我参与及自我保护等护理活动。\\n\\n广交朋友，努力保持与旧友的联系，积极主动地去建立新的人际网络。在家庭中，也要将自己的想法和心情直接向子女表述，与家庭成员间建立和谐的人际关系，营造和睦的家庭氛围。';
                    textarea.style.color = '#000';
                }}
            }}
        """)
        time.sleep(1)

        # ========== 12. 营养改善指导选择是 ==========
        print("11. 营养改善指导选择是...")
        page.evaluate(f"""
            () => {{
                const select = document.getElementById('yyjh-yygszd_' + '{suffix_id}');
                if (select) {{
                    for (let opt of select.options) {{
                        if (opt.value === 'y') {{
                            opt.selected = true;
                            break;
                        }}
                    }}
                }}
            }}
        """)
        time.sleep(1)

        # 填写营养改善指导意见
        print("11.1 填写营养改善指导意见...")
        page.evaluate(f"""
            () => {{
                const textarea = document.getElementById('txt_YYYJ_' + '{suffix_id}');
                if (textarea) {{
                    textarea.value = '食物多样化，种类齐全：谷类为主、粗细搭配；每天摄入奶类、豆制品；多吃蔬菜水果和薯类；常吃适量的鱼、禽、蛋和瘦肉；增加益生菌的摄入，维持健康的肠道菌群。\\n\\n主动少量多次饮水，每次 50~100ml，清晨一杯温开水，睡前 1~2 小时 1 杯，不应在口渴时才饮水，养成定时饮水好习惯；饮用水首选温热的白开水，也可饮用淡茶水。';
                    textarea.style.color = '#000';
                }}
            }}
        """)
        time.sleep(1)

        # ========== 11.2 康复指导（针对特定疾病患者） ==========
        # 需要康复指导的疾病（根据用户提供的 HTML，骨质疏松和骨性关节痛可能不在疾病列表中）
        REHAB_DISEASES = {
            "0207": "脑卒中",
            "0204": "冠心病",
            "0221": "脑血管病后遗症",
            "0205": "慢性阻塞性肺疾病"
            # 注：骨质疏松、骨性关节痛在 diseasetext_check_jb 中可能是 0219/0220，但根据 HTML 实际是"失能/失智"
            # 如需添加，请确认实际疾病代码
        }

        need_rehab = False
        rehab_diseases_found = []

        print("\n=== 11.2 康复指导判断 ===")
        for code, disease_name in REHAB_DISEASES.items():
            # 检查两处复选框：healthMark_check 和 diseasetext_check_jb
            is_checked = page.evaluate(f"""
                () => {{
                    const cb1 = document.getElementById('healthMark_check_{code}_38A6N');
                    const cb2 = document.getElementById('diseasetext_check_jb_{code}_38A6N');
                    return (cb1 && cb1.checked) || (cb2 && cb2.checked);
                }}
            """)
            if is_checked:
                need_rehab = True
                rehab_diseases_found.append(disease_name)
                print(f"  [选中] {disease_name} - 需要康复指导")

        # 额外检查骨质疏松和骨性关节痛（需要确认实际代码）
        # 根据 HTML，0219=失能，0220=失智，不是骨质疏松/骨性关节痛
        # 如果页面有其他标识骨质疏松/骨性关节痛的字段，需要另外添加检查逻辑

        if need_rehab:
            print(f"康复指导疾病：{', '.join(rehab_diseases_found)}")
            print("11.2 填写康复指导...")
            page.evaluate(f"""
                () => {{
                    // 康复指导选择"是"
                    const select = document.getElementById('yyjh-kfzd_' + '{suffix_id}');
                    if (select) {{
                        for (let opt of select.options) {{
                            if (opt.value === 'y') {{
                                opt.selected = true;
                                break;
                            }}
                        }}
                    }}
                    // 填写康复指导意见（正确 ID: txt_KFYJ_）
                    const textarea = document.getElementById('txt_KFYJ_' + '{suffix_id}');
                    if (textarea) {{
                        textarea.value = '1.遵医嘱服药，不要随意自行停药，如需调整药物，应先咨询医生。\\n2.定期复查。\\n3.控制血脂、血压及血糖。\\n4.存在后遗症的患者，应在医生的指导下进行适当康复训练。\\n5.防止饮水呛咳导致肺炎。';
                        textarea.style.color = '#000';
                    }}
                }}
            """)
            time.sleep(1)
        else:
            print("无需康复指导（未选中相关疾病）")
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

        page.evaluate(f"""
            () => {{
                // 使用 name 属性定位日期输入框，更稳定
                const dateInput = document.querySelector('input[name^="nextDate_"]');
                if (dateInput) {{
                    dateInput.focus();
                    dateInput.select();
                    dateInput.value = '{next_date}';
                    dateInput.style.color = '#000';
                    const evt1 = new Event('input', {{ bubbles: true }});
                    const evt2 = new Event('change', {{ bubbles: true }});
                    const evt3 = new Event('blur', {{ bubbles: true }});
                    dateInput.dispatchEvent(evt1);
                    dateInput.dispatchEvent(evt2);
                    dateInput.dispatchEvent(evt3);
                }}
            }}
        """)
        time.sleep(1)

        # ========== 14. 点击确定保存 ==========
        print("13. 点击确定保存...")
        page.evaluate("""
            () => {
                // 查找可见的确定按钮（跳过隐藏弹窗/面板里的同名按钮）
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (!(btn.textContent.includes('确定') && btn.textContent.includes('F1'))) continue;
                    const st = getComputedStyle(btn);
                    const r = btn.getBoundingClientRect();
                    if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                    btn.click();
                    return;
                }
                // 备用：查找可见的 save 按钮
                const saveBtn = document.querySelector('button.save');
                if (saveBtn) {
                    const r = saveBtn.getBoundingClientRect();
                    if (r.width > 0) saveBtn.click();
                }
            }
        """)
        time.sleep(3)

        print("完成!")


if __name__ == "__main__":
    fill_yyjh()
