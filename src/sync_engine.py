"""Kolay İK <-> DİA ERP Karşılaştırma (Staging), Fark Analizi ve Kontrollü Aktarım Motoru."""

from __future__ import annotations

import calendar
import datetime
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import ROOT_DIR, settings
from .dia_client import DiaClient
from .kolayik_client import KolayIKClient
from .mappers import (
    build_dia_izin_payload,
    build_dia_mahsup_payload,
    build_dia_personel_payload,
    build_dia_puantaj_payload,
    calculate_employee_month_days,
    classify_leave_for_dia,
    classify_overtime_date,
    extract_kolay_person_fields,
    get_month_holiday_days,
    is_annual_leave_for_dia_module,
    norm_tr,
)

OVERRIDES_PATH = ROOT_DIR / "data" / "local_overrides.json"
OVERRIDES_BAK_PATH = ROOT_DIR / "data" / "local_overrides.bak.json"

STANDARD_SGK_EXIT_REASONS: List[Dict[str, str]] = [
    {"code": "01", "name": "01 - Deneme süreli iş sözleşmesinin işverence feshi"},
    {"code": "02", "name": "02 - Deneme süreli iş sözleşmesinin işçi tarafından feshi"},
    {"code": "03", "name": "03 - Belirsiz süreli iş sözleşmesinin işçi tarafından feshi (İstifa)"},
    {"code": "04", "name": "04 - Belirsiz süreli iş sözleşmesinin işveren tarafından haklı neden bildirilmeden feshi"},
    {"code": "05", "name": "05 - Belirli süreli iş sözleşmesinin sona ermesi"},
    {"code": "08", "name": "08 - Emeklilik (yaşlılık) veya toptan ödeme nedeniyle"},
    {"code": "10", "name": "10 - Ölüm"},
    {"code": "12", "name": "12 - Askerlik"},
    {"code": "13", "name": "13 - Kadın işçinin evlenmesi"},
    {"code": "14", "name": "14 - Emeklilik için yaş dışında diğer şartların tamamlanması"},
    {"code": "17", "name": "17 - İşyerinin kapanması"},
    {"code": "18", "name": "18 - İşin sona ermesi"},
    {"code": "22", "name": "22 - Diğer nedenler"},
    {"code": "23", "name": "23 - İşçi tarafından zorunlu nedenle fesih"},
    {"code": "24", "name": "24 - İşçi tarafından sağlık nedeniyle fesih"},
    {"code": "25", "name": "25 - İşçi tarafından işverenin ahlak ve iyiniyet kurallarına aykırı davranışı nedeniyle fesih"},
    {"code": "27", "name": "27 - İşveren tarafından zorunlu nedenlerle ve tutukluluk nedeniyle fesih"},
    {"code": "28", "name": "28 - İşveren tarafından sağlık nedeni ile fesih"},
    {"code": "29", "name": "29 - İşveren tarafından işçinin ahlak ve iyiniyet kurallarına aykırı davranışı nedeniyle fesih"},
]


