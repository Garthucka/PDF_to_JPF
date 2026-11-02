import os
import sys
import time
import zipfile
import platform
import subprocess
import urllib.request
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Патч subprocess: скрываем консольное окно на Windows ───────────────────
# pdf2image вызывает pdftoppm.exe через subprocess.Popen без CREATE_NO_WINDOW,
# из-за чего при каждом файле мигает чёрное окно CMD.
# Патчируем Popen один раз при старте — добавляем нужный флаг автоматически.
if platform.system() == "Windows":
    _original_Popen = subprocess.Popen

    class _NoCmdPopen(_original_Popen):
        def __init__(self, *args, **kwargs):
            si = kwargs.get("startupinfo")
            if si is None:
                si = subprocess.STARTUPINFO()
            si.dwFlags  |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = si
            # CREATE_NO_WINDOW = 0x08000000
            cf = kwargs.get("creationflags", 0)
            kwargs["creationflags"] = cf | 0x08000000
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoCmdPopen

# ─── Определение режима запуска: EXE (PyInstaller) или скрипт ───────────────
def _is_frozen():
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

def _app_dir():
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(sys.argv[0]))

def _meipass_dir():
    return getattr(sys, "_MEIPASS", None)

# ─── Константы Poppler ───────────────────────────────────────────────────────
POPPLER_WINDOWS_URL = (
    "https://github.com/oschwartz10612/poppler-windows/releases/download/"
    "v24.08.0-0/Release-24.08.0-0.zip"
)
POPPLER_ZIP_NAME  = "poppler-windows.zip"
POPPLER_LOCAL_DIR = os.path.join(_app_dir(), "poppler")


def _find_poppler_bin(base):
    for root, dirs, _ in os.walk(base):
        if "bin" in dirs:
            candidate = os.path.join(root, "bin")
            if any(f.startswith("pdftoppm") for f in os.listdir(candidate)):
                return candidate
    return None


def get_poppler_path():
    """
    Порядок поиска Poppler:
      1. Встроенный в EXE (папка poppler_bin внутри _MEIPASS)
      2. Локальная папка poppler/ рядом с EXE/скриптом
      3. Системный PATH
      4. Не найден -> 'NOT_FOUND'
    """
    meipass = _meipass_dir()
    if meipass:
        bundled = os.path.join(meipass, "poppler_bin")
        if os.path.isdir(bundled):
            if any(f.startswith("pdftoppm") for f in os.listdir(bundled)):
                return bundled

    if os.path.isdir(POPPLER_LOCAL_DIR):
        found = _find_poppler_bin(POPPLER_LOCAL_DIR)
        if found:
            return found

    import shutil
    if shutil.which("pdftoppm"):
        return None

    return "NOT_FOUND"


