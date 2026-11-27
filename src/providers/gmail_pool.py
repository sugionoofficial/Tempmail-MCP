"""
Gmail pool provider — real, independent Gmail accounts used as a pool.

Unlike GmailProvider (which generates "+tag" aliases on a single account),
this provider hands out the *actual* Gmail addresses from a configured pool.
Each returned address is a real gmail.com inbox with its own IMAP
credentials, so websites that reject plus-addressing (user+tag@gmail.com)
will accept them.

Configuration (env):
  GMAIL_ACCOUNTS     JSON array: [{"email": "...", "app_password": "..."}, ...]
  GMAIL_EMAILS       Alternative: comma-separated list of addresses
  GMAIL_APP_PASSWORDS    Comma-separated app passwords (aligned by index)

create_email() picks accounts round-robin. get_messages()/get_message()/
delete_email() connect to the specific pooled account's inbox and filter by
the To: header, exactly like the alias provider.

Note: this provider cannot register new Gmail accounts — the pool must be
supplied beforehand (register accounts via Google's normal signup flow,
then create App Passwords at https://myaccount.google.com/apppasswords).
"""
import asyncio
import imaplib
import json
import logging
import os
from typing import Optional

from .base import EmailAccount, EmailProvider, Message
from .gmail import IMAP_HOST, IMAP_PORT, _decode, _parse_imap_msg

log = logging.getLogger(__name__)

IMAP_TIMEOUT = 30


def _load_pool() -> list[tuple[str, str]]:
    """Return [(email, app_password), ...] from env config."""
    raw = os.getenv("GMAIL_ACCOUNTS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("GMAIL_ACCOUNTS must be valid JSON") from exc
        if not isinstance(data, list):
            raise ValueError("GMAIL_ACCOUNTS must be a JSON array")
        pairs: list[tuple[str, str]] = []
        for item in data:
            try:
                email = str(item["email"]).strip()
                password = str(item["app_password"]).strip()
            except (KeyError, TypeError) as exc:
                raise ValueError(
                    "GMAIL_ACCOUNTS entries must have 'email' and 'app_password'"
                ) from exc
            if email and password:
                pairs.append((email, password))
        if not pairs:
            raise ValueError("GMAIL_ACCOUNTS is empty")
        return pairs

    emails = [e.strip() for e in os.getenv("GMAIL_EMAILS", "").split(",") if e.strip()]
    passwords = [
        p.strip() for p in os.getenv("GMAIL_APP_PASSWORDS", "").split(",") if p.strip()
    ]
    if emails:
        if len(emails) != len(passwords):
            raise ValueError(
                "GMAIL_EMAILS and GMAIL_APP_PASSWORDS must have the same "
                "number of entries"
            )
        return list(zip(emails, passwords))

    raise ValueError(
        "No Gmail accounts configured: set GMAIL_ACCOUNTS (or GMAIL_EMAILS "
        "+ GMAIL_APP_PASSWORDS)"
    )


class GmailPoolProvider(EmailProvider):
    """
    Round-robin pool of real Gmail accounts.

    create_email() returns a real gmail.com address from the pool — no
    plus-tag, so any website accepting gmail.com will accept it.
    """

    name = "gmail"

    def __init__(self) -> None:
        self._accounts = _load_pool()
        self._by_email = {email: password for email, password in self._accounts}
        self._index = 0

    # ------------------------------------------------------------------ helpers

    def _connect(self, address: str, password: str) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT)
        mail.login(address, password)
        return mail

    def _next_account(self) -> tuple[str, str]:
        account = self._accounts[self._index % len(self._accounts)]
        self._index += 1
        return account

    def _search_uids(self, mail: imaplib.IMAP4_SSL, to_addr: str) -> list[bytes]:
        mail.select("INBOX")
        _, data = mail.search(None, f'(TO "{to_addr}")')
        return data[0].split() if data[0] else []

    # ------------------------------------------------------------------ interface

    async def create_email(
        self,
        min_name_length: int = 10,
        max_name_length: int = 10,
        domain: Optional[str] = None,
    ) -> EmailAccount:
        address, _ = self._next_account()
        return EmailAccount(email=address, token=address, provider=self.name)

    async def get_messages(self, account: EmailAccount) -> list[Message]:
        password = self._by_email[account.email]

        def _sync() -> list[Message]:
            mail = self._connect(account.email, password)
            try:
                uids = self._search_uids(mail, account.email)
                messages = []
                for uid in uids:
                    _, msg_data = mail.fetch(uid, "(RFC822)")
                    messages.append(
                        _parse_imap_msg(uid.decode(), msg_data[0][1], account.email)
                    )
                return messages
            finally:
                mail.logout()

        return await asyncio.to_thread(_sync)

    async def get_message(self, account: EmailAccount, message_id: str) -> Message:
        password = self._by_email[account.email]

        def _sync() -> Message:
            mail = self._connect(account.email, password)
            try:
                mail.select("INBOX")
                _, msg_data = mail.fetch(message_id.encode(), "(RFC822)")
                return _parse_imap_msg(message_id, msg_data[0][1], account.email)
            finally:
                mail.logout()

        return await asyncio.to_thread(_sync)

    async def delete_email(self, account: EmailAccount) -> bool:
        password = self._by_email[account.email]

        def _sync() -> bool:
            mail = self._connect(account.email, password)
            try:
                uids = self._search_uids(mail, account.email)
                for uid in uids:
                    mail.store(uid, "+FLAGS", "\\Deleted")
                mail.expunge()
                return True
            finally:
                mail.logout()

        return await asyncio.to_thread(_sync)

    async def get_domains(self) -> list[str]:
        return ["gmail.com"]

    async def health_check(self) -> bool:
        """Provider is healthy if at least one pooled account logs in."""
        results = await asyncio.gather(
            *[self._check_one(email, password) for email, password in self._accounts],
            return_exceptions=True,
        )
        ok = any(r is True for r in results)
        for (email, _), r in zip(self._accounts, results):
            if r is not True:
                log.warning("gmail pool: account %s health check failed: %s", email, r)
        return ok

    async def _check_one(self, address: str, password: str) -> bool:
        def _sync() -> bool:
            mail = self._connect(address, password)
            mail.logout()
            return True

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
