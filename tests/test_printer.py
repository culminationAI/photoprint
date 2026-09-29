"""Тесты модуля «Принтер» — `photoprint.printer` (спецификация § 4.3).

Закрепляют правила пули 1: P1 (`to_printable` сначала переводит картинку в
серый, потом дизерит один раз на всю высоту, и эти точки уходят в принтер
бит в бит), P2 (бэкенд libusb грузится один раз, в `__init__`; нет файла —
`None` без обращения к pyusb), P3 (порядок команд печати, растр с
умолчаниями escpos, после растра — ровно подача `text("\\n\\n")` и отрез,
новый объект escpos на каждую печать — и у подменённого `_open`, и у
настоящего, пауза 0,3 с перед отрезом, отрез обязателен, `close()` зовётся
и после сбоя, а его ошибка печать не отменяет) и P6 (`connected` через
`_find`, `_find` — ровно
`usb.core.find(idVendor=VID, idProduct=PID, backend=self.backend)`).

Правила пули 2 (задача 2.3): P4 (`CheckedUsb._raw` сравнивает число
принятых байт с длиной сообщения и пишет с тайм-аутом 10 000 мс; меньше —
`ShortWriteError`) и P5 (любое исключение на шагах P3 п. 2–5, включая само
`_open` и `to_printable`, становится `PrinterError`: `DeviceNotFoundError`
— `PRINTER_NOT_FOUND`, всё остальное — `PRINTER_FAILED`; перед этим
принтер ищут на шине и найденный сбрасывают, а ошибки поиска и сброса
игнорируют). P7 поведения не меняет: успех — принтер принял данные.

Железа нет, подмен кода тоже нет, кроме разрешённых § 7.3: `DummyUsbPrinter`
и его подклассы здесь меняют только `_open` (настоящий `Dummy` из escpos
вместо USB) и `_find`. `CheckedDevicePrinter` оставляет `_open` настоящим,
но вставляет в его `CheckedUsb` поддельное устройство pyusb `FakeUsbDevice`
и тоже меняет `_find` — так проверяется сама проверка записи P4. Его
подкласс `_CheckedFound` меняет только `_find`: тот отдаёт принтер «на
шине», и виден сброс после `ShortWriteError` из настоящего `CheckedUsb._raw`
(P5). `_NothingOnBus` оставляет `_open` настоящим целиком, с ленивым
открытием устройства escpos при первой записи (P3 п. 2), и лишь велит его
поиску на шине отвергнуть любое устройство: так без железа виден путь
выдернутого кабеля через настоящий `CheckedUsb._raw` (P4, P5), а в
принтер не уходит ни байта. Бэкенд libusb настоящий — поставляемая `app/libusb-1.0.0.dylib`. Загрузку
именно этого файла, а не системной libusb, проверяет `test_libusb.py` в
отдельных процессах: pyusb запоминает первую загруженную библиотеку на весь
процесс.
"""
from __future__ import annotations

import errno
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

import escpos.exceptions
import pytest
import usb.backend.libusb1
import usb.core
from PIL import Image

from helpers import (
    APP,
    BUNDLED,
    CheckedDevicePrinter,
    DummyUsbPrinter,
    FakeUsbDevice,
    RecordingDummy,
    ResettableDevice,
    decode_raster,
)
from photoprint.printer import (
    PRINTER_FAILED,
    PRINTER_NO_LIBUSB,
    PRINTER_NOT_FOUND,
    CheckedUsb,
    PrinterError,
    ShortWriteError,
    UsbPrinter,
    load_backend,
    to_printable,
)


def _white(width: int, height: int) -> Image.Image:
    """Белая RGB-картинка `width`×`height` — как JPEG после `load_printable`."""
    return Image.new("RGB", (width, height), (255, 255, 255))


def _grey(width: int, height: int) -> Image.Image:
    """Ровно-серая картинка режима `L` с яркостью 64.

    На сплошном сером дизеринг даёт узор, который зависит от ошибки,
    накопленной в строках выше: если картинку дизерить кусками, узор
    начинается заново на каждом куске и виден шов (P1).
    """
    return Image.new("L", (width, height), 64)


def _gradient(width: int, height: int) -> Image.Image:
    """Цветная RGB-картинка, где каждый канал меняется по своему закону.

    На ней видно, есть ли в P1 шаг `convert("L")`. Pillow переводит RGB
    прямо в режим `1` через свою формулу яркости с отбрасыванием дробной
    части, а `convert("L")` округляет. Яркость местами расходится на 1, и
    дизеринг дальше идёт по-другому. На белом, чёрном и сером `L` этой
    разницы нет.
    """
    image = Image.new("RGB", (width, height))
    image.putdata(
        [
            ((x * 7 + y) % 256, (x + y * 3) % 256, (x * y) % 256)
            for y in range(height)
            for x in range(width)
        ]
    )
    return image


class _TimedDummy(RecordingDummy):
    """`RecordingDummy`, который запоминает момент и байты каждой записи.

    По моментам видно паузу между подачей `\\n\\n` и отрезом (P3 п. 5):
    в байтах печати она не видна. Момент берётся до записи, поэтому пауза
    между концом одной записи и началом следующей в разницу входит целиком.
    """

    def __init__(self) -> None:
        """Создать приёмник без заказанных сбоев и с пустым журналом записей."""
        super().__init__()
        self.writes: List[Tuple[float, bytes]] = []

    def _raw(self, msg: bytes) -> None:
        """Записать в журнал `(time.monotonic(), msg)` и принять запись как обычно."""
        self.writes.append((time.monotonic(), bytes(msg)))
        super()._raw(msg)


class _TimedUsbPrinter(DummyUsbPrinter):
    """`DummyUsbPrinter`, у которого `_open` отдаёт `_TimedDummy` (§ 7.3: меняется только `_open`)."""

    def _open(self) -> _TimedDummy:
        """Отдать новый `_TimedDummy` и запомнить его в `self.opened`."""
        printer = _TimedDummy()
        self.opened.append(printer)
        return printer


class _OpenFails(DummyUsbPrinter):
    """`DummyUsbPrinter`, у которого бросает уже само `_open` (§ 7.3: меняется только `_open`).

    Так проверяется, что шаг P3 п. 2 стоит внутри обработки сбоя, а не
    перед ней, и что после сбоя самого `_open` принтер тоже ищут на шине и
    сбрасывают (P5): `found` — что отдаст `_find`.
    """

    def __init__(
        self, libusb: Path, error: BaseException, found: Optional[object] = None
    ) -> None:
        """Создать принтер, чей `_open` бросит `error`, а `_find` отдаст `found`."""
        super().__init__(libusb, found=found)
        self.error = error

    def _open(self) -> RecordingDummy:
        """Бросить заданное исключение вместо создания объекта escpos."""
        raise self.error


class _CheckedFound(CheckedDevicePrinter):
    """`CheckedDevicePrinter`, чей `_find` отдаёт заданный принтер «на шине» (§ 7.3: меняется только `_find`).

    У `CheckedDevicePrinter` поиск всегда даёт `None`, поэтому сброс после
    `ShortWriteError` из настоящего `CheckedUsb._raw` там не виден (P5):
    сокращение «короткая запись — принтер ещё занят, сбрасывать нельзя»
    прошло бы все его тесты. `_open` остаётся от `CheckedDevicePrinter` —
    настоящий `CheckedUsb` с поддельным устройством записи.
    """

    def __init__(self, libusb: Path, device: FakeUsbDevice, found: ResettableDevice) -> None:
        """Создать принтер с поддельным устройством записи и принтером `found` на шине."""
        super().__init__(libusb, device)
        self.found = found

    def _find(self) -> Optional[object]:
        """Сосчитать поиск и отдать `found` вместо `usb.core.find`."""
        self.find_calls += 1
        return self.found


def _reject_every_device(device: object) -> bool:
    """P3 п. 2, § 7.3: отвергнуть любое устройство шины — `custom_match` для `usb.core.find` у `_NothingOnBus`.

    pyusb зовёт его для каждого устройства с подходящими VID и PID; `False`
    значит «не то устройство». Поиск тогда ничего не находит — как без
    принтера на шине, даже если XP-160LL подключён.
    """
    return False


