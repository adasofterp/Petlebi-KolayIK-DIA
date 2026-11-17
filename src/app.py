"""FastAPI Backend — Petlebi Kolay İK <-> DİA ERP Entegrasyon Kontrol Paneli."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT_DIR, settings
from .sync_engine import SyncEngine
from .updater import apply_hot_update, check_for_updates, get_local_version_info

STATIC_DIR = Path(__file__).resolve().parent / "static"
APP_CSRF_TOKEN = secrets.token_hex(32)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Petlebi Kolay İK (v2) <-> DİA Cloud ERP (v3) Kontrollü Veri Eşleştirme ve Aktarım Uygulaması",
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

engine = SyncEngine()


def _is_trusted_local_origin(origin_or_ref: str) -> bool:
    if not origin_or_ref:
        return True
    try:
        parsed = urlparse(origin_or_ref)
        return parsed.hostname in ("127.0.0.1", "localhost", "testserver")
    except Exception:
        return False


@app.middleware("http")
async def localhost_csrf_guard(request: Request, call_next: Any) -> Any:
    """
    K7 Güvenlik Zinciri Koruması:
    Tarayıcıda açık olan yabancı web sitelerinden 127.0.0.1:8765'e yapılabilecek
    Cross-Site Request Forgery (CSRF) ve DNS Rebinding saldırılarını engeller.
    """
    host_hdr = request.headers.get("host", "").split(":")[0].lower()
    if host_hdr and host_hdr not in ("127.0.0.1", "localhost", "testserver"):
        return JSONResponse(status_code=403, content={"detail": "Geçersiz Host başlığı reddedildi."})

    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin", "")
        referer = request.headers.get("referer", "")
        sec_fetch_site = request.headers.get("sec-fetch-site", "").lower()

        if sec_fetch_site == "cross-site":
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-site (dış kaynaklı) istek güvenlik politikası gereği engellendi."},
            )
        if origin and not _is_trusted_local_origin(origin):
            return JSONResponse(
                status_code=403,
                content={"detail": f"Güvenilmeyen Origin ({origin}) engellendi."},
            )
        if referer and not _is_trusted_local_origin(referer):
            return JSONResponse(
                status_code=403,
                content={"detail": f"Güvenilmeyen Referer ({referer}) engellendi."},
            )

        # Tarayıcıdan gelen isteklerde (Origin veya Sec-Fetch-Site varsa) CSRF token kontrolü
        if origin or sec_fetch_site:
            req_token = request.headers.get("x-csrf-token", "")
            if req_token != APP_CSRF_TOKEN:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF güvenlik anahtarı doğrulanamadı. Lütfen sayfayı yenileyin."},
                )

    return await call_next(request)


# --- İstek Şemaları ---
class PeriodStageRequest(BaseModel):
    start_date: str
    end_date: str


class RowsPushRequest(BaseModel):
    rows: List[Dict[str, Any]]


class TransactionsPushRequest(BaseModel):
    rows: List[Dict[str, Any]]
    mode: str = "mahsup"
    fis_tarihi: Optional[str] = None


class MahsupPushRequest(BaseModel):
    tarih: str
    fisno: str
    aciklama: str
    lines: List[Dict[str, Any]]


class SettingsUpdateRequest(BaseModel):
    kolayik_api_token: Optional[str] = None
    dia_base_url: Optional[str] = None
    dia_username: Optional[str] = None
    dia_password: Optional[str] = None
    dia_apikey: Optional[str] = None
    dia_firma_kodu: Optional[int] = None
    dia_donem_kodu: Optional[int] = None
    dia_sube_key: Optional[int] = None
    github_repo: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
def index() -> Any:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h3>Arayüz dosyası bulunamadı.</h3>", status_code=404)
    html_text = index_file.read_text(encoding="utf-8")
    csrf_script = f'<script>window.__CSRF_TOKEN__ = "{APP_CSRF_TOKEN}";</script>'
    if "</head>" in html_text:
        html_text = html_text.replace("</head>", f"  {csrf_script}\n</head>", 1)
    else:
        html_text = csrf_script + html_text
    return HTMLResponse(html_text)


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    ver_info = get_local_version_info()
    dia_ok = False
    dia_credit = 0.0
    dia_error = ""
    try:
        dia_credit = engine.dia.get_credit()
        dia_ok = True
    except Exception as exc:
        dia_error = str(exc)

    kolay_ok = False
    kolay_error = ""
    try:
        engine.kolay.list_person_summaries(status=1)
        kolay_ok = True
    except Exception as exc:
        kolay_error = str(exc)

    return {
        "app_name": settings.APP_NAME,
        "version": ver_info.get("version", settings.APP_VERSION),
        "released_at": ver_info.get("released_at", ""),
        "dia": {
            "connected": dia_ok,
            "credit": dia_credit,
            "firma_kodu": engine.dia.firma_kodu,
            "donem_kodu": engine.dia.donem_kodu,
            "sube_key": settings.DIA_MERKEZ_SUBE_KEY,
            "error": dia_error,
        },
        "kolayik": {
            "connected": kolay_ok,
            "base_url": engine.kolay.base_url,
            "error": kolay_error,
        },
        "accounts": settings.DIA_MUH_HESAPLAR,
    }


@app.get("/api/employees/stage")
def stage_employees(force: bool = True) -> Dict[str, Any]:
    try:
        return engine.stage_employees(force_refresh=force)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/employees/push")
def push_employees(req: RowsPushRequest) -> Dict[str, Any]:
    if not req.rows:
        raise HTTPException(status_code=400, detail="Aktarılacak personel seçilmedi.")
    try:
        return engine.push_selected_employees(req.rows)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/period/stage")
def stage_period(req: PeriodStageRequest) -> Dict[str, Any]:
    try:
        return engine.stage_period_data(req.start_date, req.end_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/leaves/push")
def push_leaves(req: RowsPushRequest) -> Dict[str, Any]:
    if not req.rows:
        raise HTTPException(status_code=400, detail="Aktarılacak izin kaydı seçilmedi.")
    try:
        return engine.push_selected_leaves(req.rows)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/transactions/push")
def push_transactions(req: TransactionsPushRequest) -> Dict[str, Any]:
    if not req.rows:
        raise HTTPException(status_code=400, detail="Aktarılacak avans/ödeme hareketi seçilmedi.")
    try:
        return engine.push_selected_transactions(req.rows, mode=req.mode, fis_tarihi=req.fis_tarihi)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/timesheets/push")
def push_timesheets(req: RowsPushRequest) -> Dict[str, Any]:
    if not req.rows:
        raise HTTPException(status_code=400, detail="Aktarılacak puantaj satırı seçilmedi.")
    try:
        return engine.push_selected_timesheets(req.rows)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/mahsup/push")
def push_mahsup(req: MahsupPushRequest) -> Dict[str, Any]:
    if not req.lines:
        raise HTTPException(status_code=400, detail="Mahsup fişi kalemi bulunamadı.")
    try:
        return engine.push_mahsup_voucher(
            tarih=req.tarih,
            fisno=req.fisno,
            aciklama=req.aciklama,
            lines=req.lines,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/update/check")
def api_check_update() -> Dict[str, Any]:
    return check_for_updates()


@app.post("/api/update/apply")
def api_apply_update() -> Dict[str, Any]:
    try:
        return apply_hot_update()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    tok = settings.KOLAYIK_API_TOKEN
    masked_tok = (tok[:6] + "..." + tok[-4:]) if len(tok) > 12 else ("Tanımlı" if tok else "Boş")
    ak = settings.DIA_APIKEY
    masked_ak = (ak[:6] + "..." + ak[-4:]) if len(ak) > 12 else ("Tanımlı" if ak else "Boş")
    return {
        "kolayik_base_url": settings.KOLAYIK_BASE_URL,
        "kolayik_token_masked": masked_tok,
        "dia_base_url": settings.DIA_BASE_URL,
        "dia_username": settings.DIA_USERNAME,
        "dia_apikey_masked": masked_ak,
        "dia_firma_kodu": settings.DIA_FIRMA_KODU,
        "dia_donem_kodu": settings.DIA_DONEM_KODU,
        "dia_sube_key": settings.DIA_MERKEZ_SUBE_KEY,
        "dia_doviz_key": settings.DIA_TL_DOVIZ_KEY,
        "github_repo": settings.TRUSTED_GITHUB_REPO,
    }


@app.post("/api/settings")
def update_settings(req: SettingsUpdateRequest) -> Dict[str, Any]:
    # K7 Koruması: Güncelleme deposunun yabancı bir depoya yönlendirilmesini engelle
    if req.github_repo is not None and req.github_repo.strip() not in ("", settings.TRUSTED_GITHUB_REPO):
        raise HTTPException(
            status_code=400,
            detail=f"Güvenlik politikası gereği güncelleme deposu yalnız '{settings.TRUSTED_GITHUB_REPO}' olabilir.",
        )

    if req.kolayik_api_token and req.kolayik_api_token.strip():
        settings.KOLAYIK_API_TOKEN = req.kolayik_api_token.strip()
        engine.kolay.api_token = settings.KOLAYIK_API_TOKEN
        engine._kolay_persons_cache = None

    if req.dia_base_url and req.dia_base_url.strip():
        settings.DIA_BASE_URL = req.dia_base_url.strip().rstrip("/")
        engine.dia.base_url = settings.DIA_BASE_URL
        engine.dia._session_id = None

    if req.dia_username and req.dia_username.strip():
        settings.DIA_USERNAME = req.dia_username.strip()
        engine.dia.username = settings.DIA_USERNAME
        engine.dia._session_id = None

    if req.dia_password and req.dia_password.strip():
        settings.DIA_PASSWORD = req.dia_password.strip()
        engine.dia.password = settings.DIA_PASSWORD
        engine.dia._session_id = None

    if req.dia_apikey and req.dia_apikey.strip():
        settings.DIA_APIKEY = req.dia_apikey.strip()
        engine.dia.apikey = settings.DIA_APIKEY
        engine.dia._session_id = None

    if req.dia_firma_kodu is not None:
        settings.DIA_FIRMA_KODU = int(req.dia_firma_kodu)
        engine.dia.firma_kodu = settings.DIA_FIRMA_KODU

    if req.dia_donem_kodu is not None:
        settings.DIA_DONEM_KODU = int(req.dia_donem_kodu)
        engine.dia.donem_kodu = settings.DIA_DONEM_KODU

    if req.dia_sube_key is not None:
        settings.DIA_MERKEZ_SUBE_KEY = int(req.dia_sube_key)

    # .env dosyasını özel karakter (#, boşluk, tırnak) korumalı olarak kaydet
    def _q(val: Any) -> str:
        return json.dumps(str(val), ensure_ascii=False)

    env_content = (
        f"# Kolay İK API Ayarları (https://app.kolayik.com/settings/developer-settings)\n"
        f"KOLAYIK_BASE_URL={_q(settings.KOLAYIK_BASE_URL)}\n"
        f"KOLAYIK_API_TOKEN={_q(settings.KOLAYIK_API_TOKEN)}\n\n"
        f"# DİA Cloud ERP Ayarları\n"
        f"DIA_BASE_URL={_q(settings.DIA_BASE_URL)}\n"
        f"DIA_USERNAME={_q(settings.DIA_USERNAME)}\n"
        f"DIA_PASSWORD={_q(settings.DIA_PASSWORD)}\n"
        f"DIA_APIKEY={_q(settings.DIA_APIKEY)}\n"
        f"DIA_FIRMA_KODU={_q(settings.DIA_FIRMA_KODU)}\n"
        f"DIA_DONEM_KODU={_q(settings.DIA_DONEM_KODU)}\n"
        f"DIA_MERKEZ_SUBE_KEY={_q(settings.DIA_MERKEZ_SUBE_KEY)}\n"
        f"DIA_TL_DOVIZ_KEY={_q(settings.DIA_TL_DOVIZ_KEY)}\n"
        f"DIA_DISCONNECT_SAME_USER={_q(settings.DIA_DISCONNECT_SAME_USER)}\n"
        f"GITHUB_REPO={_q(settings.TRUSTED_GITHUB_REPO)}\n"
    )
    (ROOT_DIR / ".env").write_text(env_content, encoding="utf-8")
    return {"status": "SUCCESS", "message": "Ayarlar güvenli biçimde kaydedildi ve istemciler güncellendi."}
