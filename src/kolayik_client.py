"""Kolay İK Public API (v2) İstemcisi — Petlebi."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .config import settings


class KolayIKApiError(RuntimeError):
    """Kolay İK API hatası."""

    def __init__(self, endpoint: str, status_code: int, message: str, raw: Any = None):
        super().__init__(f"Kolay İK Hatası [{endpoint}] (HTTP {status_code}): {message}")
        self.endpoint = endpoint
        self.status_code = status_code
        self.message = message
        self.raw = raw


class KolayIKClient:
    """Kolay İK v2 REST API İstemcisi."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or settings.KOLAYIK_BASE_URL).rstrip("/")
        self.api_token = (api_token or settings.KOLAYIK_API_TOKEN).strip()

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        if not self.api_token or self.api_token.startswith("BURAYA_"):
            raise KolayIKApiError(path, 401, "KOLAYIK_API_TOKEN tanımlı değil.")

        url = f"{self.base_url}{path}"
        if query:
            qs = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
            url = f"{url}?{qs}"

        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("error") is True:
                    raise KolayIKApiError(path, resp.status, str(data.get("message", "API hatası")), data)
                return data
        except urllib.error.HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")[:500]
            try:
                err_json = json.loads(err_text)
                msg = err_json.get("message") or str(err_json.get("errors") or err_text)
            except Exception:
                msg = err_text
            raise KolayIKApiError(path, exc.code, msg) from exc
        except urllib.error.URLError as exc:
            raise KolayIKApiError(path, 503, f"Bağlantı hatası: {exc}") from exc

    # --- Personel (Özlük) ---
    def list_person_summaries(self, status: int = 1) -> List[Dict[str, Any]]:
        """Aktif (status=1) veya işten ayrılmış (status=0) personel özet listesini sayfalı çeker."""
        all_items: List[Dict[str, Any]] = []
        page = 1
        while True:
            res = self._request("POST", "/v2/person/list", body={"status": status, "page": page})
            data = res.get("data", {}) if isinstance(res, dict) else {}
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                break
            all_items.extend(items)
            last_page = data.get("lastPage") or 1
            if page >= int(last_page):
                break
            page += 1
        return all_items

    def bulk_view_persons(self, person_ids: List[str], chunk_size: int = 40) -> List[Dict[str, Any]]:
        """Personel ID listesini bulk-view ile detaylı olarak çeker."""
        detailed: List[Dict[str, Any]] = []
        for i in range(0, len(person_ids), chunk_size):
            batch = person_ids[i : i + chunk_size]
            if not batch:
                continue
            res = self._request("POST", "/v2/person/bulk-view", body={"person_ids": batch})
            persons = res.get("data", {}).get("persons", [])
            if isinstance(persons, list):
                detailed.extend(persons)
        return detailed

    def list_all_persons_detailed(self, include_passive: bool = True) -> List[Dict[str, Any]]:
        """Tüm aktif (ve isteğe bağlı pasif) personellerin tam özlük detaylarını döndürür."""
        active_summaries = self.list_person_summaries(status=1)
        passive_summaries = self.list_person_summaries(status=0) if include_passive else []

        active_ids = [str(p["id"]) for p in active_summaries if p.get("id")]
        passive_ids = [str(p["id"]) for p in passive_summaries if p.get("id")]

        all_ids = active_ids + [pid for pid in passive_ids if pid not in set(active_ids)]
        return self.bulk_view_persons(all_ids)

    # --- PDKS / Fazla Mesai (Timelog) ---
    def list_timelogs(
        self,
        start_date: str,
        end_date: str,
        status: Optional[str] = "approved",
        type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Belirtilen tarih aralığındaki fazla mesai / PDKS timelog kayıtlarını çeker."""
        start_dt = f"{start_date[:10]} 00:00:00"
        end_dt = f"{end_date[:10]} 23:59:59"

        all_items: List[Dict[str, Any]] = []
        page = 1
        while True:
            body: Dict[str, Any] = {
                "startDate": start_dt,
                "endDate": end_dt,
                "page": page,
                "limit": 100,
            }
            if status:
                body["status"] = status
            if type_filter:
                body["type"] = type_filter

            res = self._request("POST", "/v2/timelog/list", body=body)
            data = res.get("data", {}) if isinstance(res, dict) else {}
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                break
            all_items.extend(items)
            last_page = data.get("lastPage") or 1
            if page >= int(last_page):
                break
            page += 1

        if status:
            all_items = [x for x in all_items if str(x.get("status", "")).lower() == status.lower()]
        if type_filter:
            all_items = [x for x in all_items if str(x.get("type", "")).lower() == type_filter.lower()]
        return all_items

    # --- İzin ve Raporlar (Leave) ---
    def list_leaves(
        self,
        start_date: str,
        end_date: str,
        status: str = "approved",
    ) -> List[Dict[str, Any]]:
        """Belirtilen tarih aralığındaki izin ve raporları çeker."""
        start_dt = f"{start_date[:10]} 00:00:00"
        end_dt = f"{end_date[:10]} 23:59:59"

        all_leaves: List[Dict[str, Any]] = []
        page = 1
        while True:
            res = self._request(
                "GET",
                "/v2/leave/list",
                query={
                    "status": status,
                    "startDate": start_dt,
                    "endDate": end_dt,
                    "limit": 100,
                    "page": page,
                },
            )
            data = res.get("data", [])
            if isinstance(data, dict):
                items = data.get("items", [])
            elif isinstance(data, list):
                items = data
            else:
                items = []

            if not items:
                break
            all_leaves.extend(items)
            if isinstance(data, dict) and data.get("lastPage"):
                if page >= int(data["lastPage"]):
                    break
            elif len(items) < 100:
                break
            page += 1

        return all_leaves

    # --- Avans / Masraf / Prim / Kesinti (Transaction) ---
    def list_transactions(
        self,
        start_date: str,
        end_date: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Belirtilen tarih aralığındaki ek ödeme, avans, masraf ve kesintileri çeker."""
        s_date = start_date[:10]
        e_date = end_date[:10]

        all_items: List[Dict[str, Any]] = []
        page = 1
        while True:
            res = self._request(
                "POST",
                "/v2/transaction/list",
                body={
                    "startDate": s_date,
                    "endDate": e_date,
                    "page": page,
                    "limit": 100,
                },
            )
            data = res.get("data", {}) if isinstance(res, dict) else {}
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                break
            all_items.extend(items)
            last_page = data.get("lastPage") or 1
            if page >= int(last_page):
                break
            page += 1

        if status:
            all_items = [x for x in all_items if str(x.get("status", "")).lower() == status.lower()]
        return all_items

    # --- Çarşaf Bordro (Payroll) ---
    def list_payrolls(self, year: int) -> List[Dict[str, Any]]:
        res = self._request("GET", "/v2/payroll/list", query={"year": year})
        data = res.get("data", [])
        return data if isinstance(data, list) else []

    def view_payroll_sheet(self, payroll_id: str, extra_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        res = self._request("POST", f"/v2/payroll-sheet/view/{payroll_id}", body=extra_body or {})
        return res.get("data", {}) if isinstance(res, dict) else {}
