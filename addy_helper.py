#!/usr/bin/env python3
"""
Helper untuk provider addy.io pada Tempmail-MCP.
Tidak perlu install library tambahan (hanya stdlib Python).

Perintah:
  python addy_helper.py gen
      Membuat alias acak baru dan menampilkan email + token (mudah di-copy).

  python addy_helper.py check <email> <token>
      Menampilkan pesan-pesan yang masuk ke alias tersebut.

  python addy_helper.py watch <email> <token> [--timeout 180]
      Polling otomatis sampai ada pesan masuk, lalu menampilkan isi pesan
      (cocok untuk menunggu kode verifikasi). Berhenti setelah --timeout detik.

Contoh:
  python addy_helper.py gen
  python addy_helper.py check wc1senj5@anonaddy.me 43b9a262-3853-4f3d-a3dc-b45ae61d7289
  python addy_helper.py watch wc1senj5@anonaddy.me 43b9a262-3853-4f3d-a3dc-b45ae61d7289 --timeout 180
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def _request(method, url, data=None):
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        print(f"[ERROR] HTTP {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[ERROR] Tidak bisa mencapai server: {e}", file=sys.stderr)
        print("Pastikan server jalan: python -m src.main", file=sys.stderr)
        sys.exit(1)


def cmd_gen(_args):
    print("Membuat alias acak baru...")
    result = _request("POST", f"{BASE}/api/email?name=addy.io", data={})
    email = result["email"]
    token = result["token"]
    print("\n===== ALIAS BUKU =====")
    print(f"EMAIL   : {email}")
    print(f"TOKEN   : {token}")
    print(f"PROVIDER: {result['provider']}")
    print("========================")
    print(f"\n[COPY] {email}")
    print("\nGunakan EMAIL ini untuk mendaftar. Pesan/kode verifikasi akan")
    print("diteruskan ke inbox asli Anda. Cek dengan:")
    print(f'  python addy_helper.py check {email} {token}')
    print(f'  python addy_helper.py watch {email} {token}')


def _fetch_messages(email, token):
    q = urllib.parse.urlencode({"token": token, "name": "addy.io"})
    return _request("GET", f"{BASE}/api/email/{urllib.parse.quote(email)}/messages?{q}")


def _print_messages(messages):
    if not messages:
        print("(belum ada pesan masuk)")
        return
    print(f"{len(messages)} pesan ditemukan:\n")
    for i, m in enumerate(messages, 1):
        print(f"--- Pesan {i} ---")
        print(f"Dari     : {m.get('from_addr')}")
        print(f"Subjek   : {m.get('subject')}")
        body = m.get("body_text") or m.get("body_html") or ""
        print(f"Isi      : {body[:500]}{'...' if len(body) > 500 else ''}")
        print(f"ID       : {m.get('id')}")
        print()


def cmd_check(args):
    messages = _fetch_messages(args.email, args.token)
    _print_messages(messages)


def cmd_watch(args):
    print(f"Menunggu pesan untuk {args.email} (max {args.timeout}s)...")
    print("Tekan Ctrl+C untuk berhenti.\n")
    start = time.time()
    last = 0
    try:
        while time.time() - start < args.timeout:
            messages = _fetch_messages(args.email, args.token)
            if len(messages) > last:
                last = len(messages)
                print(f"[{time.strftime('%H:%M:%S')}] Ada {len(messages)} pesan baru!\n")
                _print_messages(messages)
                if len(messages) >= 1:
                    print("\n[Selesai] Pesan sudah muncul di atas.")
                    return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nBerhenti.")
    print(f"\n[Timeout] Tidak ada pesan dalam {args.timeout}s.")
    print("Coba cek manual: python addy_helper.py check", args.email, args.token)


def main():
    p = argparse.ArgumentParser(description="Helper addy.io untuk Tempmail-MCP")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("gen", help="Membuat alias acak baru").set_defaults(func=cmd_gen)

    pc = sub.add_parser("check", help="Cek pesan masuk")
    pc.add_argument("email")
    pc.add_argument("token")
    pc.set_defaults(func=cmd_check)

    pw = sub.add_parser("watch", help="Polling otomatis sampai pesan masuk")
    pw.add_argument("email")
    pw.add_argument("token")
    pw.add_argument("--timeout", type=int, default=180, help="Timeout detik (default 180)")
    pw.add_argument("--interval", type=int, default=10, help="Interval polling detik (default 10)")
    pw.set_defaults(func=cmd_watch)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
