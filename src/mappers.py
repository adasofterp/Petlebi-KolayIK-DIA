"""Kolay İK <-> DİA ERP Veri Şeması Dönüştürücüleri ve Yardımcı Fonksiyonlar."""

from __future__ import annotations

import calendar
import datetime
import unicodedata
from typing import Any, Dict, List, Optional

from .config import settings

# Sabit Ulusal Bayramlar (Her Yıl Geçerli: Ay-Gün -> Gün Sayısı)
FIXED_NATIONAL_HOLIDAYS: Dict[str, float] = {
    "01-01": 1.0,  # Yılbaşı
    "04-23": 1.0,  # 23 Nisan Ulusal Egemenlik ve Çocuk Bayramı
    "05-01": 1.0,  # 1 Mayıs Emek ve Dayanışma Günü
    "05-19": 1.0,  # 19 Mayıs Atatürk'ü Anma, Gençlik ve Spor Bayramı
    "07-15": 1.0,  # 15 Temmuz Demokrasi ve Milli Birlik Günü
    "08-30": 1.0,  # 30 Ağustos Zafer Bayramı
    "10-28": 0.5,  # Cumhuriyet Bayramı Arifesi
    "10-29": 1.0,  # 29 Ekim Cumhuriyet Bayramı
}

# Dini Bayramlar (2025 - 2028 Türkiye Diyanet Takvimi - Arifeler 0.5 gün)
RELIGIOUS_HOLIDAYS_BY_YEAR: Dict[int, Dict[str, float]] = {
    2025: {
        "2025-03-29": 0.5,
        "2025-03-30": 1.0,
        "2025-03-31": 1.0,
        "2025-04-01": 1.0,
        "2025-06-05": 0.5,
        "2025-06-06": 1.0,
        "2025-06-07": 1.0,
        "2025-06-08": 1.0,
        "2025-06-09": 1.0,
    },
    2026: {
        "2026-03-19": 0.5,
        "2026-03-20": 1.0,
        "2026-03-21": 1.0,
        "2026-03-22": 1.0,
        "2026-05-26": 0.5,
        "2026-05-27": 1.0,
        "2026-05-28": 1.0,
        "2026-05-29": 1.0,
        "2026-05-30": 1.0,
    },
    2027: {
        "2027-03-08": 0.5,
        "2027-03-09": 1.0,
        "2027-03-10": 1.0,
        "2027-03-11": 1.0,
        "2027-05-15": 0.5,
        "2027-05-16": 1.0,
        "2027-05-17": 1.0,
        "2027-05-18": 1.0,
        "2027-05-19": 1.0,  # 19 Mayıs ile çakışır, dict anahtarı sayesinde tek sayılır
    },
    2028: {
        "2028-02-25": 0.5,
        "2028-02-26": 1.0,
        "2028-02-27": 1.0,
        "2028-02-28": 1.0,
        "2028-05-04": 0.5,
        "2028-05-05": 1.0,
        "2028-05-06": 1.0,
        "2028-05-07": 1.0,
        "2028-05-08": 1.0,
    },
}


def get_official_holidays_for_year(year: int) -> Dict[str, float]:
    """İlgili yılın tüm ulusal ve dini resmi tatillerini (YYYY-MM-DD -> gün) döndürür."""
    holidays: Dict[str, float] = {}
    for mm_dd, val in FIXED_NATIONAL_HOLIDAYS.items():
        holidays[f"{year:04d}-{mm_dd}"] = val
    for dt_str, val in RELIGIOUS_HOLIDAYS_BY_YEAR.get(year, {}).items():
        # Aynı güne hem ulusal hem dini bayram denk gelirse (örn. 2027-05-19) en büyük değeri al (çift sayma)
        holidays[dt_str] = max(holidays.get(dt_str, 0.0), val)
    return holidays


# Geriye dönük uyumluluk için 2026 sabiti
TR_OFFICIAL_HOLIDAYS_2026: Dict[str, float] = get_official_holidays_for_year(2026)


def norm_tr(val: Optional[str]) -> str:
    """Türkçe karakter duyarlı arama ve eşleştirme normalizasyonu (Büyük I/İ dahil)."""
    if not val:
        return ""
    tr_map = str.maketrans(
        {
            "İ": "i",
            "I": "i",
            "ı": "i",
            "Ş": "s",
            "ş": "s",
            "Ğ": "g",
            "ğ": "g",
            "Ü": "u",
            "ü": "u",
            "Ö": "o",
            "ö": "o",
            "Ç": "c",
            "ç": "c",
        }
    )
    s = str(val).strip().translate(tr_map).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.split())


def upper_tr(val: Optional[str]) -> str:
    """Türkçe kurallarına uygun BÜYÜK HARF dönüşümü."""
    if not val:
        return ""
    s = str(val).strip()
    s = s.replace("i", "İ").replace("ı", "I").replace("ş", "Ş").replace("ğ", "Ğ")
    s = s.replace("ü", "Ü").replace("ö", "Ö").replace("ç", "Ç")
    return s.upper()


