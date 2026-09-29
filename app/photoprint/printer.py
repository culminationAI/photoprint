"""Принтер: печать картинки на XP-160LL по USB и проверка, есть ли он на шине.

Браузер до USB-принтера не достаёт, поэтому печатает сам сервер: картинку
из папки он переводит в чёрно-белые точки и шлёт командами ESC/POS через
python-escpos и pyusb (спецификация § 4.3). libusb поставляется вместе с
клиентом (`app/libusb-1.0.0.dylib`): у заказчика нет Homebrew, а значит, нет
и системной libusb.

Правила спецификации, которые держит модуль:
- P1 — `to_printable` дизерит картинку один раз на всю высоту, и escpos
  отдаёт эти точки принтеру бит в бит;
- P2 — бэкенд libusb грузится один раз, в `UsbPrinter.__init__`, только из
  поставляемого файла; в escpos он передаётся через `usb_args`, а поиск
  на шине всегда идёт с `backend=`;
- P3 — порядок одной печати: сброс, растр, подача, отрез, закрытие; новый
  объект escpos на каждую печать; ошибка `close()` печать не отменяет;
- P4 — `CheckedUsb._raw` пишет с тайм-аутом 10 с и сверяет число принятых
  байт с длиной сообщения: меньше — `ShortWriteError`;
- P5 — любой сбой печати после проверки libusb становится `PrinterError`:
  выдернутый кабель (`DeviceNotFoundError`) — `PRINTER_NOT_FOUND`, всё
  остальное — `PRINTER_FAILED`; перед этим принтер ищут на шине и
  сбрасывают, а ошибки поиска и сброса глотают;
- P6 — `connected()` отвечает, найден ли принтер на шине, и никогда не
  бросает исключение;
- P7 — успех печати значит, что принтер принял все данные; физическую
  печать клиент не подтверждает (следствия описаны в README).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Protocol

import escpos.escpos
import escpos.exceptions
import escpos.printer
import usb.backend.libusb1
import usb.core
from PIL import Image

# XP-160LL на шине USB: производитель, изделие, конечные точки записи и чтения.
VID, PID, OUT_EP, IN_EP = 0x0483, 0x5743, 0x01, 0x82
PROFILE = "TM-P80"             # в профиле ширина 576 px и полный отрез
WRITE_TIMEOUT_MS = 10_000      # на одну запись USB (кусок до ~69 КБ)

# Тексты для людей — дословно из § 4.3. Их показывает страница после
# неудачной печати, поэтому каждый говорит, что сделать оператору.
PRINTER_NOT_FOUND = "Принтер не найден. Проверьте кабель USB и питание, потом нажмите «Печать» ещё раз."
PRINTER_NO_LIBUSB = "Не загрузилась библиотека USB. Установите клиент заново командой из README."
PRINTER_FAILED = "Принтер не ответил. Проверьте ленту, выключите и включите принтер, потом нажмите «Печать» ещё раз."


class PrinterError(Exception):
    """Печать не удалась; `str(exc)` — один из трёх текстов § 4.3 для оператора.

    Исходное исключение USB или escpos лежит в `__cause__`: текст для
    людей один на целый класс сбоев, а подробности нужны только разработчику.
    """


class ShortWriteError(Exception):
    """Принтер принял меньше байт, чем ему отправили; `str(exc)` — f"{n} из {len(msg)}".

    Бросает её проверка записи `CheckedUsb._raw` (P4); `print_image` отдаёт
    оператору вместо неё `PRINTER_FAILED` (P5).
    """


class Printer(Protocol):
    """Что API нужно от принтера: есть ли он и напечатать картинку.

    API зависит от этого протокола, а не от `UsbPrinter` (§ 3.1): в тестах
    API на его место встаёт поддельный принтер без USB.
    """

    def connected(self) -> bool:
        """Есть ли принтер на шине прямо сейчас."""

    def print_image(self, image: Image.Image) -> None:
        """Напечатать картинку целиком; при неудаче бросить `PrinterError`."""


def to_printable(image: Image.Image) -> Image.Image:
    """Перевести картинку в чёрно-белые точки (режим `1`) того же размера (P1).

    На входе — картинка из `load_printable`: JPEG в режиме `L`, `RGB` или
    `CMYK`. Сначала серый, потом один дизеринг Флойда—Стейнберга на всю
    высоту.
    """
    # P1: дизерить надо здесь и сразу всю картинку. escpos режет картинку
    # выше 960 строк на куски и дизерит каждый кусок заново — на сером это
    # видимая полоса через каждые 12 см. Готовые точки режима `1` escpos
    # пропускает бит в бит, шва нет.
    return image.convert("L").convert("1")


def load_backend(libusb: Path) -> Optional[object]:
    """Загрузить бэкенд pyusb из файла libusb `libusb`; не вышло — `None` (P2).

    `None` значит «печатать нечем»: `print_image` тогда сразу даёт
    `PRINTER_NO_LIBUSB`, а `connected()` — `False`.
    """
    # P2: проверка файла стоит до pyusb. pyusb запоминает первую
    # загруженную libusb на весь процесс и дальше отдаёт её при любом
    # `find_library`, поэтому пропавший файл без этой проверки не заметить.
    if not libusb.is_file():
        return None
    try:
        # P2: `find_library` отдаёт только наш файл. Без него pyusb искал бы
        # системную libusb — на машине разработчика она есть (Homebrew), а у
        # заказчика нет.
        return usb.backend.libusb1.get_backend(find_library=lambda _: str(libusb))
    except Exception:
        # P2: любая неудача загрузки — «библиотеки нет»; pyusb сам ловит
        # почти всё, но печать не должна зависеть от того, что он пропустит.
        return None


class CheckedUsb(escpos.printer.Usb):
    """USB-принтер escpos с проверкой числа принятых байт на каждой записи (P4).

    От `escpos.printer.Usb` отличается только `_raw`. Объект строит
    `UsbPrinter._open` (P3 п. 2); открытие устройства, закрытие и все
    команды ESC/POS — из escpos без изменений.
    """

    def _raw(self, msg: bytes) -> None:
        """Отдать принтеру `msg` одной записью USB; принял не всё — `ShortWriteError` (P4).

        Текст ошибки — `"{n} из {len(msg)}"`: сколько байт принято и сколько
        отправлено. Первое обращение к `self.device` открывает устройство
        (P3 п. 2).
        """
        # P4: тайм-аут — явно, `WRITE_TIMEOUT_MS` из `_open`: без него pyusb
        # ждёт 1000 мс, и длинный кусок растра обрывался бы.
        n = self.device.write(self.out_ep, msg, self.timeout)
        # P4: при тайм-ауте посреди передачи pyusb возвращает меньшее число
        # без исключения, а `Usb._raw` из escpos это число не смотрит —
        # оборванная картинка считалась бы напечатанной.
        if n != len(msg):
            raise ShortWriteError(f"{n} из {len(msg)}")


class UsbPrinter:
    """Принтер XP-160LL на USB — реализация протокола `Printer`.

    Бэкенд libusb грузится один раз при создании и лежит в `self.backend`
    (P2); `None` — библиотека не загрузилась. Тесты меняют только `_open` и
    `_find` (§ 7.3): всё остальное работает так же, как у заказчика.
    """

    def __init__(self, libusb: Path) -> None:
        """Загрузить бэкенд из файла `libusb`; сам принтер при этом не открывается."""
        # P2: ровно одна загрузка на объект — pyusb всё равно держит первую
        # libusb до конца процесса, повторные попытки ничего не изменят.
        self.backend = load_backend(libusb)

    def _open(self) -> escpos.escpos.Escpos:
        """Создать объект escpos для одной печати (P3 п. 2); устройство ещё не открыто.

        Точка подмены в тестах: вместо USB они отдают `Dummy` из escpos.
        """
        # P2: бэкенд — только через `usb_args`: аргумент `backend=` escpos
        # 3.1 молча игнорирует и ищет принтер через системную libusb.
        # P3 п. 2: устройство escpos откроет лениво, при первой записи.
        return CheckedUsb(
            VID,
            PID,
            usb_args={"backend": self.backend},
            # P3 п. 2, P7: у escpos тайм-аут по умолчанию 0, и libusb ждёт
            # записи бесконечно — замолчавший принтер повесил бы печать
            # навсегда. 10 с хватает куску растра до ~69 КБ с запасом.
            timeout=WRITE_TIMEOUT_MS,
            in_ep=IN_EP,
            out_ep=OUT_EP,
            profile=PROFILE,
        )

    def _find(self) -> Optional[object]:
        """Найти XP-160LL на шине USB; нет его — `None` (P5, P6).

        Его зовут `connected()` (P6) и сброс после неудачной печати (P5).
        Точка подмены в тестах: они отдают заданный объект, `None` или
        исключение.
        """
        # P2: поиск на шине — только с нашим бэкендом. Поиск без него
        # загрузил бы системную libusb и закрепил её в процессе.
        return usb.core.find(idVendor=VID, idProduct=PID, backend=self.backend)

    def connected(self) -> bool:
        """Есть ли принтер на шине; ответ всегда `True` или `False` (P6)."""
        # P6: без libusb искать нечем — сразу «нет», шину не трогаем.
        if self.backend is None:
            return False
        try:
            return self._find() is not None
        except Exception:
            # P6: состояние принтера — только ответ «есть или нет»; сбой
            # опроса шины для оператора то же, что «принтера нет».
            return False

    def print_image(self, image: Image.Image) -> None:
        """Напечатать картинку и отрезать ленту (P3); неудача — `PrinterError`.

        Возврат без исключения значит, что принтер принял все данные,
        включая отрез: число принятых байт каждой записи сверяет
        `CheckedUsb._raw` (P4). Физическую печать это не подтверждает (P7).

        Разбор сбоев (P5, после P4): без libusb — сразу `PRINTER_NO_LIBUSB`,
        шину не трогаем. Любое исключение дальше — от `_open` до отреза,
        включая `to_printable` и `ShortWriteError` из P4, — сначала сброс
        найденного на шине принтера, потом `PrinterError` с исходным
        исключением в `__cause__`: `DeviceNotFoundError` —
        `PRINTER_NOT_FOUND`, всё остальное — `PRINTER_FAILED`.
        """
        # P3 п. 1, P5: без libusb до USB не доходим вовсе — ни печати, ни
        # поиска для сброса.
        if self.backend is None:
            raise PrinterError(PRINTER_NO_LIBUSB)
        # P3 п. 6: `finally` закрывает объект и после сбоя; `None` — сбой
        # случился в самом `_open`, объекта нет и закрывать нечего.
        printer: Optional[escpos.escpos.Escpos] = None
        try:
            # P3 п. 2: новый объект на каждую печать — старый после
            # неудачного открытия хранит `_device = None`, принтер на шине
            # больше не ищет и на записи даёт голое исключение
            # (`AttributeError` в `CheckedUsb._raw`, `AssertionError` в
            # `_raw` самого escpos).
            printer = self._open()
            # P3 п. 3: как в клиенте ресепшена — `ESC @` сбрасывает принтер
            # (без него шрифт уезжает в двойную ширину), `GS ! 0` ставит
            # одинарную ширину и высоту.
            printer._raw(b"\x1b@")
            printer._raw(b"\x1d!\x00")
            # P3 п. 4: умолчания escpos — растр `GS v 0` кусками по 960
            # строк, без центрирования: картинка и так шириной в головку (F3).
            printer.image(to_printable(image))
            # P3 п. 5: как в клиенте ресепшена — две пустые строки
            # выталкивают бумагу между головкой и ножом, пауза даёт
            # принтеру дотянуть ленту до отреза.
            printer.text("\n\n")
            time.sleep(0.3)
            # P3 п. 5: ошибка отреза не глотается — без отреза печать не
            # успешна.
            printer.cut()
        except Exception as exc:
            # P5: после обрыва посреди растра принтер может ждать
            # недостающие байты (P7) — сброс USB готовит его к следующей
            # печати.
            # Принтер ищут заново: у escpos после неудачного открытия
            # устройства нет вовсе.
            try:
                found = self._find()
            except Exception:
                # P5: поиск идёт по шине, которая только что отказала в
                # записи; его сбой не должен подменить ответ оператору.
                found = None
            if found is not None:
                try:
                    found.reset()
                except Exception:
                    # P5: принтер, не принявший запись, может не принять и
                    # сброс; оператору важен исходный сбой.
                    pass
            # P5: у выдернутого кабеля или выключенного принтера свой
            # текст — проверить кабель и питание; всё остальное значит, что
            # принтер есть, но данные не принял.
            if isinstance(exc, escpos.exceptions.DeviceNotFoundError):
                text = PRINTER_NOT_FOUND
            else:
                text = PRINTER_FAILED
            raise PrinterError(text) from exc
        finally:
            if printer is not None:
                try:
                    printer.close()
                except Exception:
                    # P3 п. 6: данные уже приняты или сбой уже сообщён —
                    # неудачное закрытие ни то, ни другое не меняет.
                    pass