class _NothingOnBus(UsbPrinter):
    """`UsbPrinter`, чей настоящий `CheckedUsb` не находит на шине ни одного устройства (§ 7.3: меняются только `_open` и `_find`).

    `_open` — настоящий (`super()._open()`: VID, PID, бэкенд, тайм-аут и
    профиль P3 п. 2), только в `usb_args` объекта escpos добавлен
    `custom_match`, который отвергает любое устройство. escpos передаёт
    `usb_args` в `usb.core.find` при ленивом открытии на первой записи
    (P3 п. 2) и по-настоящему ищет принтер на шине с поставляемой libusb
    (P2) — и не находит, даже если XP-160LL подключён. Так выглядит
    выдернутый кабель; устройство не открывается, и в принтер не уходит ни
    байта. Созданные объекты escpos копятся в `opened`.

    `_find` считает вызовы в `find_calls` и отдаёт `None` вместо поиска:
    настоящий поиск нашёл бы подключённый принтер, и сброс P5 после
    неудачной печати ушёл бы в него по-настоящему.
    """

    def __init__(self, libusb: Path) -> None:
        """Загрузить libusb по-настоящему; ни открытие, ни поиск для сброса принтера не найдут."""
        super().__init__(libusb)
        self.opened: List[CheckedUsb] = []
        self.find_calls = 0

    def _open(self) -> CheckedUsb:
        """Построить настоящий `CheckedUsb`, которому не подходит ни одно устройство шины."""
        printer = super()._open()
        # P3 п. 2: устройство ещё не открыто (`_device is False`) — его
        # откроет первая запись, и поиск пойдёт уже с этим `custom_match`.
        printer.usb_args["custom_match"] = _reject_every_device
        self.opened.append(printer)
        return printer

    def _find(self) -> Optional[object]:
        """Сосчитать поиск и отдать `None` — «принтера на шине нет»."""
        self.find_calls += 1
        return None


def test_to_printable_white_rgb() -> None:
    """P1: белая RGB-картинка становится режимом `1` того же размера, вся белая."""
    printable = to_printable(_white(576, 100))

    assert printable.mode == "1"
    assert printable.size == (576, 100)
    assert printable.getextrema() == (255, 255)


def test_to_printable_black_cmyk() -> None:
    """P1: чёрная CMYK-картинка (как CMYK-JPEG) становится режимом `1`, вся чёрная.

    CMYK `(0, 0, 0, 255)` — чистый чёрный канал K. Перевод в `L` обязан
    дать чёрный, иначе CMYK-JPEG заказчика напечатается пустым.
    """
    printable = to_printable(Image.new("CMYK", (576, 10), (0, 0, 0, 255)))

    assert printable.mode == "1"
    assert printable.getextrema() == (0, 0)


def test_to_printable_dithers_whole_height_once() -> None:
    """P1: серая картинка 576×1920 дизерится одним проходом на всю высоту.

    Эталон — `convert("1")` всей картинки сразу. Дизеринг кусками по 960
    строк (как у escpos) дал бы другие точки начиная со строки 960.
    """
    img = _grey(576, 1920)

    assert to_printable(img).tobytes() == img.convert("1").tobytes()


def test_to_printable_converts_colour_to_grey_first() -> None:
    """P1 п. 1: цветная картинка сначала становится серой `L`, и только потом дизерится.

    Эталон — `convert("L").convert("1")`. Прямой `convert("1")` из RGB
    дал бы на градиенте другие точки: у Pillow в нём своя яркость без
    округления.
    """
    img = _gradient(576, 300)
    expected = img.convert("L").convert("1").tobytes()

    # Картинка обязана различать два пути, иначе тест прошёл бы и без `L`.
    assert img.convert("1").tobytes() != expected
    assert to_printable(img).tobytes() == expected


def test_print_image_byte_sequence() -> None:
    """P3: байты одной печати идут в нужном порядке и с нужными точками.

    Белая картинка 576×1500 с чёрными строками 0–9:
    - начало — `ESC @` и `GS ! 0` (P3 п. 3);
    - растры `GS v 0` по 72 байта на строку, куски 960 и 540 строк —
      умолчания escpos (P3 п. 4);
    - у каждого растра байт плотности `m` равен 0 — высокая плотность по
      обеим осям, умолчание escpos (P3 п. 4). С `m` 1 или 2 принтер
      растянул бы картинку вдвое по ширине или по высоте, а точки при этом
      остались бы теми же;
    - в первом куске строка 0 чёрная (все биты 1), строка 100 белая (0);
    - конец — отрез `ESC d 6` + `GS V 0` (P3 п. 5);
    - `\\n\\n` стоит после последнего растра, то есть перед отрезом.
    """
    printer = DummyUsbPrinter(BUNDLED)
    image = _white(576, 1500)
    image.paste((0, 0, 0), (0, 0, 576, 10))

    printer.print_image(image)
    out = printer.opened[0].output
    chunks = decode_raster(out)

    assert out.startswith(b"\x1b@\x1d!\x00")
    assert [(x, h) for x, h, _ in chunks] == [(72, 960), (72, 540)]
    # `decode_raster` байт `m` пропускает, поэтому он проверяется здесь:
    # заголовков с `m == 0` столько же, сколько растров. Ложных совпадений
    # нет — точки этой картинки только 0x00 и 0xFF.
    assert out.count(b"\x1dv0\x00") == len(chunks)
    first = chunks[0][2]
    assert first[0:72] == b"\xff" * 72
    assert first[7200:7272] == b"\x00" * 72
    assert out.endswith(b"\x1bd\x06\x1dV\x00")
    # Конец последнего растра — его заголовок плюс 8 байт заголовка и точки.
    # `rindex` находит именно заголовок: точки этой картинки — только байты
    # 0x00 и 0xFF, три байта `1d 76 30` среди них не встретятся.
    last_x, last_h, _ = chunks[-1]
    raster_end = out.rindex(b"\x1dv0") + 8 + last_x * last_h
    assert b"\n\n" in out[raster_end:]


def test_print_image_pauses_before_cut() -> None:
    """P3 п. 5: между подачей `\\n\\n` и отрезом проходит не меньше 0,3 с.

    Пауза даёт принтеру дотянуть ленту до ножа — как в клиенте ресепшена.
    Сразу после записи подачи идёт отрез (`ESC d 6`, затем `GS V 0`), и от
    начала записи подачи до начала отреза не меньше 0,3 с. Так ловится и
    пропавшая или укороченная пауза, и пауза не на своём месте — до подачи
    или после отреза. `time.sleep` спит не меньше заданного (так обещает
    документация Python), поэтому порог — ровно 0,3 с из спецификации.
    """
    printer = _TimedUsbPrinter(BUNDLED)

    printer.print_image(_white(576, 10))
    writes = printer.opened[0].writes
    feed = max(i for i, (_, msg) in enumerate(writes) if msg.endswith(b"\n\n"))

    assert [msg for _, msg in writes[feed + 1:]] == [b"\x1bd\x06", b"\x1dV\x00"]
    assert writes[feed + 1][0] - writes[feed][0] >= 0.3


