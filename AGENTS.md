# Kolay İK ↔ DİA Cloud ERP Entegrasyon Projesi

Bu proje, İK süreçlerinin yürütüldüğü **Kolay İK** (`https://api.kolayik.com/v2`) ile muhasebe ve bordro süreçlerinin yürütüldüğü **DİA Cloud ERP** (`https://<sunucu>.ws.dia.com.tr/api/v3`) arasında kontrollü veri eşleştirme ve aktarım sağlamak için oluşturulmuştur.

---

## 1. DİA ERP Teknik Kurallar ve Sabitler

* **Sunucu Adresi:** `https://<sunucu>.ws.dia.com.tr/api/v3/{modul}/json` (`sis`, `per`, `scf`, `muh`, `bcs`)
* **Firma / Dönem:** `firma_kodu: 1`, `donem_kodu: 1` (Ayarlar ekranından değiştirilebilir)
* **Kritik Yabancı Anahtar (Foreign Key) Kuralı:**
  DİA ortamında birden fazla firma tanımlı olduğunda `{"subekodu": "00000001"}` gibi dict bazlı referanslar `"Kritere uygun birden fazla kayıt var!"` hatası verir. Kayıt ekleme/güncelleme servislerinde daima **integer `_key`** kullanılmalıdır:
  * `_key_sis_sube`: `2279` (`00000001 - MERKEZ`)
  * `_key_sis_doviz`: `2272` (`TL - Türk Lirası`)
  * `_key_per_personel`: Personelin DİA'daki integer `_key` değeri (örn. `00000001 - ÖRNEK PERSONEL` -> `10001`)
* **DİA Personel Puantaj (`per_personel_puantaj_ekle` / `per_personel_puantaj_guncelle`) Davranışı:**
  DİA'ya `maas`, `ucretturu` (`"B"` Brüt / `"N"` Net), `normalmesaigun`, `normalmesaisaat`, `haftasonugun`, `haftasonusaat`, `resmitatilgun`, `resmitatilsaat`, `izinliucretligun`, `izinliucretsizgun`, `doktorraporugun`, `toplamsigortagun`, `toplamsigortasaat`, `toplambordrogun`, `toplambordrosaat` gönderildiğinde; DİA arka planda SGK işçi/işveren paylarını, gelir/damga vergisini, asgari ücret GV/DV istisnalarını ve `ayliknetkazanc` tutarını **otomatik olarak hesaplamaktadır**. (Günlük standart çalışma süresi 7,5 saat; 30 tam gün = 225,00 saattir).

---

## 2. Kolay İK Public API (`v2`) Uç Noktaları

* **Resmi Dokümantasyon:** `https://apidocs.kolayik.com/`
* **Base URL:** `https://api.kolayik.com`
* **Kimlik Doğrulama:** `Authorization: Bearer <KOLAYIK_API_TOKEN>`

### Başlıca Servis Eşleşmeleri (Kolay İK ⟷ DİA ERP)

| Modül | Kolay İK Endpoint | DİA ERP Servisi | Eşleştirme Anahtarı ve Notlar |
| :--- | :--- | :--- | :--- |
| **Personel (Özlük)** | `POST /v2/person/list`<br>`POST /v2/person/bulk-view`<br>`GET /v2/person/view/:id` | `per_personel_listele`<br>`per_personel_ekle`<br>`per_personel_guncelle` | `idNumber` (TC Kimlik No) / `registrationNumber` (`sicilno`). İşe giriş (`employmentStartDate`), işten çıkış (`employmentTerminationDate`), zorunlu SGK çıkış nedeni, IBAN, maaş senkronizasyonu. |
| **İzin ve Raporlar** | `GET /v2/leave/list?status=approved&startDate=...&endDate=...` | `per_izinler_ekle`<br>`per_personel_puantaj_ekle` | Yıllık izinler `per_izinler_ekle` ve `izinliucretligun` alanına; ücretsiz izin (`izinliucretsizgun`), hastalık/rapor (`doktorraporugun`) ve uzaktan çalışma (`uzaktancalismagunsayisi`) ise yıllık izin bakiyesini korumak için yalnız puantaja yansıtılır. |
| **Fazla Mesai (Timelog)** | `POST /v2/timelog/list` (`type=overtime`, `status=approved`) | `per_personel_puantaj_ekle` | `usedMinute` -> saat dönüşümü ile `fazlamesai1saat`, `haftasonumesaisisaat`, `resmitatilmesaisisaat`. |
| **Avans / Masraf / Prim / Kesinti** | `POST /v2/transaction/list` (`allowance`, `advancePayment`, `bonus`, `premium`, `otherCut`) | `per_ekodemekesinti_ekle`<br>`muh_muhasebefisi_ekle` | Personel avans/kesinti ve prim hareketlerinin hem bordro ek ödeme/kesinti fişine (`per_ekodemekesinti_ekle`) hem de `196.001 Personel Avansları` / `335.01 Personel Ücretleri` muhasebe mahsup fişine işlenmesi. |
| **Aylık Puantaj & Mahsup** | Konsolide Dönem Verisi | `per_personel_puantaj_ekle`<br>`muh_muhasebefisi_ekle` | DİA Puantaj tahakkuk sonuçlarından `770 / 760 / 335 / 360 / 361 / 196.001` Maaş Tahakkuk Mahsup Fişi üretimi. |
