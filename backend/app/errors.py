"""
Перевод ошибок валидации на понятный русский язык.

Зачем: по умолчанию FastAPI на неверные данные отвечает вот таким:

    {"detail":[{"type":"int_parsing","loc":["body","questions",1,"correct"],
                "msg":"Input should be a valid integer", ...}]}

Учителю это ни о чём не говорит. Обработчик ниже превращает такое в:

    {"detail":"Вопрос 2, поле «correct»: должно быть целым числом"}
"""

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Понятные названия полей вместо английских ключей JSON.
FIELD_NAMES = {
    "title": "название (title)",
    "questions": "список вопросов (questions)",
    "options": "варианты ответа (options)",
    "correct": "номер правильного ответа (correct)",
    "text": "текст (text)",
    "teacher_name": "имя учителя (teacher_name)",
    "student_name": "фамилия и имя (student_name)",
    "student_class": "класс (student_class)",
    "answers": "ответы (answers)",
}

# Человеческие формулировки для стандартных типов ошибок Pydantic.
TYPE_MESSAGES = {
    "missing": "обязательное поле не заполнено",
    "json_invalid": "тело запроса не является корректным JSON",
    "string_type": "значение должно быть строкой",
    "int_type": "значение должно быть целым числом",
    "int_parsing": "значение должно быть целым числом",
    "list_type": "значение должно быть списком",
    "dict_type": "значение должно быть объектом",
    "model_attributes_type": "значение должно быть объектом",
    "bool_type": "значение должно быть true или false",
}


def describe_location(loc: tuple) -> str:
    """
    Превращает путь до ошибки в человеческое описание.

    ("body", "questions", 1, "correct")  ->  'Вопрос 2, поле «номер правильного ответа (correct)»'
    ("body", "title")                    ->  'Поле «название (title)»'
    ("body",)                            ->  '' (ошибка относится ко всей контрольной)
    """
    # Первый элемент — всегда "body"/"query"/"path", он читателю не нужен.
    parts = [part for part in loc if part not in ("body", "query", "path")]

    prefix = ""
    # Шаблон "questions -> <номер> -> ..." означает конкретный вопрос.
    if len(parts) >= 2 and parts[0] == "questions" and isinstance(parts[1], int):
        prefix = f"Вопрос {parts[1] + 1}"  # в JSON индексы с нуля, человеку показываем с единицы
        parts = parts[2:]

    if parts:
        field = parts[-1]
        if isinstance(field, int):
            # Число в конце пути осмысленно только внутри options: это номер варианта.
            # В остальных случаях (например, позиция символа в битом JSON) число
            # читателю ничего не говорит — опускаем его.
            if len(parts) >= 2 and parts[-2] == "options":
                human = f"вариант ответа №{field + 1}"
            else:
                return prefix
        else:
            human = f"«{FIELD_NAMES.get(str(field), field)}»"
        prefix = f"{prefix}, поле {human}" if prefix else f"Поле {human}"

    return prefix


def describe_error(error: dict) -> str:
    """Собирает одну строку ошибки: «где» + «что не так»."""
    error_type = error.get("type", "")
    raw_message = error.get("msg", "Некорректное значение")

    if error_type == "json_invalid":
        # Здесь в loc лежит позиция символа, а не поле — место указывать бессмысленно.
        return (
            "Это не похоже на корректный JSON: проверьте запятые, кавычки и скобки"
        )

    if error_type == "value_error":
        # Это наши собственные проверки из app/schemas.py.
        # Pydantic добавляет к ним приставку "Value error, " — убираем её.
        message = raw_message.removeprefix("Value error, ")
        # Наши сообщения про вопросы уже начинаются с «Вопрос N: ...» —
        # второй раз номер вопроса подставлять не надо.
        if message.startswith("Вопрос ") or message[:1].isupper():
            location = describe_location(error.get("loc", ()))
            # Для ошибки поля (например, пустого title) приписку сохраняем.
            if location and not message.startswith("Вопрос "):
                return f"{location}: {message}"
            return message
    else:
        message = TYPE_MESSAGES.get(error_type, raw_message)

    location = describe_location(error.get("loc", ()))
    return f"{location}: {message}" if location else message


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Обработчик, который FastAPI вызывает вместо стандартного
    (подключается в app/main.py).
    """
    messages = [describe_error(error) for error in exc.errors()]

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            # detail — одна готовая строка, её фронтенд показывает учителю.
            "detail": " ; ".join(messages),
            # errors — список по отдельности, если понадобится показать построчно.
            "errors": messages,
        },
    )
