"""
addy.io provider — creates random, unique email aliases that forward to your
real inbox, and reads the forwarded messages from that real inbox.

How it works (hybrid):
  * create_email()  → asks addy.io for a fresh random alias (e.g. x7k2p9@anonaddy.me)
                       and returns it. addy.io auto-forwards anything sent to
                       that alias to your real inbox.
  * get_messages()  → addy.io does NOT expose message bodies via its API
                       (it is a forwarder, not a mailbox host). So we read the
                       forwarded mail directly from your REAL inbox via IMAP,
                       filtering by the alias address in the To: header.
  * delete_email()  → deletes the alias on addy.io and clears matching mail.

Required env vars:
  ADDY_API_KEY          — addy.io personal access token (Bearer)
                          Get it at https://app.addy.io/settings/api
  GMAIL_EMAIL           — your REAL inbox that addy.io forwards to
  GMAIL_APP_PASSWORD    — App Password for that real inbox (IMAP)

Optional:
  ADDY_DOMAIN           — which domain to create aliases on (e.g. anonaddy.me).
                          If unset, the first available domain is auto-picked.

Note: the alias domain is NOT gmail.com — it is the addy.io domain
(anonaddy.me / anonaddy.com / your subdomain / custom domain). Websites that
require gmail.com specifically will not accept these aliases.
"""
import asyncio
import email as email_lib
import imaplib
import logging
import os
from email.header import decode_header
from typing import Optional

import httpx

from .base import Attachment, EmailAccount, EmailProvider, Message
from .gmail import IMAP_HOST, IMAP_PORT, _decode

log = logging.getLogger(__name__)

API_BASE = "https://app.addy.io/api/v1"
IMAP_TIMEOUT = 30


def _decode_header(value: Optional[str]) -> str:
    return _decode(value)


def _parse_imap_msg(uid: str, raw: bytes, to_addr: str) -> Message:
    msg = email_lib.message_from_bytes(raw)
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    attachments: list[Attachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                filename = _decode_header(part.get_filename() or "")
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    Attachment(filename=filename, content_type=ct, size=len(payload))
                )
            elif ct == "text/plain" and body_text is None:
                body_text = (part.get_payload(decode=True) or b"").decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
            elif ct == "text/html" and body_html is None:
                body_html = (part.get_payload(decode=True) or b"").decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    else:
        ct = msg.get_content_type()
        payload = (msg.get_payload(decode=True) or b"").decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
        if ct == "text/html":
            body_html = payload
        else:
            body_text = payload

    return Message(
        id=uid,
        from_addr=_decode_header(msg.get("From", "")),
        to_addr=to_addr,
        subject=_decode_header(msg.get("Subject", "")),
        body_text=body_text,
        body_html=body_html,
        created_at=msg.get("Date", ""),
        cc=msg.get("Cc"),
        attachments=attachments,
    )


