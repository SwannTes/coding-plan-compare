#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
慢阻肺筛查问卷自动填写脚本

只需要3个数据：
1. 核对患者姓名
2. 记录患者年龄
3. 在病历首页弹窗的体格检查字段找到患者体重指数
"""

from playwright.sync_api import sync_playwright
import time

def fill_copd():
    print("1. 开始启动...")
    with sync_playwright() as p:
        print("2. 尝试连接Chrome...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        print("3. 连接成功!")

        # 找到目标页面
        target_url = "172.17.8.14:8780"
        page = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                if target_url in p.url:
                    page = p
                    break
            if page:
                break

        if not page:
            print(f"错误：未找到包含 {target_url} 的页面")
            return

        print(f"4. 当前页面标题: {page.title()}")

        # ========== 0. 获取患者姓名 ==========
        patient_name = page.evaluate("""
            () => {
                // 限定在当前活跃页头 #emr_header 内查找，避免读到隐藏缓存页头(#header2)里的旧病人
                const header = document.getElementById('emr_header');
                const h2 = (header && header.querySelector('h2.fleft')) || document.querySelector('h2.fleft');
                if (h2) return h2.textContent.trim();
                return '';
            }
        """)
        print(f"患者姓名 = {patient_name}")

        # ========== 1. 获取BMI ==========
        bmi = float(page.eval_on_selector("#BMI", "el => el.value"))
        print(f"BMI = {bmi}")

        # ========== 2. 获取年龄 ==========
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
        age = int(age_text) if age_text else 49
        print(f"年龄 = {age}")

        # ========== 3. 计算总分 ==========
        score = 0
        # Q1 年龄
        if age >= 70: score += 11
        elif age >= 60: score += 8
        elif age >= 50: score += 4
        else: score += 0  # 40-49岁

        # Q2 从不吸烟=0分
        score += 0

        # Q3 BMI
        if bmi < 18.5: score += 7
        elif bmi <= 23.9: score += 4
        elif bmi <= 27.9: score += 1
        else: score += 0  # ≥28

        # Q4 否=0分
        # Q5 没有气促=0分
        # Q6 否=0分
        # Q7 否=0分

        print(f"总分 = {score}")
        is_positive = score >= 16

        # ========== 4. 点击慢阻肺筛查问卷 ==========
        page.evaluate("""
            () => {
                const panels = document.querySelectorAll('.x-panel');
                for (let panel of panels) {
                    if (panel.textContent.includes('慢阻肺筛查问卷')) {
                        const links = panel.querySelectorAll('a');
                        for (let link of links) {
                            if (link.textContent.includes('慢阻肺筛查问卷')) {
                                link.click();
                                return;
                            }
                        }
                    }
                }
            }
        """)
        time.sleep(1)

        # ========== 5. 点击新增 ==========
        # 注意：只点击可见的按钮，跳过隐藏/缓存面板里的同名按钮
        page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (!(btn.textContent.includes('新增') && btn.textContent.includes('F2'))) continue;
                    const st = getComputedStyle(btn);
                    const r = btn.getBoundingClientRect();
                    if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                    btn.click();
                    return;
                }
            }
        """)
        time.sleep(1)

        # ========== 6. 获取HF ID ==========
        hf_id = page.evaluate("""
            () => {
                const inputs = document.querySelectorAll('input');
                for (let inp of inputs) {
                    const id = inp.id || '';
                    if (id.includes('question') && id.includes('HF')) {
                        const match = id.match(/HF(\d+)/);
                        if (match) return match[1];
                    }
                }
                return null;
            }
        """)
        print(f"问卷ID: HF{hf_id}")

        # ========== 7. 填写表单 ==========
        # Q1: 年龄
        if age >= 70: page.click(f"input[id='question1_11_HF{hf_id}']")
        elif age >= 60: page.click(f"input[id='question1_8_HF{hf_id}']")
        elif age >= 50: page.click(f"input[id='question1_4_HF{hf_id}']")
        else: page.click(f"input[id='question1_0_HF{hf_id}']")

        # Q2: 从不吸烟
        page.click(f"input[id='question2_0_HF{hf_id}']")

        # Q3: BMI
        if bmi < 18.5: page.click(f"input[id='question3_7_HF{hf_id}']")
        elif bmi <= 23.9: page.click(f"input[id='question3_4_HF{hf_id}']")
        elif bmi <= 27.9: page.click(f"input[id='question3_1_HF{hf_id}']")
        else: page.click(f"input[id='question3_0_HF{hf_id}']")

        # Q4: 否
        page.click(f"input[id='question4_0_HF{hf_id}']")

        # Q5: 没有气促
        page.click(f"input[id='question5_0_HF{hf_id}']")

        # Q6: 否
        page.click(f"input[id='question6_0_HF{hf_id}']")

        # Q7: 否
        page.click(f"input[id='question7_0_HF{hf_id}']")

        # 肺通气功能检查: 否
        page.click(f"input[id='spirometryResults_2_HF{hf_id}']")

        # 结果: 根据总分判断
        if is_positive:
            page.click(f"input[id='questionResult_1_HF{hf_id}']")
            print("结果: 阳性")
        else:
            page.click(f"input[id='questionResult_0_HF{hf_id}']")
            print("结果: 阴性")

        time.sleep(1)

        # ========== 8. 保存 ==========
        # 注意：页面中可能存在隐藏弹窗里的“确定”按钮，必须只点可见的
        saved = False
        for btn in page.query_selector_all("button"):
            if "确定" not in btn.inner_text():
                continue
            try:
                if not btn.is_visible():
                    continue
                btn.click(timeout=5000)
                saved = True
                print("已保存!")
                break
            except Exception:
                continue
        if not saved:
            # 兜底：JS 点击可见的“确定”按钮
            print("Playwright 未点到，尝试 JS 兜底...")
            page.evaluate("""
                () => {
                    for (const btn of document.querySelectorAll('button')) {
                        if (!btn.textContent.trim().startsWith('确定')) continue;
                        const st = getComputedStyle(btn);
                        const r = btn.getBoundingClientRect();
                        if (st.display === 'none' || st.visibility !== 'visible' || r.width === 0) continue;
                        btn.click();
                        return;
                    }
                }
            """)

        print("完成!")


if __name__ == "__main__":
    fill_copd()