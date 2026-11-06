"""SMTP 邮件构建与发送，不依赖 Qt，便于单独测试。"""
from __future__ import annotations

import html
import mimetypes
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import MAX_FILE_MB, MAX_TOTAL_MB

OFFICE_TYPES = {
    ".pdf": ("application", "pdf"),
    ".doc": ("application", "msword"),
    ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xls": ("application", "vnd.ms-excel"),
    ".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".ppt": ("application", "vnd.ms-powerpoint"),
    ".pptx": ("application", "vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".zip": ("application", "zip"),
    ".rar": ("application", "vnd.rar"),
    ".7z": ("application", "x-7z-compressed"),
}


class MailError(Exception):
    """带中文提示的发送错误。"""


@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 465
    security: str = "ssl"  # ssl | starttls | none
    user: str = ""
    password: str = ""
    sender_name: str = ""
    sender_email: str = ""
    account_id: str = ""
    account_label: str = ""
    oauth: bool = False           # 用 OAuth2（XOAUTH2）登录，不需要密码
    token_getter: Any = None      # 取 access_token 的函数（oauth=True 时用）

    @classmethod
    def from_account(cls, store, account: dict) -> "SmtpConfig":
        """按**指定账户**建配置（验证码这类邮件要固定从学邮发）。"""
        from . import accounts as accounts_module
        from . import oauth2

        account = dict(account or {})
        gid = str(account.get("id") or "")
        # 全局 smtp_oauth 只对微软的服务器生效（学邮/企业邮不能用 XOAUTH2）
        host = str(account.get("host") or "").lower()
        oauth_flag = (store.get_setting("smtp_oauth", "0") or "0") == "1"
        microsoft = any(key in host for key in ("office365", "outlook", "hotmail"))
        use_oauth = bool(account.get("oauth")) or (oauth_flag and microsoft)
        return cls(
            host=account.get("host", ""),
            port=int(account.get("port", 465) or 465),
            security=account.get("security", "ssl") or "ssl",
            user=account.get("user", ""),
            password=accounts_module.password(store, gid) if gid else "",
            sender_name=account.get("sender_name", ""),
            sender_email=account.get("sender_email", ""),
            account_id=gid,
            account_label=account.get("label", ""),
            oauth=use_oauth,
            token_getter=(lambda: oauth2.access_token(store)) if use_oauth else None,
        )

    @classmethod
    def from_store(cls, store) -> "SmtpConfig":
        """读取「当前选中的发件邮箱账户」。

        注意：`smtp_oauth` 是**全局**开关，以前会对所有账户都开 XOAUTH2，
        结果学邮（腾讯企业邮）也被当成微软服务器 → 登录失败。
        现在只有微软的服务器才吃这个全局开关。
        """
        from . import accounts as accounts_module

        account = accounts_module.active(store)
        if account is not None:
            return cls.from_account(store, account)

        # 兼容旧数据：没有账户列表时按单账户字段读取
        port_text = store.get_setting("smtp_port", "465")
        try:
            port = int(port_text)
        except (TypeError, ValueError):
            port = 465
        return cls(
            host=store.get_setting("smtp_host", "").strip(),
            port=port,
            security=store.get_setting("smtp_security", "ssl") or "ssl",
            user=store.get_setting("smtp_user", "").strip(),
            password=store.get_secret("smtp_password", ""),
            sender_name=store.get_setting("sender_name", "").strip(),
            sender_email=store.get_setting("sender_email", "").strip(),
        )

    def missing_fields(self) -> List[str]:
        missing = []
        if not self.host:
            missing.append("SMTP 服务器")
        if not self.sender_email:
            missing.append("发件邮箱")
        return missing

    def is_configured(self) -> bool:
        return not self.missing_fields()


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def guess_mime(path: str) -> Tuple[str, str]:
    suffix = Path(path).suffix.lower()
    if suffix in OFFICE_TYPES:
        return OFFICE_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path)
    if guessed and "/" in guessed:
        maintype, subtype = guessed.split("/", 1)
        return maintype, subtype
    return "application", "octet-stream"


