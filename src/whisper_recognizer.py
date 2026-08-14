"""
Движок распознавания речи на faster-whisper (CTranslate2).

Заменяет старый VOSK. Работает по принципу "целая фраза -> текст":
push-to-talk даёт полный аудиобуфер на отпускании клавиши, его и
распознаём за один проход. Whisper сам расставляет базовую пунктуацию,
дальше текст уходит в postprocess для голосовых команд и косметики.
"""

import logging
import time

import numpy as np

try:
    from faster_whisper import WhisperModel
except ImportError:
    raise ImportError(
        "faster-whisper не установлен. Установите: pip install faster-whisper"
    )

logger = logging.getLogger(__name__)


class WhisperRecognizer:
    """Распознаватель речи на базе faster-whisper."""

    def __init__(
        self,
        model_size="large-v3",
        device="cpu",
        compute_type="int8",
        language="ru",
        beam_size=5,
        cpu_threads=0,
        sample_rate=16000,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.cpu_threads = cpu_threads
        self.sample_rate = sample_rate
        self.model = None
        self.is_initialized = False

    def load_model(self):
        """Загрузить модель (первый раз скачается с HuggingFace в кэш)."""
        try:
            logger.info(
                "Загрузка Whisper: %s (%s / %s)...",
                self.model_size, self.device, self.compute_type,
            )
            t0 = time.time()
            kw = dict(device=self.device, compute_type=self.compute_type,
                      cpu_threads=self.cpu_threads)
            try:
                # Сначала пробуем ПОЛНОСТЬЮ ОФЛАЙН: только локальный кэш,
                # никаких обращений к сети (важно для конфиденциальной работы).
                self.model = WhisperModel(
                    self.model_size, local_files_only=True, **kw)
                logger.info("Модель из локального кэша (офлайн, без сети)")
            except Exception:
                # Модели ещё нет в кэше — разовая закачка из сети,
                # дальше все запуски будут офлайн.
                logger.info("Модели нет в кэше — разовая загрузка из сети...")
                self.model = WhisperModel(self.model_size, **kw)
            self.is_initialized = True
            logger.info("Модель загружена за %.1f c", time.time() - t0)
            return True
        except Exception as e:
            logger.error("Ошибка загрузки модели Whisper: %s", e)
            self.is_initialized = False
            return False

    @staticmethod
    def pcm16_to_float32(audio_bytes: bytes) -> np.ndarray:
        """int16 PCM (как отдаёт PyAudio) -> float32 [-1, 1] для Whisper."""
        ints = np.frombuffer(audio_bytes, dtype=np.int16)
        return ints.astype(np.float32) / 32768.0

    def transcribe(self, audio) -> str:
        """
        Распознать целый фрагмент.

        audio: np.ndarray float32 моно 16кГц в диапазоне [-1, 1],
               либо bytes int16 PCM — тогда сконвертируем сами.
        """
        if not self.is_initialized:
            logger.warning("Распознаватель не инициализирован")
            return ""

        if isinstance(audio, (bytes, bytearray)):
            audio = self.pcm16_to_float32(bytes(audio))
        audio = np.asarray(audio, dtype=np.float32).flatten()

        if audio.size < self.sample_rate * 0.2:  # < 0.2 c — почти тишина
            logger.info("Слишком короткий фрагмент (%d сэмплов)", audio.size)
            return ""

        try:
            t0 = time.time()
            segments, info = self.model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
                condition_on_previous_text=False,
                temperature=0.0,  # один проход, без скрытых пере-расчётов (стабильнее/быстрее)
                without_timestamps=True,  # тайм-коды не нужны для вставки: декодер
                                          # генерит меньше токенов, замер дал ~5%
            )
            # Умная склейка сегментов: Whisper начинает КАЖДЫЙ сегмент с
            # заглавной (в т.ч. после паузы-вдоха). Если перед сегментом не
            # было конца предложения (. ? ! …) — опускаем первую букву в
            # строчную, чтобы не было заглавных посреди фразы. Имена
            # собственные внутри сегмента и заглавные после точек остаются.
            parts = []
            prev_ends_sentence = True  # самый первый сегмент — начало, не трогаем
            for seg in segments:
                t = seg.text.strip()
                if not t:
                    continue
                if not prev_ends_sentence and t[:1].isupper():
                    t = t[:1].lower() + t[1:]
                parts.append(t)
                prev_ends_sentence = t[-1] in ".?!…"
            text = " ".join(parts).strip()
            # Сам текст в лог не пишем: пользователь диктует пароли, переписку
            # и рабочие данные, им нечего делать в файле на диске. Для
            # диагностики хватает времени и объёма.
            logger.info(
                "Распознано (%.1fs аудио за %.1fs), символов: %d",
                audio.size / self.sample_rate, time.time() - t0, len(text),
            )
            return text
        except Exception as e:
            logger.error("Ошибка распознавания: %s", e)
            return ""

    def cleanup(self):
        self.model = None
        self.is_initialized = False
