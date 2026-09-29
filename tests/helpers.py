"""Общие помощники тестов: пути репозитория, настоящие картинки и принтер без железа.

Картинки делает Pillow прямо в `tmp_path` (спецификация § 7.2): JPEG, MPO,
JPEG с поворотом EXIF, CMYK- и серый JPEG, PNG, обрезанный JPEG. Никаких
подделок Pillow — модуль `photoprint.folder` читает те же байты, что пришли
бы к заказчику с телефона или с флешки.

Принтер без железа (§ 7.2, задача 1.3): `UsbPrinter` в тестах меняет только
`_open` и `_find`. `_open` отдаёт `RecordingDummy` — настоящий `Dummy` из
escpos, который копит байты вместо записи в USB и по заказу бросает
исключение на нужной записи; `decode_raster` разбирает в этих байтах
растры `GS v 0`. В настоящий принтер тесты не пишут ни байта.

Принтер для API (§ 7.2, задача 1.4): `FakePrinter` встаёт на место
`Printer` целиком — граница подмены проходит по USB. Он запоминает
картинки, по заказу бросает `PrinterError` с заданным текстом и умеет
«застрять» посреди печати, пока тест не отпустит его событием: так тест
409 держит замок печати занятым ровно столько, сколько нужно (W5).

Запуск настоящего клиента (§ 7.2 «Настоящий uvicorn», задача 1.6):
`app_copy` кладёт копию `app/` в `tmp_path`, чтобы `run.lock` и `folder`
клиент писал туда, а не в репозиторий; `free_base` находит 20 свободных
портов подряд в срезе, который прогон pytest держит замком (`port_slice`,
задача 3.4a); `Proc` запускает `python -m photoprint` или ярлык в своей
группе процессов, без записи байт-кода, читает его вывод в фоне и ждёт
нужную строку; `get_json` спрашивает запущенный сервер мимо системного
прокси.

Установщик и ярлык (§ 7.2 «Установщик и ярлык», задача 1.7):
`build_tarball` собирает из рабочего дерева архив того же вида, что GitHub
отдаёт по адресу из I3; `run_install` выполняет установщик так же, как
команда из README (`curl … | bash`): скрипт идёт в `/bin/bash` через
stdin, `HOME` — каталог теста, а текущий каталог — другой каталог теста;
`link_test_deps` подкладывает в окружение установленного клиента путь к
пакетам тестового окружения вместо пропущенного шага «зависимости» (I6).

Следующие задачи дописывают сюда свои помощники.
"""
from __future__ import annotations

import fcntl
import json
import os
import random
import shutil
import signal
import socket
import struct
import subprocess
import sys
import sysconfig
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, Dict, List, Optional, Tuple, Union

from escpos.printer import Dummy
from PIL import Image

from photoprint.printer import PrinterError, UsbPrinter

# Корень репозитория: этот файл лежит в `<repo>/tests/`.
REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
# Поставляемая libusb рядом с кодом: её грузит бэкенд принтера (P2).
BUNDLED = APP / "libusb-1.0.0.dylib"


def make_jpeg(
    path: Path,
    width: int,
    height: int,
    *,
    color: Tuple[int, int, int] = (255, 255, 255),
    mode: str = "RGB",
    orientation: Optional[int] = None,
    noise: bool = False,
    quality: int = 95,
) -> Path:
    """Сохранить настоящий JPEG размера `width`×`height` и вернуть путь.

    `color` — заливка; `mode` — режим, в котором JPEG ляжет на диск
    (`"RGB"`, `"L"`, `"CMYK"`); `orientation` — значение тега EXIF 0x0112,
    `None` — тега нет вовсе; `noise=True` — случайные пиксели вместо заливки.
    """
    path = Path(path)
    if noise:
        # F6: шум почти не сжимается, файл выходит крупным — обрезка «до
        # половины» режет данные сканов, а заголовок остаётся целым, как у
        # файла, который ещё копируется в папку. Генератор с зерном — чтобы
        # каждый прогон давал одни и те же байты.
        pixels = random.Random(0).randbytes(width * height * 3)
        image = Image.frombytes("RGB", (width, height), pixels)
    else:
        image = Image.new("RGB", (width, height), color)
    if mode != "RGB":
        # Заливка задана в RGB, а в нужный режим переводит сам Pillow —
        # так CMYK- и серый JPEG получаются настоящими (F3: их надо принять).
        image = image.convert(mode)
    options = {"quality": quality}
    if orientation is not None:
        exif = Image.Exif()
        # 0x0112 — тег «Orientation», его же читает `check` (F3 п. 4).
        exif[0x0112] = orientation
        options["exif"] = exif.tobytes()
    # Формат указан явно: Pillow иначе выбирает его по расширению, а тестам
    # нужны и `Photo.JPEG`, и прочие имена (F1).
    image.save(path, "JPEG", **options)
    return path


def make_mpo(path: Path, width: int, height: int) -> Path:
    """Сохранить MPO — JPEG с двумя снимками: 0-й белый, 1-й чёрный.

    Так сохраняют снимки iPhone и камеры (F3 п. 3). Второй снимок чёрный,
    чтобы печать не того кадра была видна по первому же пикселю (F6).
    """
    path = Path(path)
    first = Image.new("RGB", (width, height), (255, 255, 255))
    second = Image.new("RGB", (width, height), (0, 0, 0))
    first.save(path, "MPO", save_all=True, append_images=[second], quality=95)
    return path