def validate_attachment(path: str) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_file():
        raise MailError(f"附件不存在：{file_path.name}")
    size = file_path.stat().st_size
    if size > MAX_FILE_MB * 1024 * 1024:
        raise MailError(f"「{file_path.name}」超过 {MAX_FILE_MB} MB，无法作为附件发送")
    maintype, _subtype = guess_mime(str(file_path))
    return {
        "path": str(file_path),
        "name": file_path.name,
        "size": size,
        "size_text": human_size(size),
        "kind": maintype,
    }


def attachment_total_size(attachments: List[Dict[str, Any]]) -> int:
    return sum(int(item.get("size", 0)) for item in attachments)


def build_message(
    to_addr: str,
    subject: str,
    text: str,
    html_body: str,
    attachments: List[Dict[str, Any]],
    cfg: SmtpConfig,
) -> EmailMessage:
    if not cfg.sender_email:
        raise MailError("尚未配置发件邮箱，请先前往「设置」填写 SMTP 信息")
    if "@" not in to_addr:
        raise MailError("收件邮箱格式不正确")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.sender_name or cfg.sender_email, cfg.sender_email))
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    domain = cfg.sender_email.split("@")[-1] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    total = 0
    for item in attachments:
        path = Path(item.get("path", ""))
        if not path.is_file():
            raise MailError(f"附件已被移动或删除：{path.name}")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL_MB * 1024 * 1024:
            raise MailError(f"附件总大小超过 {MAX_TOTAL_MB} MB，请减少附件后再发送")
        maintype, subtype = guess_mime(str(path))
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def _connect(cfg: SmtpConfig) -> smtplib.SMTP:
    timeout = 25
    if cfg.security == "ssl":
        context = ssl.create_default_context()
        return smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout, context=context)
    client = smtplib.SMTP(cfg.host, cfg.port, timeout=timeout)
    if cfg.security == "starttls":
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    return client


def _server_reply(exc: BaseException) -> str:
    """把服务器原话摘出来（照着它才能知道到底哪一条不合规）。"""
    parts = []
    code = getattr(exc, "smtp_code", None)
    error = getattr(exc, "smtp_error", None)
    if code:
        parts.append(str(code))
    if error:
        try:
            parts.append(bytes(error).decode("utf-8", "replace"))
        except Exception:
            parts.append(str(error))
    if not parts:
        recipients = getattr(exc, "recipients", None)
        if recipients:
            parts.append(str(recipients))
    text = " ".join(part.strip() for part in parts if str(part).strip())
    return text[:160]


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, MailError):
        return str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        code = getattr(exc, "smtp_code", 0) or 0
        reply = _server_reply(exc)
        hint = (
            "登录被服务器拒绝"
            + ("（535）" if int(code) == 535 else "")
            + "。请确认账号与「授权码 / 应用密码」是否正确；"
            "QQ / 163 等邮箱要在邮箱设置里开启 SMTP 服务。"
        )
        if "basic authentication is disabled" in reply.lower() or "5.7.139" in reply:
            hint = (
                "服务器说「基础认证已停用」（535 5.7.139）。"
                "这不是你填错密码：微软从 2024 年起关掉了 outlook.com / hotmail.com 的"
                "「账号密码 + 应用密码」发信，只剩 OAuth2 能登。\n"
                "换一个允许授权码的邮箱来发信最快：比如复旦学邮 "
                "（smtp.exmail.qq.com，465，SSL）、QQ 邮箱、163 邮箱；"
                "收信那边可以继续用 Outlook。"
            )
        elif "5.7.57" in reply or "not authenticated" in reply.lower():
            hint = ("服务器说「未通过身份验证」（530 5.7.57）：这个发件账户还没保存密码 /"
                    " 授权码，或者密码不对。到 设置 → 发件邮箱 填一次并保存。")
        return hint + (f"（服务器原话：{reply}）" if reply else "")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return ("收件邮箱被服务器拒绝，请检查地址是否正确。"
                + (f"（服务器原话：{_server_reply(exc)}）" if _server_reply(exc) else ""))
    if isinstance(exc, smtplib.SMTPSenderRefused):
        code = getattr(exc, "smtp_code", 0) or 0
        if int(code) in (530, 535):
            return ("服务器要求先通过身份验证（530/535）：这个发件账户还没有保存密码 / 授权码，"
                    "或者密码不对。请到 设置 → 发件邮箱（SMTP）填一次「授权码 / 应用密码」。"
                    + (f"（服务器原话：{_server_reply(exc)}）" if _server_reply(exc) else ""))
        return ("发件邮箱被服务器拒绝：服务器的「MAIL FROM」不接受这个地址。"
                "请把「发件邮箱」改成与登录账号完全一致，或者换一个允许代发的邮箱。"
                + (f"（服务器原话：{_server_reply(exc)}）" if _server_reply(exc) else ""))
    if isinstance(exc, smtplib.SMTPConnectError):
        return "服务器拒绝建立连接，请检查服务器地址、端口与加密方式。"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "服务器提前断开了连接，请稍后重试，并确认端口与加密方式匹配。"
    if isinstance(exc, smtplib.SMTPException):
        return f"邮件服务器返回错误：{exc}"
    if isinstance(exc, ssl.SSLError):
        return "SSL 握手失败，请尝试改用 STARTTLS 或更换端口。"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "连接超时，请检查网络或防火墙设置。"
    if isinstance(exc, socket.gaierror):
        return "无法解析服务器地址，请检查 SMTP 服务器域名。"
    if isinstance(exc, ConnectionRefusedError):
        return "服务器拒绝连接，请检查地址与端口。"
    if isinstance(exc, OSError):
        return f"网络错误：{exc}"
    return f"发送失败：{exc}"


