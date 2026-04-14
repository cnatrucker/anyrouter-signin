#!/usr/bin/env python3
"""
AnyRouter 多账号自动登录签到脚本
使用 Playwright 自动登录（邮箱+密码），然后调用 API 完成签到。

环境变量:
  ACCOUNTS       - JSON 数组，每个元素包含 name/email/password
  PROXY          - 可选，代理地址（如 http://127.0.0.1:7890）
  HEADLESS       - 可选，是否无头模式（默认 true，设为 false 可看到浏览器）
  SCREENSHOTS    - 可选，设为 true 启用截图调试
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import httpx
from playwright.async_api import async_playwright

DOMAIN = "https://anyrouter.top"
LOGIN_URL = f"{DOMAIN}/login"
SIGN_IN_API = f"{DOMAIN}/api/user/sign_in"
USER_INFO_API = f"{DOMAIN}/api/user/self"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_accounts():
    """从环境变量或本地文件加载账号配置"""
    accounts_str = os.getenv("ACCOUNTS")
    if accounts_str:
        print("[CONFIG] 从环境变量 ACCOUNTS 加载配置")
        return json.loads(accounts_str)

    accounts_file = os.path.join(SCRIPT_DIR, "accounts.json")
    if os.path.exists(accounts_file):
        print(f"[CONFIG] 从文件加载配置")
        with open(accounts_file, "r", encoding="utf-8") as f:
            return json.load(f)

    print("[ERROR] 未找到账号配置：请设置 ACCOUNTS 环境变量或创建 accounts.json 文件")
    sys.exit(1)


def get_proxy():
    """获取代理配置"""
    proxy = os.getenv("PROXY", "").strip()
    return proxy if proxy else None


def is_headless():
    return os.getenv("HEADLESS", "true").lower() != "false"


def save_screenshot_enabled():
    return os.getenv("SCREENSHOTS", "false").lower() == "true"


async def save_screenshot(page, filename):
    """保存截图（仅在启用时）"""
    if not save_screenshot_enabled():
        return
    screenshots_dir = os.path.join(SCRIPT_DIR, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    path = os.path.join(screenshots_dir, filename)
    try:
        await page.screenshot(path=path)
        print(f"    截图已保存: {filename}")
    except Exception:
        pass


async def login_account(playwright, account_name, email, password):
    """使用 Playwright 登录单个账号，返回 cookies 和 api_user"""
    print(f"  [{account_name}] 启动浏览器...")

    proxy_url = get_proxy()
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-features=VizDisplayCompositor",
    ]

    launch_kwargs = {
        "headless": is_headless(),
        "args": launch_args,
    }
    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}
        print(f"  [{account_name}] 使用代理: {proxy_url}")

    browser = await playwright.chromium.launch(**launch_kwargs)
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        ignore_https_errors=True,
    )
    page = await context.new_page()

    # 拦截 API 请求以获取 api_user
    api_user = None

    async def capture_api_user(route):
        nonlocal api_user
        request = route.request
        header_val = request.headers.get("new-api-user")
        if header_val and header_val not in ("0", "-1", ""):
            api_user = header_val
        await route.continue_()

    await page.route("**/api/**", capture_api_user)

    try:
        # 访问登录页
        print(f"  [{account_name}] 访问登录页...")
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        await save_screenshot(page, f"{account_name}_01_login_page.png")

        # 定位并填写邮箱
        email_input = (
            page.locator('input[name="email"]')
            .or_(page.locator('input[type="email"]'))
            .or_(page.locator('input[id="email"]'))
            .or_(page.locator('input[placeholder*="邮箱"]'))
            .or_(page.locator('input[placeholder*="email" i]'))
        )
        await email_input.first.wait_for(state="visible", timeout=10000)
        await email_input.first.fill(email)
        print(f"  [{account_name}] 已填写邮箱")

        # 定位并填写密码
        password_input = (
            page.locator('input[name="password"]')
            .or_(page.locator('input[type="password"]'))
            .or_(page.locator('input[id="password"]'))
        )
        await password_input.first.fill(password)
        print(f"  [{account_name}] 已填写密码")

        # 点击登录按钮
        login_button = (
            page.locator('button:has-text("登录")')
            .or_(page.locator('button:has-text("Login")'))
            .or_(page.locator('button:has-text("登 录")'))
            .or_(page.locator('button[type="submit"]'))
            .or_(page.locator('input[type="submit"]'))
        )
        await login_button.first.click()
        print(f"  [{account_name}] 已点击登录按钮")

        # 等待登录成功（URL 变化）
        try:
            await page.wait_for_url(
                lambda url: "/login" not in url, timeout=15000
            )
            print(f"  [{account_name}] 登录成功，页面已跳转到: {page.url}")
        except Exception:
            await save_screenshot(page, f"{account_name}_02_after_login.png")
            await page.wait_for_timeout(3000)
            if "/login" in page.url:
                print(f"  [{account_name}] 登录失败，仍在登录页面")
                await browser.close()
                return None

        await page.wait_for_timeout(2000)

        # 如果还没捕获到 api_user，访问 console 页面触发 API 请求
        if not api_user:
            print(f"  [{account_name}] 访问 console 页面获取 api_user...")
            try:
                await page.goto(f"{DOMAIN}/console", wait_until="networkidle", timeout=15000)
                await page.wait_for_timeout(2000)
            except Exception:
                pass

        # 提取 cookies
        browser_cookies = await context.cookies()
        cookies = {c["name"]: c["value"] for c in browser_cookies}

        session = cookies.get("session")
        if not session:
            print(f"  [{account_name}] 未获取到 session cookie")
            await save_screenshot(page, f"{account_name}_03_no_session.png")
            await browser.close()
            return None

        print(f"  [{account_name}] 已获取 session cookie")
        if api_user:
            print(f"  [{account_name}] 已获取 api_user: {api_user}")

        await browser.close()
        return {"cookies": cookies, "api_user": api_user}

    except Exception as e:
        print(f"  [{account_name}] 登录异常: {e}")
        await save_screenshot(page, f"{account_name}_error.png")
        try:
            await browser.close()
        except Exception:
            pass
        return None


def do_checkin(cookies, api_user, account_name):
    """使用提取的 cookies 和 api_user 完成签到"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": DOMAIN,
        "Origin": DOMAIN,
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    if api_user:
        headers["new-api-user"] = str(api_user)

    proxy_url = get_proxy()
    client_kwargs = {"http2": True, "timeout": 30.0}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    client = httpx.Client(**client_kwargs)
    client.cookies.update(cookies)

    try:
        # 查询签到前余额
        before_quota = None
        before_used = None
        try:
            resp = client.get(USER_INFO_API, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    user_data = data["data"]
                    before_quota = round(user_data.get("quota", 0) / 500000, 2)
                    before_used = round(user_data.get("used_quota", 0) / 500000, 2)
                    print(f"  [{account_name}] 签到前余额: ${before_quota}, 已用: ${before_used}")
        except Exception as e:
            print(f"  [{account_name}] 查询余额失败: {e}")

        # 执行签到
        resp = client.post(SIGN_IN_API, headers=headers, timeout=30)
        checkin_success = False

        if resp.status_code == 200:
            try:
                result = resp.json()
                if result.get("success") or result.get("ret") == 1 or result.get("code") == 0:
                    checkin_success = True
                    print(f"  [{account_name}] 签到成功!")
                else:
                    msg = result.get("msg") or result.get("message") or "未知错误"
                    already_keywords = ["已经签到", "已签到", "重复签到", "already"]
                    if any(k in msg.lower() for k in already_keywords):
                        checkin_success = True
                        print(f"  [{account_name}] 今日已签到过")
                    else:
                        print(f"  [{account_name}] 签到失败: {msg}")
            except json.JSONDecodeError:
                if "success" in resp.text.lower():
                    checkin_success = True
                    print(f"  [{account_name}] 签到成功!")
                else:
                    print(f"  [{account_name}] 签到响应格式异常")
        else:
            print(f"  [{account_name}] 签到请求失败: HTTP {resp.status_code}")

        # 查询签到后余额
        after_quota = None
        after_used = None
        try:
            resp = client.get(USER_INFO_API, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    user_data = data["data"]
                    after_quota = round(user_data.get("quota", 0) / 500000, 2)
                    after_used = round(user_data.get("used_quota", 0) / 500000, 2)
                    print(f"  [{account_name}] 签到后余额: ${after_quota}, 已用: ${after_used}")
        except Exception as e:
            print(f"  [{account_name}] 查询签到后余额失败: {e}")

        # 计算变化
        if before_quota is not None and after_quota is not None:
            reward = (after_quota + after_used) - (before_quota + before_used)
            if reward > 0:
                print(f"  [{account_name}] 签到奖励: +${reward:.2f}")

        return checkin_success

    except Exception as e:
        print(f"  [{account_name}] 签到过程异常: {e}")
        return False
    finally:
        client.close()


async def main():
    print("=" * 60)
    print("AnyRouter 多账号自动登录签到")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    accounts = load_accounts()
    total = len(accounts)
    print(f"[INFO] 共 {total} 个账号")

    proxy = get_proxy()
    if proxy:
        print(f"[INFO] 代理: {proxy}")

    success_count = 0
    results = []

    async with async_playwright() as playwright:
        for i, account in enumerate(accounts):
            name = account.get("name", f"账号{i + 1}")
            email = account["email"]
            password = account["password"]

            print(f"\n--- [{i + 1}/{total}] {name} ---")

            # 登录
            login_result = await login_account(playwright, name, email, password)

            if not login_result:
                print(f"  [{name}] 登录失败，跳过签到")
                results.append((name, False))
                continue

            # 签到
            success = do_checkin(
                login_result["cookies"],
                login_result["api_user"],
                name,
            )
            if success:
                success_count += 1
            results.append((name, success))

            # 账号间间隔，避免频率限制
            if i < total - 1:
                delay = 3
                print(f"  等待 {delay} 秒...")
                await asyncio.sleep(delay)

    # 汇总
    print("\n" + "=" * 60)
    print("签到结果汇总")
    print("=" * 60)
    for name, ok in results:
        status = "SUCCESS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n成功: {success_count}/{total}")
    print("=" * 60)

    sys.exit(0 if success_count > 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
