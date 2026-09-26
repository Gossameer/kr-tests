"""
Pydantic-схемы: описание того, что приходит в запросах и что уходит в ответах.

Pydantic — библиотека валидации. Мы описываем данные классами, а Pydantic сам
проверяет типы и правила. Если что-то не так — FastAPI вернёт ошибку 422,
а текст ошибки мы переводим на понятный русский (см. app/errors.py).
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

# Разумные ограничения, чтобы в базу не попало что-то огромное.
MAX_TITLE_LEN = 300
MAX_QUESTIONS = 100
MAX_QUESTION_LEN = 2000
MAX_OPTIONS = 10
MAX_OPTION_LEN = 500


# =====================================================================
# Вход: JSON, который учитель вставляет в поле на сайте
# =====================================================================


class QuestionIn(BaseModel):
    """Один вопрос: текст, варианты ответа и номер правильного варианта."""

    text: str
    options: list[str]
    # correct — индекс правильного варианта, считая с нуля.
    correct: int


class TestCreate(BaseModel):
    """Вся контрольная целиком."""

    title: str
    questions: list[QuestionIn]
    # Поля нет в JSON от ИИ, но колонка в базе NOT NULL — поэтому значение по умолчанию.
    teacher_name: str = "Учитель"

    @field_validator("title", "teacher_name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        """Убираем пробелы по краям и запрещаем пустую строку."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("не может быть пустым")
        if len(cleaned) > MAX_TITLE_LEN:
            raise ValueError(f"слишком длинное, максимум {MAX_TITLE_LEN} символов")
        return cleaned

    @model_validator(mode="after")
    def check_questions(self) -> "TestCreate":
        """
        Проверяем список вопросов целиком.

        Все проверки здесь, а не в QuestionIn, ради текста ошибки:
        так мы знаем номер вопроса и пишем «Вопрос 3: ...» вместо
        непонятного «questions -> 2 -> correct».
        """
        if not self.questions:
            raise ValueError("В контрольной должен быть хотя бы один вопрос")

        if len(self.questions) > MAX_QUESTIONS:
            raise ValueError(f"Слишком много вопросов, максимум {MAX_QUESTIONS}")

        for index, question in enumerate(self.questions, start=1):
            # `where` подставляется в начало каждой ошибки этого вопроса.
            where = f"Вопрос {index}"

            question.text = question.text.strip()
            if not question.text:
                raise ValueError(f"{where}: текст вопроса пустой")
            if len(question.text) > MAX_QUESTION_LEN:
                raise ValueError(
                    f"{where}: текст вопроса длиннее {MAX_QUESTION_LEN} символов"
                )

            if len(question.options) < 2:
                raise ValueError(
                    f"{where}: нужно минимум 2 варианта ответа, "
                    f"а указано {len(question.options)}"
                )
            if len(question.options) > MAX_OPTIONS:
                raise ValueError(
                    f"{where}: слишком много вариантов ответа, максимум {MAX_OPTIONS}"
                )

            question.options = [option.strip() for option in question.options]
            for option_index, option in enumerate(question.options, start=1):
                if not option:
                    raise ValueError(f"{where}: вариант ответа №{option_index} пустой")
                if len(option) > MAX_OPTION_LEN:
                    raise ValueError(
                        f"{where}: вариант ответа №{option_index} длиннее "
                        f"{MAX_OPTION_LEN} символов"
                    )

            # Повторяющиеся варианты — почти всегда ошибка генерации.
            if len(set(question.options)) != len(question.options):
                raise ValueError(f"{where}: есть одинаковые варианты ответа")

            # Главная проверка: correct должен указывать на существующий вариант.
            if not 0 <= question.correct < len(question.options):
                raise ValueError(
                    f"{where}: correct = {question.correct}, "
                    f"но вариантов {len(question.options)} — "
                    f"допустимы значения от 0 до {len(question.options) - 1} "
                    f"(нумерация с нуля)"
                )

        return self


# =====================================================================
# Выход: что отдаём фронтенду
# =====================================================================


class TestCreated(BaseModel):
    """Ответ на POST /api/tests."""

    id: int
    code: str = Field(description="Короткий код для ссылки ученикам")
    title: str
    questions_count: int


class OptionOut(BaseModel):
    """Вариант ответа в превью для учителя (с признаком правильного)."""

    id: int
    text: str
    is_correct: bool
    position: int


class QuestionOut(BaseModel):
    id: int
    text: str
    position: int
    options: list[OptionOut]


class TestOut(BaseModel):
    """
    Ответ на GET /api/tests/{code} — превью для учителя.

    ВНИМАНИЕ: здесь есть is_correct. Для страницы ученика понадобится
    отдельная схема без этого поля, иначе правильные ответы утекут в браузер.
    """

    id: int
    code: str
    title: str
    teacher_name: str
    is_published: bool
    created_at: datetime
    questions: list[QuestionOut]
