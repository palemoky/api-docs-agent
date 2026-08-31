"""Feishu / Lark bot notifier.

Posts an interactive card to a group webhook. Supports the optional signed
webhook mode, which most corporate deployments enforce.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import httpx

from ..errors import NotifyError
from ..models import RunResult, RunStatus
from .base import Notifier, register_notifier, render_summary


@register_notifier
class FeishuNotifier(Notifier):
    name = "feishu"

    def send(self, result: RunResult) -> None:
        webhook = self.options.get("webhook_url")
        if not webhook:
            raise NotifyError("feishu notifier requires `webhook_url`")

        payload: dict[str, Any] = {"msg_type": "interactive", "card": self._card(result)}
        secret = self.options.get("secret")
        if secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = _sign(timestamp, secret)

        response = httpx.post(webhook, json=payload, timeout=15.0)
        response.raise_for_status()
        body = response.json()
        if body.get("code") not in (0, None):
            raise NotifyError(f"Feishu rejected the message: {body}")

    def _card(self, result: RunResult) -> dict[str, Any]:
        colour = {
            RunStatus.PROPOSED: "orange",
            RunStatus.FAILED: "red",
        }.get(result.status, "blue")

        elements: list[dict[str, Any]] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": render_summary(result)}}
        ]
        if result.pull_request:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "Review the pull request"},
                            "url": result.pull_request.url,
                            "type": "primary",
                        }
                    ],
                }
            )
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": self.options.get("title", "API docs need review"),
                },
                "template": colour,
            },
            "elements": elements,
        }


def _sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")
