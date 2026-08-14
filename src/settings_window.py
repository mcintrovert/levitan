"""
Окно настроек LEVITAN — минимальный, но приятный интерфейс (Tkinter).

Один экран для не-технарей: модель, горячие клавиши, режим пунктуации,
автозапуск + продвинутые (beam_size, cpu_threads). «Сохранить» пишет конфиг
и применяет на лету через колбэк apply(cfg). Открывается из трея.

Стиль: тема clam, шрифт Segoe UI, сегменты-кнопки вместо выпадашек, сочная
зелёная кнопка, фирменный микрофон в шапке (как #mic на сайте).
"""

import os
import threading
import tkinter as tk
from tkinter import ttk

# Сегменты: (короткая подпись -> значение в конфиге)
MODELS_SEG = [("Авто", "auto"), ("small", "small"),
              ("medium", "medium"), ("turbo", "large-v3-turbo")]
MODEL_HINT = {
    "auto": "подберётся под мощность компьютера",
    "small": "быстро, качество попроще",
    "medium": "баланс скорости и качества",
    "large-v3-turbo": "максимальное качество, чуть медленнее",
}
COMBOS_SEG = [("Ctrl+Shift", "ctrl+shift"), ("Ctrl+Alt", "ctrl+alt"),
              ("Правый Ctrl", "right ctrl"), ("Ctrl+Space", "ctrl+space")]
MODES_SEG = [("Умная", "smart"), ("Голосом", "commands"), ("Авто", "auto")]

# Палитра
BG = "#f4f6f8"
CARD = "#ffffff"
INK = "#111827"
MUTED = "#6b7280"
GREEN = "#22c55e"
GREEN_D = "#16a34a"
LINE = "#d1d5db"

# Значок окна — чёрный микрофон (base64 PNG). Заполняется реальным значением.
MIC_ICON_B64 = "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"

_lock = threading.Lock()
_open = {"win": None}


def open_settings(cfg, apply_cb):
    """Открыть окно настроек (не блокирует вызывающий поток)."""
    with _lock:
        if _open["win"] is not None:
            try:
                _open["win"].lift()
                return
            except Exception:
                _open["win"] = None
        threading.Thread(target=_run, args=(dict(cfg), apply_cb),
                         daemon=True).start()


def _draw_mic(cv, s, ox, oy, color, w):
    """Фирменный микрофон (viewBox 96x96, как #mic на сайте) на Tk-канвасе."""
    def X(x): return ox + x * s
    def Y(y): return oy + y * s

    def line(x1, y1, x2, y2):
        cv.create_line(X(x1), Y(y1), X(x2), Y(y2), fill=color, width=w,
                       capstyle=tk.ROUND)

    def arc(x1, y1, x2, y2, start, extent):
        cv.create_arc(X(x1), Y(y1), X(x2), Y(y2), start=start, extent=extent,
                      style=tk.ARC, outline=color, width=w)

    arc(38, 13, 58, 33, 0, 180)        # верхняя шапка капсулы
    arc(38, 33, 58, 53, 180, 180)      # нижняя шапка капсулы
    line(38, 23, 38, 43)               # бока
    line(58, 23, 58, 43)
    arc(30, 25, 66, 61, 180, 180)      # дужка-держатель
    line(48, 61, 48, 76)               # ножка
    line(38, 79, 58, 79)               # подставка
    line(66, 23, 86, 23)               # волны-строки
    line(66, 31, 81, 31)
    line(66, 39, 76, 39)


def _segmented(parent, pairs, var):
    """Ряд кнопок-сегментов: выбранная — зелёная. var хранит значение."""
    fr = ttk.Frame(parent)
    btns = {}

    def refresh():
        cur = var.get()
        for val, b in btns.items():
            on = (val == cur)
            b.configure(
                bg=GREEN if on else CARD,
                fg="#ffffff" if on else INK,
                activebackground=GREEN_D if on else "#eef1f4",
                activeforeground="#ffffff" if on else INK,
                highlightbackground=GREEN_D if on else LINE,
                highlightcolor=GREEN_D if on else LINE,
            )

    for i, (label, val) in enumerate(pairs):
        b = tk.Button(fr, text=label, font=("Segoe UI", 9), bd=0,
                      relief="flat", padx=13, pady=6, cursor="hand2",
                      highlightthickness=1,
                      command=lambda v=val: (var.set(v), refresh()))
        b.grid(row=0, column=i, padx=(0 if i == 0 else 6, 0))
        btns[val] = b
    refresh()
    return fr


