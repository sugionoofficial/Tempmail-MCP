#!/usr/bin/env python3
"""
Helper untuk provider addy.io pada Tempmail-MCP.
Tidak perlu install library tambahan (hanya stdlib Python).

CARA PAKAI UTAMA (satu perintah, ada menu interaktif):
  python addy_helper.py
      Membuka menu loop dengan opsi:
        1. Check data  — tampilkan data akun (lalu kembali ke menu)
        2. Start       — buat alias acak + tunggu pesan masuk
        3. Add data    — tambah akun Gmail baru (input email + app password)
        4. Set key     — input key API addy.io (disimpan di config.json)
        5. Inbox       — diagnosis: lihat pesan terakhir di inbox asli (IMAP)
        6. Aliases     — cek alias addy.io (ada/aktif)
        7. Failed      — cek failed deliveries addy.io
        8. Recipients  — cek tujuan forward addy.io
        0. Exit        — keluar

  Command lanjutan (CLI) tetap tersedia:
  python addy_helper.py add
      Menambah akun Gmail baru ke config.json via input (tanpa menu).

  python addy_helper.py run [--timeout 10]
      Satu kali jalan: buat alias, tampilkan alias + EMAIL ASLI, lalu tunggu pesan.

  python addy_helper.py gen
      Membuat alias acak baru dan menampilkan email + token.

  python addy_helper.py check <email> <token>
      Menampilkan pesan-pesan yang masuk ke alias tersebut.

  python addy_helper.py watch <email> <token> [--timeout 10]
      Polling otomatis sampai pesan masuk.

Contoh:
  python addy_helper.py
  python addy_helper.py run
  python addy_helper.py check wc1senj5@anonaddy.me 43b9a262-3853-4f3d-a3dc-b45ae61d7289
"""
import argparse
import getpass
import imaplib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"

# --- ANSI colors ---------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _color_enabled():
    """Cek apakah terminal mendukung ANSI escape.

    Windows PowerShell 5.1 (lama) tidak render ANSI — warna akan muncul
    sebagai teks mentah. Disable warna di situ; tetap aktif di Windows Terminal
    (WT_SESSION) dan PowerShell 7+/Linux/Termux.
    """
    if os.name != "nt":
        return True
    # Windows Terminal atau terminal modern yang support ANSI
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("TERM"):
        return True
    # PowerShell 7+ biasanya punya TERM; CMD/PowerShell 5.1 tidak
    return False


_COLOR_ENABLED = _color_enabled()


def c(text, color):
    if not _COLOR_ENABLED:
        return text
    return f"{color}{text}{RESET}"


def spinner_frame(i):
    return _SPINNER[i % len(_SPINNER)]


def clear_line():
    if _COLOR_ENABLED:
        sys.stdout.write("\r\033[K")
    else:
        sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()


def _clear_screen():
    """Bersihkan layar terminal (portable di Termux/Linux/Windows)."""
    # primary: gunakan command clear/cls — lebih reliabel di Termux
    try:
        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")
    except OSError:
        pass
    # fallback: ANSI erase + home (skip jika terminal tidak support ANSI,
    # misal Windows PowerShell 5.1 — cls sudah cukup)
    if _COLOR_ENABLED:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def banner(title):
    line = "═" * 46
    print(c(line, DIM))
    print(c(f"  {title}", BOLD + CYAN))
    print(c(line, DIM))


def _header_block():
    """Blok header keren yang muncul di awal."""
    width = 56
    top = c("╔" + "═" * width + "╗", MAGENTA)
    bottom = c("╚" + "═" * width + "╝", MAGENTA)
    line1 = ("  ADY.IO HELPER").ljust(width)
    line2 = ("  Tempmail-MCP · Menu Interaktif").ljust(width)
    print(top)
    print(c("║", MAGENTA) + c(line1, BOLD + CYAN) + c("║", MAGENTA))
    print(c("║", MAGENTA) + c(line2, DIM) + c("║", MAGENTA))
    print(bottom)


