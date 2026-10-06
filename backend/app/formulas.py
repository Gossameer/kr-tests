"""
Формулы в текстах заданий.

Формулы пишутся как в LaTeX, между знаками доллара: «Вычислите $2\\frac{1}{3} + 1{,}5$».
В браузере их рисует KaTeX. Серверу формулы нужны для трёх вещей:

  * заметить битую формулу сразу после генерации (незакрытый $, непарные
    скобки, неизвестная команда) — такое задание уходит учителю на проверку;
  * сравнивать ответы: «\\frac{3}{5}» и «3/5» — один и тот же ответ;
  * собрать файл Word с настоящими формулами (см. app/docx_export.py).

Поэтому здесь свой небольшой разбор школьного подмножества LaTeX: дроби, корни,
степени и индексы, скобки \\left…\\right, системы (cases), греческие буквы и
знаки. Всё, что разбор принимает, принимает и KaTeX (список команд сверен с ним
тестом frontend/scripts/check-formulas.mjs). Обратное неверно: редкую команду
KaTeX нарисует, а здесь она считается ошибкой — учитель просто посмотрит задание.

Дерево формулы — вложенные кортежи:
    ("row", [узлы])                 — последовательность
    ("text", "abc", upright)        — буквы, цифры, знаки; upright — прямым шрифтом
    ("frac", числитель, знаменатель)
    ("sqrt", показатель | None, подкоренное)
    ("script", основание, нижний | None, верхний | None)
    ("delim", "(", ")", содержимое) — скобки по высоте содержимого
    ("accent", "bar" | "vec", содержимое)
    ("cases", [строки])             — система: фигурная скобка и строки
"""

import re

# Команда → знак. Все эти команды понимает KaTeX.
SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "leqslant": "⩽", "ge": "≥", "geq": "≥", "geqslant": "⩾",
    "ne": "≠", "neq": "≠", "approx": "≈", "sim": "∼", "equiv": "≡",
    "lt": "<", "gt": ">",
    "infty": "∞", "circ": "∘", "degree": "°", "angle": "∠", "triangle": "△",
    "parallel": "∥", "perp": "⊥", "ldots": "…", "dots": "…", "cdots": "⋯",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftrightarrow": "⇔", "leftrightarrow": "↔",
    "in": "∈", "notin": "∉", "cup": "∪", "cap": "∩", "subset": "⊂", "subseteq": "⊆",
    "varnothing": "∅", "emptyset": "∅", "forall": "∀", "exists": "∃",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ",
    "varepsilon": "ε", "lambda": "λ", "mu": "μ", "nu": "ν", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "ϕ", "varphi": "φ", "omega": "ω", "theta": "θ",
    "eta": "η", "xi": "ξ", "psi": "ψ", "chi": "χ", "kappa": "κ", "zeta": "ζ",
    "Delta": "Δ", "Omega": "Ω", "Sigma": "Σ", "Pi": "Π", "Phi": "Φ", "Gamma": "Γ",
    "Lambda": "Λ", "Theta": "Θ",
    "quad": " ", "qquad": "  ",
    "lbrace": "{", "rbrace": "}", "vert": "|", "mid": "|", "prime": "′",
    "sum": "∑", "int": "∫", "partial": "∂", "nabla": "∇", "bullet": "•",
    "backslash": "\\",
}
# Функции — прямым шрифтом. tg, ctg и arctg KaTeX тоже знает.
FUNCTIONS = (
    "sin", "cos", "tan", "tg", "cot", "ctg", "arcsin", "arccos", "arctan", "arctg",
    "log", "ln", "lg", "lim", "min", "max", "exp", "sh", "ch", "th",
)
# «\,», «\;» и т. п. — пробелы разной ширины; «\%», «\{» — сам знак.
ESCAPED = {
    ",": " ", ";": " ", ":": " ", "!": "", " ": " ", ">": " ",
    "%": "%", "{": "{", "}": "}", "$": "$", "#": "#", "&": "&", "_": "_", "|": "‖",
}
FRACTIONS = ("frac", "dfrac", "tfrac")
# Команды с одним аргументом, которые меняют только шрифт.
UPRIGHT = ("text", "mathrm", "textrm", "operatorname", "mathbf", "textbf", "mathit", "textit")
ACCENTS = {"overline": "bar", "bar": "bar", "vec": "vec", "overrightarrow": "vec"}
# Команды без видимого результата: размер формулы и т. п.
IGNORED = ("displaystyle", "textstyle", "limits", "nolimits", "big", "Big", "bigg", "Bigg")

