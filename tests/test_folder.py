"""Тесты модуля «Папка» — `photoprint.folder` (спецификация § 4.1).

Закрепляют правила пули 1: F1 (какие файлы попадают в список), F2 (порядок
по `ctime`), F3 п. 3–4 (формат по содержимому и ширина с учётом поворота
EXIF), F6 (чтение для печати) и F7 (поиск только по точному имени). И
правила отказов пули 2 (задача 2.1): F3 п. 1–2 (файл, который Pillow не
открыл, назван по расширению), F4 (список читает только заголовки), F5
(файл, у которого `stat` падает, пропускается), F8 (своего предела высоты
нет, отказывает только предел Pillow) и F9 (нет доступа к папке —
исключение, а не пустой список). Все картинки — настоящие файлы Pillow в
`tmp_path`, тексты ошибок сверяются дословно с § 4.1.
"""
from __future__ import annotations

import errno
import json
import os
import stat
import struct
import subprocess
import sys
import unicodedata
import zlib
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pytest
from PIL import Image, ImageOps, UnidentifiedImageError

from helpers import APP, make_jpeg, make_jpeg_header, make_mpo, make_png, truncate
from photoprint.folder import InvalidImage, check, find, load_printable, scan

# Замер пика памяти вокруг одного вызова в свежем процессе (F4, F6). Свежий —
# потому что пик `ru_maxrss` у самого pytest уже поднят прежними тестами и
# не вырастет от повторного декодирования. Все плагины Pillow загружаются
# до замера (`Image.init()`), чтобы их импорт не попал в прирост. На macOS
# `ru_maxrss` в байтах, на Linux — в килобайтах. Режимы:
# - `scan` — `scan(папка)`, ошибки — тексты записей;
# - `load` — `load_printable(big.png)`, ошибки — текст `InvalidImage`.
# Ошибки печатаются через JSON: так русский текст не зависит от кодировки
# вывода подпроцесса.
_RSS_PROBE = """
import json, resource, sys
from pathlib import Path
from PIL import Image
from photoprint.folder import InvalidImage, load_printable, scan

Image.init()
folder = Path(sys.argv[2])
unit = 1 if sys.platform == "darwin" else 1024
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
errors = None
if sys.argv[1] == "scan":
    errors = [e.error for e in scan(folder)]
else:
    try:
        load_printable(folder / "big.png")
    except InvalidImage as exc:
        errors = [str(exc)]
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({"grown_mb": (after - before) * unit // 2 ** 20, "errors": errors}))
"""

# Имена записей `scan(папка)` в свежем процессе (F1): тест канала FIFO. Если
# `scan` откроет канал, `open()` будет ждать писателя вечно, и повис бы весь
# прогон pytest; в отдельном процессе зависание обрывает тайм-аут
# `subprocess.run`, и тест падает, а прогон идёт дальше.
_SCAN_NAMES = """
import json, sys
from pathlib import Path
from photoprint.folder import scan

print(json.dumps([e.name for e in scan(Path(sys.argv[1]))]))
"""

# `check(файл)` и `load_printable(файл)` в свежем процессе, запущенном с
# `-W error` (F8). Фильтры предупреждений из `pytest.ini` сюда не попадают,
# поэтому виден только фильтр, который ставит сам модуль при импорте.
# Сначала — предпосылка: до импорта `photoprint.folder` открытие файла в
# этом процессе действительно бросает `DecompressionBombWarning`. Потом
# карточка (`check`) и печать (`load_printable`, текст `InvalidImage`; без
# исключения — `None`): фильтр модуля должен действовать на обе, а не
# только на ту, что его ставит рядом с собой. Результат печатается через
# JSON: так русский текст не зависит от кодировки вывода.
_BOMB_PROBE = """
import json, sys
from pathlib import Path
from PIL import Image

path = Path(sys.argv[1])
try:
    Image.open(path).close()
    raised = False
except Image.DecompressionBombWarning:
    raised = True
from photoprint.folder import InvalidImage, check, load_printable

try:
    load_printable(path)
    printed = None
except InvalidImage as exc:
    printed = str(exc)
print(json.dumps({"raised": raised, "reason": check(path), "printed": printed}))
"""


def _raw_exif(entries: List[Tuple[int, int, int, bytes]]) -> bytes:
    """Собрать блок EXIF для `Image.save(exif=...)` из сырых записей IFD0.

    Каждая запись — `(тег, тип TIFF, число значений, байты значения)`.
    Pillow сам записывает теги только правильного типа, а тестам нужен EXIF
    с тегом «не того» типа, как у снимков из кривых редакторов и камер:
    Pillow такой читает, а переписать при повороте не может (F6).
    Порядок байтов — Intel (`II`); значения длиннее 4 байт лежат после IFD0.
    """
    count = len(entries)
    # Смещения считаются от начала TIFF-заголовка: 8 байт заголовка, 2 байта
    # числа записей, по 12 на запись и 4 байта ссылки на следующий IFD.
    data_offset = 8 + 2 + 12 * count + 4
    ifd = struct.pack("<H", count)
    data = b""
    # TIFF требует записи по возрастанию тега.
    for tag, kind, number, value in sorted(entries):
        if len(value) <= 4:
            ifd += struct.pack("<HHI", tag, kind, number) + value.ljust(4, b"\x00")
        else:
            ifd += struct.pack("<HHII", tag, kind, number, data_offset + len(data))
            data += value
    tiff = b"II*\x00" + struct.pack("<I", 8) + ifd + struct.pack("<I", 0) + data
    return b"Exif\x00\x00" + tiff


def _orientation_entry(value: int) -> Tuple[int, int, int, bytes]:
    """Запись IFD0 «Orientation» (0x0112, тип SHORT) с поворотом `value`."""
    return (0x0112, 3, 1, struct.pack("<H", value))


def _xmp_orientation(value: int) -> bytes:
    """Пакет XMP с одним свойством `tiff:Orientation="value"` — для `Image.save(xmp=...)`.

    Так поворот записывают Lightroom и exiftool. Такой пакет остаётся в
    снимке, у которого стёрли EXIF или один его тег поворота
    (`exiftool -EXIF:all=`, «очистка» метаданных перед публикацией), —
    F3 п. 4, F6.
    """
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF'
        ' xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:tiff="http://ns.adobe.com/tiff/1.0/"'
        ' tiff:Orientation="%d"/></rdf:RDF></x:xmpmeta><?xpacket end="w"?>' % value
    ).encode("utf-8")


def _grown_mb(mode: str, folder: Path) -> Tuple[int, Optional[List[Optional[str]]]]:
    """Запустить `_RSS_PROBE` в свежем процессе и вернуть прирост пика памяти.

    `mode` — `scan` или `load` (см. `_RSS_PROBE`). Возвращает
    `(мегабайты, ошибки)`; в режиме `load` без исключения ошибок нет —
    `None`. Процесс запускается из `app/`, чтобы импортировался тот же
    `photoprint.folder`, что проверяют остальные тесты; он только читает
    папку в `tmp_path` и ничего не пишет.
    """
    done = subprocess.run(
        [sys.executable, "-c", _RSS_PROBE, mode, str(folder)],
        cwd=APP, capture_output=True, encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    return result["grown_mb"], result["errors"]


def _marked(width: int, height: int) -> Image.Image:
    """Белая картинка с чёрной меткой 64×16 в левом верхнем углу.

    Метка несимметрична и стоит в углу, поэтому у каждого из восьми
    поворотов и отражений EXIF свой результат: по пикселям видно, повернули
    ли картинку и в какую сторону (F3 п. 4, F6).
    """
    picture = Image.new("RGB", (width, height), (255, 255, 255))
    picture.paste((0, 0, 0), (0, 0, 64, 16))
    return picture


def _png_header(path: Path, width: int, height: int) -> Path:
    """Сохранить PNG, чей заголовок IHDR обещает `width`×`height`, и вернуть путь.

    Настоящий PNG: сигнатура, IHDR (8 бит, RGB), пустой IDAT и IEND, у
    каждого чанка верный CRC. Пикселей нет — файл нужен только
    `Image.open`, у которого предел Pillow (F8) срабатывает по размеру из
    заголовка. Так проверяется огромный PNG — скан или плакат, — который
    весил бы сотни мегабайт.
    """
    def chunk(kind: bytes, data: bytes) -> bytes:
        """Один чанк PNG: длина, тип, данные и CRC типа с данными (big-endian)."""
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    path = Path(path)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")
    )
    return path