def test_print_image_tail_is_exact() -> None:
    """P3 п. 5: после растра — ровно `text("\\n\\n")` и отрез, байт в байт.

    `test_print_image_byte_sequence` ищет `\\n\\n` где-то после растра, а
    `test_print_image_pauses_before_cut` — запись, которая кончается на
    `\\n\\n`: оба пропустили бы и лишнюю пустую строку, и подачу мимо
    `text`. Поэтому хвост после последнего растра сравнивается целиком:
    - `ESC t 0` — выбор кодовой страницы: escpos шлёт его перед первым
      текстом, то есть его даёт именно вызов `text`. `_raw(b"\\n\\n")`
      вместо `text("\\n\\n")` его бы не дал, и принтер заказчика получал бы
      поток, отличный от потока клиента ресепшена, который P3 п. 5
      повторяет как проверенный на XP-160LL;
    - ровно две пустые строки. Третья — несколько миллиметров пустой
      ленты перед каждым отрезом, на каждой печати;
    - отрез `ESC d 6` + `GS V 0`, и после него ничего.
    Настоящий `CheckedUsb` — тот же `Escpos` с тем же профилем `TM-P80`,
    поэтому хвост у заказчика тот же. Конец растра считается как в
    `test_print_image_byte_sequence`: точки белой картинки — только байты
    0x00, заголовок `1d 76 30` среди них не встретится.
    """
    printer = DummyUsbPrinter(BUNDLED)

    printer.print_image(_white(576, 10))
    out = printer.opened[0].output
    last_x, last_h, _ = decode_raster(out)[-1]
    raster_end = out.rindex(b"\x1dv0") + 8 + last_x * last_h

    assert out[raster_end:] == b"\x1bt\x00\n\n\x1bd\x06\x1dV\x00"


def test_print_image_grey_has_no_seam() -> None:
    """P1, P3 п. 4: в принтер уходят ровно точки одного дизеринга всей высоты.

    Серая картинка 576×1920 режется escpos на два куска по 960 строк.
    Склейка точек всех кусков обязана совпасть с `to_printable` бит в бит
    (у escpos 1 — чёрная точка, у Pillow 1 — белая, отсюда `^ 0xFF`). Если
    отдать escpos серую картинку, он дизерит каждый кусок заново — на
    строках 958–962 появляется шов, и сравнение падает.
    """
    printer = DummyUsbPrinter(BUNDLED)
    img = _grey(576, 1920)

    printer.print_image(img)
    chunks = decode_raster(printer.opened[0].output)

    assert b"".join(payload for _, _, payload in chunks) == bytes(
        b ^ 0xFF for b in to_printable(img).tobytes()
    )


def test_print_image_without_libusb(tmp_path: Path) -> None:
    """P2, P3 п. 1: без файла libusb бэкенда нет, печать — `PRINTER_NO_LIBUSB`.

    Ошибка возникает до всякой работы с USB: `_open` не вызывается ни
    разу. Текст — дословно из § 4.3.
    """
    printer = DummyUsbPrinter(tmp_path / "missing.dylib")

    assert printer.backend is None
    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_NO_LIBUSB
    assert PRINTER_NO_LIBUSB == (
        "Не загрузилась библиотека USB. Установите клиент заново командой из README."
    )
    assert printer.opened == []


def test_missing_libusb_is_none_even_after_bundled_loaded(tmp_path: Path) -> None:
    """P2: отсутствующий файл даёт `None`, даже когда pyusb уже держит libusb.

    pyusb запоминает первую загруженную библиотеку и дальше отдаёт её при
    любом `find_library`. Поэтому проверка `is_file()` обязана стоять до
    обращения к pyusb — иначе пропавший файл libusb не был бы замечен.
    """
    assert load_backend(BUNDLED) is not None
    assert load_backend(tmp_path / "missing.dylib") is None


def test_print_image_opens_new_escpos_each_time() -> None:
    """P3 п. 2: каждая печать открывает новый объект escpos.

    Старый объект после неудачного открытия принтер на шине больше не ищет
    и на записи даёт голое исключение (`AttributeError` в `CheckedUsb._raw`,
    `AssertionError` в `_raw` самого escpos), поэтому переиспользовать его
    нельзя.
    """
    printer = DummyUsbPrinter(BUNDLED)

    printer.print_image(_white(576, 10))
    printer.print_image(_white(576, 10))

    assert len(printer.opened) == 2
    assert printer.opened[0] is not printer.opened[1]


def test_print_image_wraps_usb_error() -> None:
    """P3, P5: `USBError` при печати — `PrinterError(PRINTER_FAILED)`.

    Сбой на 3-й записи — первом растре, после `ESC @` и `GS ! 0`. Исходное
    исключение сохраняется в `__cause__`. Текст — дословно из § 4.3.
    `close()` зовётся ровно раз и после сбоя (P3 п. 6): иначе захваченный
    интерфейс USB оставался бы открытым до сборки мусора, и следующая
    печать могла бы его не получить.
    """
    error = usb.core.USBError("boom")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error))

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert PRINTER_FAILED == (
        "Принтер не ответил. Проверьте ленту, выключите и включите принтер, "
        "потом нажмите «Печать» ещё раз."
    )
    assert caught.value.__cause__ is error
    # Сбой случился именно на 3-й записи: до неё принтер принял только две
    # команды сброса.
    assert printer.opened[0].output == b"\x1b@\x1d!\x00"
    assert printer.opened[0].close_calls == 1


def test_print_image_fails_when_cut_fails() -> None:
    """P3 п. 5: ошибка отреза не глотается — без отреза печать не успешна.

    Сбой заказан на первой записи, начинающейся с `GS V` (отрез). Всё до
    отреза, включая подачу `ESC d 6`, принтер принял — значит, упала
    именно команда отреза, а не что-то раньше. `close()` зовётся ровно раз
    и после этого сбоя (P3 п. 6).

    В `__cause__` — сама ошибка отреза (P5). Отрез, обёрнутый в свой
    `try` с `PrinterError`, дал бы тот же текст, но наружная обработка
    обернула бы его ещё раз, и в `__cause__` оказалась бы `PrinterError`
    вместо исходной ошибки USB.
    """
    error = usb.core.USBError("cut")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(b"\x1dV", error))

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.opened[0].output.endswith(b"\x1bd\x06")
    assert printer.opened[0].close_calls == 1


def test_print_image_wraps_any_error() -> None:
    """P5: не только `USBError` — любое исключение, кроме `DeviceNotFoundError`, даёт `PRINTER_FAILED`.

    Сбой заказан на первой записи, и это `RuntimeError`, а не ошибка USB.
    Наружу выходит только `PrinterError` с текстом § 4.3, исходное
    исключение — в `__cause__`: иначе оператор увидел бы ошибку сервера
    вместо русского текста. `close()` зовётся ровно раз (P3 п. 6).

    Принтер для сброса ищут и после такого сбоя (P5 — «исключение», а не
    «ошибка USB»): сброс только после `USBError` и `ShortWriteError` здесь
    не искал бы его вовсе.
    """
    error = RuntimeError("boom")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(1, error))

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.opened[0].output == b""
    assert printer.opened[0].close_calls == 1
    assert printer.find_calls == 1


def test_print_image_wraps_device_not_found() -> None:
    """P5: выдернутый кабель — тоже `PrinterError`, а не голое исключение escpos.

    escpos открывает устройство при первой записи, и без принтера на шине
    бросает `DeviceNotFoundError` — он не наследник `USBError`. Здесь он
    заказан на первой записи. Проверяется только обёртка: `PrinterError` с
    этим исключением в `__cause__` и `close()` ровно раз (P3 п. 6). Текст
    `PRINTER_NOT_FOUND` закрепляет `test_device_not_found_maps_to_not_found`.
    """
    error = escpos.exceptions.DeviceNotFoundError("x")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(1, error))

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert caught.value.__cause__ is error
    assert printer.opened[0].close_calls == 1


