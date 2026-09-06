# pdf_tools.py
# PDF faylni sahifama-sahifa JPG rasmlarga aylantirish

import os
from pdf2image import convert_from_path

PDF_DPI = 120
PDF_JPEG_QUALITY = 72


def convert_pdf_to_page_images(pdf_path: str, output_dir: str) -> list:
    """PDF faylni sahifama-sahifa JPG rasmlarga aylantiradi.
    Har bir sahifa uchun page_1.jpg, page_2.jpg, ... nomlari bilan saqlaydi.
    Muvaffaqiyatli yaratilgan fayl yo'llari ro'yxatini (tartib bo'yicha) qaytaradi."""
    os.makedirs(output_dir, exist_ok=True)
    images = convert_from_path(pdf_path, dpi=PDF_DPI)

    saved_paths = []
    for i, img in enumerate(images, start=1):
        img = img.convert("RGB")
        path = os.path.join(output_dir, f"page_{i}.jpg")
        img.save(path, "JPEG", quality=PDF_JPEG_QUALITY, optimize=True)
        saved_paths.append(path)

    return saved_paths