def test_scan_lists_only_images(tmp_path: Path) -> None:
    """F1: в список попадают только картинки — непосредственные дети папки.

    Скрытые файлы (в том числе двойники `._a.jpg` с флешек и `.DS_Store`),
    файл счётчиков, ярлык, текст, подпапки — даже с именем `sub.jpg` — и
    файлы внутри подпапок не попадают. Расширение сравнивается без учёта
    регистра (`B.JPG`), PNG попадает — с причиной F3.
    """
    folder = tmp_path
    for name in ("a.jpg", "B.JPG", "c.jpeg"):
        make_jpeg(folder / name, 576, 100)
    make_png(folder / "d.png", 576, 100)
    (folder / "notes.txt").write_text("заметки", encoding="utf-8")
    # Скрытый файл — настоящий JPEG 576 px: отсеять его может только правило
    # «имя не начинается с точки», а не проверка содержимого.
    make_jpeg(folder / ".hidden.jpg", 576, 100)
    # Настоящие сигнатуры: AppleDouble (0x00051607) у двойника `._a.jpg` и
    # «Bud1» у `.DS_Store` — такие файлы macOS оставляет на флешках и
    # сетевых дисках (F1, Review Focus 2).
    (folder / "._a.jpg").write_bytes(b"\x00\x05\x16\x07" + b"\xab" * 4092)
    (folder / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1" + b"\x00" * 64)
    (folder / ".print-stats.json").write_text("{}", encoding="utf-8")
    (folder / "Печать картинок.command").write_text("#!/bin/bash\n", encoding="utf-8")
    (folder / "sub.jpg").mkdir()
    (folder / "sub").mkdir()
    make_jpeg(folder / "sub" / "e.jpg", 576, 100)

    assert {e.name for e in scan(folder)} == {"a.jpg", "B.JPG", "c.jpeg", "d.png"}


def test_scan_lists_every_image_extension(tmp_path: Path) -> None:
    """F1: каждое расширение из `IMAGE_EXTENSIONS` попадает в список с причиной.

    GIF, BMP, WEBP, TIFF и HEIC (снимок iPhone по умолчанию) оператор должен
    видеть в списке с объяснением, почему файл не печатается, а не терять
    молча. Регистр расширения не важен (`j.HEIF`, `l.TIFF`).
    """
    for name, kind in (
        ("f.gif", "GIF"), ("g.bmp", "BMP"), ("h.webp", "WEBP"),
        ("k.tif", "TIFF"), ("l.TIFF", "TIFF"),
    ):
        Image.new("RGB", (576, 100), (255, 255, 255)).save(tmp_path / name, kind)
    # Pillow без модуля HEIF такие файлы не пишет и не читает — достаточно
    # байтов начала контейнера: в список файл попадает по имени (F1).
    (tmp_path / "i.heic").write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16)
    (tmp_path / "j.HEIF").write_bytes(b"\x00\x00\x00\x18ftypheif" + b"\x00" * 16)

    errors = {e.name: e.error for e in scan(tmp_path)}

    assert set(errors) == {"f.gif", "g.bmp", "h.webp", "i.heic", "j.HEIF", "k.tif", "l.TIFF"}
    # Формат, который Pillow открыл, назван по содержимому (F3 п. 3).
    assert errors["f.gif"] == "неверный формат изображения: нужен JPEG, а это GIF"
    assert errors["g.bmp"] == "неверный формат изображения: нужен JPEG, а это BMP"
    assert errors["h.webp"] == "неверный формат изображения: нужен JPEG, а это WEBP"
    assert errors["k.tif"] == "неверный формат изображения: нужен JPEG, а это TIFF"
    assert errors["l.TIFF"] == "неверный формат изображения: нужен JPEG, а это TIFF"
    # Формат, который Pillow не открыл, назван по расширению — без точки, в
    # верхнем регистре (F3 п. 1): `i.heic` — «HEIC», `j.HEIF` — «HEIF».
    assert errors["i.heic"] == "неверный формат изображения: нужен JPEG, а это HEIC"
    assert errors["j.HEIF"] == "неверный формат изображения: нужен JPEG, а это HEIF"


