# pdf_tools.py
# PDF faylni sahifama-sahifa JPG rasmlarga aylantirish

import os
from pdf2image import convert_from_path, pdfinfo_from_path

PDF_DPI = 120
PDF_JPEG_QUALITY = 72


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
