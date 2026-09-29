"""Общие фикстуры pytest для всех тестов photoprint.

Сбои записи и чтения в тестах создаются по-настоящему, через `chmod` на
настоящий файл или каталог (спецификация § 7.3), а там, где `chmod` до
нужного места не достаёт, — через флаги файловой системы `chflags`. Здесь —
проверка, что это вообще возможно (не root), и две фикстуры, которые меняют
права или флаги и обязательно возвращают их обратно.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable, Iterator, List, Tuple

import pytest

# § 7.3: подпроцессы тестов (`python -c`, клиент) не пишут кэш байткода — у
# Python из Command Line Tools он ложится в настоящий `~/Library/Caches`, а
# тесты не трогают домашний каталог владельца (финальная проверка).
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


@pytest.fixture(scope="session", autouse=True)
def not_root() -> None:
    """Остановить весь прогон, если он запущен от root.

    § 7.3: root читает и пишет всё независимо от прав, поэтому тесты сбоев
    через `chmod` прошли бы «зелёными», ничего не проверив.
    """
    assert os.geteuid() != 0, "проверки прав через chmod не работают от root"


@pytest.fixture
def chmod_to() -> Iterator[Callable[[Path, int], None]]:
    """Дать тесту функцию `chmod_to(path, mode)`, меняющую права пути.

    Исходные права каждого пути запоминаются до смены и возвращаются в
    финализаторе, даже если тест упал: иначе pytest не смог бы удалить
    `tmp_path`, а следующий тест унаследовал бы сломанные права (§ 7.3).
    """
    saved: List[Tuple[Path, int]] = []

    def change(path: Path, mode: int) -> None:
        """Запомнить текущие права `path` и поставить `mode`."""
        saved.append((Path(path), stat.S_IMODE(os.stat(path).st_mode)))
        os.chmod(path, mode)

    yield change
    # Возврат в обратном порядке: если один путь меняли дважды, последним
    # ставится самый первый, то есть исходный, режим.
    for path, mode in reversed(saved):
        try:
            os.chmod(path, mode)
        except FileNotFoundError:
            # Тест сам удалил путь — возвращать права уже некому.
            pass


@pytest.fixture
def chflags_to() -> Iterator[Callable[[Path, int], None]]:
    """Дать тесту функцию `chflags_to(path, flags)`, ставящую флаги BSD пути.

    Флаги — такой же настоящий атрибут файловой системы, как права, а не
    подмена кода (§ 7.3). Нужны там, где `chmod` не достаёт до сбоя после
    `mkstemp` (S5): каталог без записи роняет уже `mkstemp`, а нечитаемый
    файл — чтение. Флаг `UF_IMMUTABLE` (`chflags uchg`, галочка «Защита» в
    Finder) оставляет файл читаемым, но запрещает его заменять; флаг
    `UF_APPEND` на каталоге (`chflags uappnd`) даёт создавать в нём файлы,
    но не убирать и не переименовывать их. Оба флага владелец ставит и
    снимает сам, без root.

    Исходные флаги каждого пути запоминаются до смены и возвращаются в
    финализаторе, даже если тест упал: иначе pytest не смог бы удалить
    `tmp_path` — флаги запрещают удаление.
    """
    saved: List[Tuple[Path, int]] = []

    def change(path: Path, flags: int) -> None:
        """Запомнить текущие флаги `path` и поставить `flags`."""
        saved.append((Path(path), os.stat(path).st_flags))
        os.chflags(path, flags)

    yield change
    # Возврат в обратном порядке: если один путь меняли дважды, последними
    # ставятся самые первые, то есть исходные, флаги.
    for path, flags in reversed(saved):
        try:
            os.chflags(path, flags)
        except FileNotFoundError:
            # Тест сам удалил путь — возвращать флаги уже некому.
            pass