def sanitize_dia_keys(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    DİA _getir çıktılarındaki dict tabanlı _key_* yabancı anahtarlarını
    integer _key değerine dönüştürür.
    """
    cleaned: Dict[str, Any] = {}
    for k, v in obj.items():
        if k.startswith("_key_"):
            if isinstance(v, dict):
                cleaned[k] = int(v.get("_key") or 0)
            elif isinstance(v, (int, float)):
                cleaned[k] = int(v)
            elif isinstance(v, str) and v.strip().isdigit():
                cleaned[k] = int(v.strip())
            elif v in ("", None):
                cleaned[k] = 0
            else:
                cleaned[k] = v
        else:
            cleaned[k] = v
    return cleaned


def classify_cost_center(departman: str, unvan: str) -> str:
    """Departman ve unvana göre muhasebe gider profilini belirler."""
    u = norm_tr(unvan)
    d = norm_tr(departman)
    if any(k in u for k in ("mudur", "direktor", "genel koordinator", "c-level", "ceo", "cfo")):
        return "MUDUR"  # 770.001 / 335.02
    if any(k in d for k in ("depo", "operasyon", "pazarlama", "satis", "lojistik", "kargo", "magaza")) or any(
        k in u for k in ("depo", "operasyon", "pazarlama", "satis", "lojistik", "mal kabul", "kategori", "marka")
    ):
        return "PAZARLAMA_DEPO"  # 760.001 / 335.01
    return "GENEL"  # 770.002 / 335.01


def extract_kolay_person_fields(person: Dict[str, Any]) -> Dict[str, Any]:
    """Kolay İK person/view veya bulk-view nesnesini düzleştirir ve DİA alanlarına hazırlar."""
    unit_list = person.get("unitList") or []
    active_units = [u for u in unit_list if u.get("active")] or unit_list
    departman = ""
    unvan = ""
    sirket = "PETLEBİ"
    employment_type = "fulltime"

    if active_units:
        chosen = active_units[-1]
        employment_type = str(chosen.get("employmentType") or "fulltime")
        for item in chosen.get("items") or []:
            uname = norm_tr(item.get("unitName"))
            val = str(item.get("unitItemName") or "").strip()
            if "departman" in uname or "bolum" in uname or "birim" in uname:
                departman = val
            elif "unvan" in uname or "pozisyon" in uname or "gorev" in uname:
                unvan = val
            elif "sirket" in uname or "firma" in uname:
                sirket = val

    data_map: Dict[str, str] = {}
    for d in person.get("dataList") or []:
        token = str(d.get("fieldToken") or "").strip()
        val = str(d.get("value") or "").strip()
        if token and val:
            data_map[token] = val

    first_name = upper_tr(person.get("firstName"))
    last_name = upper_tr(person.get("lastName"))
    status_raw = str(person.get("status") or "active").lower()
    term_date = str(person.get("employmentTerminationDate") or "")[:10]
    durum = "P" if (status_raw != "active" or bool(term_date)) else "A"

    # K2: Kolay İK'da boş olan cinsiyet/medeni hal/il alanlarına sahte varsayılan atama
    gender_raw = str(person.get("gender") or "").strip().lower()
    if gender_raw in ("female", "k", "kadin", "kadın", "woman"):
        cinsiyet = "K"
    elif gender_raw in ("male", "e", "erkek", "man"):
        cinsiyet = "E"
    else:
        cinsiyet = ""

    marital_raw = str(person.get("maritalStatus") or "").strip().lower()
    if marital_raw in ("married", "e", "evli"):
        medenihali = "E"
    elif marital_raw in ("single", "b", "bekar", "unmarried", "divorced", "widowed"):
        medenihali = "B"
    else:
        medenihali = ""

    contract_raw = str(person.get("contractType") or "").strip().lower()
    if contract_raw == "definite":
        sozlesmesekli = "BELIR"
    elif "part" in contract_raw:
        sozlesmesekli = "KISMI"
    elif contract_raw:
        sozlesmesekli = "BELSZ"
    else:
        sozlesmesekli = ""

    term_reason = person.get("employmentTerminationReason") or {}
    if isinstance(term_reason, dict):
        ian_kodu = str(term_reason.get("code") or "").strip()
        ian_aciklama = str(term_reason.get("name") or person.get("employmentTerminationDetails") or "").strip()
    else:
        ian_kodu = str(person.get("employmentTerminationReasonCode") or "").strip()
        ian_aciklama = str(term_reason or person.get("employmentTerminationDetails") or "").strip()

    eposta = str(person.get("workEmail") or person.get("email") or "").strip()
    telefon = str(person.get("mobilePhone") or person.get("workPhone") or "").strip()
    iban = data_map.get("iban", "").replace(" ", "")
    banka_adi = upper_tr(data_map.get("bankaAdi", ""))
    hesap_no = data_map.get("hesapNo", "") or iban

    return {
        "kolayik_id": str(person.get("id") or ""),
        "adi": first_name,
        "soyadi": last_name,
        "ad_soyad": f"{first_name} {last_name}".strip(),
        "tckimlikno": str(person.get("idNumber") or "").strip(),
        "durum": durum,
        "isebaslamatarihi": str(person.get("employmentStartDate") or "")[:10],
        "istenayrilmatarihi": term_date,
        "ian_kodu": ian_kodu,
        "ian_aciklama": ian_aciklama,
        "dogumtarihi": str(person.get("birthday") or "")[:10],
        "cinsiyet": cinsiyet,
        "medenihali": medenihali,
        "sozlesmesekli": sozlesmesekli,
        "eposta": eposta,
        "telefon1": telefon,
        "gorevi": upper_tr(unvan or departman),
        "departman": upper_tr(departman),
        "sirket": upper_tr(sirket),
        "employment_type": employment_type,
        "il": upper_tr(data_map.get("sehir", "")),
        "iletisimadres1": data_map.get("adres", ""),
        "bankaadi": banka_adi,
        "bankahesapno": hesap_no,
        "iban": iban,
        "cocuksayisi": int(person.get("numberOfChildren") or 0),
        "maliyet_profili": classify_cost_center(departman, unvan),
    }


def build_dia_personel_payload(
    staged: Dict[str, Any],
    existing_dia_card: Optional[Dict[str, Any]] = None,
    sgk_ian_key_map: Optional[Dict[str, int]] = None,
    iskur_ian_key_map: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """
    Arayüzde onaylanan personel satırını DİA per_personel_ekle veya
    per_personel_guncelle servisinin beklediği tam şemaya dönüştürür.
    K2 Koruması: Güncellemede Kolay İK'da boş olan alanlar DİA'daki dolu alanları ASLA ezmez.
    K8 Koruması: İşten ayrılan personelde SGK çıkış nedeni zorunludur; sessizce '03-İstifa' yazılmaz.
    """
    ian_map = sgk_ian_key_map or {}
    iskur_map = iskur_ian_key_map or {}
    ian_code = str(staged.get("ian_kodu") or "").strip().zfill(2) if staged.get("ian_kodu") else ""
    ian_key = int(ian_map.get(ian_code, 0)) if ian_code else int(staged.get("_key_per_istenayrilmanedenleri") or 0)

    maas_val = float(staged.get("maas") if staged.get("maas") is not None else 0.0)
    if maas_val < 0:
        raise ValueError("Personel maaşı negatif olamaz!")

    ucretturu = str(staged.get("ucretturu") or "N").upper()
    if ucretturu not in ("N", "B"):
        ucretturu = "N"

    staged_ise_baslama = str(staged.get("isebaslamatarihi") or "").strip()[:10]
    staged_isten_ayrilma = str(staged.get("istenayrilmatarihi") or "").strip()[:10] or None
    staged_dogum = str(staged.get("dogumtarihi") or "").strip()[:10] or None

    if existing_dia_card:
        kart = sanitize_dia_keys(existing_dia_card)
        existing_ise_baslama = str(kart.get("isebaslamatarihi") or "").strip()[:10]
        ise_baslama = staged_ise_baslama or existing_ise_baslama
        dogum_tarihi = staged_dogum if staged_dogum is not None else (kart.get("dogumtarihi") or None)
        eposta = str(staged.get("eposta") or "").strip() or str(kart.get("eposta") or "").strip()
        telefon1 = str(staged.get("telefon1") or "").strip() or str(kart.get("telefon1") or "").strip()
        cinsiyet = str(staged.get("cinsiyet") or "").strip().upper() or str(kart.get("cinsiyet") or "E").upper()
        medenihali = str(staged.get("medenihali") or "").strip().upper() or str(kart.get("medenihali") or "B").upper()
        gorevi = (
            upper_tr(staged.get("gorevi") or staged.get("departman") or "")[:50]
            or str(kart.get("gorevi") or "").strip()[:50]
        )
        raw_sozlesme = str(staged.get("sozlesmesekli") or kart.get("sozlesmesekli") or "BELSZ").upper()
    else:
        if not staged_ise_baslama:
            raise ValueError(
                f"{staged.get('adi', '')} {staged.get('soyadi', '')} için İşe Başlama Tarihi zorunludur!"
            )
        ise_baslama = staged_ise_baslama
        dogum_tarihi = staged_dogum
        eposta = str(staged.get("eposta") or "").strip()
        telefon1 = str(staged.get("telefon1") or "").strip()
        cinsiyet = str(staged.get("cinsiyet") or "E").strip().upper() or "E"
        medenihali = str(staged.get("medenihali") or "B").strip().upper() or "B"
        gorevi = upper_tr(staged.get("gorevi") or staged.get("departman") or "")[:50]
        raw_sozlesme = str(staged.get("sozlesmesekli") or "BELSZ").upper()
        kart = {
            "sigortano": "--",
            "verginumarasi": "--",
            "ogrenimdurumu": 0,
            "askerlikdurumu": "TRHS",
            "sozlesmebitistarihi": "2099-12-31",
            "odemeturu": "A",
            "odemesekli": "B" if staged.get("bankahesapno") or staged.get("iban") else "N",
            "sigortagrubu": 2,
            "gorevkodu": 6,
            "sosyalguvenlikkuruluskodu": 1,
            "sakatlikderecesi": 1,
            "uyrugu": "TC",
            "issizliksigortasinatabi": "E",
            "eskihukumlu": "H",
            "gelirvergisihesaplanacak": "E",
            "damgavergisihesaplanacak": "E",
            "sigortahesaplanacak": "E",
            "terormagduru": "H",
            "agiuygulansin": "E",
            "tabioldugukanun": "00000",
            "asgariucretli": "H",
            "sigortakolu": "0",
            "csgbiskolu": "00",
            "otuzgundenaz": "H",
            "belgeturu": "01",
            "bes_durumu": "H",
            "asgariucretistisnasi": "E",
            "_key_sis_sube": settings.DIA_MERKEZ_SUBE_KEY,
            "_key_sis_doviz": settings.DIA_TL_DOVIZ_KEY,
            "_key_sis_ulkeler": settings.DIA_TURKIYE_ULKE_KEY,
            "_key_per_mesaigunleri": settings.DIA_MESAI_GUNLERI_KEY,
            "_key_per_izingrubu": settings.DIA_IZIN_GRUBU_KEY,
        }

    if raw_sozlesme not in ("BELIR", "BELSZ", "KISMI"):
        raw_sozlesme = "BELSZ"

    durum_val = str(staged.get("durum") or "A").upper()
    if staged_isten_ayrilma:
        durum_val = "P"

    kart.update(
        {
            "sicilno": str(staged.get("sicilno") or kart.get("sicilno") or "").strip(),
            "adi": upper_tr(staged.get("adi") or kart.get("adi") or ""),
            "soyadi": upper_tr(staged.get("soyadi") or kart.get("soyadi") or ""),
            "tckimlikno": str(staged.get("tckimlikno") or kart.get("tckimlikno") or "").strip(),
            "durum": durum_val,
            "gorevi": gorevi,
            "eposta": eposta,
            "telefon1": telefon1,
            "isebaslamatarihi": ise_baslama,
            "istenayrilmatarihi": staged_isten_ayrilma if durum_val == "P" else None,
            "dogumtarihi": dogum_tarihi,
            "cinsiyet": cinsiyet if cinsiyet in ("E", "K") else "E",
            "medenihali": medenihali if medenihali in ("E", "B") else "B",
            "sozlesmesekli": raw_sozlesme,
            "maas": f"{maas_val:.6f}",
            "ucretturu": ucretturu,
            "bankaadi": upper_tr(staged.get("bankaadi") or kart.get("bankaadi") or ""),
            "bankahesapno": str(staged.get("bankahesapno") or staged.get("iban") or kart.get("bankahesapno") or ""),
            "il": upper_tr(staged.get("il") or kart.get("il") or ""),
            "aciklama1": str(staged.get("departman") or kart.get("aciklama1") or "")[:100],
            "_key_sis_sube": int(staged.get("_key_sis_sube") or kart.get("_key_sis_sube") or settings.DIA_MERKEZ_SUBE_KEY),
            "_key_sis_doviz": int(staged.get("_key_sis_doviz") or kart.get("_key_sis_doviz") or settings.DIA_TL_DOVIZ_KEY),
        }
    )

    if durum_val == "P":
        existing_ian_key = int(kart.get("_key_per_istenayrilmanedenleri") or 0) if existing_dia_card else 0
        final_ian_key = ian_key if ian_key > 0 else existing_ian_key
        if final_ian_key <= 0:
            raise ValueError(
                f"{kart['adi']} {kart['soyadi']} işten ayrılmış (P) durumda ancak geçerli bir SGK İşten Ayrılma Nedeni Kodu seçilmemiş! "
                "Lütfen tablodan SGK Çıkış Nedenini seçin."
            )
        kart["_key_per_istenayrilmanedenleri"] = final_ian_key
        iskur_key = (
            int(iskur_map.get(ian_code, 0))
            if ian_code and ian_code in iskur_map
            else int(
                staged.get("_key_per_istenayrilmanedenleri_iskur")
                or (kart.get("_key_per_istenayrilmanedenleri_iskur") if existing_dia_card else 0)
                or 0
            )
        )
        kart["_key_per_istenayrilmanedenleri_iskur"] = iskur_key
    else:
        kart["istenayrilmatarihi"] = None
        kart["_key_per_istenayrilmanedenleri"] = 0
        kart["_key_per_istenayrilmanedenleri_iskur"] = 0

    if staged.get("dia_key"):
        kart["_key"] = int(staged["dia_key"])

    return kart


def get_month_holiday_days(
    year: int,
    month: int,
    exclude_sundays: bool = False,
    start_day: Optional[int] = None,
    end_day: Optional[int] = None,
) -> float:
    """
    İlgili ay (veya gün aralığı) içindeki resmi tatil gün sayısını döndürür.
    exclude_sundays=True ise Pazar gününe (hafta tatiline) denk gelen resmi tatilleri
    iki kez düşmemek için hariç tutar.
    """
    holidays = get_official_holidays_for_year(year)
    prefix = f"{year:04d}-{month:02d}-"
    total = 0.0
    for dt_str, days in holidays.items():
        if not dt_str.startswith(prefix):
            continue
        dt_obj = datetime.date.fromisoformat(dt_str)
        if start_day is not None and dt_obj.day < start_day:
            continue
        if end_day is not None and dt_obj.day > end_day:
            continue
        if exclude_sundays and dt_obj.weekday() == 6:
            continue
        total += days
    return total


def calculate_employee_month_days(
    year: int,
    month: int,
    ise_baslama_str: str = "",
    isten_ayrilma_str: str = "",
    ucretli_izin: float = 0.0,
    ucretsiz_izin: float = 0.0,
    rapor_gun: float = 0.0,
) -> Dict[str, float]:
    """
    K5 Çözümü: Personelin ay içindeki işe giriş / işten çıkış tarihlerini (kıstelyevm),
    gerçek Pazar (hafta tatili) sayılarını ve Pazar ile çakışmayan resmi tatil günlerini
    dikkate alarak puantaj günlerini hesaplar.
    """
    last_day_num = calendar.monthrange(year, month)[1]
    month_start = datetime.date(year, month, 1)
    month_end = datetime.date(year, month, last_day_num)

    active_start = month_start
    active_end = month_end

    if ise_baslama_str:
        try:
            emp_in = datetime.date.fromisoformat(ise_baslama_str[:10])
            if emp_in > active_start:
                active_start = emp_in
        except ValueError:
            pass

    if isten_ayrilma_str:
        try:
            emp_out = datetime.date.fromisoformat(isten_ayrilma_str[:10])
            if emp_out < active_end:
                active_end = emp_out
        except ValueError:
            pass

    if active_start > month_end or active_end < month_start or active_start > active_end:
        return {
            "active_in_month": 0.0,
            "base_days": 0.0,
            "normalmesaigun": 0.0,
            "haftasonugun": 0.0,
            "resmitatilgun": 0.0,
            "toplamsigortagun": 0.0,
            "toplambordrogun": 0.0,
        }

    is_full_month = active_start == month_start and active_end == month_end
    base_days = 30.0 if is_full_month else float((active_end - active_start).days + 1)

    # Aktif aralıktaki Pazar (hafta tatili) günlerini say
    sunday_count = 0.0
    cur = active_start
    while cur <= active_end:
        if cur.weekday() == 6:
            sunday_count += 1.0
        cur += datetime.timedelta(days=1)

    haftasonu_gun = sunday_count
    # Pazar gününe denk gelen resmi tatiller hafta tatilinde zaten sayıldığı için ikinci kez düşülmez
    resmitatil_gun = get_month_holiday_days(
        year,
        month,
        exclude_sundays=True,
        start_day=active_start.day,
        end_day=active_end.day,
    )

    sigorta_gun = max(0.0, min(30.0, base_days - ucretsiz_izin - rapor_gun))
    normal_gun = max(
        0.0,
        sigorta_gun - haftasonu_gun - resmitatil_gun - ucretli_izin,
    )
    # Eğer izinler toplamı sigorta gününü aşarsa hafta sonu / resmi tatili de taşma kadar koru
    bordro_gun = sigorta_gun

    return {
        "active_in_month": 1.0,
        "base_days": base_days,
        "normalmesaigun": round(normal_gun, 2),
        "haftasonugun": round(haftasonu_gun, 2),
        "resmitatilgun": round(resmitatil_gun, 2),
        "toplamsigortagun": round(sigorta_gun, 2),
        "toplambordrogun": round(bordro_gun, 2),
    }


def classify_overtime_date(date_str: str) -> str:
    """
    PDKS fazla mesai tarihine göre DİA puantaj mesai türünü belirler:
    - Resmi tatil -> 'resmitatilmesaisisaat'
    - Cumartesi/Pazar -> 'haftasonumesaisisaat'
    - Hafta içi -> 'fazlamesai1saat'
    """
    dt_part = str(date_str or "")[:10]
    try:
        dt = datetime.date.fromisoformat(dt_part)
        holidays = get_official_holidays_for_year(dt.year)
        if dt_part in holidays:
            return "resmitatilmesaisisaat"
        if dt.weekday() in (5, 6):
            return "haftasonumesaisisaat"
    except ValueError:
        if dt_part in TR_OFFICIAL_HOLIDAYS_2026:
            return "resmitatilmesaisisaat"
    return "fazlamesai1saat"


def classify_leave_for_dia(leave_type_name: str, is_paid: bool) -> str:
    """
    Kolay İK izin türünü DİA puantaj gün alanına eşleştirir:
    - Rapor / Hastalık -> 'doktorraporugun'
    - Evden / Uzaktan Çalışma -> 'uzaktancalismagunsayisi'
    - Ücretsiz / Mazeret (isPaid=False) -> 'izinliucretsizgun'
    - Yıllık İzin / Ücretli -> 'izinliucretligun'
    """
    n = norm_tr(leave_type_name)
    if "rapor" in n or "hastalik" in n or "istirahat" in n:
        return "doktorraporugun"
    if "evden" in n or "uzaktan" in n:
        return "uzaktancalismagunsayisi"
    if not is_paid or "ucretsiz" in n:
        return "izinliucretsizgun"
    return "izinliucretligun"


def is_annual_leave_for_dia_module(leave_type_name: str, is_paid: bool, target_field: Optional[str] = None) -> bool:
    """
    K4 Koruması: Yalnızca Yıllık İzin kayıtları DİA per_izinler_ekle (Yıllık İzin Kartı)
    servisine gönderilebilir. Rapor, Ücretsiz İzin ve Uzaktan Çalışma yalnızca Puantaja yansır.
    """
    tf = target_field or classify_leave_for_dia(leave_type_name, is_paid)
    if tf != "izinliucretligun":
        return False
    n = norm_tr(leave_type_name)
    if any(k in n for k in ("rapor", "hastalik", "istirahat", "ucretsiz", "evden", "uzaktan")):
        return False
    return True


def build_dia_puantaj_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Arayüzde düzenlenen puantaj satırını DİA per_personel_puantaj_ekle / guncelle
    servisinin beklediği tam şemaya dönüştürür (Gün * 7.5 saat kuralı + Doğrulama).
    """
    year = int(row.get("duzenlemeyil") or datetime.date.today().year)
    month = int(row.get("duzenlemeayno") or datetime.date.today().month)
    if not (1 <= month <= 12):
        raise ValueError(f"Geçersiz puantaj ayı: {month}")

    last_day = calendar.monthrange(year, month)[1]
    duzenlemetarihi = str(row.get("duzenlemetarihi") or f"{year:04d}-{month:02d}-{last_day:02d}")

    normal_gun = float(row.get("normalmesaigun") or 0.0)
    haftasonu_gun = float(row.get("haftasonugun") or 0.0)
    resmitatil_gun = float(row.get("resmitatilgun") or 0.0)
    ucretli_izin_gun = float(row.get("izinliucretligun") or 0.0)
    ucretsiz_izin_gun = float(row.get("izinliucretsizgun") or 0.0)
    rapor_gun = float(row.get("doktorraporugun") or 0.0)
    uzaktan_gun = float(row.get("uzaktancalismagunsayisi") or 0.0)

    sigorta_gun = float(
        row.get("toplamsigortagun")
        if row.get("toplamsigortagun") is not None
        else (normal_gun + haftasonu_gun + resmitatil_gun + ucretli_izin_gun)
    )
    bordro_gun = float(
        row.get("toplambordrogun")
        if row.get("toplambordrogun") is not None
        else sigorta_gun
    )

    fm1_saat = float(row.get("fazlamesai1saat") or 0.0)
    hs_mesai_saat = float(row.get("haftasonumesaisisaat") or 0.0)
    rt_mesai_saat = float(row.get("resmitatilmesaisisaat") or 0.0)
    toplam_fm_saat = fm1_saat + hs_mesai_saat + rt_mesai_saat

    maas_val = float(row.get("maas") or 0.0)
    ucretturu = str(row.get("ucretturu") or "N").upper()
    if ucretturu not in ("N", "B"):
        ucretturu = "N"

    # Sunucu tarafı doğrulama (Negatif değer ve >30 SGK günü engeli)
    if maas_val < 0:
        raise ValueError("Puantaj maaş tutarı negatif olamaz!")
    for label, val in (
        ("Normal Mesai Günü", normal_gun),
        ("Hafta Sonu Günü", haftasonu_gun),
        ("Resmi Tatil Günü", resmitatil_gun),
        ("Ücretli İzin Günü", ucretli_izin_gun),
        ("Ücretsiz İzin Günü", ucretsiz_izin_gun),
        ("Rapor Günü", rapor_gun),
        ("Uzaktan Çalışma Günü", uzaktan_gun),
        ("Fazla Mesai Saati", fm1_saat),
        ("Hafta Sonu Mesai Saati", hs_mesai_saat),
        ("Resmi Tatil Mesai Saati", rt_mesai_saat),
    ):
        if val < 0:
            raise ValueError(f"{label} negatif olamaz ({val})!")

    if not (0.0 <= sigorta_gun <= 30.0) or not (0.0 <= bordro_gun <= 30.0):
        raise ValueError(
            f"Toplam SGK ({sigorta_gun}) ve Bordro ({bordro_gun}) günü 0 ile 30 gün arasında olmalıdır!"
        )

    kart: Dict[str, Any] = {
        "_key_per_personel": int(row["_key_per_personel"]),
        "_key_sis_sube": int(row.get("_key_sis_sube") or settings.DIA_MERKEZ_SUBE_KEY),
        "_key_sis_doviz": int(row.get("_key_sis_doviz") or settings.DIA_TL_DOVIZ_KEY),
        "duzenlemetarihi": duzenlemetarihi,
        "maas": f"{maas_val:.6f}",
        "ucretturu": ucretturu,
        "odemeturu": "A",
        "dovizkuru": "1.000000",
        "normalmesaigun": f"{normal_gun:.6f}",
        "normalmesaisaat": f"{normal_gun * 7.5:.6f}",
        "haftasonugun": f"{haftasonu_gun:.6f}",
        "haftasonusaat": f"{haftasonu_gun * 7.5:.6f}",
        "resmitatilgun": f"{resmitatil_gun:.6f}",
        "resmitatilsaat": f"{resmitatil_gun * 7.5:.6f}",
        "izinliucretligun": f"{ucretli_izin_gun:.6f}",
        "izinliucretlisaat": f"{ucretli_izin_gun * 7.5:.6f}",
        "izinliucretsizgun": f"{ucretsiz_izin_gun:.6f}",
        "izinliucretsizsaat": f"{ucretsiz_izin_gun * 7.5:.6f}",
        "doktorraporugun": f"{rapor_gun:.6f}",
        "doktorraporusaat": f"{rapor_gun * 7.5:.6f}",
        "uzaktancalismagunsayisi": f"{uzaktan_gun:.6f}",
        "toplamsigortagun": f"{sigorta_gun:.6f}",
        "toplamsigortasaat": f"{sigorta_gun * 7.5:.6f}",
        "toplambordrogun": f"{bordro_gun:.6f}",
        "toplambordrosaat": f"{bordro_gun * 7.5:.6f}",
        "toplamfazlamesaisaat": f"{toplam_fm_saat:.6f}",
        "fazlamesai1saat": f"{fm1_saat:.6f}",
        "fazlamesai1ucretturu": "B",
        "haftasonumesaisisaat": f"{hs_mesai_saat:.6f}",
        "haftasonumesaisiucretturu": "B",
        "resmitatilmesaisisaat": f"{rt_mesai_saat:.6f}",
        "resmitatilmesaisiucretturu": "B",
    }
    if row.get("dia_puantaj_key"):
        kart["_key"] = int(row["dia_puantaj_key"])
    return kart


def build_dia_izin_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Arayüzde onaylanan izin satırını DİA per_izinler_ekle şemasına dönüştürür.
    K4 Koruması: Rapor, Ücretsiz İzin ve Uzaktan Çalışma kayıtlarının Yıllık İzin kartına
    yazılarak yıllık izin bakiyesini düşmesini engeller.
    """
    izin_turu = str(row.get("izin_turu") or "")
    ucretli_mi = bool(row.get("ucretli_mi", True))
    hedef_alan = str(row.get("hedef_puantaj_alani") or "")
    if (izin_turu or hedef_alan) and not is_annual_leave_for_dia_module(izin_turu or "Yıllık İzin", ucretli_mi, hedef_alan or None):
        raise ValueError(
            f"'{izin_turu or hedef_alan}' kaydı Yıllık İzin bakiyesini bozmaması için DİA İzin Modülüne (per_izinler_ekle) "
            "aktarılamaz; yalnızca Aylık Puantaj günlerine yansıtılır."
        )

    personel_key = int(row["_key_per_personel"])
    izin_kart_key = int(row.get("_key_per_izinkarti") or settings.DIA_YILLIK_IZIN_KART_KEY)
    baslangic = str(row.get("baslangictarihi") or "")[:10]
    bitis = str(row.get("bitistarihi") or "")[:10]
    if not baslangic or not bitis or baslangic > bitis:
        raise ValueError(f"Geçersiz izin başlangıç/bitiş tarihi: {baslangic} - {bitis}")

    raw_days = row.get("izinligunsayisi")
    gun_sayisi = float(raw_days) if raw_days is not None else 0.0
    if gun_sayisi <= 0:
        raise ValueError("İzin gün sayısı 0'dan büyük olmalıdır!")

    sicilno = str(row.get("sicilno") or "").strip()

    kart: Dict[str, Any] = {
        "islemturu": "P",
        "izindurumu": "G",
        "baslangictarihi": baslangic,
        "baslangicsaati": "00:00:00",
        "bitistarihi": bitis,
        "bitissaati": "23:59:59",
        "izinligunsayisi": f"{gun_sayisi:.6f}",
        "aciklama": str(row.get("aciklama") or "Kolay İK Yıllık İzin Aktarımı")[:200],
        "bordroyaaktar": str(row.get("bordroyaaktar") or "E"),
        "onaytarihi": baslangic,
        "onaydurumu": "O",
        "izineklecikar": "Y",
        "sicilno": sicilno,
        "personelsicilno": sicilno,
        "personeladisoyadi": str(row.get("ad_soyad") or "").strip(),
        "_key_per_personel": personel_key,
        "_key_per_izinkarti": izin_kart_key,
        "_key_sis_sube": int(row.get("_key_sis_sube") or settings.DIA_MERKEZ_SUBE_KEY),
    }
    # K4: Personelin kendisini onaylayan olarak atama; yalnız açıkça onaylayan _key verilirse ekle
    onaylayan_key = int(row.get("_key_per_personel_onaylayan") or 0)
    if onaylayan_key > 0 and onaylayan_key != personel_key:
        kart["_key_per_personel_onaylayan"] = onaylayan_key

    return kart


def build_dia_mahsup_payload(
    tarih: str,
    fisno: str,
    aciklama: str,
    lines: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Arayüzde onaylanan muhasebe mahsup fişi satırlarını
    DİA muh_muhasebefisi_ekle şemasına dönüştürür.
    """
    total_borc = round(sum(float(x.get("borc") or 0.0) for x in lines), 2)
    total_alacak = round(sum(float(x.get("alacak") or 0.0) for x in lines), 2)
    diff = round(total_borc - total_alacak, 2)
    if abs(diff) > 0.02:
        raise ValueError(
            f"Mahsup fişi Borç ({total_borc:,.2f} TL) ve Alacak ({total_alacak:,.2f} TL) toplamları eşit değil!"
        )

    m_kalemler: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        borc_val = round(float(line.get("borc") or 0.0), 2)
        alacak_val = round(float(line.get("alacak") or 0.0), 2)
        if borc_val < 0 or alacak_val < 0:
            raise ValueError("Mahsup fişi satır tutarları negatif olamaz!")
        if borc_val == 0.0 and alacak_val == 0.0:
            continue
        hesap_kodu = str(line.get("hesapkodu") or "").strip()
        hesap_info = settings.DIA_MUH_HESAPLAR.get(hesap_kodu, {})
        hesap_key = int(line.get("_key_muh_hesapkarti") or hesap_info.get("_key") or 0)
        if not hesap_key:
            raise ValueError(f"Muhasebe hesap anahtarı (_key_muh_hesapkarti) bulunamadı: {hesap_kodu}")

        m_kalemler.append(
            {
                "_key_muh_hesapkarti": hesap_key,
                "_key_sis_doviz": settings.DIA_TL_DOVIZ_KEY,
                "dovizkuru": "1.000000",
                "borc": f"{borc_val:.6f}",
                "alacak": f"{alacak_val:.6f}",
                "yerelborc": f"{borc_val:.6f}",
                "yerelalacak": f"{alacak_val:.6f}",
                "kdvdurumu": "H",
                "kdvyuzde": "0.000000",
                "mfkturu": "A",
                "aciklama": str(line.get("aciklama") or aciklama)[:200],
                "linenumber": idx,
                "kalemsirano": idx,
            }
        )

    if not m_kalemler:
        raise ValueError("Mahsup fişinde sıfırdan büyük en az 1 borç ve 1 alacak satırı bulunmalıdır!")

    if diff != 0.0 and m_kalemler:
        for item in reversed(m_kalemler):
            cur_alacak = float(item["alacak"])
            if cur_alacak > 0:
                adj_val = f"{round(cur_alacak + diff, 2):.6f}"
                item["alacak"] = adj_val
                item["yerelalacak"] = adj_val
                break

    return {
        "_key_sis_sube": settings.DIA_MERKEZ_SUBE_KEY,
        "_key_sis_doviz_raporlama": settings.DIA_TL_DOVIZ_KEY,
        "raporlamadovizkuru": "1.000000",
        "turu": "1",  # 1 = Mahsup Fişi
        "tarih": tarih[:10],
        "saat": datetime.datetime.now().strftime("%H:%M:%S"),
        "fisno": fisno,
        "aciklama1": aciklama[:200],
        "m_kalemler": m_kalemler,
    }
