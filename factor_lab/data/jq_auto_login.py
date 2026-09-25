#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Playwright 自动登录聚宽，并把「浏览器级」cookie 导入 jqcli。

流程：
    1. 读凭据：local/secrets/jq_login.json，缺失时回退环境变量 JQCLI_USERNAME / JQCLI_PASSWORD
    2. 打开 https://www.joinquant.com/user/login/index，填手机号 + 密码，勾选用户协议
    3. 若页面出现图片验证码，用 ddddocr 识别；识别失败且有界面时降级为人工输入
    4. 提交登录
    5. 打开 https://www.joinquant.com/default/research/redirect 触发研究平台 SSO，
       并以该页面的 `var mob` 是否非空判定登录是否真正生效（这是 research 能不能用的直接判据）
    6. 抓取所有 joinquant.com cookie（只报长度，绝不打印内容）
    7. 调 jqcli auth import-cookie 导入，再用 jqcli research ls 复核

安全约定：
    - 绝不打印密码、cookie 或其长度以外的任何片段
    - 失败时只打印错误信息本身
    - 不写任何含凭据的临时文件

用法：
    python local/scripts/jq_auto_login.py                # 无界面（默认）
    python local/scripts/jq_auto_login.py --no-headless  # 有界面，便于人工处理验证码
    python local/scripts/jq_auto_login.py --dry-run      # 只做填表 + 选择器体检，不提交
    python local/scripts/jq_auto_login.py --timeout 180
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JQCLI = REPO / ".venv" / "Scripts" / "jqcli.exe"
DEFAULT_SECRETS = REPO / "local" / "secrets" / "jq_login.json"

LOGIN_URL = "https://www.joinquant.com/user/login/index"
SSO_URL = "https://www.joinquant.com/default/research/redirect"
RESEARCH_URL = "https://www.joinquant.com/research"

SEL_USERNAME = 'input[name="username"]'
SEL_PASSWORD = 'input[name="pwd"]'
SEL_AGREEMENT = "#agreementBox"
SEL_SUBMIT = "button.login-submit.btnPwdSubmit"
SEL_CAPTCHA_IMG = 'img[src*="captcha" i], img#captcha, img.captcha, .captcha img'
SEL_CAPTCHA_INPUT = 'input[name*="captcha" i], input#captcha, input.captcha'

EXIT_OK, EXIT_BAD_CREDS, EXIT_LOGIN_FAIL, EXIT_IMPORT_FAIL, EXIT_VERIFY_FAIL = 0, 2, 3, 4, 5