def test_print_image_wraps_open_error() -> None:
    """P3 п. 2, P5: исключение из самого `_open` — тоже `PRINTER_FAILED`.

    Шаг 2 входит в шаги 2–5, на которых любое исключение становится
    `PrinterError`. Если бы `_open` стоял перед `try`, наружу вышло бы
    голое `RuntimeError`.

    Сброс P5 тоже относится к шагу 2: принтер на шине задан, его ищут и
    сбрасывают ровно раз. Объекта escpos в этот момент нет, но сброс идёт
    через `_find`, а не через него, — пропуск сброса «раз `_open` не
    дошёл до конца» (`printer is None`) здесь заметен.
    """
    error = RuntimeError("open")
    found = ResettableDevice()
    printer = _OpenFails(BUNDLED, error, found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.find_calls == 1
    assert found.reset_calls == 1


def test_close_error_does_not_fail_print() -> None:
    """P3 п. 6: `close()` зовётся ровно раз, и его ошибка печать не отменяет.

    Данные приняты принтером — печать успешна (P7), даже если после неё
    не удалось закрыть устройство.
    """
    printer = DummyUsbPrinter(BUNDLED, close_error=True)

    assert printer.print_image(_white(576, 10)) is None
    assert printer.opened[0].close_calls == 1


def test_open_builds_checked_usb() -> None:
    """P2, P3 п. 2: настоящий `_open` строит `CheckedUsb` с нужными параметрами.

    - бэкенд передан через `usb_args` — аргумент `backend=` escpos 3.1
      молча игнорирует (P2);
    - VID/PID XP-160LL, тайм-аут записи 10 с, конечные точки 0x82/0x01;
    - профиль `TM-P80`;
    - устройство ещё не открыто (`_device is False`): открытие ленивое, при
      первой записи. Тест ничего не пишет и принтер не трогает.
    """
    pr = UsbPrinter(BUNDLED)
    # Иначе проверка `is pr.backend` прошла бы и на двух `None`.
    assert pr.backend is not None

    p = pr._open()

    assert isinstance(p, CheckedUsb)
    assert p.usb_args["backend"] is pr.backend
    assert p.usb_args["idVendor"] == 0x0483
    assert p.usb_args["idProduct"] == 0x5743
    assert p.timeout == 10000
    assert p.in_ep == 0x82
    assert p.out_ep == 0x01
    assert p.profile.profile_data["name"] == "TM-P80"
    assert p._device is False


def test_real_open_builds_new_object_each_time() -> None:
    """P3 п. 2: настоящий `_open` на каждый вызов строит новый `CheckedUsb`.

    `test_print_image_opens_new_escpos_each_time` видит только подменённый
    `_open`, а `test_open_builds_checked_usb` зовёт настоящий один раз, —
    здесь проверяется сам настоящий `_open`: он не держит объект escpos
    между печатями. Объект escpos после неудачного открытия (принтер
    выключен или без кабеля) хранит `_device = None`, `close()` его не
    сбрасывает, и на каждой следующей печати переиспользованный объект
    давал бы голый `AttributeError` из `CheckedUsb._raw` (`None.write`),
    не ища принтер на шине. Оператор
    видел бы «Принтер не ответил…» и после того, как вставил кабель и
    включил принтер, — до перезапуска клиента. Оба объекта ещё не открыты
    (`_device is False`): тест ничего не пишет и принтер не трогает.
    """
    pr = UsbPrinter(BUNDLED)
    # Без бэкенда `_open` тоже строил бы объекты, но не на том пути, по
    # которому идёт печать у заказчика.
    assert pr.backend is not None

    first = pr._open()
    second = pr._open()

    assert isinstance(first, CheckedUsb)
    assert isinstance(second, CheckedUsb)
    assert first is not second
    assert second._device is False


def test_connected_via_find(tmp_path: Path) -> None:
    """P6: `connected()` — «`_find()` нашёл устройство», любая ошибка — `False`.

    Без бэкенда `connected()` сразу `False` и `_find` не зовёт. У принтера
    без libusb `found` задан объектом: если бы `_find` всё же вызвали,
    ответ был бы `True`, и тест упал бы не только на счётчике вызовов.
    """
    assert DummyUsbPrinter(BUNDLED, found=object()).connected() is True
    assert DummyUsbPrinter(BUNDLED, found=None).connected() is False
    assert DummyUsbPrinter(BUNDLED, find_error=RuntimeError("x")).connected() is False

    no_libusb = DummyUsbPrinter(tmp_path / "missing.dylib", found=object())
    assert no_libusb.connected() is False
    assert no_libusb.find_calls == 0


def test_connected_real_bus() -> None:
    """P6: настоящий `connected()` на настоящей шине USB отвечает `bool`.

    Принтера у разработчика может не быть — важно, что ответ `True` или
    `False`, а не исключение. Шина только опрашивается, записи нет.
    """
    assert isinstance(UsbPrinter(BUNDLED).connected(), bool)


def test_find_calls_always_pass_backend() -> None:
    """P2, P6: каждый вызов `usb.core.find(` в `printer.py` передаёт `backend=self.backend`.

    Вызов без бэкенда загрузил бы системную libusb (Homebrew) и закрепил
    её в процессе, а у заказчика её нет. Хотя бы один такой вызов обязан
    быть — это `_find` (P6); иначе проверка прошла бы впустую.
    """
    source = (APP / "photoprint" / "printer.py").read_text(encoding="utf-8")
    calls = [line for line in source.splitlines() if "usb.core.find(" in line]

    assert calls
    for line in calls:
        assert "backend=self.backend" in line, line


def test_find_call_is_exact() -> None:
    """P6: в `printer.py` ровно один вызов `usb.core.find(` — `_find` с VID, PID и бэкендом.

    Строка сравнивается целиком. Подстроки `backend=self.backend` мало:
    - без `idProduct` принтером казалось бы любое устройство
      STMicroelectronics (VID 0x0483, например программатор ST-Link);
    - с `find_all=True` `find` отдаёт генератор, он никогда не `None`, и
      `connected()` всегда `True`;
    - VID и PID, переставленные местами, не нашли бы принтер никогда;
    - комментарий `# backend=self.backend` в конце строки обманул бы
      проверку подстроки.
    Другие места ищут принтер через `self._find()`, а не новым вызовом.
    """
    source = (APP / "photoprint" / "printer.py").read_text(encoding="utf-8")
    calls = [line.strip() for line in source.splitlines() if "usb.core.find(" in line]

    assert calls == ["return usb.core.find(idVendor=VID, idProduct=PID, backend=self.backend)"]


def test_find_real_bus_returns_printer_or_none() -> None:
    """P6: настоящий `_find()` на настоящей шине отдаёт `None` или сам XP-160LL.

    Принтера у разработчика может не быть, тогда ответ `None`. Если он
    подключён, это устройство pyusb с VID 0x0483 и PID 0x5743. Генератор
    (`find_all=True`) тест не пропустит на любой машине, чужое устройство —
    если оно окажется на шине; прочие искажения вызова ловит
    `test_find_call_is_exact`. Шина только опрашивается, записи нет.
    """
    found = UsbPrinter(BUNDLED)._find()

    assert found is None or (
        isinstance(found, usb.core.Device)
        and (found.idVendor, found.idProduct) == (0x0483, 0x5743)
    )


def test_print_image_wraps_to_printable_error() -> None:
    """P3 п. 4, P5: ошибка `to_printable` — тоже `PrinterError(PRINTER_FAILED)`, а не голое исключение.

    Закрытая картинка: Pillow на `convert` бросает `ValueError("Operation
    on closed image")`. `to_printable` — часть шага P3 п. 4, поэтому его
    ошибка обязана пройти тот же разбор сбоев, что и ошибка USB: иначе
    оператор увидел бы ошибку сервера вместо русского текста. Если бы
    перевод в точки стоял перед обработкой сбоя, наружу вышел бы
    `ValueError`. Принтер к этому моменту принял только две команды
    сброса, `close()` зовётся ровно раз (P3 п. 6). Принтер для сброса ищут
    и после этого сбоя, хотя USB тут ни при чём (P5 — любое исключение на
    шагах 2–5).
    """
    image = _white(576, 10)
    image.close()
    printer = DummyUsbPrinter(BUNDLED)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(image)
    assert str(caught.value) == PRINTER_FAILED
    assert isinstance(caught.value.__cause__, ValueError)
    assert printer.opened[0].output == b"\x1b@\x1d!\x00"
    assert printer.opened[0].close_calls == 1
    assert printer.find_calls == 1


def test_checked_usb_full_writes_succeed() -> None:
    """P4: настоящий `CheckedUsb`, чьё устройство принимает каждую запись целиком, печатает без ошибки.

    Картинка 576×100 идёт через настоящий `_open` и `CheckedUsb._raw` в
    `FakeUsbDevice`:
    - исключения нет, и `_find` не зовётся — удачная печать принтер не
      сбрасывает (P5);
    - каждая запись — с тайм-аутом ровно 10 000 мс: без явного тайм-аута
      pyusb ждал бы 1000 мс, и длинный кусок растра обрывался бы (P4);
    - каждая запись — в конечную точку `OUT_EP` 0x01;
    - записей не меньше пяти: `ESC @`, `GS ! 0`, растр, подача, отрез;
    - устройство получило ровно те байты, что и `Dummy` при той же печати:
      `_raw` отдаёт сообщение целиком и по одному разу. Поэтому байтовые
      тесты на `Dummy` (P1, P3) верны и для настоящего `CheckedUsb`.
    Картинка — цветной градиент, чтобы в растре были разные байты, а не
    одни нули.
    """
    device = FakeUsbDevice()
    printer = CheckedDevicePrinter(BUNDLED, device)
    reference = DummyUsbPrinter(BUNDLED)
    image = _gradient(576, 100)

    printer.print_image(image)
    reference.print_image(image)

    assert set(device.timeouts) == {10000}
    assert device.writes >= 5
    assert set(device.endpoints) == {0x01}
    assert b"".join(device.sent) == reference.opened[0].output
    assert printer.find_calls == 0


def test_checked_usb_short_write_fails() -> None:
    """P4, P5: устройство приняло половину 3-й записи — печать не успешна, `PRINTER_FAILED`.

    Так pyusb отвечает на тайм-аут посреди передачи: меньшее число байт
    без исключения, а escpos это число не смотрит. `CheckedUsb._raw`
    обязан сравнить его с длиной сообщения и бросить `ShortWriteError` с
    текстом `"{n} из {len(msg)}"`, а `print_image` — превратить его в
    `PrinterError(PRINTER_FAILED)` с ним в `__cause__` (P5). 3-я запись —
    первый растр, после `ESC @` и `GS ! 0`. Ещё проверяется:
    - числа в тексте — именно принятые и отправленные байты 3-й записи;
    - после короткой записи печать останавливается: 4-й записи нет;
    - после сбоя принтер ищут на шине для сброса ровно раз (P5).
    """
    device = FakeUsbDevice(short_on=3)
    printer = CheckedDevicePrinter(BUNDLED, device)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 100))
    assert str(caught.value) == PRINTER_FAILED
    cause = caught.value.__cause__
    assert isinstance(cause, ShortWriteError)
    assert re.match(r"^\d+ из \d+$", str(cause))
    third = len(device.sent[2])
    assert str(cause) == f"{third // 2} из {third}"
    assert device.writes == 3
    assert printer.find_calls == 1


