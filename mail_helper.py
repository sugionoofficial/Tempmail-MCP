#!/usr/bin/env python3
"""
Helper untuk provider SimpleLogin pada Tempmail-MCP.
Tidak perlu install library tambahan (hanya stdlib Python).

CARA PAKAI UTAMA (satu perintah, ada menu interaktif):
  python mail_helper.py
      Membuka menu loop dengan opsi:
        1. Check data  — tampilkan data akun (lalu kembali ke menu)
        2. Start       — buat alias acak + tunggu pesan masuk
        3. Add data    — tambah akun Gmail baru (input email + app password)
        4. Set SL key  — input key API SimpleLogin (disimpan di config.json)
        5. Inbox       — diagnosis: lihat pesan terakhir di inbox asli (IMAP)
        0. Exit        — keluar

  Command lanjutan (CLI) tetap tersedia:
  python mail_helper.py add
      Menambah akun Gmail baru ke config.json via input (tanpa menu).

  python mail_helper.py run [--timeout 10]
      Satu kali jalan: buat alias, tampilkan alias + EMAIL ASLI, lalu tunggu pesan.

  python mail_helper.py gen
      Membuat alias acak baru dan menampilkan email + token.

  python mail_helper.py check <email> <token>
      Menampilkan pesan-pesan yang masuk ke alias tersebut.

  python mail_helper.py watch <email> <token> [--timeout 10]
      Polling otomatis sampai pesan masuk.

Contoh:
  python mail_helper.py
  python mail_helper.py run
  python mail_helper.py check 49ae604b@simplelogin.com 53705491 --provider simplelogin
"""
import argparse
import getpass
import html
import imaplib
import json
import os
import re
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
    line1 = ("  MAIL HELPER").ljust(width)
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
        ("4", "Set key", "Input key API SimpleLogin"),
        ("5", "Inbox", "Diagnosis inbox asli"),
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
            "Isi dengan data Anda. real_inbox = inbox asli tempat pesan "
            "SimpleLogin diteruskan (dan dibaca via IMAP). "
            "simplelogin_api_key = key API SimpleLogin. "
            "app_password = App Password 16 karakter TANPA spasi."
        ),
        "real_inbox": "",
        "simplelogin_api_key": "",
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
    mail yang diteruskan SimpleLogin sering dipindahkan ke label atau diarsip
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


def _create_alias(provider="simplelogin"):
    return _request("POST", f"{BASE}/api/email?name={provider}", data={})


def _fetch_messages(email, token, provider="simplelogin"):
    q = urllib.parse.urlencode({"token": token, "name": provider})
    return _request("GET", f"{BASE}/api/email/{urllib.parse.quote(email)}/messages?{q}")


def _strip_html(text):
    """Ubah body HTML menjadi teks bersih (tanpa tag) agar mudah dibaca/dicopy."""
    if not text:
        return ""
    # hapus <style> dan <script> block sepenuhnya
    text = re.sub(r"(?is)<(style|script).*?>.*?</\1>", " ", text)
    # hapus semua tag HTML
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # decode entity HTML
    text = html.unescape(text)
    # rapikan spasi putih berlebih
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _extract_code(text):
    """Ekstrak kode verifikasi dari teks (angka/pola umum).

    Mencari pola: 6-angka (ChatGPT/Google), 4-angka, 8-angka,
    dan kode alfanumerik 6-8 karakter yang tampak seperti OTP.
    Return string kode atau None.
    """
    if not text:
        return None
    plain = _strip_html(text) if "<" in text else text
    # 6-digit (paling umum untuk ChatGPT/Google OTP)
    m = re.search(r"(?<!\d)\d{6}(?!\d)", plain)
    if m:
        return m.group(0)
    # 4-digit
    m = re.search(r"(?<!\d)\d{4}(?!\d)", plain)
    if m:
        return m.group(0)
    # 8-digit
    m = re.search(r"(?<!\d)\d{8}(?!\d)", plain)
    if m:
        return m.group(0)
    # kode alfanumerik 6-8 char (huruf+angka, tanpa spasi)
    m = re.search(r"\b[A-Z0-9]{6,8}\b", plain)
    if m:
        return m.group(0)
    return None