def _send_once(msg: EmailMessage, cfg: SmtpConfig) -> None:
    client = None
    try:
        client = _connect(cfg)
        if cfg.oauth and cfg.user:
            _login_xoauth2(client, cfg)
        elif cfg.user and cfg.password:
            client.login(cfg.user, cfg.password)
        client.send_message(msg)
    finally:
        try:
            if client is not None:
                client.quit()
        except Exception:
            pass


def _login_xoauth2(client, cfg: SmtpConfig) -> None:
    """OAuth2 登录：AUTH XOAUTH2 <base64(user=…\\1auth=Bearer …\\1\\1)>。"""
    from . import oauth2

    if cfg.token_getter is None:
        raise MailError("这个发件账户选了 OAuth2，但没有可用的授权（去设置里点「开始微软授权」）")
    try:
        token = str(cfg.token_getter() or "")
    except oauth2.OAuthError as exc:
        raise MailError(str(exc)) from exc
    if not token:
        raise MailError("OAuth2 授权为空，请重新授权一次")
    try:
        client.ehlo_or_helo_if_needed()
    except Exception:
        pass
    code, response = client.docmd("AUTH", "XOAUTH2 " + oauth2.sasl_string(cfg.user, token))
    if int(code) not in (235, 503):
        detail = ""
        try:
            detail = response.decode("utf-8", "replace") if isinstance(response, bytes) else str(response)
        except Exception:
            detail = str(response)
        raise smtplib.SMTPAuthenticationError(
            int(code), detail.encode("utf-8", "replace")[:200])


def _missing_password_error(cfg: SmtpConfig) -> str:
    return (
        "这个发件账户还没有保存「密码 / 授权码」，服务器不会放行发信。\n"
        f"请到 设置 → 发件邮箱（SMTP）→「密码 / 授权码」里填一次并保存"
        f"（账号 {cfg.user or '（未填）'}）。\n"
        "QQ / 163 要填「授权码」；Outlook / 微软账户开了两步验证要填「应用密码」。"
    )


def send_message(msg: EmailMessage, cfg: SmtpConfig) -> None:
    """发信。发件人被服务器拒绝时，自动换成「登录账号」重试一次。"""
    import copy as _copy

    # 没存密码还去连，服务器只会回 530「未认证」，提示很难懂；这里先拦住
    # （选了 OAuth2 的账户不需要密码，跳过这个检查）
    if (cfg.user and not cfg.password and not cfg.oauth
            and not str(cfg.host).lower().startswith(
                ("localhost", "127.", "10.", "192.168."))):
        raise MailError(_missing_password_error(cfg))

    try:
        _send_once(msg, cfg)
        return
    except smtplib.SMTPSenderRefused as exc:
        login_user = (cfg.user or "").strip()
        current = (cfg.sender_email or "").strip()
        if not login_user or login_user.lower() == current.lower():
            raise MailError(_friendly_error(exc)) from exc
        try:
            retry = _copy.deepcopy(msg)
            retry.replace_header("From", formataddr((cfg.sender_name or login_user,
                                                     login_user)))
            _send_once(retry, cfg)
            return
        except BaseException as second:  # noqa: BLE001
            raise MailError(_friendly_error(second)) from second
    except BaseException as exc:  # 转成中文提示
        raise MailError(_friendly_error(exc)) from exc