def download_poppler(progress_cb=None, log_cb=None):
    if platform.system() != "Windows":
        raise RuntimeError(
            "Автоматическая установка поддерживается только на Windows.\n"
            "Linux/macOS: установите poppler-utils через менеджер пакетов."
        )
    os.makedirs(POPPLER_LOCAL_DIR, exist_ok=True)
    zip_path = os.path.join(POPPLER_LOCAL_DIR, POPPLER_ZIP_NAME)

    if log_cb:
        log_cb("Скачивается Poppler (~30 МБ)...")

    def _hook(block, bsize, total):
        if total > 0 and progress_cb:
            progress_cb(min(block * bsize / total, 1.0))

    urllib.request.urlretrieve(POPPLER_WINDOWS_URL, zip_path, _hook)

    if log_cb:
        log_cb("Распаковка...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(POPPLER_LOCAL_DIR)
    os.remove(zip_path)

    bin_path = _find_poppler_bin(POPPLER_LOCAL_DIR)
    if not bin_path:
        raise RuntimeError("Не удалось найти папку bin после распаковки.")

    if log_cb:
        log_cb(f"Poppler установлен: {bin_path}")
    return bin_path


# ════════════════════════════════════════════════════════════════════════════
class PDFtoJPGConverter:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF -> JPG Конвертер")
        self.root.geometry("700x600")
        self.root.resizable(True, True)
        self.root.minsize(650, 560)

        self.pdf_folder     = tk.StringVar()
        self.jpg_folder     = tk.StringVar()
        self.dpi            = tk.IntVar(value=200)
        self.jpg_quality    = tk.IntVar(value=85)
        self.output_format  = tk.StringVar(value="JPEG")
        self.use_subfolders = tk.BooleanVar(value=True)
        self.max_workers    = tk.IntVar(value=4)
        self.is_converting  = False
        self._stop_flag     = False
        self._poppler_bin   = None
        self._poppler_ok    = False

        self._setup_styles()
        self._create_widgets()
        self._check_poppler_on_start()

    # ── стили ────────────────────────────────────────────────────────────────
    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TProgressbar", thickness=16,
                    troughcolor="#e0e0e0", background="#4CAF50")
        s.configure("TNotebook.Tab", padding=[10, 4])

    # ── главный layout ───────────────────────────────────────────────────────
    def _create_widgets(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="x", padx=10, pady=(8, 4))

        main_tab = tk.Frame(nb, bg="#f5f5f5")
        adv_tab  = tk.Frame(nb, bg="#f5f5f5")
        nb.add(main_tab, text="  Основное  ")
        nb.add(adv_tab,  text="  Настройки  ")

        self._build_main_tab(main_tab)
        self._build_adv_tab(adv_tab)

        bottom = tk.Frame(self.root, bg="#f5f5f5")
        bottom.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self._build_buttons(bottom)
        self._build_log(bottom)

    # ── Основная вкладка ─────────────────────────────────────────────────────
    def _build_main_tab(self, parent):
        pop_frm = tk.Frame(parent, bg="#fffde7",
                           highlightthickness=1, highlightbackground="#ffe082")
        pop_frm.pack(fill="x", padx=10, pady=(8, 2))

        self.pop_icon  = tk.Label(pop_frm, text="...", font=("Segoe UI", 11),
                                  bg="#fffde7")
        self.pop_label = tk.Label(pop_frm, text="Проверка Poppler...",
                                  font=("Segoe UI", 9), bg="#fffde7", fg="#555")
        self.pop_btn   = tk.Button(pop_frm, text="Установить Poppler",
                                   command=self._install_poppler_ui,
                                   font=("Segoe UI", 9), relief="flat",
                                   bg="#2196F3", fg="white", padx=8, pady=2,
                                   cursor="hand2", state="disabled")
        self.pop_icon.pack(side="left", padx=(6, 2), pady=4)
        self.pop_label.pack(side="left", padx=4, pady=4)
        self.pop_btn.pack(side="right", padx=6, pady=4)

        self.pop_progress = ttk.Progressbar(parent, mode="determinate",
                                            maximum=100)

        frm = tk.LabelFrame(parent, text=" Пути ", bg="#f5f5f5",
                            font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        frm.pack(fill="x", padx=10, pady=6)
        frm.columnconfigure(1, weight=1)

        for r, (lbl, var, cmd) in enumerate([
            ("Папка с PDF:",   self.pdf_folder, self.select_pdf_folder),
            ("Папка для JPG:", self.jpg_folder, self.select_jpg_folder),
        ]):
            tk.Label(frm, text=lbl, bg="#f5f5f5",
                     font=("Segoe UI", 9)).grid(row=r, column=0, sticky="w", pady=3)
            tk.Entry(frm, textvariable=var,
                     font=("Segoe UI", 9)).grid(row=r, column=1, padx=6, sticky="ew")
            tk.Button(frm, text="Обзор...", command=cmd,
                      font=("Segoe UI", 9), width=8,
                      cursor="hand2").grid(row=r, column=2)

        dpi_frm = tk.LabelFrame(parent, text=" Качество (DPI) ", bg="#f5f5f5",
                                font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        dpi_frm.pack(fill="x", padx=10, pady=4)
        self.dpi_label = tk.Label(dpi_frm, text=f"DPI: {self.dpi.get()}",
                                  bg="#f5f5f5", font=("Segoe UI", 9, "bold"), width=10)
        self.dpi_label.pack(side="right", padx=6)
        tk.Scale(dpi_frm, variable=self.dpi, from_=72, to=600,
                 orient="horizontal", bg="#f5f5f5", showvalue=False,
                 command=lambda v: self.dpi_label.config(
                     text=f"DPI: {int(float(v))}"),
                 resolution=1, troughcolor="#d0e8d0",
                 activebackground="#4CAF50",
                 highlightthickness=0).pack(side="left", fill="x", expand=True)

        prog_frm = tk.Frame(parent, bg="#f5f5f5")
        prog_frm.pack(fill="x", padx=10, pady=(6, 4))
        self.progress = ttk.Progressbar(prog_frm, mode="determinate")
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        tk.Label(prog_frm, textvariable=self.status_var, bg="#f5f5f5",
                 font=("Segoe UI", 8), fg="#555").pack(anchor="w", pady=1)

    # ── Вкладка настроек ─────────────────────────────────────────────────────
    def _build_adv_tab(self, parent):
        frm = tk.LabelFrame(parent, text=" Параметры вывода ", bg="#f5f5f5",
                            font=("Segoe UI", 9, "bold"), padx=12, pady=8)
        frm.pack(fill="x", padx=10, pady=8)
        frm.columnconfigure(1, weight=1)

        tk.Label(frm, text="Формат файлов:", bg="#f5f5f5",
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(frm, textvariable=self.output_format,
                     values=["JPEG", "PNG", "TIFF", "BMP"],
                     state="readonly", width=12,
                     font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=6)

        tk.Label(frm, text="Качество JPEG (1-95):", bg="#f5f5f5",
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=5)
        tk.Spinbox(frm, from_=1, to=95, textvariable=self.jpg_quality,
                   width=8, font=("Segoe UI", 9)).grid(row=1, column=1,
                                                        sticky="w", padx=6)

        tk.Label(frm, text="Параллельных потоков:", bg="#f5f5f5",
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=5)
        tk.Spinbox(frm, from_=1, to=16, textvariable=self.max_workers,
                   width=8, font=("Segoe UI", 9)).grid(row=2, column=1,
                                                        sticky="w", padx=6)

        tk.Checkbutton(frm, text="Сохранять каждый PDF в отдельную подпапку",
                       variable=self.use_subfolders, bg="#f5f5f5",
                       font=("Segoe UI", 9),
                       cursor="hand2").grid(row=3, column=0, columnspan=2,
                                             sticky="w", pady=5)

        pop_info = tk.LabelFrame(parent, text=" Poppler ", bg="#f5f5f5",
                                 font=("Segoe UI", 9, "bold"), padx=10, pady=6)
        pop_info.pack(fill="x", padx=10, pady=4)
        self.pop_path_var = tk.StringVar(value="Определяется...")
        tk.Label(pop_info, textvariable=self.pop_path_var, bg="#f5f5f5",
                 font=("Consolas", 8), fg="#555",
                 wraplength=560, justify="left").pack(anchor="w")

        if _is_frozen():
            hint_text = "Poppler встроен в EXE — дополнительная установка не требуется."
        else:
            hint_text = ("Poppler скачивается автоматически при первом запуске\n"
                         "и сохраняется в папку poppler/ рядом со скриптом.\n"
                         "Повторная загрузка не требуется.")
        tk.Label(pop_info, text=hint_text,
                 bg="#f5f5f5", font=("Segoe UI", 8), fg="#777",
                 justify="left").pack(anchor="w", pady=(3, 0))

        hint = tk.LabelFrame(parent, text=" Совет по DPI ", bg="#f5f5f5",
                             font=("Segoe UI", 9, "bold"), padx=10, pady=6)
        hint.pack(fill="x", padx=10, pady=4)
        tk.Label(hint, justify="left", bg="#f5f5f5",
                 font=("Segoe UI", 9), fg="#444",
                 text=("• 72 DPI  — экран (быстро, малый файл)\n"
                       "• 150 DPI — стандартное качество\n"
                       "• 200 DPI — рекомендуемый баланс\n"
                       "• 300 DPI — высокое качество\n"
                       "• 600 DPI — печать (медленно, большой файл)")
                 ).pack(anchor="w")

    # ── Кнопки ───────────────────────────────────────────────────────────────
    def _build_buttons(self, parent):
        btn_frm = tk.Frame(parent, bg="#f5f5f5")
        btn_frm.pack(side="bottom", fill="x", pady=(4, 0))

        self.start_btn = tk.Button(
            btn_frm, text="  Начать конвертацию",
            command=self.start_conversion,
            bg="#4CAF50", fg="white", font=("Segoe UI", 11, "bold"),
            relief="flat", padx=16, pady=6, cursor="hand2",
            activebackground="#388E3C", state="disabled")
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = tk.Button(
            btn_frm, text="  Остановить",
            command=self._request_stop,
            bg="#e53935", fg="white", font=("Segoe UI", 11, "bold"),
            relief="flat", padx=16, pady=6, state="disabled", cursor="hand2",
            activebackground="#b71c1c")
        self.stop_btn.pack(side="left", padx=(0, 8))

        tk.Button(btn_frm, text="Очистить лог",
                  command=self._clear_log,
                  font=("Segoe UI", 9), relief="flat",
                  padx=10, pady=6, cursor="hand2").pack(side="right")

    # ── Лог ──────────────────────────────────────────────────────────────────
    def _build_log(self, parent):
        lf = tk.LabelFrame(parent, text=" Журнал ", bg="#f5f5f5",
                           font=("Segoe UI", 9, "bold"), padx=4, pady=4)
        lf.pack(side="bottom", fill="both", expand=True, pady=(0, 4))

        sb = tk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.log_text = tk.Text(lf, font=("Consolas", 9),
                                state="disabled", yscrollcommand=sb.set,
                                bg="#1e1e1e", fg="#d4d4d4",
                                selectbackground="#264f78")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.config(command=self.log_text.yview)
        for tag, color in [("ok",   "#6ec26e"), ("err",  "#f44747"),
                           ("info", "#9cdcfe"), ("warn", "#dcdcaa"),
                           ("done", "#4ec9b0")]:
            self.log_text.tag_config(tag, foreground=color)

    # ── Poppler: проверка при старте ─────────────────────────────────────────
    def _check_poppler_on_start(self):
        def _check():
            result = get_poppler_path()
            if result == "NOT_FOUND":
                self.root.after(0, self._show_poppler_missing)
            else:
                self._poppler_bin = result
                self._poppler_ok  = True
                self.root.after(0, self._show_poppler_ok)
        Thread(target=_check, daemon=True).start()

    def _show_poppler_ok(self):
        path_str = "встроен в EXE" if _is_frozen() else (self._poppler_bin or "системный PATH")
        self.pop_icon.config(text="OK")
        self.pop_label.config(text="Poppler готов", fg="#2e7d32")
        self.pop_btn.config(state="disabled", bg="#aaa", text="Установлен")
        self.pop_path_var.set(f"Путь: {path_str}")
        for w in (self.pop_icon, self.pop_label):
            w.config(bg="#e8f5e9")
        self.pop_icon.master.config(bg="#e8f5e9", highlightbackground="#a5d6a7")
        self.start_btn.config(state="normal")
        self.log(f"Poppler готов: {path_str}", tag="ok")

    def _show_poppler_missing(self):
        self.pop_icon.config(text="!!")
        self.pop_label.config(
            text="Poppler не найден — нажмите «Установить»", fg="#c62828")
        self.pop_btn.config(state="normal")
        self.pop_path_var.set("Не установлен")
        for w in (self.pop_icon, self.pop_label):
            w.config(bg="#ffebee")
        self.pop_icon.master.config(bg="#ffebee", highlightbackground="#ef9a9a")
        self.log("Poppler не найден. Нажмите «Установить Poppler».", tag="err")

    # ── Установка Poppler ────────────────────────────────────────────────────
    def _install_poppler_ui(self):
        if platform.system() != "Windows":
            messagebox.showinfo(
                "Установка Poppler",
                "Linux:   sudo apt install poppler-utils\n"
                "macOS:   brew install poppler\n\n"
                "После установки перезапустите программу.")
            return
        self.pop_btn.config(state="disabled", text="Загрузка...")
        self.pop_progress.pack(fill="x", padx=10, pady=(0, 4))
        self.pop_progress["value"] = 0

        def _run():
            try:
                bin_path = download_poppler(
                    progress_cb=lambda f: self.root.after(
                        0, lambda v=f: self.pop_progress.config(
                            value=int(v * 100))),
                    log_cb=lambda m: self.log(m, tag="info"))
                self._poppler_bin = bin_path
                self._poppler_ok  = True
                self.root.after(0, self._after_install_ok)
            except Exception as e:
                self.root.after(0, lambda: self._after_install_err(str(e)))

        Thread(target=_run, daemon=True).start()

    def _after_install_ok(self):
        self.pop_progress.pack_forget()
        self.pop_btn.config(text="Установить Poppler")
        self._show_poppler_ok()

    def _after_install_err(self, msg):
        self.pop_progress.pack_forget()
        self.pop_btn.config(state="normal", text="Повторить", bg="#e53935")
        self.log(f"Ошибка установки: {msg}", tag="err")
        messagebox.showerror("Ошибка установки Poppler", msg)

    # ── Выбор папок ──────────────────────────────────────────────────────────
    def select_pdf_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку с PDF")
        if folder:
            self.pdf_folder.set(folder)
            if not self.jpg_folder.get():
                self.jpg_folder.set(
                    os.path.join(os.path.dirname(folder), "jpg_output"))
            self.log(f"PDF: {folder}", tag="info")

    def select_jpg_folder(self):
        folder = filedialog.askdirectory(title="Выберите папку для JPG")
        if folder:
            self.jpg_folder.set(folder)
            self.log(f"JPG: {folder}", tag="info")

    # ── Лог ──────────────────────────────────────────────────────────────────
    def log(self, message, tag=""):
        def _do():
            self.log_text.config(state="normal")
            ts = time.strftime("%H:%M:%S")
            self.log_text.insert("end", f"[{ts}] {message}\n", tag or None)
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.root.after(0, _do)

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def _request_stop(self):
        self._stop_flag = True
        self.log("Остановка после текущего файла...", tag="warn")

    # ── Конвертация ───────────────────────────────────────────────────────────
    def start_conversion(self):
        if self.is_converting or not self._poppler_ok:
            return
        pdf_dir = self.pdf_folder.get().strip()
        jpg_dir = self.jpg_folder.get().strip()
        if not pdf_dir or not jpg_dir:
            messagebox.showerror("Ошибка", "Выберите обе папки (PDF и JPG)!")
            return
        if not os.path.exists(pdf_dir):
            messagebox.showerror("Ошибка", "Папка с PDF не существует!")
            return
        self.is_converting = True
        self._stop_flag    = False
        self.start_btn.config(state="disabled", text="Конвертация...", bg="#888")
        self.stop_btn.config(state="normal")
        Thread(target=self._convert_files,
               args=(pdf_dir, jpg_dir), daemon=True).start()

    def _convert_one(self, args):
        from pdf2image import convert_from_path
        file_path, jpg_dir, poppler, dpi, fmt, quality, use_sub = args
        stem    = os.path.splitext(os.path.basename(file_path))[0]
        out_dir = os.path.join(jpg_dir, stem) if use_sub else jpg_dir
        os.makedirs(out_dir, exist_ok=True)
        ext = "jpg" if fmt == "JPEG" else fmt.lower()
        kw  = {"poppler_path": poppler} if poppler else {}
        images = convert_from_path(file_path, dpi=dpi, **kw)
        for i, img in enumerate(images):
            save_kw = {"quality": quality, "optimize": True} if fmt == "JPEG" else {}
            img.save(os.path.join(out_dir, f"{stem}_p{i+1:03d}.{ext}"), fmt, **save_kw)
        return os.path.basename(file_path), len(images)

    def _convert_files(self, pdf_dir, jpg_dir):
        try:
            os.makedirs(jpg_dir, exist_ok=True)
            pdf_files = sorted(
                f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf"))
            total = len(pdf_files)
            if total == 0:
                self.log("PDF-файлы не найдены!", tag="err")
                return

            dpi     = self.dpi.get()
            fmt     = self.output_format.get()
            quality = self.jpg_quality.get()
            use_sub = self.use_subfolders.get()
            workers = self.max_workers.get()
            poppler = self._poppler_bin

            self.log(f"Найдено {total} PDF. DPI={dpi}, Формат={fmt}, Потоки={workers}",
                     tag="info")
            self.root.after(0, lambda: self.progress.config(maximum=total, value=0))

            success = error = 0
            t0 = time.time()
            args_list = [
                (os.path.join(pdf_dir, f), jpg_dir, poppler,
                 dpi, fmt, quality, use_sub)
                for f in pdf_files
            ]

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(self._convert_one, a): a for a in args_list}
                for idx, future in enumerate(as_completed(futures), 1):
                    if self._stop_flag:
                        ex.shutdown(wait=False, cancel_futures=True)
                        self.log("Остановлено пользователем.", tag="warn")
                        break
                    fname = os.path.basename(futures[future][0])
                    try:
                        _, pages = future.result()
                        self.log(f"[{idx}/{total}] OK {fname} -> {pages} стр.", tag="ok")
                        success += 1
                    except Exception as e:
                        self.log(f"[{idx}/{total}] FAIL {fname}: {e}", tag="err")
                        error += 1

                    elapsed = time.time() - t0
                    speed   = idx / elapsed if elapsed else 0
                    eta     = int((total - idx) / speed) if speed else 0
                    self._set_status(
                        f"Обработано {idx}/{total}  |  "
                        f"{speed:.1f} файл/с  |  Осталось ~{eta} с")
                    self.root.after(0, lambda v=idx: self.progress.config(value=v))

            elapsed = time.time() - t0
            self.log(f"\nГотово! Успешно: {success}, Ошибок: {error}, "
                     f"Время: {elapsed:.1f} с", tag="done")
            self._set_status(f"Завершено — {success} файлов за {elapsed:.1f} с")
            if not self._stop_flag:
                self.root.after(0, lambda: messagebox.showinfo(
                    "Готово",
                    f"Конвертация завершена!\n\n"
                    f"Успешно: {success}\n"
                    f"Ошибок:  {error}\n"
                    f"Время:   {elapsed:.1f} с"))

        except Exception as e:
            self.log(f"Критическая ошибка: {e}", tag="err")
            self.root.after(0, lambda: messagebox.showerror("Ошибка", str(e)))
        finally:
            self.is_converting = False
            self._stop_flag    = False
            self.root.after(0, lambda: (
                self.start_btn.config(state="normal",
                                      text="  Начать конвертацию",
                                      bg="#4CAF50"),
                self.stop_btn.config(state="disabled"),
                self.progress.config(value=0),
            ))


if __name__ == "__main__":
    root = tk.Tk()
    PDFtoJPGConverter(root)
    root.mainloop()
