"""
LEVITAN — голосовой ввод для Windows (трей-приложение).

Зажми комбо (по умолчанию Ctrl+Shift) -> говори -> отпусти ->
текст распознан Whisper и вставлен под курсор. Пунктуация — голосом
("запятая", "точка", "новый абзац", "восклицательный знак", ...).

Живёт в системном трее, цвет иконки = статус:
  серый  — загрузка модели
  зелёный — жду
  красный — идёт запись
  оранжевый — распознаю

Настройки — в levitan.json рядом (создастся при первом запуске).
Выход — правый клик по иконке в трее -> Выход.
"""

import os
import sys
import json
import time
import logging
from logging.handlers import RotatingFileHandler
import threading

import numpy as np
import sounddevice as sd
import pyperclip
import keyboard
from PIL import Image, ImageDraw
import pystray

try:
    import winsound

    def beep(start: bool):
        winsound.Beep(880 if start else 523, 110)
except Exception:
    def beep(start: bool):
        pass

# Чтобы работало и из исходников (src/), и из собранного exe.
# В собранном exe __file__ уезжает внутрь _internal — берём папку самого exe,
# чтобы levitan.json и levitan.log лежали рядом с LEVITAN.exe.
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    sys.path.insert(0, sys._MEIPASS)  # модули внутри бандла
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(BASE, "src"))
from whisper_recognizer import WhisperRecognizer  # noqa: E402
import postprocess  # noqa: E402
import code_1c  # noqa: E402

CONFIG_PATH = os.path.join(BASE, "levitan.json")


def total_ram_gb():
    """Объём ОЗУ в ГБ (Windows, через ctypes). При ошибке — безопасные 8."""
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / (1024 ** 3)
    except Exception:
        return 8.0


