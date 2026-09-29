"""Папка с картинками: список, проверка и чтение JPEG для печати.

Заказчик кладёт в папку снимки, а этот модуль решает, что из них показать
на странице и что можно отдать принтеру (спецификация § 4.1). Печатающая
головка XP-160LL — 576 точек, поэтому пригоден только JPEG ровно такой
ширины, какой его видно на экране.

Правила спецификации, которые держит модуль:
- F1 — в список попадают только обычные файлы-картинки прямо в папке;
- F2 — сверху самый свежий по `ctime`, при равенстве — по имени;
- F3 — причина отказа, дословный русский текст: файл, который Pillow не
  открыл, назван по расширению (п. 1 — «нужен JPEG, а это HEIC», п. 2 —
  «файл не читается» у имени `.jpg`/`.jpeg`); формат по содержимому
  (JPEG или MPO, п. 3) и ширина 576 с учётом поворота — тега из блока
  EXIF, а не из XMP (п. 4);
- F4 — список читает только заголовки и не декодирует пиксели: поворот —
  из сырого блока EXIF в заголовке JPEG и MPO, без `getexif()`, который у
  PNG ради EXIF декодирует картинку;
- F5 — файл, у которого `stat` падает (исчез, петля ссылок), пропускается;
- F6 — картинка для печати загружена целиком, повёрнута по тому же тегу,
  что проверен в F3 п. 4, и не держит файл;
- F7 — запись ищется только по точному имени из списка;
- F8 — своего предела высоты нет, предупреждение Pillow о «бомбе»
  выключено при импорте, а его отказ — обычная причина F3;
- F9 — нет доступа к папке — `PermissionError` уходит наверх (текст — W7).
"""
from __future__ import annotations

import os
import stat
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PIL import Image

HEAD_DOTS = 576   # ширина печатающей головки XP-160LL в точках
# F1: какие файлы вообще считаются картинками. PNG, HEIC и прочие попадают
# в список, но с причиной F3 — оператор видит, почему файл не печатается.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif", ".tif", ".tiff")
JPEG_EXTENSIONS = (".jpg", ".jpeg")   # имена, которые обещают JPEG (F3 п. 1–2)

# Тексты для людей — дословно из § 4.1. Полная ошибка — f"{INVALID}: {причина}".
INVALID = "неверный формат изображения"
REASON_NEED_JPEG = "нужен JPEG, а это {kind}"
REASON_UNREADABLE = "файл не читается"
REASON_WIDTH = "ширина {width} px, нужна 576"

# F8: предупреждение Pillow о «бомбе распаковки» выключено при импорте, а не
# только в `pytest.ini`: у заказчика оно печаталось бы в Терминал среди
# строк сервера, а при `-W error` стало бы исключением `Image.open`, и
# настоящая причина отказа сменилась бы на «файл не читается». Своего
# предела высоты у модуля нет — ни в `check`, ни в `load_printable`. JPEG
# шириной 576 до порога предупреждения (89 478 485 точек) не дотягивает
# вовсе: в заголовке кадра на высоту два байта, это до 65 535 строк —
# 37,7 млн точек, а настоящий кадр libjpeg пишет и декодирует не выше
# 65 500 строк, ≈8,2 м ленты. Порог достижим только у файлов другой
# ширины, и `check` называет им ширину (F3 п. 4). Выше второго порога
# (178 956 970 точек) Pillow бросает `DecompressionBombError` — он ловится
# как любой сбой `Image.open` (F3 п. 1–2).
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)

# F6: значение тега поворота из блока EXIF → как повернуть картинку, чтобы
# она стала такой, как на экране. Таблица та же, что внутри
# `ImageOps.exif_transpose` (Pillow 11.3), но сам `exif_transpose` не годится:
# он берёт поворот из `getexif()`, то есть и из XMP, которого браузер не
# читает (F3 п. 4), а после поворота переписывает EXIF и на теге «не того»
# типа падает. 1 и прочие значения — без поворота.
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


@dataclass(frozen=True)
class ImageEntry:
    """Одна картинка из папки — строка списка на странице.

    Запись неизменяемая: её отдают наружу и по ней же находят файл для
    печати (F7), поэтому путь в ней менять нельзя.
    """

    name: str             # имя ровно как вернул os.listdir
    path: Path
    ctime: float          # os.stat().st_ctime
    error: Optional[str]  # None — пригодна; иначе f"{INVALID}: {причина}"


class InvalidImage(ValueError):
    """Файл нельзя напечатать; `str(exc)` — полный текст ошибки для людей."""


