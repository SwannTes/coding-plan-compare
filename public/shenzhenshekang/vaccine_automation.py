#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
疫苗处方自动化脚本

功能：
1. 后台运行
2. 每3分钟轮询处理患者
3. 点击有未开处方标志的患者
4. 点击疫苗处方按钮
5. 在弹窗中点击其他社康
6. 点击打印，在弹出的打印预览页点取消（不真正打印）
7. 关闭弹窗
8. 继续处理下一个患者
"""

import asyncio
import ctypes
import json
from playwright.async_api import async_playwright
import time

TARGET_URL = "172.17.9.179"
LOGIN_URL = "https://172.17.9.179/vaccine-web/#/login"
CONFIG = {
    "interval": 3 * 60,  # 3分钟
    "debug": True
}

def log(msg):
    if CONFIG["debug"]:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

async def keep_alive(page):
    """防掉线：没有新病人时用 CDP 真实输入模拟鼠标轻微移动（isTrusted=true），
    让系统的无操作检测认为用户仍在操作。只移动不点击，避免误触。"""
    try:
        size = await page.evaluate(
            "() => ({x: Math.floor(window.innerWidth / 2), y: Math.floor(window.innerHeight / 2)})"
        )
        await page.mouse.move(size['x'], size['y'])
        await asyncio.sleep(0.2)
        await page.mouse.move(size['x'] + 2, size['y'] + 2)
        await asyncio.sleep(0.2)
        await page.mouse.move(size['x'], size['y'])
        log("本轮无新病人，已模拟鼠标移动防掉线")
    except Exception as e:
        log(f"防掉线活动失败: {e}")

async def find_unprescribed_patient(page):
    """查找有未开处方标志的患者（按就诊顺序：先来先处理）"""
    log("查找有未开处方标志的患者...")

    # 查找所有包含"未开处方"的患者行，反转顺序（最早的在前）
    try:
        elements = await page.query_selector_all('text=未开处方')
        if elements:
            # 反转列表，让最早就诊的患者先被处理
            elements = list(reversed(elements))
            for el in elements:
                if await el.is_visible():
                    # 使用JavaScript点击疫苗处方按钮
                    await page.evaluate("""
                        (ele) => {
                            let curr = ele;
                            while (curr) {
                                if (curr.textContent && curr.textContent.includes('疫苗处方')) {
                                    const btns = curr.querySelectorAll('.case_analysis');
                                    for (let b of btns) {
                                        if (b.innerText.includes('疫苗处方')) {
                                            b.click();
                                            return;
                                        }
                                    }
                                }
                                curr = curr.parentElement;
                            }
                        }
                    """, el)
                    await asyncio.sleep(1)
                    log("点击未开处方患者")
                    return True
    except Exception as e:
        log(f"查找失败: {e}")

    log("未找到未开处方患者")
    return False

async def process_vaccine_prescription(page, browser):
    """处理疫苗处方"""
    log("开始处理疫苗处方...")

    try:
        # 1. 点击有未开处方标志的患者
        log("步骤1: 查找并点击未开处方患者")
        if not await find_unprescribed_patient(page):
            return False
        await asyncio.sleep(1)

        # 2. 点击疫苗处方按钮
        log("步骤2: 点击疫苗处方按钮")

        # 检查弹窗是否已经打开（疫苗健康处方弹窗）
        dialog = await page.query_selector('.el-overlay.el-modal-dialog')
        if dialog:
            log("检测到疫苗处方弹窗已打开，跳过点击疫苗处方按钮")
        else:
            # 方法1: 通过class="case_analysis" + text="疫苗处方"
            found = False
            try:
                elements = await page.query_selector_all('.case_analysis')
                for el in elements:
                    if '疫苗处方' in await el.inner_text():
                        await el.click()
                        log("点击疫苗处方按钮 (class=case_analysis)")
                        found = True
                        break
            except Exception as e:
                log(f"方法1失败: {e}")

            # 方法2: 直接查找包含"疫苗处方"的div
            if not found:
                try:
                    elements = await page.query_selector_all('div')
                    for el in elements:
                        if await el.is_visible() and '疫苗处方' in await el.inner_text():
                            await el.click()
                            log("点击疫苗处方按钮 (div text)")
                            found = True
                            break
                except Exception as e:
                    log(f"方法2失败: {e}")

            # 方法3: 通过文字包含"处方"的按钮
            if not found:
                buttons = await page.query_selector_all("button")
                for btn in buttons:
                    text = await btn.inner_text()
                    if "处方" in text:
                        await btn.click()
                        log(f"点击处方按钮: {text.strip()}")
                        found = True
                        break

            if not found:
                log("未找到疫苗处方按钮")
                return False

        await asyncio.sleep(1.5)

        # 3. 点击其他社康
        log("步骤3: 点击其他社康")
        try:
            labels = await page.query_selector_all('label.el-radio')
            for label in labels:
                if '其他社康' in await label.inner_text():
                    await label.click()
                    log("点击其他社康成功")
                    break
            else:
                log("未找到其他社康选项")
        except Exception as e:
            log(f"点击其他社康失败: {e}")

        await asyncio.sleep(0.5)

        # 4. 点击打印（Chrome未开静默打印，点击后必弹打印预览，
        #    在预览页点"取消"关掉，不真正打印）
        log("步骤4: 点击打印")

        CLICK_PRINT_JS = """
            () => {
                const dialogs = document.querySelectorAll('.el-dialog');
                for (let dialog of dialogs) {
                    if (dialog.offsetParent === null) continue;
                    const title = dialog.querySelector('.el-dialog__title');
                    if (!title || !title.textContent.includes('疫苗健康处方')) continue;
                    const buttons = dialog.querySelectorAll('button');
                    for (let btn of buttons) {
                        const txt = (btn.textContent || '').replace(/\\s/g, '');
                        if (txt === '打印' && !btn.disabled && !btn.classList.contains('is-disabled')) {
                            btn.click();
                            return 'CLICKED';
                        }
                    }
                    return 'NO_PRINT_BUTTON';
                }
                return 'NO_DIALOG';
            }
        """

        def preview_click_js(btn_text):
            """在预览页shadow DOM里点指定文字的按钮（按钮id不稳定，按文字匹配）。
            按钮路径: PRINT-PREVIEW-APP/PRINT-PREVIEW-SIDEBAR/PRINT-PREVIEW-BUTTON-STRIP"""
            return r"""
                (() => {
                    const BTN_TEXT = %s;
                    let clicked = false;
                    function walk(root, depth) {
                        if (depth > 12 || !root || clicked) return;
                        for (const b of root.querySelectorAll('cr-button, button')) {
                            if ((b.textContent || '').trim() === BTN_TEXT) {
                                b.click(); clicked = true; return;
                            }
                        }
                        for (const el of root.querySelectorAll('*')) {
                            if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
                        }
                    }
                    walk(document, 0);
                    return clicked;
                })()
            """ % json.dumps(btn_text, ensure_ascii=False)

        async def find_preview():
            """用CDP Target列表找chrome://print预览页，返回(target_id, page对象或None)。
            注意：connect_over_cdp时browser.contexts的pages列表可能漏掉预览窗口，
            所以以CDP Target.getTargets为准，page对象仅尽力匹配。"""
            try:
                cdp = await browser.new_browser_cdp_session()
                try:
                    targets = await cdp.send('Target.getTargets')
                finally:
                    await cdp.detach()
            except Exception:
                return None, None
            for t in targets['targetInfos']:
                if t['url'].startswith('chrome://print'):
                    pg = None
                    for ctx in browser.contexts:
                        for p in ctx.pages:
                            if 'chrome://print' in p.url:
                                pg = p
                                break
                        if pg:
                            break
                    return t['targetId'], pg
            return None, None

        try:
            # 点击打印并确认预览页真的弹出（没弹出=没触发，重试）
            target_id = None
            for attempt in range(3):
                result = await page.evaluate(CLICK_PRINT_JS)
                if result != 'CLICKED':
                    log(f"打印按钮未点到({result})")
                    break
                log(f"已点击打印(第{attempt + 1}次)，等待打印预览页弹出...")
                for _ in range(20):  # 最多等10秒
                    target_id, _ = await find_preview()
                    if target_id:
                        break
                    await asyncio.sleep(0.5)
                if target_id:
                    break
                log("未检测到打印预览页，打印未触发，1秒后重试...")
                await asyncio.sleep(1)

            if not target_id:
                log("失败：多次点击打印均未弹出预览页，本次未打印")
                return False

            # 在预览页点"取消"（本次不真正打印）。
            # 注意：预览页打开期间主页面JS完全冻结，且connect_over_cdp
            # 拿不到预览页的page对象，所以先CDP直连预览target点"取消"，
            # 不行再激活预览窗口发OS级ESC（ESC等同于点"取消"）
            log("检测到打印预览页，点击预览中的'取消'按钮...")

            async def cdp_click_cancel(tid):
                """attach到预览页target，把点'取消'的JS直接发过去执行。
                sendMessageToTarget是异步的，不等返回，靠预览页是否消失来验证。"""
                cdp = await browser.new_browser_cdp_session()
                try:
                    resp = await cdp.send('Target.attachToTarget',
                                          {'targetId': tid, 'flatten': False})
                    msg = json.dumps({
                        'id': 1,
                        'method': 'Runtime.evaluate',
                        'params': {'expression': preview_click_js('取消'),
                                   'returnByValue': True}
                    })
                    await cdp.send('Target.sendMessageToTarget',
                                   {'sessionId': resp['sessionId'], 'message': msg})
                finally:
                    await cdp.detach()

            def press_esc():
                """OS级ESC键（keybd_event），发到当前焦点窗口"""
                user32 = ctypes.windll.user32
                user32.keybd_event(0x1B, 0, 0, 0)        # ESC按下
                user32.keybd_event(0x1B, 0, 0x0002, 0)   # ESC抬起

            cancelled = False

            # 方法1: CDP直连预览页点"取消"（预览渲染需要时间，多试几次）
            for _ in range(10):  # 最多约5秒
                try:
                    await cdp_click_cancel(target_id)
                except Exception as e:
                    log(f"CDP点击'取消'异常: {e}")
                await asyncio.sleep(0.5)
                tid, _ = await find_preview()
                if not tid:
                    cancelled = True
                    break

            # 方法2: CDP点不动，激活预览窗口后发ESC（预览弹窗此刻应有系统焦点）
            for esc_attempt in range(3):
                if cancelled:
                    break
                log(f"CDP未点到'取消'，改发ESC取消(第{esc_attempt + 1}次)...")
                try:
                    cdp = await browser.new_browser_cdp_session()
                    try:
                        await cdp.send('Target.activateTarget', {'targetId': target_id})
                    finally:
                        await cdp.detach()
                except Exception as e:
                    log(f"激活预览窗口失败: {e}")
                await asyncio.sleep(0.3)
                try:
                    press_esc()
                except Exception as e:
                    log(f"发送ESC失败: {e}")
                for _ in range(10):  # 最多等5秒确认预览页关闭
                    await asyncio.sleep(0.5)
                    tid, _ = await find_preview()
                    if not tid:
                        cancelled = True
                        break

            if cancelled:
                log("已取消打印，预览页已关闭（本次未打印）")
            else:
                # 兜底：CDP强制关闭预览页，解冻主页面
                log("'取消'和ESC都未生效，CDP强制关闭预览页")
                try:
                    cdp = await browser.new_browser_cdp_session()
                    try:
                        await cdp.send('Target.closeTarget', {'targetId': target_id})
                    finally:
                        await cdp.detach()
                except Exception as e:
                    log(f"强制关闭预览页失败: {e}")
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    tid, _ = await find_preview()
                    if not tid:
                        break
                else:
                    log("警告：预览页关不掉，后续操作可能卡死，请手动关闭")
                    return False
        except Exception as e:
            log(f"点击打印失败: {e}")

        # 5. 点击取消关闭弹窗（只在可见的"疫苗健康处方"弹窗内找取消，避免点错同名按钮）
        log("步骤5: 关闭弹窗")
        try:
            cancel_result = await asyncio.wait_for(page.evaluate("""
                () => {
                    const dialogs = document.querySelectorAll('.el-dialog');
                    for (let d of dialogs) {
                        const r = d.getBoundingClientRect();
                        const wrapper = d.closest('.el-dialog__wrapper, .el-overlay');
                        const wrapperVisible = wrapper ?
                            window.getComputedStyle(wrapper).display !== 'none' : true;
                        if (r.width === 0 || !wrapperVisible) continue;
                        const title = d.querySelector('.el-dialog__title');
                        if (!title || !title.textContent.includes('疫苗健康处方')) continue;
                        const btns = d.querySelectorAll('button');
                        for (let b of btns) {
                            if ((b.textContent || '').trim() === '取消') {
                                b.click();
                                return 'CLICKED';
                            }
                        }
                        return 'NO_CANCEL_BUTTON';
                    }
                    return 'NO_DIALOG';
                }
            """), timeout=15)
            if cancel_result == 'CLICKED':
                log("点击取消成功")
            else:
                log(f"取消按钮未点到({cancel_result})，尝试按ESC")
                await page.keyboard.press("Escape")
        except asyncio.TimeoutError:
            log("关闭弹窗超时（页面可能被冻结），跳过本次")
        except Exception as e:
            log(f"关闭弹窗失败: {e}")

        await asyncio.sleep(0.5)

        log("处理完成")
        return True

    except Exception as e:
        log(f"处理出错: {e}")
        return False

async def main_async():
    """异步主函数"""
    async with async_playwright() as p:
        log("尝试连接Chrome...")

        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            print(f"连接Chrome失败: {e}")
            print("请确保Chrome已开启远程调试端口: chrome --remote-debugging-port=9222")
            return

        log("连接成功!")

        # 先尝试查找已打开的页面
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if TARGET_URL in pg.url:
                    page = pg
                    break
            if page:
                break

        # 如果没找到目标页面，尝试在现有context中创建新标签页
        if not page:
            existing_pages = []
            for ctx in browser.contexts:
                existing_pages.extend(ctx.pages)

            if existing_pages:
                page = await existing_pages[0].context.new_page()
                await page.goto(LOGIN_URL, wait_until="networkidle", timeout=0)
                log(f"已打开新标签页: {LOGIN_URL}")
            else:
                log("未找到任何页面，请先在Chrome中打开目标网站")
                return

        # 检查是否在登录页面
        if "login" in page.url.lower():
            log("检测到登录页面，等待登录完成...")
            log("请在浏览器中完成登录，等待1分钟...")
            for i in range(60):
                await asyncio.sleep(1)
                if "login" not in page.url.lower():
                    break
                if i % 30 == 0:
                    log(f"还剩 {60 - i} 秒...")
            log(f"登录成功！当前URL: {page.url}")

        log(f"当前页面标题: {page.title()}")
        log(f"当前URL: {page.url}")
        log("=" * 50)
        log("自动化已启动！按 Ctrl+C 停止")
        log("=" * 50)

        async def run_once():
            """执行一次处理"""
            try:
                result = await process_vaccine_prescription(page, browser)
                if result:
                    log("处理成功！")
                else:
                    log("本轮未处理患者或处理未成功")
                    # 没有新病人时模拟鼠标轻微移动，防止系统判定无操作而退出登录
                    await keep_alive(page)
                return result
            except Exception as e:
                log(f"执行出错: {e}")
                return False

        # 立即执行第一次，然后启动定时循环
        await run_once()

        # 启动定时循环
        interval_min = CONFIG["interval"] // 60
        while True:
            log(f"等待 {interval_min} 分钟后执行下一次...")
            await asyncio.sleep(CONFIG["interval"])
            await run_once()

if __name__ == "__main__":
    print("=" * 50)
    print("疫苗处方自动化")
    print("=" * 50)
    print("请确保:")
    print("1. Chrome已开启远程调试: chrome --remote-debugging-port=9222")
    print("2. 已登录系统")
    print("")
    asyncio.run(main_async())