class SyncEngine:
    """
    On-Demand (Çağır -> İncele/Düzenle -> Seçilenleri DİA'ya Aktar) iş akışı motoru.
    Otomatik arka plan aktarımı yapmaz; yalnız kullanıcı tetiklediğinde çalışır.
    """

    def __init__(
        self,
        dia_client: Optional[DiaClient] = None,
        kolay_client: Optional[KolayIKClient] = None,
    ) -> None:
        self.dia = dia_client or DiaClient()
        self.kolay = kolay_client or KolayIKClient()
        self._kolay_persons_cache: Optional[List[Dict[str, Any]]] = None
        self._kolay_persons_ts: float = 0.0

    # --- Yerel Kullanıcı Tercihleri (Atomik Kayıt ve Bozulma Korumalı) ---
    def load_overrides(self) -> Dict[str, Dict[str, Any]]:
        if not OVERRIDES_PATH.exists():
            return {}
        try:
            data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            return {}
        except Exception as exc:
            # Bozuk dosyayı asla sessizce sıfırlama; yedeğe al ve varsa .bak dosyasından geri yükle
            corrupt_path = ROOT_DIR / "data" / f"local_overrides.corrupt.{int(time.time())}.json"
            try:
                shutil.copyfile(OVERRIDES_PATH, corrupt_path)
            except Exception:
                pass
            if OVERRIDES_BAK_PATH.exists():
                try:
                    bak_data = json.loads(OVERRIDES_BAK_PATH.read_text(encoding="utf-8"))
                    if isinstance(bak_data, dict):
                        return bak_data
                except Exception:
                    pass
            raise RuntimeError(
                f"Yerel tercih dosyası (data/local_overrides.json) okunamadı ({exc}). "
                f"Veri kaybını önlemek için bozuk dosya '{corrupt_path.name}' olarak yedeklendi."
            ) from exc

    def save_overrides(self, overrides: Dict[str, Dict[str, Any]]) -> None:
        OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(overrides, ensure_ascii=False, indent=2)
        tmp_path = ROOT_DIR / "data" / "local_overrides.tmp.json"
        tmp_path.write_text(payload, encoding="utf-8")
        if OVERRIDES_PATH.exists():
            try:
                shutil.copyfile(OVERRIDES_PATH, OVERRIDES_BAK_PATH)
            except Exception:
                pass
        tmp_path.replace(OVERRIDES_PATH)

    def _get_ian_maps(self) -> Tuple[Dict[str, int], Dict[str, int], Dict[int, str]]:
        """
        DİA işten ayrılma nedenlerinden:
        - sgk_map (turu='S'): kod -> _key
        - iskur_map (turu='I'): kod -> _key
        - sgk_key_to_code: _key -> kod
        haritalarını döndürür.
        """
        sgk_map: Dict[str, int] = {}
        iskur_map: Dict[str, int] = {}
        sgk_key_to_code: Dict[int, str] = {}
        try:
            reasons = self.dia.list_termination_reasons()
        except Exception:
            reasons = []

        for item in reasons:
            turu = str(item.get("turu") or "").strip().upper()
            code = str(item.get("kodu") or "").strip().zfill(2)
            key = int(item.get("_key") or 0)
            if not code or key <= 0:
                continue
            if turu == "S":
                sgk_map[code] = key
                sgk_key_to_code[key] = code
            elif turu == "I":
                iskur_map[code] = key
        return sgk_map, iskur_map, sgk_key_to_code

    def get_kolay_persons(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        if (
            not force_refresh
            and self._kolay_persons_cache is not None
            and (time.time() - self._kolay_persons_ts < 300)
        ):
            return self._kolay_persons_cache
        raw_persons = self.kolay.list_all_persons_detailed(include_passive=True)
        self._kolay_persons_cache = [extract_kolay_person_fields(p) for p in raw_persons]
        self._kolay_persons_ts = time.time()
        return self._kolay_persons_cache

    def _match_employees(
        self,
        kolay_list: List[Dict[str, Any]],
        dia_list: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
        """
        K3 Çözümü: Kolay İK personellerini DİA personelleriyle adaş çakışmasını önleyerek eşleştirir.
        1. Öncelik: 11 haneli TC Kimlik No (tckimlikno) birebir eşleşmesi.
        2. Öncelik: Normalize Ad Soyad (norm_tr) — YALNIZCA:
           - İki kayıtta birbiriyle çelişen dolu TC Kimlik No yoksa,
           - DİA tarafında bu isimde yalnızca 1 kayıt varsa (adaş yoksa),
           - Eşleşmemiş Kolay İK kayıtları arasında bu isimde yalnızca 1 kişi varsa,
           - İlgili DİA kartı başka bir personelle eşleşmemişse.
        """
        dia_by_tc: Dict[str, Dict[str, Any]] = {}
        dia_by_name_list: Dict[str, List[Dict[str, Any]]] = {}

        for d in dia_list:
            tc = str(d.get("tckimlikno") or "").strip()
            if tc and len(tc) == 11:
                dia_by_tc[tc] = d
            full_name = norm_tr(f"{d.get('adi', '')} {d.get('soyadi', '')}")
            if full_name:
                dia_by_name_list.setdefault(full_name, []).append(d)

        kolay_to_dia: Dict[str, Dict[str, Any]] = {}
        matched_dia_keys: Dict[int, Dict[str, Any]] = {}
        unmatched_kolay: List[Dict[str, Any]] = []

        # 1. Geçiş: TC Kimlik No ile kesin eşleştirme
        for kp in kolay_list:
            kid = kp["kolayik_id"]
            tc = str(kp.get("tckimlikno") or "").strip()
            if tc and tc in dia_by_tc:
                matched = dia_by_tc[tc]
                dkey = int(matched.get("_key") or 0)
                if dkey not in matched_dia_keys:
                    kolay_to_dia[kid] = matched
                    matched_dia_keys[dkey] = kp
                    continue
            unmatched_kolay.append(kp)

        # Eşleşmemiş Kolay İK kayıtlarında isim frekansı (adaş kontrolü)
        unmatched_name_counts: Dict[str, int] = {}
        for kp in unmatched_kolay:
            nk = norm_tr(kp.get("ad_soyad"))
            if nk:
                unmatched_name_counts[nk] = unmatched_name_counts.get(nk, 0) + 1

        # 2. Geçiş: Yalnızca tekil ve TC çelişkisi olmayan isimlerle eşleştirme
        for kp in unmatched_kolay:
            kid = kp["kolayik_id"]
            kp_tc = str(kp.get("tckimlikno") or "").strip()
            name_key = norm_tr(kp.get("ad_soyad"))
            if not name_key or unmatched_name_counts.get(name_key, 0) != 1:
                continue
            candidates = dia_by_name_list.get(name_key) or []
            if len(candidates) != 1:
                continue
            cand = candidates[0]
            dkey = int(cand.get("_key") or 0)
            if dkey in matched_dia_keys:
                continue
            dia_tc = str(cand.get("tckimlikno") or "").strip()
            # İki tarafta da dolu ama farklı TC varsa asla aynı karta bağlama!
            if kp_tc and dia_tc and kp_tc != dia_tc:
                continue
            kolay_to_dia[kid] = cand
            matched_dia_keys[dkey] = kp

        return kolay_to_dia, matched_dia_keys

    # --- 1. Personel Özlük Karşılaştırma (Staging) ---
    def stage_employees(self, force_refresh: bool = True) -> Dict[str, Any]:
        kolay_list = self.get_kolay_persons(force_refresh=force_refresh)
        dia_list = self.dia.list_employees()
        overrides = self.load_overrides()
        _, _, sgk_key_to_code = self._get_ian_maps()
        kolay_to_dia, _ = self._match_employees(kolay_list, dia_list)

        max_sicil = 0
        for d in dia_list:
            s_raw = str(d.get("sicilno") or "").strip()
            if s_raw.isdigit():
                max_sicil = max(max_sicil, int(s_raw))

        next_sicil_counter = max_sicil + 1
        rows: List[Dict[str, Any]] = []

        counts = {
            "total_kolayik": len(kolay_list),
            "total_dia": len(dia_list),
            "new_in_kolayik": 0,
            "needs_update": 0,
            "synced": 0,
            "passive_in_kolayik": sum(1 for x in kolay_list if x.get("durum") == "P" or x.get("istenayrilmatarihi")),
            "exit_diff_count": 0,
            "entry_exit_actionable": 0,
        }

        for kp in sorted(kolay_list, key=lambda x: (0 if x["durum"] == "A" else 1, x["ad_soyad"])):
            kid = kp["kolayik_id"]
            ov = overrides.get(kid) or overrides.get(kp["tckimlikno"]) or {}
            dia_rec = kolay_to_dia.get(kid)

            has_exit_diff = False
            has_entry_diff = False

            if dia_rec:
                dia_key = int(dia_rec.get("_key") or 0)
                sicilno = str(dia_rec.get("sicilno") or "").strip()
                dia_maas = float(dia_rec.get("maas") or 0.0)
                dia_ucretturu = str(dia_rec.get("ucretturu") or "N")
                maas = float(ov.get("maas") if ov.get("maas") is not None else dia_maas)
                ucretturu = str(ov.get("ucretturu") or dia_ucretturu)

                dia_durum = str(dia_rec.get("durum") or "").strip()
                dia_giris = str(dia_rec.get("isebaslamatarihi") or "")[:10]
                dia_cikis = str(dia_rec.get("istenayrilmatarihi") or "")[:10]

                dia_ian_raw = dia_rec.get("_key_per_istenayrilmanedenleri")
                dia_ian_key = (
                    int(dia_ian_raw.get("_key") or 0)
                    if isinstance(dia_ian_raw, dict)
                    else int(dia_ian_raw or 0)
                )
                existing_ian_code = sgk_key_to_code.get(dia_ian_key, "")

                if kp["durum"] == "P" or kp["istenayrilmatarihi"]:
                    if dia_durum != "P" or (kp["istenayrilmatarihi"] and dia_cikis != kp["istenayrilmatarihi"]):
                        has_exit_diff = True

                if (kp["durum"] == "A" and (dia_durum == "P" or dia_cikis)) or (
                    kp["isebaslamatarihi"] and dia_giris != kp["isebaslamatarihi"]
                ):
                    has_entry_diff = True

                diffs: List[Dict[str, str]] = []
                check_pairs = [
                    ("tckimlikno", "TC Kimlik No", str(dia_rec.get("tckimlikno") or "").strip(), kp["tckimlikno"]),
                    ("durum", "Çalışma Durumu", dia_durum, kp["durum"]),
                    ("isebaslamatarihi", "İşe Giriş Tarihi", dia_giris, kp["isebaslamatarihi"]),
                    ("istenayrilmatarihi", "İşten Çıkış Tarihi", dia_cikis, kp["istenayrilmatarihi"]),
                    ("eposta", "E-Posta", str(dia_rec.get("eposta") or "").strip(), kp["eposta"]),
                    ("gorevi", "Görevi / Unvan", str(dia_rec.get("gorevi") or "").strip(), kp["gorevi"]),
                ]
                for f_code, f_label, d_val, k_val in check_pairs:
                    if k_val and d_val != k_val:
                        diffs.append({"field": f_code, "label": f_label, "dia": d_val or "(Boş)", "kolay": k_val})
                    elif f_code == "istenayrilmatarihi" and kp["durum"] == "A" and d_val:
                        diffs.append({"field": f_code, "label": f_label, "dia": d_val, "kolay": "(Aktif Çalışıyor)"})

                if diffs:
                    sync_status = "NEEDS_UPDATE"
                    counts["needs_update"] += 1
                else:
                    sync_status = "SYNCED"
                    counts["synced"] += 1
            else:
                dia_key = None
                sicilno = str(ov.get("sicilno") or f"{next_sicil_counter:08d}")
                next_sicil_counter += 1
                maas = float(ov.get("maas") or 0.0)
                ucretturu = str(ov.get("ucretturu") or "N")
                existing_ian_code = ""
                diffs = []
                sync_status = "NEW_IN_KOLAYIK"
                counts["new_in_kolayik"] += 1
                has_entry_diff = True
                if kp["durum"] == "P" or kp["istenayrilmatarihi"]:
                    has_exit_diff = True

            if has_exit_diff:
                counts["exit_diff_count"] += 1
            if has_exit_diff or has_entry_diff:
                counts["entry_exit_actionable"] += 1

            resolved_ian_kodu = (
                str(kp.get("ian_kodu") or ov.get("ian_kodu") or existing_ian_code or "").strip().zfill(2)
                if (kp.get("ian_kodu") or ov.get("ian_kodu") or existing_ian_code)
                else ""
            )

            row = {
                **kp,
                "dia_key": dia_key,
                "sicilno": sicilno,
                "maas": maas,
                "ucretturu": ucretturu,
                "ian_kodu": resolved_ian_kodu,
                "maliyet_profili": str(ov.get("maliyet_profili") or kp["maliyet_profili"]),
                "_key_sis_sube": settings.DIA_MERKEZ_SUBE_KEY,
                "_key_sis_doviz": settings.DIA_TL_DOVIZ_KEY,
                "sync_status": sync_status,
                "diffs": diffs,
                "has_exit_diff": has_exit_diff,
                "has_entry_diff": has_entry_diff,
                "has_entry_exit_diff": bool(has_exit_diff or has_entry_diff),
            }
            rows.append(row)

        return {
            "summary": counts,
            "sgk_exit_reasons": STANDARD_SGK_EXIT_REASONS,
            "rows": rows,
        }

    # --- 2. Seçilen Personelleri DİA'ya Aktarma ---
    def push_selected_employees(self, selected_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        sgk_ian_map, iskur_ian_map, _ = self._get_ian_maps()
        overrides = self.load_overrides()

        results: List[Dict[str, Any]] = []
        added_count = 0
        updated_count = 0
        error_count = 0

        for row in selected_rows:
            kid = str(row.get("kolayik_id") or row.get("tckimlikno") or "")
            if kid:
                overrides[kid] = {
                    "maas": float(row.get("maas") or 0.0),
                    "ucretturu": str(row.get("ucretturu") or "N"),
                    "maliyet_profili": str(row.get("maliyet_profili") or "GENEL"),
                    "sicilno": str(row.get("sicilno") or ""),
                    "ian_kodu": str(row.get("ian_kodu") or ""),
                }

            try:
                dia_key = row.get("dia_key")
                if dia_key:
                    existing_card = self.dia.get_employee(int(dia_key))
                    kart = build_dia_personel_payload(
                        row,
                        existing_dia_card=existing_card,
                        sgk_ian_key_map=sgk_ian_map,
                        iskur_ian_key_map=iskur_ian_map,
                    )
                    res = self.dia.update_employee(kart)
                    updated_count += 1
                    results.append(
                        {
                            "ad_soyad": f"{row.get('adi', '')} {row.get('soyadi', '')}".strip(),
                            "sicilno": kart["sicilno"],
                            "action": "UPDATED",
                            "status": "SUCCESS",
                            "dia_key": dia_key,
                            "response": res,
                        }
                    )
                else:
                    kart = build_dia_personel_payload(
                        row,
                        existing_dia_card=None,
                        sgk_ian_key_map=sgk_ian_map,
                        iskur_ian_key_map=iskur_ian_map,
                    )
                    res = self.dia.add_employee(kart)
                    new_key = res.get("key") or res.get("_key") or res.get("msg")
                    added_count += 1
                    results.append(
                        {
                            "ad_soyad": f"{row.get('adi', '')} {row.get('soyadi', '')}".strip(),
                            "sicilno": kart["sicilno"],
                            "action": "ADDED",
                            "status": "SUCCESS",
                            "dia_key": new_key,
                            "response": res,
                        }
                    )
            except Exception as exc:
                error_count += 1
                results.append(
                    {
                        "ad_soyad": f"{row.get('adi', '')} {row.get('soyadi', '')}".strip(),
                        "sicilno": str(row.get("sicilno") or ""),
                        "action": "ERROR",
                        "status": "ERROR",
                        "message": str(exc),
                    }
                )

        self.save_overrides(overrides)
        return {
            "added": added_count,
            "updated": updated_count,
            "errors": error_count,
            "results": results,
        }

    # --- 3. Tarih Aralığına Göre PDKS, İzin, Avans, Puantaj ve Mahsup Hazırlama ---
    def stage_period_data(self, start_date: str, end_date: str) -> Dict[str, Any]:
        s_date = start_date[:10]
        e_date = end_date[:10]
        s_dt = datetime.date.fromisoformat(s_date)
        e_dt = datetime.date.fromisoformat(e_date)
        if s_dt > e_dt:
            raise ValueError("Başlangıç tarihi bitiş tarihinden büyük olamaz!")
        if (s_dt.year, s_dt.month) != (e_dt.year, e_dt.month):
            raise ValueError(
                "Aylık puantaj ve maaş tahakkuk hesaplaması için Başlangıç ve Bitiş tarihleri aynı ay içinde olmalıdır!"
            )

        year = e_dt.year
        month = e_dt.month
        last_day = calendar.monthrange(year, month)[1]
        duzenlemetarihi = f"{year:04d}-{month:02d}-{last_day:02d}"

        kolay_list = self.get_kolay_persons(force_refresh=False)
        dia_list = self.dia.list_employees()
        overrides = self.load_overrides()
        kolay_to_dia, _ = self._match_employees(kolay_list, dia_list)
        kolay_by_id = {p["kolayik_id"]: p for p in kolay_list}

        # Kolay İK dönem verilerini çek (Fazla mesai filtreli)
        timelogs = self.kolay.list_timelogs(s_date, e_date, status="approved")
        leaves = self.kolay.list_leaves(s_date, e_date, status="approved")
        transactions = self.kolay.list_transactions(s_date, e_date)

        # DİA dönem verilerini çek
        dia_leaves = self.dia.list_leaves()
        dia_timesheets = self.dia.list_timesheets(year=year, month=month)
        dia_ts_by_emp_key = {int(t.get("_key_per_personel") or 0): t for t in dia_timesheets}

        # 3A. PDKS Fazla Mesai Satırları (Yalnızca fazla mesai kayıtları)
        pdks_rows: List[Dict[str, Any]] = []
        overtime_by_person: Dict[str, Dict[str, float]] = {}

        for tl in timelogs:
            tl_type = str(tl.get("type") or "overtime").strip().lower()
            if tl_type not in ("overtime", "fazla_mesai", "fazlamesai", ""):
                continue
            pid = str(tl.get("personId") or "")
            kp = kolay_by_id.get(pid, {})
            dia_rec = kolay_to_dia.get(pid)
            dia_key = int(dia_rec["_key"]) if dia_rec and dia_rec.get("_key") else None
            used_min = int(tl.get("usedMinute") or 0)
            if used_min <= 0:
                continue
            hours = round(used_min / 60.0, 2)
            start_dt = str(tl.get("startDate") or "")
            target_field = classify_overtime_date(start_dt)

            overtime_by_person.setdefault(
                pid,
                {"fazlamesai1saat": 0.0, "haftasonumesaisisaat": 0.0, "resmitatilmesaisisaat": 0.0},
            )
            overtime_by_person[pid][target_field] = round(overtime_by_person[pid][target_field] + hours, 2)

            pdks_rows.append(
                {
                    "timelog_id": str(tl.get("id") or ""),
                    "kolayik_person_id": pid,
                    "dia_personel_key": dia_key,
                    "sicilno": str(dia_rec.get("sicilno") or "DİA'DA YOK") if dia_rec else "DİA'DA YOK",
                    "ad_soyad": kp.get("ad_soyad") or pid,
                    "departman": kp.get("departman") or "",
                    "baslangic": start_dt.replace("T", " "),
                    "bitis": str(tl.get("endDate") or "").replace("T", " "),
                    "dakika": used_min,
                    "saat": hours,
                    "hedef_dia_alani": target_field,
                    "aciklama": str(tl.get("description") or ""),
                    "status": str(tl.get("status") or "approved"),
                    "in_dia": bool(dia_key),
                }
            )

        # 3B. İzin ve Rapor Satırları (Ay sınırını aşan izinleri döneme kırpar, 0 günü 1 yapmaz)
        existing_dia_leave_keys = {
            (
                int(dl.get("_key_per_personel") or 0),
                str(dl.get("baslangictarihi") or "")[:10],
                str(dl.get("bitistarihi") or "")[:10],
            )
            for dl in dia_leaves
        }

        leave_rows: List[Dict[str, Any]] = []
        leaves_by_person: Dict[str, Dict[str, float]] = {}

        for lv in leaves:
            p_info = lv.get("person") or {}
            pid = str(p_info.get("id") or "")
            kp = kolay_by_id.get(pid, {})
            dia_rec = kolay_to_dia.get(pid)
            dia_key = int(dia_rec["_key"]) if dia_rec and dia_rec.get("_key") else None

            ltype = lv.get("type") or {}
            ltype_name = str(ltype.get("name") or "İzin")
            is_paid = bool(lv.get("isPaid", True))
            raw_used_days = lv.get("usedDays")
            used_days = float(raw_used_days) if raw_used_days is not None else 0.0
            if used_days <= 0:
                continue

            b_tarih = str(lv.get("startDate") or "")[:10]
            e_tarih = str(lv.get("endDate") or "")[:10]
            target_field = classify_leave_for_dia(ltype_name, is_paid)

            # Ay sınırını aşan izinlerin yalnız seçili aya düşen kısmını puantaja yansıt
            puantaj_days = used_days
            try:
                lv_start = datetime.date.fromisoformat(b_tarih)
                lv_end = datetime.date.fromisoformat(e_tarih)
                if lv_start < s_dt or lv_end > e_dt:
                    clip_start = max(lv_start, s_dt)
                    clip_end = min(lv_end, e_dt)
                    total_cal_days = max(1, (lv_end - lv_start).days + 1)
                    clipped_cal_days = max(0, (clip_end - clip_start).days + 1)
                    puantaj_days = round(used_days * (clipped_cal_days / total_cal_days), 2)
            except ValueError:
                pass

            leaves_by_person.setdefault(
                pid,
                {
                    "izinliucretligun": 0.0,
                    "izinliucretsizgun": 0.0,
                    "doktorraporugun": 0.0,
                    "uzaktancalismagunsayisi": 0.0,
                },
            )
            leaves_by_person[pid][target_field] = round(leaves_by_person[pid][target_field] + puantaj_days, 2)

            already_in_dia = bool(dia_key and (dia_key, b_tarih, e_tarih) in existing_dia_leave_keys)
            can_push_dia_izin = is_annual_leave_for_dia_module(ltype_name, is_paid, target_field)

            leave_rows.append(
                {
                    "leave_id": str(lv.get("id") or ""),
                    "kolayik_person_id": pid,
                    "_key_per_personel": dia_key,
                    "sicilno": str(dia_rec.get("sicilno") or "DİA'DA YOK") if dia_rec else "DİA'DA YOK",
                    "ad_soyad": kp.get("ad_soyad") or str(p_info.get("name") or ""),
                    "izin_turu": ltype_name,
                    "ucretli_mi": is_paid,
                    "baslangictarihi": b_tarih,
                    "bitistarihi": e_tarih,
                    "izinligunsayisi": puantaj_days,
                    "toplam_izin_gunu": used_days,
                    "hedef_puantaj_alani": target_field,
                    "_key_per_izinkarti": settings.DIA_YILLIK_IZIN_KART_KEY,
                    "bordroyaaktar": "E",
                    "aciklama": str(lv.get("comment") or f"Kolay İK - {ltype_name}"),
                    "in_dia": bool(dia_key),
                    "already_in_dia": already_in_dia,
                    "can_push_dia_izin": can_push_dia_izin,
                }
            )

        # 3C. Avans / Masraf / Prim / Kesinti (Transaction) Satırları
        transaction_rows: List[Dict[str, Any]] = []
        advances_by_person: Dict[str, float] = {}

        for tr in transactions:
            pid = str(tr.get("personId") or (tr.get("person") or {}).get("id") or "")
            kp = kolay_by_id.get(pid, {})
            dia_rec = kolay_to_dia.get(pid)
            dia_key = int(dia_rec["_key"]) if dia_rec and dia_rec.get("_key") else None
            t_status = str(tr.get("status") or "").lower()
            amount = float(tr.get("amount") or 0.0)
            t_type = str(tr.get("type") or "allowance")
            t_type_low = t_type.lower()

            # A4 Kararı: allowance, advancePayment ve otherCut -> KESINTI (Avans); bonus/premium -> KAZANC (Prim)
            is_cut = t_type_low in ("allowance", "advancepayment", "advance-expense", "othercut", "kesinti", "avans")
            islem_yonu = "KESINTI" if is_cut else "KAZANC"
            borc_hesapkodu = "196.001" if is_cut else "770.002.001"
            alacak_hesapkodu = "335.01"

            if t_status == "approved" and is_cut:
                advances_by_person[pid] = round(advances_by_person.get(pid, 0.0) + amount, 2)

            p_nested = tr.get("person") or {}
            fallback_name = f"{p_nested.get('firstName', '')} {p_nested.get('lastName', '')}".strip()

            transaction_rows.append(
                {
                    "transaction_id": str(tr.get("id") or ""),
                    "kolayik_person_id": pid,
                    "_key_per_personel": dia_key,
                    "sicilno": str(dia_rec.get("sicilno") or "DİA'DA YOK") if dia_rec else "DİA'DA YOK",
                    "ad_soyad": kp.get("ad_soyad") or fallback_name,
                    "tarih": str(tr.get("date") or "")[:10],
                    "tur": t_type,
                    "islem_yonu": islem_yonu,
                    "tutar": amount,
                    "para_birimi": str(tr.get("currency") or "TL"),
                    "brut_mu": bool(tr.get("isGross", False)),
                    "bordroya_yansisin": bool(tr.get("affectPayroll", True)),
                    "durum": t_status,
                    "borc_hesapkodu": borc_hesapkodu,
                    "alacak_hesapkodu": alacak_hesapkodu,
                    "aciklama": str(
                        tr.get("description")
                        or (f"Personel Avans/Kesinti - {fallback_name}" if is_cut else f"Personel Prim/Ek Ödeme - {fallback_name}")
                    ),
                    "in_dia": bool(dia_key),
                }
            )

        # 3D. Aylık Konsolide Puantaj Satırları (K5: Kıstelyevm + Gerçek Pazar + Çok Yıllı Resmi Tatil)
        holiday_days = get_month_holiday_days(year, month, exclude_sundays=True)
        puantaj_rows: List[Dict[str, Any]] = []

        for kp in sorted(kolay_list, key=lambda x: (0 if x["durum"] == "A" else 1, x["ad_soyad"])):
            # İlgili dönemden önce ayrılmış veya dönem bittikten sonra işe girmiş personelleri atla
            if kp["istenayrilmatarihi"] and kp["istenayrilmatarihi"] < s_date:
                continue
            if kp["isebaslamatarihi"] and kp["isebaslamatarihi"] > e_date:
                continue

            kid = kp["kolayik_id"]
            ov = overrides.get(kid) or overrides.get(kp["tckimlikno"]) or {}
            dia_rec = kolay_to_dia.get(kid)
            dia_key = int(dia_rec["_key"]) if dia_rec and dia_rec.get("_key") else None
            existing_ts = dia_ts_by_emp_key.get(dia_key) if dia_key else None

            ot = overtime_by_person.get(
                kid,
                {"fazlamesai1saat": 0.0, "haftasonumesaisisaat": 0.0, "resmitatilmesaisisaat": 0.0},
            )
            lv_sum = leaves_by_person.get(
                kid,
                {
                    "izinliucretligun": 0.0,
                    "izinliucretsizgun": 0.0,
                    "doktorraporugun": 0.0,
                    "uzaktancalismagunsayisi": 0.0,
                },
            )

            ucretli_izin = lv_sum["izinliucretligun"]
            ucretsiz_izin = lv_sum["izinliucretsizgun"]
            rapor_gun = lv_sum["doktorraporugun"]
            uzaktan_gun = lv_sum["uzaktancalismagunsayisi"]

            calc_days = calculate_employee_month_days(
                year=year,
                month=month,
                ise_baslama_str=kp.get("isebaslamatarihi") or "",
                isten_ayrilma_str=kp.get("istenayrilmatarihi") or "",
                ucretli_izin=ucretli_izin,
                ucretsiz_izin=ucretsiz_izin,
                rapor_gun=rapor_gun,
            )
            if calc_days["active_in_month"] <= 0:
                continue

            dia_maas = float(dia_rec.get("maas") or 0.0) if dia_rec else 0.0
            maas = float(
                existing_ts.get("maas")
                if existing_ts and float(existing_ts.get("maas") or 0) > 0
                else (ov.get("maas") if ov.get("maas") is not None else dia_maas)
            )
            ucretturu = str(
                existing_ts.get("ucretturu")
                if existing_ts and existing_ts.get("ucretturu")
                else (ov.get("ucretturu") or (dia_rec.get("ucretturu") if dia_rec else "N") or "N")
            )

            puantaj_rows.append(
                {
                    "kolayik_person_id": kid,
                    "_key_per_personel": dia_key,
                    "dia_puantaj_key": int(existing_ts["_key"]) if existing_ts and existing_ts.get("_key") else None,
                    "sicilno": str(dia_rec.get("sicilno") or "DİA'DA YOK") if dia_rec else "DİA'DA YOK",
                    "ad_soyad": kp["ad_soyad"],
                    "departman": kp["departman"],
                    "isebaslamatarihi": kp.get("isebaslamatarihi") or "",
                    "istenayrilmatarihi": kp.get("istenayrilmatarihi") or "",
                    "maliyet_profili": str(ov.get("maliyet_profili") or kp["maliyet_profili"]),
                    "duzenlemeyil": year,
                    "duzenlemeayno": month,
                    "duzenlemetarihi": duzenlemetarihi,
                    "maas": maas,
                    "ucretturu": ucretturu,
                    "normalmesaigun": calc_days["normalmesaigun"],
                    "haftasonugun": calc_days["haftasonugun"],
                    "resmitatilgun": calc_days["resmitatilgun"],
                    "izinliucretligun": ucretli_izin,
                    "izinliucretsizgun": ucretsiz_izin,
                    "doktorraporugun": rapor_gun,
                    "uzaktancalismagunsayisi": uzaktan_gun,
                    "toplamsigortagun": calc_days["toplamsigortagun"],
                    "toplambordrogun": calc_days["toplambordrogun"],
                    "fazlamesai1saat": ot["fazlamesai1saat"],
                    "haftasonumesaisisaat": ot["haftasonumesaisisaat"],
                    "resmitatilmesaisisaat": ot["resmitatilmesaisisaat"],
                    "avans_kesinti_tutari": advances_by_person.get(kid, 0.0),
                    "in_dia": bool(dia_key),
                    "already_in_dia": bool(existing_ts),
                    "dia_hesaplanan_brut": float(existing_ts.get("aylikbrutkazanc") or 0.0) if existing_ts else 0.0,
                    "dia_hesaplanan_net": float(existing_ts.get("ayliknetkazanc") or 0.0) if existing_ts else 0.0,
                    "dia_hesaplanan_gv": float(existing_ts.get("odenecekgelirvergisi") or 0.0) if existing_ts else 0.0,
                    "dia_hesaplanan_dv": float(existing_ts.get("odenecekdamgavergisi") or 0.0) if existing_ts else 0.0,
                    "dia_hesaplanan_sgk_isci": (
                        float(existing_ts.get("toplamiscisskprimtutari") or 0.0)
                        + float(existing_ts.get("issizlikiscipayitutari") or 0.0)
                        if existing_ts
                        else 0.0
                    ),
                    "dia_hesaplanan_sgk_isveren": (
                        float(existing_ts.get("toplamisverensskprimtutari") or 0.0)
                        + float(existing_ts.get("issizlikisverenpayitutari") or 0.0)
                        if existing_ts
                        else 0.0
                    ),
                }
            )

        # 3E. Muhasebe Maaş Tahakkuk Mahsup Fişi Önizlemesi
        mahsup_preview = self.build_mahsup_preview_from_dia_timesheets(year, month, dia_timesheets, puantaj_rows)

        return {
            "period": {
                "start_date": s_date,
                "end_date": e_date,
                "year": year,
                "month": month,
                "duzenlemetarihi": duzenlemetarihi,
                "holiday_days": holiday_days,
            },
            "pdks_rows": pdks_rows,
            "leave_rows": leave_rows,
            "transaction_rows": transaction_rows,
            "puantaj_rows": puantaj_rows,
            "mahsup_preview": mahsup_preview,
        }

    def build_mahsup_preview_from_dia_timesheets(
        self,
        year: int,
        month: int,
        dia_timesheets: List[Dict[str, Any]],
        puantaj_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        DİA'da ilgili ay için oluşmuş puantaj kayıtlarından
        770.001 / 770.002 / 760.001 - 335 / 360 / 361 Maaş Tahakkuk Mahsup Fişi
        kalemlerini otomatik hesaplar.
        """
        last_day = calendar.monthrange(year, month)[1]
        tarih = f"{year:04d}-{month:02d}-{last_day:02d}"
        aciklama = f"{year}/{month:02d} DÖNEMİ PERSONEL MAAŞ TAHAKKUK FİŞİ"

        profile_by_dia_key = {
            int(r["_key_per_personel"]): str(r.get("maliyet_profili") or "GENEL")
            for r in puantaj_rows
            if r.get("_key_per_personel")
        }
        avans_by_dia_key = {
            int(r["_key_per_personel"]): float(r.get("avans_kesinti_tutari") or 0.0)
            for r in puantaj_rows
            if r.get("_key_per_personel")
        }

        buckets = {
            "MUDUR": {"brut": 0.0, "sgk_isveren": 0.0, "issizlik_isveren": 0.0, "net": 0.0},
            "GENEL": {"brut": 0.0, "sgk_isveren": 0.0, "issizlik_isveren": 0.0, "net": 0.0},
            "PAZARLAMA_DEPO": {"brut": 0.0, "sgk_isveren": 0.0, "issizlik_isveren": 0.0, "net": 0.0},
        }
        toplam_gv = 0.0
        toplam_dv = 0.0
        toplam_sgk_prim = 0.0
        toplam_sgk_issizlik = 0.0
        toplam_avans = 0.0

        for ts in dia_timesheets:
            emp_key = int(ts.get("_key_per_personel") or 0)
            prof = profile_by_dia_key.get(emp_key, "GENEL")
            if prof not in buckets:
                prof = "GENEL"

            brut = float(ts.get("aylikbrutkazanc") or 0.0)
            sgk_isv = float(ts.get("toplamisverensskprimtutari") or 0.0)
            iss_isv = float(ts.get("issizlikisverenpayitutari") or 0.0)
            sgk_isci = float(ts.get("toplamiscisskprimtutari") or 0.0)
            iss_isci = float(ts.get("issizlikiscipayitutari") or 0.0)
            gv = float(ts.get("odenecekgelirvergisi") or 0.0)
            dv = float(ts.get("odenecekdamgavergisi") or 0.0)
            net = float(ts.get("toplamnetkazanc") or ts.get("ayliknetkazanc") or 0.0)
            avans = min(net, avans_by_dia_key.get(emp_key, 0.0))

            buckets[prof]["brut"] = round(buckets[prof]["brut"] + brut, 2)
            buckets[prof]["sgk_isveren"] = round(buckets[prof]["sgk_isveren"] + sgk_isv, 2)
            buckets[prof]["issizlik_isveren"] = round(buckets[prof]["issizlik_isveren"] + iss_isv, 2)
            buckets[prof]["net"] = round(buckets[prof]["net"] + (net - avans), 2)

            toplam_gv = round(toplam_gv + gv, 2)
            toplam_dv = round(toplam_dv + dv, 2)
            toplam_sgk_prim = round(toplam_sgk_prim + sgk_isv + sgk_isci, 2)
            toplam_sgk_issizlik = round(toplam_sgk_issizlik + iss_isv + iss_isci, 2)
            toplam_avans = round(toplam_avans + avans, 2)

        account_prefix_map = {
            "MUDUR": ("770.001.001", "770.001.002", "770.001.003"),
            "GENEL": ("770.002.001", "770.002.002", "770.002.003"),
            "PAZARLAMA_DEPO": ("760.001.001", "760.001.002", "760.001.003"),
        }

        lines: List[Dict[str, Any]] = []

        def _add_line(hesapkodu: str, borc: float, alacak: float, note: str) -> None:
            if round(borc, 2) <= 0 and round(alacak, 2) <= 0:
                return
            h_info = settings.DIA_MUH_HESAPLAR.get(hesapkodu, {})
            lines.append(
                {
                    "hesapkodu": hesapkodu,
                    "hesapadi": h_info.get("aciklama", ""),
                    "_key_muh_hesapkarti": h_info.get("_key", 0),
                    "borc": round(borc, 2),
                    "alacak": round(alacak, 2),
                    "aciklama": f"{year}/{month:02d} {note}",
                }
            )

        for prof, codes in account_prefix_map.items():
            b = buckets[prof]
            _add_line(codes[0], b["brut"], 0.0, f"{prof} Brüt Ücret Gideri")
            _add_line(codes[1], b["sgk_isveren"], 0.0, f"{prof} SGK İşveren Payı")
            _add_line(codes[2], b["issizlik_isveren"], 0.0, f"{prof} SGK İşveren İşsizlik Payı")

        _add_line("335.02", 0.0, buckets["MUDUR"]["net"], "Ödenecek Müdür Net Ücretleri")
        _add_line(
            "335.01",
            0.0,
            round(buckets["GENEL"]["net"] + buckets["PAZARLAMA_DEPO"]["net"], 2),
            "Ödenecek Personel Net Ücretleri",
        )
        _add_line("196.001", 0.0, toplam_avans, "Personel Maaş Avansı Mahsubu")
        _add_line("360.003.001", 0.0, toplam_gv, "Ödenecek Gelir Vergisi Stopajı")
        _add_line("360.001.002", 0.0, toplam_dv, "Ödenecek Damga Vergisi")
        _add_line("361.001", 0.0, toplam_sgk_prim, "Ödenecek SGK Primi (İşçi + İşveren)")
        _add_line("361.002", 0.0, toplam_sgk_issizlik, "Ödenecek SGK İşsizlik Primi (İşçi + İşveren)")

        total_borc = round(sum(x["borc"] for x in lines), 2)
        total_alacak = round(sum(x["alacak"] for x in lines), 2)
        diff = round(total_borc - total_alacak, 2)
        if 0 < abs(diff) <= 0.05 and lines:
            for item in reversed(lines):
                if item["alacak"] > 0:
                    item["alacak"] = round(item["alacak"] + diff, 2)
                    break
            total_alacak = round(sum(x["alacak"] for x in lines), 2)
            diff = round(total_borc - total_alacak, 2)

        return {
            "tarih": tarih,
            "fisno": f"MAAS{year}{month:02d}",
            "aciklama": aciklama,
            "timesheet_count": len(dia_timesheets),
            "total_borc": total_borc,
            "total_alacak": total_alacak,
            "diff": diff,
            "lines": lines,
        }

    # --- 4. Seçilen İzinleri DİA'ya Aktarma (per_izinler_ekle — K4 & K6 Korumalı) ---
    def push_selected_leaves(self, selected_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        added = 0
        errors = 0
        results: List[Dict[str, Any]] = []

        try:
            existing_dia_leaves = self.dia.list_leaves()
        except Exception:
            existing_dia_leaves = []

        existing_leave_keys = {
            (
                int(dl.get("_key_per_personel") or 0),
                str(dl.get("baslangictarihi") or "")[:10],
                str(dl.get("bitistarihi") or "")[:10],
            )
            for dl in existing_dia_leaves
        }

        for row in selected_rows:
            if not row.get("_key_per_personel"):
                errors += 1
                results.append(
                    {
                        "ad_soyad": row.get("ad_soyad", ""),
                        "status": "ERROR",
                        "message": "Personel henüz DİA'da kayıtlı değil. Önce Personel sekmesinden DİA'ya aktarın.",
                    }
                )
                continue

            dup_key = (
                int(row["_key_per_personel"]),
                str(row.get("baslangictarihi") or "")[:10],
                str(row.get("bitistarihi") or "")[:10],
            )
            if dup_key in existing_leave_keys:
                errors += 1
                results.append(
                    {
                        "ad_soyad": row.get("ad_soyad", ""),
                        "status": "ERROR",
                        "message": f"{dup_key[1]} - {dup_key[2]} tarihli izin kaydı DİA'da zaten mevcut (Mükerrer kayıt engellendi).",
                    }
                )
                continue

            try:
                kart = build_dia_izin_payload(row)
                res = self.dia.add_leave(kart)
                existing_leave_keys.add(dup_key)
                added += 1
                results.append(
                    {
                        "ad_soyad": row.get("ad_soyad", ""),
                        "baslangictarihi": kart["baslangictarihi"],
                        "izinligunsayisi": kart["izinligunsayisi"],
                        "status": "SUCCESS",
                        "response": res,
                    }
                )
            except Exception as exc:
                errors += 1
                results.append(
                    {
                        "ad_soyad": row.get("ad_soyad", ""),
                        "status": "ERROR",
                        "message": str(exc),
                    }
                )

        return {"added": added, "errors": errors, "results": results}

    # --- 5. Seçilen Puantajları DİA'ya Aktarma (per_personel_puantaj_ekle / guncelle — K6 Korumalı) ---
    def push_selected_timesheets(self, selected_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        added = 0
        updated = 0
        errors = 0
        results: List[Dict[str, Any]] = []

        # K6 Mükerrer Puantaj Koruması: DİA'da ilgili yıl/ay için mevcut puantajları canlı sorgula
        existing_ts_map: Dict[Tuple[int, int, int], int] = {}
        queried_periods: set[Tuple[int, int]] = set()
        for r in selected_rows:
            y = int(r.get("duzenlemeyil") or datetime.date.today().year)
            m = int(r.get("duzenlemeayno") or datetime.date.today().month)
            if (y, m) not in queried_periods:
                queried_periods.add((y, m))
                try:
                    for ts in self.dia.list_timesheets(year=y, month=m):
                        ekey = int(ts.get("_key_per_personel") or 0)
                        tkey = int(ts.get("_key") or 0)
                        if ekey > 0 and tkey > 0:
                            existing_ts_map[(ekey, y, m)] = tkey
                except Exception:
                    pass

        for row in selected_rows:
            if not row.get("_key_per_personel"):
                errors += 1
                results.append(
                    {
                        "ad_soyad": row.get("ad_soyad", ""),
                        "status": "ERROR",
                        "message": "Personel henüz DİA'da kayıtlı değil. Önce Personel sekmesinden DİA'ya aktarın.",
                    }
                )
                continue
            try:
                emp_key = int(row["_key_per_personel"])
                y = int(row.get("duzenlemeyil") or datetime.date.today().year)
                m = int(row.get("duzenlemeayno") or datetime.date.today().month)
                live_ts_key = row.get("dia_puantaj_key") or existing_ts_map.get((emp_key, y, m))
                row_copy = dict(row)
                if live_ts_key:
                    row_copy["dia_puantaj_key"] = int(live_ts_key)

                kart = build_dia_puantaj_payload(row_copy)
                if row_copy.get("dia_puantaj_key"):
                    res = self.dia.update_timesheet(kart)
                    updated += 1
                    action = "UPDATED"
                else:
                    res = self.dia.add_timesheet(kart)
                    new_key = int(res.get("key") or res.get("_key") or 0) if isinstance(res, dict) else 0
                    if new_key > 0:
                        existing_ts_map[(emp_key, y, m)] = new_key
                    added += 1
                    action = "ADDED"

                results.append(
                    {
                        "ad_soyad": row.get("ad_soyad", ""),
                        "sicilno": row.get("sicilno", ""),
                        "action": action,
                        "status": "SUCCESS",
                        "response": res,
                    }
                )
            except Exception as exc:
                errors += 1
                results.append(
                    {
                        "ad_soyad": row.get("ad_soyad", ""),
                        "sicilno": row.get("sicilno", ""),
                        "action": "ERROR",
                        "status": "ERROR",
                        "message": str(exc),
                    }
                )

        return {"added": added, "updated": updated, "errors": errors, "results": results}

    # --- 6. Seçilen Avans / Ek Ödemeleri DİA'ya Aktarma (K6 Mükerrer Korumalı) ---
    def push_selected_transactions(
        self,
        selected_rows: List[Dict[str, Any]],
        mode: str = "mahsup",
        fis_tarihi: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Seçilen avans/prim/kesinti hareketlerini DİA'ya aktarır.
        mode='mahsup': Seçilen hareketleri Muhasebe Mahsup Fişi olarak kaydeder.
        mode='ekodeme': Personel Ek Ödeme/Kesinti Fişi (per_ekodemekesinti_ekle) olarak kaydeder.
        """
        if not selected_rows:
            raise ValueError("Aktarılacak hareket seçilmedi.")

        tarih = (fis_tarihi or selected_rows[0].get("tarih") or datetime.date.today().isoformat())[:10]
        compact_dt = tarih.replace("-", "")

        if mode == "mahsup":
            lines: List[Dict[str, Any]] = []
            id_tokens: List[str] = []
            for row in selected_rows:
                tutar = round(float(row.get("tutar") or 0.0), 2)
                if tutar <= 0:
                    continue
                ad = str(row.get("ad_soyad") or "")
                yon = str(row.get("islem_yonu") or "").upper()
                tur_raw = str(row.get("tur") or "allowance").lower()
                is_cut = (yon == "KESINTI") if yon in ("KESINTI", "KAZANC") else (
                    tur_raw in ("allowance", "advancepayment", "advance-expense", "othercut", "kesinti", "avans")
                )
                borc_kod = str(row.get("borc_hesapkodu") or ("196.001" if is_cut else "770.002.001"))
                alacak_kod = str(row.get("alacak_hesapkodu") or "335.01")
                row_dt = str(row.get("tarih") or tarih)[:10]
                id_tokens.append(str(row.get("transaction_id") or f"{ad}:{row_dt}:{tutar}"))

                lines.append(
                    {
                        "hesapkodu": borc_kod,
                        "borc": tutar,
                        "alacak": 0.0,
                        "aciklama": f"{ad} - {'Personel Avans/Kesinti' if is_cut else 'Personel Prim/Ek Ödeme'} ({row_dt})",
                    }
                )
                lines.append(
                    {
                        "hesapkodu": alacak_kod,
                        "borc": 0.0,
                        "alacak": tutar,
                        "aciklama": f"{ad} - {'Personel Avans Tahakkuku' if is_cut else 'Personel Prim Tahakkuku'} ({row_dt})",
                    }
                )

            digest = hashlib.sha256("|".join(sorted(id_tokens)).encode("utf-8")).hexdigest()[:4].upper()
            fisno = f"AVS{compact_dt[-6:]}{digest}"

            # K6 Mükerrer Fiş Koruması
            try:
                existing_vouchers = self.dia.list_accounting_vouchers(fisno=fisno)
            except Exception:
                existing_vouchers = []
            if any(str(v.get("fisno") or "").strip() == fisno for v in existing_vouchers):
                raise ValueError(
                    f"'{fisno}' numaralı muhasebe avans/ödeme fişi DİA'da zaten kayıtlı! Mükerrer fiş oluşturma engellendi."
                )

            kart = build_dia_mahsup_payload(
                tarih=tarih,
                fisno=fisno,
                aciklama=f"{tarih} KOLAY İK PERSONEL AVANS/ÖDEME FİŞİ",
                lines=lines,
            )
            res = self.dia.add_accounting_voucher(kart)
            return {
                "added": len(selected_rows),
                "errors": 0,
                "mode": "mahsup",
                "fisno": fisno,
                "response": res,
            }

        # mode == 'ekodeme' (per_ekodemekesinti_ekle — K6 Mükerrer Korumalı)
        try:
            existing_slips = self.dia.list_extra_payment_slips()
        except Exception:
            existing_slips = []

        existing_slip_keys = {
            (
                int(s.get("_key_per_personel") or 0),
                str(s.get("tarih") or "")[:10],
                round(float(s.get("tutar") or 0.0), 2),
                str(s.get("turu") or "").strip().upper(),
            )
            for s in existing_slips
        }

        added = 0
        errors = 0
        results: List[Dict[str, Any]] = []
        for row in selected_rows:
            dkey = row.get("_key_per_personel")
            tutar = round(float(row.get("tutar") or 0.0), 2)
            if not dkey or tutar <= 0:
                errors += 1
                results.append(
                    {
                        "ad_soyad": str(row.get("ad_soyad") or ""),
                        "status": "ERROR",
                        "message": "Personel DİA'da kayıtlı değil veya tutar 0'dan büyük değil.",
                    }
                )
                continue

            yon = str(row.get("islem_yonu") or "").upper()
            tur_raw = str(row.get("tur") or "allowance").lower()
            is_cut = (yon == "KESINTI") if yon in ("KESINTI", "KAZANC") else (
                tur_raw in ("allowance", "advancepayment", "advance-expense", "othercut", "kesinti", "avans")
            )
            dia_turu = "K" if is_cut else "E"
            odeme_kesinti_key = settings.DIA_AVANS_KESINTI_KEY if is_cut else settings.DIA_PRIM_KAZANC_KEY
            sicilno = str(row.get("sicilno") or "").strip()
            row_tarih = str(row.get("tarih") or tarih)[:10]

            slip_dup_key = (int(dkey), row_tarih, tutar, dia_turu)
            if slip_dup_key in existing_slip_keys:
                errors += 1
                results.append(
                    {
                        "ad_soyad": str(row.get("ad_soyad") or ""),
                        "sicilno": sicilno,
                        "status": "ERROR",
                        "message": f"{row_tarih} tarihli {tutar:,.2f} TL tutarındaki ek ödeme/kesinti fişi DİA'da zaten mevcut (Mükerrer kayıt engellendi).",
                    }
                )
                continue

            kart = {
                "_key_per_personel": int(dkey),
                "_key_per_odemekesinti": odeme_kesinti_key,
                "_key_sis_sube": settings.DIA_MERKEZ_SUBE_KEY,
                "turu": dia_turu,
                "tarih": row_tarih,
                "tutar": f"{tutar:.2f}",
                "netbrut": "B" if row.get("brut_mu") else "N",
                "odemeturu": "A",
                "bordroyaaktar": "E" if row.get("bordroya_yansisin", True) else "H",
                "sicilno": sicilno,
                "personelsicilno": sicilno,
                "personeladisoyadi": str(row.get("ad_soyad") or "").strip(),
                "aciklama": str(row.get("aciklama") or f"Kolay İK {tur_raw}")[:150],
            }
            try:
                res = self.dia.add_extra_payment_slip(kart)
                existing_slip_keys.add(slip_dup_key)
                added += 1
                results.append(
                    {
                        "ad_soyad": str(row.get("ad_soyad") or ""),
                        "sicilno": sicilno,
                        "status": "SUCCESS",
                        "response": res,
                    }
                )
            except Exception as exc:
                errors += 1
                results.append(
                    {
                        "ad_soyad": str(row.get("ad_soyad") or ""),
                        "sicilno": sicilno,
                        "status": "SUCCESS" if False else "ERROR",
                        "message": str(exc),
                    }
                )

        if added == 0 and errors > 0 and not results:
            raise ValueError("Seçilen personeller henüz DİA'da kayıtlı değil veya tutar 0.")

        return {"added": added, "errors": errors, "mode": "ekodeme", "results": results}

    # --- 7. Muhasebe Mahsup Fişini DİA'ya Aktarma (muh_muhasebefisi_ekle — K6 Korumalı) ---
    def push_mahsup_voucher(
        self,
        tarih: str,
        fisno: str,
        aciklama: str,
        lines: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        clean_fisno = str(fisno or "").strip()
        if not clean_fisno:
            raise ValueError("Mahsup fiş numarası (Fiş No) boş olamaz!")

        try:
            existing_vouchers = self.dia.list_accounting_vouchers(fisno=clean_fisno)
        except Exception:
            existing_vouchers = []
        if any(str(v.get("fisno") or "").strip() == clean_fisno for v in existing_vouchers):
            raise ValueError(
                f"'{clean_fisno}' fiş numaralı Muhasebe Mahsup Fişi DİA'da zaten mevcut! Mükerrer tahakkuk engellendi."
            )

        kart = build_dia_mahsup_payload(tarih=tarih, fisno=clean_fisno, aciklama=aciklama, lines=lines)
        res = self.dia.add_accounting_voucher(kart)
        return {"status": "SUCCESS", "fisno": clean_fisno, "tarih": tarih, "response": res}
