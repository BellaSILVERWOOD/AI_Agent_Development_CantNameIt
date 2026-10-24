"""端到端界面流程测试：日程编辑、汇报发送、提醒与页面刷新。"""
from __future__ import annotations

import asyncore
import email
import smtpd
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from email import policy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QDate, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lumina import theme  # noqa: E402
from lumina.dialogs import ComposeDialog, EventDialog  # noqa: E402
from lumina.main_window import MainWindow  # noqa: E402
from lumina.reminders import ReminderCenter, ReminderToast  # noqa: E402
from lumina import accounts as accounts_module
from lumina.store import Store  # noqa: E402

PORT = 11826
received = []


class Collector(smtpd.SMTPServer):
    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):
        received.append((mailfrom, rcpttos, data))


def check(name, condition, detail=""):
    print(("[OK  ] " if condition else "[FAIL] ") + name + " " + str(detail), flush=True)
    if not condition:
        raise SystemExit(1)


def main():
    data_dir = Path(tempfile.mkdtemp(prefix="lumina_flow_"))
    store = Store(data_dir / "flow.db")
    app = QApplication(sys.argv)
    theme.load_from_store(store)
    theme.apply_theme(app)

    window = MainWindow(store)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    app.processEvents()

    today = QDate.currentDate()
    iso_today = today.toString("yyyy-MM-dd")
    before = len(store.events_on(iso_today))

    # 新建日程
    dialog = EventDialog(store, window, date=today)
    dialog.title_edit.setText("端到端测试日程")
    dialog.desc_edit.setPlainText("由流程测试创建")
    dialog.start_field.set_time(dialog.start_field.time().addSecs(3600))
    dialog.end_field.set_time(dialog.start_field.time().addSecs(3600))
    dialog._save()
    app.processEvents()
    events = store.events_on(iso_today)
    check("日程数量 +1", len(events) == before + 1)
    created = [item for item in events if item["title"] == "端到端测试日程"]
    check("标题写入数据库", len(created) == 1)
    event_id = created[0]["id"]
    check("提醒时间已计算", created[0]["remind_at"] is not None, created[0]["remind_at"])
    window.calendar_page.refresh()
    check("日历页面刷新无异常", True)

    # 编辑日程
    edit = EventDialog(store, window, event=store.get_event(event_id))
    edit.title_edit.setText("端到端测试日程（已修改）")
    edit._save()
    app.processEvents()
    check("编辑生效", store.get_event(event_id)["title"].endswith("（已修改）"))

    # 完成状态
    store.set_done(event_id, True)
    window.refresh_all()
    check("完成状态写入", store.get_event(event_id)["done"] is True)
    check("完成后统计更新", store.counts_between(iso_today, iso_today)["done"] >= 1)

    # 汇报邮件发送
    server = Collector(("127.0.0.1", PORT), None, decode_data=False)
    threading.Thread(target=asyncore.loop, kwargs={'timeout': 0.2}, daemon=True).start()
    account = accounts_module.blank_account("测试账户")
    account.update({
        "host": "127.0.0.1", "port": PORT, "security": "none",
        "user": "", "sender_name": "示例汇报",
        "sender_email": "me@example.com", "label": "me@example.com",
    })
    accounts_module.save(store, [account], account["id"])
    store.set_setting("default_to", "boss@example.com")

    attachment = data_dir / "成果报告.pdf"
    attachment.write_bytes(b"%PDF-1.4 flow-test")
    compose = ComposeDialog(store, window)
    compose.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    compose.recipient.setEditText("boss@example.com")
    compose.add_files([str(attachment)])
    check("附件加入列表", len(compose._attachments) == 1)
    check("已选任务", len(compose._selected_tasks()) >= 1)
    compose.show()
    compose._send()
    deadline = time.time() + 20
    while compose._sending and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    check("发送线程结束", not compose._sending)
    mail_id = compose.mail_id
    check("邮件记录存在", mail_id is not None)
    mail = store.get_mail(mail_id)
    check("邮件状态为已发送", mail["status"] == "sent", mail["status"] + " " + mail["error"])
    check("SMTP 服务器收到邮件", len(received) == 1)
    parsed = email.message_from_bytes(received[0][2], policy=policy.default)
    attachment_names = [part.get_filename() for part in parsed.walk() if part.get_filename()]
    check("邮件含中文附件", attachment_names == ["成果报告.pdf"], str(attachment_names))
    check("邮件含任务正文", "端到端测试日程" in parsed.get_body(preferencelist=("html",)).get_content())

    window.refresh_all()
    window.navigate(1)
    app.processEvents()
    check("发件箱显示邮件", len(window.outbox_page._mails) >= 1)
    check("预览可定位到新邮件", window.outbox_page._selected_id == mail_id)

    # 发送失败路径：错误端口应记录失败原因且对话框保持打开
    account["port"] = 11899
    accounts_module.save(store, [account], account["id"])
    failing = ComposeDialog(store, window)
    failing.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    failing.recipient.setEditText("boss@example.com")
    failing.show()
    failing._send()
    deadline = time.time() + 20
    while failing._sending and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    failed_mail = store.get_mail(failing.mail_id)
    check("失败邮件状态", failed_mail["status"] == "failed", failed_mail["status"])
    check("失败原因可读", "连接" in failed_mail["error"] or "拒绝" in failed_mail["error"],
          failed_mail["error"])
    check("失败后对话框未关闭", failing.isVisible())
    failing.close()

    # 草稿保存路径
    account["port"] = PORT
    accounts_module.save(store, [account], account["id"])
    draft = ComposeDialog(store, window)
    draft.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    draft.recipient.setEditText("team@example.com")
    draft.note_edit.setPlainText("这是一封草稿")
    draft._save_draft()
    app.processEvents()
    draft_mail = store.get_mail(draft.mail_id)
    check("草稿状态保存", draft_mail["status"] == "draft")
    check("草稿正文已生成", "工作汇报" in draft_mail["html"])

    # 提醒逻辑：即将开始的任务应进入提醒队列
    soon = (datetime.now() + timedelta(minutes=2)).strftime("%H:%M")
    end = (datetime.now() + timedelta(minutes=32)).strftime("%H:%M")
    reminder_event = store.add_event(
        title="提醒测试", date=iso_today, start_time=soon, end_time=end,
        remind_minutes=5,
    )
    center = ReminderCenter(store)
    shown = []
    center._show = lambda ev: shown.append(ev)
    center.check()
    check("提醒队列命中最新的日程", any(item["id"] == reminder_event for item in shown))
    store.mark_notified(reminder_event)
    center._on_snoozed(reminder_event)
    snoozed = store.get_event(reminder_event)
    check("稍后提醒后重新入队", not snoozed["notified"] and snoozed["remind_at"])

    toast = ReminderToast(store.get_event(reminder_event))
    toast.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    completed = []
    toast.completed.connect(completed.append)
    toast._complete()
    check("提醒卡片完成信号", completed == [reminder_event])
    store.set_done(reminder_event, True)
    check("标记完成写入数据库", store.get_event(reminder_event)["done"])

    # 设置保存（不改配色时应走 settings_saved 分支）
    saved = []
    window.settings_page.settings_saved.connect(lambda: saved.append(True))
    window.navigate(2)
    window.settings_page.save()
    app.processEvents()
    check("设置保存信号", saved == [True])

    # 关闭与重开
    window.close()
    app.processEvents()
    check("窗口正常关闭", not window.isVisible())
    print("\n所有界面流程测试通过。", flush=True)


if __name__ == "__main__":
    main()
