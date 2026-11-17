"""DİA Cloud ERP Web Servis (v3) İstemcisi."""

from __future__ import annotations

import datetime
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import ROOT_DIR, settings

AUDIT_LOG_PATH = ROOT_DIR / "data" / "dia_audit.jsonl"
WRITE_SERVICES = {
    "per_personel_ekle",
    "per_personel_guncelle",
    "per_personel_sil",
    "per_izinler_ekle",
    "per_izinler_sil",
    "per_ekodemekesinti_ekle",
    "per_ekodemekesinti_sil",
    "per_personel_puantaj_ekle",
    "per_personel_puantaj_guncelle",
    "per_personel_puantaj_sil",
    "muh_muhasebefisi_ekle",
    "muh_muhasebefisi_sil",
}


class DiaApiError(RuntimeError):
    """DİA API iş veya doğrulama hatası."""

    def __init__(self, service: str, code: str, message: str, raw: Optional[Dict[str, Any]] = None):
        super().__init__(f"DİA Hatası [{service}] (Kod: {code}): {message}")
        self.service = service
        self.code = str(code)
        self.message = message
        self.raw = raw or {}


class DiaClient:
    """DİA v3 JSON-RPC İstemcisi (Oturum önbellekli, sayfalamalı ve denetim izi kayıtlı)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        apikey: Optional[str] = None,
        firma_kodu: Optional[int] = None,
        donem_kodu: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or settings.DIA_BASE_URL).rstrip("/")
        self.username = username or settings.DIA_USERNAME
        self.password = password or settings.DIA_PASSWORD
        self.apikey = apikey or settings.DIA_APIKEY
        self.firma_kodu = firma_kodu if firma_kodu is not None else settings.DIA_FIRMA_KODU
        self.donem_kodu = donem_kodu if donem_kodu is not None else settings.DIA_DONEM_KODU

        self._session_id: Optional[str] = None
        self._session_ts: float = 0.0
        self._lock = threading.Lock()
        self._ian_cache: Optional[List[Dict[str, Any]]] = None

    def _write_audit_log(
        self,
        module: str,
        service: str,
        status: str,
        params: Dict[str, Any],
        response_or_error: Any,
    ) -> None:
        if service not in WRITE_SERVICES:
            return
        try:
            AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            kart = params.get("kart") if isinstance(params.get("kart"), dict) else {}
            entry = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "module": module,
                "service": service,
                "firma_kodu": self.firma_kodu,
                "donem_kodu": self.donem_kodu,
                "status": status,
                "target_key": kart.get("_key") or params.get("key") or kart.get("_key_per_personel"),
                "sicilno": kart.get("sicilno") or kart.get("fisno") or "",
                "detail": str(response_or_error)[:500],
            }
            with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _post_raw(self, module: str, body: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
        url = f"{self.base_url}/{module}/json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
                return json.loads(raw_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
            raise DiaApiError(module, str(exc.code), f"HTTP {exc.code}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise DiaApiError(module, "NETWORK_ERROR", f"Bağlantı hatası: {exc}") from exc

    def login(self) -> str:
        """DİA sis/json üzerinden oturum açar ve session_id döndürür."""
        with self._lock:
            disconnect_flag = str(settings.DIA_DISCONNECT_SAME_USER or "False")
            res = self._post_raw(
                "sis",
                {
                    "login": {
                        "username": self.username,
                        "password": self.password,
                        "disconnect_same_user": disconnect_flag,
                        "params": {"apikey": self.apikey},
                    }
                },
            )
            code = str(res.get("code", ""))
            msg = str(res.get("msg", ""))
            # Eğer aynı kullanıcı açık olduğu için oturum reddedildiyse ve disconnect_flag False ise,
            # DİA WS oturumunu devralmak için True ile bir kez daha dene
            if code != "200" and disconnect_flag.lower() == "false" and (
                "SAME_USER" in msg.upper() or "AKTIF" in msg.upper() or "OTURUM" in msg.upper()
            ):
                res = self._post_raw(
                    "sis",
                    {
                        "login": {
                            "username": self.username,
                            "password": self.password,
                            "disconnect_same_user": "True",
                            "params": {"apikey": self.apikey},
                        }
                    },
                )
                code = str(res.get("code", ""))

            if code != "200" or not res.get("msg"):
                raise DiaApiError("login", code or "AUTH", str(res.get("msg", "Oturum açılamadı")), res)
            self._session_id = str(res["msg"])
            self._session_ts = time.time()
            return self._session_id

    def _ensure_session(self) -> str:
        # Oturum süresi 1 saattir; 45 dakikada bir proaktif yenile
        if not self._session_id or (time.time() - self._session_ts > 2700):
            return self.login()
        return self._session_id

    def call_service(
        self,
        module: str,
        service: str,
        params: Optional[Dict[str, Any]] = None,
        include_company_period: bool = True,
        timeout: int = 30,
    ) -> Any:
        """DİA servisini çağırır. Parametre immutability, hata yutma koruması ve denetim izi içerir."""
        req_params: Dict[str, Any] = dict(params) if params else {}
        session_id = self._ensure_session()

        def _build_body(sid: str) -> Dict[str, Any]:
            inner: Dict[str, Any] = {"session_id": sid}
            if include_company_period:
                inner["firma_kodu"] = req_params.get("firma_kodu", self.firma_kodu)
                inner["donem_kodu"] = req_params.get("donem_kodu", self.donem_kodu)
            for k, v in req_params.items():
                if k not in ("session_id",):
                    inner[k] = v
            return {service: inner}

        try:
            res = self._post_raw(module, _build_body(session_id), timeout=timeout)
            msg_str = str(res.get("msg", ""))
            if "SESSION" in msg_str.upper() or "OTURUM" in msg_str.upper():
                session_id = self.login()
                res = self._post_raw(module, _build_body(session_id), timeout=timeout)
                msg_str = str(res.get("msg", ""))

            if res.get("faultcode"):
                err = DiaApiError(
                    service,
                    str(res.get("faultcode")),
                    str(res.get("faultstring", "Doğrulama hatası")),
                    res,
                )
                self._write_audit_log(module, service, "ERROR", req_params, str(err))
                raise err

            code = str(res.get("code", ""))
            if code != "200":
                err = DiaApiError(service, code or "ERROR", msg_str or "Bilinmeyen DİA hatası", res)
                self._write_audit_log(module, service, "ERROR", req_params, str(err))
                raise err

            self._session_ts = time.time()
            out = res.get("result") if "result" in res else res
            self._write_audit_log(module, service, "SUCCESS", req_params, out)
            return out
        except DiaApiError:
            raise
        except Exception as exc:
            self._write_audit_log(module, service, "ERROR", req_params, str(exc))
            raise

    def list_records(
        self,
        module: str,
        service: str,
        filters: Any = "",
        sorts: Any = "",
        extra_params: Any = "",
        limit: int = 500,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Standart _listele servislerini çağırır ve her zaman liste döndürür."""
        res = self.call_service(
            module,
            service,
            {
                "filters": filters if filters else "",
                "sorts": sorts if sorts else "",
                "params": extra_params if extra_params else "",
                "limit": limit,
                "offset": offset,
            },
        )
        if isinstance(res, list):
            return res
        return []

    def list_all_records(
        self,
        module: str,
        service: str,
        filters: Any = "",
        sorts: Any = "",
        extra_params: Any = "",
        page_size: int = 500,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """Tüm sayfaları (offset/limit) otomatik tarayarak kesintisiz tam liste döndürür."""
        all_rows: List[Dict[str, Any]] = []
        for page_idx in range(max_pages):
            offset = page_idx * page_size
            batch = self.list_records(
                module=module,
                service=service,
                filters=filters,
                sorts=sorts,
                extra_params=extra_params,
                limit=page_size,
                offset=offset,
            )
            if not batch:
                break
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
        return all_rows

    # --- SIS (Sistem) ---
    def get_credit(self) -> float:
        res = self.call_service("sis", "sis_kontor_sorgula", {}, include_company_period=False)
        if isinstance(res, dict):
            return float(res.get("kontorsayisi", 0.0))
        return 0.0

    # --- PER (Personel Özlük) ---
    def list_employees(self) -> List[Dict[str, Any]]:
        return self.list_all_records(
            "per",
            "per_personel_listele",
            sorts=[{"field": "sicilno", "sorttype": "ASC"}],
            page_size=500,
        )

    def get_employee(self, key: int) -> Dict[str, Any]:
        res = self.call_service("per", "per_personel_getir", {"key": int(key)})
        return res if isinstance(res, dict) else {}

    def add_employee(self, kart: Dict[str, Any]) -> Dict[str, Any]:
        payload_kart = dict(kart)
        res = self.call_service("per", "per_personel_ekle", {"kart": payload_kart})
        return res if isinstance(res, dict) else {"result": res}

    def update_employee(self, kart: Dict[str, Any]) -> Dict[str, Any]:
        payload_kart = dict(kart)
        res = self.call_service("per", "per_personel_guncelle", {"kart": payload_kart})
        return res if isinstance(res, dict) else {"result": res}

    def delete_employee(self, key: Any) -> Dict[str, Any]:
        res = self.call_service("per", "per_personel_sil", {"key": str(key), "params": ""})
        return res if isinstance(res, dict) else {"result": res}

    def list_termination_reasons(self) -> List[Dict[str, Any]]:
        if self._ian_cache is None:
            self._ian_cache = self.list_all_records("per", "per_istenayrilmanedenleri_listele", page_size=200)
        return self._ian_cache

    # --- PER (İzinler) ---
    def list_leave_cards(self) -> List[Dict[str, Any]]:
        return self.list_all_records("per", "per_izinkarti_listele", page_size=100)

    def list_leaves(self, filters: Any = "") -> List[Dict[str, Any]]:
        return self.list_all_records("per", "per_izinler_listele", filters=filters, page_size=500)

    def add_leave(self, kart: Dict[str, Any]) -> Dict[str, Any]:
        payload_kart = dict(kart)
        res = self.call_service("per", "per_izinler_ekle", {"kart": payload_kart})
        return res if isinstance(res, dict) else {"result": res}

    def delete_leave(self, key: Any) -> Dict[str, Any]:
        res = self.call_service("per", "per_izinler_sil", {"key": str(key), "params": ""})
        return res if isinstance(res, dict) else {"result": res}

    # --- PER (Ek Kazanç / Kesinti / Sosyal Yardım) ---
    def list_extra_earning_cards(self) -> List[Dict[str, Any]]:
        return self.list_all_records("per", "per_ekkazanc_listele", page_size=100)

    def list_social_aid_cards(self) -> List[Dict[str, Any]]:
        return self.list_all_records("per", "per_sosyalyardim_listele", page_size=100)

    def list_extra_payment_slips(self, filters: Any = "") -> List[Dict[str, Any]]:
        return self.list_all_records("per", "per_ekodemekesinti_listele", filters=filters, page_size=500)

    def add_extra_payment_slip(self, kart: Dict[str, Any]) -> Dict[str, Any]:
        payload_kart = dict(kart)
        res = self.call_service("per", "per_ekodemekesinti_ekle", {"kart": payload_kart})
        return res if isinstance(res, dict) else {"result": res}

    def delete_extra_payment_slip(self, key: Any) -> Dict[str, Any]:
        res = self.call_service("per", "per_ekodemekesinti_sil", {"key": str(key), "params": ""})
        return res if isinstance(res, dict) else {"result": res}

    # --- PER (Puantaj / Bordro) ---
    def list_timesheets(self, year: Optional[int] = None, month: Optional[int] = None) -> List[Dict[str, Any]]:
        filters = []
        if year is not None:
            filters.append({"field": "duzenlemeyil", "operator": "=", "value": str(year)})
        if month is not None:
            filters.append({"field": "duzenlemeayno", "operator": "=", "value": str(month)})
        return self.list_all_records(
            "per",
            "per_personel_puantaj_listele",
            filters=filters or "",
            sorts=[{"field": "_key", "sorttype": "DESC"}],
            page_size=500,
        )

    def get_timesheet(self, key: int) -> Dict[str, Any]:
        res = self.call_service("per", "per_personel_puantaj_getir", {"key": int(key)})
        return res if isinstance(res, dict) else {}

    def add_timesheet(self, kart: Dict[str, Any]) -> Dict[str, Any]:
        payload_kart = dict(kart)
        res = self.call_service("per", "per_personel_puantaj_ekle", {"kart": payload_kart})
        return res if isinstance(res, dict) else {"result": res}

    def update_timesheet(self, kart: Dict[str, Any]) -> Dict[str, Any]:
        payload_kart = dict(kart)
        res = self.call_service("per", "per_personel_puantaj_guncelle", {"kart": payload_kart})
        return res if isinstance(res, dict) else {"result": res}

    def delete_timesheet(self, key: Any) -> Dict[str, Any]:
        res = self.call_service("per", "per_personel_puantaj_sil", {"key": str(key), "params": ""})
        return res if isinstance(res, dict) else {"result": res}

    # --- MUH (Muhasebe Mahsup Fişi) ---
    def list_accounts(self, prefix: str = "") -> List[Dict[str, Any]]:
        filters = [{"field": "hesapkodu", "operator": "", "value": prefix}] if prefix else ""
        return self.list_all_records("muh", "muh_hesapkarti_listele", filters=filters, page_size=500)

    def list_accounting_vouchers(self, fisno: Optional[str] = None) -> List[Dict[str, Any]]:
        filters = [{"field": "fisno", "operator": "=", "value": str(fisno)}] if fisno else ""
        return self.list_all_records("muh", "muh_muhasebefisi_listele", filters=filters, page_size=200)

    def add_accounting_voucher(self, kart: Dict[str, Any]) -> Dict[str, Any]:
        payload_kart = dict(kart)
        res = self.call_service("muh", "muh_muhasebefisi_ekle", {"kart": payload_kart})
        return res if isinstance(res, dict) else {"result": res}

    def delete_accounting_voucher(self, key: Any) -> Dict[str, Any]:
        res = self.call_service("muh", "muh_muhasebefisi_sil", {"key": str(key), "params": ""})
        return res if isinstance(res, dict) else {"result": res}

    def get_next_number(self, tablo_kodu: str, ekran_kodu: str) -> Optional[str]:
        """DİA sis_numara_getir ile sıradaki belge/sicil numarasını sorgular."""
        try:
            res = self.call_service(
                "sis",
                "sis_numara_getir",
                {"params": {"tablokodu": tablo_kodu, "ekrankodu": ekran_kodu}},
            )
            if isinstance(res, dict) and res.get("numara"):
                return str(res["numara"])
        except Exception:
            pass
        return None
