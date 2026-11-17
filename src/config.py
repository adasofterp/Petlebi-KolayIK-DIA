"""Merkezi yapılandırma ve DİA ERP sabitleri."""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _read_version() -> str:
    vf = ROOT_DIR / "version.json"
    if vf.exists():
        try:
            return str(json.loads(vf.read_text(encoding="utf-8")).get("version") or "1.1.0")
        except Exception:
            pass
    return "1.1.0"


class Settings:
    APP_NAME: str = "Petlebi Kolay İK ↔ DİA ERP Entegrasyon"
    APP_VERSION: str = _read_version()
    TRUSTED_GITHUB_REPO: str = "adasofterp/Petlebi-KolayIK-DIA"
    GITHUB_REPO: str = os.getenv("GITHUB_REPO", "").strip() or TRUSTED_GITHUB_REPO

    # Kolay İK
    KOLAYIK_BASE_URL: str = os.getenv("KOLAYIK_BASE_URL", "https://api.kolayik.com").rstrip("/")
    KOLAYIK_API_TOKEN: str = os.getenv("KOLAYIK_API_TOKEN", "").strip()

    # DİA ERP
    DIA_BASE_URL: str = os.getenv("DIA_BASE_URL", "https://ws.dia.com.tr/api/v3").rstrip("/")
    DIA_USERNAME: str = os.getenv("DIA_USERNAME", "").strip()
    DIA_PASSWORD: str = os.getenv("DIA_PASSWORD", "").strip()
    DIA_APIKEY: str = os.getenv("DIA_APIKEY", "").strip()
    DIA_FIRMA_KODU: int = int(os.getenv("DIA_FIRMA_KODU", "1"))
    DIA_DONEM_KODU: int = int(os.getenv("DIA_DONEM_KODU", "1"))
    DIA_DISCONNECT_SAME_USER: str = os.getenv("DIA_DISCONNECT_SAME_USER", "False").strip() or "False"

    # Zorunlu Foreign Key (_key) Sabitleri (Ayarlar veya .env üzerinden değiştirilebilir)
    DIA_MERKEZ_SUBE_KEY: int = int(os.getenv("DIA_MERKEZ_SUBE_KEY", "2279"))
    DIA_TL_DOVIZ_KEY: int = int(os.getenv("DIA_TL_DOVIZ_KEY", "2272"))
    DIA_TURKIYE_ULKE_KEY: int = int(os.getenv("DIA_TURKIYE_ULKE_KEY", "1755"))
    DIA_MESAI_GUNLERI_KEY: int = int(os.getenv("DIA_MESAI_GUNLERI_KEY", "17202"))
    DIA_IZIN_GRUBU_KEY: int = int(os.getenv("DIA_IZIN_GRUBU_KEY", "17127"))
    DIA_YILLIK_IZIN_KART_KEY: int = int(os.getenv("DIA_YILLIK_IZIN_KART_KEY", "17223"))

    # DİA Ek Ödeme / Kesinti Varsayılan Kart Anahtarları (_key_per_odemekesinti)
    DIA_AVANS_KESINTI_KEY: int = int(os.getenv("DIA_AVANS_KESINTI_KEY", "17152"))
    DIA_PRIM_KAZANC_KEY: int = int(os.getenv("DIA_PRIM_KAZANC_KEY", "17145"))

    # DİA Ek Kazanç Kartları (_key_per_ekkazanc)
    DIA_EK_KAZANC_KEYS = {
        "01": {"_key": 17145, "kodu": "01", "aciklama": "Prim"},
        "02": {"_key": 17146, "kodu": "02", "aciklama": "İkramiye"},
        "03": {"_key": 17147, "kodu": "03", "aciklama": "Ek Görev Ücreti"},
        "04": {"_key": 17148, "kodu": "04", "aciklama": "Bayram İkramiyesi"},
        "05": {"_key": 17149, "kodu": "05", "aciklama": "Sakatlık ve Hastalık Tazminatı"},
        "06": {"_key": 17150, "kodu": "06", "aciklama": "Özel Hizmet Tazminatı"},
        "07": {"_key": 17151, "kodu": "07", "aciklama": "Mali Sorumluluk Ödeneği"},
    }

    # DİA Sosyal Yardım Kartları (_key_per_sosyalyardim)
    DIA_SOSYAL_YARDIM_KEYS = {
        "01": {"_key": 17163, "kodu": "01", "aciklama": "Yol Parası"},
        "02": {"_key": 17164, "kodu": "02", "aciklama": "Yemek Parası"},
        "03": {"_key": 17165, "kodu": "03", "aciklama": "Kira Yardımı"},
        "04": {"_key": 17166, "kodu": "04", "aciklama": "Giyecek Yardımı"},
        "05": {"_key": 17167, "kodu": "05", "aciklama": "Yakacak Yardımı"},
        "06": {"_key": 17168, "kodu": "06", "aciklama": "Kreş Ücreti"},
    }

    # DİA Muhasebe Hesap Planı Eşleşmeleri (_key_muh_hesapkarti)
    DIA_MUH_HESAPLAR = {
        "195.01.01": {"_key": 342918, "hesapkodu": "195.01.01", "aciklama": "VERİLEN DİĞER TİCARİ TL AVANSLARI"},
        "196.001": {"_key": 5911, "hesapkodu": "196.001", "aciklama": "PERSONEL AVANSLARI"},
        "335.01": {"_key": 348866, "hesapkodu": "335.01", "aciklama": "PERSONEL ÜCRETLERİ"},
        "335.02": {"_key": 348989, "hesapkodu": "335.02", "aciklama": "MÜDÜR ÜCRETLERİ"},
        "335.03": {"_key": 348870, "hesapkodu": "335.03", "aciklama": "İCRA ÖDEMELERİ"},
        "335.04": {"_key": 348872, "hesapkodu": "335.04", "aciklama": "AGESA BES ÖDEMELERİ"},
        "335.05": {"_key": 348909, "hesapkodu": "335.05", "aciklama": "TAZMİNAT ÖDEMELERİ"},
        "335.06": {"_key": 4312023, "hesapkodu": "335.06", "aciklama": "YOL ÜCRETİ ÖDEMELERİ"},
        "360.001.002": {"_key": 6208, "hesapkodu": "360.001.002", "aciklama": "ÖDENECEK DAMGA VERGİSİ"},
        "360.003.001": {"_key": 4489762, "hesapkodu": "360.003.001", "aciklama": "ÖDENECEK STOPAJLAR"},
        "361.001": {"_key": 6226, "hesapkodu": "361.001", "aciklama": "ÖDENECEK SGK İŞVEREN PRİMİ"},
        "361.002": {"_key": 349721, "hesapkodu": "361.002", "aciklama": "ÖDENECEK SGK İŞVEREN İŞSİZLİK PRİMİ"},
        "770.001.001": {"_key": 7824, "hesapkodu": "770.001.001", "aciklama": "MÜDÜR - BRÜT ÜCRETLER"},
        "770.001.002": {"_key": 7826, "hesapkodu": "770.001.002", "aciklama": "MÜDÜR - SGK İŞVEREN PAYI"},
        "770.001.003": {"_key": 7828, "hesapkodu": "770.001.003", "aciklama": "MÜDÜR - SGK İŞVEREN İŞSİZLİK PAYI"},
        "770.002.001": {"_key": 7853, "hesapkodu": "770.002.001", "aciklama": "PERSONEL - BRÜT ÜCRETLER"},
        "770.002.002": {"_key": 7855, "hesapkodu": "770.002.002", "aciklama": "PERSONEL - SGK İŞVEREN PAYI"},
        "770.002.003": {"_key": 7857, "hesapkodu": "770.002.003", "aciklama": "PERSONEL - SGK İŞVEREN İŞSİZLİK PAYI"},
        "760.001.001": {"_key": 6721, "hesapkodu": "760.001.001", "aciklama": "PAZARLAMA/DEPO - BRÜT ÜCRETLER"},
        "760.001.002": {"_key": 6723, "hesapkodu": "760.001.002", "aciklama": "PAZARLAMA/DEPO - SGK İŞVEREN PAYI"},
        "760.001.003": {"_key": 6725, "hesapkodu": "760.001.003", "aciklama": "PAZARLAMA/DEPO - SGK İŞVEREN İŞSİZLİK PAYI"},
    }


settings = Settings()
