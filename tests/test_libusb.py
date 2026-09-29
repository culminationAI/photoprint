"""Тесты загрузки libusb — `photoprint.printer.load_backend` (P2, § 7.2 «libusb»).

pyusb запоминает первую загруженную libusb на весь процесс и дальше отдаёт
её при любом `find_library`. В процессе pytest поставляемая библиотека уже
загружена другими тестами, поэтому каждый тест здесь — отдельный процесс
`python -c` с `cwd=APP`: в свежем процессе видно, какую библиотеку
`load_backend` загрузит на самом деле.

Грузить можно только поставляемую libusb: у заказчика Homebrew нет, но
чужая libusb на его Mac найтись может, а на машине разработчика она то
есть (Homebrew), то нет. Поэтому проверяется путь загруженной библиотеки,
а не только то, что бэкенд не `None`. Чужую libusb на любой машине даёт
приманка: копия поставляемой в `$HOME/lib` под системным именем
`libusb-1.0.dylib` — там её находит `ctypes.util.find_library` (его
список поиска по умолчанию начинается с `~/lib`), а через него — pyusb без
подсказки (§ 7.3: условие
окружения заказчика, не подмена; `HOME` — каталог теста).

Каждый процесс-проба получает `TMPDIR` внутри `tmp_path` (§ 7.3: условие
окружения, не подмена): escpos 3.1 при импорте делает `mkdtemp()` в
`$TMPDIR`, и без этого каждая проба оставляла бы каталог в системной
временной папке.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from helpers import APP, BUNDLED

# Проба для свежего процесса: `load_backend` по очереди для каждого пути из
# `argv[1:]`, в одном процессе; путь превращается в абсолютный относительно
# `cwd`. На каждый путь — строка: путь загруженной библиотеки (`lib._name` —
# имя, под которым ctypes открыл файл) или `None`. Если вместо `None`
# загрузится чужая libusb, в выводе будет её путь — тест сразу покажет,
# откуда она. Несколько путей подряд показывают, что даёт `load_backend`,
# когда pyusb уже держит первую загруженную библиотеку (P2).
_PROBE = """
import sys
from pathlib import Path
from photoprint.printer import load_backend

for arg in sys.argv[1:]:
    backend = load_backend(Path(arg).resolve())
    print(None if backend is None else backend.lib._name)
"""

# Проба «что найдёт поиск по умолчанию» для свежего процесса: первая
# строка — что находит `ctypes.util.find_library` (так pyusb ищет libusb без
# подсказки), вторая — путь libusb, которую загрузил бы pyusb сам, без
# `find_library` (или `None`). Именно этот поиск сделал бы любой запасной
# путь «наш файл не загрузился — взять какую-нибудь libusb».
_DEFAULT_SEARCH = """
import ctypes.util
import usb.backend.libusb1