def _print_messages(messages):
    if not messages:
        print(c("(belum ada pesan masuk)", DIM))
        return
    print(c(f"{len(messages)} pesan ditemukan\n", BOLD + GREEN))
    for i, m in enumerate(messages, 1):
        print(c(f"┌─ Pesan {i} " + "─" * 30, DIM))
        print(f"  {c('Dari  :', BOLD)} {c(m.get('from_addr') or '', CYAN)}")
        print(f"  {c('Subjek:', BOLD)} {c(m.get('subject') or '(tanpa subjek)', YELLOW)}")

        raw_body = m.get("body_text") or m.get("body_html") or ""
        body_plain = _strip_html(raw_body)

        # Tampilkan kode verifikasi prominently jika terdeteksi
        code = _extract_code(raw_body)
        if code:
            print(f"  {c('KODE :', BOLD + GREEN)} {c(code, BOLD + GREEN)}")
            print(c("       (copy: tekan Ctrl+C di terminal, atau pilih kode di atas)", DIM))

        # Tampilkan isi yang sudah dibersihkan (teks bersih, tidak HTML mentah)
        body_show = body_plain[:800] + ("..." if len(body_plain) > 800 else "")
        print(f"  {c('Isi   :', BOLD)} {body_show}")
        print(f"  {c('ID    :', BOLD)} {c(m.get('id') or '', DIM)}")
        print(c("└" + "─" * 40, DIM))
        print()


def cmd_gen(args):
    try:
        provider = getattr(args, "provider", "simplelogin")
        print(c(f"⏳  Membuat alias acak baru ({provider})...", CYAN))
        result = _with_spinner("Membuat alias acak baru", lambda: _create_alias(provider))
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
        print(f"  python mail_helper.py check {email} {token} --provider {provider}")
        print(f"  python mail_helper.py watch {email} {token} --provider {provider}")
    except ApiError as e:
        clear_line()
        _die(str(e))


def cmd_run(args):
    try:
        provider = getattr(args, "provider", "simplelogin")
        banner("GENERATE ALIAS")
        print(c(f"⏳  Membuat alias acak baru ({provider})...", CYAN))
        result = _with_spinner("Membuat alias acak baru", lambda: _create_alias(provider))
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
                messages = _fetch_messages(email, token, provider)
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
        print(c(f"Coba cek manual: python mail_helper.py check {email} {token} --provider {provider}", DIM))
    except ApiError as e:
        clear_line()
        _die(str(e))


def cmd_check(args):
    try:
        provider = getattr(args, "provider", "simplelogin")
        messages = _fetch_messages(args.email, args.token, provider)
        _print_messages(messages)
    except ApiError as e:
        _die(str(e))


def cmd_inbox(_args):
    """Diagnosis: tampilkan pesan terakhir di inbox asli (IMAP langsung)."""
    _imap_list_recent(15)


def cmd_watch(args):
    try:
        provider = getattr(args, "provider", "simplelogin")
        print(c(f"⏳  Menunggu pesan untuk {args.email} (max {args.timeout}s)...", CYAN))
        start = time.time()
        i = 0
        try:
            while time.time() - start < args.timeout:
                elapsed = int(time.time() - start)
                clear_line()
                sys.stdout.write(f"{c(spinner_frame(i), CYAN)} Menunggu pesan... {elapsed}s / {args.timeout}s")
                sys.stdout.flush()
                messages = _fetch_messages(args.email, args.token, provider)
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
        print(c(f"Coba cek manual: python mail_helper.py check {args.email} {args.token} --provider {provider}", DIM))
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


def _get_api_key():
    """Key API SimpleLogin: dari config.json, fallback ke .env SIMPLELOGIN_API_KEY."""
    cfg = _load_config()
    if cfg and cfg.get("simplelogin_api_key"):
        return cfg["simplelogin_api_key"]
    return _read_env_value("SIMPLELOGIN_API_KEY")