def test_short_write_on_cut_fails_with_exact_counts() -> None:
    """P4, P3 п. 5: принтер принял 1 байт из 3 байт отреза `GS V 0` — печать не успешна.

    Записи печати 576×100 через настоящий `CheckedUsb`: `ESC @`, `GS ! 0`,
    растр, `ESC t 0`, `\\n\\n`, `ESC d 6` и 7-я — отрез `GS V 0`. Короткая
    запись здесь — именно отрез: картинка и подача приняты, а нож не
    получил команду целиком, и печать обязана считаться неудачной
    (`PRINTER_FAILED`, в `__cause__` — `ShortWriteError`).

    Текст причины — «принято из отправлено» (P4): «1 из 3». В
    `test_checked_usb_short_write_fails` короткая запись — растр чётной
    длины, где принятых ровно столько же, сколько непринятых, и
    перепутанное первое число («сколько не принято») там не видно. На
    нечётных 3 байтах отреза `3 // 2 == 1`, а непринятых — 2.
    """
    device = FakeUsbDevice(short_on=7)
    printer = CheckedDevicePrinter(BUNDLED, device)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 100))
    assert str(caught.value) == PRINTER_FAILED
    cause = caught.value.__cause__
    assert isinstance(cause, ShortWriteError)
    # Короткой была именно запись отреза, и она последняя: после неё
    # принтеру ничего не отправляли.
    assert device.sent[6] == b"\x1dV\x00"
    assert device.writes == 7
    assert str(cause) == "1 из 3"
    assert printer.find_calls == 1


def test_short_write_resets_found_device() -> None:
    """P4, P5, P7: короткая запись посреди растра — найденный принтер сбрасывают ровно раз.

    Короткая запись и есть «обрыв посреди растра» (P7): принтер ждёт
    недостающие байты, и именно ради этого случая P5 велит сброс.
    `ShortWriteError` прямо назван в P5 среди сбоев, после которых сброс
    обязателен. Запись идёт через настоящий `CheckedUsb._raw` (P4), и
    `ShortWriteError` бросает именно он; 3-я запись — первый растр.
    В `test_checked_usb_short_write_fails` поиск отдаёт `None`, поэтому
    там виден только поиск, а не сброс.
    """
    device = FakeUsbDevice(short_on=3)
    found = ResettableDevice()
    printer = _CheckedFound(BUNDLED, device, found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 100))
    assert str(caught.value) == PRINTER_FAILED
    assert isinstance(caught.value.__cause__, ShortWriteError)
    assert device.writes == 3
    assert printer.find_calls == 1
    assert found.reset_calls == 1


def test_device_not_found_maps_to_not_found() -> None:
    """P5: `DeviceNotFoundError` из escpos — `PRINTER_NOT_FOUND`, дословно из § 4.3.

    escpos бросает его при первой записи, когда принтера нет на шине:
    кабель выдернут или питание выключено. Оператору тогда надо проверить
    кабель и питание, а не ленту, поэтому у этого сбоя свой текст. Текст
    сравнивается и с константой, и с буквальной строкой спецификации.
    Исходное исключение — в `__cause__`.

    Сброс P5 нужен и здесь, поэтому принтер на шине задан, и его ищут и
    сбрасывают ровно раз. escpos 3.1 превращает в `DeviceNotFoundError` и
    `USBError`, вышедшую из поиска и проверки драйвера в `Usb.open` (ошибки
    `set_configuration`/`reset` там только пишутся в лог), то есть и
    принтер, который на шине есть, но открыться не смог. Сокращение «принтера нет —
    сбрасывать нечего» или ранний `raise PRINTER_NOT_FOUND` до сброса
    оставили бы такой принтер без сброса.
    """
    error = escpos.exceptions.DeviceNotFoundError("x")
    found = ResettableDevice()
    printer = DummyUsbPrinter(BUNDLED, fail_on=(1, error), found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_NOT_FOUND
    assert str(caught.value) == (
        "Принтер не найден. Проверьте кабель USB и питание, "
        "потом нажмите «Печать» ещё раз."
    )
    assert caught.value.__cause__ is error
    assert printer.find_calls == 1
    assert found.reset_calls == 1


def test_timeout_maps_to_failed() -> None:
    """P5: `USBTimeoutError` — `PRINTER_FAILED`, дословно из § 4.3.

    Принтер на шине есть, но не принял запись за 10 с: кончилась лента или
    он завис. Это не «принтер не найден» — оператору надо проверить ленту и
    перезапустить принтер (P7). Исходное исключение — в `__cause__`.
    """
    error = usb.core.USBTimeoutError("t")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(1, error))

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert str(caught.value) == (
        "Принтер не ответил. Проверьте ленту, выключите и включите принтер, "
        "потом нажмите «Печать» ещё раз."
    )
    assert caught.value.__cause__ is error


def test_timeout_resets_found_device() -> None:
    """P5, P7: `USBTimeoutError` посреди растра — принтер на шине есть, но завис; его сбрасывают ровно раз.

    P5 прямо называет `USBTimeoutError` среди сбоев, после которых нужен
    сброс; «тайм-аут — принтер ещё занят, сбрасывать нельзя» — не правило
    спецификации. После тайм-аута на растре принтер может ждать
    недостающие байты (P7) — ровно тот случай, ради которого сброс.
    `test_timeout_maps_to_failed` принтер на шине не задаёт, и сброс там
    не виден.
    """
    found = ResettableDevice()
    error = usb.core.USBTimeoutError("t")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error), found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    # Приняты только `ESC @` и `GS ! 0`: тайм-аут случился на растре.
    assert printer.opened[0].output == b"\x1b@\x1d!\x00"
    assert printer.find_calls == 1
    assert found.reset_calls == 1