def run_jqcli(args):
    """运行 jqcli 并强制按 UTF-8 解码输出。

    本机 locale 是 GBK 而 jqcli 输出 UTF-8；不指定 encoding 时 subprocess 会在
    读取线程抛 UnicodeDecodeError，stdout 变空，research ls 校验会假通过。
    """
    return subprocess.run([str(JQCLI), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str, code: int) -> "NoReturn":  # noqa: F821
    log("ERROR: " + msg)
    raise SystemExit(code)


def load_credentials(secrets_path: Path) -> tuple[str, str]:
    if secrets_path.exists():
        try:
            data = json.loads(secrets_path.read_text(encoding="utf-8"))
            user = str(data.get("username") or "").strip()
            pwd = str(data.get("password") or "")
            if user and pwd:
                return user, pwd
            if user and not pwd:
                fail("凭据文件里 password 为空：请手动填入 " + str(secrets_path), EXIT_BAD_CREDS)
        except SystemExit:
            raise
        except Exception as exc:
            fail("凭据文件无法解析：%s" % type(exc).__name__, EXIT_BAD_CREDS)
    user = os.environ.get("JQCLI_USERNAME", "").strip()
    pwd = os.environ.get("JQCLI_PASSWORD", "")
    if user and pwd:
        log("凭据来源: 环境变量 JQCLI_USERNAME/JQCLI_PASSWORD")
        return user, pwd
    fail("缺少凭据：请创建 %s 并填入 username/password" % secrets_path, EXIT_BAD_CREDS)


def sso_mob_present(page) -> tuple[bool, bool]:
    """Return (mob_non_empty, marker_found) for the research SSO page."""
    page.goto(SSO_URL, wait_until="domcontentloaded", timeout=60000)
    html = page.content()
    m = re.search(r"var\s+mob\s*=\s*['\"]([^'\"]*)['\"]", html)
    if not m:
        return False, False
    return bool(m.group(1).strip()), True


def page_error_text(page) -> str | None:
    """返回登录页上短小的错误提示文本，找不到则 None。

    刻意收窄：只看「叶子元素」且文本命中错误关键词。此前用宽泛的
    [class*=error] 会误命中通用提示弹窗（“提示 / 确定”），导致假失败。
    """
    keywords = ("错误", "失败", "不正确", "已锁定", "异常", "频繁", "不存在")
    try:
        texts = page.eval_on_selector_all(
            "div,span,p,li,label",
            "els => els.filter(e => e.offsetParent && e.children.length === 0)"
            ".map(e => (e.innerText || '').trim())")
    except Exception:
        return None
    for t in texts:
        if not t or len(t) > 60:
            continue
        if any(k in t for k in keywords):
            return t[:120]
    return None


def solve_captcha_if_any(page, headless: bool, timeout_ms: int) -> str:
    if page.locator(SEL_CAPTCHA_IMG).count() == 0:
        return "none"
    log("检测到图片验证码，尝试 ddddocr ...")
    try:
        import ddddocr  # noqa: F401
        ocr = ddddocr.DdddOcr(show_ad=False)
        buf = page.locator(SEL_CAPTCHA_IMG).first.screenshot()
        text = ocr.classification(buf)
        if text and page.locator(SEL_CAPTCHA_INPUT).count() > 0:
            page.locator(SEL_CAPTCHA_INPUT).first.fill(text)
            return "ocr"
    except Exception as exc:
        log("ddddocr 识别失败：%s" % type(exc).__name__)
    if headless:
        return "manual_needed_headless"
    log("请在弹出的浏览器里手动填写验证码，脚本会等待提交完成 ...")
    return "manual"


def main() -> int:
    ap = argparse.ArgumentParser(description="Playwright 自动登录聚宽并导入 jqcli")
    ap.add_argument("--secrets", default=str(DEFAULT_SECRETS))
    ap.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True,
                    help="无界面运行（默认开）；--no-headless 开界面以便人工处理验证码")
    ap.add_argument("--dry-run", action="store_true", help="只填表体检，不提交登录")
    ap.add_argument("--timeout", type=float, default=120.0, help="等待登录/SSO 的总秒数")
    args = ap.parse_args()

    if not JQCLI.exists():
        fail("找不到 jqcli：%s" % JQCLI, EXIT_BAD_CREDS)

    user, pwd = load_credentials(Path(args.secrets))
    log("凭据来源: 已读取（用户名长度 %d，密码长度 %d）" % (len(user), len(pwd)))

    from playwright.sync_api import sync_playwright

    timeout_ms = int(args.timeout * 1000)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        ctx = browser.new_context(locale="zh-CN")
        page = ctx.new_page()
        try:
            log("打开登录页 ...")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_selector(SEL_USERNAME, timeout=30000)
            page.fill(SEL_USERNAME, user)
            page.fill(SEL_PASSWORD, pwd)
            log("已填入用户名与密码（不打印）")

            if page.locator(SEL_AGREEMENT).count() > 0 and not page.locator(SEL_AGREEMENT).first.is_checked():
                page.locator(SEL_AGREEMENT).first.check()
                log("已勾选用户协议")

            cap = solve_captcha_if_any(page, args.headless, timeout_ms)
            log("验证码处理: %s" % cap)
            if cap == "manual_needed_headless":
                fail("验证码无法自动处理，请用 --no-headless 重跑", EXIT_LOGIN_FAIL)
            if cap == "manual":
                page.wait_for_timeout(timeout_ms)

            if args.dry_run:
                log("dry-run：跳过提交与导入")
                return EXIT_OK

            if page.locator(SEL_SUBMIT).count() > 0:
                page.locator(SEL_SUBMIT).first.click()
            else:
                page.keyboard.press("Enter")
            log("已提交登录，等待结果 ...")

            # 先等页面离开登录页或明确报错，再用研究平台 SSO 页面反复确认真实登录态
            moved = False
            page_error = None
            phase1 = timeout_ms
            while phase1 > 0:
                page.wait_for_timeout(1000)
                phase1 -= 1000
                # 先判断是否已经离开登录页：登录成功后不能在这里跳转，
                # 否则会把仍在飞行中的登录请求打断（实测会造成假失败）
                if "/user/login" not in (page.url or ""):
                    moved = True
                    break
                page_error = page_error_text(page)
                if page_error:
                    break

            if not moved:
                fail("登录未生效：" + (page_error or "登录页未跳转，可能凭据错误或需要验证码"),
                     EXIT_LOGIN_FAIL)
            log("已离开登录页：%s" % (page.url or "").split("?")[0])

            # 按规格访问 /research 触发研究平台 SSO。这里只做参考性预检，
            # 真正的判定交给 jqcli research ls —— 它会自己完成完整的 SSO 握手。
            sso_ok, sso_marker = sso_mob_present(page)
            log("研究平台 SSO 预检: 页面含 mob=%s, mob 非空=%s（仅供参考）" % (sso_marker, sso_ok))

            page.goto(RESEARCH_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            cookies = [c for c in ctx.cookies() if "joinquant.com" in (c.get("domain") or "")]
            if not cookies:
                fail("未抓到任何 joinquant.com cookie", EXIT_LOGIN_FAIL)
            cookie_header = "; ".join("%s=%s" % (c["name"], c["value"]) for c in cookies)
            log("抓到 joinquant.com cookie：%d 条，序列化长度 %d" % (len(cookies), len(cookie_header)))

            os.environ["JQCLI_COOKIE"] = cookie_header
            try:
                imp = run_jqcli(["auth", "import-cookie"])
            finally:
                os.environ.pop("JQCLI_COOKIE", None)
            if imp.returncode != 0:
                fail("jqcli auth import-cookie 失败，exit=%d" % imp.returncode, EXIT_IMPORT_FAIL)
            log("已导入 jqcli（cookie 经 JQCLI_COOKIE 传递，未进命令行）")

            chk = run_jqcli(["--non-interactive", "--format", "json", "research", "ls"])
            payload = None
            try:
                payload = json.loads(chk.stdout or "")
            except Exception:
                payload = None
            if (isinstance(payload, dict) and isinstance(payload.get("error"), dict)
                    and payload["error"].get("code") == "not_authenticated"):
                fail("导入后 research ls 仍报 not_authenticated", EXIT_VERIFY_FAIL)
            if chk.returncode != 0:
                fail("research ls 失败，exit=%d" % chk.returncode, EXIT_VERIFY_FAIL)
            n_items = len(payload.get("items", [])) if isinstance(payload, dict) else -1
            log("验证通过：research ls 可用（条目 %d）" % n_items)
            return EXIT_OK
        finally:
            try:
                ctx.close()
            finally:
                browser.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("已中断")
        raise SystemExit(130)