def _make_check(parent, text, var):
    """Свой чек-бокс с зелёной галочкой."""
    fr = ttk.Frame(parent)
    box = tk.Canvas(fr, width=22, height=22, bg=BG, highlightthickness=0)
    box.grid(row=0, column=0, padx=(0, 9))
    lbl = ttk.Label(fr, text=text)
    lbl.grid(row=0, column=1)

    def redraw():
        box.delete("all")
        if var.get():
            box.create_rectangle(3, 3, 19, 19, fill=GREEN, outline=GREEN_D)
            box.create_line(6, 11, 10, 15, fill="white", width=2,
                            capstyle=tk.ROUND)
            box.create_line(10, 15, 16, 6, fill="white", width=2,
                            capstyle=tk.ROUND)
        else:
            box.create_rectangle(3, 3, 19, 19, fill=CARD, outline="#9ca3af")

    def toggle(_=None):
        var.set(not var.get())
        redraw()

    box.bind("<Button-1>", toggle)
    lbl.bind("<Button-1>", toggle)
    redraw()
    return fr


def _run(cfg, apply_cb):
    root = tk.Tk()
    _open["win"] = root
    root.title("LEVITAN — Настройки")
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        if MIC_ICON_B64:
            root._mic_icon = tk.PhotoImage(data=MIC_ICON_B64)
            root.iconphoto(True, root._mic_icon)   # значок окна = микрофон
    except Exception:
        pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    f_base = ("Segoe UI", 10)
    style.configure(".", background=BG, foreground=INK, font=f_base)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=INK, font=f_base)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                    font=("Segoe UI", 9))
    style.configure("Section.TLabel", background=BG, foreground=INK,
                    font=("Segoe UI", 10, "bold"))
    style.configure("TSpinbox", fieldbackground=CARD, bordercolor=LINE,
                    arrowsize=14, padding=(6, 4))
    style.configure("Accent.TButton", background=GREEN, foreground="#ffffff",
                    font=("Segoe UI", 10, "bold"), borderwidth=0,
                    focuscolor=GREEN, padding=(22, 9))
    style.map("Accent.TButton",
              background=[("active", GREEN_D), ("pressed", GREEN_D)])
    style.configure("TButton", font=f_base, padding=(16, 8))
    style.configure("TLabelframe", background=BG, bordercolor=LINE)
    style.configure("TLabelframe.Label", background=BG, foreground=INK,
                    font=("Segoe UI", 10, "bold"))
    style.configure("Val.TLabel", background=BG, foreground=GREEN_D,
                    font=("Segoe UI", 11, "bold"))
    style.configure("Horizontal.TScale", background=BG, troughcolor="#e2e6ea")

    outer = ttk.Frame(root, padding=(22, 16, 22, 18))
    outer.grid(sticky="nsew")

    # --- Шапка: фирменный микрофон + название ---
    header = ttk.Frame(outer)
    header.grid(row=0, column=0, sticky="w", pady=(0, 4))
    cv = tk.Canvas(header, width=50, height=50, bg=BG, highlightthickness=0)
    cv.grid(row=0, column=0, rowspan=2, padx=(0, 12))
    _draw_mic(cv, 50 / 96.0, 0, 0, INK, 3)   # фирменный чёрный микрофон
    ttk.Label(header, text="LEVITAN",
              font=("Segoe UI", 17, "bold")).grid(row=0, column=1, sticky="w")
    ttk.Label(header, text="Голосовой ввод · настройки",
              style="Muted.TLabel").grid(row=1, column=1, sticky="w")

    ttk.Separator(outer).grid(row=1, column=0, sticky="ew", pady=(12, 12))

    grid = ttk.Frame(outer)
    grid.grid(row=2, column=0, sticky="ew")
    r = 0

    def seg_row(title, pairs, var, hint_var=None):
        nonlocal r
        ttk.Label(grid, text=title, style="Section.TLabel").grid(
            row=r, column=0, sticky="w", pady=(10, 3)); r += 1
        _segmented(grid, pairs, var).grid(row=r, column=0, sticky="w"); r += 1
        if hint_var is not None:
            ttk.Label(grid, textvariable=hint_var, style="Muted.TLabel").grid(
                row=r, column=0, sticky="w", pady=(3, 0)); r += 1

    model_var = tk.StringVar(value=cfg.get("model", "auto"))
    model_hint = tk.StringVar()

    def upd_hint(*_):
        model_hint.set(MODEL_HINT.get(model_var.get(), ""))
    model_var.trace_add("write", upd_hint); upd_hint()
    seg_row("Модель", MODELS_SEG, model_var, model_hint)

    combo_var = tk.StringVar(value=cfg.get("combo", "ctrl+shift"))
    seg_row("Горячие клавиши", COMBOS_SEG, combo_var)

    mode_var = tk.StringVar(value=cfg.get("mode", "smart"))
    seg_row("Пунктуация", MODES_SEG, mode_var)

    autostart_var = tk.BooleanVar(value=bool(cfg.get("autostart", True)))
    _make_check(grid, "Запускаться при входе в Windows",
                autostart_var).grid(row=r, column=0, sticky="w", pady=(14, 2))
    r += 1

    code_auto_var = tk.BooleanVar(value=bool(cfg.get("code_auto", False)))
    _make_check(grid, "Авто-режим «код 1С» в Конфигураторе",
                code_auto_var).grid(row=r, column=0, sticky="w", pady=(4, 2))
    r += 1

    adv = ttk.LabelFrame(outer, text="Скорость и качество",
                         padding=(14, 12, 14, 14))
    adv.grid(row=3, column=0, sticky="ew", pady=(14, 6))
    adv.columnconfigure(0, weight=1)

    def slider_row(base, title, lo, hi, initial, fmt, cap_lo, cap_hi):
        top = (10 if base else 0, 0)
        ttk.Label(adv, text=title, style="Section.TLabel").grid(
            row=base, column=0, sticky="w", pady=top)
        vv = tk.StringVar()
        ttk.Label(adv, textvariable=vv, style="Val.TLabel").grid(
            row=base, column=1, sticky="e", pady=top)
        hold = {"n": initial}

        def on_move(v):
            n = int(round(float(v)))
            hold["n"] = n
            vv.set(fmt(n))
        sc = ttk.Scale(adv, from_=lo, to=hi, orient="horizontal",
                       command=on_move)
        sc.set(initial)
        sc.grid(row=base + 1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        caps = ttk.Frame(adv)
        caps.grid(row=base + 2, column=0, columnspan=2, sticky="ew")
        caps.columnconfigure(0, weight=1)
        ttk.Label(caps, text=cap_lo, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(caps, text=cap_hi, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e")
        on_move(initial)
        return hold

    cpu_max = os.cpu_count() or 8
    beam_hold = slider_row(0, "Точность распознавания", 1, 5,
                           int(cfg.get("beam_size", 1)), lambda n: str(n),
                           "быстрее", "точнее")
    threads_hold = slider_row(3, "Ядра процессора", 0, cpu_max,
                              int(cfg.get("cpu_threads", 0)),
                              lambda n: "авто" if n == 0 else str(n),
                              "0 — авто", "макс")

    # Статус — своя строка НАД кнопками, фикс. ширина (окно не разъезжается)
    status = ttk.Label(outer, text="", style="Muted.TLabel", width=52,
                       anchor="w")
    status.grid(row=4, column=0, sticky="w", pady=(14, 2))

    def on_save():
        new = dict(cfg)
        new["model"] = model_var.get()
        new["combo"] = combo_var.get()
        new["mode"] = mode_var.get()
        new["autostart"] = bool(autostart_var.get())
        new["code_auto"] = bool(code_auto_var.get())
        new["beam_size"] = int(beam_hold["n"])
        new["cpu_threads"] = int(threads_hold["n"])
        try:
            apply_cb(new)
            cfg.update(new)
            status.config(text="Сохранено ✓  — модель применяется в фоне",
                          foreground=GREEN_D)
        except Exception as e:
            status.config(text=f"Ошибка: {e}", foreground="#dc2626")

    # Кнопки: Сохранить (главная) слева, Закрыть справа
    btns = ttk.Frame(outer)
    btns.grid(row=5, column=0, sticky="e")
    ttk.Button(btns, text="Сохранить", style="Accent.TButton",
               command=on_save).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(btns, text="Закрыть", command=root.destroy).grid(row=0, column=1)

    def on_close():
        _open["win"] = None
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.update_idletasks()
    root.mainloop()
    _open["win"] = None