def _menu_box():
    """Menu box berbingkai yang rapi."""
    width = 56
    rows = [
        ("1", "Check data", "Tampilkan data akun"),
        ("2", "Start", "Buat alias + tunggu pesan"),
        ("3", "Add data", "Tambah akun baru"),
        ("4", "Set key", "Input key API addy.io"),
        ("5", "Inbox", "Diagnosis inbox asli"),
        ("6", "Aliases", "Cek alias addy.io"),
        ("7", "Failed", "Cek failed deliveries"),
        ("8", "Recipients", "Cek tujuan forward"),
        ("0", "Exit", "Keluar dari script"),
    ]
    print(c("┌" + "─" * width + "┐", CYAN))
    title = "  MENU  ".ljust(width)
    print(c("│", CYAN) + c(title, BOLD + CYAN) + c("│", CYAN))
    print(c("├" + "─" * width + "┤", CYAN))
    for num, name, desc in rows:
        label = f"[ {num} ]  {name}"
        gap = width - len(label) - len(desc)
        if gap < 2:
            gap = 2
        content = c(label, BOLD + CYAN) + " " * gap + c(desc, DIM)
        print(c("│", CYAN) + content + c("│", CYAN))
    print(c("└" + "─" * width + "┘", CYAN))


def info_box(rows):
    """Tampilkan baris-baris dalam box rapi."""
    width = 44
    print(c("┌" + "─" * width + "┐", DIM))
    for label, value in rows:
        # label kanan, value kiri
        pad = max(1, width - len(label) - 2)
        print(c("│", DIM) + c(f" {label} ", BOLD) + c(" " * pad, DIM) + c(f"{value}", DIM) + c("│", DIM))
    print(c("└" + "─" * width + "┘", DIM))


def _with_spinner(text, func):
    """Jalankan func di thread sambil menampilkan spinner animasi."""
    result = [None]
    exc = [None]

    def target():
        try:
            result[0] = func()
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=target)
    t.start()
    i = 0
    while t.is_alive():
        clear_line()
        sys.stdout.write(f"{c(spinner_frame(i), CYAN)} {text}")
        sys.stdout.flush()
        time.sleep(0.08)
        i += 1
    clear_line()
    t.join()
    if exc[0]:
        raise exc[0]
    return result[0]


class ApiError(Exception):
    pass


def _die(msg):
    print(c(f"[ERROR] {msg}", RED), file=sys.stderr)
    sys.exit(1)