def test_connection(cfg: SmtpConfig) -> Tuple[bool, str]:
    if not cfg.host:
        return False, "请先填写 SMTP 服务器地址"
    if not cfg.sender_email:
        return False, "请先填写发件邮箱"
    if cfg.oauth:
        client = None
        try:
            client = _connect(cfg)
            _login_xoauth2(client, cfg)
            return True, f"OAuth2 登录成功，服务器 {cfg.host}:{cfg.port} 可用"
        except MailError as exc:
            return False, str(exc)
        except BaseException as exc:  # noqa: BLE001
            return False, _friendly_error(exc)
        finally:
            try:
                if client is not None:
                    client.quit()
            except Exception:
                pass
    if cfg.user and not cfg.password:
        return False, ("还没有保存「密码 / 授权码」：现在只能连上服务器、不能登录发信。"
                       "请填一次并保存（QQ/163 用授权码，Outlook 用应用密码）。"
                       "Outlook 现在只能用 OAuth2，见下面「用 OAuth2 登录」。")
    client = None
    try:
        client = _connect(cfg)
        if cfg.user and cfg.password:
            client.login(cfg.user, cfg.password)
        return True, f"连接成功，服务器 {cfg.host}:{cfg.port} 可用"
    except BaseException as exc:
        return False, _friendly_error(exc)
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------- 汇报正文
def _today_label() -> str:
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return f"{now.year} 年 {now.month} 月 {now.day} 日 {weekdays[now.weekday()]}"


def report_subject(task_count: int, attach_count: int) -> str:
    """根据任务数与附件数生成邮件主题。"""
    date_label = _today_label()
    if task_count and attach_count:
        return f"【工作汇报】{date_label} 完成 {task_count} 项任务，附 {attach_count} 个成果文件"
    if task_count:
        return f"【工作汇报】{date_label} 完成 {task_count} 项任务"
    if attach_count:
        return f"【工作汇报】{date_label} 工作成果（{attach_count} 个文件）"
    return f"【工作汇报】{date_label}"