def test_scan_follows_symlink_to_image(tmp_path: Path) -> None:
    """F1: символическая ссылка на JPEG — обычный файл, она в списке.

    Спецификация проверяет `is_file()`, а он идёт по ссылке: `os.stat`, не
    `os.lstat`. Пригодность и `ctime` берутся у самого файла.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    real = make_jpeg(outside / "real.jpg", 576, 100)
    folder = tmp_path / "folder"
    folder.mkdir()
    os.symlink(real, folder / "link.jpg")

    entries = scan(folder)

    assert [(e.name, e.error) for e in entries] == [("link.jpg", None)]
    assert entries[0].ctime == real.stat().st_ctime


def test_scan_skips_fifo(tmp_path: Path) -> None:
    """F1: канал (FIFO) `pipe.jpg` — не обычный файл, `scan` его не открывает и не виснет.

    Правило — «только обычные файлы» (`is_file()`), а не «всё, кроме
    каталогов». Открытие канала без писателя ждёт вечно: проверка F3 на
    `pipe.jpg` повесила бы `scan`, а с ним и список на странице (опрос не
    вернётся), и каждую печать — `find` тоже идёт через `scan`. У заказчика
    перестало бы печататься всё, пока канал лежит в папке. `scan` идёт в
    свежем процессе с тайм-аутом: зависание — провал теста, а не зависший
    прогон.
    """
    make_jpeg(tmp_path / "good.jpg", 576, 100)
    os.mkfifo(tmp_path / "pipe.jpg")
    # Предпосылка теста: `pipe.jpg` — канал, а не обычный файл и не каталог.
    assert stat.S_ISFIFO(os.stat(tmp_path / "pipe.jpg").st_mode)

    # 10 с — с большим запасом: без зависания процесс укладывается меньше
    # чем в секунду даже с импортом Pillow; `subprocess.run` по тайм-ауту
    # убивает процесс сам и бросает `TimeoutExpired` — это и есть провал.
    done = subprocess.run(
        [sys.executable, "-c", _SCAN_NAMES, str(tmp_path)],
        cwd=APP, capture_output=True, encoding="utf-8", timeout=10,
    )

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == ["good.jpg"]


def test_scan_skips_link_to_device(tmp_path: Path) -> None:
    """F1: ссылка `null.jpg` на устройство `/dev/null` — не обычный файл, её нет в списке.

    `is_file()` идёт по ссылке (F1, `test_scan_follows_symlink_to_image`) и
    видит устройство, а не файл. Проверка «не каталог» пропустила бы такую
    ссылку: в списке появилась бы карточка «файл не читается» для того, что
    вовсе не картинка, а чтение устройства в каждом опросе списка могло бы
    не кончиться.
    """
    make_jpeg(tmp_path / "good.jpg", 576, 100)
    os.symlink("/dev/null", tmp_path / "null.jpg")
    # Предпосылка теста: ссылка ведёт на символьное устройство — не обычный
    # файл и не каталог.
    assert stat.S_ISCHR(os.stat(tmp_path / "null.jpg").st_mode)

    assert [(e.name, e.error) for e in scan(tmp_path)] == [("good.jpg", None)]


def test_scan_takes_last_suffix(tmp_path: Path) -> None:
    """F1: расширение — последний суффикс имени (`path.suffix`), а не всё после первой точки.

    Имена с датой и несколькими точками — обычное дело: «Чек 26.09.2026.jpg»,
    «IMG_1234.edited.JPEG». Это пригодные картинки: возьми код расширение
    от первой точки, они молча пропали бы из списка, и напечатать их было бы
    нельзя (`find` тоже идёт через `scan`). А `notes.jpg.txt` — текст: его
    последний суффикс `.txt`, и в списке ему не место.
    """
    make_jpeg(tmp_path / "Чек 26.09.2026.jpg", 576, 100)
    make_jpeg(tmp_path / "IMG_1234.edited.JPEG", 576, 100)
    (tmp_path / "notes.jpg.txt").write_text("заметки", encoding="utf-8")

    assert {(e.name, e.error) for e in scan(tmp_path)} == {
        ("Чек 26.09.2026.jpg", None),
        ("IMG_1234.edited.JPEG", None),
    }
    assert find(tmp_path, "Чек 26.09.2026.jpg") is not None


def test_scan_orders_by_ctime_not_mtime(tmp_path: Path) -> None:
    """F2: сверху файл с самым новым `ctime`, даже если его `mtime` старый.

    Так ведёт себя файл, перенесённый в папку в Finder: `mtime` остаётся
    от старого снимка, а `ctime` — момент переноса. Вторая половина теста
    поднимает наверх b.jpg — файл, который по имени второй: только так
    видно, что порядок задаёт `ctime`, а не имя (§ 7.4 п. 3).
    """
    b = make_jpeg(tmp_path / "b.jpg", 576, 100)
    a = make_jpeg(tmp_path / "a.jpg", 576, 100)
    # 2020-01-01 00:00:00 UTC: `mtime` у a.jpg старше, а смена атрибутов
    # делает его `ctime` самым новым.
    os.utime(a, (1577836800, 1577836800))
    # Предпосылка теста: по `mtime` порядок был бы обратным.
    assert a.stat().st_mtime < b.stat().st_mtime

    assert [e.name for e in scan(tmp_path)] == ["a.jpg", "b.jpg"]

    # Ожидание выше совпадает и с порядком по имени. Теперь смена атрибутов
    # делает самым новым `ctime` у b.jpg, а `mtime` у обоих файлов равны:
    # сортировка по имени или по `mtime` поставила бы первым a.jpg.
    os.utime(b, (1577836800, 1577836800))
    # Предпосылка теста: `ctime` у b.jpg новее, `mtime` одинаковые.
    assert b.stat().st_ctime > a.stat().st_ctime
    assert b.stat().st_mtime == a.stat().st_mtime

    assert [e.name for e in scan(tmp_path)] == ["b.jpg", "a.jpg"]


def test_scan_breaks_ctime_ties_by_name(tmp_path: Path) -> None:
    """F2: при равном `ctime` порядок — по имени по возрастанию.

    Жёсткая ссылка делит с исходным файлом один inode, поэтому `ctime`
    у двух имён совпадает точно.
    """
    b = make_jpeg(tmp_path / "b.jpg", 576, 100)
    a = tmp_path / "a.jpg"
    os.link(b, a)
    # Предпосылка теста: `ctime` действительно равны.
    assert a.stat().st_ctime == b.stat().st_ctime

    assert [e.name for e in scan(tmp_path)] == ["a.jpg", "b.jpg"]


def test_check_accepts_jpeg_576(tmp_path: Path) -> None:
    """F3 п. 3–4: JPEG шириной 576 px пригоден при любом регистре расширения."""
    x = make_jpeg(tmp_path / "x.jpg", 576, 300)
    photo = make_jpeg(tmp_path / "Photo.JPEG", 576, 300)

    assert check(x) is None
    assert check(photo) is None
    # Запись `scan` пригодного файла — без причины.
    assert [e.error for e in scan(tmp_path)] == [None, None]


def test_check_accepts_mpo_576(tmp_path: Path) -> None:
    """F3 п. 3: MPO (JPEG с несколькими снимками, iPhone и камеры) пригоден."""
    p = make_mpo(tmp_path / "m.jpg", 576, 300)
    # Предпосылка теста: Pillow видит в файле именно MPO, а не простой JPEG.
    with Image.open(p) as im:
        assert im.format == "MPO"

    assert check(p) is None


def test_check_accepts_cmyk_and_grey_jpeg(tmp_path: Path) -> None:
    """F3: CMYK- и серый JPEG шириной 576 px пригодны — формат тот же, JPEG."""
    cmyk = make_jpeg(tmp_path / "cmyk.jpg", 576, 50, mode="CMYK")
    grey = make_jpeg(tmp_path / "grey.jpg", 576, 50, mode="L")
    # Предпосылка теста: файлы действительно легли в этих режимах.
    with Image.open(cmyk) as im:
        assert im.mode == "CMYK"
    with Image.open(grey) as im:
        assert im.mode == "L"

    assert check(cmyk) is None
    assert check(grey) is None


def test_check_rejects_png(tmp_path: Path) -> None:
    """F3 п. 3: PNG непригоден, причина называет формат; F1: в списке он есть.

    Оператор видит PNG в списке с полным текстом ошибки и понимает, почему
    файл не печатается.
    """
    logo = make_png(tmp_path / "logo.png", 576, 300)

    assert check(logo) == "нужен JPEG, а это PNG"
    assert [(e.name, e.error) for e in scan(tmp_path)] == [
        ("logo.png", "неверный формат изображения: нужен JPEG, а это PNG"),
    ]


def test_check_detects_png_renamed_to_jpg(tmp_path: Path) -> None:
    """F3 п. 3: формат определяется по содержимому, а не по имени файла."""
    fake = make_png(tmp_path / "fake.jpg", 576, 300)

    assert check(fake) == "нужен JPEG, а это PNG"


def test_png_refused_without_decoding(tmp_path: Path) -> None:
    """F4, F3, F6: PNG отвергается по заголовку — без декодирования, даже ради EXIF.

    У PNG без чанка eXIf Pillow ищет EXIF во всём файле и для этого
    декодирует картинку целиком (`PngImageFile.getexif` → `load()`).
    Поэтому поворот читается только у JPEG и MPO — после проверки формата
    (F3 п. 3 раньше п. 4). Иначе каждый опрос списка раз в 3 с и каждая
    миниатюра декодировали бы все PNG папки: макет 12000×12000 — это
    полгигабайта памяти на каждый опрос (F4). То же
    для печати: `load_printable` повторяет F3 (F6) и отказывает PNG, не
    декодируя его.
    Здесь белый двухцветный PNG 9000×9000: на диске 27 КБ, в памяти
    Pillow — байт на точку, 77 МБ. Пик памяти свежего процесса после
    `scan` и после отказа `load_printable` должен вырасти меньше чем на
    20 МБ.
    """
    # 9000×9000 = 81 млн точек — ниже порога DecompressionBombWarning
    # (89,5 млн), так что подпроцесс открывает файл без предупреждений.
    Image.new("1", (9000, 9000), 1).save(tmp_path / "big.png", "PNG")
    # Предпосылки «полное декодирование замер видит» (≥ 60 МБ) здесь нет
    # намеренно: под нагрузкой на память, особенно в параллельных прогонах,
    # пик `ru_maxrss` у декодирования выходил ниже, и тест ложно падал на
    # подготовке. Порог 20 МБ почти вчетверо ниже 77 МБ декодированной
    # картинки: декодирование в `scan` или в отказе `load_printable` его
    # превысило бы (F4).
    scanned_mb, errors = _grown_mb("scan", tmp_path)
    refused_mb, refusal = _grown_mb("load", tmp_path)

    assert errors == ["неверный формат изображения: нужен JPEG, а это PNG"]
    assert scanned_mb < 20
    assert refusal == ["неверный формат изображения: нужен JPEG, а это PNG"]
    assert refused_mb < 20


def test_check_rejects_wrong_width(tmp_path: Path) -> None:
    """F3 п. 4: ширина должна быть ровно 576 — ни шире, ни на пиксель уже."""
    wide = make_jpeg(tmp_path / "wide.jpg", 800, 300)
    narrow = make_jpeg(tmp_path / "narrow.jpg", 575, 300)

    assert check(wide) == "ширина 800 px, нужна 576"
    assert check(narrow) == "ширина 575 px, нужна 576"


def test_check_uses_exif_orientation(tmp_path: Path) -> None:
    """F3 п. 4: ширина считается так, как снимок видно на экране (EXIF 0x0112).

    Поворот 6 и 8 меняет ширину с высотой местами, поворот 3 (на 180°) —
    нет. Снимок с телефона, хранящийся «боком», пригоден, если на экране
    он шириной 576 (Review Focus 1).
    """
    side = make_jpeg(tmp_path / "side.jpg", 300, 576, orientation=6)
    turned = make_jpeg(tmp_path / "turned.jpg", 576, 300, orientation=8)
    upside = make_jpeg(tmp_path / "upside.jpg", 576, 300, orientation=3)
    # Предпосылка теста: тег поворота действительно записан в файл.
    for path, value in ((side, 6), (turned, 8), (upside, 3)):
        with Image.open(path) as im:
            assert im.getexif().get(0x0112) == value

    assert check(side) is None
    assert check(turned) == "ширина 300 px, нужна 576"
    assert check(upside) is None


def test_mirrored_orientations_5_and_7_swap_width(tmp_path: Path) -> None:
    """F3 п. 4, F6: зеркальные повороты 5 и 7 тоже меняют ширину с высотой.

    Эти значения описывают снимок, отражённый и повёрнутый на 90° или 270°.
    Хранящийся 300×576 пригоден и печатается 576×300, хранящийся 576×300
    на экране шириной 300 — непригоден.
    """
    for value in (5, 7):
        side = make_jpeg(tmp_path / f"side{value}.jpg", 300, 576, orientation=value)
        turned = make_jpeg(tmp_path / f"turned{value}.jpg", 576, 300, orientation=value)
        # Предпосылка теста: тег поворота действительно записан в файл.
        for path in (side, turned):
            with Image.open(path) as im:
                assert im.getexif().get(0x0112) == value

        assert check(side) is None, value
        assert check(turned) == "ширина 300 px, нужна 576", value
        assert load_printable(side).size == (576, 300), value


@pytest.mark.parametrize("orientation", [0, 9])
def test_orientation_outside_1_to_8_means_no_rotation(tmp_path: Path, orientation: int) -> None:
    """F3 п. 4, F6: значение поворота вне 1–8 — не поворот, ширина — хранимая.

    Ширина с высотой меняются только для 5–8. Значения 0 и 9 стандарт EXIF
    не определяет, и ни браузер, ни `exif_transpose` такой снимок не
    поворачивают. Хранящийся 576×300 пригоден и печатается 576×300, а
    хранящийся 300×576 — «ширина 300 px»: иначе (`orientation > 4` вместо
    5–8) проверка отказала бы картинке, которая на экране шириной 576, а
    узкую пропустила бы, и на принтер ушла бы полоса в 300 точек. Значение 0
    ловит и обратную ошибку — «всё, кроме 1–4, — поворот».
    """
    wide = make_jpeg(tmp_path / "wide.jpg", 576, 300, orientation=orientation)
    tall = make_jpeg(tmp_path / "tall.jpg", 300, 576, orientation=orientation)
    # Предпосылка теста: тег поворота действительно записан в файл.
    for path in (wide, tall):
        with Image.open(path) as im:
            assert im.getexif().get(0x0112) == orientation

    assert check(wide) is None
    assert load_printable(wide).size == (576, 300)
    assert check(tall) == "ширина 300 px, нужна 576"
    with pytest.raises(InvalidImage) as info:
        load_printable(tall)
    assert str(info.value) == "неверный формат изображения: ширина 300 px, нужна 576"


def test_broken_exif_means_no_rotation(tmp_path: Path) -> None:
    """F3 п. 4: EXIF, который Pillow не разбирает, — поворота нет, файл пригоден.

    Такой снимок проходит проверку по хранимой ширине, `scan` его не роняет,
    и он печатается как хранится.
    """
    p = tmp_path / "badexif.jpg"
    # `dpi` обязателен: без плотности в заголовке JFIF Pillow ещё в
    # `Image.open` читает EXIF ради неё, глотает ошибку и запоминает пустой
    # EXIF — тогда `getexif()` не бросает, и запасная ветка не проверяется.
    Image.new("RGB", (576, 300), (255, 255, 255)).save(
        p, "JPEG", quality=95, exif=b"Exif\x00\x00garbage!", dpi=(72, 72),
    )
    # Предпосылка теста: чтение EXIF у этого файла действительно бросает.
    with Image.open(p) as im:
        with pytest.raises(SyntaxError):
            im.getexif()

    assert check(p) is None
    assert [e.error for e in scan(tmp_path)] == [None]
    assert load_printable(p).size == (576, 300)


def test_truncated_exif_means_no_rotation(tmp_path: Path) -> None:
    """F3 п. 4: EXIF, оборванный сразу после заголовка TIFF, — поворота нет, файл пригоден.

    Правило говорит «при исключении 1» — при любом. Мусорный EXIF Pillow
    отвергает `SyntaxError` (`test_broken_exif_means_no_rotation`), а
    оборванный — `struct.error`: чтению не хватает байтов смещения IFD0.
    Такой блок оставляют кривые редакторы и обрыв при записи. Не поймай
    `check` это исключение — один такой снимок ронял бы `scan`: страница
    теряла бы весь список, а печать — любую картинку папки (`find` идёт
    через `scan`), пока файл не уберут. Соседний пригодный снимок в тесте —
    чтобы видеть, что список цел целиком.
    """
    p = tmp_path / "shortexif.jpg"
    # `dpi` — по той же причине, что в test_broken_exif_means_no_rotation:
    # без плотности в JFIF Pillow прочитал бы EXIF ещё в `Image.open` и
    # проглотил ошибку сам.
    Image.new("RGB", (576, 300), (255, 255, 255)).save(
        p, "JPEG", quality=95, exif=b"Exif\x00\x00II*\x00", dpi=(72, 72),
    )
    make_jpeg(tmp_path / "good.jpg", 576, 100)
    # Предпосылка теста: чтение EXIF бросает, и это не `SyntaxError`.
    with Image.open(p) as im:
        with pytest.raises(struct.error):
            im.getexif()

    assert check(p) is None
    assert {(e.name, e.error) for e in scan(tmp_path)} == {
        ("shortexif.jpg", None),
        ("good.jpg", None),
    }
    assert load_printable(p).size == (576, 300)


@pytest.mark.parametrize("with_exif", [False, True])
@pytest.mark.parametrize("value", [3, 6])
def test_xmp_orientation_is_not_rotation(tmp_path: Path, value: int, with_exif: bool) -> None:
    """F3 п. 4, F6: поворот — только тег 0x0112 блока EXIF; XMP `tiff:Orientation` — не поворот.

    Браузер (Safari, Chrome) поворачивает снимок только по тегу EXIF и XMP
    не читает, а Pillow, когда в EXIF тега нет, подставляет в `getexif()`
    значение из XMP. Возьми проверка или печать поворот оттуда — экран и
    печать разошлись бы (финальная проверка, 2026-09-28):
    - хранящийся 576×300 — на экране шириной 576 — получил бы ложное
      «ширина 300 px» (поворот 6) или напечатался бы вверх ногами (3);
    - хранящийся 300×576 — на экране узкий — ушёл бы на принтер
      повёрнутым (6).
    Поэтому пригодность и печать — как у неповёрнутой картинки: печать
    совпадает с хранимыми пикселями точка в точку (метка в углу видна при
    любом повороте). `with_exif`: EXIF есть, но без тега поворота и без
    плотности в JFIF — тогда Pillow читает EXIF ещё в `Image.open` и
    запоминает его вместе со значением из XMP, и убрать XMP из `im.info`
    перед проверкой уже поздно.
    """
    options = {"quality": 95, "xmp": _xmp_orientation(value)}
    if with_exif:
        exif = Image.Exif()
        exif[0x010F] = "Maker"   # производитель; тега поворота 0x0112 нет
        options["exif"] = exif.tobytes()
    wide = tmp_path / "wide.jpg"
    tall = tmp_path / "tall.jpg"
    _marked(576, 300).save(wide, "JPEG", **options)
    _marked(300, 576).save(tall, "JPEG", **options)
    # Предпосылки теста: Pillow видит поворот `value`, но он из XMP — в
    # самом блоке EXIF тега поворота нет (или нет и самого блока).
    for path in (wide, tall):
        with Image.open(path) as im:
            assert im.getexif().get(0x0112) == value
            raw = Image.Exif()
            raw.load(im.info.get("exif"))
            assert 0x0112 not in raw
            assert ("exif" in im.info) is with_exif
    with Image.open(wide) as im:
        stored = im.tobytes()

    assert check(wide) is None
    assert [e.error for e in scan(tmp_path) if e.name == "wide.jpg"] == [None]
    printed = load_printable(wide)
    assert printed.size == (576, 300)
    assert printed.tobytes() == stored
    assert check(tall) == "ширина 300 px, нужна 576"
    with pytest.raises(InvalidImage) as info:
        load_printable(tall)
    assert str(info.value) == "неверный формат изображения: ширина 300 px, нужна 576"


def test_check_unreadable_jpg(tmp_path: Path) -> None:
    """F3 п. 2: JPEG-имя, которое Pillow не открывает, — «файл не читается».

    Мусор и пустой файл (например, ещё не начавший копироваться) не
    роняют проверку, а дают причину.
    """
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")

    assert check(broken) == "файл не читается"
    assert check(empty) == "файл не читается"


def test_find_exact_name_only(tmp_path: Path) -> None:
    """F7: `find` отдаёт запись только при точном совпадении имени.

    Другой регистр, `..`, `.`, путь с `/` и имя с `\\x00` не совпадают
    никогда: путь к файлу берётся только из записи `scan`, и выйти за
    пределы папки нельзя.
    """
    folder = tmp_path / "folder"
    folder.mkdir()
    make_jpeg(folder / "кот 1.jpg", 576, 300)
    # Приманка на уровень выше: наивная сборка пути `folder / name` нашла бы
    # её по имени "../кот 1.jpg".
    make_jpeg(tmp_path / "кот 1.jpg", 576, 300)

    entry = find(folder, "кот 1.jpg")
    assert entry is not None
    assert entry.path == folder / "кот 1.jpg"
    for name in ("кот 1.JPG", "..", ".", "../кот 1.jpg", "a\x00b.jpg"):
        assert find(folder, name) is None, name


def test_find_without_normalization(tmp_path: Path) -> None:
    """F7: имя сравнивается как есть — без нормализации Unicode, пробелов и пути.

    APFS хранит имя в той форме, в какой его записали: «й» в форме NFD —
    это «и» плюс отдельный знак краткой. `find` находит файл только по
    имени ровно из `scan`; та же буква в форме NFC или имя с пробелом по
    краям — другое имя. Имя с `/` не совпадает никогда, даже если путь
    ведёт к тому же файлу: `./имя`, `имя/` и `sub/../имя` — не имена из
    списка, и сборка пути `папка / имя` или `os.path.normpath` их бы
    «нашли», а F7 разрешает только точное совпадение.
    """
    name = unicodedata.normalize("NFD", "йогурт.jpg")
    # Предпосылка теста: формы NFD и NFC действительно различаются.
    assert name != unicodedata.normalize("NFC", name)
    make_jpeg(tmp_path / name, 576, 100)

    assert scan(tmp_path)[0].name == name
    assert find(tmp_path, name) is not None
    for other in (
        unicodedata.normalize("NFC", name), " " + name, name + " ",
        "./" + name, name + "/", "sub/../" + name,
    ):
        assert find(tmp_path, other) is None, repr(other)


def test_load_printable_rotates_to_576(tmp_path: Path) -> None:
    """F6: картинка для печати повёрнута по EXIF — как её видно на экране.

    Хранится 300×576 с поворотом 6, печататься должна 576×300
    (Review Focus 1).
    """
    p = make_jpeg(tmp_path / "side.jpg", 300, 576, orientation=6)

    assert load_printable(p).size == (576, 300)


def test_load_printable_survives_exif_transpose_failure(tmp_path: Path) -> None:
    """F6: сбой `exif_transpose` не отказ в печати — файл печатается.

    В EXIF, кроме поворота 3, частный тег типа LONG8 со значением 2⁶⁴−1.
    Pillow его читает, а переписать EXIF после поворота не может и бросает
    `struct.error` — так бывает и у заказчика, без всяких фильтров
    предупреждений. Проверка такой файл пропускает, значит, и печать должна
    пройти.
    """
    p = tmp_path / "long8.jpg"
    exif = _raw_exif([_orientation_entry(3), (0x9999, 16, 1, b"\xff" * 8)])
    Image.new("RGB", (576, 300), (255, 255, 255)).save(p, "JPEG", quality=95, exif=exif)
    # Предпосылка теста: поворот Pillow на этом файле действительно падает.
    with Image.open(p) as im:
        with pytest.raises(struct.error):
            ImageOps.exif_transpose(im)

    assert check(p) is None
    img = load_printable(p)
    assert img.size == (576, 300)
    assert img.getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize("orientation", [2, 3, 4, 5, 6, 7, 8])
def test_load_printable_turns_like_screen_when_exif_transpose_fails(
    tmp_path: Path, orientation: int,
) -> None:
    """F6, F3 п. 4: при сбое `exif_transpose` картинка всё равно повёрнута по EXIF.

    Pillow поворачивает картинку и только потом переписывает EXIF. Если
    переписать не выходит (здесь ResolutionUnit записан как SSHORT −2),
    `exif_transpose` бросает, и повёрнутый результат теряется. Брать
    картинку как хранится нельзя: снимок 300×576 с поворотом 6 проверка
    пропустила по ширине на экране, а на принтер ушла бы узкая боковая
    полоса. Печать должна совпасть по пикселям с тем, что даёт
    `exif_transpose` тому же снимку с исправным EXIF.
    """
    # 5–8 меняют ширину с высотой: хранится 300×576, на экране 576×300.
    size = (300, 576) if orientation in (5, 6, 7, 8) else (576, 300)
    picture = _marked(*size)
    broken = tmp_path / "broken.jpg"
    picture.save(broken, "JPEG", quality=95, exif=_raw_exif([
        _orientation_entry(orientation),
        (0x0128, 8, 1, struct.pack("<h", -2)),   # ResolutionUnit, тип SSHORT
    ]))
    # Двойник: те же пиксели и тот же поворот, но EXIF исправен.
    healthy = tmp_path / "healthy.jpg"
    picture.save(healthy, "JPEG", quality=95, exif=_raw_exif([_orientation_entry(orientation)]))
    # Предпосылки теста: хранимые пиксели файлов совпадают; у сломанного
    # файла поворот Pillow падает, а у двойника проходит и даёт ширину 576.
    with Image.open(broken) as stored_broken, Image.open(healthy) as stored_healthy:
        assert stored_broken.tobytes() == stored_healthy.tobytes()
    with Image.open(broken) as im:
        with pytest.raises(struct.error):
            ImageOps.exif_transpose(im)
    with Image.open(healthy) as im:
        expected = ImageOps.exif_transpose(im)
    assert expected.size == (576, 300)

    assert check(broken) is None
    img = load_printable(broken)
    assert img.size == (576, 300)
    assert img.tobytes() == expected.tobytes()


def test_load_printable_mpo_first_frame(tmp_path: Path) -> None:
    """F6: у MPO печатается первый снимок (белый), а не второй (чёрный)."""
    p = make_mpo(tmp_path / "m.jpg", 576, 300)

    assert load_printable(p).getpixel((0, 0)) == (255, 255, 255)


def test_load_printable_rejects_invalid(tmp_path: Path) -> None:
    """F6: непригодный по F3 файл даёт `InvalidImage` с полным текстом ошибки."""
    wide = make_jpeg(tmp_path / "wide.jpg", 800, 300)
    logo = make_png(tmp_path / "logo.png", 576, 300)

    with pytest.raises(InvalidImage) as wide_info:
        load_printable(wide)
    assert str(wide_info.value) == "неверный формат изображения: ширина 800 px, нужна 576"

    with pytest.raises(InvalidImage) as logo_info:
        load_printable(logo)
    assert str(logo_info.value) == "неверный формат изображения: нужен JPEG, а это PNG"


def test_load_printable_truncated(tmp_path: Path) -> None:
    """F6: обрезанный JPEG (ещё копируется в папку) при печати — «файл не читается».

    Заголовок у файла цел, поэтому сбой виден только при полном
    декодировании `im.load()` (Review Focus 3).
    """
    p = make_jpeg(tmp_path / "long.jpg", 576, 2000, noise=True)
    truncate(p, p.stat().st_size // 2)

    with pytest.raises(InvalidImage) as info:
        load_printable(p)
    assert str(info.value) == "неверный формат изображения: файл не читается"


def test_load_printable_truncated_jpeg_under_other_name(tmp_path: Path) -> None:
    """F6, F3 п. 3, F4: сбой декодирования — «файл не читается» при любом имени, а не причина по расширению.

    JPEG шириной 576 под именем `long.png` (так называют файлы иные
    редакторы и загрузки из браузера) ещё копируется в папку. Pillow
    определяет формат по содержимому, поэтому карточка пригодна (F3 п. 3,
    F4), и печать должна сказать то же, что для `long.jpg`: «файл не
    читается» (F6) — докопируется и напечатается. Причина по расширению
    (п. 1) — только для файла, который `Image.open` не открыл. Назови печать
    сбой декодирования по имени, карточка звала бы файл пригодным, а
    печать — «нужен JPEG, а это PNG», хотя это JPEG: оператор взялся бы
    переделывать исправный снимок вместо того, чтобы дождаться конца
    копирования.
    """
    p = make_jpeg(tmp_path / "long.png", 576, 2000, noise=True)
    truncate(p, p.stat().st_size // 2)
    # Предпосылки теста: Pillow открывает файл как JPEG по содержимому, а
    # полное декодирование падает.
    with Image.open(p) as im:
        assert im.format == "JPEG"
        with pytest.raises(OSError):
            im.load()

    assert check(p) is None
    assert [(e.name, e.error) for e in scan(tmp_path)] == [("long.png", None)]
    with pytest.raises(InvalidImage) as info:
        load_printable(p)
    assert str(info.value) == "неверный формат изображения: файл не читается"


def test_load_printable_unopenable_is_invalid(
    tmp_path: Path, chmod_to: Callable[[Path, int], None],
) -> None:
    """F6, F3: любой сбой `Image.open` при печати — `InvalidImage` «файл не читается».

    Pillow бросает при открытии не только `UnidentifiedImageError`. JPEG,
    который только начал копироваться и оборван посреди заголовка, даёт
    голый `OSError` «Truncated File Read»; файл без права чтения —
    `PermissionError`; файл, убранный из папки, — `FileNotFoundError`.
    Карточка на странице может ещё показывать такой файл пригодным (список
    обновляется раз в 3 с, вторая вкладка — и того реже): печать должна
    дать 422 с причиной, а не голый 500 «Ошибка клиента… Запустите ещё
    раз». Проверка `check` после обновления списка называет ту же причину.
    """
    copying = make_jpeg(tmp_path / "copying.jpg", 576, 300)
    # 100 байт — внутри второй таблицы квантования (DQT): SOI и начало
    # заголовка есть, до размеров картинки (SOF) Pillow не дочитывает.
    truncate(copying, 100)
    locked = make_jpeg(tmp_path / "locked.jpg", 576, 300)
    chmod_to(locked, 0o000)
    gone = make_jpeg(tmp_path / "gone.jpg", 576, 300)
    gone.unlink()
    # Предпосылка теста: `Image.open` бросает `OSError`, но не
    # `UnidentifiedImageError` — ровно те сбои, которые проверка, ловящая
    # одно документированное исключение Pillow, пропустила бы.
    for path in (copying, locked, gone):
        with pytest.raises(OSError) as opened:
            Image.open(path)
        assert not isinstance(opened.value, UnidentifiedImageError), path

    for path in (copying, locked, gone):
        assert check(path) == "файл не читается", path
        with pytest.raises(InvalidImage) as info:
            load_printable(path)
        assert str(info.value) == "неверный формат изображения: файл не читается", path


def test_load_printable_detached_from_file(tmp_path: Path) -> None:
    """F6: возвращённая картинка загружена целиком и не держит файл.

    После удаления файла её размер и пиксели по-прежнему читаются: печать
    не зависит от того, что оператор сделает с файлом дальше.
    """
    p = make_jpeg(tmp_path / "x.jpg", 576, 300)
    img = load_printable(p)
    p.unlink()

    assert img.size == (576, 300)
    assert img.getpixel((0, 0)) == (255, 255, 255)


def test_check_non_jpeg_unreadable_names_extension(tmp_path: Path) -> None:
    """F3 п. 1–2: файл, который Pillow не открыл, назван по расширению; JPEG-имя — «файл не читается».

    Снимок HEIC с iPhone Pillow без модуля HEIF не открывает, битый WEBP —
    тоже. Оператору «нужен JPEG, а это HEIC» говорит, что делать: сохранить
    снимок в JPEG; «файл не читается» не сказало бы ничего. `EXT` — суффикс
    имени без точки в верхнем регистре, то есть последний суффикс
    (`path.suffix`): снимок, который телефон или редактор назвал
    `IMG_1234.edited.heic`, — «HEIC», а не «EDITED». Имя, которое обещает
    JPEG (`.jpg` и `.jpeg` в любом регистре — `broken.JPG`, `half.Jpeg`,
    `Чек 26.09.2026.JPEG`), — «файл не читается»: файл битый или ещё
    копируется; без `.jpeg` в `JPEG_EXTENSIONS` недокопированный
    `photo.jpeg` получил бы нелепое «нужен JPEG, а это JPEG». П. 1 касается
    любого имени не из `JPEG_EXTENSIONS`, а не только форматов, которых
    Pillow не знает: битые PNG и TIFF — тоже «нужен JPEG, а это PNG/TIF».
    `load_printable` повторяет F3 (F6), поэтому отказ печати называет ту же
    причину, что и карточка.
    """
    heic = tmp_path / "photo.heic"
    webp = tmp_path / "x.webp"
    broken = tmp_path / "broken.JPG"
    # F3 п. 2: вторая половина `JPEG_EXTENSIONS` — `.jpeg`, в смешанном
    # регистре, как бывает у файлов с Windows.
    half = tmp_path / "half.Jpeg"
    # F3 п. 1–2: имена с несколькими точками. Суффикс, взятый после первой
    # точки, дал бы «а это EDITED» и «а это 09».
    edited = tmp_path / "IMG_1234.edited.heic"
    receipt = tmp_path / "Чек 26.09.2026.JPEG"
    # F3 п. 1: TIFF Pillow открывать умеет, но этот файл битый.
    tif = tmp_path / "scan.tif"
    for path in (heic, webp, broken, half, edited, receipt, tif):
        path.write_bytes(b"not an image")
    # F3 п. 1: PNG, от которого успела скопироваться только сигнатура.
    cut = tmp_path / "cut.png"
    cut.write_bytes(b"\x89PNG\r\n\x1a\n")
    # Предпосылка теста: Pillow не открывает ни один из файлов, то есть
    # работают п. 1–2, а не п. 3 (формат по содержимому).
    for path in (heic, webp, broken, half, edited, receipt, cut, tif):
        with pytest.raises(UnidentifiedImageError):
            Image.open(path)

    assert check(heic) == "нужен JPEG, а это HEIC"
    assert check(webp) == "нужен JPEG, а это WEBP"
    assert check(broken) == "файл не читается"
    assert check(half) == "файл не читается"
    assert check(edited) == "нужен JPEG, а это HEIC"
    assert check(receipt) == "файл не читается"
    assert check(cut) == "нужен JPEG, а это PNG"
    assert check(tif) == "нужен JPEG, а это TIF"
    assert {(e.name, e.error) for e in scan(tmp_path)} == {
        ("photo.heic", "неверный формат изображения: нужен JPEG, а это HEIC"),
        ("x.webp", "неверный формат изображения: нужен JPEG, а это WEBP"),
        ("broken.JPG", "неверный формат изображения: файл не читается"),
        ("half.Jpeg", "неверный формат изображения: файл не читается"),
        ("IMG_1234.edited.heic", "неверный формат изображения: нужен JPEG, а это HEIC"),
        ("Чек 26.09.2026.JPEG", "неверный формат изображения: файл не читается"),
        ("cut.png", "неверный формат изображения: нужен JPEG, а это PNG"),
        ("scan.tif", "неверный формат изображения: нужен JPEG, а это TIF"),
    }
    for path, text in (
        (heic, "неверный формат изображения: нужен JPEG, а это HEIC"),
        (webp, "неверный формат изображения: нужен JPEG, а это WEBP"),
        (broken, "неверный формат изображения: файл не читается"),
        (half, "неверный формат изображения: файл не читается"),
        (edited, "неверный формат изображения: нужен JPEG, а это HEIC"),
        (receipt, "неверный формат изображения: файл не читается"),
        (cut, "неверный формат изображения: нужен JPEG, а это PNG"),
        (tif, "неверный формат изображения: нужен JPEG, а это TIF"),
    ):
        with pytest.raises(InvalidImage) as info:
            load_printable(path)
        assert str(info.value) == text, path


def test_any_open_failure_of_non_jpeg_name_names_extension(
    tmp_path: Path, chmod_to: Callable[[Path, int], None],
) -> None:
    """F3 п. 1, F8, F6: любой сбой `Image.open` у имени не из `JPEG_EXTENSIONS` — «нужен JPEG, а это EXT».

    П. 1 говорит «любое исключение», а не только `UnidentifiedImageError`:
    снимок iPhone `locked.heic` без права чтения (`PermissionError`),
    `gone.png`, убранный из папки после опроса списка (`FileNotFoundError`),
    и огромный PNG `huge.png` 20 000×10 000 — скан или плакат выше предела
    Pillow (`DecompressionBombError`; F8: «причина F3 п. 1–2», «файл не
    читается» — только у `.jpg`). Назови проверка такие файлы «файл не
    читается», оператор перекачивал бы исправный файл, а печатать его всё
    равно нельзя — нужен JPEG. `load_printable` повторяет F3 (F6), поэтому
    отказ печати называет ту же причину, что и карточка.
    """
    locked = tmp_path / "locked.heic"
    locked.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16)
    chmod_to(locked, 0o000)
    gone = make_png(tmp_path / "gone.png", 576, 300)
    gone.unlink()
    huge = _png_header(tmp_path / "huge.png", 20000, 10000)
    # Предпосылка теста: `Image.open` падает у каждого, но не
    # `UnidentifiedImageError` — ровно те сбои, которые проверка, называющая
    # по расширению только нераспознанный файл, отдала бы п. 2. У огромного
    # PNG — именно предел Pillow, а не битый заголовок.
    for path, kind in (
        (locked, PermissionError),
        (gone, FileNotFoundError),
        (huge, Image.DecompressionBombError),
    ):
        with pytest.raises(kind) as opened:
            Image.open(path)
        assert not isinstance(opened.value, UnidentifiedImageError), path

    for path, reason in (
        (locked, "нужен JPEG, а это HEIC"),
        (gone, "нужен JPEG, а это PNG"),
        (huge, "нужен JPEG, а это PNG"),
    ):
        assert check(path) == reason, path
        with pytest.raises(InvalidImage) as info:
            load_printable(path)
        assert str(info.value) == f"неверный формат изображения: {reason}", path
    assert {(e.name, e.error) for e in scan(tmp_path)} == {
        ("locked.heic", "неверный формат изображения: нужен JPEG, а это HEIC"),
        ("huge.png", "неверный формат изображения: нужен JPEG, а это PNG"),
    }


def test_scan_does_not_decode(tmp_path: Path) -> None:
    """F4: `scan` читает только заголовок — обрезанный JPEG в списке пригоден, отказывает печать.

    Так выглядит снимок, который ещё копируется в папку (Review Focus 3):
    заголовок цел, данных сканов не хватает. `scan` и `check` не зовут
    `load()`, поэтому карточка пригодна, а обрыв виден только при полном
    декодировании в `load_printable` (F6) — там он «файл не читается».
    Декодируй `scan` пиксели, каждый опрос списка раз в 3 с распаковывал бы
    все снимки папки.
    """
    p = make_jpeg(tmp_path / "long.jpg", 576, 2000, noise=True)
    truncate(p, p.stat().st_size // 2)
    # Предпосылка теста: полное декодирование этого файла действительно
    # падает — иначе пригодность в `scan` ничего бы не доказывала.
    with Image.open(p) as im:
        with pytest.raises(OSError):
            im.load()

    assert [(e.name, e.error) for e in scan(tmp_path)] == [("long.jpg", None)]
    with pytest.raises(InvalidImage) as info:
        load_printable(p)
    assert str(info.value) == "неверный формат изображения: файл не читается"


def test_scan_skips_vanished_file(tmp_path: Path) -> None:
    """F5: имя, у которого `stat` бросает `OSError`, пропускается, а `scan` не падает.

    Висячая ссылка `gone.jpg → missing.jpg` ведёт себя как файл, который
    исчез между `os.listdir` и `stat` (оператор убрал его посреди опроса):
    `stat` бросает `FileNotFoundError`. Ссылка сама на себя `loop.jpg` —
    петля: `OSError` с `ELOOP`, и это не `FileNotFoundError`. Пропускается
    любой `OSError` (F5 называет оба случая). Упади `scan` — страница
    потеряла бы весь список, а печать — любую картинку папки: `find` идёт
    через `scan`. Пропускается только сама запись, обход папки идёт
    дальше: `night.jpg` `os.listdir` отдаёт после обеих ссылок, и оборви
    `scan` цикл на первом сбое `stat`, исчезновение одного файла посреди
    опроса спрятало бы все картинки после него, а их печать получила бы
    «файла нет в папке».
    """
    make_jpeg(tmp_path / "good.jpg", 576, 100)
    os.symlink("missing.jpg", tmp_path / "gone.jpg")
    os.symlink("loop.jpg", tmp_path / "loop.jpg")
    make_jpeg(tmp_path / "night.jpg", 576, 100)
    # Предпосылки теста: у висячей ссылки `stat` бросает
    # `FileNotFoundError`, у петли — `OSError` с другим классом и `ELOOP`.
    with pytest.raises(FileNotFoundError):
        os.stat(tmp_path / "gone.jpg")
    with pytest.raises(OSError) as looped:
        os.stat(tmp_path / "loop.jpg")
    assert looped.value.errno == errno.ELOOP
    assert not isinstance(looped.value, FileNotFoundError)
    # Предпосылка теста: `os.listdir` отдаёт `night.jpg` после обеих ссылок.
    # На APFS порядок — по хешу имени и от порядка создания не зависит,
    # поэтому имя подобрано: `good.jpg` там всегда первым, а `night.jpg`
    # последним. По алфавиту (HFS+) `night.jpg` тоже после `gone.jpg` и
    # `loop.jpg`.
    order = os.listdir(tmp_path)
    assert order.index("night.jpg") > max(order.index("gone.jpg"), order.index("loop.jpg")), order

    assert sorted(e.name for e in scan(tmp_path)) == ["good.jpg", "night.jpg"]
    assert find(tmp_path, "good.jpg") is not None
    assert find(tmp_path, "night.jpg") is not None
    assert find(tmp_path, "gone.jpg") is None
    assert find(tmp_path, "loop.jpg") is None


def test_scan_skips_file_behind_closed_directory(
    tmp_path: Path, chmod_to: Callable[[Path, int], None],
) -> None:
    """F5: `stat` с `PermissionError` (нет права поиска в каталоге) — файл пропускается, это не F9.

    Ссылка `link.jpg` ведёт в каталог без прав: `stat` по ней бросает
    `PermissionError`. F5 называет этот случай прямо: файл пропускается,
    `scan` не падает. F9 — только отказ при чтении самой папки
    (`os.listdir`). Подними `scan` здесь `PermissionError`, одна ссылка
    заменила бы весь список текстом W7 «Нет доступа к папке…», хотя доступ
    к папке есть, а печать любой картинки упала бы — `find` идёт через
    `scan`. Так же упала бы и проверка `Path.is_file()` дословно по F1: в
    Python 3.9 она глотает только ENOENT, ENOTDIR, EBADF и ELOOP, а
    `EACCES` поднимает. Каталог, который читается, но без права поиска
    (`r` без `x`, только ручной `chmod`), даёт пустой список, а не F9 (F5).
    """
    folder = tmp_path / "folder"
    folder.mkdir()
    make_jpeg(folder / "good.jpg", 576, 100)
    closed = tmp_path / "closed"
    closed.mkdir()
    make_jpeg(closed / "real.jpg", 576, 100)
    os.symlink(closed / "real.jpg", folder / "link.jpg")
    chmod_to(closed, 0o000)
    # Предпосылка теста: `stat` по ссылке бросает именно `PermissionError`.
    with pytest.raises(PermissionError):
        os.stat(folder / "link.jpg")

    assert [e.name for e in scan(folder)] == ["good.jpg"]
    assert find(folder, "good.jpg") is not None
    assert find(folder, "link.jpg") is None

    unsearchable = tmp_path / "unsearchable"
    unsearchable.mkdir()
    make_jpeg(unsearchable / "good.jpg", 576, 100)
    chmod_to(unsearchable, 0o400)
    # Предпосылка теста: имена читаются, а `stat` по ним — нет.
    assert os.listdir(unsearchable) == ["good.jpg"]
    with pytest.raises(PermissionError):
        os.stat(unsearchable / "good.jpg")

    assert scan(unsearchable) == []
    assert find(unsearchable, "good.jpg") is None


def test_height_limits(tmp_path: Path) -> None:
    """F8: своего предела высоты нет, отказывает только предел Pillow — «файл не читается».

    Высоту JPEG ограничивает сам формат: в заголовке кадра (SOF) на ширину
    и на высоту по два байта, не больше 65 535. Ширина 576 при такой высоте
    — 37,7 млн точек, ниже порога предупреждения Pillow (89 478 485), так
    что `check` пропускает JPEG шириной 576 с любой высотой в заголовке.
    Настоящий кадр libjpeg пишет и декодирует не выше 65 500 строк (≈8,2 м
    ленты): такой печатается (`test_tallest_real_jpeg_prints`), а у этих
    файлов пиксели от картинки 576×8, и печать их отвергла бы, как
    недокопированный снимок (F4, F6). Пределы Pillow достижимы только у
    файлов другой ширины.
    На высоте 65 535 ширина 2730 — 178 910 550 точек, файл ещё открывается,
    и причина — ширина (F3 п. 4). Ширина 2731 — 178 976 085 точек, за
    порогом ошибки 178 956 970: `DecompressionBombError` → «файл не
    читается» (F3 п. 2), и при печати — `InvalidImage` с тем же текстом.
    Этот класс не наследует `OSError`: перехвати `check` или
    `load_printable` только `OSError`, опрос списка упал бы целиком, а
    печать такого файла дала бы 500 вместо 422 с причиной (F6, W4 п. 2).
    """
    tallest = make_jpeg_header(tmp_path / "tallest.jpg", 576, 65535)
    opens = make_jpeg_header(tmp_path / "opens.jpg", 2730, 65535)
    bomb = make_jpeg_header(tmp_path / "bomb.jpg", 2731, 65535)
    # Предпосылки теста: Pillow видит размеры из заголовков; 2730×65535 он
    # открывает (предупреждение выключено в `pytest.ini`), а 2731×65535
    # отвергает именно пределом, и это не `OSError`.
    with Image.open(tallest) as im:
        assert im.size == (576, 65535)
    with Image.open(opens) as im:
        assert im.size == (2730, 65535)
    with pytest.raises(Image.DecompressionBombError) as refused:
        Image.open(bomb)
    assert not isinstance(refused.value, OSError)

    assert check(tallest) is None
    assert check(opens) == "ширина 2730 px, нужна 576"
    assert check(bomb) == "файл не читается"
    with pytest.raises(InvalidImage) as info:
        load_printable(bomb)
    assert str(info.value) == "неверный формат изображения: файл не читается"


def test_tallest_real_jpeg_prints(tmp_path: Path) -> None:
    """F8, F6: самый высокий настоящий JPEG шириной 576 и пригоден, и печатается — своего предела нет и у печати.

    `test_height_limits` проверяет высоту только в `check`: пиксели его
    файлов — от картинки 576×8, декодировать их нельзя. Здесь пиксели
    настоящие, и высота — наибольшая, какая у JPEG бывает: libjpeg не пишет
    и не декодирует кадр выше 65 500 строк («Maximum supported image
    dimension is 65500 pixels»), это ≈8,2 м ленты. Свой предел высоты на
    пути печати (`load_printable`) — любой, ниже этого — сделал бы карточку
    пригодной, а печать — отказом, чего F8 («высота не ограничена») не
    допускает. Белая картинка в оттенках серого весит на диске 0,4 МБ, в
    памяти — 38 МБ.
    """
    p = tmp_path / "tall.jpg"
    Image.new("L", (576, 65500), 255).save(p, "JPEG", quality=95)
    # Предпосылка теста: файл действительно настоящий JPEG 576×65500.
    with Image.open(p) as im:
        assert (im.format, im.size) == ("JPEG", (576, 65500))

    assert check(p) is None
    assert [e.error for e in scan(tmp_path)] == [None]
    printed = load_printable(p)
    assert printed.size == (576, 65500)
    # Картинка декодирована до последней строки, а не только до заголовка.
    assert printed.getpixel((0, 65499)) == 255


def test_bomb_warning_silenced_in_production(tmp_path: Path) -> None:
    """F8: `DecompressionBombWarning` выключает сам модуль при импорте, а не только `pytest.ini`.

    Подпроцесс запущен с `-W error` — строже, чем у заказчика: там
    предупреждение лишь печаталось бы в Терминал среди строк сервера, а
    здесь без фильтра модуля оно стало бы исключением `Image.open`, и
    `check` сказал бы «файл не читается» вместо настоящей причины. Файл —
    самый узкий JPEG на предельной высоте 65 535, который уже в диапазоне
    предупреждения: 1366×65535 = 89 520 810 точек > 89 478 485. JPEG
    шириной 576 сюда не годится — он до порога не дотягивает
    (`test_height_limits`), поэтому пригодного файла с предупреждением не
    бывает, и ожидаемая причина — ширина (F3 п. 4). «Выключено» значит и
    «не напечатано»: stderr процесса пуст.
    Фильтр — при импорте модуля (F8), а не рядом с одним `Image.open`:
    `load_printable` открывает файл сам (F6), и он тоже должен назвать
    ширину. Выключи предупреждение только внутри `check` — карточка
    молчала бы, а каждая попытка печати (устаревшая карточка, `POST
    /api/print`) выводила бы предупреждение в Терминал заказчика, а при
    `-W error` отказ печати сменился бы на «файл не читается», хотя
    карточка называет ширину.
    """
    p = make_jpeg_header(tmp_path / "wide.jpg", 1366, 65535)

    # Процесс запускается из `app/`, как `_grown_mb`: он только читает файл
    # в `tmp_path` и ничего не пишет.
    done = subprocess.run(
        [sys.executable, "-W", "error", "-c", _BOMB_PROBE, str(p)],
        cwd=APP, capture_output=True, encoding="utf-8",
    )

    assert done.returncode == 0, done.stderr
    # F8 «выключено»: фильтр модуля с действием `default`, `once` или
    # `always` тоже перекрыл бы `-W error` и исключения не было бы, но
    # предупреждение печаталось бы в stderr — у заказчика это Терминал, а с
    # `always` строка появлялась бы на каждом опросе списка раз в 3 с.
    # Только `ignore` оставляет stderr пустым.
    assert done.stderr == ""
    result = json.loads(done.stdout)
    # Предпосылка теста: в этом процессе предупреждение на этом файле
    # действительно становится исключением.
    assert result["raised"] is True
    assert result["reason"] == "ширина 1366 px, нужна 576"
    # F8, F6: фильтр модуля действует и на печать — `load_printable`
    # открывает файл сам и называет ту же причину, что карточка.
    assert result["printed"] == "неверный формат изображения: ширина 1366 px, нужна 576"


def test_scan_permission_error_propagates(
    tmp_path: Path, chmod_to: Callable[[Path, int], None],
) -> None:
    """F9: `PermissionError` при чтении каталога `scan` не глотает — текст даёт API (W7).

    Так бывает, когда macOS не дала Терминалу доступ к папке. Пустой список
    вместо ошибки спрятал бы причину: оператор видел бы пустую папку и не
    знал бы, что нужно дать доступ. `find` идёт через `scan`, поэтому
    печать получает ту же ошибку, а не «файла нет в папке».
    """
    folder = tmp_path / "folder"
    folder.mkdir()
    make_jpeg(folder / "good.jpg", 576, 100)
    chmod_to(folder, 0o000)

    with pytest.raises(PermissionError):
        scan(folder)
    with pytest.raises(PermissionError):
        find(folder, "good.jpg")


def test_pillow_exif_warning_is_not_refusal(tmp_path: Path) -> None:
    """F3 п. 4, F6: JPEG, на EXIF которого Pillow предупреждает, пригоден и печатается — и под pytest.

    В EXIF снимка бывает тег, чьё значение лежит за концом блока (кривой
    редактор, оборванная запись). Pillow пропускает такой тег с
    `UserWarning` «Truncated File Read» — уже в `Image.open`, когда ищет в
    EXIF плотность. У заказчика это строка в Терминале, а снимок пригоден.
    `filterwarnings = error` в `pytest.ini` превращал бы предупреждение в
    исключение `Image.open`, и тесты видели бы «файл не читается» там, где
    заказчик печатает; поэтому предупреждения Pillow в `pytest.ini`
    выключены — тесты видят то же, что заказчик (§ 7.2). Поворот 6 записан
    раньше битого тега и прочитан: хранимые 300×576 печатаются 576×300.
    """
    p = tmp_path / "camera.jpg"
    # Software (0x0131, ASCII) на 64 байта: значение длиннее 4 байт, поэтому
    # 4 байта записи — это смещение значения, и оно (4096) за концом блока.
    # Теги идут по возрастанию, так что поворот (0x0112) прочитан раньше.
    exif = _raw_exif([_orientation_entry(6), (0x0131, 2, 64, struct.pack("<I", 4096))])
    Image.new("RGB", (300, 576), (255, 255, 255)).save(p, "JPEG", quality=95, exif=exif)
    # Предпосылки теста: Pillow предупреждает уже при открытии, но открывает
    # файл и читает поворот.
    with pytest.warns(UserWarning, match="Truncated File Read"):
        with Image.open(p) as im:
            assert im.getexif().get(0x0112) == 6

    assert check(p) is None
    assert [e.error for e in scan(tmp_path)] == [None]
    assert load_printable(p).size == (576, 300)
