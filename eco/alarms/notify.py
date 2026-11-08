"""Sending alarm messages to phones.

Backends share one small interface: ``send(person, user_key, msg)`` returns a
receipt (or `None`), ``poll(receipt)`` says whether an emergency message was
acknowledged on a phone, ``cancel(tag)`` stops the repeats of every message
sent with that tag.

`PushoverBackend` talks to api.pushover.net (outbound HTTPS only). Its
application token comes from gopass (``alarms/pushover-token`` in the
bernina store), else ``$ECO_PUSHOVER_TOKEN``, else a file named in
``system.json`` (``pushover_token_file``). Users' keys are in the alarm
directory's ``people.json`` (name -> key), never in the shift sheet.

`DryRunBackend` records what would have been sent -- used while the system is
disarmed and in tests.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PUSHOVER_URL = "https://api.pushover.net/1"
GOPASS_TOKEN_PATH = "alarms/pushover-token"

# Pushover priorities
LOWEST, QUIET, NORMAL, HIGH, EMERGENCY = -2, -1, 0, 1, 2


@dataclass
class Message:
    title: str
    text: str
    priority: int = NORMAL
    sound: str | None = None
    tag: str | None = None
    retry: int = 60  # emergency: seconds between repeats (>= 30)
    expire: int = 3600  # emergency: stop repeating after (<= 10800)
    url: str | None = None
    url_title: str | None = None


class NotifyError(RuntimeError):
    pass


class DryRunBackend:
    name = "dry-run"

    def __init__(self):
        self.sent = []
        self._acks = {}
        self._n = 0

    def send(self, person, user_key, msg):
        self._n += 1
        receipt = f"dry{self._n}" if msg.priority == EMERGENCY else None
        self.sent.append({"person": person, "key": user_key, "msg": msg, "receipt": receipt, "t": time.time()})
        logger.info("[dry-run] to %s: %s / %s (prio %s)", person, msg.title, msg.text, msg.priority)
        return receipt

    def poll(self, receipt):
        return self._acks.get(receipt, {"acknowledged": False})

    def cancel(self, tag):
        self.sent.append({"cancel": tag, "t": time.time()})

    # test helper: pretend `user_key` acknowledged `receipt` on the phone
    def fake_ack(self, receipt, user_key):
        self._acks[receipt] = {"acknowledged": True, "acknowledged_by": user_key, "acknowledged_at": time.time()}


def pushover_token():
    tok = None
    try:
        from eco.utilities.secrets_gopass import get_gopass_password

        tok = get_gopass_password(GOPASS_TOKEN_PATH)
    except Exception:
        tok = None
    if not tok:
        tok = os.environ.get("ECO_PUSHOVER_TOKEN")
    if not tok:
        try:
            from eco.alarms.store import SYSTEM

            p = SYSTEM.read().get("pushover_token_file")
            if p:
                with open(os.path.expanduser(p)) as f:
                    tok = f.read().strip()
        except Exception:
            tok = None
    return tok or None


class PushoverBackend:
    name = "pushover"

    def __init__(self, token=None, timeout=10.0):
        self.token = token or pushover_token()
        if not self.token:
            raise NotifyError(
                "no Pushover application token: store it with "
                f"`gopass insert bernina/{GOPASS_TOKEN_PATH}` or set ECO_PUSHOVER_TOKEN"
            )
        self.timeout = timeout

    def _post(self, path, data):
        import requests

        r = requests.post(f"{PUSHOVER_URL}/{path}", data=dict(data, token=self.token), timeout=self.timeout)
        try:
            body = r.json()
        except ValueError:
            body = {"status": 0, "errors": [r.text[:200]]}
        if r.status_code != 200 or body.get("status") != 1:
            raise NotifyError(f"pushover {path}: HTTP {r.status_code} {body.get('errors')}")
        return body

    def send(self, person, user_key, msg):
        data = {
            "user": user_key,
            "title": msg.title[:250],
            "message": msg.text[:1024],
            "priority": msg.priority,
        }
        if msg.sound:
            data["sound"] = msg.sound
        if msg.url:
            data["url"] = msg.url
            if msg.url_title:
                data["url_title"] = msg.url_title
        if msg.priority == EMERGENCY:
            data["retry"] = max(30, int(msg.retry))
            data["expire"] = min(10800, int(msg.expire))
            if msg.tag:
                data["tags"] = msg.tag
        body = self._post("messages.json", data)
        return body.get("receipt")

    def poll(self, receipt):
        import requests

        r = requests.get(
            f"{PUSHOVER_URL}/receipts/{receipt}.json", params={"token": self.token}, timeout=self.timeout
        )
        body = r.json()
        return {
            "acknowledged": bool(body.get("acknowledged")),
            "acknowledged_by": body.get("acknowledged_by"),
            "acknowledged_at": body.get("acknowledged_at"),
            "expired": bool(body.get("expired")),
        }

    def cancel(self, tag):
        self._post(f"receipts/cancel_by_tag/{tag}.json", {})

    def validate_user(self, user_key):
        """True if Pushover knows this user key (cheap sanity check when a
        key is registered)."""
        try:
            self._post("users/validate.json", {"user": user_key})
            return True
        except NotifyError:
            return False


def make_backend(dry_run=False):
    if dry_run:
        return DryRunBackend()
    return PushoverBackend()
