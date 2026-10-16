# -*- coding: utf-8 -*-
import os
import re
import time
import random
import string
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# --- SERWER KEEP-ALIVE DLA RENDER.COM ---
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"OK | Checked: {CHECKED} | Found: {FOUND}".encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return

def start_keep_alive():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
# ----------------------------------------

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
START_NAME = os.environ.get("START_NAME", "").strip() or None

ALLOWED_CHARS = string.ascii_lowercase + string.digits + "_."
ALLOWED_CHARS_FIRST = string.ascii_lowercase + string.digits

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

DATA_LOCK = threading.Lock()
CHECKED = 0
FOUND = 0
RATE_LIMITS = 0
WORKING_PROXIES = []
LAST_PROXY_FETCH = 0
PROXY_FETCH_INTERVAL = 600  # Odświeżanie proxy co 10 minut

def is_valid_username(name: str) -> bool:
    if ".." in name or "__" in name or "._" in name or "_." in name:
        return False
    if name[0] in "_." or name[-1] in "_.":
        return False
    return 3 <= len(name) <= 4

def generate_4char_names(start_name=None):
    skip = start_name is not None
    for a in ALLOWED_CHARS_FIRST:
        for b in ALLOWED_CHARS:
            for c in ALLOWED_CHARS:
                for d in ALLOWED_CHARS:
                    name = a + b + c + d
                    if skip:
                        if name == start_name:
                            skip = False
                        else:
                            continue
                    if is_valid_username(name):
                        yield name

def send_webhook(name: str):
    if not WEBHOOK_URL:
        return
    embed = {
        "title": "Znaleziono wolna nazwe!",
        "description": f"**Nazwa:** `{name}`\n**Dlugosc:** {len(name)} znaki\n**Status:** Dostepna (Zweryfikowana)",
        "color": 0x00FF00,
        "timestamp": datetime.utcnow().isoformat()
    }
    try:
        requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
    except Exception as e:
        print(f"[!] Blad webhooka: {e}", flush=True)

def fetch_and_test_proxies():
    global WORKING_PROXIES, LAST_PROXY_FETCH
    print("[*] Pobieranie darmowych proxy...", flush=True)
    sources = [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/protocols/http/data.txt"
    ]
    raw_proxies = set()
    pattern = re.compile(r"(\d+\.\d+\.\d+\.\d+:\d+)")

    for url in sources:
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                for match in pattern.findall(r.text):
                    raw_proxies.add(f"http://{match}")
        except Exception:
            pass

    sample = random.sample(list(raw_proxies), min(len(raw_proxies), 200))
    print(f"[*] Testowanie {len(sample)} losowych proxy...", flush=True)

    def test_single(p):
        try:
            start = time.time()
            r = requests.get("https://discord.com", proxies={"http": p, "https": p}, timeout=4)
            if r.status_code == 200:
                return p, time.time() - start
        except Exception:
            pass
        return None, 999

    working = []
    with ThreadPoolExecutor(max_workers=30) as ex:
        for p, lat in ex.map(test_single, sample):
            if p:
                working.append((p, lat))

    working.sort(key=lambda x: x[1])
    with DATA_LOCK:
        WORKING_PROXIES = [p[0] for p in working]
        LAST_PROXY_FETCH = time.time()

    print(f"[*] Aktywne proxy: {len(WORKING_PROXIES)}", flush=True)

def check_username(username: str, proxy: str):
    global CHECKED, FOUND, RATE_LIMITS
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Content-Type": "application/json",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/register",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        r = requests.post(
            "https://discord.com/api/v9/unique-username/username-attempt-unauthed",
            json={"username": username},
            headers=headers,
            proxies=proxies,
            timeout=6
        )

        if r.status_code == 200:
            data = r.json()
            if "taken" in data:
                with DATA_LOCK:
                    CHECKED += 1
                if data["taken"] is False:
                    with DATA_LOCK:
                        FOUND += 1
                    print(f"[+] DOSTEPNA: {username}", flush=True)
                    send_webhook(username)
                    return True
                return False

        elif r.status_code == 429:
            with DATA_LOCK:
                RATE_LIMITS += 1
            # Jeśli proxy dostało bana/rate-limit, usuwamy je z puli
            if proxy:
                with DATA_LOCK:
                    if proxy in WORKING_PROXIES and len(WORKING_PROXIES) > 3:
                        WORKING_PROXIES.remove(proxy)
    except Exception:
        pass
    return None

def main():
    start_keep_alive()
    print("[*] Start cHv Sniper (Headless Server Mode)", flush=True)
    if not WEBHOOK_URL:
        print("[!] UWAGA: Brak zmiennej WEBHOOK_URL! Wyniki beda widoczne tylko w logach.", flush=True)

    fetch_and_test_proxies()
    start_time = time.time()
    proxy_idx = 0

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = set()

        for name in generate_4char_names(START_NAME):
            # Odświeżanie puli proxy co 10 minut lub gdy zostanie mniej niż 3
            if time.time() - LAST_PROXY_FETCH > PROXY_FETCH_INTERVAL or len(WORKING_PROXIES) < 3:
                fetch_and_test_proxies()

            with DATA_LOCK:
                if WORKING_PROXIES:
                    proxy = WORKING_PROXIES[proxy_idx % len(WORKING_PROXIES)]
                    proxy_idx += 1
                else:
                    proxy = None

            futures.add(executor.submit(check_username, name, proxy))

            # Ograniczenie kolejki w pamięci RAM (ważne przy 512 MB na Renderze)
            if len(futures) >= 45:
                done, futures = next(as_completed(futures)), {f for f in futures if not f.done()}
                if CHECKED > 0 and CHECKED % 50 == 0:
                    elapsed = max(1, int(time.time() - start_time))
                    print(
                        f"[*] Sprawdzono: {CHECKED} | Wolne: {FOUND} | "
                        f"Predkosc: {CHECKED/elapsed:.1f}/s | Proxy: {len(WORKING_PROXIES)} | "
                        f"Ostatnia: {name}",
                        flush=True
                    )

            time.sleep(0.05)

if __name__ == "__main__":
    main()
