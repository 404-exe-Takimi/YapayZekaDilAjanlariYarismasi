import asyncio
from playwright.async_api import async_playwright
import json
import time
import re

# Aynı anda en fazla kaç bankanın paralel taranacağını belirleyen limit (Donanım dostu)
MAX_CONCURRENT_BANKS = 3
semaphore = asyncio.Semaphore(MAX_CONCURRENT_BANKS)


# =====================================================================
# 0. TÜRKÇE KARAKTER VE KISALTMA ARAÇLARI
# =====================================================================

def turkish_lower(text: str) -> str:
    """Türkçe karakterleri (İ->i, I->ı) hatasız küçülten özel fonksiyon."""
    if not text:
        return ""
    return text.replace('İ', 'i').replace('I', 'ı').lower().strip()


def sartlari_kondanse_et(sart_metni: str) -> str:
    """
    Uzun kampanya şartlarını jürinin tabloda rahat okuyabilmesi için
    maksimum 3-4 maddelik en kritik kurallara indirger. Yasal çöpleri temizler.
    """
    if not sart_metni:
        return ""

    satirlar = [s.strip() for s in sart_metni.split("\n") if s.strip()]
    temiz_satirlar = []

    yasal_copler = [
        "hakkini sakli tutar", "hakkını saklı tutar", "durdurma hakki", "durdurma hakkı",
        "kabul etmis sayilir", "kabul etmiş sayılır", "sorumlu degildir", "sorumlu değildir",
        "devredilemez", "bolunemez", "bölünemez", "paraya cevrilmez", "paraya çevrilemez",
        "hak talep edilemez", "iptal ve iade", "degisiklik yapma", "değişiklik yapma",
        "iade edilmez", "iade yapilmaz", "iade yapılmaz"
    ]

    for satir in satirlar:
        satir_lower = turkish_lower(satir)
        if any(cop in satir_lower for cop in yasal_copler):
            continue
        temiz_satirlar.append(satir)

    ozet_satirlar = temiz_satirlar[:3]

    if len(temiz_satirlar) > 3:
        return "\n".join(ozet_satirlar) + "\n• ...ve diğer standart bankacılık kuralları geçerlidir."

    return "\n".join(ozet_satirlar)


# =====================================================================
# 1. TAMAMEN YEREL FİNANSAL VERİ AYIKLAMA ALGORİTMASI (RULE-BASED NLP)
# =====================================================================

def metinden_kar_orani_bul(sart_metni):
    """Metin içinde geçen ekstra %, kâr payı veya indirim oranını yakalar."""
    if not sart_metni:
        return None

    # [KURŞUN GEÇİRMEZ YÜZDE AYIKLAMA]: Sayısal değerin etrafındaki virgül ve noktaları güvenle temizler
    oranlar = re.findall(r'%\s*([0-9]+(?:[.,][0-9]+)?)', sart_metni)
    if oranlar:
        try:
            # Virgülü noktaya çevirerek float dönüşümü yapıyoruz
            return float(oranlar[0].replace(",", "."))
        except ValueError:
            pass

    kar_oranlari = re.findall(r'(?:kâr|kar|oranı|oranli|oranlı)\s+.*?%?\s*([0-9]+[.,][0-9]+)', sart_metni,
                              re.IGNORECASE)
    if kar_oranlari:
        return float(kar_oranlari[0].replace(",", "."))

    return None


def metinden_maksimum_tutar_bul(sart_metni):
    """Metinden 2.500 TL, 100.000 TL, 2 Milyon TL gibi limitleri ve tutarları ayıklar."""
    if not sart_metni:
        return None
    metin_lower = turkish_lower(sart_metni)

    # Milyon tespiti (Örn: 2 Milyon TL)
    milyon_bul = re.findall(r'([0-9]+[.,]?[0-9]*)\s*milyon', metin_lower)
    if milyon_bul:
        sayi = float(milyon_bul[0].replace(".", "").replace(",", "."))
        return int(sayi * 1000000)

    # Bin tespiti (Örn: 50 Bin TL)
    bin_bul = re.findall(r'([0-9]+[.,]?[0-9]*)\s*bin', metin_lower)
    if bin_bul:
        sayi = float(bin_bul[0].replace(".", "").replace(",", "."))
        return int(sayi * 1000)

    # Standart sayısal TL tespiti (Örn: "2.000 tl ve üzeri" veya "2.000 tl")
    tl_tutarlari = re.findall(r'([0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)\s*(?:tl)', metin_lower)
    if tl_tutarlari:
        temiz_sayilar = []
        for tutar in tl_tutarlari:
            temiz_tutar = int(tutar.replace(".", ""))
            if temiz_tutar >= 1000:
                temiz_sayilar.append(temiz_tutar)
        if temiz_sayilar:
            return max(temiz_sayilar)

    return None