DELIMITERS = {
    "(": "(", ")": ")", "[": "[", "]": "]", "|": "|", ".": "",
    "\\{": "{", "\\}": "}", "\\lbrace": "{", "\\rbrace": "}",
    "\\|": "‖", "\\vert": "|", "\\langle": "⟨", "\\rangle": "⟩",
}

TOKEN_RE = re.compile(r"\\[a-zA-Z]+|\\.|\s+|.", re.DOTALL)
COMMAND_RE = re.compile(r"\\[a-zA-Z]{2,}")


class FormulaError(Exception):
    """Формулу не разобрать. Текст — короткая причина для учителя."""


# =====================================================================
# Формулы внутри текста
# =====================================================================


def split_formulas(text: str) -> list[tuple[str, str]]:
    """
    Делит текст на куски: ("text", …), ("inline", формула), ("display", формула).

    «\\$» — обычный знак доллара. Если знак $ не закрыт, остаток — обычный текст
    (так же поступает фронтенд: битая формула показывается как есть).
    """
    parts: list[tuple[str, str]] = []
    plain: list[str] = []
    position = 0
    length = len(text)

    while position < length:
        char = text[position]
        if char == "\\" and position + 1 < length and text[position + 1] == "$":
            plain.append("$")
            position += 2
            continue
        if char != "$":
            plain.append(char)
            position += 1
            continue

        mark = "$$" if text.startswith("$$", position) else "$"
        end = _find_closing(text, position + len(mark), mark)
        if end == -1:
            plain.append(text[position:])
            break
        if plain:
            parts.append(("text", "".join(plain)))
            plain = []
        parts.append(("display" if mark == "$$" else "inline", text[position + len(mark) : end]))
        position = end + len(mark)

    if plain:
        parts.append(("text", "".join(plain)))
    return parts


def _find_closing(text: str, start: int, mark: str) -> int:
    position = start
    while position < len(text):
        if text[position] == "\\":
            position += 2
            continue
        if text.startswith(mark, position):
            return position
        position += 1
    return -1


def has_unclosed_dollar(text: str) -> bool:
    return any(kind == "text" and "$" in chunk.replace("\\$", "") for kind, chunk in _raw(text))


def _raw(text: str) -> list[tuple[str, str]]:
    """Как split_formulas, но «\\$» в обычном тексте остаётся как было."""
    marker = "\x00"
    return [
        (kind, chunk.replace(marker, "\\$"))
        for kind, chunk in split_formulas(text.replace("\\$", marker))
    ]


def formula_problem(text: str) -> str:
    """
    Что не так с формулами в тексте — коротко, или пустая строка.

    Ловит то, из-за чего ученик увидит сырую разметку вместо формулы.
    """
    if "$" not in text and "\\" not in text:
        return ""
    if has_unclosed_dollar(text):
        return "незакрытый знак $"

    for kind, chunk in _raw(text):
        if kind == "text":
            command = COMMAND_RE.search(chunk)
            if command:
                return f"команда {command.group()} вне знаков $"
            continue
        if not chunk.strip():
            return "пустая формула"
        try:
            parse(chunk)
        except FormulaError as error:
            return str(error)
    return ""


# =====================================================================
# Разбор одной формулы
# =====================================================================