def make_png(path: Path, width: int, height: int) -> Path:
    """Сохранить белый PNG размера `width`×`height` и вернуть путь.

    Формат задан явно, поэтому PNG-байты можно положить и под именем `.jpg`:
    `check` обязан определить формат по содержимому, а не по имени (F3 п. 3).
    """
    path = Path(path)
    Image.new("RGB", (width, height), (255, 255, 255)).save(path, "PNG")
    return path


def truncate(path: Path, keep: int) -> Path:
    """Оставить в файле только первые `keep` байт и вернуть путь.

    Так выглядит JPEG, который ещё не докопировался в папку: заголовок цел,
    а данных сканов не хватает (F6, Review Focus 3).
    """
    path = Path(path)
    path.write_bytes(path.read_bytes()[:keep])
    return path


# Начало команды `GS v 0` — растровая картинка ESC/POS (P3 п. 4). За ним идут
# байт плотности `m` и два 16-битных числа little-endian: ширина в байтах и
# высота в строках.
RASTER_HEADER = b"\x1dv0"


def decode_raster(data: bytes) -> List[Tuple[int, int, bytes]]:
    """Найти в байтах печати все растры `GS v 0` и разобрать их по порядку.

    Каждый растр — заголовок `1d 76 30 m xL xH yL yH` и за ним `xbytes *
    height` байт точек, по биту на точку, 1 — чёрная. Возвращает список
    `(xbytes, height, payload)`. Так тест видит, на какие куски escpos
    разрезал картинку и какие точки ушли в принтер (P1, P3 п. 4).

    Растр, у которого точек меньше, чем обещает заголовок, — ошибка теста:
    значит, байты печати оборваны.
    """
    chunks: List[Tuple[int, int, bytes]] = []
    position = data.find(RASTER_HEADER)
    while position != -1:
        # Байт `m` (плотность, position + 3) здесь не разбирается: на точки
        # он не влияет. Что он равен 0 — высокая плотность по обеим осям,
        # умолчание escpos (P3 п. 4), — проверяет `test_print_image_byte_sequence`.
        xbytes, height = struct.unpack_from("<HH", data, position + 4)
        start = position + 8
        end = start + xbytes * height
        payload = data[start:end]
        assert len(payload) == xbytes * height, (
            f"растр на {position} оборван: {len(payload)} из {xbytes * height} байт"
        )
        chunks.append((xbytes, height, payload))
        # Поиск продолжается после точек, а не внутри них: точки картинки
        # могут случайно сложиться в те же три байта `1d 76 30`.
        position = data.find(RASTER_HEADER, end)
    return chunks


class RecordingDummy(Dummy):
    """`Dummy` из escpos, который копит байты печати и по заказу даёт сбой.

    Стоит на месте настоящего `CheckedUsb` (§ 7.2 п. 2): все записи идут
    через `_raw`, как и в USB, поэтому здесь видно всё, что ушло бы в
    принтер. Профиль — `TM-P80`, как у настоящего `_open` (P3 п. 2): ширина
    576 точек и полный отрез.

    `fail_on` — `(условие, исключение)` или `None`:
    - условие `int` — бросить исключение на N-м вызове `_raw`, считая с 1;
    - условие `bytes` — бросить на первом `_raw`, чьё сообщение начинается
      с этих байт.
    Исключение бросается один раз, и сообщение, на котором оно брошено, в
    `output` не попадает: принтер его не принял.

    `close()` считает вызовы в `close_calls`, а при `close_error=True`
    бросает `RuntimeError("close")` — так проверяется, что ошибка закрытия
    не отменяет успешную печать (P3 п. 6).
    """

    def __init__(
        self,
        fail_on: Optional[Tuple[Union[int, bytes], BaseException]] = None,
        close_error: bool = False,
    ) -> None:
        """Создать пустой приёмник байтов с профилем `TM-P80`."""
        super().__init__(profile="TM-P80")
        self.fail_on = fail_on
        self.close_error = close_error
        self.raw_calls = 0
        self.close_calls = 0
        self._fired = False

    def _raw(self, msg: bytes) -> None:
        """Принять одну запись; если она заказана как сбойная — бросить исключение."""
        self.raw_calls += 1
        if self.fail_on is not None and not self._fired:
            trigger, error = self.fail_on
            if isinstance(trigger, int):
                hit = self.raw_calls == trigger
            else:
                hit = msg.startswith(trigger)
            if hit:
                self._fired = True
                raise error
        super()._raw(msg)

    def close(self) -> None:
        """Сосчитать закрытие и, если заказано, бросить `RuntimeError("close")`."""
        self.close_calls += 1
        if self.close_error:
            raise RuntimeError("close")

    def __del__(self) -> None:
        """Не закрывать принтер при сборке мусора.

        `Escpos.__del__` зовёт `close()`. Тогда `close_calls` считал бы и
        закрытия сборщиком мусора, а не только явные из `print_image` (P3
        п. 6), а `RuntimeError("close")` из сборщика стал бы «неподнимаемым»
        исключением, которое pytest при `filterwarnings = error` роняет в
        чужом тесте.
        """


