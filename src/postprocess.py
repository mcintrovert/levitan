"""
Постобработка распознанного текста.

Режимы (mode):
  - "commands" (по умолчанию): убираем авто-пунктуацию Whisper и ставим
        знаки ТОЛЬКО по голосовым командам ("запятая" -> ","). Предсказуемо,
        как в Google Docs, без задвоений.
  - "auto": доверяем пунктуации Whisper, командные слова не трогаем
        (быстрая диктовка без явных команд).

Границы слов заданы через Unicode-lookaround, а не \b — чтобы кириллица
вела себя одинаково на любом Python/ОС.
"""

import re
import difflib

# Группы синонимов -> символ. Длинные фразы обрабатываются раньше коротких
# (сортировка ниже), чтобы "точка с запятой" не разбилась на "точка".
_COMMANDS = [
    (["точка с запятой"], ";"),
    (["вопросительный знак", "знак вопроса"], "?"),
    (["восклицательный знак"], "!"),
    (["новый абзац", "новая строка", "с новой строки", "перенос строки"], "\n"),
    (["абзац"], "\n"),
    (["многоточие"], "…"),
    (["двоеточие"], ":"),
    (["запятая"], ","),
    (["точка"], "."),
    (["тире"], " — "),
    (["дефис"], "-"),
    (["открыть скобку", "открывающая скобка"], "("),
    (["закрыть скобку", "закрывающая скобка"], ")"),
    (["кавычки"], '"'),
]

_L = r"[^\W\d_]"  # одна Unicode-буква
_SENT_END = set(".?!…")

_COMPILED = []
for _phrases, _repl in _COMMANDS:
    for _ph in _phrases:
        _pat = re.compile(rf"(?<!{_L}){re.escape(_ph)}(?!{_L})", re.IGNORECASE)
        _COMPILED.append((_pat, _repl, len(_ph)))
_COMPILED.sort(key=lambda x: -x[2])  # длинные фразы первыми


def _strip_auto_punct(text: str) -> str:
    # Знаки, которые Whisper мог поставить сам, заменяем пробелом.
    return re.sub(r"[,.;:!?…—]", " ", text)


def _apply_commands(text: str) -> str:
    for pat, repl, _ in _COMPILED:
        text = pat.sub(repl, text)
    return text


# Длинные команды-фразы, которые модель часто коверкает ("восклицательный"
# -> "воскресательный"). Ловим по нечёткому совпадению. Только многословные
# и характерные — у одиночных слов риск ложных замен ("точно"->"точка").
_FUZZY = [
    ("точка с запятой", ";"),
    ("вопросительный знак", "?"),
    ("восклицательный знак", "!"),
    ("знак вопроса", "?"),
    ("новый абзац", "\n"),
]
_FUZZY_THRESHOLD = 0.8


def _apply_fuzzy(text: str) -> str:
    words = text.split()
    out = []
    i = 0
    while i < len(words):
        matched = False
        for n in (3, 2):  # длина фразы в словах
            if i + n > len(words):
                continue
            window = " ".join(words[i:i + n]).lower().strip(".,!?;:«»\"")
            for phrase, sym in _FUZZY:
                if len(phrase.split()) != n:
                    continue
                ratio = difflib.SequenceMatcher(None, window, phrase).ratio()
                if ratio >= _FUZZY_THRESHOLD:
                    out.append(sym)
                    i += n
                    matched = True
                    break
            if matched:
                break
        if not matched:
            out.append(words[i])
            i += 1
    return " ".join(out)


def _normalize_spacing(text: str) -> str:
    text = re.sub(r"\s+([,.;:?!…)])", r"\1", text)   # нет пробела перед прилипающими
    text = re.sub(r"\(\s+", "(", text)                # после ( нет пробела
    text = re.sub(r"([,.;:?!…])(?=[^\s\d.)])", r"\1 ", text)  # пробел после знака
    # схлопнуть задвоенные одинаковые знаки: ",," -> ",", "!." -> "!"
    text = re.sub(r"([,.;:!?…])[\s]*[,.;:!?…]+", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)            # лишние пробелы
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)      # пробелы вокруг переносов
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_LETTER = r"[^\W\d_]"


def _cap_first(m):
    return m.group(1) + m.group(2).upper()


def _capitalize_sentences(text: str) -> str:
    # Первая буква текста.
    text = re.sub(rf"^(\s*)({_LETTER})", _cap_first, text, count=1)
    # После конца предложения (. ? ! …) и ОБЯЗАТЕЛЬНО пробела/переноса —
    # так десятичная точка "3.14" не капитализирует следующее слово.
    text = re.sub(rf"([.?!…][\s]+)({_LETTER})", _cap_first, text)
    # После переноса строки.
    text = re.sub(rf"(\n\s*)({_LETTER})", _cap_first, text)
    return text


def process(text: str, mode: str = "smart", capitalize: bool = True) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if mode == "commands":
        # Только голосовые команды: режем авто-пунктуацию Whisper.
        text = _strip_auto_punct(text)
        text = _apply_commands(text)
        text = _apply_fuzzy(text)
    elif mode == "smart":
        # Гибрид: пунктуацию Whisper оставляем + команды поверх неё.
        # Задвоения схлопнет _normalize_spacing.
        text = _apply_commands(text)
        text = _apply_fuzzy(text)
    # mode == "auto": ничего не трогаем, доверяем Whisper полностью
    text = _normalize_spacing(text)
    if capitalize:
        text = _capitalize_sentences(text)
    return text


if __name__ == "__main__":
    tests = [
        "Проверка связи, запятая голос, вот работает восклицательный знак.",
        "сегодня купили молоко хлеб и сыр точка завтра поедем на дачу",
        "первый пункт новый абзац второй пункт",
        "это тест точка с запятой и ещё кусок",
        "открыть скобку важно закрыть скобку",
    ]
    for t in tests:
        print("IN  :", repr(t))
        print("CMD :", repr(process(t, "commands")))
        print("AUTO:", repr(process(t, "auto")))
        print("-" * 60)