def cpu_mhz():
    """Номинальная частота CPU в МГц (Windows, реестр). При ошибке — 2000."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as k:
            v, _ = winreg.QueryValueEx(k, "~MHz")
            return float(v)
    except Exception:
        return 2000.0


def _system_uptime_sec():
    """Сколько секунд назад загрузилась система (для холодного старта)."""
    try:
        import ctypes
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:
        return 9999.0


def pick_model():
    """Авто-выбор модели по СКОРОСТИ CPU (а не по RAM — узкое место у Whisper
    это тяжёлый энкодер, который жуёт фикс. 30-сек окно). Замерено на i5-13400
    (16 потоков): small ~1.5с, medium ~4.4с, turbo ~6.4с на фразу. turbo из
    авто-подбора ИСКЛЮЧЁН — он и тормозит у всех; берётся только вручную."""
    gb = total_ram_gb()
    cores = os.cpu_count() or 2
    mhz = cpu_mhz()
    score = cores * (mhz / 1000.0)  # грубый индекс мощности CPU
    if gb < 5:
        choice = "small"            # мало ОЗУ — не уходим в своп
    elif score >= 24:
        choice = "medium"           # мощный CPU — можно качество (латентность ок)
    else:
        choice = "small"            # большинство машин — снаппи-режим
    log.info("CPU: cores=%d ~%.0fМГц score=%.1f, ОЗУ ~%.1fГБ -> авто-модель: %s",
             cores, mhz, score, gb, choice)
    return choice


def resolve_model(name):
    """Порядок: 1) локальная папка model/ рядом с exe (mod-версия, офлайн);
    2) "auto" -> подбор по скорости CPU; 3) явное имя из конфига."""
    local = os.path.join(BASE, "model")
    if os.path.isdir(local) and os.path.exists(os.path.join(local, "model.bin")):
        return local
    if name == "auto":
        return pick_model()
    return name


# --- прогресс первой закачки модели ---------------------------------------
# Модель качается с HuggingFace один раз. Раньше пользователь всё это время
# видел просто серый значок и думал, что программа зависла. Теперь показываем
# в подсказке трея, сколько уже скачано.

_MODEL_MB = {"small": 464, "medium": 1460, "turbo": 1550,
             "large-v3-turbo": 1550, "large-v3": 3090, "base": 145}

_HF_REPO = {"small": "models--Systran--faster-whisper-small",
            "medium": "models--Systran--faster-whisper-medium",
            "turbo": "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
            "large-v3-turbo": "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
            "large-v3": "models--Systran--faster-whisper-large-v3",
            "base": "models--Systran--faster-whisper-base"}


def _hf_hub_dir():
    """Каталог кэша HuggingFace, куда льются модели."""
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        p = os.environ.get(var)
        if p:
            return p
    home = os.environ.get("HF_HOME")
    if home:
        return os.path.join(home, "hub")
    return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")


def _model_cache_path(model_name):
    repo = _HF_REPO.get(model_name)
    return os.path.join(_hf_hub_dir(), repo) if repo else None


def _downloaded_mb(model_name):
    """Сколько мегабайт модели уже лежит на диске.

    Считаем по подкаталогу blobs — там настоящие файлы. В snapshots на
    Windows лежат их копии (симлинки доступны не всегда), и если считать
    весь каталог, размер удваивается.
    """
    path = _model_cache_path(model_name)
    if not path or not os.path.isdir(path):
        return 0.0
    blobs = os.path.join(path, "blobs")
    target = blobs if os.path.isdir(blobs) else path
    return _dir_size(target) / (1024 * 1024)


def model_in_cache(model_name):
    """Модель уже скачана? (папка репозитория есть и весит правдоподобно)"""
    if os.path.isdir(model_name):          # локальная папка model/ рядом с exe
        return True
    path = _model_cache_path(model_name)
    if not path or not os.path.isdir(path):
        return False
    expected = _MODEL_MB.get(model_name, 400)
    return _downloaded_mb(model_name) > expected * 0.9


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total

DEFAULT_CONFIG = {
    "model": "auto",            # auto = подбор по CPU; или turbo/medium/small
    "device": "cpu",
    "compute_type": "int8",
    "beam_size": 1,              # 1 = быстро (для диктовки хватает); 5 = точнее
    "cpu_threads": 0,            # 0 = авто; на своём железе = число P-ядер (у i5-13400 это 6)
    "combo": "ctrl+shift",
    "mode": "smart",             # smart (гибрид) | commands | auto
    "capitalize": True,
    "restore_clipboard": True,
    "sample_rate": 16000,
    "autostart": True,           # запускаться при входе в Windows
    "code_mode": False,          # режим «код 1С» (ручной тумблер в трее)
    "code_auto": False,          # авто-режим код, когда активен Конфигуратор 1С
    "debug_log": False,          # писать распознанный текст в лог (только для отладки!)
}

# При запуске через pythonw.exe (без консоли) sys.stdout == None —
# StreamHandler в этом случае не добавляем, иначе логгер падает.
# Лог с ротацией: 512 КБ и один бэкап — файл не растёт бесконечно.
_handlers = [RotatingFileHandler(os.path.join(BASE, "levitan.log"),
                                 maxBytes=512 * 1024, backupCount=1,
                                 encoding="utf-8")]
if sys.stdout is not None:
    _handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("levitan")


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log.warning("Не прочитал конфиг, беру дефолт: %s", e)
    else:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("Не сохранил конфиг: %s", e)


# --- Автозапуск с Windows (ключ реестра HKCU\...\Run) ----------------------
try:
    import winreg
except ImportError:
    winreg = None

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "LEVITAN"


def _autostart_command():
    # Для собранного exe — путь к самому LEVITAN.exe; из исходников — pythonw.
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'pythonw "{os.path.join(BASE, "levitan.py")}"'


def set_autostart(enable):
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, _APP_NAME, 0, winreg.REG_SZ,
                                  _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, _APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log.error("Автозапуск (запись реестра): %s", e)
        return False


# --- Иконки статуса ---------------------------------------------------------
_COLORS = {
    "loading": (130, 130, 130),
    "idle": (40, 170, 70),
    "recording": (210, 50, 50),
    "busy": (230, 150, 30),
}


def make_icon(state):
    """Фирменный микрофон LEVITAN (тот же контур, что #mic на сайте)
    в цвете статуса. Рисуем в 96px и уменьшаем до 64 — для сглаживания."""
    color = _COLORS.get(state, (130, 130, 130))
    img = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = 6
    d.rounded_rectangle([38, 13, 58, 53], radius=10, outline=color, width=w)
    d.arc([30, 25, 66, 61], 0, 180, fill=color, width=w)   # дужка-держатель
    d.line([48, 61, 48, 76], fill=color, width=w)          # ножка
    d.line([38, 79, 58, 79], fill=color, width=w)          # подставка
    d.line([66, 23, 86, 23], fill=color, width=w)          # волны-строки
    d.line([66, 31, 81, 31], fill=color, width=w)
    d.line([66, 39, 76, 39], fill=color, width=w)
    return img.resize((64, 64), Image.LANCZOS)


_STATUS_TEXT = {
    "loading": "загрузка модели...",
    "idle": "жду (зажми комбо)",
    "recording": "● запись...",
    "busy": "распознаю...",
}


class Levitan:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sr = cfg["sample_rate"]
        self.combo = [k.strip() for k in cfg["combo"].lower().split("+")]
        self.recording = False
        self.frames = []
        self.lock = threading.Lock()
        self._reload_lock = threading.Lock()
        self.state = "loading"
        self.icon = None
        model_path = resolve_model(cfg["model"])
        if model_path != cfg["model"]:
            log.info("Использую локальную модель из папки: %s", model_path)
        self.model_name = model_path   # нужно для прогресса первой закачки
        self.rec = WhisperRecognizer(
            model_size=model_path, device=cfg["device"],
            compute_type=cfg["compute_type"], sample_rate=self.sr,
            beam_size=cfg.get("beam_size", 1),
            cpu_threads=cfg.get("cpu_threads", 0),
        )

    # --- статус/иконка ---
    def set_state(self, state):
        self.state = state
        if self.icon:
            self.icon.icon = make_icon(state)
            self.icon.title = f"LEVITAN — {_STATUS_TEXT.get(state, state)}"

    # --- аудио ---
    def _audio_cb(self, indata, frames, time_info, status):
        if self.recording:
            self.frames.append(indata.copy())

    def _combo_down(self):
        try:
            return all(keyboard.is_pressed(k) for k in self.combo)
        except Exception:
            return False

    def _on_event(self, e):
        down = self._combo_down()
        if down and not self.recording:
            with self.lock:
                self.recording = True
                self.frames = []
            beep(True)
            self.set_state("recording")
        elif not down and self.recording:
            with self.lock:
                self.recording = False
                frames = self.frames
                self.frames = []
            beep(False)
            threading.Thread(target=self._process, args=(frames,),
                             daemon=True).start()

    def _process(self, frames):
        if not frames:
            self.set_state("idle")
            return
        self.set_state("busy")
        audio = np.concatenate(frames).flatten()
        t0 = time.time()
        raw = self.rec.transcribe(audio)
        if self._code_active():
            code = code_1c.to_code(raw)
            move_left = 0
            if code_1c.CARET in code:
                move_left = len(code) - code.index(code_1c.CARET) - 1
                code = code_1c.strip_caret(code)
            self._log_result(t0, code, "код 1С")
            if code.strip():
                self._insert(code, move_left=move_left)
        else:
            text = postprocess.process(
                raw, mode=self.cfg["mode"], capitalize=self.cfg["capitalize"])
            self._log_result(t0, text)
            if text:
                self._insert(text)
        self.set_state("idle")

    def _log_result(self, t0, text, tag=""):
        """Записать факт распознавания.

        По умолчанию САМ ТЕКСТ в лог не пишется: человек диктует пароли,
        переписку и рабочие данные, и им нечего делать в файле на диске.
        В лог идут только время и длина — этого хватает, чтобы понять,
        что распознавание отработало. Включить полный текст можно
        флагом "debug_log": true в levitan.json, осознанно и на время.
        """
        mark = f"[{tag}]" if tag else ""
        if self.cfg.get("debug_log", False):
            log.info("[%.1fc]%s %r", time.time() - t0, mark, text)
        else:
            log.info("[%.1fc]%s распознано, символов: %d",
                     time.time() - t0, mark, len(text))

    def _code_active(self):
        """Активен ли режим «код 1С»: ручной тумблер ИЛИ авто в Конфигураторе."""
        if self.cfg.get("code_mode", False):
            return True
        if self.cfg.get("code_auto", False):
            try:
                return code_1c.active_window_is_1c()
            except Exception:
                return False
        return False

    def _insert(self, text, move_left=0):
        for _ in range(30):
            if not any(keyboard.is_pressed(k) for k in self.combo):
                break
            time.sleep(0.02)
        prev = None
        if self.cfg["restore_clipboard"]:
            try:
                prev = pyperclip.paste()
            except Exception:
                pass
        pyperclip.copy(text)
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        if move_left > 0:                    # подвести каретку к месту курсора ‸
            time.sleep(0.06)
            for _ in range(move_left):
                keyboard.send("left")
        if prev is not None:
            def restore():
                time.sleep(1.0)
                try:
                    pyperclip.copy(prev)
                except Exception:
                    pass
            threading.Thread(target=restore, daemon=True).start()

    def _watch_download(self, model_name, done):
        """Пока идёт первая закачка модели — показывать прогресс в трее.

        Точного числа байт от faster-whisper не получить, поэтому считаем
        по размеру каталога кэша: для пользователя важно видеть движение,
        а не точность до мегабайта.
        """
        total_mb = _MODEL_MB.get(model_name, 500)
        if not _model_cache_path(model_name):
            return
        log.info("Модели нет в кэше — качаем ~%d МБ, показываю прогресс", total_mb)
        while True:
            try:
                done_mb = _downloaded_mb(model_name)
                pct = min(99, int(done_mb / total_mb * 100)) if total_mb else 0
                if self.icon:
                    self.icon.title = (f"LEVITAN — загрузка модели {pct}% "
                                       f"({done_mb:.0f} из {total_mb} МБ)")
            except Exception as e:
                log.debug("Прогресс закачки не посчитался: %s", e)
            if done.wait(1.5):
                break
        if self.icon:
            self.icon.title = "LEVITAN — модель загружена"

    # --- запуск ---
    def _load_and_arm(self):
        # ХОЛОДНЫЙ старт (автозапуск при загрузке Windows): звук и система могут
        # быть ещё не готовы. Не виснем в «сером значке» — ждём поднятия системы
        # и повторяем инициализацию с паузами.
        up = _system_uptime_sec()
        if up < 90:
            delay = min(20.0, 90.0 - up)
            log.info("Холодный старт (аптайм %.0fс) — пауза %.0fс перед init",
                     up, delay)
            time.sleep(delay)

        # 1) Модель — с повторами. Если её ещё нет в кэше, пойдёт закачка на
        #    сотни мегабайт: показываем прогресс в подсказке трея, иначе
        #    выглядит как зависший серый значок.
        watcher = None
        if not model_in_cache(self.model_name):
            watcher = threading.Event()
            threading.Thread(target=self._watch_download,
                             args=(self.model_name, watcher),
                             daemon=True).start()
        try:
            for attempt in range(1, 13):
                if self.rec.is_initialized or self.rec.load_model():
                    break
                log.warning("Модель не загрузилась (попытка %d/12) — повтор через 5с",
                            attempt)
                time.sleep(5)
            else:
                log.error("Модель так и не загрузилась — выход")
                if self.icon:
                    self.icon.stop()
                return
        finally:
            if watcher:
                watcher.set()

        # 2) Микрофон — с повторами (после холодной загрузки устройство ввода
        #    может быть ещё не поднято)
        self.stream = None
        for attempt in range(1, 13):
            try:
                self.stream = sd.InputStream(samplerate=self.sr, channels=1,
                                             dtype="float32",
                                             callback=self._audio_cb)
                self.stream.start()
                break
            except Exception as e:
                log.warning("Микрофон не готов (попытка %d/12): %s — повтор 3с",
                            attempt, e)
                time.sleep(3)
        if self.stream is None:
            log.error("Микрофон так и не поднялся — апп живой, но без записи")
            self.set_state("loading")
            return

        # 3) Хук клавиатуры
        try:
            keyboard.hook(self._on_event)
        except Exception as e:
            log.error("Не удалось поставить хук клавиатуры: %s", e)

        # Привести автозапуск в соответствие с конфигом (по умолчанию вкл).
        set_autostart(self.cfg.get("autostart", True))
        self.set_state("idle")
        log.info("LEVITAN готов. Комбо: %s | автозапуск: %s",
                 "+".join(self.combo), self.cfg.get("autostart", True))

    def _set_model(self, name):
        """Сменить модель на лету из трея (перезагружает движок в фоне)."""
        # Синхронно отсекаем повторный вызов, пока идёт перезагрузка —
        # чтобы быстрые двойные сохранения настроек не наложились.
        with self._reload_lock:
            if self.state == "loading":
                return
            self.set_state("loading")
        def worker():
            try:
                self.cfg["model"] = name
                save_config(self.cfg)
                mp = resolve_model(name)
                rec = WhisperRecognizer(
                    model_size=mp, device=self.cfg["device"],
                    compute_type=self.cfg["compute_type"], sample_rate=self.sr,
                    beam_size=self.cfg.get("beam_size", 1),
                    cpu_threads=self.cfg.get("cpu_threads", 0),
                )
                if rec.load_model():
                    self.rec = rec
                    log.info("Модель переключена -> %s", name)
                else:
                    log.error("Не удалось загрузить модель %s", name)
            finally:
                self.set_state("idle")
        threading.Thread(target=worker, daemon=True).start()

    def apply_settings(self, new):
        """Применить настройки из окна на лету: модель/beam/threads -> перезагрузка
        движка; комбо/режим/автозапуск -> сразу."""
        old = dict(self.cfg)
        need_reload = (
            new.get("model") != old.get("model")
            or new.get("beam_size") != old.get("beam_size")
            or new.get("cpu_threads") != old.get("cpu_threads")
        )
        self.cfg.update(new)
        self.combo = [k.strip() for k in self.cfg["combo"].lower().split("+")]
        save_config(self.cfg)
        set_autostart(self.cfg.get("autostart", True))
        log.info("Настройки применены: %s", self.cfg)
        if need_reload:
            self._set_model(self.cfg["model"])

    def _open_settings(self, icon, item):
        try:
            import settings_window
            settings_window.open_settings(self.cfg, self.apply_settings)
        except Exception as e:
            log.error("Окно настроек: %s", e)

    def _toggle_code_mode(self, icon, item):
        self.cfg["code_mode"] = not self.cfg.get("code_mode", False)
        save_config(self.cfg)
        log.info("Режим кода 1С: %s", self.cfg["code_mode"])

    def _toggle_autostart(self, icon, item):
        new = not self.cfg.get("autostart", True)
        self.cfg["autostart"] = new
        save_config(self.cfg)
        set_autostart(new)
        log.info("Автозапуск переключён: %s", new)

    # pystray разрешает у обработчиков только 0/1/2 аргумента (иначе ValueError),
    # поэтому имя модели захватываем фабрикой, а не третьим параметром лямбды.
    def _make_model_action(self, name):
        def action(icon, item):
            self._set_model(name)
        return action

    def _make_model_checked(self, name):
        def checked(item):
            return self.cfg.get("model") == name
        return checked

    def _model_menu(self):
        # auto/small/medium/turbo — радио-выбор, галочка = текущая модель.
        opts = [("Авто (по CPU)", "auto"),
                ("small — быстро", "small"),
                ("medium — баланс", "medium"),
                ("large-v3-turbo — точно, но медленно", "large-v3-turbo")]
        return pystray.Menu(*[
            pystray.MenuItem(title, self._make_model_action(name),
                             checked=self._make_model_checked(name), radio=True)
            for title, name in opts
        ])

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda i: f"LEVITAN — {_STATUS_TEXT.get(self.state, '')}",
                             None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Настройки…", self._open_settings, default=True),
            pystray.MenuItem("Модель", self._model_menu()),
            pystray.MenuItem("Режим кода 1С", self._toggle_code_mode,
                             checked=lambda i: self.cfg.get("code_mode", False)),
            pystray.MenuItem("Автозапуск с Windows", self._toggle_autostart,
                             checked=lambda i: self.cfg.get("autostart", True)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._quit),
        )

    def run(self):
        self.icon = pystray.Icon("levitan", make_icon("loading"),
                                 "LEVITAN — загрузка...", self._build_menu())
        # модель грузим в фоне, чтобы трей появился сразу
        threading.Thread(target=self._load_and_arm, daemon=True).start()
        self.icon.run()

    def _quit(self, icon, item):
        log.info("Выход")
        try:
            self.stream.stop()
        except Exception:
            pass
        icon.stop()


def main():
    cfg = load_config()
    log.info("Конфиг: %s", cfg)
    Levitan(cfg).run()


if __name__ == "__main__":
    main()