def test_enodev_usb_error_is_failed() -> None:
    """P5: `USBError` с errno ENODEV посреди растра — всё равно `PRINTER_FAILED`.

    Так pyusb отвечает, когда кабель выдернут посреди печати: `USBError`
    «No such device» с кодом libusb −4 и errno 19. P5 делит сбои только
    по классу исключения: `PRINTER_NOT_FOUND` — один `DeviceNotFoundError`
    из escpos, любая `USBError` — `PRINTER_FAILED`. Разбор по errno здесь
    был бы правилом, которого в спецификации нет.
    """
    error = usb.core.USBError("No such device (it may have been disconnected)", -4, 19)
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error))

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    # Ошибка действительно несёт ENODEV — иначе тест прошёл бы впустую.
    assert error.errno == 19
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error


def test_failure_resets_found_device() -> None:
    """P5: после сбоя печати принтер ищут на шине, и найденный сбрасывают ровно раз.

    Сбой — `USBError` на 3-й записи, посреди растра. Принтер после обрыва
    может ждать недостающие байты растра (P7), и сброс USB возвращает его в
    исходное состояние к следующей печати. Текст — `PRINTER_FAILED`,
    исходное исключение — в `__cause__`.
    """
    found = ResettableDevice()
    error = usb.core.USBError("x")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error), found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert found.reset_calls == 1
    assert printer.find_calls == 1


def _failing_print(
    step: str, found: ResettableDevice
) -> Tuple[DummyUsbPrinter, Image.Image, str]:
    """Собрать печать, которая падает на шаге `step` P3: принтер, картинку и ожидаемый текст.

    `found` — принтер «на шине», который `_find` отдаст после сбоя (P5).
    Шаги и исключения подобраны так, чтобы покрыть все шаги P3 п. 2–5 и
    все виды исключений. Сброс после сбоев на растре проверяют отдельные
    тесты, здесь их нет: `USBError` — `test_failure_resets_found_device`,
    `USBTimeoutError` — `test_timeout_resets_found_device`,
    `ShortWriteError` из настоящего `CheckedUsb._raw` —
    `test_short_write_resets_found_device`. Шаги:
    - `p3.2-open-error` — `RuntimeError` из самого `_open`, объекта escpos
      ещё нет;
    - `p3.3-not-found` — `DeviceNotFoundError` на первой записи;
    - `p3.3-runtime-error` — `RuntimeError` на первой записи, не ошибка USB;
    - `p3.4-to-printable-error` — закрытая картинка: `to_printable` бросает
      `ValueError`;
    - `p3.5-cut-error` — `USBError` на отрезе `GS V`.
    """
    image = _white(576, 10)
    text = PRINTER_FAILED
    printer: DummyUsbPrinter
    if step == "p3.2-open-error":
        printer = _OpenFails(BUNDLED, RuntimeError("open"), found=found)
    elif step == "p3.3-not-found":
        not_found = escpos.exceptions.DeviceNotFoundError("x")
        printer = DummyUsbPrinter(BUNDLED, fail_on=(1, not_found), found=found)
        text = PRINTER_NOT_FOUND
    elif step == "p3.3-runtime-error":
        printer = DummyUsbPrinter(BUNDLED, fail_on=(1, RuntimeError("boom")), found=found)
    elif step == "p3.4-to-printable-error":
        image.close()
        printer = DummyUsbPrinter(BUNDLED, found=found)
    elif step == "p3.5-cut-error":
        cut = usb.core.USBError("cut")
        printer = DummyUsbPrinter(BUNDLED, fail_on=(b"\x1dV", cut), found=found)
    else:
        # Опечатка в имени шага не должна тихо дать «печать без сбоя».
        raise AssertionError(f"неизвестный шаг {step!r}")
    return printer, image, text


@pytest.mark.parametrize(
    "step",
    [
        "p3.2-open-error",
        "p3.3-not-found",
        "p3.3-runtime-error",
        "p3.4-to-printable-error",
        "p3.5-cut-error",
    ],
)
def test_every_failure_step_resets_found_device(step: str) -> None:
    """P5: сбой на любом шаге P3 п. 2–5 любым исключением — принтер ищут и сбрасывают ровно раз.

    P5 говорит «исключение на шагах P3.2–P3.5», а не «ошибка USB при
    записи»: сброс нужен после открытия, выдернутого кабеля, чужого
    исключения, ошибки `to_printable` и отреза так же, как после обрыва
    растра. Иначе принтер мог бы остаться в ожидании недостающих байт
    (P7). Каждый случай ловит свою правдоподобную порчу обработки сбоя:
    сброс только после `USBError` и `ShortWriteError`, без сброса при
    `DeviceNotFoundError`, без сброса, когда упал сам `_open`. Текст —
    по P5: `PRINTER_NOT_FOUND` у `DeviceNotFoundError`, иначе
    `PRINTER_FAILED`.
    """
    found = ResettableDevice()
    printer, image, text = _failing_print(step, found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(image)
    assert str(caught.value) == text
    assert printer.find_calls == 1
    assert found.reset_calls == 1


def test_failure_without_device_skips_reset() -> None:
    """P5: после сбоя принтер ищут ровно раз; на шине его нет — сбрасывать нечего.

    Так выглядит кабель, выдернутый посреди печати: запись падает с
    `USBError`, а поиск уже ничего не находит (`_find` → `None`). Наружу —
    `PrinterError` с текстом и исходным исключением, как при любом сбое.
    """
    error = usb.core.USBError("x")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error), found=None)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.find_calls == 1


def test_reset_error_is_ignored() -> None:
    """P5: ошибка самого сброса не заменяет ответ — наружу `PrinterError(PRINTER_FAILED)`.

    Принтер, который не принял запись, может не принять и сброс. Эта
    ошибка игнорируется: оператор получает текст исходного сбоя, а в
    `__cause__` — исходное исключение, а не ошибка сброса. Если бы `USBError`
    сброса вышел наружу, `pytest.raises(PrinterError)` его бы не поймал.
    """
    found = ResettableDevice(error=usb.core.USBError("r"))
    error = usb.core.USBError("x")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error), found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    # Сброс действительно был и действительно упал — иначе тест прошёл бы
    # и там, где сброса нет вовсе.
    assert found.reset_calls == 1


def test_find_error_after_failure_is_ignored() -> None:
    """P5: сбой поиска принтера для сброса тоже игнорируется — наружу `PrinterError(PRINTER_FAILED)`.

    `_find` после сбоя печати зовётся внутри обработки ошибок: шина,
    которая только что отказала в записи, может отказать и в поиске. Если
    бы его `USBError` вышел наружу, оператор увидел бы ошибку сервера вместо
    текста, а сбой печати не был бы сосчитан (W4 п. 4). Исходное исключение —
    в `__cause__`, поиск — ровно один.
    """
    error = usb.core.USBError("x")
    printer = DummyUsbPrinter(
        BUNDLED, fail_on=(3, error), find_error=usb.core.USBError("f")
    )

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.find_calls == 1


def test_non_usb_reset_error_is_ignored() -> None:
    """P5: ошибка сброса не из USB тоже игнорируется — наружу `PrinterError(PRINTER_FAILED)`.

    P5 велит игнорировать любые ошибки сброса, а не только `USBError`. У
    pyusb это не теория: `Device.reset()` идёт через `_check` бэкенда
    libusb1, и на `LIBUSB_ERROR_NOT_SUPPORTED` тот бросает
    `NotImplementedError`, а не `USBError`. Если бы он вышел из
    `print_image`, API не поймал бы его как `PrinterError`: «ошибок
    печати» не прибавилось бы, и оператор увидел бы ошибку сервера вместо
    текста (W4 п. 4). Сброс был ровно один, в `__cause__` — исходный сбой.
    """
    found = ResettableDevice(error=NotImplementedError("reset"))
    error = usb.core.USBError("x")
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error), found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert found.reset_calls == 1


