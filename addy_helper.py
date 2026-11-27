#!/usr/bin/env python3
"""
Helper untuk provider addy.io pada Tempmail-MCP.
Tidak perlu install library tambahan (hanya stdlib Python).

Perintah:
  python addy_helper.py run [--timeout 10]
      SATU KALI JALAN: buat alias acak, tampilkan alias + EMAIL ASLI (inbox
      tujuan forward), lalu otomatis tunggu pesan masuk selama --timeout detik.
      Jika pesan masuk, langsung menampilkan isinya dan berhenti.

  python addy_helper.py gen
      Membuat alias acak baru dan menampilkan email + token (mudah di-copy).

  python addy_helper.py check <email> <token>
      Menampilkan pesan-pesan yang masuk ke alias tersebut.

  python addy_helper.py watch <email> <token> [--timeout 10]
      Polling otomatis selama --timeout detik. Jika ada pesan masuk, langsung
      menampilkan isi pesan dan berhenti.

Contoh:
  python addy_helper.py run
  python addy_helper.py check wc1senj5@anonaddy.me 43b9a262-3853-4f3d-a3dc-b45ae61d7289
  python addy_helper.py watch wc1senj5@anonaddy.me 43b9a262-3853-4f3d-a3dc-b45ae61d7289 --timeout 10
"""
import argparse
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


def c(text, color):
    return f"{color}{text}{RESET}"


def spinner_frame(i):
    return _SPINNER[i % len(_SPINNER)]


def clear_line():
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def banner(title):
    line = "═" * 46
    print(c(line, DIM))
    print(c(f"  {title}", BOLD + CYAN))
    print(c(line, DIM))


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
    """Baca .env untuk mengambil GMAIL_EMAIL (inbox asli tujuan forward)."""
    try:
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GMAIL_EMAIL=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None


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
        real = _read_env()
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
        real = _read_env()

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
        print(c(f"Kode verifikasi diteruskan ke EMAIL ASLI: {real or '(lihat .env)'}", DIM))
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


def main():
    p = argparse.ArgumentParser(
        description=c("Helper addy.io untuk Tempmail-MCP", BOLD + CYAN)
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("gen", help="Membuat alias acak baru").set_defaults(func=cmd_gen)

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
    args.func(args)


if __name__ == "__main__":
    main()