def metinden_vade_bul(sart_metni):
    """Taksit veya vade sayısını (örn: 5 Taksit, 12 Ay) yakalar."""
    if not sart_metni:
        return None
    metin_lower = turkish_lower(sart_metni)
    vade_pattern = re.findall(r'([0-9]+\s*(?:taksit|ay|vade))', metin_lower)
    if vade_pattern:
        return vade_pattern[0].strip().title()

    return None


def metinden_odul_miktari_bul(baslik, sart_metni):
    """Kazanılacak ödülü (2.000 Mil, 200 TL indirim, %20 iade) tespit eder."""
    birllesik_metin = f"{baslik} {sart_metni}"
    birllesik_lower = turkish_lower(birllesik_metin)

    # Mil cinsinden ödül tespiti (Örn: 2.000 Mil)
    mil_odulleri = re.findall(r'([0-9.]+\s*mil)', birllesik_lower)
    if mil_odulleri:
        return mil_odulleri[0].strip().title()

    oduller = re.findall(r'([0-9]+[.,]?[0-9]*\s*(?:tl|%|’ye varan|’e varan)\s*(?:indirim|iade|puan|hediye|premium))',
                         birllesik_lower)
    if oduller:
        return oduller[0].strip().title()

    baslik_odul = re.findall(r'([0-9.]+\s*(?:tl|%)\s*[a-zışğçöü]+)', turkish_lower(baslik))
    if baslik_odul:
        return baslik_odul[0].strip().title()

    return "Avantaj/İndirim"


def metinden_kampanya_suresi_bul(ham_metin):
    """Ham metin içerisinden kampanya geçerlilik tarihlerini yakalar."""
    if not ham_metin:
        return "Belirtilmemiş"

    # Noktalı tarih formatı: GG.AA.YYYY - GG.AA.YYYY (Satır atlamalı durumlar dahil)
    tarih_pattern = re.findall(r'([0-9]{2}\.[0-9]{2}\.[0-9]{4}\s*-\s*[0-9]{2}\.[0-9]{2}\.[0-9]{4})', ham_metin)
    if tarih_pattern:
        return tarih_pattern[0].strip()

    tarih_bloklari = re.findall(
        r'([0-9]+\s+[A-Za-zĞğıİşŞçÇöÖüÜ]+\s*[0-9]*\s*-\s*[0-9]+\s+[A-Za-zĞğıİşŞçÇöÖüÜ]+\s+[0-9]{4})', ham_metin)
    if tarih_bloklari:
        return list(set(tarih_bloklari))[0].strip()

    tarih_bloklari_kisa = re.findall(r'([0-9]+\s+[A-Za-zĞğıİşŞçÇöÖüÜ]+\s*-\s*[0-9]+\s+[A-Za-zĞğıİşŞçÇöÖüÜ]+)',
                                     ham_metin)
    if tarih_bloklari_kisa:
        return list(set(tarih_bloklari_kisa))[0].strip()

    return "Belirtilmemiş"


def kampanya_turu_belirle(baslik, sart_metni):
    """Gelişmiş anahtar kelime havuzuyla kampanya türünü sınıflandırır."""
    metin = turkish_lower(f"{baslik} {sart_metni}")

    # Mil ve Miles&Smiles kampanyaları doğrudan Kart Kampanyasıdır
    if any(x in metin for x in ["mil", "miles", "smiles", "mıl"]):
        return "Kart Kampanyası"

    if any(x in metin for x in
           ["ihtiyaç finansmanı", "ihtiyac", "kredi", "finansmanlar", "destek ödemesi", "nakit ihtiyaç"]):
        return "İhtiyaç Finansmanı"
    elif any(x in metin for x in ["konut finansmanı", "konut", "ev finansmanı", "gayrimenkul", "arsa"]):
        return "Konut Finansmanı"
    elif any(x in metin for x in ["taşıt finansmanı", "tasit", "otomobil", "araç finansmanı", "veteriner"]):
        return "Taşıt Finansmanı"
    elif any(x in metin for x in
             ["puan", "chip-para", "altın puan", "hediye", "indirim", "kod", "bilet", "rezervasyon", "otel", "premium",
              "davet kodu", "ücretsiz üyelik", "hadi"]):
        return "Alışveriş Puanı"
    elif any(x in metin for x in
             ["kart", "vkart", "kredi kart", "mastercard", "troy", "visa", "pos", "taksit", "vergi ödeme", "mtv"]):
        return "Kart Kampanyası"
    else:
        return "Diğer"


