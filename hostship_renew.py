#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright


SERVER_URL = os.getenv("SERVER_URL", "").strip()
HOSTSHIP_LOGIN = os.getenv("HOSTSHIP_LOGIN", "").strip()
HOSTSHIP_PASSWORD = os.getenv("HOSTSHIP_PASSWORD", "").strip()

# 可选：已登录会话 cookie（Cookie header 字符串或 Chrome 导出的 JSON 数组），
# 存在时优先复用登录态，绕开登录流程；失效时回退账号密码登录。
SESSION_COOKIES = os.getenv("SESSION_COOKIES", "").strip()

# 可选：具备 repo 权限的 GitHub PAT。登录成功后用它把最新会话
# cookie 回写进仓库 secrets 的 SESSION_COOKIES，保持会话持续新鲜。
GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()

IS_PROXY = os.getenv("IS_PROXY", "false").lower() == "true"
PROXY_SERVER = os.getenv(
    "PROXY_SERVER",
    "socks5://127.0.0.1:1080"
).strip()

MANUAL_RUN = os.getenv(
    "MANUAL_RUN",
    "false"
).lower() == "true"

BJ_TZ = ZoneInfo("Asia/Shanghai")


def log(msg):
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
        flush=True
    )


def server_id():
    if not SERVER_URL:
        return "未知"

    return SERVER_URL.rstrip("/").split("/")[-1]


def node_status():
    if IS_PROXY:
        return "✅ 已启用"

    return "⚪ 未启用（直连）"


def get_days(text):
    if not text:
        return None

    match = re.search(
        r"(\d+)\s*Days?",
        text,
        re.I
    )

    if match:
        return int(match.group(1))

    return None


def beijing_now():
    return datetime.now(BJ_TZ)


def estimate_renew_date(status):
    days = get_days(status)

    if days is None:
        return None

    return (
        beijing_now().date()
        + timedelta(days=days)
    )


def tg(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False

    try:
        proxies = None

        if IS_PROXY:
            proxies = {
                "http": PROXY_SERVER,
                "https": PROXY_SERVER,
            }

        response = requests.post(
            (
                "https://api.telegram.org/"
                f"bot{TG_BOT_TOKEN}/sendMessage"
            ),
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
            },
            timeout=20,
            proxies=proxies,
        )

        if response.ok:
            log("✅ Telegram 通知发送成功")
            return True

        log(
            "❌ Telegram 通知失败: "
            + response.text
        )

        return False

    except Exception as exc:
        log(
            f"❌ Telegram 通知异常: {exc}"
        )
        return False


def current_ip():
    try:
        proxies = None

        if IS_PROXY:
            proxies = {
                "http": PROXY_SERVER,
                "https": PROXY_SERVER,
            }

        response = requests.get(
            "https://api.ipify.org",
            timeout=15,
            proxies=proxies,
        )

        if response.ok:
            return response.text.strip()

        return "获取失败"

    except Exception:
        return "获取失败"


def build_check_message(status, ip):
    days = get_days(status)
    renew_date = estimate_renew_date(status)
    now = beijing_now()

    lines = [
        "⏳ Host-Ship 检查完成",
        "",
        f"🖥️ 服务器：#{server_id()}",
        f"🌐 节点状态：{node_status()}",
        f"📍 出口IP：{ip}",
        f"🕗 检查时间：{now.strftime('%Y/%m/%d %H:%M')}",
        "",
        "🔒 当前状态：未到续期时间",
    ]

    if days is not None:
        lines.append(
            f"⏱️ 距离续期：约 {days} 天"
        )

    if renew_date is not None:
        lines.append(
            "📆 预计可续期："
            + renew_date.strftime("%Y/%m/%d")
        )

    if days is None:
        lines.append(
            f"📅 面板状态：{status}"
        )

    lines.append(
        "⏰ 自动检查：每天 08:00（北京时间）"
    )

    return "\n".join(lines)


def build_success_message(before, after, ip):
    before_days = get_days(before)
    after_days = get_days(after)
    now = beijing_now()

    lines = [
        "🎉 Host-Ship 续期成功",
        "",
        f"🖥️ 服务器：#{server_id()}",
        f"🌐 节点状态：{node_status()}",
        f"📍 出口IP：{ip}",
        f"🕗 续期时间：{now.strftime('%Y/%m/%d %H:%M')}",
        "",
    ]

    if before_days is not None:
        lines.append(
            f"📅 续期前：约 {before_days} 天"
        )
    else:
        lines.append(
            f"📅 续期前：{before}"
        )

    if after_days is not None:
        lines.append(
            f"✅ 续期后：约 {after_days} 天"
        )
    else:
        lines.append(
            f"✅ 续期后：{after}"
        )

    lines.append(
        "⏰ 自动检查：每天 08:00（北京时间）"
    )

    return "\n".join(lines)


