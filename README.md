# Petlebi — Kolay İK ↔ DİA Cloud ERP Entegrasyon Uygulaması

Petlebi İK süreçleri (**Kolay İK `v2`**) ile muhasebe/bordro süreçleri (**DİA Cloud ERP `v3`**) arasında **kontrollü (On-Demand)** veri karşılaştırma, satır içi düzenleme ve aktarım sağlayan masaüstü/web uygulaması.

---

## Özellikler ve İş Akışı

1. **Otomatik Aktarım Yok — Tam Kullanıcı Kontrolü:**
   - Kullanıcı tarih aralığı seçip **"Kolay İK'dan Verileri Çağır & DİA İle Karşılaştır"** butonuna bastığında veriler çekilir.
   - Tablo üzerinde tüm DİA şema alanları (`sicilno`, `tckimlikno`, `maas`, `ucretturu`, `normalmesaigun`, `fazlamesai1saat`, `izinliucretligun`, `770/760/335` muhasebe hesap kodları vb.) **elle düzenlenebilir (inline edit)**.
   - Yalnızca **seçilen (checkbox)** satırlar onay sonrası DİA Cloud ERP'ye aktarılır.
2. **Personel Fark Analizi (`per_personel_ekle` / `per_personel_guncelle`):**
   - Kolay İK'da olup DİA'da olmayan personeller otomatik tespit edilir ve sıradaki `sicilno` atanır.
   - Her iki sistemde bulunan personellerde (TC Kimlik No veya Türkçe normalize Ad-Soyad eşleşmesiyle) eksik/farklı alanlar (`tckimlikno`, `durum`, `isebaslamatarihi`, `istenayrilmatarihi`, `eposta`, `gorevi`) satır bazında gösterilir.
3. **Tüm Modüller Entegre:**
   - **1. Personel Kartları (Özlük):** `POST /v2/person/list` + `bulk-view` ↔ `per_personel_ekle` / `per_personel_guncelle`
   - **2. PDKS & Fazla Mesai:** `POST /v2/timelog/list` ↔ DİA Puantaj `fazlamesai1saat` / `haftasonumesaisisaat` / `resmitatilmesaisisaat`
   - **3. İzin ve Raporlar:** `GET /v2/leave/list` ↔ `per_izinler_ekle` ve Puantaj izin/rapor günleri
   - **4. Avans / Ek Ödeme / Kesinti:** `POST /v2/transaction/list` ↔ `196.001 / 335.01` Muhasebe Avans Fişi veya `per_ekodemekesinti_ekle`
   - **5. Aylık Puantaj & Maaş Tahakkuk Mahsup Fişi:** `per_personel_puantaj_ekle` (1 gün = 7,5 saat / 30 gün = 225 saat) ve `muh_muhasebefisi_ekle` (`770.001 / 770.002 / 760.001 / 335 / 360 / 361`)

---

## Çalıştırma (macOS / Windows Geliştirme Ortamı)

```bash
pip install -r requirements.txt
python3 desktop_launcher.py
```

Uygulama otomatik olarak boş bir yerel port (`http://127.0.0.1:8765`) bulur ve masaüstü uygulama penceresi olarak açılır.

---

## Windows Kurulum Sihirbazı (`.exe`) ve Uzaktan Güncelleme

- **Kurulum Sihirbazı (`Petlebi-KolayIK-DIA-Setup.exe`):**
  - `.github/workflows/build-windows-exe.yml` iş akışı her yeni sürüm etiketinde (`v1.0.1` vb.) veya GitHub Actions üzerinden manuel tetiklendiğinde **PyInstaller** + **Inno Setup (`installer.iss`)** kullanarak tek tıkla kurulan Windows Setup sihirbazını derler.
- **Uzaktan Güncelleme (`src/updater.py` & `version.json`):**
  - Kodda geliştirme yaptığınızda `version.json` içindeki `"version"` numarasını artırıp GitHub'a göndermeniz (`git push`) yeterlidir.
  - Kullanıcının bilgisayarındaki uygulamada üst bardaki **"Güncellemeleri Kontrol Et"** butonuna basıldığında `.env` ve yerel maaş tercihleri (`data/local_overrides.json`) bozulmadan güncel kod paketi saniyeler içinde indirilir.