class DummyUsbPrinter(UsbPrinter):
    """`UsbPrinter` без железа: меняет только `_open` и `_find` (§ 7.2, § 7.3).

    Всё остальное — настоящее: `__init__` грузит libusb по пути `libusb`
    (P2), а `print_image` и `connected` работают как у заказчика.
    - `_open()` создаёт новый `RecordingDummy` с `fail_on` и `close_error`,
      кладёт его в `self.opened` и отдаёт — по списку видно, сколько раз
      открывали принтер и что в него ушло (P3 п. 2).
    - `_find()` прибавляет 1 к `self.find_calls`; бросает `find_error`, если
      он задан, иначе отдаёт `found` — «устройство на шине» или `None` (P6).
    """

    def __init__(
        self,
        libusb: Path,
        *,
        fail_on: Optional[Tuple[Union[int, bytes], BaseException]] = None,
        close_error: bool = False,
        found: Optional[object] = None,
        find_error: Optional[BaseException] = None,
    ) -> None:
        """Создать принтер с настоящей загрузкой libusb и поддельной шиной USB."""
        super().__init__(libusb)
        self.fail_on = fail_on
        self.close_error = close_error
        self.found = found
        self.find_error = find_error
        self.opened: List[RecordingDummy] = []
        self.find_calls = 0

    def _open(self) -> RecordingDummy:
        """Отдать новый `RecordingDummy` вместо `CheckedUsb` и запомнить его."""
        printer = RecordingDummy(fail_on=self.fail_on, close_error=self.close_error)
        self.opened.append(printer)
        return printer

    def _find(self) -> Optional[object]:
        """Сосчитать поиск на шине и отдать заданный исход вместо `usb.core.find`."""
        self.find_calls += 1
        if self.find_error is not None:
            raise self.find_error
        return self.found


class FakePrinter:
    """Поддельный принтер для тестов API: реализует `Printer` без USB (§ 7.2).

    API видит принтер только через протокол `Printer` (§ 3.1), поэтому здесь
    подменяется он целиком, а всё остальное — папка, картинки, счётчики,
    замок печати — в тестах настоящее.

    - `images` — картинки, которые API отдал в `print_image`, по порядку;
      картинка попадает сюда при каждом вызове, в том числе неудачном, —
      по списку видно, дошла ли печать до принтера (W4).
    - `error` — текст `PrinterError`, который бросит каждая печать; `None` —
      печать успешна. Атрибут изменяемый: тест снимает сбой на ходу и
      проверяет, что замок после сбоя отпущен (W5).
    - `hold=True` — печать ставит `started` и ждёт `release` (не дольше 5 с),
      то есть держит замок печати, пока тест шлёт второй запрос (тест 409).
    - `is_connected` — что ответит `connected()`; изменяемый, чтобы видеть,
      что API спрашивает принтер при каждом запросе.
    """

    def __init__(
        self,
        error: Optional[str] = None,
        hold: bool = False,
        connected: bool = True,
    ) -> None:
        """Создать принтер с заданным исходом печати и состоянием подключения."""
        self.error = error
        self.hold = hold
        self.is_connected = connected
        self.images: List[Image.Image] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def connected(self) -> bool:
        """Отдать заданное состояние подключения вместо поиска на шине USB (P6)."""
        return self.is_connected

    def print_image(self, image: Image.Image) -> None:
        """Запомнить картинку; при `hold` — ждать `release`; при `error` — бросить `PrinterError`."""
        self.images.append(image)
        if self.hold:
            # Тест 409: печать «идёт», пока тест не отпустит её. Предел 5 с —
            # чтобы упавший тест не повесил поток навсегда.
            self.started.set()
            self.release.wait(5)
        if self.error is not None:
            # Тот же класс и тот же текст, что у настоящего `UsbPrinter`
            # (P5): API показывает `str(exc)` как есть (W4 п. 4).
            raise PrinterError(self.error)