class _Parser:
    def __init__(self, source: str) -> None:
        self.tokens = TOKEN_RE.findall(source)
        self.position = 0

    def peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> str:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def skip_spaces(self) -> None:
        while (token := self.peek()) is not None and token.isspace():
            self.position += 1

    def row(self, stops: tuple[str, ...] = ()) -> tuple:
        """Последовательность до одного из стоп-знаков (сам знак не забирает)."""
        nodes: list[tuple] = []
        while (token := self.peek()) is not None and token not in stops:
            if token.isspace():
                self.position += 1
                continue
            if token == "}":
                raise FormulaError("лишняя скобка }")
            if token in ("^", "_"):
                base = nodes.pop() if nodes else ("row", [])
                nodes.append(self.scripts(base))
                continue
            if token == "'":
                self.position += 1
                nodes.append(("text", "′", True))
                continue
            node = self.atom()
            if node is not None:
                nodes.append(node)
        return ("row", nodes)

    def scripts(self, base: tuple) -> tuple:
        sub = sup = None
        while (token := self.peek()) in ("^", "_"):
            self.position += 1
            argument = self.argument("после знака " + token)
            if token == "^":
                if sup is not None:
                    raise FormulaError("две степени подряд")
                sup = argument
            else:
                if sub is not None:
                    raise FormulaError("два индекса подряд")
                sub = argument
            self.skip_spaces()
        return ("script", base, sub, sup)

    def argument(self, where: str) -> tuple:
        """Аргумент команды: {группа} или один знак."""
        self.skip_spaces()
        token = self.peek()
        if token is None or token in ("}", "^", "_", "&"):
            raise FormulaError(f"нет аргумента {where}")
        if token == "{":
            return self.group()
        node = self.atom()
        return node if node is not None else ("row", [])

    def group(self) -> tuple:
        self.take()  # {
        body = self.row(("}",))
        if self.peek() != "}":
            raise FormulaError("не закрыта скобка {")
        self.take()
        return body

    def atom(self) -> tuple | None:
        token = self.take()
        if token == "{":
            self.position -= 1
            return self.group()
        if token == "&":
            raise FormulaError("знак & вне системы")
        if not token.startswith("\\"):
            return ("text", token, not token.isalpha())
        if len(token) == 2 and not token[1].isalpha():
            if token[1] == "\\":
                raise FormulaError("перенос строки \\\\ вне системы")
            if token[1] not in ESCAPED:
                raise FormulaError(f"неизвестная команда {token}")
            value = ESCAPED[token[1]]
            return ("text", value, True) if value else None

        name = token[1:]
        if name in SYMBOLS:
            return ("text", SYMBOLS[name], True)
        if name in FUNCTIONS:
            return ("text", name, True)
        if name in FRACTIONS:
            numerator = self.argument(f"у {token}")
            return ("frac", numerator, self.argument(f"у {token}"))
        if name == "sqrt":
            index = None
            self.skip_spaces()
            if self.peek() == "[":
                self.take()
                index = self.row(("]",))
                if self.peek() != "]":
                    raise FormulaError("не закрыта скобка [ у корня")
                self.take()
            return ("sqrt", index, self.argument("у корня"))
        if name in UPRIGHT:
            return ("text", self.raw_group(token), True)
        if name in ACCENTS:
            return ("accent", ACCENTS[name], self.argument(f"у {token}"))
        if name in IGNORED:
            return None
        if name == "left":
            return self.delimited()
        if name == "right":
            raise FormulaError("\\right без \\left")
        if name == "begin":
            return self.environment()
        if name == "end":
            raise FormulaError("\\end без \\begin")
        raise FormulaError(f"неизвестная команда {token}")

    def raw_group(self, command: str) -> str:
        """Текст в \\text{…} — берём как есть, без разбора."""
        self.skip_spaces()
        if self.peek() != "{":
            raise FormulaError(f"нет аргумента у {command}")
        self.take()
        depth = 1
        parts: list[str] = []
        while self.peek() is not None:
            token = self.take()
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth == 0:
                    return "".join(parts)
            parts.append(ESCAPED.get(token[1], token) if len(token) == 2 and token[0] == "\\" else token)
        raise FormulaError("не закрыта скобка {")

    def delimiter(self, command: str) -> str:
        self.skip_spaces()
        token = self.peek()
        if token is None or token not in DELIMITERS:
            raise FormulaError(f"после {command} нужна скобка")
        self.take()
        return DELIMITERS[token]

    def delimited(self) -> tuple:
        opening = self.delimiter("\\left")
        body = self.row(("\\right",))
        if self.peek() != "\\right":
            raise FormulaError("\\left без \\right")
        self.take()
        return ("delim", opening, self.delimiter("\\right"), body)

    def environment(self) -> tuple:
        name = self.raw_group("\\begin")
        if name != "cases":
            raise FormulaError(f"неизвестное окружение {name}")
        rows: list[tuple] = []
        while True:
            cells = [self.row(("&", "\\\\", "\\end"))]
            while self.peek() == "&":
                self.take()
                cells.append(self.row(("&", "\\\\", "\\end")))
            # «x, & если x > 0» — столбцы системы склеиваем через пробел.
            nodes: list[tuple] = []
            for number, cell in enumerate(cells):
                if number:
                    nodes.append(("text", " ", True))
                nodes.extend(cell[1])
            if nodes:
                rows.append(("row", nodes))
            token = self.peek()
            if token is None:
                raise FormulaError("\\begin{cases} без \\end{cases}")
            self.take()
            if token == "\\end":
                if self.raw_group("\\end") != name:
                    raise FormulaError("\\end не от того окружения")
                break
        if not rows:
            raise FormulaError("пустая система")
        return ("cases", rows)