def test_non_usb_find_error_is_ignored() -> None:
    """P5: исключение не из USB при поиске принтера для сброса тоже игнорируется.

    Поиск на шине идёт через бэкенд libusb1 pyusb, который бросает не
    только `USBError` (на `LIBUSB_ERROR_NOT_SUPPORTED` — `NotImplementedError`).
    Любая ошибка поиска после сбоя печати не должна подменить ответ
    оператору (W4 п. 4): наружу `PrinterError(PRINTER_FAILED)` с исходным
    сбоем в `__cause__`, поиск — ровно один.
    """
    error = usb.core.USBError("x")
    printer = DummyUsbPrinter(
        BUNDLED, fail_on=(3, error), find_error=NotImplementedError("find")
    )

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.find_calls == 1


@pytest.mark.parametrize("where", ["find", "reset"])
def test_foreign_find_or_reset_error_is_ignored(where: str) -> None:
    """P5: ошибка поиска или сброса любого класса, не только из pyusb, игнорируется.

    P5 велит игнорировать ошибки поиска и сброса, не называя их классов.
    Бэкенд libusb1 pyusb бросает `USBError`, `USBTimeoutError` и
    `NotImplementedError`; `RuntimeError` — ни одно из них
    (`NotImplementedError` — его подкласс, а не наоборот). Обработка,
    суженная до `except (USBError, NotImplementedError)`, прошла бы
    `test_non_usb_*`, но выпустила бы `RuntimeError` наружу вместо
    `PrinterError`. Наружу — `PRINTER_FAILED` с исходным сбоем в
    `__cause__`, поиск — ровно один.
    """
    error = usb.core.USBError("x")
    found = ResettableDevice(error=RuntimeError("reset"))
    if where == "find":
        # Поиск падает сам и `found` не отдаёт: сбрасывать некого.
        printer = DummyUsbPrinter(
            BUNDLED, fail_on=(3, error), found=found, find_error=RuntimeError("find")
        )
        resets = 0
    else:
        printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error), found=found)
        # Сброс действительно был и упал — иначе тест прошёл бы и без сброса.
        resets = 1

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.find_calls == 1
    assert found.reset_calls == resets


def test_success_does_not_reset() -> None:
    """P5: удачная печать принтер на шине не ищет и не сбрасывает.

    Сброс нужен только после сбоя; после удачной печати он прервал бы
    работу принтера, который ещё печатает принятые данные (P7). Принтер на
    шине задан: лишний поиск нашёл бы его и сбросил.
    """
    found = ResettableDevice()
    printer = DummyUsbPrinter(BUNDLED, found=found)

    printer.print_image(_white(576, 10))
    assert printer.find_calls == 0
    assert found.reset_calls == 0


def test_no_libusb_does_not_reset(tmp_path: Path) -> None:
    """P3 п. 1, P5: без libusb печать — `PRINTER_NO_LIBUSB`, и принтер для сброса не ищут.

    Шаг P3 п. 1 не входит в шаги 2–5, после которых нужен сброс: без
    бэкенда шину не трогают вовсе. Принтер на шине задан: лишний поиск
    нашёл бы его и сбросил.
    """
    found = ResettableDevice()
    printer = DummyUsbPrinter(tmp_path / "missing.dylib", found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_NO_LIBUSB
    assert printer.find_calls == 0
    assert found.reset_calls == 0


def test_real_lazy_open_without_printer_is_not_found() -> None:
    """P3 п. 2, P4, P5: настоящий `CheckedUsb` без принтера на шине — `PRINTER_NOT_FOUND`.

    Так печать идёт у заказчика с выдернутым кабелем или выключенным
    принтером. Первая запись `CheckedUsb._raw` обращается к `self.device`,
    и escpos открывает устройство лениво: ищет его на шине с поставляемой
    libusb, не находит и бросает `DeviceNotFoundError`. Он обязан дойти до
    разбора сбоев P5 как есть: оператору — «Принтер не найден. Проверьте
    кабель USB и питание…», в `__cause__` — `DeviceNotFoundError`, принтер
    для сброса ищут ровно раз. Остальные тесты `CheckedUsb` кладут
    поддельное устройство прямо в `_device`, а `DeviceNotFoundError` у них
    бросает `Dummy`, — ленивое открытие настоящего `CheckedUsb` не идёт
    нигде. Поэтому не видны:
    - запись через `self._device` вместо `self.device`: открытие пропущено,
      `False.write` даёт `AttributeError`, и у заказчика не печатается
      ничего — «Принтер не ответил…» на каждой печати, а при выдернутом
      кабеле тоже он, а не «Принтер не найден…»;
    - обёртка любой ошибки записи в `ShortWriteError` («один класс для
      P5»): выдернутый кабель показывал бы «Проверьте ленту…» вместо
      «Проверьте кабель USB и питание…».
    Поиску escpos подходит ни одно устройство (`_NothingOnBus`), поэтому
    тест одинаков на любой машине, и в подключённый XP-160LL не уходит ни
    байта.
    """
    printer = _NothingOnBus(BUNDLED)
    # Без бэкенда печать кончилась бы на P3 п. 1, до открытия устройства.
    assert printer.backend is not None

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_NOT_FOUND
    assert isinstance(caught.value.__cause__, escpos.exceptions.DeviceNotFoundError)
    # Открытие действительно шло через `self.device`: escpos кладёт в
    # `_device` `None` — «открывали, устройства нет»; без обращения к
    # `self.device` там осталось бы `False`.
    assert len(printer.opened) == 1
    assert printer.opened[0]._device is None
    assert printer.find_calls == 1


@pytest.mark.parametrize("case", ["raster-usb-error", "first-write-not-found"])
def test_close_error_after_failure_keeps_printer_error(case: str) -> None:
    """P5, P7, P3 п. 6: ошибка `close()` после неудачной печати не меняет ответ оператору.

    Печать падает, и `close()` тоже бросает. У pyusb это не теория:
    закрытие освобождает интерфейсы через `release_all_interfaces`, а тот
    глотает только `USBError` — `NotImplementedError` бэкенда libusb1
    выходит наружу. Наружу из `print_image` обязана выйти `PrinterError` с
    текстом исходного сбоя по P5 и с ним же в `__cause__`; закрытие — ровно
    одно, найденный принтер сброшен ровно раз. Сбои (`case`): `USBError` на
    растре — `PRINTER_FAILED`; `DeviceNotFoundError` на первой записи —
    `PRINTER_NOT_FOUND`. `test_close_error_does_not_fail_print` роняет
    `close()` только после удачной печати, поэтому не видны:
    - `return` вместо `pass` в обработчике ошибки `close()` в `finally`:
      он отбрасывает `PrinterError`, и неудачная печать становится
      «Напечатано» с +1 к «напечатано» (P7);
    - `close()` без защиты в обработке сбоя, перед сбросом: наружу выходит
      голое исключение закрытия — ошибка сервера вместо текста § 4.3, и
      «ошибок печати» не прибавляется.
    """
    error: BaseException
    if case == "raster-usb-error":
        error, write, text = usb.core.USBError("x"), 3, PRINTER_FAILED
    elif case == "first-write-not-found":
        error, write, text = escpos.exceptions.DeviceNotFoundError("x"), 1, PRINTER_NOT_FOUND
    else:
        # Опечатка в имени случая не должна тихо дать «печать без сбоя».
        raise AssertionError(f"неизвестный случай {case!r}")
    found = ResettableDevice()
    printer = DummyUsbPrinter(BUNDLED, fail_on=(write, error), close_error=True, found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == text
    assert caught.value.__cause__ is error
    assert printer.opened[0].close_calls == 1
    assert printer.find_calls == 1
    assert found.reset_calls == 1


def test_usb_error_entity_not_found_is_failed() -> None:
    """P5: `USBError` «Entity not found» на записи — `PRINTER_FAILED`: текст выбирают по классу, а не по словам.

    Исключение делает сам pyusb — его перевод кода libusb в исключение
    (`usb.backend.libusb1._check`) для `LIBUSB_ERROR_NOT_FOUND` (−5):
    `USBError` «[Errno 2] Entity not found». libusb на macOS отвечает так
    на запись в конечную точку, которой нет среди захваченных интерфейсов.
    Принтер при этом на шине есть, и оператору нужно «Проверьте ленту,
    выключите и включите принтер…». P5 даёт `PRINTER_NOT_FOUND` только
    классу `DeviceNotFoundError` из escpos. Выбор текста по словам «not
    found» в сообщении прошёл бы все остальные тесты: у
    `DeviceNotFoundError` эти слова в тексте есть, а у их `USBError` — нет.
    Здесь он отправил бы оператора проверять кабель и питание вместо ленты.
    """
    with pytest.raises(usb.core.USBError) as made:
        usb.backend.libusb1._check(usb.backend.libusb1.LIBUSB_ERROR_NOT_FOUND)
    error = made.value
    # Предпосылки: это обычная `USBError` (не тайм-аут), с errno ENOENT, и в
    # её тексте действительно есть «not found» — иначе тест прошёл бы впустую.
    assert type(error) is usb.core.USBError
    assert error.errno == errno.ENOENT
    assert "not found" in str(error).lower()
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, error))

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error