def build_error_message(title, reason, ip):
    now = beijing_now()

    return (
        f"{title}\n\n"
        f"🖥️ 服务器：#{server_id()}\n"
        f"🌐 节点状态：{node_status()}\n"
        f"📍 出口IP：{ip}\n"
        f"🕗 检查时间：{now.strftime('%Y/%m/%d %H:%M')}\n\n"
        f"⚠️ 原因：{reason}"
    )


def first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(
            selector
        ).first

        try:
            if (
                locator.count()
                and locator.is_visible()
            ):
                return locator

        except Exception:
            pass

    return None


def normalize_cookie(cookie, domain):
    """Coerce a Chrome-export / raw cookie into a Playwright cookie dict."""
    c = dict(cookie)

    if "expirationDate" in c:
        c.setdefault(
            "expires",
            c.pop("expirationDate"),
        )

    if not c.get("domain") and not c.get("url"):
        c["domain"] = domain

    c.setdefault("path", "/")

    same_site = c.get("sameSite")

    if same_site:
        mapping = {
            "no_restriction": "None",
            "none": "None",
            "unspecified": "Lax",
            "lax": "Lax",
            "strict": "Strict",
        }
        c["sameSite"] = mapping.get(
            str(same_site).lower(),
            "Lax",
        )

    if c.get("sameSite") == "None":
        c["secure"] = True

    return c


def parse_session_cookies(raw, domain):
    """Accept a Cookie header string or a Chrome-exported JSON array."""
    if not raw:
        return []

    s = str(raw).strip()

    if not s:
        return []

    if s.startswith("["):
        data = json.loads(s)

        if not isinstance(data, list):
            raise ValueError(
                "SESSION_COOKIES JSON 必须是数组"
            )

        return [
            normalize_cookie(c, domain)
            for c in data
            if isinstance(c, dict)
            and c.get("name")
            and "value" in c
        ]

    cookies = []

    for part in s.split(";"):
        idx = part.find("=")

        if idx <= 0:
            continue

        cookies.append(
            normalize_cookie(
                {
                    "name": part[:idx].strip(),
                    "value": part[idx + 1:].strip(),
                },
                domain,
            )
        )

    return cookies


def is_logged_in(page):
    body = page.locator(
        "body"
    ).inner_text().lower()

    return (
        "/server/" in page.url
        and "password" not in body
    )