def parse(source: str) -> tuple:
    """Формула (без знаков $) → дерево. Не разобралась — FormulaError."""
    parser = _Parser(source)
    tree = parser.row()
    if parser.peek() is not None:
        raise FormulaError("лишний знак " + parser.peek())
    if not tree[1]:
        raise FormulaError("пустая формула")
    return tree


# =====================================================================
# Формула → строка, как её наберёт человек: 3/5, 2 1/3, 2^3, √(x+1)
# =====================================================================

_SIMPLE_RE = re.compile(r"[\w,.°%′]*", re.UNICODE)


def _wrap(text: str) -> str:
    """Скобки вокруг составного выражения: (a+b)/2, но 3/5."""
    return text if _SIMPLE_RE.fullmatch(text) else f"({text})"


def to_plain(node: tuple) -> str:
    kind = node[0]
    if kind == "row":
        parts: list[str] = []
        for child in node[1]:
            text = to_plain(child)
            # Смешанное число: «2\frac{1}{3}» → «2 1/3», а не «21/3».
            if child[0] == "frac" and parts and parts[-1][-1:].isdigit():
                parts.append(" ")
            parts.append(text)
        return "".join(parts)
    if kind == "text":
        return node[1]
    if kind == "frac":
        return f"{_wrap(to_plain(node[1]))}/{_wrap(to_plain(node[2]))}"
    if kind == "sqrt":
        body = _wrap(to_plain(node[2]))
        index = to_plain(node[1]) if node[1] is not None else ""
        return {"": "√", "2": "√", "3": "∛", "4": "∜"}.get(index, f"√[{index}]") + body
    if kind == "script":
        text = to_plain(node[1])
        if node[2] is not None:
            text += "_" + _wrap(to_plain(node[2]))
        if node[3] is not None:
            power = to_plain(node[3])
            # «^\circ» — это градус, а не степень.
            text += "°" if power == "∘" else "^" + _wrap(power)
        return text
    if kind == "delim":
        return node[1] + to_plain(node[3]) + node[2]
    if kind == "accent":
        return to_plain(node[2])
    if kind == "cases":
        return "; ".join(to_plain(row) for row in node[1])
    return ""


def plain_text(text: str) -> str:
    """
    Текст с формулами → обычная строка: «$\\frac{3}{5}$» → «3/5».

    Нужна там, где формулу не нарисовать: сравнение ответов, промт самопроверки.
    Формулу, которую не разобрать, оставляет как есть, только без знаков $.
    """
    if "$" not in text and "\\" not in text:
        return text
    parts: list[str] = []
    for kind, chunk in _raw(text):
        if kind == "text":
            # Ученик мог набрать «\frac{1}{2}» и без долларов.
            parts.append(
                _try_plain(chunk) if COMMAND_RE.search(chunk) else chunk.replace("\\$", "$")
            )
        else:
            parts.append(_try_plain(chunk))
    return "".join(parts)


def _try_plain(source: str) -> str:
    try:
        return to_plain(parse(source))
    except FormulaError:
        return source
