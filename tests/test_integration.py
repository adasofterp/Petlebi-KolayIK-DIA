"""Birim, Güvenlik ve Şema Doğrulama Testleri (Anonimleştirilmiş — Canlı DİA'ya Test Verisi Yazmaz)."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from src.app import APP_CSRF_TOKEN, app
from src.config import settings
from src.mappers import (
    build_dia_izin_payload,
    build_dia_mahsup_payload,
    build_dia_personel_payload,
    build_dia_puantaj_payload,
    calculate_employee_month_days,
    classify_cost_center,
    classify_leave_for_dia,
    get_month_holiday_days,
    norm_tr,
    sanitize_dia_keys,
)
from src.sync_engine import SyncEngine


class TestDiaSchemasAndSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.engine = SyncEngine()

    def test_norm_tr_and_uppercase_turkish_classification(self) -> None:
        self.assertEqual(norm_tr("ÖRNEK ÇALIŞAN"), "ornek calisan")
        self.assertEqual(norm_tr("İsmail Şahin"), "ismail sahin")
        self.assertEqual(norm_tr("HASTALIK İZNİ"), "hastalik izni")
        self.assertEqual(norm_tr("SATIŞ VE PAZARLAMA"), "satis ve pazarlama")
        self.assertEqual(classify_leave_for_dia("HASTALIK İZNİ", True), "doktorraporugun")
        self.assertEqual(classify_cost_center("SATIŞ", "UZMAN"), "PAZARLAMA_DEPO")

    def test_sanitize_dia_keys_enforces_integer_foreign_keys(self) -> None:
        raw = {
            "adi": "ÖRNEK",
            "_key_sis_sube": {"_key": 2279, "subekodu": "00000001"},
            "_key_sis_doviz": {"_key": 2272, "adi": "TL"},
            "_key_per_personel": "10001",
            "_key_scf_carikart": "",
        }
        cleaned = sanitize_dia_keys(raw)
        self.assertEqual(cleaned["_key_sis_sube"], 2279)
        self.assertEqual(cleaned["_key_sis_doviz"], 2272)
        self.assertEqual(cleaned["_key_per_personel"], 10001)
        self.assertEqual(cleaned["_key_scf_carikart"], 0)

    def test_k2_employee_update_preserves_existing_dia_fields(self) -> None:
        existing_dia = {
            "_key": 10001,
            "sicilno": "00000003",
            "adi": "ÖRNEK",
            "soyadi": "KULLANICI",
            "tckimlikno": "10000000146",
            "durum": "A",
            "isebaslamatarihi": "2022-05-10",
            "dogumtarihi": "1990-08-15",
            "cinsiyet": "K",
            "medenihali": "E",
            "eposta": "ornek@sirket.local",
            "telefon1": "05550000000",
            "gorevi": "KIDEMLİ UZMAN",
            "il": "İSTANBUL",
            "_key_sis_sube": {"_key": 2279},
            "_key_sis_doviz": {"_key": 2272},
        }
        # Kolay İK'da boş gelen alanlar mevcut DİA kartını ezmemeli
        staged_sparse = {
            "dia_key": 10001,
            "sicilno": "00000003",
            "adi": "ÖRNEK",
            "soyadi": "KULLANICI",
            "tckimlikno": "10000000146",
            "durum": "A",
            "isebaslamatarihi": "",
            "dogumtarihi": "",
            "cinsiyet": "",
            "medenihali": "",
            "eposta": "",
            "telefon1": "",
            "gorevi": "",
            "il": "",
            "maas": 55000.0,
            "ucretturu": "N",
        }
        payload = build_dia_personel_payload(staged_sparse, existing_dia_card=existing_dia)
        self.assertEqual(payload["isebaslamatarihi"], "2022-05-10")
        self.assertEqual(payload["dogumtarihi"], "1990-08-15")
        self.assertEqual(payload["cinsiyet"], "K")
        self.assertEqual(payload["medenihali"], "E")
        self.assertEqual(payload["eposta"], "ornek@sirket.local")
        self.assertEqual(payload["telefon1"], "05550000000")
        self.assertEqual(payload["gorevi"], "KIDEMLİ UZMAN")
        self.assertEqual(payload["il"], "İSTANBUL")

    def test_k3_homonym_employees_are_not_merged_into_same_dia_card(self) -> None:
        kolay_list = [
            {"kolayik_id": "k1", "ad_soyad": "AHMET YILMAZ", "tckimlikno": "10000000146"},
            {"kolayik_id": "k2", "ad_soyad": "AHMET YILMAZ", "tckimlikno": "10000000262"},
        ]
        dia_list = [
            {"_key": 501, "adi": "AHMET", "soyadi": "YILMAZ", "tckimlikno": "10000000146"},
        ]
        kolay_to_dia, _ = self.engine._match_employees(kolay_list, dia_list)
        self.assertIn("k1", kolay_to_dia)
        self.assertNotIn("k2", kolay_to_dia)

    def test_k4_leave_payload_blocks_non_annual_leaves_and_self_approver(self) -> None:
        annual_row = {
            "_key_per_personel": 10001,
            "sicilno": "00000010",
            "ad_soyad": "ÖRNEK PERSONEL",
            "izin_turu": "Yıllık İzin",
            "ucretli_mi": True,
            "hedef_puantaj_alani": "izinliucretligun",
            "baslangictarihi": "2026-10-15",
            "bitistarihi": "2026-10-16",
            "izinligunsayisi": 2.0,
        }
        kart = build_dia_izin_payload(annual_row)
        self.assertEqual(kart["_key_per_personel"], 10001)
        self.assertNotIn("_key_per_personel_onaylayan", kart)

        sick_row = {
            **annual_row,
            "izin_turu": "HASTALIK İZNİ",
            "hedef_puantaj_alani": "doktorraporugun",
        }
        with self.assertRaises(ValueError):
            build_dia_izin_payload(sick_row)

    def test_k5_pro_rata_puantaj_and_multiyear_holidays(self) -> None:
        # Ocak 2027 resmi tatili (2027-01-01 Cuma) = 1.0 gün
        self.assertEqual(get_month_holiday_days(2027, 1, exclude_sundays=True), 1.0)
        # Ağustos 2026: 30 Ağustos Pazar gününe denk geldiği için Pazar hariç tutulduğunda 0.0 olmalı (çift düşülmez)
        self.assertEqual(get_month_holiday_days(2026, 8, exclude_sundays=True), 0.0)

        # Ay ortasında (2026-10-16) işe giren personel 30 gün değil, 16-31 Ekim (16 gün) kıstelyevm almalı
        partial = calculate_employee_month_days(2026, 10, ise_baslama_str="2026-10-16")
        self.assertEqual(partial["base_days"], 16.0)
        self.assertEqual(partial["toplamsigortagun"], 16.0)

    def test_k8_terminated_employee_requires_sgk_exit_reason(self) -> None:
        staged_terminated = {
            "sicilno": "00000099",
            "adi": "ÖRNEK",
            "soyadi": "AYRILAN",
            "tckimlikno": "10000000146",
            "durum": "P",
            "isebaslamatarihi": "2024-01-01",
            "istenayrilmatarihi": "2026-10-31",
            "ian_kodu": "",
            "maas": 30000.0,
        }
        with self.assertRaises(ValueError):
            build_dia_personel_payload(staged_terminated, sgk_ian_key_map={"03": 6532})

        staged_with_reason = {**staged_terminated, "ian_kodu": "03"}
        kart = build_dia_personel_payload(staged_with_reason, sgk_ian_key_map={"03": 6532})
        self.assertEqual(kart["durum"], "P")
        self.assertEqual(kart["_key_per_istenayrilmanedenleri"], 6532)

    def test_puantaj_and_mahsup_validation(self) -> None:
        row = {
            "_key_per_personel": 10001,
            "duzenlemeyil": 2026,
            "duzenlemeayno": 10,
            "maas": 100000.0,
            "ucretturu": "B",
            "normalmesaigun": 24.5,
            "haftasonugun": 4.0,
            "resmitatilgun": 1.5,
            "toplamsigortagun": 30.0,
            "toplambordrogun": 30.0,
            "fazlamesai1saat": 4.5,
            "haftasonumesaisisaat": 2.0,
            "resmitatilmesaisisaat": 1.0,
        }
        kart = build_dia_puantaj_payload(row)
        self.assertEqual(float(kart["toplamsigortasaat"]), 225.0)
        self.assertEqual(float(kart["toplamfazlamesaisaat"]), 7.5)

        # 45 SGK günü veya negatif maaş reddedilmeli
        with self.assertRaises(ValueError):
            build_dia_puantaj_payload({**row, "toplamsigortagun": 45.0})
        with self.assertRaises(ValueError):
            build_dia_puantaj_payload({**row, "maas": -5000.0})

        # Mahsup denklik kontrolü ve önizleme testi
        preview = self.engine.build_mahsup_preview_from_dia_timesheets(
            2026,
            10,
            [
                {
                    "_key_per_personel": 10001,
                    "aylikbrutkazanc": "50000.00",
                    "toplamisverensskprimtutari": "10000.00",
                    "issizlikisverenpayitutari": "1000.00",
                    "toplamiscisskprimtutari": "7000.00",
                    "issizlikiscipayitutari": "500.00",
                    "odenecekgelirvergisi": "6000.00",
                    "odenecekdamgavergisi": "379.50",
                    "ayliknetkazanc": "36120.50",
                }
            ],
            [{"_key_per_personel": 10001, "maliyet_profili": "GENEL", "avans_kesinti_tutari": 5000.0}],
        )
        self.assertEqual(preview["diff"], 0.0)
        self.assertEqual(preview["total_borc"], preview["total_alacak"])

    def test_k7_csrf_and_untrusted_repo_guard(self) -> None:
        # Yabancı Origin ile gelen POST isteği 403 ile reddedilmeli
        r_evil = self.client.post(
            "/api/settings",
            headers={"Origin": "https://evil.example.com"},
            json={"dia_firma_kodu": 1},
        )
        self.assertEqual(r_evil.status_code, 403)

        # Lokal Origin ama CSRF token olmadan gelen istek 403 ile reddedilmeli
        r_no_csrf = self.client.post(
            "/api/settings",
            headers={"Origin": "http://127.0.0.1:8765"},
            json={"dia_firma_kodu": 1},
        )
        self.assertEqual(r_no_csrf.status_code, 403)

        # Doğru CSRF token ile bile yabancı GitHub deposu reddedilmeli
        r_bad_repo = self.client.post(
            "/api/settings",
            headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": APP_CSRF_TOKEN},
            json={"github_repo": "evil/malware-repo"},
        )
        self.assertEqual(r_bad_repo.status_code, 400)


if __name__ == "__main__":
    unittest.main()
