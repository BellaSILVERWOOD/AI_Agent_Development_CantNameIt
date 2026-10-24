"""核心逻辑自测：数据库 CRUD、提醒计算、本地 SMTP 真实投递。"""
from __future__ import annotations

import asyncore
import email
import os
import smtpd
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lumina.mailer import (  # noqa: E402
    MailError, SmtpConfig, build_message, build_report, human_size,
    send_message, test_connection, validate_attachment,
)
from lumina.secure import unprotect  # noqa: E402
from lumina.store import Store  # noqa: E402

PORT = 11825
received = []


class Collector(smtpd.SMTPServer):
    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):
        received.append((mailfrom, rcpttos, data))


def start_smtp():
    server = Collector(("127.0.0.1", PORT), None, decode_data=False)
    thread = threading.Thread(target=asyncore.loop, kwargs={"timeout": 0.2}, daemon=True)
    thread.start()
    time.sleep(0.2)
    return server


def check(name, condition, detail=""):
    status = "OK  " if condition else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not condition:
        raise SystemExit(1)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lumina_test_"))
    store = Store(tmp / "test.db")
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # ---- 密码加密
    store.set_secret("smtp_password", "授权码-abc-123")
    raw = store.get_setting("smtp_password")
    check("DPAPI 密文不以明文存储", "授权码" not in raw, raw[:16])
    check("DPAPI 解密还原", store.get_secret("smtp_password") == "授权码-abc-123")

    # ---- 日程 CRUD
    before = len(store.events_on(today))
    event_id = store.add_event(
        title="联调测试任务", description="验证提醒计算", date=today,
        start_time="15:00", end_time="16:00", remind_minutes=30, color="teal",
    )
    ev = store.get_event(event_id)
    check("新增日程", ev is not None and ev["title"] == "联调测试任务")
    check(
        "提醒时间 = 开始前 30 分钟",
        ev["remind_at"] == f"{today} 14:30:00",
        str(ev["remind_at"]),
    )
    store.update_event(event_id, start_time="17:00", remind_minutes=15)
    ev = store.get_event(event_id)
    check("修改后重新计算提醒", ev["remind_at"] == f"{today} 16:45:00", str(ev["remind_at"]))
    check("按日期查询", any(e["id"] == event_id for e in store.events_on(today)))

    # ---- 提醒触发/稍后提醒
    store.update_event(event_id, remind_minutes=0, date=today, start_time="08:00")
    due = store.pending_reminders(datetime.now())
    check("过期一小时内仍会提醒一次", any(e["id"] == event_id for e in due))
    store.mark_notified(event_id)
    check("已提醒的不再重复", not any(e["id"] == event_id for e in store.pending_reminders()))
    snooze_at = datetime.now() + timedelta(minutes=10)
    store.snooze(event_id, snooze_at)
    due_again = store.pending_reminders(datetime.now() + timedelta(minutes=11))
    check("稍后提醒重新入队", any(e["id"] == event_id for e in due_again))
    store.update_event(event_id, remind_minutes=None)
    check("不提醒时不入队", not any(e["id"] == event_id for e in store.pending_reminders(datetime.now())))

    # ---- 完成状态与统计
    store.set_done(event_id, True)
    check("标记完成", store.get_event(event_id)["done"] is True)
    counts = store.counts_between(today, tomorrow)
    check("区间统计", counts["total"] >= 1 and counts["done"] >= 1, str(counts))
    store.delete_event(event_id)
    check("删除日程", store.get_event(event_id) is None)

    # ---- 邮件草稿
    tasks = [{"id": 1, "title": "整理客户反馈", "date": today,
              "start_time": "16:00", "end_time": "17:30", "description": "输出改进清单"}]
    doc = tmp / "周报成果.pdf"
    doc.write_bytes(b"%PDF-1.4 test attachment content")
    info = validate_attachment(str(doc))
    check("附件校验", info["name"] == "周报成果.pdf" and info["size"] > 0, human_size(info["size"]))
    try:
        validate_attachment(str(tmp / "missing.pdf"))
        check("缺失附件应报错", False)
    except MailError:
        check("缺失附件应报错", True)

    subject, html_body, text_body = build_report(
        tasks, [info], "本周重点：客户反馈闭环。", True, True
    )
    check("主题包含任务数", "1 项任务" in subject and "1 个成果文件" in subject, subject)
    check("正文含任务标题", "整理客户反馈" in html_body)
    check("正文含附件名", "周报成果.pdf" in html_body)
    check("纯文本摘要", "我完成的结果" in text_body and "补充说明" in text_body)

    mail_id = store.add_mail("boss@example.com", subject, html_body, text_body, tasks, [info])
    mail = store.get_mail(mail_id)
    check("邮件草稿保存与解析", mail["tasks"][0]["title"] == "整理客户反馈"
          and mail["attachments"][0]["name"] == "周报成果.pdf")
    check("草稿计数", store.mail_counts()["draft"] == 1, str(store.mail_counts()))

    # ---- 本地 SMTP 真实发送
    start_smtp()
    cfg = SmtpConfig(
        host="127.0.0.1", port=PORT, security="none",
        sender_name="示例汇报", sender_email="me@example.com",
    )
    ok, message = test_connection(cfg)
    check("SMTP 连通性测试", ok, message)

    msg = build_message("boss@example.com", subject, text_body, html_body, [info], cfg)
    send_message(msg, cfg)
    check("服务器收到邮件", len(received) == 1)
    mailfrom, rcpttos, payload = received[0]
    parsed = email.message_from_bytes(payload, policy=policy.default)
    decoded_subject = str(parsed["Subject"])
    check("收件人", rcpttos == ["boss@example.com"], str(rcpttos))
    check("中文主题解码", "工作汇报" in decoded_subject, decoded_subject)
    parts = list(parsed.walk())
    html_parts = [p for p in parts if p.get_content_type() == "text/html"]
    check("HTML 正文", bool(html_parts) and "整理客户反馈" in html_parts[0].get_content())
    attachment_parts = [p for p in parts if p.get_filename()]
    check("附件文件名保留中文", attachment_parts
          and attachment_parts[0].get_filename() == "周报成果.pdf",
          str([p.get_filename() for p in attachment_parts]))
    check("附件内容一致", attachment_parts[0].get_payload(decode=True) == doc.read_bytes())

    store.set_mail_status(mail_id, "sent")
    check("发送状态写回", store.get_mail(mail_id)["status"] == "sent"
          and store.get_mail(mail_id)["sent_at"])
    check("发件箱搜索", len(store.list_mails("客户反馈")) == 1)

    # ---- 设置
    store.update_settings({"smtp_host": "smtp.qq.com", "smtp_port": "465"})
    check("设置读写", store.get_setting("smtp_host") == "smtp.qq.com")
    store.add_contact("李四", "lisi@example.com")
    store.add_contact("李四（改）", "lisi@example.com")
    contacts = store.list_contacts()
    check("联系人去重更新", len(contacts) == 2 and contacts[-1]["name"] == "李四（改）", str(contacts))

    print("\n全部核心测试通过。数据库：", store.path)
    store.close()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
