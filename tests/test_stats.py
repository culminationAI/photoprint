"""Тесты модуля «Счётчики» — `photoprint.stats` (спецификация § 4.2).

Закрепляют правила пули 1: S1 (нет файла, записи или поля — ноль, и в
`get`, и в `increment`; чужое поле — `ValueError`), S2 (файл читается заново
при каждой операции и в UTF-8), S3 (атомарная запись под одним замком,
временный файл скрытый и в той же папке, права 0644, без остатков временных
файлов, файл «только для чтения» заменяется) и S5 (сбой записи —
`StatsWriteError` с причиной, прежний файл цел, временный удалён, сбой
чтения в `get` — ноль).

Правила пули 2 (задача 2.2): S4 (битый файл — не UTF-8, не JSON,
вложенность глубже предела рекурсии или чужая структура, в том числе ключ,
не кодируемый в UTF-8, и ключ, повторённый в одном объекте: `get` читает
его как ноль и не трогает, `increment` откладывает его в `BROKEN_FILE` и
считает с пустого) и S6 (ключ — имя ровно как в `os.listdir`, без
нормализации Unicode ни в одну сторону, с регистром и пробелами как есть, и
в `get`, и в `increment`; переименованная картинка — с нуля).

Все сбои настоящие, подмен нет: права меняет `chmod_to` на настоящем файле
или каталоге (§ 7.3), а флаги файловой системы — `chflags_to`, там, где
`chmod` не даёт сбоя после `mkstemp`. Сбой самой записи — предел размера
файла `RLIMIT_FSIZE` в отдельном процессе (§ 7.3). Так же настоящие перенос
папки (`rename`), каталог на месте файла счётчиков или отложенной копии,
символическая ссылка сама на себя на месте файла счётчиков (ELOOP), битые
байты в файле, время изменения файла (`os.utime`) и счёт открытых
дескрипторов процесса по `/dev/fd`.

Задача 2.7a добавила тесты на выживших мутантов ворот 2: значения и записи,
на которых правдоподобная ошибка проверки S4 бросала бы или принимала битый
файл, пустой файл, порядок имён § 4.2, пустая запись другой картинки (S6),
любой `OSError` чтения в `get` (S5), отказ в переименовании битого файла
при уже отложенной копии и байт не в UTF-8 после обратной черты (S4).

Задача 3.4a (финальная проверка, 2026-09-28) добавила `snapshot` — счёт всех
картинок из одного чтения файла для списка W1. Его тесты проходят те же
случаи S1–S6, что и тесты `get`: нет файла, недостающее поле и пустая
запись, свежее чтение после правки того же размера с прежним временем,
каждый битый файл, настоящие сбои чтения, ключи как есть и записи
удалённых картинок; для любого имени снимок даёт то же, что `get`.
"""
from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Callable, Dict, List

import pytest

from helpers import APP, make_jpeg
from photoprint.folder import scan
from photoprint.stats import BROKEN_FILE, STATS_FILE, Counts, Stats, StatsWriteError


def test_get_without_file_is_zero(tmp_path: Path) -> None:
    """S1: без файла счётчиков `get` даёт `Counts(0, 0)` и файл не создаёт.

    Файл появляется только от первого `increment`: папка, в которой ещё
    ничего не печатали, остаётся нетронутой.
    """
    assert Stats(tmp_path).get("a.jpg") == Counts(0, 0)
    assert not (tmp_path / STATS_FILE).exists()


def test_increment_creates_exact_file(tmp_path: Path) -> None:
    """S1, формат § 4.2: первый `increment` создаёт файл ровно такого вида.

    Отступ 2, ключи по алфавиту (`failed` раньше `printed`), кириллица как
    есть, запись с обоими полями, перевод строки в конце. Сверяются байты:
    так проверяется сразу и текст, и кодировка UTF-8 (`\\u043c…` вместо букв
    дал бы другие байты).
    """
    expected = '{\n  "меню.jpg": {\n    "failed": 0,\n    "printed": 1\n  }\n}\n'

    assert Stats(tmp_path).increment("меню.jpg", "printed") == Counts(1, 0)
    assert (tmp_path / STATS_FILE).read_bytes() == expected.encode("utf-8")


def test_increment_counts_each_field(tmp_path: Path) -> None:
    """S1, S3: `increment` прибавляет 1 только к своему полю и отдаёт новые значения.

    Два сбоя подряд дают `failed == 2`, `printed` не меняется; следующая
    печать прибавляет к `printed`, не трогая накопленные сбои.
    """
    stats = Stats(tmp_path)

    assert stats.increment("a.jpg", "failed") == Counts(0, 1)
    assert stats.increment("a.jpg", "failed") == Counts(0, 2)
    assert stats.increment("a.jpg", "printed") == Counts(1, 2)
    assert stats.get("a.jpg") == Counts(1, 2)