print(ctypes.util.find_library("usb-1.0"))
backend = usb.backend.libusb1.get_backend()
print(None if backend is None else backend.lib._name)
"""


def _run(code: str, args: List[str], tmp_path: Path, home: Optional[Path]) -> str:
    """Выполнить `code` в свежем процессе из `app/` и вернуть его вывод без `\\n` в конце.

    P2: свежий процесс — потому что pyusb запоминает первую загруженную
    libusb на весь процесс; в процессе pytest поставляемая уже загружена, и
    какую библиотеку загрузит `load_backend`, там не видно.
    `args` — аргументы после `-c code`. `home` — `HOME` процесса (§ 7.3),
    `None` — как у pytest. `TMPDIR` — `tmp_path / "tmp"`: туда escpos при
    импорте кладёт свой временный каталог. Кроме него процесс только читает
    файлы и ничего не пишет.
    """
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    # § 7.3, решение по задаче 1.3: escpos 3.1 при импорте делает
    # `mkdtemp()` в `$TMPDIR`; свой `TMPDIR` держит этот мусор в `tmp_path`.
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmpdir)
    done = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=APP, capture_output=True, encoding="utf-8", env=env,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _probe(libusb: str, tmp_path: Path, home: Optional[Path] = None) -> str:
    """Запустить `_PROBE` для одного пути в свежем процессе из `app/` и вернуть его вывод без `\\n`.

    `libusb` — путь к файлу libusb, относительный путь отсчитывается от
    `app/`. `tmp_path` — каталог теста для `TMPDIR` процесса. `home` —
    `HOME` процесса (§ 7.3), `None` — как у pytest. Процесс только читает
    файл; пишет он лишь временный каталог escpos в `TMPDIR`.
    """
    return _run(_PROBE, [libusb], tmp_path, home)


def _home_with_foreign_libusb(tmp_path: Path) -> Path:
    """Создать `HOME` теста с чужой libusb в `~/lib/libusb-1.0.dylib` и вернуть его путь.

    Чужая libusb — побайтная копия поставляемой: `shutil.copyfile` копирует
    только содержимое, без расширенных атрибутов файла, так что метки
    карантина на ней нет и быть не может. Так выглядит Mac, где libusb
    ставил кто-то другой (P2: грузить её нельзя — клиент с ней не проверен).
    """
    home = tmp_path / "home"
    (home / "lib").mkdir(parents=True)
    shutil.copyfile(BUNDLED, home / "lib" / "libusb-1.0.dylib")
    return home


def test_bundled_libusb_is_loaded(tmp_path: Path) -> None:
    """P2: `load_backend` грузит именно поставляемую `app/libusb-1.0.0.dylib`.

    Путь задан относительно `app/`, как его видит код рядом с библиотекой.
    Загруженный файл обязан быть этим, а не libusb из Homebrew.
    """
    assert _probe("libusb-1.0.0.dylib", tmp_path) == str(BUNDLED.resolve())


def test_garbage_libusb_is_none(tmp_path: Path) -> None:
    """P2: файл libusb с мусором вместо библиотеки даёт `None`, а не чужую libusb.

    Так выглядит повреждённая при установке библиотека: файл есть, но
    ctypes его не открывает. Подменять его системной libusb нельзя.
    """
    garbage = tmp_path / "libusb-1.0.0.dylib"
    garbage.write_bytes(b"garbage")

    assert _probe(str(garbage), tmp_path) == "None"


def test_missing_libusb_is_none(tmp_path: Path) -> None:
    """P2: несуществующий файл libusb даёт `None` — pyusb не вызывается."""
    assert _probe(str(tmp_path / "missing.dylib"), tmp_path) == "None"


def test_only_bundled_libusb_beside_foreign_libusb(tmp_path: Path) -> None:
    """P2: рядом с чужой libusb грузится только поставляемая; битая — `None`.

    `test_garbage_libusb_is_none` ловит запасной путь «наш файл не
    загрузился — взять системную libusb» только там, где системная есть: на
    Mac без неё такой запасной путь тоже даёт `None`, и тест проходит
    впустую. Здесь чужая libusb есть на любой машине — приманка в `~/lib`
    (`_home_with_foreign_libusb`).

    Сначала предпосылка: свежий процесс с этим `HOME` находит приманку и
    через `ctypes.util.find_library`, и поиском самого pyusb — иначе тест
    ничего бы не доказывал. Затем:
    - поставляемая libusb грузится сама, приманка её не заслоняет:
      порядок «сначала системная, потом наша» загрузил бы приманку;
    - мусор на месте поставляемой (повреждённая установка) даёт `None`, а
      не приманку. С `None` оператор видит «Не загрузилась библиотека USB.
      Установите клиент заново…» — верное действие. С чужой libusb клиент
      работал бы на непроверенной библиотеке, а её сбой показал бы
      «Принтер не ответил… выключите и включите принтер» — не то действие.
    """
    home = _home_with_foreign_libusb(tmp_path)
    decoy = str(home / "lib" / "libusb-1.0.dylib")
    garbage = tmp_path / "libusb-1.0.0.dylib"
    garbage.write_bytes(b"garbage")

    assert _run(_DEFAULT_SEARCH, [], tmp_path, home).splitlines() == [decoy, decoy]

    assert _probe("libusb-1.0.0.dylib", tmp_path, home=home) == str(BUNDLED.resolve())
    assert _probe(str(garbage), tmp_path, home=home) == "None"


def test_directory_is_none_even_after_bundled_loaded(tmp_path: Path) -> None:
    """P2: каталог на месте libusb даёт `None`, даже когда pyusb уже держит поставляемую.

    P2 велит проверять `is_file()`, а не `exists()`: каталог с именем
    `libusb-1.0.0.dylib` существует, но библиотекой не является. С
    `exists()` он прошёл бы проверку, и pyusb отдал бы библиотеку, которую
    запомнил раньше: `load_backend` ответил бы «библиотека есть», хотя
    файла на месте нет. Поэтому проба в одном свежем процессе сначала
    грузит поставляемую (так pyusb её запоминает), а затем каталог. Без
    первой загрузки разницы не было бы видно: каталог ctypes не откроет, и
    `exists()` тоже дал бы `None`.
    """
    directory = tmp_path / "libusb-1.0.0.dylib"
    directory.mkdir()

    output = _run(_PROBE, ["libusb-1.0.0.dylib", str(directory)], tmp_path, None)
    lines = output.splitlines()

    assert lines == [str(BUNDLED.resolve()), "None"]
