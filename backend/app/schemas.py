"""
Pydantic-схемы: что приходит в запросах и что уходит в ответах.

Модель контрольной:
    контрольная → умения (что проверяем)
                → варианты (1..N), в каждом свои задания
    задание принадлежит варианту И умению, имеет формат ответа:
        'input'  — ученик вводит ответ, засчитывается любой из accepted_answers;
        'choice' — ученик выбирает один из готовых вариантов.

Главное правило: схемы с префиксом Public_ уходят УЧЕНИКУ. В них не должно быть
ни правильных ответов, ни решений — что попало в схему, то видно в браузере.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.names import normalize_class_name, normalize_full_name

# Ограничения, чтобы в базу не попало что-то огромное.
MAX_TITLE_LEN = 300
MAX_CLASSES = 30
MAX_CLASS_LEN = 20
MAX_SKILLS = 30
MAX_SKILL_TITLE_LEN = 300
MAX_TASKS_PER_SKILL = 10
MAX_VARIANTS = 20
MAX_TASK_LEN = 3000
MAX_OPTIONS = 10
MAX_OPTION_LEN = 500
MAX_ANSWERS = 10
MAX_ANSWER_LEN = 200
MAX_SOLUTION_LEN = 3000
MAX_STUDENT_NAME_LEN = 120
MAX_STUDENT_CLASS_LEN = 40

# Формат ответа на задание.
AnswerFormat = Literal["input", "choice"]

FORMAT_NAMES = {"input": "ввод ответа", "choice": "выбор из вариантов"}


# =====================================================================
# Создание контрольной (учитель)
# =====================================================================


class SkillIn(BaseModel):
    """Умение: что именно проверяет группа заданий."""

    title: str
    # Сколько заданий на это умение в каждом варианте.
    tasks_per_variant: int = 1
    # По умолчанию «ввод ответа»: так ученик не угадывает из четырёх.
    answer_format: AnswerFormat = "input"

    @field_validator("title")
    @classmethod
    def check_title(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("название умения не может быть пустым")
        if len(cleaned) > MAX_SKILL_TITLE_LEN:
            raise ValueError(f"название умения длиннее {MAX_SKILL_TITLE_LEN} символов")
        return cleaned

    @field_validator("tasks_per_variant")
    @classmethod
    def check_count(cls, value: int) -> int:
        if not 1 <= value <= MAX_TASKS_PER_SKILL:
            raise ValueError(
                f"число заданий на умение должно быть от 1 до {MAX_TASKS_PER_SKILL}"
            )
        return value


class TaskIn(BaseModel):
    """Одно задание конкретного варианта."""

    # Номер умения в списке skills, считая с единицы: так его указывает ИИ.
    skill_index: int
    text: str
    answer_format: AnswerFormat = "input"
    # Для 'choice': варианты ответа и номер верного (с нуля).
    options: list[str] = []
    correct: int | None = None
    # Для 'input': все ответы, которые засчитываются.
    accepted_answers: list[str] = []
    # Краткое решение — только для учителя.
    solution: str = ""

    @field_validator("text")
    @classmethod
    def check_text(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) > MAX_TASK_LEN:
            raise ValueError(f"текст задания длиннее {MAX_TASK_LEN} символов")
        return cleaned

    @field_validator("solution")
    @classmethod
    def check_solution(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) > MAX_SOLUTION_LEN:
            raise ValueError(f"решение длиннее {MAX_SOLUTION_LEN} символов")
        return cleaned


class VariantIn(BaseModel):
    """Один вариант контрольной целиком."""

    variant_no: int
    tasks: list[TaskIn] = []


class TestCreate(BaseModel):
    """Вся контрольная: шапка, умения и варианты с заданиями."""

    title: str
    teacher_name: str
    classes: list[str]
    variants_count: int = 1
    # Перемешивать ли порядок заданий внутри варианта у каждого ученика.
    shuffle: bool = True
    skills: list[SkillIn]
    variants: list[VariantIn]

    @field_validator("title", "teacher_name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("не может быть пустым")
        if len(cleaned) > MAX_TITLE_LEN:
            raise ValueError(f"слишком длинное, максимум {MAX_TITLE_LEN} символов")
        return cleaned

    @field_validator("classes")
    @classmethod
    def check_classes(cls, value: list[str]) -> list[str]:
        """Убирает пробелы, пустые значения и повторы, сохраняя порядок."""
        cleaned: list[str] = []
        for item in value:
            name = " ".join(item.split())
            if not name:
                continue
            if len(name) > MAX_CLASS_LEN:
                raise ValueError(f"название класса длиннее {MAX_CLASS_LEN} символов")
            if name not in cleaned:
                cleaned.append(name)

        if not cleaned:
            raise ValueError("укажите хотя бы один класс, например 6А")
        if len(cleaned) > MAX_CLASSES:
            raise ValueError(f"слишком много классов, максимум {MAX_CLASSES}")
        return cleaned

    @field_validator("variants_count")
    @classmethod
    def check_variants_count(cls, value: int) -> int:
        if not 1 <= value <= MAX_VARIANTS:
            raise ValueError(f"число вариантов должно быть от 1 до {MAX_VARIANTS}")
        return value

    @field_validator("skills")
    @classmethod
    def check_skills(cls, value: list[SkillIn]) -> list[SkillIn]:
        if not value:
            raise ValueError("добавьте хотя бы одно умение")
        if len(value) > MAX_SKILLS:
            raise ValueError(f"слишком много умений, максимум {MAX_SKILLS}")
        return value

    @model_validator(mode="after")
    def check_variants(self) -> "TestCreate":
        """
        Проверка полноты: в каждом варианте должны быть ВСЕ умения,
        нужное число заданий и совпадающий формат ответа.

        Сообщения пишем так, чтобы учитель сразу видел, где чинить:
        «Вариант 2, умение 3 «Проценты»: заданий 1, а нужно 2».
        """
        if len(self.variants) != self.variants_count:
            raise ValueError(
                f"вариантов должно быть {self.variants_count}, "
                f"а получено {len(self.variants)}"
            )

        seen_numbers = sorted(variant.variant_no for variant in self.variants)
        if seen_numbers != list(range(1, self.variants_count + 1)):
            raise ValueError(
                "номера вариантов должны идти подряд от 1 до "
                f"{self.variants_count}, а пришли: "
                + ", ".join(str(number) for number in seen_numbers)
            )

        for variant in self.variants:
            where_variant = f"Вариант {variant.variant_no}"

            for task in variant.tasks:
                if not 1 <= task.skill_index <= len(self.skills):
                    raise ValueError(
                        f"{where_variant}: задание ссылается на умение "
                        f"№{task.skill_index}, а умений всего {len(self.skills)}"
                    )

            for skill_number, skill in enumerate(self.skills, start=1):
                own = [task for task in variant.tasks if task.skill_index == skill_number]
                where = f"{where_variant}, умение {skill_number} «{skill.title}»"

                if len(own) != skill.tasks_per_variant:
                    raise ValueError(
                        f"{where}: заданий {len(own)}, а нужно {skill.tasks_per_variant}"
                    )

                for order, task in enumerate(own, start=1):
                    place = f"{where}, задание {order}"

                    if not task.text:
                        raise ValueError(f"{place}: пустой текст задания")

                    if task.answer_format != skill.answer_format:
                        raise ValueError(
                            f"{place}: формат «{FORMAT_NAMES[task.answer_format]}», "
                            f"а у умения — «{FORMAT_NAMES[skill.answer_format]}»"
                        )

                    if task.answer_format == "choice":
                        task.options = [option.strip() for option in task.options]
                        if len(task.options) < 2:
                            raise ValueError(
                                f"{place}: нужно минимум 2 варианта ответа, "
                                f"а указано {len(task.options)}"
                            )
                        if len(task.options) > MAX_OPTIONS:
                            raise ValueError(
                                f"{place}: слишком много вариантов ответа, "
                                f"максимум {MAX_OPTIONS}"
                            )
                        if any(not option for option in task.options):
                            raise ValueError(f"{place}: есть пустые варианты ответа")
                        if len(set(task.options)) != len(task.options):
                            raise ValueError(f"{place}: варианты ответа повторяются")
                        if task.correct is None or not 0 <= task.correct < len(task.options):
                            raise ValueError(
                                f"{place}: не отмечен правильный вариант "
                                f"(correct = {task.correct})"
                            )
                        task.accepted_answers = []
                    else:
                        task.accepted_answers = [
                            answer.strip()
                            for answer in task.accepted_answers
                            if answer.strip()
                        ]
                        if not task.accepted_answers:
                            raise ValueError(f"{place}: не указан правильный ответ")
                        if len(task.accepted_answers) > MAX_ANSWERS:
                            raise ValueError(
                                f"{place}: слишком много допустимых ответов, "
                                f"максимум {MAX_ANSWERS}"
                            )
                        if any(
                            len(answer) > MAX_ANSWER_LEN for answer in task.accepted_answers
                        ):
                            raise ValueError(
                                f"{place}: ответ длиннее {MAX_ANSWER_LEN} символов"
                            )
                        task.options = []
                        task.correct = None

        return self


class TestCreated(BaseModel):
    """Ответ на POST /api/tests."""

    id: int
    code: str = Field(description="Короткий код для ссылки ученикам")
    results_token: str = Field(
        description="Длинный секрет для ссылки на результаты — только для учителя"
    )
    title: str
    variants_count: int
    skills_count: int
    tasks_count: int


# =====================================================================
# Публичная часть: то, что видит и присылает УЧЕНИК
#
# Ни правильных ответов, ни решений здесь нет и быть не должно.
# =====================================================================


class PublicTestInfo(BaseModel):
    """Экран «Начать»: название, учитель, классы. Заданий тут ещё нет."""

    code: str
    title: str
    teacher_name: str
    classes: list[str]
    is_open: bool
    tasks_count: int


class PublicOptionOut(BaseModel):
    """Вариант ответа для ученика: только id и текст."""

    id: int
    text: str


class PublicTaskOut(BaseModel):
    """Задание для ученика: без правильного ответа и без решения."""

    id: int
    position: int
    text: str
    answer_format: AnswerFormat
    options: list[PublicOptionOut] = []


class StartAttemptIn(BaseModel):
    """Тело POST /api/public/tests/{code}/start."""

    student_name: str
    student_class: str

    @field_validator("student_name")
    @classmethod
    def check_name(cls, value: str) -> str:
        # Нормализация на сервере: «радов  мадлена» → «Радов Мадлена».
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
            raise ValueError("выберите класс")
        if len(cleaned) > MAX_STUDENT_CLASS_LEN:
            raise ValueError(f"слишком длинное, максимум {MAX_STUDENT_CLASS_LEN} символов")
        return cleaned


class StartAttemptOut(BaseModel):
    """
    Ответ на «Начать»: выданный вариант и его задания.

    attempt_token — секрет попытки. Без него сдать работу нельзя, поэтому
    чужие ответы за ученика отправить не получится.
    """

    attempt_id: int
    attempt_token: str
    variant_no: int
    student_name: str
    student_class: str
    title: str
    shuffle: bool
    tasks: list[PublicTaskOut]


class SubmitAttemptIn(BaseModel):
    """
    Тело сдачи работы.

    choices — {id задания: id выбранного варианта} для формата «выбор»;
    inputs  — {id задания: введённый текст} для формата «ввод».
    Заданий, которых нет ни там, ни там, ученик не решал.
    """

    attempt_token: str
    choices: dict[int, int] = {}
    inputs: dict[int, str] = {}


class TaskResult(BaseModel):
    """Итог по одному заданию. Правильный ответ НЕ раскрывается."""

    task_id: int
    position: int
    answered: bool
    is_correct: bool


class AttemptResultOut(BaseModel):
    """Ответ на сдачу работы."""

    attempt_id: int
    variant_no: int
    student_name: str
    student_class: str
    score: int
    max_score: int
    results: list[TaskResult]


# =====================================================================
# Результаты для учителя (доступ по секретной ссылке /r/<results_token>)
# =====================================================================


class SkillOut(BaseModel):
    """Умение в ответе учителю."""

    id: int
    position: int
    title: str
    tasks_per_variant: int
    answer_format: AnswerFormat


class AttemptRow(BaseModel):
    """Строка таблицы учеников."""

    attempt_id: int
    student_name: str
    student_class: str
    variant_no: int
    score: int
    max_score: int
    percent: int
    finished_at: datetime | None
    # {id умения: процент выполнения} — основа матрицы «ученик × умение».
    skill_percents: dict[int, int]


class SkillStat(BaseModel):
    """Сводка по умению: как справился класс целиком."""

    skill_id: int
    position: int
    title: str
    correct: int
    total: int
    percent: int


class ResultsOverview(BaseModel):
    """Ответ GET /api/results/{results_token}."""

    title: str
    teacher_name: str
    code: str
    classes: list[str]
    variants_count: int
    is_open: bool
    skills: list[SkillOut]
    attempts_count: int
    attempts: list[AttemptRow]
    skill_stats: list[SkillStat]


class AttemptDetailItem(BaseModel):
    """Одно задание в разборе работы — здесь правильный ответ показывать МОЖНО."""

    task_id: int
    position: int
    skill_title: str
    text: str
    answer_format: AnswerFormat
    student_answer: str | None
    correct_answer: str
    solution: str
    answered: bool
    is_correct: bool


class AttemptDetail(BaseModel):
    """Ответ GET /api/results/{results_token}/attempts/{attempt_id}."""

    attempt_id: int
    student_name: str
    student_class: str
    variant_no: int
    score: int
    max_score: int
    percent: int
    finished_at: datetime | None
    items: list[AttemptDetailItem]


class TestSettingsUpdate(BaseModel):
    """Тело PATCH /api/results/{results_token}."""

    is_open: bool


class ResultsOverviewSettings(BaseModel):
    """Ответ на PATCH: подтверждаем новое состояние."""

    is_open: bool