# P3 п. 3–5: записи одной печати белой картинки 576×10 по порядку — ровно
# семь, по одной на команду. Растр — один кусок: заголовок `GS v 0` с
# `m = 0`, 72 байта на строку (0x48), 10 строк (0x0a) и 720 нулевых байт
# точек — у белого ни одной чёрной точки.
_WRITES_576X10 = [
    b"\x1b@",                                        # 1: ESC @ — сброс (P3 п. 3)
    b"\x1d!\x00",                                    # 2: GS ! 0 — одинарный шрифт (P3 п. 3)
    b"\x1dv0\x00\x48\x00\x0a\x00" + bytes(72 * 10),  # 3: растр (P3 п. 4)
    b"\x1bt\x00",                                    # 4: ESC t 0 — кодовая страница из `text` (P3 п. 5)
    b"\n\n",                                         # 5: подача из `text` (P3 п. 5)
    b"\x1bd\x06",                                    # 6: ESC d 6 — подача к ножу из `cut` (P3 п. 5)
    b"\x1dV\x00",                                    # 7: GS V 0 — отрез (P3 п. 5)
]


def test_print_576x10_is_seven_writes() -> None:
    """P3 п. 3–5: печать белой 576×10 — ровно семь записей `_WRITES_576X10`, по одной на команду.

    На этом держится `test_failure_on_every_write_fails_print`: он роняет
    каждую из семи записей по номеру и сверяет, что до сбоя принтер принял
    ровно записи перед ней. Лишняя, пропавшая или склеенная запись сдвинула
    бы номера, и сбой пришёлся бы не на ту команду, которую называет тест.
    """
    printer = _TimedUsbPrinter(BUNDLED)

    printer.print_image(_white(576, 10))

    assert [msg for _, msg in printer.opened[0].writes] == _WRITES_576X10


@pytest.mark.parametrize(
    "error_type", [usb.core.USBError, usb.core.USBTimeoutError, ShortWriteError, RuntimeError]
)
@pytest.mark.parametrize(
    "write",
    [
        pytest.param(1, id="1-esc-at"),
        pytest.param(2, id="2-gs-bang"),
        pytest.param(3, id="3-raster"),
        pytest.param(4, id="4-esc-t"),
        pytest.param(5, id="5-feed"),
        pytest.param(6, id="6-esc-d"),
        pytest.param(7, id="7-gs-v"),
    ],
)
def test_failure_on_every_write_fails_print(write: int, error_type: type) -> None:
    """P7, P5, P3 п. 3–5: сбой любой из семи записей печати любым классом — печать не успешна.

    Успех — принтер принял все данные, включая подачу и отрез (§ 1, P7;
    P3 п. 5: «без отреза печать не успешна»). Печать 576×10 — семь записей
    `_WRITES_576X10`; сбой заказан на записи `write` исключением
    `error_type`: `USBError`, `USBTimeoutError` (запись не принята за 10 с —
    так выглядит кончившаяся лента, P7), `ShortWriteError` (P4) и чужое
    `RuntimeError`. Каждый раз наружу — `PrinterError(PRINTER_FAILED)` с
    этим исключением в `__cause__`; принтер принял ровно записи до сбойной,
    после неё записей нет; закрытие, поиск и сброс найденного принтера — по
    одному (P3 п. 6, P5). Другие тесты роняют только записи 1 и 3 и отрез
    обычной `USBError` или короткой записью, поэтому не видны:
    - проглоченная ошибка подачи («подача — не печать, картинка уже
      принята»), записи 4–5: печать, чью подачу принтер не принял, оператор
      видел бы как «Напечатано», и «напечатано» +1;
    - проглоченный `USBTimeoutError` отреза («нож медленный»), записи 6–7:
      лента не отрезана или пуста, а оператор видит «Напечатано» и
      «напечатано» +1; зависший принтер без `PrinterError` не сбрасывается
      (P5).
    """
    error = error_type("x")
    found = ResettableDevice()
    printer = DummyUsbPrinter(BUNDLED, fail_on=(write, error), found=found)

    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    # Сбой пришёлся ровно на запись `write`: всё до неё принято, после неё
    # принтеру ничего не отправляли.
    assert printer.opened[0].output == b"".join(_WRITES_576X10[: write - 1])
    assert printer.opened[0].raw_calls == write
    assert printer.opened[0].close_calls == 1
    assert printer.find_calls == 1
    assert found.reset_calls == 1


@pytest.mark.parametrize("seen_by", ["connected", "failed-print"])
def test_find_error_does_not_reset_device_seen_earlier(seen_by: str) -> None:
    """P5: после сбоя печати сбрасывают только то, что нашёл этот поиск; упал поиск — сброса нет.

    P5: `d = self._find()`; `d` не `None` → `d.reset()`. Если сам поиск
    бросил исключение, `d` нет, и сбрасывать нечего. Принтер до этого уже
    находили (`seen_by`): опросом `connected()` (P6 — его зовёт каждый
    `GET /api/printer` страницы) или поиском после прошлой неудачной
    печати, где его и сбросили ровно раз. Порча «запомнить найденное
    устройство и сбросить его, когда поиск упал» проходит
    `test_foreign_find_or_reset_error_is_ignored`: там удачного поиска перед
    упавшим нет, и запоминать нечего. Здесь она дала бы сброс USB, которого
    P5 не велит, — устройству из прошлого поиска, может быть уже не тому,
    что на шине сейчас.
    """
    found = ResettableDevice()
    printer = DummyUsbPrinter(BUNDLED, fail_on=(3, usb.core.USBError("first")), found=found)
    if seen_by == "connected":
        assert printer.connected() is True
        resets = 0
    elif seen_by == "failed-print":
        with pytest.raises(PrinterError):
            printer.print_image(_white(576, 10))
        # Прошлая неудачная печать нашла принтер и сбросила его — ровно раз.
        resets = 1
    else:
        # Опечатка в имени случая не должна тихо пропустить «принтер уже видели».
        raise AssertionError(f"неизвестный случай {seen_by!r}")
    assert printer.find_calls == 1
    assert found.reset_calls == resets

    # Теперь печать падает снова, а поиск на шине бросает сам.
    error = usb.core.USBError("second")
    printer.fail_on = (3, error)
    printer.find_error = usb.core.USBError("find")
    with pytest.raises(PrinterError) as caught:
        printer.print_image(_white(576, 10))
    assert str(caught.value) == PRINTER_FAILED
    assert caught.value.__cause__ is error
    assert printer.find_calls == 2
    assert found.reset_calls == resets
