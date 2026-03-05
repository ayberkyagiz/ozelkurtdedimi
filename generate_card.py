"""
generate_card.py — OzelKurtDediMi Twitter kart üreteci
Kullanım: python generate_card.py <sonuc> <streak> <tarih>
  sonuc : "demedi" | "konusmadi"
  streak: integer (sadece demedi için anlamlı)
  tarih : "5 Mart 2026"
Çıktı  : card.png (1200x675, Twitter summary_large_image boyutu)
"""

import os
import sys
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD   = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
FONT_MEDIUM = "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf"
FONT_LIGHT  = "/usr/share/fonts/truetype/google-fonts/Poppins-Light.ttf"

W, H = 1200, 675

BG           = (250, 249, 247)
BLACK        = (15, 15, 15)
GREY_MID     = (100, 100, 100)
GREY_LIGHT   = (190, 188, 184)
GREY_BORDER  = (220, 218, 214)


def load(path, size):
    return ImageFont.truetype(path, size)


def make_card(sonuc, streak, tarih, out="card.png"):
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 4], fill=BLACK)

    f_small = load(FONT_MEDIUM, 20)
    draw.text((60, 46), "@OzelKurtDediMi", font=f_small, fill=GREY_MID)

    f_light = load(FONT_LIGHT, 20)
    bbox = draw.textbbox((0, 0), tarih, font=f_light)
    draw.text((W - 60 - (bbox[2] - bbox[0]), 46), tarih, font=f_light, fill=GREY_MID)

    draw.rectangle([60, 86, W - 60, 87], fill=GREY_BORDER)

    f_question = load(FONT_LIGHT, 30)
    draw.text((60, 122), 'Özgür Özel bugün "Kürt" dedi mi?', font=f_question, fill=GREY_MID)

    f_result = load(FONT_BOLD, 108)

    if sonuc == "demedi":
        draw.text((60, 186), "DEMEDİ.", font=f_result, fill=BLACK)
    elif sonuc == "konusmadi":
        f_result2 = load(FONT_BOLD, 80)
        draw.text((60, 216), "KONUŞMADI.", font=f_result2, fill=BLACK)

    f_streak = load(FONT_MEDIUM, 26)
    if sonuc == "demedi" and streak > 0:
        streak_text = f'{streak} konusma gunudur "Kurt" demiyor.'
        draw.text((62, 368), streak_text, font=f_streak, fill=GREY_MID)
    elif sonuc == "konusmadi":
        draw.text((62, 368), "Bugün konuşma yapılmadı.", font=f_streak, fill=GREY_MID)

    draw.rectangle([60, H - 76, W - 60, H - 75], fill=GREY_BORDER)

    f_footer = load(FONT_LIGHT, 18)
    draw.text((60, H - 54), "Kaynak: chp.org.tr/gundem", font=f_footer, fill=GREY_LIGHT)

    wm = "dedi mi kurt bugün oo?"
    bbox2 = draw.textbbox((0, 0), wm, font=f_footer)
    draw.text((W - 60 - (bbox2[2] - bbox2[0]), H - 54), wm, font=f_footer, fill=GREY_LIGHT)

    img.save(out, "PNG", quality=95)
    print(f"Saved: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python generate_card.py <demedi|konusmadi> <streak> <tarih>")
        sys.exit(1)
    make_card(sys.argv[1].lower(), int(sys.argv[2]), sys.argv[3])