def _request(method, url, data=None):
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise ApiError(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ApiError(f"Tidak bisa mencapai server: {e}\nPastikan server jalan: python -m src.main")


def _read_env():
    """Baca .env untuk mengambil GMAIL_EMAIL (fallback jika config.json tidak ada)."""
    try:
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GMAIL_EMAIL=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None


def _default_config():
    """Template config.json default (dibuat jika file belum ada)."""
    return {
        "_note": (
            "Isi dengan data Anda. real_inbox = inbox asli tempat pesan addy.io "
            "diteruskan (dan dibaca via IMAP). addy_api_key = key API addy.io. "
            "app_password = App Password 16 karakter TANPA spasi."
        ),
        "real_inbox": "",
        "addy_api_key": "",
        "accounts": [],
    }


def _load_config():
    """Baca config.json (data akun). Return dict atau None jika tidak ada/invalid."""
    try:
        with open("config.json", "r") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else None
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _ensure_config():
    """Baca config.json; jika tidak ada, buat file baru dengan template default."""
    cfg = _load_config()
    if cfg is None:
        cfg = _default_config()
        try:
            with open("config.json", "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(c(f"[ERROR] Gagal membuat config.json: {e}", RED))
    return cfg


def _get_addy_key():
    """Key API addy.io: dari config.json, fallback ke .env ADDY_API_KEY."""
    cfg = _load_config()
    if cfg and cfg.get("addy_api_key"):
        return cfg["addy_api_key"]
    try:
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ADDY_API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None


def _get_real_inbox():
    """Inbox asli tujuan forward: dari config.json, fallback ke .env GMAIL_EMAIL."""
    cfg = _load_config()
    if cfg and cfg.get("real_inbox"):
        return cfg["real_inbox"]
    return _read_env()


def _get_accounts():
    """Daftar akun dari config.json: [{email, app_password}, ...]."""
    cfg = _load_config()
    if cfg and isinstance(cfg.get("accounts"), list):
        return cfg["accounts"]
    return []


def _imap_credentials():
    """Baca GMAIL_EMAIL + GMAIL_APP_PASSWORD dari config.json atau .env."""
    cfg = _load_config()
    email = None
    pw = None
    if cfg:
        email = cfg.get("real_inbox") or None
        if not email and cfg.get("accounts"):
            email = cfg["accounts"][0].get("email")
        if email and cfg.get("accounts"):
            for a in cfg["accounts"]:
                if a.get("email") == email:
                    pw = a.get("app_password")
                    break
        if not pw:
            # first account as fallback
            if cfg.get("accounts"):
                email = cfg["accounts"][0].get("email")
                pw = cfg["accounts"][0].get("app_password")
    if not email:
        email = _read_env()
    if not pw:
        pw = _read_env_value("GMAIL_APP_PASSWORD")
    return email, pw


def _read_env_value(key):
    try:
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None


def _imap_msg_brief(mail, uid):
    """Ambil ringkasan (From/Subject/Date) dari satu pesan."""
    _, msg_data = mail.fetch(
        uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
    )
    if not msg_data or not msg_data[0]:
        return None
    raw = msg_data[0][1].decode("utf-8", "replace")
    from_v = sub_v = date_v = ""
    for line in raw.splitlines():
        ln = line.lower()
        if ln.startswith("from:"):
            from_v = line[5:].strip()
        elif ln.startswith("subject:"):
            sub_v = line[8:].strip()
        elif ln.startswith("date:"):
            date_v = line[5:].strip()
    return (from_v, sub_v, date_v)


def _imap_list_recent(limit=15):
    """Diagnosis: tampilkan pesan-pesan terakhir di inbox asli via IMAP langsung.

    Membaca INBOX, SPAM, TRASH, dan juga [Gmail]/All Mail + semua label —
    mail yang diteruskan addy.io sering dipindahkan ke label atau diarsip
    Gmail, jadi harus dicek di seluruh folder, bukan hanya INBOX.
    """
    email, pw = _imap_credentials()
    if not email or not pw:
        _die("EMAIL ASLI atau App Password belum terisi. Lengkapi via menu 3/4.")
    print(c(f"\n⏳  Menghubungi inbox asli {email} via IMAP...", CYAN))
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    try:
        mail.login(email, pw)

        # 1. list semua label Gmail
        print(c("\n--- Label Gmail ---", BOLD))
        try:
            _, lst = mail.list()
            labels = []
            for item in lst:
                if not item:
                    continue
                try:
                    decoded = item.decode("utf-8", "replace")
                except Exception:
                    decoded = str(item)
                # format: (flags) "delimiter" "name"
                if '"' in decoded:
                    name = decoded.split('"')[-2] if decoded.count('"') >= 2 else decoded
                else:
                    name = decoded
                name = name.strip().strip('"')
                if name:
                    labels.append(name)
            if labels:
                for lname in labels:
                    print(c(f"  • {lname}", DIM))
            else:
                print(c("  (tidak ada label terdaftar)", DIM))
        except imaplib.IMAP4.error:
            pass

        # 2. cari di folder utama + All Mail
        folders = [
            ("INBOX", "Inbox"),
            ("SPAM", "Spam"),
            ("TRASH", "Trash"),
            ("[Gmail]/All Mail", "All Mail"),
        ]
        found_total = 0
        for fname, flabel in folders:
            try:
                mail.select(fname)
                _, data = mail.search(None, "ALL")
            except imaplib.IMAP4.error:
                continue
            uids = list(data[0].split()) if data[0] else []
            if not uids:
                if fname == "INBOX":
                    print(c("(INBOX kosong)", DIM))
                continue
            uids = uids[-limit:]
            found_total += len(uids)
            print(c(f"\n{len(uids)} pesan terakhir di {flabel}:\n", BOLD + GREEN))
            for uid in reversed(uids):
                brief = _imap_msg_brief(mail, uid)
                if not brief:
                    continue
                from_v, sub_v, date_v = brief
                print(f"  {c('Dari   :', BOLD)} {c(from_v, CYAN)}")
                print(f"  {c('Subjek :', BOLD)} {c(sub_v or '(tanpa subjek)', YELLOW)}")
                print(f"  {c('Tanggal:', BOLD)} {c(date_v, DIM)}")
                print()
        if found_total == 0:
            print(c("(tidak ada pesan di INBOX/SPAM/TRASH/All Mail)", DIM))
    except imaplib.IMAP4.error as e:
        _die(f"IMAP login gagal: {e}\nPeriksa Email + App Password (16 karakter tanpa spasi).")
    except Exception as e:
        _die(f"IMAP gagal: {e}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _addy_api_get(path, params=None):
    """Call addy.io API directly (bypass server) untuk diagnosis."""
    key = _get_addy_key()
    if not key:
        _die("Key API addy.io belum terisi. Gunakan menu 4. Set key.")
    url = f"https://app.addy.io/api/v1{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise ApiError(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ApiError(f"Tidak bisa mencapai addy.io: {e}")


def cmd_aliases(_args):
    """Diagnosis: cek alias-alias di addy.io (ada/aktif) via API langsung."""
    try:
        data = _addy_api_get("/aliases")
    except ApiError as e:
        _die(str(e))
    aliases = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(aliases, list) or not aliases:
        print(c("(tidak ada alias di akun addy.io ini)", DIM))
        return
    print(c(f"\n{len(aliases)} alias di addy.io:\n", BOLD + GREEN))
    for a in aliases:
        email = a.get("email", "?")
        active = a.get("active", True)
        status = c("AKTIF", GREEN) if active else c("NON-AKTIF", RED)
        fwd = a.get("emails_forwarded", 0) or 0
        blocked = a.get("emails_blocked", 0) or 0
        last_forwarded = a.get("last_forwarded")
        last_blocked = a.get("last_blocked")
        print(f"  {c(email, CYAN)}   {status}")
        if fwd:
            print(f"      {c('forwarded :', BOLD)} {fwd}   {c('terakhir: ' + str(last_forwarded), DIM) if last_forwarded else ''}")
        else:
            print(f"      {c('forwarded :', BOLD)} {c('0', DIM)}")
        if blocked:
            print(f"      {c('blocked   :', BOLD)} {c(str(blocked), RED)}   {c('terakhir: ' + str(last_blocked), DIM) if last_blocked else ''}")
    print()
    print(c("Tip: alias harus AKTIF agar addy.io meneruskan pesan ke inbox asli.", DIM))
    print(c("  forwarded=0: belum ada mail masuk ke alias ini.", DIM))
    print(c("  Jika blocked > 0: mail masuk tapi ditolak addy.io — tidak sampai inbox asli.", DIM))


def cmd_failed(_args):
    """Diagnosis: cek failed deliveries addy.io (mail yang gagal di-forward)."""
    try:
        data = _addy_api_get("/failed-deliveries", {"filter[email_type]": "inbound"})
    except ApiError as e:
        msg = str(e)
        if "404" in msg:
            print(c("✗ Endpoint failed-deliveries tidak tersedia (404) untuk akun ini.", YELLOW))
            print(c("  Gunakan menu 6. Aliases — menampilkan emails_forwarded/emails_blocked per alias.", DIM))
            return
        _die(msg)
    fails = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(fails, list) or not fails:
        print(c("✓ Tidak ada failed delivery — semua mail yang masuk berhasil di-forward.", GREEN))
        print(c("  Artinya: opencode.ai belum mengirim ke alias, atau mail masih dalam perjalanan.", DIM))
        return
    print(c(f"\n{len(fails)} failed delivery di addy.io:\n", BOLD + RED))
    for f in fails:
        alias = f.get("alias_email", "?")
        sender = f.get("sender", "?")
        status = f.get("status", "?")
        code = f.get("code", "")
        tried = f.get("attempted_at", "")
        print(f"  {c('Alias  :', BOLD)} {c(alias, CYAN)}")
        print(f"  {c('Dari   :', BOLD)} {sender}")
        print(f"  {c('Status :', BOLD)} {c(status, RED)}  {c(code, DIM)}")
        print(f"  {c('Waktu  :', BOLD)} {c(str(tried), DIM)}")
        print()
    print(c("Mail yang gagal di-forward TIDAK akan sampai ke inbox asli.", YELLOW))


def cmd_recipients(_args):
    """Diagnosis: cek recipient (tujuan forward) addy.io via API langsung."""
    try:
        data = _addy_api_get("/recipients")
    except ApiError as e:
        msg = str(e)
        if "404" in msg:
            print(c("✗ Endpoint recipients tidak tersedia (404).", YELLOW))
            return
        _die(msg)
    recips = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(recips, list) or not recips:
        print(c("(tidak ada recipient di akun addy.io ini)", DIM))
        return
    print(c(f"\n{len(recips)} recipient (tujuan forward) addy.io:\n", BOLD + GREEN))
    for r in recips:
        email = r.get("email", "?")
        verified = r.get("email_verified_at")
        active = r.get("active", True)
        status = c("AKTIF", GREEN) if active else c("NON-AKTIF", RED)
        verified_txt = c("terverifikasi", GREEN) if verified else c("TIDAK terverifikasi", RED)
        print(f"  {c(email, CYAN)}   {status}   {verified_txt}")
    print()
    current = _get_real_inbox()
    if current:
        print(c(f"Inbox yang Anda cek (config.json): {current}", YELLOW))
        matches = [r for r in recips if r.get("email", "").lower() == current.lower()]
        if matches:
            print(c("✓ Recipient addy.io sama dengan inbox yang dicek — seharusnya mail masuk.", GREEN))
        else:
            print(c("✗ Recipient addy.io BERBEDA dengan inbox yang dicek!", RED))
            print(c("  Mail addy.io diteruskan ke recipient di atas, bukan ke inbox yang Anda cek.", YELLOW))
            print(c("  Sinkronkan: ubah recipient addy.io atau set real_inbox ke recipient tersebut.", DIM))


def _create_alias():
    return _request("POST", f"{BASE}/api/email?name=addy.io", data={})


def _fetch_messages(email, token):
    q = urllib.parse.urlencode({"token": token, "name": "addy.io"})
    return _request("GET", f"{BASE}/api/email/{urllib.parse.quote(email)}/messages?{q}")


def _print_messages(messages):
    if not messages:
        print(c("(belum ada pesan masuk)", DIM))
        return
    print(c(f"{len(messages)} pesan ditemukan\n", BOLD + GREEN))
    for i, m in enumerate(messages, 1):
        print(c(f"┌─ Pesan {i} " + "─" * 30, DIM))
        print(f"  {c('Dari  :', BOLD)} {c(m.get('from_addr') or '', CYAN)}")
        print(f"  {c('Subjek:', BOLD)} {c(m.get('subject') or '(tanpa subjek)', YELLOW)}")
        body = m.get("body_text") or m.get("body_html") or ""
        body_show = body[:600] + ("..." if len(body) > 600 else "")
        print(f"  {c('Isi   :', BOLD)} {body_show}")
        print(f"  {c('ID    :', BOLD)} {c(m.get('id') or '', DIM)}")
        print(c("└" + "─" * 40, DIM))
        print()


def cmd_gen(_args):
    try:
        print(c("⏳  Membuat alias acak baru...", CYAN))
        result = _with_spinner("Membuat alias acak baru", _create_alias)
        email = result["email"]
        token = result["token"]
        real = _get_real_inbox()
        accounts = _get_accounts()
        print()
        banner("ALIAS BUKU")
        rows = [
            ("EMAIL", c(email, BOLD + GREEN)),
            ("TOKEN", c(token, DIM)),
            ("PROVIDER", result["provider"]),
        ]
        if real:
            rows.append(("EMAIL ASLI", c(real, YELLOW)))
        info_box(rows)
        print()
        print(c(f"[COPY] {email}", BOLD + GREEN))
        print()
        if accounts:
            emails = ", ".join(a.get("email", "?") for a in accounts)
            print(c(f"Akun terkonfigurasi ({len(accounts)}): {emails}", DIM))
            print()
        print(c("Gunakan EMAIL ini untuk mendaftar. Cek pesan dengan:", DIM))
        print(f"  python addy_helper.py check {email} {token}")
        print(f"  python addy_helper.py watch {email} {token}")
    except ApiError as e:
        clear_line()
        _die(str(e))


def cmd_run(args):
    try:
        banner("GENERATE ALIAS")
        print(c("⏳  Membuat alias acak baru...", CYAN))
        result = _with_spinner("Membuat alias acak baru", _create_alias)
        email = result["email"]
        token = result["token"]
        real = _get_real_inbox()
        accounts = _get_accounts()

        print()
        banner("ALIAS & EMAIL ASLI")
        rows = [
            ("ALIAS", c(email, BOLD + GREEN)),
            ("TOKEN", c(token, DIM)),
            ("EMAIL ASLI", c(real or "(tidak terdeteksi)", YELLOW)),
            ("PROVIDER", result["provider"]),
        ]
        info_box(rows)
        print()
        print(c(f"[COPY] {email}", BOLD + GREEN))
        print(c(f"Daftar website menggunakan ALIAS di atas.", DIM))
        print(c(f"Kode verifikasi diteruskan ke EMAIL ASLI: {real or '(lihat .env/config.json)'}", DIM))
        if accounts:
            emails = ", ".join(a.get("email", "?") for a in accounts)
            print(c(f"Akun terkonfigurasi ({len(accounts)}): {emails}", DIM))
        print()

        print(c(f"⏳  Menunggu pesan masuk (max {args.timeout}s)...", CYAN))
        start = time.time()
        i = 0
        try:
            while time.time() - start < args.timeout:
                elapsed = int(time.time() - start)
                clear_line()
                sys.stdout.write(f"{c(spinner_frame(i), CYAN)} Menunggu pesan... {elapsed}s / {args.timeout}s")
                sys.stdout.flush()
                messages = _fetch_messages(email, token)
                if messages:
                    clear_line()
                    print()
                    _print_messages(messages)
                    print(c("✓  Pesan masuk — script berhenti.", BOLD + GREEN))
                    return
                time.sleep(args.interval)
                i += 1
        except KeyboardInterrupt:
            clear_line()
            print(c("\nBerhenti.", YELLOW))
        clear_line()
        print(c(f"✗  [Timeout] Tidak ada pesan dalam {args.timeout}s.", YELLOW))
        print(c(f"Coba cek manual: python addy_helper.py check {email} {token}", DIM))
    except ApiError as e:
        clear_line()
        _die(str(e))


def cmd_check(args):
    try:
        messages = _fetch_messages(args.email, args.token)
        _print_messages(messages)
    except ApiError as e:
        _die(str(e))


def cmd_inbox(_args):
    """Diagnosis: tampilkan pesan terakhir di inbox asli (IMAP langsung)."""
    _imap_list_recent(15)


def cmd_watch(args):
    try:
        print(c(f"⏳  Menunggu pesan untuk {args.email} (max {args.timeout}s)...", CYAN))
        start = time.time()
        i = 0
        try:
            while time.time() - start < args.timeout:
                elapsed = int(time.time() - start)
                clear_line()
                sys.stdout.write(f"{c(spinner_frame(i), CYAN)} Menunggu pesan... {elapsed}s / {args.timeout}s")
                sys.stdout.flush()
                messages = _fetch_messages(args.email, args.token)
                if messages:
                    clear_line()
                    print()
                    _print_messages(messages)
                    print(c("✓  Pesan masuk — script berhenti.", BOLD + GREEN))
                    return
                time.sleep(args.interval)
                i += 1
        except KeyboardInterrupt:
            clear_line()
            print(c("\nBerhenti.", YELLOW))
        clear_line()
        print(c(f"✗  [Timeout] Tidak ada pesan dalam {args.timeout}s.", YELLOW))
    except ApiError as e:
        clear_line()
        _die(str(e))


def cmd_add(_args):
    """Tambah akun Gmail baru ke config.json via input (tanpa edit file manual)."""
    banner("TAMBAH AKUN GMAIL")
    print(c("Isi data di bawah — akun otomatis disimpan ke config.json", DIM))
    print()

    email = input(c("Email Gmail        : ", BOLD)).strip()
    if not email or "@" not in email:
        print(c("[ERROR] Email tidak valid (harus mengandung @).", RED))
        return

    app_password = getpass.getpass(
        c("App Password        : ", BOLD)
    ).strip()
    if not app_password:
        print(c("[ERROR] App Password tidak boleh kosong.", RED))
        return

    cfg = _ensure_config()
    if not isinstance(cfg.get("accounts"), list):
        cfg["accounts"] = []

    # jika sudah ada email yang sama -> update password
    existing = next(
        (a for a in cfg["accounts"] if a.get("email") == email), None
    )
    if existing:
        existing["app_password"] = app_password
        print(c(f"✓ Email {email} sudah ada — App Password diperbarui.", GREEN))
    else:
        cfg["accounts"].append({"email": email, "app_password": app_password})
        print(c(f"✓ Akun {email} ditambahkan.", GREEN))

    # tanya apakah menjadi EMAIL ASLI (inbox tujuan)
    if not cfg.get("real_inbox"):
        set_inbox = input(
            c(f"Set sebagai EMAIL ASLI (inbox tujuan)? [Y/n]: ", BOLD)
        ).strip().lower()
        if set_inbox in ("", "y", "yes"):
            cfg["real_inbox"] = email
            print(c(f"✓ EMAIL ASLI diatur ke {email}.", GREEN))

    # tulis kembali ke config.json
    try:
        with open("config.json", "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(c("✓ config.json sudah diperbarui.", GREEN))
    except OSError as e:
        print(c(f"[ERROR] Gagal menulis config.json: {e}", RED))
        return

    print()
    banner("AKUN TERKONFIGURASI")
    for a in cfg["accounts"]:
        print(f"  • {c(a.get('email', '?'), CYAN)}")
    print(c(f"  Total: {len(cfg['accounts'])} akun", DIM))
    if cfg.get("real_inbox"):
        print(c(f"  EMAIL ASLI: {cfg['real_inbox']}", YELLOW))


def cmd_key(_args):
    """Input key API addy.io dan simpan ke config.json (tidak hardcode)."""
    banner("SET KEY API ADY.IO")
    print(c("Key ini disimpan di config.json — tidak hardcode di script.", DIM))
    print()

    existing = _get_addy_key()
    if existing:
        shown = existing if len(existing) <= 8 else existing[:4] + "..." + existing[-4:]
        print(c(f"Key saat ini : {shown}", DIM))
        overwrite = input(c("Ganti key? [Y/n]: ", BOLD)).strip().lower()
        if overwrite not in ("", "y", "yes"):
            print(c("Key tidak diganti.", YELLOW))
            return

    key = getpass.getpass(c("Key API addy.io     : ", BOLD)).strip()
    if not key:
        print(c("[ERROR] Key tidak boleh kosong.", RED))
        return

    cfg = _ensure_config()
    cfg["addy_api_key"] = key
    try:
        with open("config.json", "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(c("✓ Key API addy.io disimpan ke config.json.", GREEN))
    except OSError as e:
        print(c(f"[ERROR] Gagal menulis config.json: {e}", RED))
        return

    print()
    print(c("Untuk gunakan key ini, tambahkan ke .env juga (opsional):", DIM))
    print(c("  ADDY_API_KEY=" + key, YELLOW))


def _show_data():
    """Tampilkan data akun dari config.json."""
    cfg = _load_config()
    banner("DATA AKUN")
    if not cfg:
        print(c("Belum ada config.json. Pilih menu '3. Add data' untuk menambah akun.", YELLOW))
        return
    real = cfg.get("real_inbox")
    print(f"  {c('EMAIL ASLI', BOLD)} : {c(real or '(belum diatur)', YELLOW)}")
    key = cfg.get("addy_api_key")
    if key:
        shown = key if len(key) <= 8 else key[:4] + "..." + key[-4:]
        print(f"  {c('KEY ADY.IO', BOLD)} : {c(shown, CYAN)}")
    else:
        print(f"  {c('KEY ADY.IO', BOLD)} : {c('(belum diatur)', DIM)}")
    accounts = cfg.get("accounts", [])
    if not accounts:
        print(c("  Belum ada akun di accounts[]. Pilih '3. Add data'.", DIM))
        return
    print()
    for i, a in enumerate(accounts, 1):
        email = a.get("email", "?")
        pw = a.get("app_password", "")
        print(f"  {c(str(i), BOLD)}. {c(email, CYAN)}   {c(pw, DIM)}")
    print(c(f"  Total: {len(accounts)} akun", DIM))


def _preflight_check():
    """Periksa saat script dijalankan: minimal 1 akun Gmail + key addy.io.

    Jika ada yang kosong, tampilkan peringatan. Script TIDAK keluar —
    menu tetap muncul di posisi semula agar pengguna bisa mengisi data.
    """
    cfg = _load_config()
    missing = []

    accounts = cfg.get("accounts", []) if cfg else []
    if not accounts:
        missing.append("1 akun Gmail (menu 3. Add data)")

    key = _get_addy_key()
    if not key:
        missing.append("key API addy.io (menu 4. Set key)")

    if missing:
        print()
        print(c("┌" + "─" * 56 + "┐", YELLOW))
        print(c("│", YELLOW) + c("  ⚠ PERINGATAN DATA", BOLD + YELLOW) + c("│", YELLOW))
        print(c("├" + "─" * 56 + "┤", YELLOW))
        print(c("│", YELLOW) + c("  Data yang belum terisi:", BOLD) + c("│", YELLOW))
        for m in missing:
            print(c("│", YELLOW) + c("  • " + m, YELLOW) + c("│", YELLOW))
        print(c("│", YELLOW) + c("  Lengkapi terlebih dahulu, script tetap", YELLOW) + c("│", YELLOW))
        print(c("│", YELLOW) + c("  jalan dan tidak akan keluar.", YELLOW) + c("│", YELLOW))
        print(c("└" + "─" * 56 + "┘", YELLOW))
        print()


def menu_loop():
    """Menu interaktif utama — loop sampai Exit."""
    _clear_screen()
    while True:
        _preflight_check()
        print()
        _header_block()
        print()
        _menu_box()
        choice = input(c("\n  ❯ Pilih menu: ", BOLD)).strip()

        if choice == "1":
            _show_data()
        elif choice == "2":
            timeout_str = input(c("Timeout (detik, default 10): ", BOLD)).strip()
            try:
                timeout = int(timeout_str) if timeout_str else 10
            except ValueError:
                timeout = 10
            if timeout <= 0:
                print(c("[ERROR] Timeout harus > 0.", RED))
                continue
            cmd_run(argparse.Namespace(timeout=timeout, interval=3))
        elif choice == "3":
            cmd_add(None)
        elif choice == "4":
            cmd_key(None)
        elif choice == "5":
            cmd_inbox(None)
        elif choice == "6":
            cmd_aliases(None)
        elif choice == "7":
            cmd_failed(None)
        elif choice == "8":
            cmd_recipients(None)
        elif choice == "0":
            print(c("✓ Terima kasih, exit.", GREEN))
            break
        else:
            print(c("[ERROR] Pilihan tidak valid (0/1/2/3/4/5/6/7/8).", RED))


def main():
    p = argparse.ArgumentParser(
        description=c("Helper addy.io untuk Tempmail-MCP — menu interaktif", BOLD + CYAN)
    )
    sub = p.add_subparsers(dest="command")

    sub.add_parser("gen", help="Membuat alias acak baru").set_defaults(func=cmd_gen)

    sub.add_parser("add", help="Tambah akun Gmail baru ke config.json (input email + app password)").set_defaults(func=cmd_add)

    sub.add_parser("key", help="Input key API addy.io ke config.json").set_defaults(func=cmd_key)

    sub.add_parser("inbox", help="Diagnosis: lihat pesan terakhir di inbox asli").set_defaults(func=cmd_inbox)

    sub.add_parser("aliases", help="Diagnosis: cek alias addy.io (ada/aktif)").set_defaults(func=cmd_aliases)

    sub.add_parser("failed", help="Diagnosis: cek failed deliveries addy.io").set_defaults(func=cmd_failed)

    sub.add_parser("recipients", help="Diagnosis: cek tujuan forward addy.io").set_defaults(func=cmd_recipients)

    pr = sub.add_parser("run", help="Satu kali jalan: buat alias, tampilkan, lalu tunggu pesan")
    pr.add_argument("--timeout", type=int, default=10, help="Timeout detik (default 10)")
    pr.add_argument("--interval", type=int, default=3, help="Interval polling detik (default 3)")
    pr.set_defaults(func=cmd_run)

    pc = sub.add_parser("check", help="Cek pesan masuk")
    pc.add_argument("email")
    pc.add_argument("token")
    pc.set_defaults(func=cmd_check)

    pw = sub.add_parser("watch", help="Polling otomatis sampai pesan masuk")
    pw.add_argument("email")
    pw.add_argument("token")
    pw.add_argument("--timeout", type=int, default=10, help="Timeout detik (default 10)")
    pw.add_argument("--interval", type=int, default=3, help="Interval polling detik (default 3)")
    pw.set_defaults(func=cmd_watch)

    args = p.parse_args()
    if args.command is None:
        menu_loop()
    else:
        args.func(args)


if __name__ == "__main__":
    main()
