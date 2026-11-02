import os
from pdf2image import convert_from_path


pdf_folder = r"C:\integral\PDF\pdf v"        # укажите ваш путь
jpg_folder = r"C:\integral\PDF\JPG"  # укажите ваш путь
poppler_path = r"C:\poppler\Library\bin"             # путь до папки bin Poppler

os.makedirs(jpg_folder, exist_ok=True)

for filename in os.listdir(pdf_folder):
    if filename.lower().endswith('.pdf'):
        file_path = os.path.join(pdf_folder, filename)
        try:
            images = convert_from_path(file_path, dpi=300, poppler_path=poppler_path)
            for i, image in enumerate(images):
                # Если в PDF несколько страниц, каждая сохранится отдельным jpg-файлом
                jpg_name = f"{os.path.splitext(filename)[0]}_page_{i+1}.jpg"
                image.save(os.path.join(jpg_folder, jpg_name), "JPEG")
            print(f"OK: {filename}")
        except Exception as e:
            print(f"Ошибка с файлом {filename}: {e}")
