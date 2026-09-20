# pdf_tools.py
# PDF faylni sahifama-sahifa JPG rasmlarga (yoki, agar mavjud bo'lsa, matnga) aylantirish

import os
import re
import pymupdf as fitz  # PyMuPDF
from pdf2image import convert_from_path, pdfinfo_from_path

PDF_DPI = 120
PDF_JPEG_QUALITY = 72
MIN_TEXT_CHARS = 40  # shundan kam matn topilsa, sahifa "skanerlangan" deb hisoblanadi

# Ba'zi eski/maxsus shriftlarda PDF ichida to'g'ri Unicode xaritasi (ToUnicode CMap)
# bo'lmaydi — natijada o'sha shrift bilan yozilgan qism (masalan sarlavha yoki
# kolontitul) matn sifatida ajratilganda tushunarsiz belgilarga aylanadi
# ("V f0 / £ T .Jllir" kabi), qolgan asosiy matn esa to'g'ri chiqadi.
# Quyidagi ro'yxat — o'zbek tilidagi kitoblarda uchraydigan "to'g'ri" belgilar:
_ALLOWED_CHARS_RE = re.compile(
    r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9\.,!?;:'\"()\[\]{}%№\-–—_/\\+=*&^@#$€£¥«»„“”‘’…•·]"
)
GARBAGE_RATIO_THRESHOLD = 0.01  # 1% dan ortiq begona belgi — matn ishonchsiz deb topiladi

# So'z darajasidagi tekshiruv: ba'zi buzilgan shriftlarda HAR BIR harf o'zi to'g'ri
# (lotin/kirill alifbosida bor) bo'ladi, lekin ular birga qo'shilganda "oJill",
# "SjI", "dhdjl" kabi ma'nosiz so'z hosil qiladi. Bunday holatni belgilar nisbati
# (yuqoridagi) ushlay olmaydi — shuning uchun alohida so'z tahlili kerak.
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]+")
_VOWELS = set("aeiouAEIOUаеёиоуыэюяАЕЁИОУЫЭЮЯўЎ")


def _word_looks_like_garbage(word: str) -> bool:
    """Bitta so'z ma'nosiz (shrift xato xaritalangani natijasi) ko'rinishga
    egami — ikkita mustaqil belgi orqali tekshiradi."""
    if len(word) < 3:
        return False
    # 1) Tartibsiz katta-kichik harf: real so'zda katta harf faqat boshida bo'ladi
    #    (yoki so'z butunlay katta harflar bilan yozilgan — qisqartma). Agar
    #    o'rtada kutilmagan katta harf chiqsa, bu buzilgan matn belgisi.
    rest = word[1:]
    if any(c.isupper() for c in rest) and not word.isupper():
        return True
    # 2) Uzun so'zda bironta ham unli tovush yo'qligi — haqiqiy so'zda deyarli
    #    hech qachon uchramaydigan holat.
    if len(word) >= 5 and not any(c in _VOWELS for c in word):
        return True
    return False


def _is_text_reliable(text: str) -> bool:
    """Ajratilgan matnning qanchalik "ishonchli" ekanini tekshiradi. Agar begona
    (shrift xato xaritalangan) belgilar ulushi chegaradan oshsa, YOKI matnda
    ma'nosiz ("buzilgan") so'zlar uchrasa, False qaytaradi — bunday holda sahifa
    matn emas, rasm sifatida saqlanishi kerak, chunki rasm PDF qanday ko'rinsa
    aynan shundayligicha chiqadi va hech qachon buzilmaydi."""
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    allowed = len(_ALLOWED_CHARS_RE.findall(compact))
    garbage_ratio = 1 - (allowed / len(compact))
    if garbage_ratio > GARBAGE_RATIO_THRESHOLD:
        return False

    words = _WORD_RE.findall(text)
    if any(_word_looks_like_garbage(w) for w in words):
        return False

    return True


def convert_pdf_to_page_images(pdf_path: str, output_dir: str) -> list:
    """PDF faylni sahifama-sahifa JPG rasmlarga aylantiradi.

    MUHIM: sahifalar BITTADAN qayta ishlanadi (bir vaqtning o'zida faqat bitta
    sahifa xotirada bo'ladi). Agar barcha sahifalarni bir zumda xotiraga olsak
    (masalan pdf2image.convert_from_path to'g'ridan-to'g'ri), 200 sahifali oddiy
    kitobning o'zi ~2.4 GB xotira talab qiladi va kichik serverlarda (Railway
    kabi) dastur xotira yetishmasligidan jim o'chib qoladi. Sahifama-sahifa
    usulda xotira sarfi hajmidan qat'i nazar doim past va barqaror bo'ladi.

    Har bir sahifa uchun page_1.jpg, page_2.jpg, ... nomlari bilan saqlaydi.
    Muvaffaqiyatli yaratilgan fayl yo'llari ro'yxatini (tartib bo'yicha) qaytaradi."""
    os.makedirs(output_dir, exist_ok=True)

    info = pdfinfo_from_path(pdf_path)
    total_pages = info["Pages"]

    saved_paths = []
    for page_num in range(1, total_pages + 1):
        images = convert_from_path(pdf_path, dpi=PDF_DPI, first_page=page_num, last_page=page_num)
        img = images[0].convert("RGB")
        path = os.path.join(output_dir, f"page_{page_num}.jpg")
        img.save(path, "JPEG", quality=PDF_JPEG_QUALITY, optimize=True)
        saved_paths.append(path)
        del img, images

    return saved_paths


def convert_pdf_hybrid(pdf_path: str, output_dir: str) -> list:
    """Har bir sahifani avval MATN sifatida ajratishga harakat qiladi (PDF ichida
    matn qatlami bo'lsa — tez, kichik hajm, shrift o'zgartirish mumkin bo'ladi).
    Agar sahifada yetarli matn topilmasa (skanerlangan/fotosurat sahifa),
    o'sha sahifani oldingidek RASM sifatida saqlaydi.

    Har bir element uchun {"type": "text", "content": "..."} yoki
    {"type": "image", "path": "..."} qaytaradi (sahifalar tartibida)."""
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    results = []

    for page_num in range(total_pages):
        page = doc[page_num]
        text = page.get_text("text").strip()

        if len(text) >= MIN_TEXT_CHARS and _is_text_reliable(text):
            results.append({"type": "text", "content": text})
        else:
            # Matn yetarli emas yoki shrift xato xaritalangani uchun ishonchsiz —
            # bu sahifani rasm sifatida saqlaymiz
            images = convert_from_path(pdf_path, dpi=PDF_DPI, first_page=page_num + 1, last_page=page_num + 1)
            img = images[0].convert("RGB")
            path = os.path.join(output_dir, f"page_{page_num + 1}.jpg")
            img.save(path, "JPEG", quality=PDF_JPEG_QUALITY, optimize=True)
            results.append({"type": "image", "path": path})
            del img, images

    doc.close()
    return results
