"""Uygulama İçi Uzaktan Güncelleme (Hot-Update & GitHub Release Installer) Modülü."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Tuple

from .config import ROOT_DIR, settings

VERSION_FILE = ROOT_DIR / "version.json"


def get_local_version_info() -> Dict[str, Any]:
    if VERSION_FILE.exists():
        try:
            return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": settings.APP_VERSION, "released_at": "2026-11-17", "notes": ""}


def _parse_ver(v: str) -> Tuple[int, ...]:
    parts = []
    for p in str(v or "0.0.0").lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_updates() -> Dict[str, Any]:
    """
    Yalnızca güvenilir resmi GitHub deposu (TRUSTED_GITHUB_REPO) üzerinden yeni sürüm kontrolü yapar.
    """
    local_info = get_local_version_info()
    local_ver = str(local_info.get("version") or settings.APP_VERSION)
    repo = settings.TRUSTED_GITHUB_REPO

    raw_url = f"https://raw.githubusercontent.com/{repo}/main/version.json"
    req = urllib.request.Request(raw_url, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            remote_info = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {
            "configured": True,
            "repo": repo,
            "update_available": False,
            "local_version": local_ver,
            "remote_version": local_ver,
            "message": f"Güncelleme sunucusuna ulaşılamadı: {exc}",
        }

    remote_ver = str(remote_info.get("version") or local_ver)
    has_update = _parse_ver(remote_ver) > _parse_ver(local_ver)

    return {
        "configured": True,
        "repo": repo,
        "update_available": has_update,
        "local_version": local_ver,
        "remote_version": remote_ver,
        "released_at": remote_info.get("released_at", ""),
        "notes": remote_info.get("notes", ""),
        "is_frozen_exe": bool(getattr(sys, "frozen", False)),
        "installer_url": f"https://github.com/{repo}/releases/latest/download/Petlebi-KolayIK-DIA-Setup.exe",
        "release_page_url": f"https://github.com/{repo}/releases/latest",
        "message": (
            f"Yeni sürüm mevcut: v{remote_ver}"
            if has_update
            else f"Uygulamanız güncel (v{local_ver})."
        ),
    }


def apply_hot_update() -> Dict[str, Any]:
    """
    K7 Korumalı Güncelleme:
    - Yalnızca resmi TRUSTED_GITHUB_REPO deposundan güncelleme kabul eder.
    - Eğer uygulama derlenmiş taşınabilir/kurulum .exe (frozen) olarak çalışıyorsa,
      yalnızca arayüzün güncellenip arka ucun eski kalmasını önlemek için resmi Setup.exe indirme bağlantısını döndürür.
    - Kaynak kod modunda çalışıyorsa .env ve data/local_overrides.json verilerini koruyarak günceller.
    """
    repo = settings.TRUSTED_GITHUB_REPO
    status = check_for_updates()

    if getattr(sys, "frozen", False):
        return {
            "status": "INSTALLER_REQUIRED",
            "version": status.get("remote_version"),
            "installer_url": f"https://github.com/{repo}/releases/latest/download/Petlebi-KolayIK-DIA-Setup.exe",
            "message": (
                "Derlenmiş masaüstü (.exe) sürümünde arka uç motorunun da tam güncellenmesi için "
                "lütfen yeni Setup.exe kurulum dosyasını indirip çalıştırın."
            ),
        }

    if (ROOT_DIR / ".git").exists():
        try:
            subprocess.check_call(["git", "pull", "--ff-only"], cwd=str(ROOT_DIR), timeout=20)
            new_info = get_local_version_info()
            return {
                "status": "SUCCESS",
                "version": new_info.get("version"),
                "message": f"Git üzerinden v{new_info.get('version')} sürümüne başarıyla güncellendi.",
            }
        except Exception:
            pass

    zip_url = f"https://github.com/{repo}/archive/refs/heads/main.zip"
    req = urllib.request.Request(zip_url, headers={"User-Agent": "Petlebi-Updater/1.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        zip_bytes = resp.read()

    root_resolved = ROOT_DIR.resolve()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if not names:
            raise RuntimeError("İndirilen güncelleme paketi boş.")
        top_prefix = names[0].split("/")[0] + "/"

        for member in names:
            if not member.startswith(top_prefix):
                continue
            rel_path = member[len(top_prefix) :]
            if not rel_path or rel_path.endswith("/") or ".." in rel_path:
                continue
            if rel_path in (".env", "data/local_overrides.json"):
                continue
            if rel_path.startswith("src/") or rel_path in ("version.json", "desktop_launcher.py"):
                target_file = (ROOT_DIR / rel_path).resolve()
                if not str(target_file).startswith(str(root_resolved)):
                    continue
                target_file.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src_f, open(target_file, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)

    new_info = get_local_version_info()
    return {
        "status": "SUCCESS",
        "version": new_info.get("version"),
        "message": f"Uygulama v{new_info.get('version')} sürümüne güncellendi. Değişikliklerin tam yansıması için uygulamayı yeniden başlatabilirsiniz.",
    }
