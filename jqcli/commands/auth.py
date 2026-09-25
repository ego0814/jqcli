from __future__ import annotations

import os
import sys

import click

from jqcli.api.auth import login_with_password
from jqcli.api.client import ApiClient
from jqcli.api.research import _bootstrap_research
from jqcli.cli import AppContext
from jqcli.config import resolve_login_credentials
from jqcli.errors import JqcliError, UsageError
from jqcli.output import write_json


@click.group(name="auth")
def auth_group() -> None:
    """认证管理。"""


@auth_group.command("status")
@click.pass_obj
def status(app: AppContext) -> None:
    authenticated = bool(app.token or app.cookie)
    payload = {
        "authenticated": authenticated,
        "api_base": app.api_base,
        "credential_source": "token" if app.token else "cookie" if app.cookie else None,
        "username": app.config.data.get("username"),
        "research_session": app.config.data.get("research_session"),
    }
    if app.json_output:
        write_json(payload)
    elif authenticated:
        click.echo("已配置认证凭据")
    else:
        click.echo("未登录")


@auth_group.command("logout")
@click.pass_obj
def logout(app: AppContext) -> None:
    app.config.data.pop("token", None)
    app.config.data.pop("cookie", None)
    app.config.data.pop("username", None)
    app.config.data.pop("research_session", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True})
    elif not app.quiet:
        click.echo("已清除本地认证凭据")


@auth_group.command("import-token")
@click.option("--token", required=True, help="要保存的 token")
@click.pass_obj
def import_token(app: AppContext, token: str) -> None:
    app.config.data["token"] = token
    app.config.data.pop("cookie", None)
    app.config.data.pop("research_session", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True, "credential": "token"})
    elif not app.quiet:
        click.echo("token 已保存")


@auth_group.command("import-cookie")
@click.option("--cookie", required=False, help="要保存的 cookie；省略时读取 JQCLI_COOKIE")
@click.pass_obj
def import_cookie(app: AppContext, cookie: str | None) -> None:
    cookie = (cookie or os.environ.get("JQCLI_COOKIE") or "").strip()
    if not cookie:
        raise UsageError("缺少 cookie，请传入 --cookie 或设置 JQCLI_COOKIE")
    app.config.data["cookie"] = cookie
    app.config.data.pop("token", None)
    app.config.data.pop("research_session", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True, "credential": "cookie"})
    elif not app.quiet:
        click.echo("cookie 已保存")


@auth_group.command("login")
@click.option("--username", help="用户名；省略时读取 JQCLI_USERNAME")
@click.option("--password-stdin", is_flag=True, help="从 stdin 读取密码")
@click.pass_obj
def login(app: AppContext, username: str | None, password_stdin: bool) -> None:
    password = sys.stdin.read() if password_stdin else None
    username, password = resolve_login_credentials(username=username, password=password)
    if not username:
        raise UsageError("缺少用户名，请传入 --username 或在 env 文件中设置 JQCLI_USERNAME")
    if not password:
        raise UsageError("缺少密码，请传入 --password-stdin 或在 env 文件中设置 JQCLI_PASSWORD")
    result = login_with_password(app.api_base, username, password, timeout=app.timeout)

    # Probe the research platform once, so a stale or unusable credential is
    # reported at login time instead of surfacing later as not_authenticated.
    cookie = result["cookie"]
    research_ok = False
    research_note = None
    if cookie:
        probe = ApiClient(app.api_base, cookie=cookie, timeout=app.timeout)
        try:
            _bootstrap_research(probe)
            cookie = probe.get_cookie_header("/") or cookie
            research_ok = True
        except JqcliError as exc:
            research_note = str(exc)
        finally:
            probe.close()

    app.config.data["username"] = username
    app.config.data["cookie"] = cookie
    app.config.data["research_session"] = research_ok
    app.config.data.pop("password_login_pending", None)
    app.config.save()
    if app.json_output:
        payload = {"ok": True, "username": username, "credential": "cookie",
                   "research_session": research_ok}
        if research_note:
            payload["research_note"] = research_note
        write_json(payload)
    elif not app.quiet:
        click.echo("登录成功，cookie 已保存")