# =====================================================================
# 2. METİN BÖLÜMLEME VE GELİŞTİRİLMİŞ SÜZME MANTIĞI
# =====================================================================

def metni_akilli_bol(ham_metin, kesin_silinecekler, baslik):
    """Header/Footer sızıntılarını tamamen önler, şartları ve yararlanmayı akıllıca dağıtır."""
    bölümler = {
        "nasil_yararlanabilirim": "",
        "kampanya_sartlari": ""
    }

    satirlar = [s.strip() for s in ham_metin.split("\n") if s.strip()]
    baslik_temiz = re.sub(r'\s+', ' ', turkish_lower(baslik))

    baslik_bulundu = False
    temiz_satirlar = []

    for satir in satirlar:
        satir_lower = turkish_lower(satir)
        satir_temiz = re.sub(r'\s+', ' ', satir_lower)

        if any(f in satir_temiz for f in
               ["ilginizi çekebilir", "nasıl yardımcı olabiliriz", "yardımcı konular", "bizi takip edin",
                "faydalı linkler"]):
            break

        if not baslik_bulundu:
            if baslik_temiz in satir_temiz or satir_temiz in baslik_temiz or (
                    len(satir_temiz) > 10 and satir_temiz in baslik_temiz):
                baslik_bulundu = True
                continue
            continue

        temiz_satirlar.append(satir)

    if not temiz_satirlar:
        temiz_satirlar = satirlar

    current_section = "nasil_yararlanabilirim"  # Varsayılan bölüm yararlanma

    # --- KUVVETLİ BÖLÜŞTÜRÜCÜ MANTIĞI ---
    for satir in temiz_satirlar:
        satir_lower = turkish_lower(satir)
        satir_temiz = re.sub(r'\s+', ' ', satir_lower)

        # Başlık ve Şart Alanı Tespiti
        if satir_temiz in [
            "kampanyadan nasıl yararlanabilirim", "nasıl yararlanabilirim", "nasıl katılırım",
            "katılım koşulları", "kampanyaya nasıl katılabilirim", "indirim kodu nasıl kullanılır"
        ]:
            current_section = "nasil_yararlanabilirim"
            continue

        elif satir_temiz in [
            "kampanya şartları", "kampanya sartlari", "kampanya koşulları", "kampanya kosullari",
            "koşullar", "şartlar", "genel kurallar", "kampanya kuralları"
        ]:
            current_section = "kampanya_sartlari"
            continue

        if satirilari_filtrele(satir, kesin_silinecekler):
            # EĞER METİNDE AYRI BİR "ŞARTLAR" BAŞLIĞI YOKSA:
            # "Kampanyadan maksimum...", "Kampanya kazanımı...", "Kuveyt Türk..." gibi kuralları
            # otomatik olarak kampanya_sartlari bölümüne yönlendiriyoruz!
            hedef_bolum = current_section
            if current_section == "nasil_yararlanabilirim":
                if any(x in satir_lower for x in
                       ["maksimum kazanım", "kazanımı", "koşullarında değişiklik", "birleştirilemez",
                        "müşteri bazlıdır"]):
                    hedef_bolum = "kampanya_sartlari"

            prefix = "• " if satir.startswith("•") or len(satir) > 100 else ""
            clean_satir = satir.lstrip("• ").strip()

            if clean_satir not in bölümler[hedef_bolum]:
                bölümler[hedef_bolum] += f"{prefix}{clean_satir}\n"

    for k in bölümler:
        bölümler[k] = bölümler[k].strip()

    return bölümler