class AddyIOProvider(EmailProvider):
    """
    Random addy.io aliases forwarding to a real inbox, read back via IMAP.
    """

    name = "addy.io"

    def __init__(self) -> None:
        self._api_key = os.environ["ADDY_API_KEY"]
        self._gmail_address = os.environ["GMAIL_EMAIL"]
        self._gmail_password = os.environ["GMAIL_APP_PASSWORD"]
        self._user, self._domain = self._gmail_address.split("@", 1)
        self._domain_override = os.getenv("ADDY_DOMAIN", "").strip()
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ helpers

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        }

    async def _pick_domain(self) -> str:
        """Return the alias domain to use (override, default_alias_domain, or /domains)."""
        if self._domain_override:
            return self._domain_override
        # Prefer account-details default_alias_domain — reliable even for
        # fresh accounts where the /domains list may be empty.
        try:
            resp = await self._client.get("/account-details", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            d = data.get("data", data) if isinstance(data, dict) else {}
            dom = d.get("default_alias_domain") if isinstance(d, dict) else None
            if dom:
                return dom
        except Exception as exc:
            log.warning("addy.io: account-details lookup failed: %s", exc)
        # Fallback: first domain from /domains list
        resp = await self._client.get("/domains", headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        domains = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(domains, list) and domains:
            return domains[0].get("domain") or domains[0].get("name")
        raise ValueError("No addy.io domain available")

    async def _create_alias_remote(self) -> tuple[str, str]:
        """Create a random alias on addy.io. Returns (email, alias_id)."""
        domain = await self._pick_domain()
        payload = {
            "domain": domain,
            "format": "random_characters",
            "description": "tempmail-mcp",
        }
        resp = await self._client.post("/aliases", json=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        alias = data.get("data", data) if isinstance(data, dict) else data
        email = alias.get("email")
        alias_id = alias.get("id") or alias.get("uuid")
        if not email:
            raise ValueError(f"addy.io create response missing email: {data}")
        # Fallback: look up id by email if not present in create response
        if not alias_id:
            alias_id = await self._find_alias_id(email)
        return email, alias_id

    async def _find_alias_id(self, email: str) -> str:
        resp = await self._client.get(
            "/aliases",
            params={"filter[search]": email},
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        aliases = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(aliases, list) and aliases:
            return aliases[0].get("id") or aliases[0].get("uuid")
        raise ValueError(f"addy.io alias not found for {email}")

    def _connect_imap(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT)
        mail.login(self._gmail_address, self._gmail_password)
        return mail

    def _collect_matching(
        self, mail: imaplib.IMAP4_SSL, to_addr: str
    ) -> list[tuple[str, bytes]]:
        """Find messages matching the alias across INBOX, SPAM and TRASH.

        Returns a list of (folder, uid) so callers can operate (fetch/store)
        on each message in the correct folder. Each folder is selected,
        searched, and (fallback) scanned while it is the active selection,
        which keeps the IMAP connection in a valid SELECTED state.

        addy.io-forwarded mail is often classified as spam by Gmail, so it
        must be searched in the SPAM folder too — not just INBOX.
        """
        crit = f'(OR (TO "{to_addr}") (CC "{to_addr}"))'
        needle = to_addr.lower()
        # Gmail IMAP folder names — try system + Gmail-prefixed variants.
        folders = ("INBOX", "[Gmail]/Spam", "SPAM", "[Gmail]/Trash", "TRASH")
        results: list[tuple[str, bytes]] = []
        for folder in folders:
            try:
                mail.select(folder)
            except imaplib.IMAP4.error:
                continue
            # direct search by To/Cc
            try:
                _, data = mail.search(None, crit)
                uids = list(data[0].split()) if data[0] else []
            except imaplib.IMAP4.error:
                uids = []
            # fallback: scan recent headers for the alias anywhere
            if not uids:
                try:
                    _, data = mail.search(None, "ALL")
                    all_uids = list(data[0].split()) if data[0] else []
                except imaplib.IMAP4.error:
                    all_uids = []
                for uid in all_uids[-30:]:
                    _, msg_data = mail.fetch(uid, "(BODY.PEEK[HEADER])")
                    if not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    if needle in raw.decode("utf-8", "replace").lower():
                        uids.append(uid)
            for uid in uids:
                results.append((folder, uid))
        return results

    # ------------------------------------------------------------------ interface

    async def create_email(
        self,
        min_name_length: int = 10,
        max_name_length: int = 10,
        domain: Optional[str] = None,
    ) -> EmailAccount:
        email, alias_id = await self._create_alias_remote()
        return EmailAccount(email=email, token=alias_id or email, provider=self.name)

    async def get_messages(self, account: EmailAccount) -> list[Message]:
        def _sync() -> list[Message]:
            mail = self._connect_imap()
            try:
                matches = self._collect_matching(mail, account.email)
                messages = []
                for folder, uid in matches:
                    # re-select the folder so FETCH runs in a valid SELECTED state
                    try:
                        mail.select(folder)
                        _, msg_data = mail.fetch(uid, "(RFC822)")
                    except imaplib.IMAP4.error:
                        continue
                    if msg_data and msg_data[0]:
                        messages.append(
                            _parse_imap_msg(uid.decode(), msg_data[0][1], account.email)
                        )
                return messages
            finally:
                mail.logout()

        return await asyncio.to_thread(_sync)

    async def get_message(self, account: EmailAccount, message_id: str) -> Message:
        def _sync() -> Message:
            mail = self._connect_imap()
            try:
                mail.select("INBOX")
                _, msg_data = mail.fetch(message_id.encode(), "(RFC822)")
                return _parse_imap_msg(message_id, msg_data[0][1], account.email)
            finally:
                mail.logout()

        return await asyncio.to_thread(_sync)

    async def delete_email(self, account: EmailAccount) -> bool:
        # Delete the alias on addy.io (best-effort; id may be email fallback)
        alias_id = account.token
        if alias_id and alias_id != account.email:
            try:
                resp = await self._client.delete(
                    f"/aliases/{alias_id}", headers=self._headers()
                )
                resp.raise_for_status()
            except Exception as exc:
                log.warning("addy.io delete alias %s failed: %s", alias_id, exc)
        # Clear matching mail from the real inbox
        def _sync() -> bool:
            mail = self._connect_imap()
            try:
                matches = self._collect_matching(mail, account.email)
                for folder, uid in matches:
                    try:
                        mail.select(folder)
                        mail.store(uid, "+FLAGS", "\\Deleted")
                    except imaplib.IMAP4.error:
                        continue
                mail.expunge()
                return True
            finally:
                mail.logout()

        return await asyncio.to_thread(_sync)

    async def get_domains(self) -> list[str]:
        result: list[str] = []
        # default_alias_domain from account-details
        try:
            resp = await self._client.get("/account-details", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            d = data.get("data", data) if isinstance(data, dict) else {}
            if isinstance(d, dict) and d.get("default_alias_domain"):
                result.append(d["default_alias_domain"])
        except Exception:
            pass
        # any domains from /domains list
        try:
            resp = await self._client.get("/domains", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            domains = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(domains, list):
                for d in domains:
                    val = d.get("domain") or d.get("name")
                    if val and val not in result:
                        result.append(val)
        except Exception:
            pass
        return result or [await self._pick_domain()]

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/api-token-details", headers=self._headers())
            resp.raise_for_status()
            return True
        except Exception:
            return False
