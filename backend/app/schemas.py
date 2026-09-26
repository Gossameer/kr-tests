"""
Pydantic-схемы: описание того, что приходит в запросах и что уходит в ответах.

Pydantic — библиотека валидации. Мы описываем данные классами, а Pydantic сам
проверяет типы и правила. Если что-то не так — FastAPI вернёт ошибку 422,
а текст ошибки мы переводим на понятный русский (см. app/errors.py).
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.names import normalize_class_name, normalize_full_name

# Разумные ограничения, чтобы в базу не попало что-то огромное.
MAX_TITLE_LEN = 300
MAX_QUESTIONS = 100
MAX_QUESTION_LEN = 2000
MAX_OPTIONS = 10
MAX_OPTION_LEN = 500
MAX_CLASSES = 30
MAX_CLASS_LEN = 20


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
    # ФИО учителя обязательно: учитель вводит его в форме создания.
    teacher_name: str
    # Классы, для которых эта контрольная. Ученик выбирает свой из этого списка,
    # поэтому пустым он быть не может.
    classes: list[str]
    # Перемешивать ли вопросы и варианты у каждого ученика.
    shuffle: bool = True

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

    @field_validator("classes")
    @classmethod
    def check_classes(cls, value: list[str]) -> list[str]:
        """
        Приводит список классов в порядок: убирает пробелы, пустые значения
        и повторы, сохраняя порядок, в котором учитель их написал.
        """
        cleaned: list[str] = []
        for item in value:
            name = " ".join(item.split())
            if not name:
                continue  # пустые значения просто пропускаем
            if len(name) > MAX_CLASS_LEN:
                raise ValueError(
                    f"название класса «{name[:20]}…» длиннее {MAX_CLASS_LEN} символов"
                )
            if name not in cleaned:  # повтор — не ошибка, просто лишнее
                cleaned.append(name)

        if not cleaned:
            raise ValueError("укажите хотя бы один класс, например 6А")
        if len(cleaned) > MAX_CLASSES:
            raise ValueError(f"слишком много классов, максимум {MAX_CLASSES}")

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
    results_token: str = Field(
        description="Длинный секрет для ссылки на результаты — только для учителя"
    )
    title: str
    questions_count: int


# =====================================================================
# Публичная часть: то, что видит и присылает УЧЕНИК
#
# Главное правило этого блока: ни одна схема здесь не содержит
# признака правильного ответа. Что попало в схему — то уедет в браузер
# ученика, где это легко посмотреть через «Инспектор».
# =====================================================================

MAX_STUDENT_NAME_LEN = 120
MAX_STUDENT_CLASS_LEN = 40


class PublicOptionOut(BaseModel):
    """Вариант ответа для ученика: только id и текст, БЕЗ is_correct."""

    id: int
    text: str


class PublicQuestionOut(BaseModel):
    id: int
    text: str
    position: int
    options: list[PublicOptionOut]


class PublicTestOut(BaseModel):
    """Контрольная для прохождения. Правильных ответов здесь нет."""

    code: str
    title: str
    teacher_name: str
    # Список классов — ученик выбирает свой из выпадающего списка.
    classes: list[str]
    # Перемешивать ли порядок; само перемешивание делает страница ученика.
    shuffle: bool
    # Открыт ли приём работ. Если закрыт, questions приходит пустым.
    is_open: bool
    questions: list[PublicQuestionOut]


class AttemptCreate(BaseModel):
    """
    Сданная работа.

    answers — словарь {id вопроса: id выбранного варианта}.
    Вопросы, которых нет в словаре, считаются пропущенными (это разрешено).
    """

    student_name: str
    student_class: str
    answers: dict[int, int] = {}

    @field_validator("student_name")
    @classmethod
    def check_name(cls, value: str) -> str:
        # Нормализация на сервере: «радов  мадлена» → «Радов Мадлена».
        # Делать это только на фронте нельзя — запрос легко отправить мимо страницы.
        cleaned = normalize_full_name(value)
        if not cleaned:
            raise ValueError("укажите фамилию и имя")
        if len(cleaned) > MAX_STUDENT_NAME_LEN:
            raise ValueError(f"слишком длинное, максимум {MAX_STUDENT_NAME_LEN} символов")
        return cleaned

    @field_validator("student_class")
    @classmethod
    def check_class(cls, value: str) -> str:
        cleaned = normalize_class_name(value)
        if not cleaned:
            raise ValueError("укажите класс")
        if len(cleaned) > MAX_STUDENT_CLASS_LEN:
            raise ValueError(f"слишком длинное, максимум {MAX_STUDENT_CLASS_LEN} символов")
        return cleaned


class QuestionResult(BaseModel):
    """
    Итог по одному вопросу.

    Здесь намеренно НЕТ поля с правильным вариантом: ученик узнаёт только,
    угадал он или нет. Иначе достаточно сдать работу один раз, чтобы
    получить все ответы и передать их другим.
    """

    question_id: int
    position: int
    # Выбрал ли ученик вариант вообще (false = вопрос пропущен).
    answered: bool
    # Верен ли выбранный вариант.
    is_correct: bool


class AttemptResult(BaseModel):
    """Ответ на POST /api/public/tests/{code}/attempts."""

    attempt_id: int
    student_name: str
    student_class: str
    score: int
    max_score: int
    results: list[QuestionResult]


# =====================================================================
# Результаты для учителя (доступ по секретной ссылке /r/<results_token>)
# =====================================================================


class AttemptRow(BaseModel):
    """Одна строка таблицы сдавших."""

    attempt_id: int
    student_name: str
    student_class: str
    score: int
    max_score: int
    # Процент считаем на сервере, чтобы в таблице и в Excel было одно и то же число.
    percent: int
    finished_at: datetime | None


class QuestionStat(BaseModel):
    """Сводка по одному вопросу: как класс с ним справился."""

    question_id: int
    position: int
    text: str
    correct_count: int
    wrong_count: int
    skipped_count: int


class ResultsOverview(BaseModel):
    """Ответ GET /api/results/{results_token}."""

    title: str
    teacher_name: str
    # Ученический код показываем, чтобы учитель мог заодно скопировать ссылку классу.
    code: str
    classes: list[str]
    shuffle: bool
    # Открыт ли приём работ — этим управляет переключатель на странице результатов.
    is_open: bool
    questions_count: int
    attempts_count: int
    attempts: list[AttemptRow]
    question_stats: list[QuestionStat]


class AttemptDetailItem(BaseModel):
    """Ответ ученика на один вопрос — здесь правильный вариант показывать МОЖНО."""

    question_id: int
    position: int
    question_text: str
    chosen_option_text: str | None
    correct_option_text: str | None
    answered: bool
    is_correct: bool


class AttemptDetail(BaseModel):
    """Ответ GET /api/results/{results_token}/attempts/{attempt_id}."""

    attempt_id: int
    student_name: str
    student_class: str
    score: int
    max_score: int
    percent: int
    finished_at: datetime | None
    items: list[AttemptDetailItem]


class TestSettingsUpdate(BaseModel):
    """Тело PATCH /api/results/{results_token}: что меняем в настройках."""

    # Пока настройка одна — открыт ли приём работ.
    is_open: bool


class ResultsOverviewSettings(BaseModel):
    """Ответ на PATCH: подтверждаем новое состояние."""

    is_open: bool