def test_increment_rejects_unknown_field(
    tmp_path: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """S1: поле не из `FIELDS` — `ValueError("unknown field: …")` до работы с файлом.

    Без файла он так и не появляется. С файлом, который нельзя прочитать,
    ошибка всё равно `ValueError`, а не `StatsWriteError`: значит, поле
    проверено раньше, чем модуль коснулся файла.
    """
    stats = Stats(tmp_path)

    with pytest.raises(ValueError) as caught:
        stats.increment("a.jpg", "other")
    assert str(caught.value) == "unknown field: other"
    assert not (tmp_path / STATS_FILE).exists()

    stats.increment("a.jpg", "printed")
    path = tmp_path / STATS_FILE
    before = path.read_bytes()
    # Нечитаемый файл: любое обращение к нему дало бы `PermissionError`,
    # и вместо `ValueError` вылетел бы `StatsWriteError` (S5).
    chmod_to(path, 0o000)
    with pytest.raises(ValueError) as caught:
        stats.increment("a.jpg", "other")
    assert str(caught.value) == "unknown field: other"
    # Права возвращаются, чтобы сверить байты: файл не тронут.
    chmod_to(path, 0o600)
    assert path.read_bytes() == before


def test_missing_field_reads_as_zero(tmp_path: Path) -> None:
    """S1: недостающее поле записи и недостающая запись читаются как 0.

    Файл правили руками и оставили только `printed` — `failed` считается
    нулём, а не ошибкой. Имя, которого в файле нет, — `Counts(0, 0)`.
    """
    (tmp_path / STATS_FILE).write_text(
        json.dumps({"a.jpg": {"printed": 2}}), encoding="utf-8"
    )
    stats = Stats(tmp_path)

    assert stats.get("a.jpg") == Counts(2, 0)
    assert stats.get("b.jpg") == Counts(0, 0)


def test_increment_fills_missing_fields(tmp_path: Path) -> None:
    """S1, § 4.2: `increment` считает недостающее поле нулём и пишет каждую запись с обоими полями.

    Файл правили руками: у `a.jpg` остался только `printed`, у `b.jpg` —
    только `failed`. Сбой печати `a.jpg` даёт `Counts(2, 1)`, а не
    `KeyError` после уже состоявшейся печати. В записанном файле обе записи
    — с обоими полями, включая чужую `b.jpg`, которую `increment` не
    менял: файл снова в формате § 4.2. У `b.jpg` сбоев 3, а не 1, чтобы её
    значение не совпало с новым значением `a.jpg`.
    """
    (tmp_path / STATS_FILE).write_text(
        json.dumps({"a.jpg": {"printed": 2}, "b.jpg": {"failed": 3}}),
        encoding="utf-8",
    )
    expected = (
        '{\n  "a.jpg": {\n    "failed": 1,\n    "printed": 2\n  },\n'
        '  "b.jpg": {\n    "failed": 3,\n    "printed": 0\n  }\n}\n'
    )

    assert Stats(tmp_path).increment("a.jpg", "failed") == Counts(2, 1)
    assert (tmp_path / STATS_FILE).read_text(encoding="utf-8") == expected


def test_increment_writes_names_in_alphabetical_order(tmp_path: Path) -> None:
    """§ 4.2: имена картинок в файле — по алфавиту, а не в порядке печати.

    Печатают `b.jpg`, потом `c.jpg`, потом сбой у `a.jpg`; в файле всё
    равно `a.jpg`, `b.jpg`, `c.jpg`. Порядок печати выбран так, что ни он,
    ни обратный ему не совпадают с алфавитом. Остальные побайтовые сверки
    файла получают имена уже по алфавиту (одно имя или `a.jpg` раньше
    `b.jpg` и в исходном файле), и запись без `sort_keys`, где поля каждой
    записи отсортированы, а имена идут, как вставлены, их проходила бы.
    Тогда файл ушёл бы из формата § 4.2: оператор, открыв его, чтобы
    поправить счёт руками, искал бы картинку по алфавиту и не находил бы её
    на месте. Сверяются байты.
    """
    stats = Stats(tmp_path)
    stats.increment("b.jpg", "printed")
    stats.increment("c.jpg", "printed")
    stats.increment("a.jpg", "failed")
    expected = (
        '{\n  "a.jpg": {\n    "failed": 1,\n    "printed": 0\n  },\n'
        '  "b.jpg": {\n    "failed": 0,\n    "printed": 1\n  },\n'
        '  "c.jpg": {\n    "failed": 0,\n    "printed": 1\n  }\n}\n'
    )

    assert (tmp_path / STATS_FILE).read_bytes() == expected.encode("utf-8")


def test_reads_file_fresh_each_time(tmp_path: Path) -> None:
    """S2: каждая операция читает файл заново — ничего не кэшируется.

    Правку файла руками при работающем клиенте видит следующий `get`, а
    следующий `increment` прибавляет к исправленному значению, а не к
    запомненному в памяти.

    `get` до правки — первое удачное чтение файла (первый `increment`
    файла ещё не нашёл). Кэш «после первого чтения» запомнил бы здесь
    `Counts(1, 0)` и дальше показывал бы его: оператор исправил бы счёт
    руками, а страница продолжала бы показывать старый, и следующая печать
    затёрла бы правку. Без этого `get` первое удачное чтение пришлось бы
    уже на исправленный файл, и такой кэш прошёл бы тест.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    assert stats.get("a.jpg") == Counts(1, 0)
    (tmp_path / STATS_FILE).write_text(
        json.dumps({"a.jpg": {"printed": 10, "failed": 0}}), encoding="utf-8"
    )

    assert stats.get("a.jpg") == Counts(10, 0)
    assert stats.increment("a.jpg", "printed") == Counts(11, 0)


def test_same_size_edit_with_old_mtime_is_seen(tmp_path: Path) -> None:
    """S2: правка того же размера с прежним временем изменения тоже видна.

    Файл читается заново всегда, а не «когда изменились время и размер».
    Правка руками, после которой время файла вернули прежним (`touch -r`,
    копия с `cp -p`), не меняет ни размера, ни времени; так же выглядят две
    записи одного размера в одну секунду на флешке HFS+ (время с точностью
    до 1 с) или FAT32 (до 2 с). Кэш, сверяющий только время и размер,
    показал бы на странице старый счёт, а следующая печать прибавила бы к
    старому и затёрла бы правку. Время ставит настоящий `os.utime` —
    атрибут файловой системы, как права у `chmod`, а не подмена кода.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    path = tmp_path / STATS_FILE
    # Первое удачное чтение — до правки: кэш, если он есть, наполнится здесь.
    assert stats.get("a.jpg") == Counts(1, 0)
    old = os.stat(path)
    text = path.read_text(encoding="utf-8")
    edited = text.replace('"printed": 1', '"printed": 7')
    assert edited != text
    path.write_text(edited, encoding="utf-8")
    # Прежнее время возвращается с точностью до наносекунды — как после
    # `touch -r` или у второй записи в ту же секунду на HFS+.
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert os.stat(path).st_size == old.st_size
    assert os.stat(path).st_mtime_ns == old.st_mtime_ns

    assert stats.get("a.jpg") == Counts(7, 0)
    assert stats.increment("a.jpg", "printed") == Counts(8, 0)


def test_cyrillic_name_round_trip(tmp_path: Path) -> None:
    """S2: имя с кириллицей, записанное в файл, читается обратно тем же ключом.

    Файл читается в UTF-8 — той же кодировкой, в какой пишется. Чтение в
    другой кодировке (cp1251, latin-1) дало бы вместо «меню.jpg» другой
    ключ: вторая печать начала бы счёт с нуля, и счёт одной картинки
    разошёлся бы по двум записям файла.
    """
    stats = Stats(tmp_path)

    assert stats.increment("меню.jpg", "printed") == Counts(1, 0)
    assert stats.increment("меню.jpg", "printed") == Counts(2, 0)
    assert stats.get("меню.jpg") == Counts(2, 0)
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert list(data) == ["меню.jpg"]


def test_concurrent_increments_are_not_lost(tmp_path: Path) -> None:
    """S3: два потока по 200 `increment` одного имени и поля дают ровно 400.

    Чтение, прибавление и запись идут под одним замком объекта `Stats`;
    без него потоки читали бы одно и то же значение и теряли прибавки.
    Оба потока работают с одним объектом — как ручки сервера.
    """
    stats = Stats(tmp_path)
    # Барьер пускает оба потока разом, чтобы их операции перемежались.
    start = threading.Barrier(2)
    errors: List[Exception] = []

    def worker() -> None:
        """Дождаться второго потока и сделать 200 `increment`."""
        try:
            start.wait(10)
            for _ in range(200):
                stats.increment("a.jpg", "printed")
        except Exception as exc:
            # Исключение в потоке не валит тест само: его надо донести до
            # главного потока и показать в проверке ниже.
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert stats.get("a.jpg") == Counts(400, 0)


def test_written_file_mode_is_0644(tmp_path: Path) -> None:
    """S3: файл счётчиков после записи имеет права 0644.

    `mkstemp` создаёт временный файл с правами 0600; без явного
    `os.chmod(tmp, 0o644)` после `os.replace` файл достался бы только
    владельцу.
    """
    Stats(tmp_path).increment("a.jpg", "printed")

    mode = stat.S_IMODE(os.stat(tmp_path / STATS_FILE).st_mode)
    assert mode == 0o644, oct(mode)


def test_no_temp_file_left_after_writes(tmp_path: Path) -> None:
    """S3: после записей в папке остаётся только файл счётчиков.

    Каждый временный `.print-stats.*.tmp` уходит через `os.replace`; ни
    один не остаётся рядом с картинками.
    """
    stats = Stats(tmp_path)
    for _ in range(5):
        stats.increment("a.jpg", "printed")

    assert sorted(os.listdir(tmp_path)) == [".print-stats.json"]


def test_increments_do_not_leak_descriptors(tmp_path: Path) -> None:
    """S3: `increment` закрывает всё, что открыл, — дескрипторы не копятся.

    `mkstemp` отдаёт уже открытый дескриптор; запись идёт через него
    (`os.fdopen`), и он закрывается вместе с потоком. Открой код временный
    файл второй раз по имени, дескриптор `mkstemp` оставался бы открытым —
    по одному на печать, и без `ResourceWarning`: это голое число. Сервер
    заказчика запускается из Терминала с пределом 256 дескрипторов
    (`launchctl limit maxfiles`): примерно через 250 печатей каждая
    следующая получала бы «Напечатано, но счётчик не сохранился: Too many
    open files», а сервер переставал бы принимать соединения до
    перезапуска. Открытые дескрипторы своего процесса считаются по
    `/dev/fd` — без подмен и без смены предела; `<=`, потому что сборщик
    мусора может закрыть чужой дескриптор, но не открыть новый.
    """
    stats = Stats(tmp_path)
    # Первый `increment` создаёт файл: всё, что открывается один раз на
    # процесс при первой записи, открыто до замера.
    stats.increment("a.jpg", "printed")
    before = set(os.listdir("/dev/fd"))

    for _ in range(50):
        stats.increment("a.jpg", "printed")

    after = set(os.listdir("/dev/fd"))
    assert len(after) <= len(before), sorted(after - before, key=int)
    assert stats.get("a.jpg") == Counts(51, 0)


def test_write_failure_keeps_old_file(
    tmp_path: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """S5: папка без права записи — `StatsWriteError("Permission denied")`.

    `str()` ошибки — ровно причина (strerror исходного `OSError`), её
    покажет страница. Прежний файл не повреждён (байты те же), временных
    файлов в папке не прибавилось.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    path = tmp_path / STATS_FILE
    before = path.read_bytes()
    listing = sorted(os.listdir(tmp_path))
    # § 7.3: сбой записи — настоящий `chmod` каталога; файл читается, а
    # `mkstemp` в папке падает с EACCES.
    chmod_to(tmp_path, 0o555)

    with pytest.raises(StatsWriteError) as caught:
        stats.increment("a.jpg", "printed")
    assert isinstance(caught.value, OSError)
    assert str(caught.value) == "Permission denied"
    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == listing


def test_unreadable_file(
    tmp_path: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """S5: нечитаемый файл счётчиков — `get` даёт ноль, `increment` — ошибку.

    `PermissionError` чтения — не «файла нет»: `get` показывает
    `Counts(0, 0)`, а `increment` не начинает счёт с пустого (это стёрло бы
    накопленное), а бросает `StatsWriteError("Permission denied")`.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    path = tmp_path / STATS_FILE
    chmod_to(path, 0o000)

    assert stats.get("a.jpg") == Counts(0, 0)
    with pytest.raises(StatsWriteError) as caught:
        stats.increment("a.jpg", "printed")
    assert str(caught.value) == "Permission denied"
    assert sorted(os.listdir(tmp_path)) == [".print-stats.json"]


def test_replace_failure_removes_temp(
    tmp_path: Path, chflags_to: Callable[[Path, int], None]
) -> None:
    """S5: сбой после `mkstemp` — `StatsWriteError`, временный файл удалён, прежний цел.

    Файл счётчиков защищён флагом `uchg` (галочка «Защита» в Finder): он
    читается, `mkstemp` в папке, запись, `fsync` и `chmod` временного файла
    проходят, и только `os.replace` получает EPERM. Сбой настоящий и
    случается, когда временный файл уже создан, — он должен исчезнуть, а
    причина дойти до страницы как «Operation not permitted».
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    path = tmp_path / STATS_FILE
    before = path.read_bytes()
    chflags_to(path, stat.UF_IMMUTABLE)

    with pytest.raises(StatsWriteError) as caught:
        stats.increment("a.jpg", "printed")
    assert str(caught.value) == "Operation not permitted"
    assert isinstance(caught.value.__cause__, PermissionError)
    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == [".print-stats.json"]


def test_unremovable_temp_is_hidden_in_folder(
    tmp_path: Path, chflags_to: Callable[[Path, int], None]
) -> None:
    """S3: временный файл создаётся в самой папке и под скрытым именем `.print-stats.*.tmp`.

    Папка получает флаг `uappnd`: создавать в ней файлы можно, а убирать и
    переименовывать нельзя. `mkstemp` проходит, а `os.replace` и удаление
    временного файла получают EPERM — временный файл остаётся, и его видно.
    Он лежит в самой папке, а не в `$TMPDIR`: для папки на флешке или
    сетевом диске `os.replace` из `$TMPDIR` упал бы с EXDEV на каждой
    печати. Имя скрытое, поэтому такой остаток не попадёт в список
    картинок (F1).
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    path = tmp_path / STATS_FILE
    before = path.read_bytes()
    chflags_to(tmp_path, stat.UF_APPEND)

    with pytest.raises(StatsWriteError) as caught:
        stats.increment("a.jpg", "printed")
    assert str(caught.value) == "Operation not permitted"
    assert path.read_bytes() == before
    left = sorted(set(os.listdir(tmp_path)) - {STATS_FILE})
    assert len(left) == 1, left
    assert left[0].startswith(".print-stats."), left
    assert left[0].endswith(".tmp"), left


def test_folder_gone_is_write_error(tmp_path: Path) -> None:
    """S5: папку перенесли при работающем клиенте — `StatsWriteError("No such file or directory")`.

    Сбой не от прав: файла счётчиков по старому пути нет (S1 — счёт с
    пустого), а `mkstemp` в исчезнувшей папке даёт `FileNotFoundError`.
    Это тоже «любой `OSError`» S5, как ENOSPC полной флешки или EIO
    сетевого диска. Наружу уходит `StatsWriteError` с причиной, и страница
    покажет «Напечатано, но счётчик не сохранился: No such file or
    directory» (W4 п. 5): оператор знает, что картинка уже напечатана, и не
    печатает её второй раз. Голый `FileNotFoundError` страница не разобрала
    бы и не сказала бы, что печать была. Папка по старому пути не создаётся
    заново, в перенесённой — прежний счёт.
    """
    folder = tmp_path / "Печать картинок"
    folder.mkdir()
    stats = Stats(folder)
    stats.increment("a.jpg", "printed")
    moved = tmp_path / "moved"
    # Настоящий перенос папки, как в Finder; `Stats` помнит старый путь.
    folder.rename(moved)

    with pytest.raises(StatsWriteError) as caught:
        stats.increment("a.jpg", "printed")
    assert str(caught.value) == "No such file or directory"
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert not folder.exists()
    assert sorted(os.listdir(moved)) == [STATS_FILE]
    assert Stats(moved).get("a.jpg") == Counts(1, 0)


def test_directory_in_place_of_file(tmp_path: Path) -> None:
    """S5: на месте файла счётчиков каталог — `get` даёт ноль, `increment` — `StatsWriteError("Is a directory")`.

    Сбой чтения — не «файла нет» и не «нет прав», а `IsADirectoryError`;
    S5 говорит о любом `OSError`, и так же ведут себя сбойная флешка (EIO)
    или сетевой диск. `get` показывает `Counts(0, 0)`: список картинок (W1)
    читает счёт тем же чтением (`snapshot`), и одна ошибка чтения иначе
    уронила бы весь список — оператор не увидел бы ни одной картинки и ничего не смог
    бы напечатать. `increment` не считает с пустого (это стёрло бы
    накопленное), а отдаёт причину, которую покажет страница (W4 п. 5).
    Каталог не тронут, временных файлов нет.
    """
    (tmp_path / STATS_FILE).mkdir()
    stats = Stats(tmp_path)

    assert stats.get("a.jpg") == Counts(0, 0)
    with pytest.raises(StatsWriteError) as caught:
        stats.increment("a.jpg", "printed")
    assert str(caught.value) == "Is a directory"
    assert isinstance(caught.value.__cause__, IsADirectoryError)
    assert sorted(os.listdir(tmp_path)) == [STATS_FILE]
    assert os.listdir(tmp_path / STATS_FILE) == []


def test_symlink_loop_in_place_of_file(tmp_path: Path) -> None:
    """S5: любой сбой чтения в `get` — ноль, а не только «нет файла», «нет прав» и каталог.

    На месте файла счётчиков — символическая ссылка сама на себя: открытие
    даёт настоящий `ELOOP` («Too many levels of symbolic links»). Это
    простой `OSError` — не `FileNotFoundError`, не `PermissionError` и не
    `IsADirectoryError`, которые закреплены тестами выше. Так же ведут себя
    EIO сбойной флешки и ETIMEDOUT или ESTALE сетевого диска, которых в
    тесте честно не вызвать. `get`, ловящий только три известные ошибки,
    бросил бы, а список картинок (W1) читает счёт тем же чтением
    (`snapshot`): весь список отвечал бы ошибкой, и оператор не увидел бы ни одной картинки, пока
    файл не починят. `increment` не считает с пустого (это стёрло бы
    накопленное), а отдаёт причину (`StatsWriteError`) — её покажет
    страница (W4 п. 5). Ссылка не тронута, новых файлов нет.
    """
    path = tmp_path / STATS_FILE
    # Ссылка по относительному пути на своё же имя: разрешение ходит по
    # кругу, пока ядро не остановит его с ELOOP.
    os.symlink(STATS_FILE, path)
    stats = Stats(tmp_path)

    assert stats.get("a.jpg") == Counts(0, 0)
    with pytest.raises(StatsWriteError) as caught:
        stats.increment("a.jpg", "printed")
    # Предпосылка: сбой — простой `OSError` с ELOOP, а не один из трёх
    # подклассов, которые уже закреплены тестами выше.
    assert type(caught.value.__cause__) is OSError
    assert caught.value.__cause__.errno == errno.ELOOP
    assert str(caught.value) == "Too many levels of symbolic links"
    assert os.readlink(path) == STATS_FILE
    assert sorted(os.listdir(tmp_path)) == [STATS_FILE]


def test_increment_after_write_failure_does_not_hang(
    tmp_path: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """S3, S5: после `StatsWriteError` замок отпущен — следующий `increment` того же `Stats` проходит.

    Замок `increment` взят через `with`, поэтому сбой записи его
    освобождает. Возьми код замок руками (`acquire`/`release`) и не отпусти
    его при исключении, первая же печать с несохранённым счётчиком (W4 п. 5)
    оставила бы замок занятым: следующая печать напечатала бы картинку и
    навсегда повисла бы в `increment`, держа замок печати W5, а все
    дальнейшие получали бы 409 «принтер занят» до перезапуска клиента.
    Второй `increment` идёт в потоке-демоне и ждётся не дольше 10 с:
    зависание — названный провал этого теста, а не зависший прогон.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    mode = stat.S_IMODE(os.stat(tmp_path).st_mode)
    # § 7.3: настоящий сбой записи — каталог без права записи, `mkstemp`
    # получает EACCES.
    chmod_to(tmp_path, 0o555)
    with pytest.raises(StatsWriteError):
        stats.increment("a.jpg", "printed")
    # Права возвращаются: сбой прошёл, следующая запись должна удаться.
    chmod_to(tmp_path, mode)
    results: List[Counts] = []
    errors: List[Exception] = []

    def worker() -> None:
        """Сделать один `increment` и донести результат или исключение до теста."""
        try:
            results.append(stats.increment("a.jpg", "printed"))
        except Exception as exc:
            # Исключение в потоке не валит тест само: его надо донести до
            # главного потока и показать в проверке ниже.
            errors.append(exc)

    # Демон: при неотпущенном замке поток висит вечно, и поток не-демон не
    # дал бы процессу pytest завершиться после провала.
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(10)

    assert not thread.is_alive(), "increment завис: замок не отпущен после StatsWriteError"
    assert errors == []
    assert results == [Counts(2, 0)]
    assert stats.get("a.jpg") == Counts(2, 0)


def test_read_only_file_is_replaced(
    tmp_path: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """S3, § 7.3: файл счётчиков «только для чтения» (`chmod 444`) запись не ломает.

    `increment` не пишет в сам файл, а подменяет его новым через
    `os.replace`: для замены нужно право записи в папку, а не в файл. Поэтому
    файл, закрытый от записи (права 444 или «Только чтение» в окне «Свойства»
    Finder), получает новый счёт `Counts(2, 0)`, а не «Напечатано, но счётчик
    не сохранился: Permission denied». У нового файла обычные права 0644:
    они берутся от временного файла, а не копируются с прежнего.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    path = tmp_path / STATS_FILE
    chmod_to(path, 0o444)

    assert stats.increment("a.jpg", "printed") == Counts(2, 0)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o644, oct(mode)
    assert stats.get("a.jpg") == Counts(2, 0)
    assert sorted(os.listdir(tmp_path)) == [STATS_FILE]


# Один `increment` в свежем процессе с пределом размера файла (S5, § 7.3):
# `argv[1]` — папка, `argv[2]` — предел в байтах. Предел ставится после
# импорта: иначе он коснулся бы и записи байткода. `SIGXFSZ` Python и так
# пропускает при запуске, но здесь это сказано явно — без этого процесс
# убил бы сигнал вместо ошибки `EFBIG`. Итог печатается одной строкой JSON:
# текст исключения и `errno` его причины (или `null`, если сбоя не было).
# Вывод уходит в канал (`capture_output`): на каналы предел размера файла не
# действует, а в обычный файл вывод обрезался бы на тех же 16 байтах.
_INCREMENT_UNDER_FSIZE = """
import json, resource, signal, sys
from pathlib import Path
from photoprint.stats import Stats, StatsWriteError

stats = Stats(Path(sys.argv[1]))
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
hard = resource.getrlimit(resource.RLIMIT_FSIZE)[1]
resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[2]), hard))
try:
    stats.increment("a.jpg", "printed")
except StatsWriteError as exc:
    print(json.dumps({"error": str(exc), "errno": exc.__cause__.errno}))
else:
    print(json.dumps({"error": None, "errno": None}))
"""


def test_write_failure_removes_temp(tmp_path: Path) -> None:
    """S5: сбой самой записи — `StatsWriteError("File too large")`, временный файл удалён, прежний цел.

    Так же выглядит флешка, на которой место кончилось посреди записи:
    `mkstemp` прошёл, временный файл уже создан и частично записан, а
    следующая запись получает ошибку. Сбой настоящий — предел размера файла `RLIMIT_FSIZE`
    в отдельном процессе (§ 7.3): ядро пишет первые 16 байт, а дальше даёт
    `EFBIG`. Временный `.print-stats.*.tmp` не должен остаться рядом с
    картинками, прежний файл счётчиков — байт в байт прежний, а причина
    доходит до страницы как «File too large».
    """
    stats = Stats(tmp_path)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        stats.increment(name, "printed")
    path = tmp_path / STATS_FILE
    before = path.read_bytes()
    # Предпосылка: новый текст длиннее предела, иначе сбоя записи не будет.
    assert len(before) > 16

    done = subprocess.run(
        [sys.executable, "-c", _INCREMENT_UNDER_FSIZE, str(tmp_path), "16"],
        cwd=APP, capture_output=True, encoding="utf-8", timeout=60,
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"error": "File too large", "errno": errno.EFBIG}
    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == [STATS_FILE]


# S4: содержимое битого файла счётчиков. Первые восемь — ветки исходного
# правила S4, по одной на каждую: не JSON; корень не объект; запись не
# объект; число меньше нуля; `bool` (в Python `True` — это `int`, но S4 его
# целым не считает); поле не из `FIELDS`; дробное число; текст не в UTF-8
# (UTF-16 с меткой порядка байт `FF FE` — `UnicodeDecodeError`, тоже
# `ValueError`, а не `JSONDecodeError`). Далее — ветки, добавленные
# решениями контролёра по задаче 2.2 (RecursionError, суррогат, повтор
# ключа), и случаи, отличающие верную проверку от правдоподобной ошибки; у
# каждого параметра свой комментарий «почему»:
# - ветки решений контролёра: глубокая вложенность (`RecursionError`, а не
#   `ValueError`); ключ с одиноким суррогатом (не кодируется в UTF-8);
#   ключ, повторённый в одном объекте, — в корне и внутри записи;
# - случаи, отличающие верную проверку от правдоподобной ошибки: ещё два
#   «не UTF-8» (cp1251 и UTF-8 с меткой порядка байт); плохая запись
#   последней и посередине, а не только первой; `false` рядом с `true`
#   (`bool` отвергается целиком, а не одно `True`); сырой управляющий
#   символ внутри строки (строгий разбор JSON); суррогат не первым и рядом
#   с правильной записью, нижний суррогат из диапазона `surrogateescape`
#   (U+DC80..U+DCFF) и суррогат первым ключом с пустой записью; повтор
#   ключа не рядом со своим двойником, не первым и не последним;
# - случаи ворот 2 (задача 2.7a): `null`, строка, `2.0` и `NaN` на месте
#   целого; поле в другом регистре или с пробелом; пустой файл и файл из
#   пробелов; запись `null` и `0` (ложные, в отличие от `3`).
_BROKEN_CONTENTS = [
    pytest.param(b"not json", id="not-json"),
    pytest.param(b"[]", id="list"),
    pytest.param(b'{"a.jpg": 3}', id="record-not-object"),
    pytest.param(b'{"a.jpg": {"printed": -1}}', id="negative"),
    pytest.param(b'{"a.jpg": {"printed": true}}', id="bool"),
    pytest.param(b'{"a.jpg": {"x": 1}}', id="foreign-field"),
    pytest.param(b'{"a.jpg": {"printed": 1.5}}', id="float"),
    pytest.param('{"a.jpg": {"printed": 1}}'.encode("utf-16"), id="utf-16"),
    # S2, S4: файл, сохранённый руками в cp1251 (Блокнот «ANSI», TextEdit
    # «Кириллица (Windows)»). Вне кириллицы это верный JSON, и сломан он
    # только тем, что не UTF-8, — в отличие от UTF-16, у которого после любого
    # снисходительного декодера ломается и сама грамматика JSON. Поэтому
    # только он отличает `UnicodeDecodeError` от чтения с `errors="replace"`
    # или `"ignore"`: с ними `increment` переписал бы файл с U+FFFD или без
    # букв имени, а исходные байты пропали бы без отложенной копии. Чтение с
    # `"surrogateescape"` от строгого не отличимо ничем (ворота 2): байты
    # становятся одинокими суррогатами, а такой файл отвергается и так — в
    # имени проверкой кодирования в UTF-8 (параметр
    # `lone-low-surrogate-dc80`), в поле — `FIELDS`, в значении — проверкой
    # целого, вне строки — разбором JSON. `"backslashreplace"` отличает тест
    # `test_undecodable_byte_after_backslash_is_broken`.
    pytest.param(
        '{"a.jpg": {"printed": 1}, "меню.jpg": {"printed": 2}}'.encode("cp1251"),
        id="cp1251",
    ),
    # S2, S4: метка порядка байт UTF-8 `EF BB BF` в начале. Чтение ровно в
    # `utf-8` оставляет её символом U+FEFF, и `json.loads` отвергает текст
    # (`JSONDecodeError`) — файл битый. Кодировка `utf-8-sig` метку молча
    # сняла бы и приняла файл, а S2 требует именно `utf-8`.
    pytest.param(b'\xef\xbb\xbf{"a.jpg": {"printed": 1}}', id="utf-8-bom"),
    # S4: одна плохая запись ломает весь файл, где бы она ни стояла.
    # `json.loads` сохраняет порядок файла, а у остальных параметров запись
    # одна, поэтому здесь правильная запись первой, а плохая — последней
    # (чужое поле) или посередине (отрицательное число): иначе проверка,
    # остановившаяся на первой записи, прошла бы все тесты.
    pytest.param(b'{"a.jpg": {"printed": 1}, "b.jpg": {"x": 1}}', id="bad-last-record"),
    pytest.param(
        b'{"a.jpg": {"printed": 1}, "b.jpg": {"printed": -1}, "c.jpg": {"failed": 2}}',
        id="bad-record-not-first",
    ),
    # S4: `bool` не целое целиком, а не только `true`. Проверка «значение —
    # не `True`» пропустила бы `false` как 0, и `false` осталось бы в файле
    # и ушло бы в API. `get` такой ошибки не видит (`Counts(False, 0)` равно
    # `Counts(0, 0)`), её ловит `increment`: файл должен уйти в `BROKEN_FILE`.
    pytest.param(b'{"a.jpg": {"printed": false}}', id="false"),
    # S4: сырой символ табуляции внутри строки-ключа (не `\t`, а сам байт
    # 0x09) — неверный JSON, `json.loads` бросает `JSONDecodeError`. Разбор
    # должен быть строгим: с `strict=False` такой файл принялся бы, и `get`
    # показал бы счёт правильной записи `a.jpg` по битому файлу.
    pytest.param(
        b'{"a.jpg": {"printed": 1}, "b\tc.jpg": {"printed": 1}}',
        id="raw-control-char",
    ),
    # S4 (решение контролёра по задаче 2.2): вложенность глубже предела
    # рекурсии Python. `json.loads` бросает `RecursionError` — это
    # `RuntimeError`, а не `ValueError`. Без отдельного правила `get` бросал
    # бы и список W1 лежал бы, а `increment` падал бы после печати, не
    # отложив файл, — и так при каждом перезапуске.
    pytest.param(b"[" * 100000, id="deep-nesting"),
    # S4 (решение контролёра по задаче 2.2): экранирование `\ud800` в JSON
    # даёт ключ-строку с одиноким суррогатом. Это `str`, и проверка типа его
    # пропускает, но в UTF-8 он не кодируется: `increment` не смог бы
    # записать файл обратно (`UnicodeEncodeError`), и счётчики больше
    # никогда не сохранялись бы. Такой ключ — нарушение структуры.
    pytest.param(b'{"\\ud800.jpg": {"printed": 1}}', id="lone-surrogate"),
    # S4: тот же суррогат не первым и рядом с правильной записью `a.jpg`.
    # Здесь `get("a.jpg")` без проверки ключа дал бы `Counts(2, 0)`: так
    # видно, что файл битый целиком и для `get`, а не только для записи в
    # `increment` (у параметра выше `a.jpg` в файле нет, и ноль вышел бы и
    # без проверки).
    pytest.param(
        b'{"a.jpg": {"printed": 2}, "b\\ud83d.jpg": {"printed": 1}}',
        id="lone-surrogate-not-first",
    ),
    # S4: одинокий суррогат из нижней половины, U+DC80..U+DCFF, — тоже не
    # UTF-8. Это ровно тот диапазон, которым `surrogateescape` прячет
    # недекодируемые байты, поэтому проверка через `os.fsencode(name)` или
    # `encode("utf-8", "surrogateescape")` его пропустила бы (оба параметра
    # выше — верхние суррогаты, их такая проверка ловит). Тогда `get("a.jpg")`
    # дал бы `Counts(2, 0)`, а `_write` на строгом UTF-8 падал бы с
    # `UnicodeEncodeError` при каждой печати, файл не откладывался бы, и счёт
    # больше никогда не сохранялся бы (решение контролёра по задаче 2.2).
    pytest.param(
        b'{"a.jpg": {"printed": 2}, "b\\udc80.jpg": {"printed": 1}}',
        id="lone-low-surrogate-dc80",
    ),
    # S4: суррогат первым ключом и с пустой записью `{}`, а правильная
    # `a.jpg` — после него. Пустая запись пропускает проверку в цикле по
    # полям записи: полей нет, и тело цикла не выполняется ни разу. А плохой
    # ключ — первый, поэтому его пропускает и проверка одного лишь последнего
    # ключа (переменной цикла, оставшейся после него): последней стоит
    # правильная `a.jpg`. У всех параметров выше с суррогатом запись
    # непустая, а плохой ключ последний или единственный, и обе ошибки
    # проходили бы их. Под любой из них `get("a.jpg")` дал бы `Counts(2, 0)`,
    # а `increment` падал бы с `UnicodeEncodeError` в `_write`, не отложив
    # файл, — и так при каждой печати (решение контролёра по задаче 2.2).
    pytest.param(
        b'{"\\ud800.jpg": {}, "a.jpg": {"printed": 2}}',
        id="lone-surrogate-first-empty-record",
    ),
    # S4 (решение контролёра по задаче 2.2): ключ повторён в корне — ручная
    # правка вписала запись `a.jpg` ещё раз. `json.loads` без
    # `object_pairs_hook` молча оставил бы последнюю: `get("a.jpg")` показал
    # бы `Counts(0, 1)`, а `increment` переписал бы файл без `"printed": 7`,
    # и прежний счёт пропал бы без отложенной копии в `BROKEN_FILE`.
    pytest.param(
        b'{"a.jpg": {"printed": 7}, "a.jpg": {"failed": 1}}',
        id="duplicate-key-root",
    ),
    # S4: тот же повтор внутри записи — поле `printed` дважды. Повтор
    # отвергается в любом объекте, а не только среди имён картинок в корне:
    # проверка одного корня пропустила бы его, `get` дал бы `Counts(2, 0)`,
    # а `increment` записал бы 3 и потерял бы `"printed": 1` без копии.
    pytest.param(
        b'{"a.jpg": {"printed": 1, "printed": 2}}',
        id="duplicate-field",
    ),
    # S4: повтор ключа не рядом со своим двойником, не первым и не
    # последним ключом объекта: `b.jpg` повторён через `c.jpg`, первый ключ
    # `a.jpg`, последний `d.jpg`. В двух параметрах выше пар всего две:
    # повтор стоит вплотную к двойнику и сразу и первый, и последний ключ,
    # поэтому их проходила бы проверка «повтор среди соседних пар», «первый
    # ключ повторён дальше» или «последний ключ был раньше». Соседство
    # правдоподобно: `_write` пишет ключи по алфавиту (`sort_keys=True`), и
    # кажется, что повтор всегда встанет рядом. Но правка руками вставляет
    # запись куда угодно, а `json.loads` молча оставил бы последнюю
    # `b.jpg`. Тогда `get("a.jpg")` дал бы `Counts(2, 0)` по битому файлу, а
    # `increment` прибавил бы к `"printed": 2` и переписал бы файл без
    # `"printed": 7` — счёт `b.jpg` пропал бы без копии в `BROKEN_FILE`
    # (решение контролёра по задаче 2.2).
    pytest.param(
        b'{"a.jpg": {"printed": 2}, "b.jpg": {"printed": 7}, "c.jpg": {},'
        b' "b.jpg": {"failed": 1}, "d.jpg": {}}',
        id="duplicate-key-not-adjacent",
    ),
    # Повтор ключа с одинаковыми значениями — тоже битый файл: S4 не делает
    # исключения для равных значений. Иначе проверка «повтор, только если
    # значения различаются» проходила бы все параметры выше — в них значения
    # разные (решение контролёра по задаче 2.2).
    pytest.param(
        b'{"a.jpg": {"printed": 1}, "a.jpg": {"printed": 1}}',
        id="duplicate-key-same-value",
    ),
    # S4 (ворота 2): `null` и строка на месте числа — не целое. Кавычки
    # вокруг числа (`"3"`) — обычная опечатка правки руками. Проверка, в
    # которой `value < 0` стоит раньше `isinstance(value, int)` (или тип
    # сверяется только с `bool` и `float`, как у параметров выше), на них
    # бросает `TypeError` вместо «файл битый»: `get` уронил бы весь список W1
    # — оператор не увидел бы ни одной картинки, — а `increment` падал бы
    # после печати, не отложив файл, и так до правки файла руками.
    pytest.param(b'{"a.jpg": {"printed": null}}', id="null-value"),
    pytest.param(b'{"a.jpg": {"printed": "3"}}', id="string-value"),
    # S4 (ворота 2): целое — это `int` Python. `2.0` `json.loads` разбирает
    # во `float`, и целым оно не считается, хоть дробной части и нет: у
    # параметра `float` выше (`1.5`) она есть, и проверка «дробной части
    # нет» проходила бы его. С такой проверкой `get` показал бы счёт по
    # битому файлу, а `increment` записал бы `"printed": 3.0` — файл
    # навсегда ушёл бы из целых чисел формата § 4.2 без отложенной копии.
    # `NaN` `json.loads` принимает по умолчанию; проверка через `int(value)`
    # бросала бы на нём `ValueError` уже после разбора, мимо «файл битый»:
    # список W1 лежал бы, а `increment` падал бы после печати.
    pytest.param(b'{"a.jpg": {"printed": 2.0}}', id="whole-float"),
    pytest.param(b'{"a.jpg": {"printed": NaN}}', id="nan"),
    # S4 (ворота 2): поле — ровно `printed` или `failed`, без приведения
    # регистра и пробелов по краям. `"Printed"` с заглавной и `"printed "` с
    # пробелом — опечатки правки руками. Проверка, которая прощала бы их,
    # пропустила бы файл, но `_full` такого поля не нашёл бы: `get` показал
    # бы `Counts(0, 1)` по битому файлу, а `increment` молча выбросил бы
    # `"Printed": 5` без копии в `BROKEN_FILE` — пять печатей пропали бы
    # (решение задачи 1.2: чужие ключи уходят в `BROKEN_FILE`, а не
    # пропадают). У параметров выше чужое поле — `x`, его никакое приведение
    # в `printed` не превратит.
    pytest.param(b'{"a.jpg": {"Printed": 5, "failed": 1}}', id="field-other-case"),
    pytest.param(b'{"a.jpg": {"printed ": 5, "failed": 1}}', id="field-with-space"),
    # S4 (ворота 2): пустой файл (0 байт) и файл из пробелов и перевода
    # строки — не JSON: `json.loads` бросает `JSONDecodeError`, и по букве S4
    # файл битый. Такой файл остаётся, например, после копирования папки,
    # прерванного на полной флешке, или после «выделить всё, удалить,
    # сохранить» в редакторе. `get` даёт ноль при любом прочтении, поэтому
    # разницу видит только `increment`: он откладывает пустой файл в
    # `BROKEN_FILE`, и по этой копии оператор поймёт, почему счёт начался с
    # нуля. Короткий путь «пустой файл — счёта ещё нет» начал бы счёт с
    # пустого молча, не оставив следа. Сам счёт при этом не теряется — в
    # пустом файле его нет.
    pytest.param(b"", id="empty-file"),
    pytest.param(b"  \n", id="whitespace-file"),
    # S4 (ворота 2): запись — объект, даже «пустая по смыслу». `null` и `0`
    # на месте записи ложны, и проверка «пустая запись — пропустить» перед
    # проверкой типа (так легко прочесть правило «пустая запись `{}` — не
    # битая») их пропустила бы; у `record-not-object` выше запись `3` —
    # истинна. Тогда `_full` бросал бы `AttributeError`: `get` уронил бы
    # список W1, а `increment` падал бы после печати, не отложив файл. Во
    # втором параметре плохая запись первая, а `a.jpg` правильная:
    # `get("a.jpg")` показал бы `Counts(2, 0)` по битому файлу.
    pytest.param(b'{"a.jpg": null}', id="record-null"),
    pytest.param(b'{"z.jpg": 0, "a.jpg": {"printed": 2}}', id="record-zero-first"),
]


@pytest.mark.parametrize("content", _BROKEN_CONTENTS)
def test_broken_file_reads_as_zero_untouched(tmp_path: Path, content: bytes) -> None:
    """S4: битый файл счётчиков `get` читает как `Counts(0, 0)` и не трогает.

    Список картинок (W1) читает счёт тем же чтением (`snapshot`): исключение из
    одного испорченного правкой руками файла уронило бы весь список, и
    оператор не увидел бы ни одной картинки. `get` только показывает:
    файл остаётся байт в байт прежним, отложенной копии `BROKEN_FILE` нет —
    откладывает битый файл только `increment`. Параметры — каждая ветка S4:
    не JSON, чужая структура, отрицательное, `bool`, чужое поле, дробное,
    не UTF-8 (UTF-16, cp1251, UTF-8 с меткой порядка байт), а также плохая
    запись не первой: тогда запрошенная `a.jpg` сама правильная, и ноль
    выходит, только если проверен весь файл. И ещё `false`, сырой
    управляющий символ в строке, вложенность глубже предела рекурсии
    (`RecursionError`), ключ с одиноким суррогатом — отдельно и рядом с
    правильной `a.jpg`, верхним и нижним из диапазона `surrogateescape`,
    первым ключом с пустой записью, — и ключ, повторённый в корне (в том
    числе не рядом со своим двойником, не первым и не последним) или
    внутри записи (решения контролёра по задаче 2.2): без них `get` на
    глубокой вложенности бросал бы, и список W1 лежал бы, а при повторе
    ключа показывал бы счёт одной из двух записей. Ворота 2 добавили
    `null`, строку, `2.0` и `NaN` на месте целого, поле в другом регистре
    или с пробелом и запись `null` или `0`: на них правдоподобная ошибка
    проверки бросала бы `TypeError`, `ValueError` или `AttributeError` —
    список W1 лежал бы — или показывала бы счёт по битому файлу. Пустой
    файл и файл из пробелов `get` читает как ноль при любом прочтении, их
    отличает `increment`.
    """
    path = tmp_path / STATS_FILE
    path.write_bytes(content)

    assert Stats(tmp_path).get("a.jpg") == Counts(0, 0)
    assert path.read_bytes() == content
    assert not (tmp_path / BROKEN_FILE).exists()
    assert sorted(os.listdir(tmp_path)) == [STATS_FILE]


def test_increment_moves_broken_file_aside(tmp_path: Path) -> None:
    """S4: `increment` откладывает битый файл в `BROKEN_FILE` и считает с пустого.

    Печать уже состоялась, и счёт не должен сорваться из-за файла,
    испорченного правкой руками: счёт начинается с пустого, а сам битый
    файл не удаляется, а переименовывается — накопленное можно восстановить
    руками. Имя отложенной копии сверяется литералом, а не только
    константой: это часть интерфейса § 4.2, по нему оператор найдёт файл, и
    правка константы не должна пройти незамеченной. После записи в папке
    ровно два файла — отложенный и новый, временных нет (S3).
    """
    assert BROKEN_FILE == ".print-stats.broken.json"
    (tmp_path / STATS_FILE).write_bytes(b"not json")

    assert Stats(tmp_path).increment("a.jpg", "printed") == Counts(1, 0)
    assert (tmp_path / ".print-stats.broken.json").read_bytes() == b"not json"
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert data == {"a.jpg": {"failed": 0, "printed": 1}}
    assert sorted(os.listdir(tmp_path)) == [".print-stats.broken.json", ".print-stats.json"]


def test_second_broken_file_replaces_first(tmp_path: Path) -> None:
    """S4: новый битый файл заменяет прежнюю отложенную копию.

    Отложенная копия одна: второй битый файл занимает её место
    (`os.replace`), а не роняет печать оттого, что место уже занято.
    Каждый раз счёт — с пустого: второй `increment` даёт снова
    `Counts(1, 0)`, а не прибавку к счёту, записанному после первого.
    """
    stats = Stats(tmp_path)
    path = tmp_path / STATS_FILE
    path.write_bytes(b"one")
    assert stats.increment("a.jpg", "printed") == Counts(1, 0)
    path.write_bytes(b"two")

    assert stats.increment("a.jpg", "printed") == Counts(1, 0)
    assert (tmp_path / BROKEN_FILE).read_bytes() == b"two"
    assert sorted(os.listdir(tmp_path)) == [BROKEN_FILE, STATS_FILE]


@pytest.mark.parametrize("content", _BROKEN_CONTENTS)
def test_increment_moves_each_broken_file_aside(tmp_path: Path, content: bytes) -> None:
    """S4: `increment` откладывает битый файл при каждой ветке правила, не только «не JSON».

    Те же содержимые, что у `get` в `test_broken_file_reads_as_zero_untouched`:
    отложенная копия — байт в байт прежний файл, счёт — с пустого. Без
    этого `increment` мог бы считать битым меньше, чем `get`: например,
    принять `true` за 1 и записать 2, молча выбросить чужое поле при
    дополнении записи до `FIELDS` (решение задачи 1.2: чужие ключи уходят в
    `BROKEN_FILE`, а не пропадают) или уронить печать на тексте в UTF-16.
    На глубокой вложенности (`RecursionError`) и на ключе с одиноким
    суррогатом (`UnicodeEncodeError` при записи) `increment` иначе падал бы
    после печати, не отложив файл, — и так при каждой следующей печати, а
    при повторе ключа переписал бы файл с одной из двух записей и потерял
    бы другую без копии (решения контролёра по задаче 2.2). Параметры
    ворот 2: на `null`, строке, `NaN` и записи `null` или `0` правдоподобная
    ошибка проверки роняла бы `increment` после печати, на `2.0` он записал
    бы `3.0`, на поле `Printed` молча выбросил бы пять печатей, а пустой
    файл и файл из пробелов не отложил бы, начав счёт с пустого без следа.
    """
    (tmp_path / STATS_FILE).write_bytes(content)

    assert Stats(tmp_path).increment("a.jpg", "printed") == Counts(1, 0)
    assert (tmp_path / BROKEN_FILE).read_bytes() == content
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert data == {"a.jpg": {"failed": 0, "printed": 1}}


def test_undecodable_byte_after_backslash_is_broken(tmp_path: Path) -> None:
    """S2, S4: байт не в UTF-8 сразу после обратной черты в строке — битый файл.

    Файл правили руками в редакторе, сохранившем его в cp1251, и набрали
    имя с обратной чертой перед «я» без экранирования JSON (`\\я`, а не
    `\\\\я`; «я» в cp1251 — байт `FF`). Строгое чтение в `utf-8` (S2)
    даёт `UnicodeDecodeError`, и по S4 файл битый. Чтение с
    `errors="backslashreplace"` кажется ему равным: байт становится
    четырьмя символами `\\xff`, а `\\x` в JSON — ошибка разбора.
    Но здесь перед ним обратная черта самого файла, и вместе они дают
    экранированную обратную черту: текст — верный JSON с ключом `x\\xff.jpg`.
    Такое чтение приняло бы файл: `get("a.jpg")` показал бы 5 по битому
    файлу, а `increment` записал бы 6 и переписал бы имя с двумя обратными
    чертами — исходный байт пропал бы без отложенной копии в `BROKEN_FILE`.
    По S4 `get` — ноль, файл не тронут; `increment` откладывает его байт в
    байт и считает с пустого. Случай редкий, но другого способа отличить
    это чтение от строгого нет: у `cp1251` в `_BROKEN_CONTENTS` байты не в
    UTF-8 не стоят после обратной черты, и `\\x` ломает разбор.
    """
    content = b'{"a.jpg": {"printed": 5}, "x\\\xff.jpg": {}}'
    # Предпосылка: при чтении с `backslashreplace` это верный JSON с
    # правильной `a.jpg`, то есть файл сломан только байтом не в UTF-8.
    # Иначе тест не отличал бы строгое чтение от снисходительного.
    lenient = json.loads(content.decode("utf-8", errors="backslashreplace"))
    assert lenient == {"a.jpg": {"printed": 5}, "x\\xff.jpg": {}}
    path = tmp_path / STATS_FILE
    path.write_bytes(content)
    stats = Stats(tmp_path)

    assert stats.get("a.jpg") == Counts(0, 0)
    assert path.read_bytes() == content
    assert not (tmp_path / BROKEN_FILE).exists()
    assert stats.increment("a.jpg", "printed") == Counts(1, 0)
    assert (tmp_path / BROKEN_FILE).read_bytes() == content
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"a.jpg": {"failed": 0, "printed": 1}}


def test_one_bad_record_breaks_whole_file(tmp_path: Path) -> None:
    """S4: одна испорченная запись делает битым весь файл — и для `get`, и для `increment`.

    Структура S4 — свойство файла целиком, а не одной записи. У `a.jpg`
    рядом с настоящим `"printed": 2` чужое поле `"x"`, запись `b.jpg`
    правильная. У большинства параметров `test_broken_file_reads_as_zero_untouched`
    в файле одна запись, и ноль вышел бы и без проверки ключей, и при
    проверке одной лишь запрошенной записи; плохую запись не первой
    закрепляют параметры `bad-last-record` и `bad-record-not-first`. Здесь
    плохая запись первой, а спрашивается и правильная:
    - `get("a.jpg")` — ноль, а не `Counts(2, 0)`: ключи ⊆ `FIELDS`;
    - `get("b.jpg")` — тоже ноль, а не `Counts(0, 3)`: проверяется весь
      файл, иначе страница показывала бы счёт по файлу, который `increment`
      отложит;
    - `increment("b.jpg", "failed")` — `Counts(0, 1)`, а не `Counts(0, 4)`:
      счёт с пустого, из отложенного файла ничего не переносится, и
      `"printed": 2` картинки `a.jpg` в новый файл тоже не попадает.

    Отложенная копия — байт в байт прежний файл: всё накопленное можно
    вернуть руками.
    """
    content = b'{"a.jpg": {"printed": 2, "x": 1}, "b.jpg": {"failed": 3}}'
    path = tmp_path / STATS_FILE
    path.write_bytes(content)
    stats = Stats(tmp_path)

    assert stats.get("a.jpg") == Counts(0, 0)
    assert stats.get("b.jpg") == Counts(0, 0)
    assert path.read_bytes() == content
    assert stats.increment("b.jpg", "failed") == Counts(0, 1)
    assert (tmp_path / BROKEN_FILE).read_bytes() == content
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"b.jpg": {"failed": 1, "printed": 0}}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(b"{}", {"a.jpg": {"failed": 0, "printed": 1}}, id="empty-root"),
        pytest.param(b'{"a.jpg": {}}', {"a.jpg": {"failed": 0, "printed": 1}}, id="empty-record"),
        pytest.param(
            b'{"a.jpg": {}, "b.jpg": {"failed": 2}}',
            {"a.jpg": {"failed": 0, "printed": 1}, "b.jpg": {"failed": 2, "printed": 0}},
            id="empty-record-among-others",
        ),
        # S6, § 4.2 (ворота 2): пустая запись другой картинки остаётся в
        # файле и дописывается до обоих полей. В параметрах выше пустая
        # запись — та, к которой прибавляют, и `setdefault` вернул бы её,
        # даже если бы `increment`, переписывая файл, выбрасывал пустые
        # записи («сжатие» файла). Запись, которую оператор завёл руками,
        # пропала бы после первой же печати другой картинки.
        pytest.param(
            b'{"z.jpg": {}}',
            {"a.jpg": {"failed": 0, "printed": 1}, "z.jpg": {"failed": 0, "printed": 0}},
            id="other-empty-record",
        ),
    ],
)
def test_empty_structures_are_not_broken(
    tmp_path: Path, content: bytes, expected: Dict[str, Dict[str, int]]
) -> None:
    """S1, S4: пустой объект и пустая запись — правильная структура, а не битый файл.

    S4 требует «строка → объект, ключи ⊆ `FIELDS`»: пустое множество ключей
    — подмножество, а пустой корень — объект без записей. `{}` — счёт,
    сброшенный руками; `{"a.jpg": {}}` — запись без полей, и по S1
    недостающее поле — 0. `get` читает их как ноль, а `increment` считает
    поверх них и не откладывает их в `BROKEN_FILE`: иначе сброс руками затёр
    бы прежнюю отложенную копию (здесь `b"old"`), в которой, может быть,
    весь накопленный счёт, а соседняя правильная запись `b.jpg` пропала бы.
    Пустая запись другой картинки (`z.jpg`) после записи остаётся в файле с
    обоими полями: записи не вычищаются (S6), и каждая пишется в формате
    § 4.2.
    """
    path = tmp_path / STATS_FILE
    path.write_bytes(content)
    (tmp_path / BROKEN_FILE).write_bytes(b"old")
    stats = Stats(tmp_path)

    assert stats.get("a.jpg") == Counts(0, 0)
    assert path.read_bytes() == content
    assert stats.increment("a.jpg", "printed") == Counts(1, 0)
    assert (tmp_path / BROKEN_FILE).read_bytes() == b"old"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == expected
    assert sorted(os.listdir(tmp_path)) == [BROKEN_FILE, STATS_FILE]