def build_report(
    tasks: List[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
    note: str,
    include_tasks: bool,
    include_results: bool,
) -> Tuple[str, str, str]:
    """生成汇报邮件正文，返回 (subject, html, text)。"""
    date_label = _today_label()
    task_count = len(tasks) if include_tasks else 0
    attach_count = len(attachments) if include_results else 0
    subject = report_subject(task_count, attach_count)

    accent = "#5B4BE0"
    muted = "#7c8398"
    ink = "#22283a"

    parts: List[str] = []
    parts.append(
        f"""
        <div style="background:#ffffff;padding:26px 14px;font-family:'Microsoft YaHei UI','Segoe UI',Arial,sans-serif;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="max-width:660px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e6e8f0;">
            <tr>
              <td style="padding:26px 30px 8px 30px;">
                <div style="font-size:12px;letter-spacing:1px;color:{accent};font-weight:bold;">DANLUME · 工作汇报</div>
                <div style="font-size:21px;font-weight:bold;color:{ink};margin-top:8px;">{html.escape(date_label)}</div>
                <div style="font-size:12px;color:{muted};margin-top:4px;">由 DanLume 日程助手整理发送</div>
                <hr style="border:none;border-top:1px solid #eef0f6;margin:18px 0 0 0;" />
              </td>
            </tr>
            <tr>
              <td style="padding:16px 30px 8px 30px;font-size:14px;color:{ink};">
                <p style="margin:0 0 10px 0;">您好，</p>
                <p style="margin:0 0 16px 0;color:#4a5166;">以下是我近期的工作进展与成果，请查收。</p>
        """
    )

    if include_tasks and tasks:
        rows = []
        for index, task in enumerate(tasks, start=1):
            title = html.escape(str(task.get("title", "")))
            date_text = html.escape(str(task.get("date", "")))
            time_text = html.escape(
                f"{task.get('start_time', '')} – {task.get('end_time', '')}".strip(" –")
            )
            desc = html.escape(str(task.get("description", "")).strip()).replace(
                "\n", "<br/>"
            )
            desc_html = (
                f'<div style="font-size:12px;color:{muted};margin-top:4px;line-height:1.6;">{desc}</div>'
                if desc else ""
            )
            rows.append(
                f"""
                <tr>
                  <td style="width:26px;vertical-align:top;padding:9px 0 0 0;color:{accent};font-weight:bold;">{index}.</td>
                  <td style="padding:9px 0 9px 0;border-bottom:1px solid #f1f3f8;">
                    <div style="font-size:14px;font-weight:bold;color:{ink};">{title}</div>
                    <div style="font-size:12px;color:{muted};margin-top:3px;">{date_text} · {time_text}</div>
                    {desc_html}
                  </td>
                </tr>
                """
            )
        parts.append(
            f"""
            <h3 style="font-size:14px;color:{accent};margin:6px 0 4px 0;">✅ 我完成了什么任务</h3>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
              {''.join(rows)}
            </table>
            """
        )

    if include_results and attachments:
        rows = []
        for index, item in enumerate(attachments, start=1):
            name = html.escape(str(item.get("name", "")))
            size_text = html.escape(str(item.get("size_text", "")))
            rows.append(
                f"""
                <tr>
                  <td style="width:26px;padding:7px 0;color:{accent};font-weight:bold;">{index}.</td>
                  <td style="padding:7px 0;font-size:13px;color:{ink};">📎 {name}
                    <span style="color:{muted};font-size:12px;">（{size_text}）</span></td>
                </tr>
                """
            )
        parts.append(
            f"""
            <h3 style="font-size:14px;color:{accent};margin:18px 0 4px 0;">📎 我完成的结果</h3>
            <p style="font-size:12px;color:{muted};margin:0 0 6px 0;">以下文件已作为附件随邮件发送，请查收。</p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
            """
        )

    if note.strip():
        safe_note = html.escape(note.strip()).replace("\n", "<br/>")
        parts.append(
            f"""
            <h3 style="font-size:14px;color:{accent};margin:18px 0 6px 0;">📝 补充说明</h3>
            <div style="font-size:13px;color:{ink};line-height:1.7;background:#f7f8fc;border-left:3px solid {accent};
                        padding:10px 14px;">{safe_note}</div>
            """
        )

    parts.append(
        f"""
              </td>
            </tr>
            <tr>
              <td style="padding:10px 30px 26px 30px;">
                <hr style="border:none;border-top:1px solid #eef0f6;margin:8px 0 14px 0;" />
                <div style="font-size:12px;color:{muted};line-height:1.7;">
                  此邮件由 DanLume 桌面助手生成。<br/>
                  如需调整内容，可在 DanLume 的「发件箱」中重新编辑并发送。
                </div>
              </td>
            </tr>
          </table>
        </div>
        """
    )
    html_body = "".join(parts)

    text_lines = ["您好，", "", "以下是我近期的工作进展与成果，请查收。", ""]
    if include_tasks and tasks:
        text_lines.append("我完成了什么任务")
        for index, task in enumerate(tasks, start=1):
            text_lines.append(
                f"{index}. {task.get('title', '')}（{task.get('date', '')} "
                f"{task.get('start_time', '')}-{task.get('end_time', '')}）"
            )
            desc = str(task.get("description", "")).strip()
            if desc:
                text_lines.append(f"   说明：{desc}")
        text_lines.append("")
    if include_results and attachments:
        text_lines.append("我完成的结果（见附件）")
        for index, item in enumerate(attachments, start=1):
            text_lines.append(f"{index}. {item.get('name', '')}（{item.get('size_text', '')}）")
        text_lines.append("")
    if note.strip():
        text_lines += ["补充说明：", note.strip(), ""]
    text_lines += ["—— 由 DanLume 桌面助手生成", ""]
    text_body = "\n".join(text_lines)
    return subject, html_body, text_body
