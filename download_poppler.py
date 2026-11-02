"""
Вспомогательный скрипт: скачивает и распаковывает Poppler для Windows.
Вызывается из build_exe.bat
Использование: python download_poppler.py <папка_назначения>
"""
import sys, os, urllib.request, zipfile

URL  = ("https://github.com/oschwartz10612/poppler-windows/releases/download/"
        "v24.08.0-0/Release-24.08.0-0.zip")
DEST = sys.argv[1] if len(sys.argv) > 1 else "poppler_bundle"
ZIP  = os.path.join(DEST, "poppler.zip")

os.makedirs(DEST, exist_ok=True)

def hook(block, bsize, total):
    if total > 0:
        pct = min(block * bsize / total, 1.0)
        bar = "#" * int(pct * 40) + "." * (40 - int(pct * 40))
        sys.stdout.write(f"\r  [{bar}] {pct*100:.0f}%")
        sys.stdout.flush()

print("  Скачивание Poppler (~30 МБ)...")
urllib.request.urlretrieve(URL, ZIP, hook)
print()
print("  Распаковка...")
with zipfile.ZipFile(ZIP) as z:
    z.extractall(DEST)
os.remove(ZIP)
print("  Готово!")
