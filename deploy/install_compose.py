#!/usr/bin/env python3
"""
Вставка сервисов kr-tests в общий docker-compose.yml сервера.

Запуск на сервере из /opt/2090-fun-infra:

    python3 apps/kr-tests/deploy/install_compose.py

Что делает:
  * читает ./docker-compose.yml;
  * если сервисы уже вставлены — ничего не меняет и говорит об этом;
  * проверяет, что разделы volumes: и networks: встречаются ровно по одному разу;
  * вставляет блок из compose-block.yml перед разделом volumes:;
  * добавляет том kr-postgres-data и сеть kr.

Скрипт правит файл по тексту, а не через разбор YAML: разбор потерял бы
комментарии, якоря (*default-logging) и форматирование, которыми живёт
общий файл сервера.

Сторонних библиотек не требует — только стандартная библиотека Python 3.

ВАЖНО: перед запуском сделайте копию файла, как описано в DEPLOY.md:
    cp docker-compose.yml "backups/docker-compose.yml.$(date +%F-%H%M)"
"""

import sys
from pathlib import Path

# Файл, который правим. Скрипт запускают из корня инфраструктуры.
COMPOSE = Path("docker-compose.yml")

# Блок сервисов лежит рядом со скриптом.
BLOCK = Path(__file__).resolve().parent / "compose-block.yml"

# По этой строке понимаем, что вставка уже была.
ALREADY_MARK = "kr-postgres:"

# Разделы, рядом с которыми вставляем. Перевод строки в начале нужен, чтобы
# не поймать строку внутри чужого сервиса (например «    volumes:» с отступом).
VOLUMES_MARKER = "\nvolumes:\n"
NETWORKS_MARKER = "\nnetworks:\n"

# Что добавляем в разделы.
VOLUME_LINE = "  kr-postgres-data:\n"
NETWORK_LINE = "  kr:\n"


def fail(message: str) -> int:
    """Печатает ошибку и возвращает код выхода. Файл при этом не менялся."""
    print(f"Ошибка: {message}")
    print("Файл не изменён.")
    return 1


def main() -> int:
    if not COMPOSE.exists():
        return fail(
            f"не найден {COMPOSE}. Запускайте скрипт из /opt/2090-fun-infra:\n"
            "  cd /opt/2090-fun-infra\n"
            "  python3 apps/kr-tests/deploy/install_compose.py"
        )

    if not BLOCK.exists():
        return fail(f"рядом со скриптом нет файла {BLOCK.name}")

    text = COMPOSE.read_text(encoding="utf-8")

    # --- Уже вставлено? ---------------------------------------------------
    if ALREADY_MARK in text:
        print("Уже установлено: в docker-compose.yml есть kr-postgres.")
        print("Файл не изменён.")
        return 0

    # --- Проверки разделов ------------------------------------------------
    volumes_count = text.count(VOLUMES_MARKER)
    networks_count = text.count(NETWORKS_MARKER)

    if volumes_count != 1:
        return fail(
            f"раздел «volumes:» на верхнем уровне найден {volumes_count} раз(а), "
            "а должен быть ровно один. Вставьте блок вручную."
        )

    if networks_count != 1:
        return fail(
            f"раздел «networks:» на верхнем уровне найден {networks_count} раз(а), "
            "а должен быть ровно один. Вставьте блок вручную."
        )

    block = BLOCK.read_text(encoding="utf-8").rstrip("\n") + "\n"

    # --- Вставка ----------------------------------------------------------
    # partition делит текст на «до раздела volumes», сам разделитель и «после».
    head, _, tail = text.partition(VOLUMES_MARKER)

    updated = (
        head
        # закрываем последнюю строку предыдущего сервиса и оставляем пустую
        # строку — так блок отделён от соседей и файл остаётся читаемым
        + "\n\n"
        + block
        + "\nvolumes:\n"
        + VOLUME_LINE
        + tail
    )

    # Сеть добавляем в самый конец: раздел networks: в этом файле последний.
    if not updated.endswith("\n"):
        updated += "\n"
    updated += NETWORK_LINE

    # newline="\n" — переводы строк всегда LF, даже если скрипт запустят
    # на Windows: иначе docker compose получит файл со смешанными концами строк.
    COMPOSE.write_text(updated, encoding="utf-8", newline="\n")

    print("Вставлено: сервисы kr-postgres, kr-backend, kr-frontend,")
    print("           том kr-postgres-data, сеть kr.")
    print("Проверьте результат:  docker compose config > /dev/null && echo ок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