def app_copy(tmp_path: Path) -> Path:
    """Скопировать `app/` в `tmp_path / "root" / "app"` и вернуть путь копии.

    Клиент считает своим корнем родителя `app` (§ 4.5) и пишет туда
    `run.lock` (M2) и `folder` (M4). Запуск из копии держит эти файлы в
    `tmp_path` — в репозитории и в настоящем `~/.photoprint` их не будет
    (global: изоляция тестов). `__pycache__` не копируется: байт-код
    репозитория мог устареть, а копия должна исполнять ровно исходники.
    """
    target = tmp_path / "root" / "app"
    shutil.copytree(APP, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def _port_free(port: int) -> bool:
    """Узнать, можно ли занять `port` и на `127.0.0.1`, и на `0.0.0.0`.

    Проба строже M3: `bind` без `SO_REUSEADDR` на обоих адресах. Занятым
    считается и порт, который слушают на любом из адресов (его пропустил бы
    и клиент, M3), и порт с соединениями в `TIME_WAIT` после прошлого
    теста. Такой порт клиент занял бы и сам (M3 берёт его с
    `SO_REUSEADDR`), но тесту нужен диапазон, где ни одно соседство с
    прошлыми тестами не влияет на результат.
    """
    for host in ("127.0.0.1", "0.0.0.0"):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                return False
    return True


# Порты тестовых клиентов — 20000–40000, вне временных портов macOS (49152
# и выше); диапазон поделён на 16 срезов, по одному на прогон pytest.
PORT_LOW = 20000
PORT_HIGH = 40000
PORT_SLICES = 16
PORT_SLICE_SIZE = (PORT_HIGH - PORT_LOW) // PORT_SLICES
# Замки срезов — общие для всей машины, а не в `TMPDIR`: у каждого прогона
# свой `TMPDIR`, а делить срезы должны все прогоны из всех рабочих копий.
SLICE_LOCKS = Path("/tmp/photoprint-test-slices")
# Срез этого процесса и дескриптор его замка: берутся один раз и держатся до
# конца процесса pytest. Ядро снимает `flock` при любом выходе, даже при
# `kill -9`, а клиенты дескриптор не наследуют (PEP 446: `os.open` создаёт
# ненаследуемый дескриптор).
_slice: Optional[int] = None
_slice_fd: Optional[int] = None


def port_slice() -> int:
    """Вернуть номер среза портов этого процесса, 0–15, взяв на него замок `flock` на весь прогон.

    Срез — первый `n`, у которого удался `flock(LOCK_EX | LOCK_NB)` на
    `SLICE_LOCKS / f"{n}.lock"`; занятый замок держит другой прогон pytest,
    и срез пропускается. Так до 16 одновременных прогонов получают разные
    срезы. Номер процесса (`pid % 16`) у двух прогонов совпадал с
    вероятностью 1/16, и клиент одного прогона отвечал на опрос M2 другого
    (финальная проверка, 2026-09-28). Все 16 заняты → `AssertionError`.
    """
    global _slice, _slice_fd
    if _slice is None:
        SLICE_LOCKS.mkdir(parents=True, exist_ok=True)
        for n in range(PORT_SLICES):
            fd = os.open(SLICE_LOCKS / f"{n}.lock", os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            _slice, _slice_fd = n, fd
            break
        else:
            raise AssertionError(
                f"все {PORT_SLICES} срезов портов {PORT_LOW}–{PORT_HIGH} заняты другими прогонами pytest"
            )
    return _slice


def free_base(count: int = 20) -> int:
    """Найти случайное основание в 20000–40000, у которого `count` портов подряд свободны.

    Диапазон клиента — 20 портов от `PHOTOPRINT_PORT` (M3), поэтому
    свободными должны быть все, а не только первый. Основание случайное,
    чтобы соседние тесты не попадали на порты друг друга, ещё не
    отпущенные системой. Все порты лежат ниже 40000 — вне временных портов
    macOS (49152 и выше), которые система раздаёт исходящим соединениям.

    Основание берётся из своего среза диапазона — того, что прогон pytest
    держит замком (`port_slice`). Параллельные прогоны (рабочие копии,
    ворота, финальная проверка) иначе брали пересекающиеся диапазоны:
    клиент одного прогона отвечал на опрос M2 другого, и тест «никто не
    отвечает» получал «другая папка» (ворота 2, финальная проверка
    2026-09-28). Проверка `_port_free` на момент выбора этого не ловит:
    чужой клиент может встать в диапазон позже.
    """
    low = PORT_LOW + port_slice() * PORT_SLICE_SIZE
    for _ in range(200):
        base = random.randint(low, low + PORT_SLICE_SIZE - count)
        if all(_port_free(port) for port in range(base, base + count)):
            return base
    raise AssertionError(f"не нашлось {count} свободных портов подряд в 20000–40000")


class Proc:
    """Запущенный клиент (`python -m photoprint`) или ярлык с выводом, который читается в фоне.

    § 7.2 «Настоящий uvicorn»: сервер проверяется настоящим процессом.
    Каждый поток вывода читает свой фоновый поток: так дочерний процесс
    не встанет на полном канале, а тест ждёт нужную строку с тайм-аутом,
    а не висит на `readline`. После `returncode_within` и `stop` в
    `stdout` и `stderr` — весь вывод процесса.

    `popen` открыт тестам: задача 2.5 шлёт сигналы группе процесса
    (`os.killpg(proc.popen.pid, …)`).
    """

    def __init__(self, popen: subprocess.Popen) -> None:
        """Взять запущенный процесс и начать читать его stdout и stderr в фоне."""
        self.popen = popen
        self._out: List[str] = []
        self._err: List[str] = []
        self._out_closed = False
        # Одно условие на оба потока: `wait_for` просыпается и на новой
        # строке, и на закрытии stdout (процесс вышел — ждать нечего).
        self._changed = threading.Condition()
        self._readers = [
            threading.Thread(target=self._read, args=(popen.stdout, self._out, True), daemon=True),
            threading.Thread(target=self._read, args=(popen.stderr, self._err, False), daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    @classmethod
    def start(cls, cwd: Path, args: List[str], env_extra: Dict[str, str]) -> Proc:
        """Запустить процесс в каталоге `cwd` и вернуть `Proc`.

        - `args[0]` начинается с `-` — это аргументы Python тестового
          окружения (`["-m", "photoprint", …]`); иначе `args` — готовая
          команда, например `["/bin/bash", str(ярлык)]`.
        - Окружение — текущее, плюс `PHOTOPRINT_OPEN=echo` (§ 4.5: адрес
          печатается, а не открывается в браузере), плюс
          `PYTHONDONTWRITEBYTECODE=1`, плюс `env_extra`.
        - `TMPDIR`, если его нет в `env_extra`, — `<cwd>.parent / "tmp"`.
        """
        command = [sys.executable, *args] if args[0].startswith("-") else list(args)
        env = dict(os.environ)
        env["PHOTOPRINT_OPEN"] = "echo"
        # Байт-код не пишется (финальная проверка, 2026-09-28): Python из
        # Command Line Tools кладёт его в
        # `~/Library/Caches/com.apple.python/<полный путь>`, и каждая копия
        # `app/` в `tmp_path` оставляла бы там свой кэш навсегда — в
        # настоящем каталоге владельца. Готовый кэш библиотек читается
        # по-прежнему; код клиента от этого условия окружения не меняется
        # (§ 7.3).
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(env_extra)
        if "TMPDIR" not in env_extra:
            # escpos 3.1 при импорте делает `mkdtemp()` в `$TMPDIR`
            # (дополнение контролёра к задаче 1.6): свой `TMPDIR` внутри
            # каталога теста держит этот мусор в `tmp_path`, а не в
            # системной временной папке.
            tmpdir = Path(cwd).parent / "tmp"
            tmpdir.mkdir(parents=True, exist_ok=True)
            env["TMPDIR"] = str(tmpdir)
        popen = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            # Клиенту нечего читать с клавиатуры; пустой stdin не даёт ему
            # случайно ждать ввода от pytest.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            # Битый байт в выводе не должен ронять фоновый поток чтения:
            # вместо него придёт «�», и сравнение строк упадёт с понятным
            # выводом.
            errors="replace",
            # Своя сессия и группа процессов: сигналы, которые тест шлёт
            # группе (2.5, M7), не заденут сам pytest.
            start_new_session=True,
        )
        return cls(popen)

    def _read(self, stream: IO[str], sink: List[str], is_stdout: bool) -> None:
        """Читать строки `stream` в `sink`, пока канал не закроется, затем закрыть его."""
        try:
            for line in stream:
                with self._changed:
                    sink.append(line)
                    self._changed.notify_all()
        finally:
            # Незакрытый канал дал бы `ResourceWarning`, а `filterwarnings =
            # error` превратил бы его в ошибку чужого теста.
            stream.close()
            with self._changed:
                if is_stdout:
                    self._out_closed = True
                self._changed.notify_all()

    @property
    def stdout(self) -> str:
        """Весь прочитанный stdout одной строкой."""
        with self._changed:
            return "".join(self._out)

    @property
    def stderr(self) -> str:
        """Весь прочитанный stderr одной строкой."""
        with self._changed:
            return "".join(self._err)

    def _report(self) -> str:
        """Код выхода и весь вывод процесса — текст для сообщения упавшего теста."""
        return (
            f"код выхода: {self.popen.poll()}\n"
            f"--- stdout ---\n{self.stdout}"
            f"--- stderr ---\n{self.stderr}"
        )

    def wait_for(self, text: str, timeout: float = 15.0) -> List[str]:
        """Дождаться в stdout строки, равной `text`, и вернуть строки до неё.

        Строки сравниваются без завершающего `\\n` и возвращаются без него.
        Строки нет за `timeout` секунд или stdout закрылся без неё (процесс
        вышел) → `AssertionError` с кодом выхода и всем выводом.
        """
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                lines = [line.rstrip("\n") for line in self._out]
                if text in lines:
                    return lines[: lines.index(text)]
                left = deadline - time.monotonic()
                if self._out_closed or left <= 0:
                    break
                self._changed.wait(left)
        why = "stdout закрылся без неё" if self._out_closed else f"не пришла за {timeout} с"
        raise AssertionError(f"строка {text!r} {why}\n{self._report()}")

    def _join(self) -> None:
        """Дождаться, пока фоновые потоки дочитают вывод вышедшего процесса."""
        for reader in self._readers:
            reader.join(5)

    def returncode_within(self, seconds: float) -> int:
        """Дождаться выхода процесса не дольше `seconds` секунд и вернуть код выхода.

        Не вышел за это время → `AssertionError` с выводом; сам процесс
        остаётся живым, его останавливает `stop`.
        """
        try:
            code = self.popen.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"процесс не вышел за {seconds} с\n{self._report()}"
            ) from None
        self._join()
        return code

    def stop(self) -> None:
        """Остановить процесс: `SIGTERM`, до 5 с ожидания, затем `kill`.

        Уже вышедший процесс не трогается. Вызывается в финализаторе каждого
        теста: живой процесс держал бы порт, а `Popen` без `wait` дал бы
        `ResourceWarning`.
        """
        if self.popen.poll() is None:
            self.popen.send_signal(signal.SIGTERM)
            try:
                self.popen.wait(5)
            except subprocess.TimeoutExpired:
                self.popen.kill()
                self.popen.wait()
        self._join()


def get_json(url: str) -> Tuple[int, object]:
    """Сделать `GET url` мимо прокси и вернуть `(код, разобранный JSON)`.

    Ответ не 200 тоже разбирается: у отказов API есть JSON с `detail`
    (W6). Прокси выключен, как у самого клиента (§ 4.5): urllib на macOS
    иначе пошёл бы к `127.0.0.1` через системный прокси.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        finally:
            error.close()


# --- задача 1.7: установщик и ярлык ---

# Верхний каталог архива — такой же, как у архива ветки `main`, который
# GitHub отдаёт по адресу из I3 (`codeload…/tar.gz/refs/heads/main`):
# установщик срезает его `--strip-components=1`, и тест обязан проверить
# именно это.
TARBALL_TOP = "photoprint-main"
# Чего нет в git, а значит, и в архиве GitHub: байт-код Python и служебный
# файл Finder. В рабочем дереве они могут лежать, в архив не попадают.
TARBALL_SKIP = ("__pycache__", ".DS_Store")
# PATH установщика в тестах — только системные каталоги, как в Терминале
# у заказчика: ни тестового окружения, ни Homebrew. Установщик обязан
# обойтись системными `curl`, `tar`, `mktemp` и `xcode-select`.
INSTALL_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def build_tarball(dest: Path, *, include_app: bool = True) -> Path:
    """Собрать `dest / "photoprint.tar.gz"` из рабочего дерева и вернуть путь архива.

    Архив устроен как архив ветки на GitHub (§ 7.2 «Установщик и ярлык»):
    всё лежит под верхним каталогом `photoprint-main/`. Внутри —
    `install.sh`, `README.md` (если он уже есть) и `app/` без
    `__pycache__` и `.DS_Store`. `include_app=False` кладёт только
    `install.sh` — так выглядит архив, в котором нет кода клиента (I3).

    Каталог `dest` создаётся, если его нет; прежний архив в нём
    перезаписывается. Нет `install.sh` в рабочем дереве — `FileNotFoundError`.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    tarball = dest / "photoprint.tar.gz"

    def skip_junk(info: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        """Не класть в архив `__pycache__` и `.DS_Store`; каталог отбрасывается вместе с содержимым."""
        if os.path.basename(info.name) in TARBALL_SKIP:
            return None
        return info

    with tarfile.open(tarball, "w:gz") as archive:
        # Запись самого верхнего каталога есть и в архиве GitHub; без неё
        # `--strip-components=1` вёл бы себя на тестовом архиве иначе, чем
        # на настоящем.
        top = tarfile.TarInfo(TARBALL_TOP)
        top.type = tarfile.DIRTYPE
        top.mode = 0o755
        top.mtime = int(time.time())
        archive.addfile(top)
        archive.add(REPO / "install.sh", arcname=f"{TARBALL_TOP}/install.sh")
        if include_app:
            readme = REPO / "README.md"
            # README появится в задаче 3.1; до неё архив собирается без него.
            if readme.exists():
                archive.add(readme, arcname=f"{TARBALL_TOP}/README.md")
            archive.add(APP, arcname=f"{TARBALL_TOP}/app", filter=skip_junk)
    return tarball


def run_install(
    home: Path,
    tarball: Path,
    *,
    cwd: Path,
    script: Optional[bytes] = None,
    env_extra: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Выполнить установщик, как команду из README, и вернуть итог процесса.

    § 7.2: `curl … | bash` — это `/bin/bash`, которому скрипт приходит
    через stdin. Здесь то же самое: `["/bin/bash"]` с `input=script` (по
    умолчанию — байты `install.sh` рабочего дерева) в каталоге `cwd`;
    stdout и stderr собираются байтами, предел — 120 с.

    Окружение пустое, кроме:
    - `HOME` — каталог теста (global: настоящий `~/.photoprint` и
      `~/Desktop` тесты не трогают);
    - `PATH` — только системные каталоги (`INSTALL_PATH`);
    - `PHOTOPRINT_TARBALL` — `file://` на архив `tarball` вместо GitHub (I3);
    - `PHOTOPRINT_SKIP_PIP=1` — без скачивания пакетов (I6, § 7.2);
    - `TMPDIR` — `<cwd>.parent / "tmp"`;
    - затем `env_extra`, который может заменить любое из них.
    """
    if script is None:
        script = (REPO / "install.sh").read_bytes()
    env = {
        "HOME": str(home),
        "PATH": INSTALL_PATH,
        "PHOTOPRINT_TARBALL": Path(tarball).as_uri(),
        "PHOTOPRINT_SKIP_PIP": "1",
    }
    # Дополнение контролёра к задаче 1.7: временные файлы подпроцессов
    # установщика (например, escpos при импорте делает `mkdtemp()` в
    # `$TMPDIR`) остаются в каталоге теста, а не в системной временной
    # папке. Каталог — рядом с `cwd`, как у `Proc.start`, а не внутри:
    # тест проверяет, что в `cwd` установщик ничего не пишет.
    tmpdir = Path(cwd).parent / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(tmpdir)
    env.update(env_extra or {})
    return subprocess.run(
        ["/bin/bash"],
        input=script,
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=120,
    )


def link_test_deps(home: Path) -> None:
    """Подключить к окружению установленного клиента пакеты тестового окружения.

    § 7.2, сквозной тест: установщик в тестах пропускает шаг «зависимости»
    (`PHOTOPRINT_SKIP_PIP=1`), и в `HOME/.photoprint/venv` нет ни FastAPI,
    ни escpos. Файл `test-deps.pth` в его `site-packages` одной строкой
    называет `site-packages` текущего интерпретатора — Python того же
    3.9.6, — и клиент берёт пакеты оттуда (§ 7.3: подмена `test-deps.pth`
    разрешена).

    Каталог `site-packages` не создаётся: его должен был создать установщик
    (I5), и его отсутствие — ошибка установки, а не теста.
    """
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = Path(home) / ".photoprint" / "venv" / "lib" / version / "site-packages"
    (site_packages / "test-deps.pth").write_text(
        sysconfig.get_paths()["purelib"] + "\n", encoding="utf-8"
    )


# --- задача 2.1 ---
# Картинка с заголовком «любого» размера для F8: настоящий JPEG, в заголовке
# которого размеры переписаны, а пиксели остались от маленькой картинки.


def make_jpeg_header(path: Path, width: int, height: int) -> Path:
    """Сохранить JPEG, чей заголовок обещает `width`×`height`, и вернуть путь.

    Сама картинка — белый JPEG 576×8 (`quality=95`, baseline), в маркере
    начала кадра `FF C0` (SOF0) которого высота записана в байты `+5..+6`,
    а ширина — в `+7..+8` от `FF`, big-endian. Пиксели заголовку не
    соответствуют: файл нужен только `check`, который читает заголовок
    (F4), а предел Pillow (F8) срабатывает уже в `Image.open` — по размеру
    из заголовка. Так проверяются высоты, для которых настоящий JPEG весил
    бы десятки мегабайт.

    В SOF0 на каждый размер — два байта, поэтому больше 65 535 записать
    нельзя: `struct` бросит `struct.error`, и тест упадёт на подготовке, а
    не молча проверит другое число.
    """
    path = Path(path)
    # baseline задан явно: только у него кадр начинается маркером `FF C0`;
    # у прогрессивного JPEG был бы `FF C2`, и искать было бы нечего.
    Image.new("RGB", (576, 8), (255, 255, 255)).save(
        path, "JPEG", quality=95, progressive=False,
    )
    data = bytearray(path.read_bytes())
    # Идём по сегментам от SOI (`FF D8`, 2 байта), а не ищем `FF C0` по
    # всему файлу: так найден именно маркер кадра, а не случайная пара
    # байтов внутри таблиц. Сегмент — маркер (2 байта), длина (2 байта,
    # big-endian, включает сами байты длины) и данные.
    position = 2
    while True:
        assert data[position] == 0xFF, f"на {position} нет маркера JPEG"
        marker = data[position + 1]
        if marker == 0xC0:
            break
        # До кадра не может быть ни данных сканов (SOS, `FF DA`), ни конца
        # файла (EOI, `FF D9`): иначе Pillow сохранил не baseline.
        assert marker not in (0xD9, 0xDA), f"нет маркера FF C0 до {position}"
        (length,) = struct.unpack_from(">H", data, position + 2)
        position += 2 + length
    # SOF0: `FF C0`, длина (2), точность (1), высота (2), ширина (2).
    struct.pack_into(">HH", data, position + 5, height, width)
    path.write_bytes(bytes(data))
    return path


# --- задача 2.3 ---
#
# Проверка записи USB (P4, § 7.2 п. 3) и сброс принтера после сбоя (P5,
# § 7.2 п. 4). `FakeUsbDevice` встаёт на место устройства pyusb внутри
# настоящего `CheckedUsb` — подмена § 7.3 «поддельное устройство pyusb»:
# код escpos и `CheckedUsb._raw` работает так же, как у заказчика, только
# байты не уходят на шину. `ResettableDevice` — то, что `_find` отдаёт
# после сбоя печати: у него зовут только `reset()`.

# Импорт здесь, а не в начале файла: в волне A задача 2.3 только дописывает
# свой блок в конец `helpers.py` (карта конфликтов плана).
from photoprint.printer import CheckedUsb  # noqa: E402


class _FakeUsbContext:
    """Контекст ресурсов поддельного устройства: `dispose` ничего не делает.

    `CheckedUsb.close()` (это `close` из escpos `Usb`) освобождает
    устройство через `usb.util.dispose_resources(device)`, а та зовёт
    `device._ctx.dispose(device)`. Освобождать у поддельного устройства
    нечего, но вызов обязан пройти: иначе в тесте падало бы закрытие
    (P3 п. 6), а не проверяемая запись.
    """

    def dispose(self, device: object) -> None:
        """Ничего не делать: у поддельного устройства нет ресурсов libusb."""


class FakeUsbDevice:
    """Поддельное устройство pyusb для настоящего `CheckedUsb` (§ 7.2 п. 3, § 7.3).

    `write(ep, data, timeout)` отвечает, как `usb.core.Device.write`, —
    числом принятых байт. Обычно это `len(data)`, а на вызове номер
    `short_on` (считая с 1) — `len(data) // 2`, и без исключения: так pyusb
    отвечает, когда тайм-аут наступил посреди передачи (P4). `None` —
    все записи полные.

    Запоминает по порядку:
    - `writes` — сколько раз звали `write`, включая короткую запись;
    - `timeouts` — `timeout` каждого вызова: без явного тайм-аута pyusb
      берёт 1000 мс, а нужно `WRITE_TIMEOUT_MS` (P4);
    - `endpoints` — конечную точку каждого вызова: писать надо в `OUT_EP`;
    - `sent` — сообщение каждого вызова целиком, как его отдал `_raw`.

    `_ctx.dispose(dev)` ничего не делает — его зовёт закрытие (P3 п. 6).
    """

    def __init__(self, short_on: Optional[int] = None) -> None:
        """Создать устройство, которое примет половину записи номер `short_on`."""
        self.short_on = short_on
        self.writes = 0
        self.timeouts: List[int] = []
        self.endpoints: List[int] = []
        self.sent: List[bytes] = []
        self._ctx = _FakeUsbContext()

    def write(self, ep: int, data: bytes, timeout: int) -> int:
        """Запомнить запись и вернуть число «принятых» байт.

        У `timeout` нет значения по умолчанию, хотя у pyusb оно есть: запись
        без явного тайм-аута (P4) здесь сразу даёт `TypeError`, а не
        проходит молча.
        """
        self.writes += 1
        self.timeouts.append(timeout)
        self.endpoints.append(ep)
        self.sent.append(bytes(data))
        if self.writes == self.short_on:
            # P4: частичная передача — pyusb возвращает меньшее число без
            # исключения, и заметить её может только проверка в `_raw`.
            return len(data) // 2
        return len(data)


class ResettableDevice:
    """Принтер на шине, который `_find` отдаёт после сбоя печати (P5).

    У него зовут только `reset()`: вызов прибавляет 1 к `reset_calls` и
    бросает `error`, если он задан. Так выглядит принтер, который уже не
    отвечает и на сброс, — ошибку сброса P5 велит игнорировать.
    """

    def __init__(self, error: Optional[BaseException] = None) -> None:
        """Создать устройство, чей `reset()` бросит `error` (`None` — сброс удачный)."""
        self.error = error
        self.reset_calls = 0

    def reset(self) -> None:
        """Сосчитать сброс и, если задано, бросить `error`."""
        self.reset_calls += 1
        if self.error is not None:
            raise self.error


class CheckedDevicePrinter(UsbPrinter):
    """`UsbPrinter`, чей настоящий `CheckedUsb` пишет в `FakeUsbDevice` (§ 7.2 п. 3).

    `_open` — настоящий (`super()._open()`: `CheckedUsb` с тайм-аутом,
    конечными точками и профилем P3 п. 2), только устройство у объекта
    escpos уже «открыто» — в `_device` лежит `device`. Поэтому escpos не
    ищет принтер на шине, а `CheckedUsb._raw` пишет в поддельное
    устройство: так без железа проверяется сама проверка записи (P4).

    `_find` тоже подменён (§ 7.3): после сбоя печати P5 ищет принтер для
    сброса, и настоящий поиск пошёл бы на шину USB — подключённый XP-160LL
    тест сбросил бы по-настоящему. Здесь `_find` считает вызовы в
    `find_calls` и отдаёт `None` — «принтера на шине нет».
    """

    def __init__(self, libusb: Path, device: FakeUsbDevice) -> None:
        """Загрузить libusb по-настоящему и запомнить поддельное устройство."""
        super().__init__(libusb)
        self.device = device
        self.find_calls = 0

    def _open(self) -> CheckedUsb:
        """Построить настоящий `CheckedUsb` и вставить в него поддельное устройство."""
        printer = super()._open()
        # P3 п. 2: escpos открывает устройство лениво, пока `_device is
        # False`; готовое устройство в `_device` это открытие пропускает,
        # и первая же запись идёт в `device`.
        printer._device = self.device
        return printer

    def _find(self) -> Optional[object]:
        """Сосчитать поиск на шине и отдать `None` вместо `usb.core.find`."""
        self.find_calls += 1
        return None


# --- задача 2.4 ---
#
# Клиент API с адресом этого компьютера (W9). `TestClient` по умолчанию
# шлёт `Host: testserver`, а такой запрос W9 отклоняет с 403 как чужой:
# все тесты API идут через `client_for`, как браузер оператора — на
# `127.0.0.1`.

# Импорт здесь, а не в начале файла: в волне B задача 2.4 только дописывает
# свой блок в конец `helpers.py` (карта конфликтов плана).
from typing import Any  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def client_for(app: FastAPI, **kwargs: Any) -> TestClient:
    """Вернуть `TestClient` к `app`, который ходит на `http://127.0.0.1` (W9).

    Имя хоста в `Host` — `127.0.0.1`, как у страницы, которую запуск
    открывает в браузере оператора (M5), поэтому проверка W9 такой запрос
    пропускает. Порта в адресе нет: W9 сравнивает имя хоста без порта.
    Остальные параметры (`raise_server_exceptions=False` для W8) уходят в
    `TestClient` как есть.
    """
    return TestClient(app, base_url="http://127.0.0.1", **kwargs)