def satirilari_filtrele(satir, kesin_silinecekler):
    satir_lower = turkish_lower(satir)

    menü_kelimeleri = [
        "yatırımcı ilişkileri", "şube ve atm", "kendim için", "işim için",
        "hakkımızda", "internet şube", "müşteri ol", "ana sayfa", "kampanyalar",
        "nasıl yardımcı olabiliriz", "yardım merkezi", "bizimle iletişime geçin",
        "iletişim bilgileri", "hesaplama araçları", "kar paylaşım oranları",
        "katılma hesapları", "bireysel bankacılık", "dijital bankacılık",
        "finansmanlar", "satılık gayrimenkuller", "katılım bankacılığı",
        "insan kaynakları", "özel durum açıklamaları", "politikalarımız",
        "bizi takip edin", "site haritası", "kişisel verilerin korunması",
        "bilgi toplumu hizmetleri", "sözleşmeler ve formlar", "gizlilik politikası",
        "engelsiz bankacılık", "mobil şube", "varkat", "vkart", "kartlar",
        "english", "bildirimler", "ürün ve hizmet ücretleri", "faydalı linkler",
        "bireysel emeklilik", "sağlık sigortaları", "kredi kartı başvurusu",
        "döviz alım satımı", "fatura ödemeleri", "ilginizi çekebilir", "finans portalı",
        "başvuru merkezi", "altın günleri", "döviz kurları", "bize yazın",
        "sıkça sorulan sorular", "kariyer", "iştiraklerimiz", "müşteri iletişim",
        "duyurular", "ortak atm", "ödeme sistemleri", "reklam filmi", "voleybol milli",
        "konsolide aktif", "copyright", "uygulamayı indir", "qr kod", "instagram", "facebook'da", "x'de", "linkedin'de"
    ]

    if any(m in satir_lower for m in menü_kelimeleri):
        return False

    if len(satir_lower) < 5 or satir_lower.isdigit():
        return False

    if any(x in satir_lower for x in ["çerez", "telif hakkı", "yasal uyarı", "tüm hakları", "ilginizi çekebilecek"]):
        return False

    if "vade farksız" in satir_lower and "detaylı bilgi" in satir_lower:
        return False

    if satir_lower in [turkish_lower(k) for k in kesin_silinecekler]:
        return False

    if "© 202" in satir_lower:
        return False

    return True


# =====================================================================
# 3. KAZIMA ÖNCESİ GÜVENLİ SAYFA KAYDIRMA (SCROLL) FONKSİYONU
# =====================================================================

async def guvenli_sayfa_kaydir(page):
    try:
        body_exists = await page.evaluate("document.body !== null")
        if body_exists:
            for _ in range(3):
                await page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight || document.documentElement.scrollHeight)")
                await asyncio.sleep(0.8)
        else:
            await page.evaluate("window.scrollTo(0, 1000)")
    except Exception as e:
        print(f"ℹ️  Yumuşak scroll atlandı (Hata önlendi): {e}")


# =====================================================================
# 4. TEK BİR BANKAYI ASENKRON KAZIYAN İŞÇİ (ASYNC WORKER)
# =====================================================================