def restore_session(page):
    """Inject SESSION_COOKIES and reuse the login state if still valid."""
    if not SESSION_COOKIES:
        return False

    domain = urlparse(
        SERVER_URL
    ).hostname or ""

    try:
        cookies = parse_session_cookies(
            SESSION_COOKIES,
            domain,
        )

    except Exception as exc:
        log(
            "⚠️ SESSION_COOKIES 解析失败: "
            f"{exc}"
        )
        return False

    if not cookies:
        return False

    log(
        f"🍪 尝试注入已登录 cookie"
        f"（{len(cookies)} 个）..."
    )

    try:
        page.context.add_cookies(
            cookies
        )

    except Exception as exc:
        log(f"⚠️ 注入 cookie 失败: {exc}")
        return False

    page.goto(
        SERVER_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(2000)

    if is_logged_in(page):
        log("✅ 已复用登录态")
        return True

    log(
        "⚠️ 注入 cookie 后仍未登录，"
        "回退账号密码登录"
    )
    return False


def export_session_cookies(context):
    """Dump current cookies for the panel domain as JSON (re-consumable by parse_session_cookies)."""
    return json.dumps(
        context.cookies(SERVER_URL),
        ensure_ascii=False,
    )


def update_secret_cookies(context):
    """Write fresh session cookies back to the SESSION_COOKIES secret via gh CLI."""
    if not GH_TOKEN or not GITHUB_REPOSITORY:
        return False

    value = export_session_cookies(context)

    try:
        result = subprocess.run(
            [
                "gh",
                "secret",
                "set",
                "SESSION_COOKIES",
                "--repo",
                GITHUB_REPOSITORY,
            ],
            input=value,
            capture_output=True,
            text=True,
            timeout=30,
        )

    except Exception as exc:
        log(f"⚠️ 回写 SESSION_COOKIES 异常: {exc}")
        return False

    if result.returncode == 0:
        log(
            "✅ 已回写 GitHub secret "
            "SESSION_COOKIES"
        )
        return True

    log(
        "⚠️ 回写 SESSION_COOKIES 失败: "
        f"{result.stderr.strip()}"
    )
    return False


def login_if_needed(page):
    page.goto(
        SERVER_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    time.sleep(2)

    if is_logged_in(page):
        return True

    email = first_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[name="username"]',
            'input[autocomplete="username"]',
        ],
    )

    password = first_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ],
    )

    if not email or not password:
        log(
            "❌ 没找到登录框，"
            f"当前页面: {page.url}"
        )
        return False

    if (
        not HOSTSHIP_LOGIN
        or not HOSTSHIP_PASSWORD
    ):
        log(
            "❌ 缺少 HOSTSHIP_LOGIN "
            "/ HOSTSHIP_PASSWORD"
        )
        return False

    log("🔐 正在登录 Host-Ship...")

    email.fill(HOSTSHIP_LOGIN)
    password.fill(HOSTSHIP_PASSWORD)

    submit = first_visible(
        page,
        [
            'button[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
        ],
    )

    if not submit:
        log("❌ 没找到登录按钮")
        return False

    submit.click()

    page.wait_for_timeout(3000)

    text = page.locator(
        "body"
    ).inner_text().lower()

    challenge_words = [
        "captcha",
        "verify you are human",
        "security check",
        "cloudflare",
    ]

    if any(
        word in text
        for word in challenge_words
    ):
        log(
            "⚠️ 检测到验证码/"
            "安全验证，需要手动处理"
        )
        return False

    page.goto(
        SERVER_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(2000)

    return "/server/" in page.url


def get_renewal_text(page):
    text = page.locator(
        "body"
    ).inner_text()

    patterns = [
        r"Renewal\s+in\s+\d+\s+Days?",
        r"Renew\s+in\s+\d+\s+Days?",
        r"\d+\s+Days?\s+until\s+renewal",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.I,
        )

        if match:
            return match.group(0)

    if re.search(
        r"Renew\s+Limit\s+Reached",
        text,
        re.I,
    ):
        return "Renew Limit Reached"

    return "未识别"


def find_renew_button(page):
    candidates = [
        page.get_by_role(
            "button",
            name=re.compile(
                r"^Renew(?:\s+Now|\s+Server)?$",
                re.I,
            ),
        ),
        page.get_by_role(
            "link",
            name=re.compile(
                r"^Renew(?:\s+Now|\s+Server)?$",
                re.I,
            ),
        ),
        page.locator(
            'button:has-text("Renew")'
        ),
        page.locator(
            'a:has-text("Renew")'
        ),
    ]

    for group in candidates:
        try:
            count = group.count()

            for i in range(count):
                item = group.nth(i)

                if item.is_visible():
                    return item

        except Exception:
            pass

    return None


def confirm_renewal(page):
    """Wait for the renewal dialog and click the real submit button."""
    title = page.get_by_text(
        re.compile(
            r"Confirm\s+server\s+renewal",
            re.I,
        )
    ).first

    try:
        title.wait_for(
            state="visible",
            timeout=8000,
        )
    except Exception:
        log("❌ 点击 Renew 后未出现续期确认弹窗")
        return False

    confirm_buttons = [
        page.get_by_role(
            "dialog"
        ).last.get_by_role(
            "button",
            name=re.compile(
                r"^Renew\s+now$",
                re.I,
            ),
        ),
        page.get_by_role(
            "button",
            name=re.compile(
                r"^Renew\s+now$",
                re.I,
            ),
        ),
        page.locator(
            'button:has-text("Renew now")'
        ),
    ]

    for group in confirm_buttons:
        try:
            count = group.count()

            for i in range(count):
                button = group.nth(i)

                if (
                    button.is_visible()
                    and button.is_enabled()
                ):
                    log(
                        "✅ 确认弹窗已打开，"
                        "点击 Renew now..."
                    )
                    button.click()
                    return True

        except Exception:
            pass

    log("❌ 确认弹窗中未找到可点击的 Renew now")
    return False


def renewal_succeeded(before, after, body_text):
    text = body_text.lower()
    success_words = [
        "renewed successfully",
        "renewal successful",
        "successfully renewed",
        "renew limit reached",
    ]

    if any(word in text for word in success_words):
        return True

    before_days = get_days(before)
    after_days = get_days(after)

    return (
        before_days is not None
        and after_days is not None
        and after_days > before_days
    )


def wait_for_renewal_result(page, before):
    """Poll once, then reload to avoid reading a stale SPA state."""
    after = get_renewal_text(page)

    for attempt in range(5):
        body_text = page.locator(
            "body"
        ).inner_text()
        after = get_renewal_text(page)

        if renewal_succeeded(
            before,
            after,
            body_text,
        ):
            return True, after

        page.wait_for_timeout(2000)

        if attempt == 1:
            page.reload(
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(2000)

    return False, after


def main():
    if not SERVER_URL.startswith(
        "https://panel.host-ship.com/server/"
    ):
        log("❌ SERVER_URL 不正确")

        tg(
            "❌ Host-Ship 配置错误\n"
            "SERVER_URL 不是服务器详情页地址"
        )

        return 1

    log("======================================")
    log(" Host-Ship Free Auto Renew")
    log("======================================")

    log(
        f"🌐 节点状态：{node_status()}"
    )

    ip = current_ip()

    log(
        f"📍 当前出口IP：{ip}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy={
                "server": PROXY_SERVER
            }
            if IS_PROXY
            else None,
            args=[
                "--no-sandbox"
            ],
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            },
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            if not restore_session(page) and not login_if_needed(page):
                page.screenshot(
                    path="hostship_login_fail.png",
                    full_page=True,
                )

                tg(
                    build_error_message(
                        "❌ Host-Ship 登录失败",
                        "登录失败或遇到安全验证",
                        ip,
                    )
                )

                return 1

            log("✅ 登录成功")

            update_secret_cookies(context)

            before = get_renewal_text(page)

            log(
                f"📅 当前续期状态：{before}"
            )

            body = page.locator(
                "body"
            ).inner_text()

            if re.search(
                r"Renew\s+Limit\s+Reached",
                body,
                re.I,
            ):
                log(
                    "⏳ 目前未到续期时间，"
                    "不进行操作"
                )

                if MANUAL_RUN:
                    tg(
                        build_check_message(
                            before,
                            ip,
                        )
                    )

                return 0

            button = find_renew_button(page)

            if not button:
                page.screenshot(
                    path="hostship_no_renew_button.png",
                    full_page=True,
                )

                tg(
                    build_error_message(
                        "⚠️ Host-Ship 需要检查",
                        (
                            "没有找到可用的 "
                            f"Renew 按钮；{before}"
                        ),
                        ip,
                    )
                )

                return 1

            try:
                disabled = button.is_disabled()

            except Exception:
                disabled = False

            if disabled:
                log(
                    "⏳ Renew 按钮当前不可点击"
                )

                if MANUAL_RUN:
                    tg(
                        build_check_message(
                            before,
                            ip,
                        )
                    )

                return 0

            log(
                "🔄 已到续期窗口，"
                "点击 Renew..."
            )

            button.click()

            if not confirm_renewal(page):
                page.screenshot(
                    path="hostship_confirm_fail.png",
                    full_page=True,
                )

                tg(
                    build_error_message(
                        "❌ Host-Ship 续期确认失败",
                        (
                            "点击第一层 Renew 后，"
                            "未能点击确认弹窗中的 Renew now"
                        ),
                        ip,
                    )
                )

                return 1

            success, after = wait_for_renewal_result(
                page,
                before,
            )

            if success:
                log(
                    "✅ 续期成功："
                    f"{before} -> {after}"
                )

                tg(
                    build_success_message(
                        before,
                        after,
                        ip,
                    )
                )

                return 0

            page.screenshot(
                path="hostship_renew_uncertain.png",
                full_page=True,
            )

            log(
                "⚠️ 已点击续期，"
                "但无法确认结果"
            )

            tg(
                build_error_message(
                    "⚠️ Host-Ship 续期结果需要检查",
                    (
                        f"续期前：{before}；"
                        f"续期后：{after}"
                    ),
                    ip,
                )
            )

            return 1

        except Exception as exc:
            log(
                f"❌ 运行异常：{exc}"
            )

            try:
                page.screenshot(
                    path="hostship_error.png",
                    full_page=True,
                )
            except Exception:
                pass

            tg(
                build_error_message(
                    "❌ Host-Ship 自动续期异常",
                    str(exc),
                    ip,
                )
            )

            return 1

        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
