import asyncio
import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


async def send(
    host: str,
    port: int,
    user: str | None,
    password: str | None,
    sender: str,
    use_tls: bool,
    to: str,
    subject: str,
    body: str,
) -> bool:
    """Send a plain-text email via SMTP. Blocking smtplib runs in a thread."""

    def _send() -> bool:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(host, port or 587, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if user:
                smtp.login(user, password or "")
            smtp.send_message(msg)
        return True

    try:
        return await asyncio.to_thread(_send)
    except Exception as exc:
        logger.exception("email send failed: %s", exc)
        return False