async def tek_banka_kazı_async(context, banka_anahtari, banka_config):
    async with semaphore:
        banka_adi = banka_config["banka_adi"]
        kesin_silinecekler = banka_config["kesin_silinecekler"]
        link_pattern = banka_config["link_pattern"]
        base_url = banka_config["base_url"]
        title_selectors = banka_config["title_selectors"]

        banka_kampanyalari = []
        hedef_sayfalar = {}

        if "bireysel_url" in banka_config and banka_config["bireysel_url"]:
            hedef_sayfalar["Bireysel"] = banka_config["bireysel_url"]
        if "ticari_url" in banka_config and banka_config["ticari_url"]:
            hedef_sayfalar["Ticari"] = banka_config["ticari_url"]

        page = await context.new_page()
        campaign_urls = set()

        # Adım 1: Linkleri Topla
        for kategori, url in hedef_sayfalar.items():
            print(f"🌐 [{banka_adi}] - {kategori} listesi yükleniyor...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                await guvenli_sayfa_kaydir(page)

                links = await page.locator("a").all()
                for link in links:
                    href = await link.get_attribute("href")
                    if href:
                        href_temiz = href.strip()
                        if link_pattern in href_temiz:
                            full_url = f"{base_url}{href_temiz}" if href_temiz.startswith("/") else href_temiz
                            campaign_urls.add((full_url, kategori))

            except Exception as e:
                print(f"⚠️  [{banka_adi}] {kategori} listesi çekilemedi: {e}")

        campaign_urls_list = list(campaign_urls)
        print(f"📊 [{banka_adi}] Toplam {len(campaign_urls_list)} kampanya linki bulundu.")
        await page.close()

        # Adım 2: Detay Sayfalarını Çek
        async def detay_kazı_gorevi(url, kategori):
            try:
                detail_page = await context.new_page()
                await detail_page.goto(url, timeout=30000, wait_until="domcontentloaded")

                title = "Başlık Bulunamadı"
                for selector in title_selectors:
                    element = detail_page.locator(selector).first
                    if await element.count() > 0:
                        text = await element.text_content()
                        if text:
                            title = text.strip()
                            break

                govde_metni = ""
                main_element = detail_page.locator("main").first
                if await main_element.count() > 0:
                    govde_metni = await main_element.inner_text()
                else:
                    govde_metni = await detail_page.locator("body").inner_text()

                await detail_page.close()

                # Bölümleme ve NLP İşleme Aşamaları
                bölünmüş_metin = metni_akilli_bol(govde_metni, kesin_silinecekler, title)
                nasil_yararlanabilirim = bölünmüş_metin["nasil_yararlanabilirim"]
                ham_sartlar = bölünmüş_metin["kampanya_sartlari"]

                kondanse_sartlar = sartlari_kondanse_et(ham_sartlar)

                # --- NLP FALLBACK SÜZGECİ ---
                # Parametreleri tararken şartlar alanı boşsa, yararlanma metnini tarayarak null riskini önler.

                # YENİ
                referans_metin = f"{nasil_yararlanabilirim}\n{ham_sartlar}".strip()

                # NLP Parametre Çıkarımları
                kampanya_turu = kampanya_turu_belirle(title, referans_metin)
                kar_orani = metinden_kar_orani_bul(referans_metin)
                maksimum_tutar = metinden_maksimum_tutar_bul(referans_metin)
                vade_suresi = metinden_vade_bul(referans_metin)
                odul_miktari = metinden_odul_miktari_bul(title, referans_metin)
                campaign_duration = metinden_kampanya_suresi_bul(govde_metni)

                return {
                    "banka": banka_adi,
                    "campaign_name": title,
                    "campaign_type": kampanya_turu,
                    "profit_rate": kar_orani,
                    "max_amount": maksimum_tutar,
                    "vade_duration": vade_suresi,
                    "target_audience": kategori,
                    "reward_amount": odul_miktari,
                    "campaign_duration": campaign_duration,
                    "url": url,
                    "how_to_benefit": nasil_yararlanabilirim,
                    "campaign_terms": kondanse_sartlar
                }
            except Exception as e:
                print(f"❌ [{banka_adi}] Detay çekilemedi ({url}): {e}")
                if 'detail_page' in locals():
                    await detail_page.close()
                return None

        # Eşzamanlı (Paralel) detay sayfalarını toplama
        detay_gorevleri = [detay_kazı_gorevi(url, kat) for url, kat in campaign_urls_list]
        sonuclar = await asyncio.gather(*detay_gorevleri)

        return [s for s in sonuclar if s is not None]


# =====================================================================
# 5. TÜM SİSTEMİ YÖNETEN MERKEZİ ASENKRON MASTER DÖNGÜ
# =====================================================================

async def master_yurutucu_async():
    try:
        with open("banka_ayarlari.json", "r", encoding="utf-8") as f:
            ayarlar = json.load(f)
    except Exception as e:
        print(f"❌ Konfigürasyon dosyası yüklenemedi: {e}")
        return

    baslangic_zamani = time.time()
    print("🏁 [CANLI MOD - PARALEL] Asenkron Playwright başlatılıyor...")

    tum_kampanyalar = []
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(user_agent=user_agent, viewport={"width": 1920, "height": 1080})

        banka_gorevleri = []
        for banka_anahtari, banka_config in ayarlar.items():
            if not banka_config.get("aktif", True):
                print(f"⏭️  [{banka_config['banka_adi']}] pasif işaretlendiği için atlanıyor.")
                continue

            gorev = tek_banka_kazı_async(context, banka_anahtari, banka_config)
            banka_gorevleri.append(gorev)

        print(
            f"⚡ {len(banka_gorevleri)} banka için kontrollü paralel tarama başlatıldı (Eşzamanlı limit: {MAX_CONCURRENT_BANKS})...")
        toplu_sonuclar = await asyncio.gather(*banka_gorevleri)

        for sonuclar in toplu_sonuclar:
            tum_kampanyalar.extend(sonuclar)

        await browser.close()

    # Birleştirilmiş JSON çıktısını kaydet
    with open("tum_katilim_bankalari_kampanyalari.json", "w", encoding="utf-8") as f:
        json.dump(tum_kampanyalar, f, ensure_ascii=False, indent=4)

    bitis_zamani = time.time()
    gecen_sure = round(bitis_zamani - baslangic_zamani, 2)

    print("\n" + "=" * 60)
    print("🎉 MASTER PARALEL KAZIMA TAMAMLANDI!")
    print(f"⏱️  Toplam Canlı Kazıma Süresi: {gecen_sure} saniye")
    print(f"📁 Birleştirilmiş Toplam Kampanya Sayısı: {len(tum_kampanyalar)}")
    print("💾 Sonuçlar 'tum_katilim_bankalari_kampanyalari.json' dosyasına yazıldı.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(master_yurutucu_async())