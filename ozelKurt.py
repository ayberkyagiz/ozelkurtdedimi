import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
from datetime import datetime
import csv

def kemal_kilicdaroglu_tarihi_arsiv():
    base_url = "https://chp.org.tr/gundem"
    domain = "https://chp.org.tr"

    # Başlangıç tarihi (Kemal Kılıçdaroğlu'nun yeniden genel başkan seçildiği gün)
    baslangic_tarihi = datetime.strptime("21.05.2026", "%d.%m.%Y")

    # Sadece konuştuğu günleri tutacağımız kayıt defteri
    rapor = {}

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    anahtar_kelimeler = ['kemal kılıçdaroğlu', 'kılıçdaroğlu', 'kilicdaroglu', 'genel başkan', 'chp lideri']

    print("21 Mayıs 2026'dan günümüze tarihi arşiv taranıyor...")
    print("DİKKAT: Bu işlem yüzlerce haberi okuyabileceği için birkaç dakika sürebilir. Lütfen pencereyi kapatmayın!\n")

    page = 1
    tarih_siniri_asildi = False

    while not tarih_siniri_asildi:
        url = f"{base_url}?page={page}"
        print(f"Sayfa {page} taranıyor...")

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            link_etiketleri = soup.find_all('a', href=True)
            sayfada_haber_var_mi = False

            for etiket in link_etiketleri:
                metin = etiket.get_text(separator=" ", strip=True)

                # Çok uzun/kısa metinleri atla
                if len(metin) > 400 or len(metin) < 10:
                    continue

                temiz_metin = re.sub(r'\s+', ' ', metin).lower()

                # Tarihi yakala
                tarih_eslesmesi = re.search(r'(\d{2}\.\d{2}\.\d{4})', metin)

                if tarih_eslesmesi:
                    sayfada_haber_var_mi = True
                    tarih_str = tarih_eslesmesi.group(1)
                    haber_tarihi = datetime.strptime(tarih_str, "%d.%m.%Y")

                    # Eğer haber tarihi 21 Mayıs 2026'dan eskiyse, işlemi sonlandır
                    if haber_tarihi < baslangic_tarihi:
                        tarih_siniri_asildi = True
                        continue

                    # Eğer haber Kemal Kılıçdaroğlu ile ilgiliyse
                    if any(kelime in temiz_metin for kelime in anahtar_kelimeler):

                        # O günü listeye "Konuştu" olarak ekle
                        if tarih_str not in rapor:
                            rapor[tarih_str] = {"kurt_dedi": False}

                        # Zaten bu tarihte 'Kürt' dediğini bulduysak, o günün diğer haberlerine girme
                        if rapor[tarih_str]["kurt_dedi"]:
                            continue

                        # Haberin tam metnini okumak için linke gir
                        haber_linki = urljoin(domain, etiket['href'])
                        try:
                            detay_response = requests.get(haber_linki, headers=headers, timeout=10)
                            if detay_response.status_code == 200:
                                detay_soup = BeautifulSoup(detay_response.text, 'html.parser')
                                tam_metin = detay_soup.get_text(separator=" ", strip=True).lower()

                                # Kelime geçiyor mu kontrol et
                                if 'kürt' in tam_metin:
                                    rapor[tarih_str]["kurt_dedi"] = True
                        except:
                            pass # Link bozuksa veya zaman aşımı olursa atla

            # Sayfada hiç tarih formatı yoksa (örneğin son sayfaya gelindiyse) döngüyü kır
            if not sayfada_haber_var_mi:
                break

            page += 1

        except Exception as e:
            print(f"{page}. sayfada bir hata oluştu: {e}")
            break

    # --- İSTATİSTİK HESAPLAMA VE EXCEL OLUŞTURMA ---
    toplam_konustugu_gun = len(rapor)
    dedi_sayisi = sum(1 for gun in rapor.values() if gun["kurt_dedi"])
    demedi_sayisi = toplam_konustugu_gun - dedi_sayisi

    print("\n" + "="*50)
    print("TARAMA TAMAMLANDI! (21 Mayıs 2026 - Bugün)")
    print(f"Toplam Konuştuğu Gün Sayısı: {toplam_konustugu_gun}")
    print(f"Konuşup 'Kürt' DEDİĞİ Gün Sayısı: {dedi_sayisi}")
    print(f"Konuşup 'Kürt' DEMEDİĞİ Gün Sayısı: {demedi_sayisi}")
    print("="*50)

    # Detaylı listeyi CSV olarak kaydet
    dosya_adi = "Kemal_Kilicdaroglu_Tarihi_Arsiv.csv"
    with open(dosya_adi, mode='w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file, delimiter=';')
        writer.writerow(["Tarih", "Kürt Deme Durumu"])

        # Tarihleri en yeniden en eskiye doğru sıralayarak yazdır
        for tarih in sorted(rapor.keys(), key=lambda x: datetime.strptime(x, "%d.%m.%Y"), reverse=True):
            durum = "Dedi" if rapor[tarih]["kurt_dedi"] else "Demedi"
            writer.writerow([tarih, durum])

    print(f"\nTüm günlerin detaylı dökümü '{dosya_adi}' adıyla kaydedildi.")

# Kodu başlat
kemal_kilicdaroglu_tarihi_arsiv()