def cmd_set_key(_args):
    """Input key API SimpleLogin dan simpan ke config.json (tidak hardcode)."""
    banner("SET KEY API SIMPLELOGIN")
    print(c("Key ini disimpan di config.json — tidak hardcode di script.", DIM))
    print()

    existing = _get_api_key()
    if existing:
        shown = existing if len(existing) <= 8 else existing[:4] + "..." + existing[-4:]
        print(c(f"Key saat ini : {shown}", DIM))
        overwrite = input(c("Ganti key? [Y/n]: ", BOLD)).strip().lower()
        if overwrite not in ("", "y", "yes"):
            print(c("Key tidak diganti.", YELLOW))
            return

    key = getpass.getpass(c("Key API SimpleLogin : ", BOLD)).strip()
    if not key:
        print(c("[ERROR] Key tidak boleh kosong.", RED))
        return

    cfg = _ensure_config()
    cfg["simplelogin_api_key"] = key
    try:
        with open("config.json", "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(c("✓ Key API SimpleLogin disimpan ke config.json.", GREEN))
    except OSError as e:
        print(c(f"[ERROR] Gagal menulis config.json: {e}", RED))
        return

    print()
    print(c("Untuk gunakan key ini, tambahkan ke .env (wajib untuk server):", DIM))
    print(c("  SIMPLELOGIN_API_KEY=" + key, YELLOW))


def _show_data():
    """Tampilkan data akun dari config.json."""
    cfg = _load_config()
    banner("DATA AKUN")
    if not cfg:
        print(c("Belum ada config.json. Pilih menu '3. Add data' untuk menambah akun.", YELLOW))
        return
    real = cfg.get("real_inbox")
    print(f"  {c('EMAIL ASLI', BOLD)} : {c(real or '(belum diatur)', YELLOW)}")
    key = _get_api_key()
    if key:
        shown = key if len(key) <= 8 else key[:4] + "..." + key[-4:]
        print(f"  {c('KEY SIMPLELOGIN', BOLD)} : {c(shown, CYAN)}")
    else:
        print(f"  {c('KEY SIMPLELOGIN', BOLD)} : {c('(belum diatur)', DIM)}")
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
    """Periksa saat script dijalankan: minimal 1 akun Gmail + key SimpleLogin.

    Jika ada yang kosong, tampilkan peringatan. Script TIDAK keluar —
    menu tetap muncul di posisi semula agar pengguna bisa mengisi data.
    """
    cfg = _load_config()
    missing = []

    accounts = cfg.get("accounts", []) if cfg else []
    if not accounts:
        missing.append("1 akun Gmail (menu 3. Add data)")

    key = _get_api_key()
    if not key:
        missing.append("key API SimpleLogin (menu 4. Set key)")

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
    # informasi tambahan (tidak blocking): status key SimpleLogin
    sl_key = _get_api_key()
    if sl_key:
        print(c(f"  ✓ Key SimpleLogin: {sl_key[:4]}...{sl_key[-4:]}", DIM))
    else:
        print(c("  · Key SimpleLogin belum diatur (menu 4. Set key, opsional)", DIM))


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
            cmd_run(argparse.Namespace(timeout=timeout, interval=3, provider="simplelogin"))
        elif choice == "3":
            cmd_add(None)
        elif choice == "4":
            cmd_set_key(None)
        elif choice == "5":
            cmd_inbox(None)
        elif choice == "0":
            print(c("✓ Terima kasih, exit.", GREEN))
            break
        else:
            print(c("[ERROR] Pilihan tidak valid (0/1/2/3/4/5).", RED))


def main():
    p = argparse.ArgumentParser(
        description=c("Helper SimpleLogin untuk Tempmail-MCP — menu interaktif", BOLD + CYAN)
    )
    sub = p.add_subparsers(dest="command")

    pg = sub.add_parser("gen", help="Membuat alias acak baru")
    pg.add_argument("--provider", default="simplelogin", choices=["simplelogin"],
                    help="Provider alias (default: simplelogin)")
    pg.set_defaults(func=cmd_gen)

    sub.add_parser("add", help="Tambah akun Gmail baru ke config.json (input email + app password)").set_defaults(func=cmd_add)

    sub.add_parser("key", help="Input key API SimpleLogin ke config.json").set_defaults(func=cmd_set_key)

    sub.add_parser("inbox", help="Diagnosis: lihat pesan terakhir di inbox asli").set_defaults(func=cmd_inbox)

    pr = sub.add_parser("run", help="Satu kali jalan: buat alias, tampilkan, lalu tunggu pesan")
    pr.add_argument("--timeout", type=int, default=10, help="Timeout detik (default 10)")
    pr.add_argument("--interval", type=int, default=3, help="Interval polling detik (default 3)")
    pr.add_argument("--provider", default="simplelogin", choices=["simplelogin"],
                    help="Provider alias (default: simplelogin)")
    pr.set_defaults(func=cmd_run)

    pc = sub.add_parser("check", help="Cek pesan masuk")
    pc.add_argument("email")
    pc.add_argument("token")
    pc.add_argument("--provider", default="simplelogin", choices=["simplelogin"],
                    help="Provider alias (default: simplelogin)")
    pc.set_defaults(func=cmd_check)

    pw = sub.add_parser("watch", help="Polling otomatis sampai pesan masuk")
    pw.add_argument("email")
    pw.add_argument("token")
    pw.add_argument("--timeout", type=int, default=10, help="Timeout detik (default 10)")
    pw.add_argument("--interval", type=int, default=3, help="Interval polling detik (default 3)")
    pw.add_argument("--provider", default="simplelogin", choices=["simplelogin"],
                    help="Provider alias (default: simplelogin)")
    pw.set_defaults(func=cmd_watch)

    args = p.parse_args()
    if args.command is None:
        menu_loop()
    else:
        args.func(args)


if __name__ == "__main__":
    main()
