"""
Сравнение ответа ученика с правильным.

Задача: не засчитать ошибку из-за записи. Ученик пишет с телефона, и «0,50»,
«0.5 », «0,5» — это один и тот же ответ. При этом нельзя засчитывать неверное
по существу: «15» и «1 5» с точки зрения записи разные ответы, но «1 000» и
«1000» — один (пробел как разделитель разрядов).

Что делаем:
  * убираем пробелы по краям и внутри (в числах это разделитель разрядов);
  * приводим к нижнему регистру;
  * ё → е;
  * запятую в десятичных меняем на точку;
  * разметку формул снимаем: «$\frac{3}{5}$» и «3/5» — один ответ, «2{,}5» = «2,5»;
  * если обе записи — числа, сравниваем ЧИСЛА, а не строки:
    так «0,50» = «0.5», «2.0» = «2».

Всё сравнение идёт на сервере: ответы ученику не отправляются.
"""

import re
from decimal import Decimal, InvalidOperation

from app.formulas import plain_text

# Смешанное число «2 1/3»: пробел здесь значимый, иначе получится «21/3».
MIXED_RE = re.compile(r"(\d)\s+(?=\d+\s*/\s*\d)")
# Минус и тире, которые подставляют телефоны и Word.
DASHES = ("\u2212", "\u2013", "\u2014")


def normalize_answer(raw: str) -> str:
    """Приводит ответ к единому виду для сравнения строк."""
    # Разметку формул снимаем до смены регистра: команды LaTeX к нему чувствительны.
    text = plain_text(raw.strip()).lower().replace("ё", "е")
    for dash in DASHES:
        text = text.replace(dash, "-")
    text = MIXED_RE.sub(r"\1_", text)

    # Неразрывный и узкий пробелы тоже встречаются — они прилетают из Word.
    for space in (" ", " ", " "):
        text = text.replace(space, " ")

    # Пробелы внутри убираем целиком: «1 000» → «1000», «2 x» → «2x».
    text = "".join(text.split())

    # Запятая как десятичный разделитель.
    text = text.replace(",", ".")

    return text


def to_number(text: str) -> Decimal | None:
    """
    Пробует прочитать нормализованную строку как число.

    Decimal, а не float: «0.1 + 0.2» в float даёт 0.30000000000000004,
    и сравнение дробей стало бы ненадёжным.
    """
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    # «nan», «inf», «snan» Decimal тоже читает, но это не числа-ответы, а
    # сравнение с sNaN вообще бросает исключение — такие записи сравниваем как текст.
    return number if number.is_finite() else None


def answers_match(student_raw: str, accepted_raw: str) -> bool:
    """Совпадает ли ответ ученика с одним из допустимых."""
    student = normalize_answer(student_raw)
    accepted = normalize_answer(accepted_raw)

    if not student:
        return False

    if student == accepted:
        return True

    # Числа сравниваем по значению: «0.50» == «0.5», «2.0» == «2».
    student_number = to_number(student)
    accepted_number = to_number(accepted)
    if student_number is not None and accepted_number is not None:
        return student_number == accepted_number

    return False


def check_input_answer(student_raw: str, accepted_list: list[str]) -> bool:
    """Верен ли введённый ответ: засчитываем совпадение с ЛЮБЫМ из допустимых."""
    return any(answers_match(student_raw, accepted) for accepted in accepted_list)
