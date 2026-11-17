"""Lokal Masaüstü Başlatıcı (Windows .exe ve macOS Uygulama Modu)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# PyInstaller frozen .exe desteği ve penceresiz (--windowed) stdout/stderr koruması
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    os.chdir(str(BASE_DIR))
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

if sys.stdout is None or sys.stderr is None:
    try:
        log_dir = Path(os.getcwd()) / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "app_runtime.log", "a", encoding="utf-8")
    except Exception:
        log_file = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log_file
    if sys.stderr is None:
        sys.stderr = log_file

import uvicorn
from src.app import app


def is_existing_instance_running(port: int = 8765) -> bool:
    """Port 8765 üzerinde halihazırda çalışan bir uygulama örneği olup olmadığını kontrol eder."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_free_port(preferred: int = 8765) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def open_desktop_window(url: str, delay: float = 1.0) -> None:
    """Tarayıcı sekmesi yerine yerel masaüstü penceresi (--app modu) olarak açar."""
    if delay > 0:
        time.sleep(delay)
    if sys.platform.startswith("win"):
        edge_paths = [
            shutil.which("msedge"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for p in edge_paths:
            if p and os.path.exists(p):
                subprocess.Popen([p, f"--app={url}", "--window-size=1440,900"])
                return
    elif sys.platform == "darwin":
        chrome_mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(chrome_mac):
            subprocess.Popen([chrome_mac, f"--app={url}", "--window-size=1440,900"])
            return

    webbrowser.open(url)


def main() -> None:
    preferred_port = 8765
    # Eğer uygulama zaten arka planda çalışıyorsa ikinci kopyayı başlatma; mevcut pencereyi aç
    if is_existing_instance_running(preferred_port):
        url = f"http://127.0.0.1:{preferred_port}"
        if os.getenv("NO_BROWSER") != "1":
            open_desktop_window(url, delay=0.0)
        return

    port = find_free_port(preferred_port)
    url = f"http://127.0.0.1:{port}"
    print(f"Petlebi Kolay İK <-> DİA ERP Uygulaması başlatılıyor: {url}")

    if os.getenv("NO_BROWSER") != "1":
        threading.Thread(target=open_desktop_window, args=(url, 1.0), daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", log_config=None)


if __name__ == "__main__":
    main()