def _error(reason: str) -> str:
    """Собрать полный текст ошибки из причины F3: «неверный формат…: причина»."""
    return f"{INVALID}: {reason}"


def _unopened_reason(path: Path) -> str:
    """Вернуть причину F3 п. 1–2 для файла, который `Image.open` не открыл.

    Причину выбирает имя файла, потому что содержимое прочитать не удалось.
    """
    # F3 п. 1: имя не обещает JPEG — называем формат по расширению, без
    # точки и в верхнем регистре. Снимок HEIC с iPhone Pillow не открывает,
    # и «нужен JPEG, а это HEIC» говорит оператору, что делать: сохранить
    # снимок в JPEG. F3 п. 2: имя обещает JPEG (`.jpg`, `.jpeg` в любом
    # регистре) — файл битый или ещё копируется, «файл не читается».
    suffix = path.suffix
    if suffix.lower() in JPEG_EXTENSIONS:
        return REASON_UNREADABLE
    return REASON_NEED_JPEG.format(kind=suffix[1:].upper())


def _orientation(im: Image.Image) -> int:
    """Вернуть тег поворота 0x0112 из блока EXIF открытого JPEG или MPO; нет тега — 1.

    Читает только сырой блок EXIF, который Pillow взял из заголовка
    (`im.info["exif"]`), — пиксели не декодируются (F4). Значение
    разбирается заново из тех же байтов при каждом вызове, поэтому
    проверка и поворот для печати видят одно и то же.
    """
    # F3 п. 4: браузер показывает снимок повёрнутым по тегу EXIF, и печать
    # должна совпадать с экраном. Только сам блок EXIF, не `im.getexif()`:
    # когда в EXIF нет тега, Pillow подставляет туда `tiff:Orientation` из
    # XMP, а браузер XMP не читает — снимок, на экране шириной 576, получил
    # бы ложное «ширина 300 px», а узкий ушёл бы на принтер повёрнутым
    # (финальная проверка, 2026-09-28). Свой `Image.Exif()`, а не удаление
    # XMP из `im.info`: без плотности в JFIF Pillow читает EXIF ещё в
    # `Image.open` и запоминает его вместе со значением из XMP. Битый EXIF
    # не повод отказывать: считаем, что поворота нет.
    try:
        exif = Image.Exif()
        exif.load(im.info.get("exif"))
        return exif.get(0x0112, 1)
    except Exception:
        return 1


def _reason(im: Image.Image) -> Optional[str]:
    """Проверить уже открытый файл по F3 п. 3–4 и вернуть причину или None.

    Читает только то, что Pillow взял из заголовка: формат, размер и — у
    JPEG и MPO — EXIF. Пиксели не декодируются (F4).
    """
    # F3 п. 3: Pillow определяет формат по содержимому, а не по имени, так
    # что переименованный PNG здесь честно называется PNG. MPO — это JPEG с
    # несколькими снимками внутри, так сохраняют iPhone и камеры.
    if im.format not in ("JPEG", "MPO"):
        return REASON_NEED_JPEG.format(kind=im.format)
    # F3: п. 4 проверяется только после п. 3, у JPEG и MPO. F4: поворот —
    # из сырого блока EXIF (`_orientation`), а не `getexif()`: у PNG без
    # чанка eXIf Pillow ради EXIF декодирует всю картинку
    # (`PngImageFile.getexif` → `load()`), и каждый опрос списка
    # декодировал бы все PNG папки, а F4 разрешает списку читать только
    # заголовки.
    orientation = _orientation(im)
    # F3 п. 4: значения 5–8 — повороты на 90° и 270° (с отражением и без):
    # на экране ширина — это хранимая высота.
    width = im.height if orientation in (5, 6, 7, 8) else im.width
    if width != HEAD_DOTS:
        return REASON_WIDTH.format(width=width)
    return None


def check(path: Path) -> Optional[str]:
    """Проверить файл по F3 и вернуть причину без префикса или None.

    Файл открывается лениво — читается только заголовок, поэтому проверка
    быстрая и годится для каждого опроса списка (F4: `load()` здесь нет).
    """
    # F3 п. 1–2: ловим любое `Exception` — Pillow бросает
    # UnidentifiedImageError, OSError, SyntaxError, DecompressionBombError
    # (F8), и общего класса у них нет. `try` охватывает только `Image.open`,
    # а не весь `with`: сбой открытия — причины п. 1–2, проверки п. 3–4 —
    # другие.
    try:
        im = Image.open(path)
    except Exception:
        return _unopened_reason(path)
    with im:
        return _reason(im)