def test_broken_file_rename_failure_is_write_error(tmp_path: Path) -> None:
    """S4, S5: битый файл не удалось отложить — `StatsWriteError("Is a directory")`, файл на месте.

    На месте `BROKEN_FILE` каталог: переименование файла поверх каталога
    даёт настоящий `IsADirectoryError`, а `mkstemp` и запись в папке при
    этом прошли бы. S5 называет переименование битого файла среди сбоев
    `increment`: наружу — причина, её покажет страница. Проглоти код этот
    сбой и считай с пустого, новый счёт затёр бы битый файл, не отложив
    его, — а в нём, может быть, весь накопленный счёт с одной опечаткой
    правки руками. Битый файл — байт в байт прежний, каталог пуст,
    временных файлов нет. `chmod` папки здесь не годится: с ним упал бы и
    `mkstemp`, и проглоченный сбой переименования не был бы виден.
    """
    path = tmp_path / STATS_FILE
    path.write_bytes(b"not json")
    (tmp_path / BROKEN_FILE).mkdir()

    with pytest.raises(StatsWriteError) as caught:
        Stats(tmp_path).increment("a.jpg", "printed")
    assert str(caught.value) == "Is a directory"
    assert isinstance(caught.value.__cause__, IsADirectoryError)
    assert path.read_bytes() == b"not json"
    assert sorted(os.listdir(tmp_path)) == [BROKEN_FILE, STATS_FILE]
    assert os.listdir(tmp_path / BROKEN_FILE) == []


