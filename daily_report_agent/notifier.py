"""
可插拔推送: 邮箱(SMTP) / Telegram / 微信(Server酱)。
按 config.notify 里启用的渠道推送; 缺凭据的渠道跳过并打印告警, 不中断流程。
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.header import Header

import requests


def _send_email(subject: str, content: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    to = os.getenv("SMTP_TO", user or "")
    if not (host and user and password and to):
        print("[notify] 邮箱凭据不全, 跳过邮件推送")
        return
    recipients = [x.strip() for x in to.split(",") if x.strip()]
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    # 465 用 SSL, 其余用 STARTTLS
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls()
    try:
        server.login(user, password)
        server.sendmail(user, recipients, msg.as_string())
        print(f"[notify] 邮件已发送至 {to}")
    finally:
        server.quit()


def _send_telegram(subject: str, content: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("[notify] Telegram 凭据不全, 跳过")
        return
    text = f"*{subject}*\n\n{content}"
    # Telegram 单条消息上限 4096 字符, 超长截断
    if len(text) > 4000:
        text = text[:4000] + "\n\n...(报告过长已截断, 完整版见本地文件)"
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=30,
    )
    if resp.ok:
        print("[notify] Telegram 已发送")
    else:
        print(f"[notify] Telegram 发送失败: {resp.status_code} {resp.text[:200]}")


def _send_serverchan(subject: str, content: str) -> None:
    key = os.getenv("SERVERCHAN_SENDKEY")
    if not key:
        print("[notify] Server酱 SENDKEY 未配置, 跳过")
        return
    resp = requests.post(
        f"https://sctapi.ftqq.com/{key}.send",
        data={"title": subject, "desp": content},
        timeout=30,
    )
    if resp.ok:
        print("[notify] 微信(Server酱) 已发送")
    else:
        print(f"[notify] Server酱 发送失败: {resp.status_code} {resp.text[:200]}")


def notify(subject: str, content: str, channels: dict) -> None:
    """channels: 形如 {'email': True, 'telegram': False, 'serverchan': False}。"""
    senders = {
        "email": _send_email,
        "telegram": _send_telegram,
        "serverchan": _send_serverchan,
    }
    for name, enabled in (channels or {}).items():
        if not enabled:
            continue
        fn = senders.get(name)
        if not fn:
            print(f"[notify] 未知渠道: {name}")
            continue
        try:
            fn(subject, content)
        except Exception as e:
            print(f"[notify] {name} 推送异常: {e}")