def scan(folder: Path) -> List[ImageEntry]:
    """Вернуть картинки папки (F1) в порядке показа (F2), каждую с причиной F3.

    Пиксели не декодируются (F4). Нет доступа к самой папке —
    `PermissionError` из `os.listdir` уходит наверх (F9).
    """
    entries: List[ImageEntry] = []
    # F9: `os.listdir` не оборачиваем. Так бывает, когда macOS не дала
    # Терминалу доступ к папке; пустой список спрятал бы причину, а текст
    # для людей из этого исключения делает API (W7).
    for name in os.listdir(folder):
        # F1: скрытые файлы не показываем — среди них двойники `._photo.jpg`
        # с флешек и сетевых дисков, `.DS_Store` и файл счётчиков.
        if name.startswith("."):
            continue
        # F1: расширение без учёта регистра — `Photo.JPG` тоже картинка.
        if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
            continue
        path = folder / name
        # F5: файл убрали между `os.listdir` и `stat` или это петля
        # символических ссылок — пропускаем. Ловим любой `OSError`, а не
        # только `FileNotFoundError`: петля — `ELOOP`, и упади на ней `scan`,
        # страница потеряла бы весь список, а печать — любую картинку папки
        # (`find` идёт через `scan`).
        try:
            st = os.stat(path)
        except OSError:
            continue
        # F1: только обычные файлы — подпапка `sub.jpg/` картинкой не станет.
        if not stat.S_ISREG(st.st_mode):
            continue
        reason = check(path)
        entries.append(ImageEntry(
            name=name,
            path=path,
            ctime=st.st_ctime,
            error=None if reason is None else _error(reason),
        ))
    # F2: `ctime`, а не `mtime` — при переносе в Finder `mtime` остаётся
    # старым, а `ctime` становится текущим, и положенный файл оказывается
    # сверху. Имя при равенстве — чтобы порядок не прыгал между опросами.
    return sorted(entries, key=lambda e: (-e.ctime, e.name))


def find(folder: Path, name: str) -> Optional[ImageEntry]:
    """Найти запись `scan` с именем ровно `name` или вернуть None (F7).

    Путь к файлу берётся только из записи: `..`, `.`, имена с `/` и `\\x00`
    не совпадут ни с одним именем из `os.listdir`, и выйти за пределы папки
    нельзя. Регистр не выравнивается — на диске без учёта регистра иначе
    нашёлся бы файл, которого нет в списке под этим именем.
    """
    return next((e for e in scan(folder) if e.name == name), None)


def load_printable(path: Path) -> Image.Image:
    """Прочитать файл для печати: проверить, декодировать, повернуть по EXIF (F6).

    Возвращает загруженную картинку шириной 576, уже не связанную с файлом:
    файл закрыт, и что бы оператор ни сделал с ним дальше, печать не
    пострадает. Непригодный файл — `InvalidImage` с полным текстом ошибки.
    """
    # F6: те же проверки, что в `check` (F3), только причина уходит в
    # исключение — и для сбоя открытия та же причина п. 1–2, что на
    # карточке. `try` охватывает только `Image.open`: иначе `InvalidImage`
    # с причиной п. 3–4 превратился бы в сбой открытия.
    try:
        im = Image.open(path)
    except Exception as exc:
        raise InvalidImage(_error(_unopened_reason(path))) from exc
    with im:
        reason = _reason(im)
        if reason is not None:
            raise InvalidImage(_error(reason))
        # F6: полное декодирование — у MPO это первый снимок. Обрезанный
        # файл, который ещё копируется в папку, падает только здесь: у него
        # цел заголовок. ImageFile.LOAD_TRUNCATED_IMAGES не трогаем — иначе
        # Pillow молча дорисовал бы недостающее и напечатался бы недокопированный
        # снимок.
        try:
            im.load()
        except Exception as exc:
            raise InvalidImage(_error(REASON_UNREADABLE)) from exc
        # F6: поворот по тому же тегу из блока EXIF, что проверил `_reason`
        # (F3 п. 4), — как на экране. Не `ImageOps.exif_transpose`: он берёт
        # поворот из `getexif()`, куда Pillow подставляет XMP (финальная
        # проверка, 2026-09-28), и после поворота переписывает EXIF — на
        # теге «не того» типа это падает, а картинка «как хранится» ушла бы
        # на принтер узкой боковой полосой (снимок 300×576 с поворотом 6).
        # Значение читается заново из тех же байтов `im.info["exif"]`:
        # `load()` их не меняет, у MPO кадр остаётся первым. `transpose` и
        # `copy` возвращают новую картинку, поэтому результат не держит файл
        # после выхода из `with`.
        method = _TRANSPOSE.get(_orientation(im))
        return im.copy() if method is None else im.transpose(method)