def test_broken_file_rename_denied_keeps_old_copy(
    tmp_path: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """S4, S5: битый файл откладывается переименованием — при отказе прежняя отложенная копия цела.

    Папку закрыли от записи (в окне «Свойства» Finder «Только чтение» для
    себя, без «Применить к вложенным объектам»), а файлы в ней остались
    записываемыми. В папке уже лежит `BROKEN_FILE` — прежняя отложенная
    копия, в которой, может быть, весь накопленный счёт, — и файл счётчиков
    снова битый. Переименованию нужно право записи в папку, поэтому
    `os.replace` получает EACCES: наружу `StatsWriteError("Permission
    denied")`, и страница скажет, что счётчик не сохранился (W4 п. 5).
    Копия вместо переименования (`shutil.copyfile`, `shutil.move`) пишет в
    уже существующий записываемый файл, и права папки ей не нужны: она
    затёрла бы прежнюю отложенную копию новым битым файлом, а следующий шаг
    (`mkstemp`) всё равно упал бы — счёт не сохранён, а прежняя копия
    пропала навсегда. Здесь байты `BROKEN_FILE` и файла счётчиков прежние,
    новых файлов нет. В отличие от `test_broken_file_rename_failure_is_write_error`,
    здесь нужен именно `chmod` папки: сбой должен задеть переименование, но
    не запись в уже существующий файл.
    """
    path = tmp_path / STATS_FILE
    path.write_bytes(b"not json NEW")
    old = tmp_path / BROKEN_FILE
    old.write_bytes(b"not json OLD")
    # § 7.3: настоящий `chmod` папки — читать и входить можно, писать нет.
    chmod_to(tmp_path, 0o500)
    # Предпосылка: прежняя копия записываема и после `chmod` папки — её
    # права копии не мешают, и разницу даёт только способ отложить файл.
    assert os.access(old, os.W_OK)

    with pytest.raises(StatsWriteError) as caught:
        Stats(tmp_path).increment("a.jpg", "printed")
    assert str(caught.value) == "Permission denied"
    assert isinstance(caught.value.__cause__, PermissionError)
    assert old.read_bytes() == b"not json OLD"
    assert path.read_bytes() == b"not json NEW"
    assert sorted(os.listdir(tmp_path)) == [BROKEN_FILE, STATS_FILE]


def test_key_is_exact_listdir_name(tmp_path: Path) -> None:
    """S6: ключ счётчика — имя ровно как в `os.listdir`, без нормализации Unicode.

    «й» в разложенной форме NFD («и» + знак краткой) — так имена приходят
    с дисков HFS+ и из архивов, сделанных на маке; APFS хранит имя в той
    форме, в какой файл создали, и `scan` отдаёт его как есть. `increment`
    пишет в файл ровно этот ключ: он совпадает со строкой, которую
    показывает список и по которой `find` ищет файл (F7 — точное
    сравнение). Составная форма NFC — другое имя, и её счёт — ноль.
    И в обратную сторону: ключ в NFC («ё.jpg», набранное с клавиатуры) по
    форме NFD не находится, а в файл пишется ровно в NFC. И `increment`
    ищет ключ ровно как есть: другая форма имени, уже записанного в файле,
    заводит свой ключ со счётом с нуля, а не прибавляет к прежней записи.
    """
    n = unicodedata.normalize("NFD", "й.jpg")
    # Предпосылка: формы действительно разные, иначе тест ничего не проверяет.
    assert n != unicodedata.normalize("NFC", n)
    make_jpeg(tmp_path / n, 576, 100)
    name = scan(tmp_path)[0].name
    assert name == n
    stats = Stats(tmp_path)

    assert stats.increment(name, "printed") == Counts(1, 0)
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert list(data) == [n]
    assert stats.get(n) == Counts(1, 0)
    assert stats.get(unicodedata.normalize("NFC", n)) == Counts(0, 0)
    # S6 в обратную сторону: ключ в составной форме NFC не находится по
    # разложенной NFD. Проверки выше этого не видят: там ключ в файле — NFD,
    # и запасной поиск в `get` по `unicodedata.normalize("NFC", name)`
    # проходил бы их (NFC-запрос к NFC и приводится, а NFD-ключа он не
    # ищет). Здесь такой поиск нашёл бы ключ `c` по NFD-имени, и картинка,
    # скопированная с диска HFS+ (имя в NFD), продолжила бы счёт другого
    # файла. Ключ в файле — ровно `c`, без приведения при записи.
    c = unicodedata.normalize("NFC", "ё.jpg")
    # Предпосылка: у «ё» разложенная форма тоже другая строка.
    assert c != unicodedata.normalize("NFD", c)
    assert stats.increment(c, "printed") == Counts(1, 0)
    assert stats.get(unicodedata.normalize("NFD", c)) == Counts(0, 0)
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert sorted(data) == sorted([n, c])
    # S6: и `increment` не сливает другую форму имени с ключом, уже
    # записанным в файле. Проверки `get` выше этого не видят: они файл не
    # пишут. А `increment` выше получал только имена, которых в файле ещё
    # не было ни в какой форме, поэтому слияние «ключ, у которого NFC (или
    # NFD) совпадает с NFC (или NFD) имени» их проходило бы. Здесь в файле
    # уже `n` (NFD) и `c` (NFC), и вторая форма каждого — отдельный ключ со
    # счётом с нуля, а прежние записи не меняются. Так бывает на деле:
    # запись удалённой копии `й.jpg` с диска HFS+ (NFD) осталась в файле
    # (S6), а новую `й.jpg`, набранную с клавиатуры (NFC), напечатали. При
    # слиянии её счёт ушёл бы в чужую запись, а `get` показал бы ей ноль.
    # Нужны оба направления. Слияние, которое приводит к NFC только ключи
    # файла (или к NFD только само имя), находит `n` по `NFC(n)`, но не `c`
    # по `NFD(c)`; приводящее к NFD только ключи (или к NFC только имя) —
    # наоборот. Слияние по NFC обеих сторон ловят обе проверки.
    assert stats.increment(unicodedata.normalize("NFC", n), "failed") == Counts(0, 1)
    assert stats.get(n) == Counts(1, 0)
    assert stats.increment(unicodedata.normalize("NFD", c), "failed") == Counts(0, 1)
    assert stats.get(c) == Counts(1, 0)
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert data == {
        n: {"failed": 0, "printed": 1},
        c: {"failed": 0, "printed": 1},
        unicodedata.normalize("NFC", n): {"failed": 1, "printed": 0},
        unicodedata.normalize("NFD", c): {"failed": 1, "printed": 0},
    }


def test_key_keeps_case_and_spaces(tmp_path: Path) -> None:
    """S6: ключ — имя как есть: регистр и пробелы по краям не меняются.

    APFS по умолчанию не различает регистр, но хранит его, и «ключ без
    регистра, как на диске» — такая же правдоподобная ошибка, как
    нормализация NFC. `scan` отдаёт `Logo.jpg`, и ключ в файле — ровно
    `Logo.jpg`; имя с пробелом в конце (`b.jpg `) — тоже ключ как есть, без
    `strip`, а `b.jpg` без пробела — другое имя. Переименование в Finder
    одним регистром (`logo.jpg`) даёт другое имя: его счёт с нуля, а запись
    `Logo.jpg` остаётся в файле. Иначе ключ в файле разошёлся бы со
    строкой, которую показывает список (F7), и переименованная картинка
    продолжила бы чужой счёт. В обратную сторону: ключи `logo.jpg` и
    `B.JPG` не находятся по имени в другом регистре или с пробелом по краю,
    а `increment("b.jpg")` рядом с `b.jpg ` и `B.JPG` заводит свой ключ, а
    не прибавляет к чужому.
    """
    make_jpeg(tmp_path / "Logo.jpg", 576, 100)
    name = scan(tmp_path)[0].name
    assert name == "Logo.jpg"
    stats = Stats(tmp_path)

    assert stats.increment(name, "printed") == Counts(1, 0)
    assert stats.increment("b.jpg ", "failed") == Counts(0, 1)
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert sorted(data) == ["Logo.jpg", "b.jpg "]
    assert stats.get("Logo.jpg") == Counts(1, 0)
    assert stats.get("b.jpg ") == Counts(0, 1)
    assert stats.get("b.jpg") == Counts(0, 0)
    os.rename(tmp_path / "Logo.jpg", tmp_path / "logo.jpg")
    # Предпосылка: переименование одним регистром состоялось, и список
    # отдаёт уже новое имя.
    assert scan(tmp_path)[0].name == "logo.jpg"
    assert stats.get("logo.jpg") == Counts(0, 0)
    assert stats.get("Logo.jpg") == Counts(1, 0)
    # S6 в обратную сторону: теперь в файле есть ключ в нижнем регистре и
    # без пробелов, и он не находится ни по тому же имени в другом регистре,
    # ни по имени с пробелом по краю — ни в начале, ни в конце. Без этих
    # проверок выживал бы запасной поиск в `get` по `name.lower()`,
    # `name.strip()` или `name.rstrip()` — он приводит только запрошенное
    # имя, а не ключи файла, и все проверки выше проходил бы: переименование
    # `logo.jpg` в `LOGO.JPG` или в `logo.jpg ` (пробел в конце, как у
    # `b.jpg ` выше) продолжило бы чужой счёт. Пробел в начале ловит
    # `strip`/`lstrip`, но не `rstrip` — поэтому нужен и пробел в конце.
    assert stats.increment("logo.jpg", "failed") == Counts(0, 1)
    assert stats.get("LOGO.JPG") == Counts(0, 0)
    assert stats.get(" logo.jpg") == Counts(0, 0)
    assert stats.get("logo.jpg ") == Counts(0, 0)
    # S6: ключ в верхнем регистре не находится по имени в нижнем. Все ключи
    # выше — в нижнем или смешанном регистре, и `LOGO.JPG` в файле нет,
    # поэтому запасной поиск в `get` по `name.upper()` проходил бы все
    # проверки выше. Здесь в файле `B.JPG`, и такой поиск нашёл бы его по
    # `b.jpg`: картинка, переименованная из `B.JPG` в `b.jpg`, продолжила бы
    # прежний счёт.
    assert stats.increment("B.JPG", "printed") == Counts(1, 0)
    assert stats.get("b.jpg") == Counts(0, 0)
    # S6: и `increment` ищет ключ ровно как есть. `b.jpg` при уже
    # записанных `b.jpg ` (пробел в конце) и `B.JPG` — отдельный ключ со
    # счётом с нуля. Слияние по `strip()` прибавило бы к чужой записи
    # `b.jpg ` (`Counts(1, 1)`), и ключа `b.jpg` в файле не было бы.
    # Проверки `get` выше этого не видят: они файл не пишут.
    assert stats.increment("b.jpg", "printed") == Counts(1, 0)
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert data == {
        "B.JPG": {"failed": 0, "printed": 1},
        "Logo.jpg": {"failed": 0, "printed": 1},
        "b.jpg": {"failed": 0, "printed": 1},
        "b.jpg ": {"failed": 1, "printed": 0},
        "logo.jpg": {"failed": 1, "printed": 0},
    }
    # S6: и `increment` в обратную сторону — имя в другом регистре или с
    # пробелом по краю не прибавляет к уже записанному `logo.jpg`: запасной
    # поиск по `name.lower()`, `name.strip()` или `name.rstrip()` в
    # `increment` проходил бы все проверки выше (там такие имена либо новые,
    # либо без «простого» двойника в файле). Картинка, переименованная из
    # `logo.jpg` в `LOGO.JPG`, продолжила бы чужой счёт.
    assert stats.increment("LOGO.JPG", "printed") == Counts(1, 0)
    assert stats.increment("logo.jpg ", "printed") == Counts(1, 0)
    assert stats.increment(" logo.jpg", "printed") == Counts(1, 0)
    assert stats.get("logo.jpg") == Counts(0, 1)
    # S4 и S6 вместе: `b.jpg` и `b.jpg ` в одном файле — разные ключи, а не
    # повтор. Проверка повтора, которая сравнивала бы ключи после `strip()`,
    # сочла бы такой законный файл битым: `get` показал бы нули, а следующий
    # `increment` унёс бы весь файл в `BROKEN_FILE`.
    assert stats.get("b.jpg ") == Counts(0, 1)


def test_renamed_image_starts_from_zero(tmp_path: Path) -> None:
    """S6: переименованная картинка считается с нуля, запись прежнего имени остаётся.

    Счёт привязан к имени, а не к содержимому: `b.jpg`, в которую
    переименовали `a.jpg`, начинает с `Counts(0, 0)`. Запись `a.jpg`
    остаётся в файле и после записи счёта `b.jpg` — записи удалённых и
    переименованных файлов не вычищаются, и картинка, положенная снова под
    прежним именем, продолжит свой счёт.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")

    assert stats.get("b.jpg") == Counts(0, 0)
    assert stats.increment("b.jpg", "printed") == Counts(1, 0)
    data = json.loads((tmp_path / STATS_FILE).read_text(encoding="utf-8"))
    assert data == {
        "a.jpg": {"failed": 0, "printed": 1},
        "b.jpg": {"failed": 0, "printed": 1},
    }


# W1 (финальная проверка, 2026-09-28): список картинок берёт счётчики из
# одного `snapshot` на запрос, а не из `get` на каждую картинку. Тесты ниже
# закрепляют, что снимок держит те же правила S1–S6, что `get`: для любого
# имени `snapshot().get(name, Counts(0, 0))` — ровно то, что дал бы `get`.


def test_snapshot_without_file_is_empty(tmp_path: Path) -> None:
    """S1, W1: без файла счётчиков снимок пустой, и файл не создаётся.

    Для любого имени пустой снимок даёт `Counts(0, 0)` — как `get`. Папка,
    в которой ещё ничего не печатали, остаётся нетронутой: файл создаёт
    только первый `increment`.
    """
    stats = Stats(tmp_path)

    assert stats.snapshot() == {}
    assert stats.get("a.jpg") == Counts(0, 0)
    assert os.listdir(tmp_path) == []


def test_snapshot_has_every_record_as_is(tmp_path: Path) -> None:
    """S1, S2, S6, W1: снимок — каждая запись файла под ключом ровно как в файле, с обоими полями.

    Недостающее поле и пустая запись — 0 (S1); кириллица читается в UTF-8
    (S2); ключи — как есть, без нормализации Unicode, регистра и пробелов
    по краям: `й.jpg` в формах NFD и NFC, `b.jpg` и `b.jpg ` — разные
    записи (S6); запись картинки, которой в папке нет, тоже в снимке —
    записи удалённых файлов остаются (S6). Для каждого ключа и для имён,
    которых в файле нет (другая форма, регистр, пробел), снимок даёт ровно
    то, что `get`. Снимок только читает: файл байт в байт прежний.
    """
    nfd = unicodedata.normalize("NFD", "й.jpg")
    nfc = unicodedata.normalize("NFC", "й.jpg")
    # Предпосылка: формы действительно разные строки.
    assert nfd != nfc
    records: Dict[str, Dict[str, int]] = {
        "a.jpg": {"printed": 2},
        "b.jpg": {"failed": 3},
        "b.jpg ": {"printed": 5, "failed": 1},
        "c.jpg": {},
        "Logo.jpg": {"printed": 6, "failed": 0},
        "меню.jpg": {"printed": 1, "failed": 1},
        nfd: {"printed": 4, "failed": 0},
        nfc: {"printed": 0, "failed": 7},
        "удалена.jpg": {"printed": 9, "failed": 2},
    }
    path = tmp_path / STATS_FILE
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    stats = Stats(tmp_path)

    snapshot = stats.snapshot()

    assert snapshot == {
        "a.jpg": Counts(2, 0),
        "b.jpg": Counts(0, 3),
        "b.jpg ": Counts(5, 1),
        "c.jpg": Counts(0, 0),
        "Logo.jpg": Counts(6, 0),
        "меню.jpg": Counts(1, 1),
        nfd: Counts(4, 0),
        nfc: Counts(0, 7),
        "удалена.jpg": Counts(9, 2),
    }
    for name in list(records) + ["logo.jpg", "LOGO.JPG", " a.jpg", "a.jpg ", "нет.jpg"]:
        assert snapshot.get(name, Counts(0, 0)) == stats.get(name), name
    assert path.read_bytes() == before
    assert os.listdir(tmp_path) == [STATS_FILE]


def test_snapshot_reads_file_fresh_each_time(tmp_path: Path) -> None:
    """S2, W1: каждый снимок читает файл заново — и после печати, и после правки руками.

    Список берёт снимок при каждом запросе, и страница показывает новые
    числа сразу после печати (U4) и после правки файла руками при
    работающем клиенте. Правка здесь того же размера, и время изменения
    файла возвращено прежним (`os.utime`, как после `touch -r`): снимок,
    запомненный «пока не изменились время и размер», показал бы старый
    счёт. Следующий `increment` прибавляет к исправленному.
    """
    stats = Stats(tmp_path)
    stats.increment("a.jpg", "printed")
    # Первое удачное чтение — до правки: кэш, если он есть, наполнится здесь.
    assert stats.snapshot() == {"a.jpg": Counts(1, 0)}
    path = tmp_path / STATS_FILE
    old = os.stat(path)
    text = path.read_text(encoding="utf-8")
    edited = text.replace('"printed": 1', '"printed": 7')
    assert edited != text
    path.write_text(edited, encoding="utf-8")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    # Предпосылка: ни размер, ни время файла не изменились.
    assert os.stat(path).st_size == old.st_size
    assert os.stat(path).st_mtime_ns == old.st_mtime_ns

    assert stats.snapshot() == {"a.jpg": Counts(7, 0)}
    assert stats.increment("a.jpg", "failed") == Counts(7, 1)
    assert stats.snapshot() == {"a.jpg": Counts(7, 1)}


@pytest.mark.parametrize("content", _BROKEN_CONTENTS)
def test_broken_file_snapshot_is_empty_untouched(tmp_path: Path, content: bytes) -> None:
    """S4, W1: битый файл — пустой снимок, как ноль у `get`; файл не тронут, отложенной копии нет.

    Параметры — те же, что у `test_broken_file_reads_as_zero_untouched`:
    каждая ветка S4, в том числе файлы, где правильная запись `a.jpg`
    стоит рядом с плохой. Снимок из правильных записей такого файла
    показал бы на странице счёт по файлу, который `increment` отложит в
    `BROKEN_FILE` и начнёт с пустого; исключение из снимка уронило бы весь
    список. Откладывает битый файл только `increment`.
    """
    path = tmp_path / STATS_FILE
    path.write_bytes(content)
    stats = Stats(tmp_path)

    assert stats.snapshot() == {}
    assert stats.get("a.jpg") == Counts(0, 0)
    assert path.read_bytes() == content
    assert os.listdir(tmp_path) == [STATS_FILE]


@pytest.mark.parametrize("failure", ["no-permission", "directory", "symlink-loop"])
def test_unreadable_file_snapshot_is_empty(
    tmp_path: Path, chmod_to: Callable[[Path, int], None], failure: str
) -> None:
    """S5, W1: любой сбой чтения файла счётчиков — пустой снимок, а не исключение.

    Сбои настоящие, как у `get` (`test_unreadable_file`,
    `test_directory_in_place_of_file`, `test_symlink_loop_in_place_of_file`):
    файл без прав на чтение (`chmod 000`), каталог на месте файла и
    символическая ссылка сама на себя (ELOOP — простой `OSError`, как EIO
    сбойной флешки). Снимок, который бросал бы, уронил бы весь список
    картинок (W1): оператор не увидел бы ни одной картинки, пока файл не
    починят. Как и `get`, снимок ничего не чинит: на диске всё как было.
    """
    path = tmp_path / STATS_FILE
    if failure == "no-permission":
        Stats(tmp_path).increment("a.jpg", "printed")
        chmod_to(path, 0o000)
    elif failure == "directory":
        path.mkdir()
    else:
        os.symlink(STATS_FILE, path)
    stats = Stats(tmp_path)

    assert stats.snapshot() == {}
    assert stats.get("a.jpg") == Counts(0, 0)
    assert os.listdir(tmp_path) == [STATS_FILE]
