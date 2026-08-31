"""SMTP notifier.

Defaults its recipient to the commit author, which is usually exactly the person
who can answer "is this documentation change right?".
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from ..errors import NotifyError
from ..models import RunResult
from .base import Notifier, register_notifier, render_summary


@register_notifier
class EmailNotifier(Notifier):
    name = "email"

    def send(self, result: RunResult) -> None:
        host = self.options.get("host")
        sender = self.options.get("from")
        if not host or not sender:
            raise NotifyError("email notifier requires `host` and `from`")

        recipients = list(self.options.get("to", []))
        if self.options.get("notify_commit_author", True) and result.change:
            author = result.change.author_email
            if author and author not in recipients:
                recipients.append(author)
        if not recipients:
            raise NotifyError("email notifier has no recipients")

        message = EmailMessage()
        message["Subject"] = self.options.get(
            "subject", "[api-docs-agent] API documentation needs review"
        )
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(render_summary(result))

        port = int(self.options.get("port", 587))
        use_ssl = bool(self.options.get("ssl", port == 465))
        username = self.options.get("username")
        password = self.options.get("password")

        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(host, port, timeout=20) as smtp:
            if not use_ssl and self.options.get("starttls", True):
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
