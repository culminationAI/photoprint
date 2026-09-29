"""Тесты запуска — `python -m photoprint --folder …` и `--check-printer` (спецификация § 4.5).

Закрепляют правила пуль 1 и 2:
- аргументы — обязательная взаимоисключающая группа `--folder` |
  `--check-printer`: без аргументов и с обоими сразу — usage и код 2
  (argparse);
- M1 — папка приводится к `resolve()`: и `..`, и символическая ссылка
  дают настоящий путь; не каталог (нет пути или это файл) — текст в
  stderr с приведённым путём, код 2, stdout пуст; петля символических
  ссылок и относительный путь при удалённом текущем каталоге (`resolve`
  не удался) — тот же текст с путём как передан;
- M2 — один клиент на машину: замок `flock` на `<root>/run.lock`. Второй
  запуск для той же папки открывает адрес первого (код 0), для другой —
  отказывает с папкой первого (код 1); замок держит процесс, который не
  отвечает, — после 5 с опроса текст и код 1. Первого второй ищет на всех
  20 портах диапазона и мимо прокси; замок берётся раньше выбора порта —
  занятые чужими программами остальные порты диапазона второму не мешают,
  а сам он, пока опрашивает, ни одного порта не держит; отказанный запуск
  `<root>/folder` не трогает. Чужая программа, которая отвечает в
  `/api/health` JSON, который не объект (пустой и непустой список,
  строка, `true`), или объектом без `"app": "photoprint"`, — не клиент;
  не JSON (HTML, не UTF-8), не HTTP или оборванный ответ — тоже, и опрос
  идёт к следующему порту без трассировки; своя папка, переданная
  символической ссылкой, — своя. «…не отвечает…» приходит через 5 с от
  первого опроса; соединение, которое принято и молчит (зависший клиент),
  опрос ждёт не дольше тайм-аута запроса, а начатый круг доходит до
  последнего порта и после 5 с. `run.lock` не открывается (любой
  `OSError`: `<root>` без права записи, каталог на месте файла) или
  `flock` на нём кончается ошибкой, а не «занято», — клиент запускается
  без замка;
- M3 — порт занят, если к `127.0.0.1:port` подключается `connect`;
  свободный занимается `bind` с `SO_REUSEADDR` (не `SO_REUSEPORT`: чужой
  сокет с ним встать на адрес клиента не может). Порт, который слушают на
  `127.0.0.1` или на `0.0.0.0`, пропускается, а порт в `TIME_WAIT` сразу
  после остановки клиента — нет: перезапуск встаёт на прежний порт. Порт,
  где проба `connect` кончается тайм-аутом (чужой `bind` без `listen`), а
  `bind` отказывает, тоже пропускается. Первый порт по умолчанию — 8766;
  в диапазоне ровно 20 портов — занятые 19 уводят сервер на 20-й, все
  20 — текст и код 1; сервер слушает только
  `127.0.0.1` (§ 6.3); выбранный сокет держится до uvicorn — чужой `bind`
  посреди старта его не отнимает;
- M4 — путь папки лежит в `<root>/folder` без перевода строки; запуск,
  отвергнутый M1, прежнюю запись не трогает; запись не удалась (отказ в
  правах или каталог на месте файла) — клиент всё равно запускается;
- M5 — наблюдатель дожидается своей папки в `/api/health`, открывает
  адрес командой `PHOTOPRINT_OPEN`, печатает «Готово…» один раз и
  выходит, даже если команда открытия кончилась ошибкой; запросы к
  `127.0.0.1` идут мимо прокси из окружения; сервер с уровнем лога
  `warning` не пишет в окно Терминала ни строки на запросы и остановку,
  но его предупреждения туда доходят; libusb грузится из `<app>`; ни
  импорт модуля запуска, ни запуск с ошибкой аргументов не грузят ни
  сервер, ни API, ни принтер — они грузятся внутри `main` после разбора
  аргументов; наблюдатель не держит процесс: Ctrl+C при работающем
  сервере до «Готово…» — код 130;
- M6 — `--check-printer` печатает одну строку о принтере (код 0): «найден»
  ровно тогда, когда поиск на шине нашёл устройство, а сбой поиска —
  «не найден»; без поставляемой libusb или с файлом, который не грузится,
  — отказ (код 1); замок и сервер не трогает;
- M7 — своих обработчиков сигналов нет: закрытие окна Терминала (SIGHUP),
  Ctrl+C (SIGINT) и SIGTERM останавливают клиент и освобождают порт и
  замок; Ctrl+C — код 130 без трассировки Python, и посреди импорта
  модулей при запуске, и во время опроса M2, и при двойном нажатии тоже —
  и в простое, и посреди печати; третий Ctrl+C, пока клиент после двух
  ждёт конца печати, завершает процесс сигналом сразу и молча.

Кроме того — имена с буквальным `%` и имя `..` на настоящем uvicorn (F7,
W2, Review Focus 5): `TestClient` starlette 0.48 декодирует путь дважды,
поэтому эти имена проверяет только настоящий сервер (§ 7.2).

Каждый тест запускает настоящий процесс `python -m photoprint` из копии
`app/` в `tmp_path` (`app_copy`): `<root>` клиента — `tmp_path / "root"`,
туда и пишутся `folder` и `run.lock`, а не в репозиторий. Подменены только
переменные окружения из § 7.3: `PHOTOPRINT_OPEN=echo` (адрес печатается в
stdout, браузер не открывается; в тесте сбоя открытия — `/usr/bin/false`,
браузер тоже не открывается), `PHOTOPRINT_PORT` — основание из
`free_base` (в тесте порта по умолчанию его нет вовсе), и в тестах прокси
— `http_proxy`/`HTTP_PROXY`. Принтер — настоящий `UsbPrinter` с
поставляемой libusb, и в USB не уходит ни байта: `--check-printer` только
ищет устройство на шине (P6), а печать вызывают лишь тесты Ctrl+C посреди
печати, и она идёт в `Dummy` из escpos. Точки подмены из § 7.3 — только в
подпроцессе клиента: чтобы проверить обе строки M6 и сбой поиска на
машине без принтера, два теста переопределяют `UsbPrinter._find`, а тесты
Ctrl+C посреди печати — `UsbPrinter._open` (`HELD_PRINT_CLIENT`). Порты
занимают настоящие слушающие сокеты, чужую программу на порту — настоящий
`http.server` в потоке теста или сокет, который рвёт соединения, замок —
настоящий процесс с `flock`, сигналы — настоящие, группе процесса клиента,
как их шлёт Терминал; сбой записи M4 — флаг `uchg` на настоящем файле или
настоящий каталог на месте файла, сбой замка M2 — `chmod 555` на `<root>`,
настоящий каталог или именованный канал (FIFO) на месте `run.lock` (§ 7.3);
окно старта до «Готово…» держит именованный канал на месте `<root>/folder`,
libusb, которая не грузится, — мусор вместо файла, сбой `resolve()` M1 —
удалённый текущий каталог.

Последний блок файла (задача 3.4a) закрепляет помощники самих тестов, чтобы
одновременные прогоны набора не давали ложных падений: разные срезы портов
у разных прогонов (`free_base`), общий замок на диапазон по умолчанию
(`default_ports_busy`), и процессы тестов не пишут байт-код в настоящий
`~/Library/Caches` (`Proc.start`).

Тексты для людей сверяются с дословными строками § 4.5 из этого файла, а
не с константами модуля: так расхождение текста в коде со спецификацией
тоже роняет тест.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import http.client
import http.server
import json
import os
import re
import signal
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

import pytest

from helpers import (
    APP,
    BUNDLED,
    PORT_LOW,
    PORT_SLICE_SIZE,
    PORT_SLICES,
    REPO,
    Proc,
    app_copy,
    free_base,
    get_json,
    make_jpeg,
)
from photoprint.printer import UsbPrinter

# Тексты § 4.5 дословно (M3, M5). Текст M1 тест собирает сам — он короткий.
PORTS_BUSY = (
    "Порты {first}–{last} на 127.0.0.1 заняты другими программами. "
    "Закройте лишние программы и запустите снова."
)
READY = "Готово. Страница открыта в браузере: {url}. Не закрывайте это окно, пока идёт работа."
# Ответ W2 на имя, которого нет среди записей `scan`: так видно, что 404
# дала ручка через `find` (F7), а не что-то до неё.
DOT_DOT_404 = {"detail": "файла «..» нет в папке"}
# Системный `lsof` macOS: по нему тест видит, что процесс клиента слушает и
# какие файлы держит, — снаружи, без правки кода клиента.
LSOF = "/usr/sbin/lsof"
# Сколько тест ждёт после «Готово…», прежде чем сверить весь вывод: 7,5
# пауз наблюдателя по 0,2 с (M5). Повтор «Готово…» или строка лога на
# опрос успели бы появиться.
QUIET_WAIT = 1.5
# Байты, которые не разбираются как HTTP-запрос: uvicorn 0.37 с h11 0.16
# (httptools в зависимостях нет) отвечает на них 400 и пишет в лог
# предупреждение (`h11_impl.py`, `handle_events`).
NOT_HTTP = b"NOT HTTP AT ALL\r\n\r\n"
# Строка этого предупреждения в stderr клиента. Два пробела после двоеточия:
# формат uvicorn — `%(levelprefix)s %(message)s`, а `levelprefix` —
# «WARNING:», дополненный пробелом до 9 знаков. Цветов нет: stderr —
# канал, а не Терминал.
INVALID_REQUEST_WARNING = "WARNING:  Invalid HTTP request received.\n"
# Сколько тест ждёт, пока предупреждение дойдёт до stderr: запись идёт в
# цикле событий сервера и может отстать от ответа 400.
WARNING_WAIT = 5.0
# Команда открытия, которая ничего не открывает и всегда выходит с кодом 1
# (M5): настоящая системная утилита — так у заказчика кончается `open` при
# сбое LaunchServices или сломанном браузере по умолчанию.
FAILING_OPEN = "/usr/bin/false"
# Пауза между проверками `<root>/folder` в тесте окна старта (M3, M5), с:
# окно между записью файла и стартом uvicorn — около 0,2 с по замеру задачи
# 1.8a (170–250 мс), а с отложенным импортом сервера, API и принтера после
# M4 (задача 2.5) — около 0,45 с; проба `bind` попадает в него с запасом.
START_POLL = 0.001

# Тексты § 4.5 дословно (M2, M6).
ALREADY_RUNNING = "Клиент уже запущен — открываю страницу."
OTHER_FOLDER = "Уже запущен клиент другой папки: {folder}. Закройте его окно и запустите снова."
NOT_RESPONDING = (
    "Клиент уже запущен в другом окне, но не отвечает. "
    "Закройте все окна «Печать картинок» и запустите снова."
)
NO_LIBUSB = "Не загрузилась библиотека USB — печать работать не будет."
PRINTER_FOUND = "Принтер XP-160LL найден"
PRINTER_MISSING = "Принтер не найден — проверьте кабель и питание. Клиент всё равно установлен."
# M7: код выхода после Ctrl+C — как у оболочки для процесса, прерванного
# SIGINT: 128 + 2.
CTRL_C_EXIT = 130
# M3: диапазон без `PHOTOPRINT_PORT` — 20 портов от 8766.
DEFAULT_FIRST = 8766
DEFAULT_LAST = 8785
# Замок диапазона по умолчанию на время теста порта по умолчанию. Путь общий
# для всей машины, а не в `TMPDIR`: у каждого прогона свой `TMPDIR`, а ждать
# друг друга должны все одновременные прогоны (финальная проверка,
# 2026-09-28).
DEFAULT_PORTS_LOCK = "/tmp/photoprint-test-default-ports.lock"
# M2: нижняя граница времени отказа «не отвечает», с. Клиент опрашивает
# 5 с; к ним ещё добавляется запуск Python и импорт модулей. Запас в 0,5 с
# — на округление паузы 0,2 с, если опрос считает шаги, а не часы.
LOCK_WAIT_MIN = 4.5
# Помощник, который держит замок M2 и молчит: `flock` на путь из
# `argv[1]` — так же, как клиент, — строка `locked` и 30 с сна. Ни одного
# порта он не слушает: для второго запуска это клиент, который завис.
HOLD_LOCK = (
    "import fcntl, os, sys, time\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "print('locked', flush=True)\n"
    "time.sleep(30)\n"
)
# Клиент с сигналами, как в окне Терминала (L3, M7): SIGINT и SIGHUP —
# действие по умолчанию, затем `exec` в `python -m photoprint` с теми же
# аргументами. Номер процесса и группа после `exec` те же. Действие по
# умолчанию `exec` сохраняет, но игнор (`SIG_IGN`) тоже наследуется:
# pytest, запущенный в фоне неинтерактивной оболочки (`pytest &`),
# игнорирует SIGINT, и клиент, запущенный напрямую, не заметил бы Ctrl+C.
# Код клиента обёртка не меняет — она воспроизводит Терминал (§ 7.3).
TERMINAL_EXEC = (
    "import os, signal, sys\n"
    "signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
    "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
    "os.execv(sys.executable, [sys.executable, '-m', 'photoprint'] + sys.argv[1:])\n"
)
# Проверка принтера (M6) с переопределённым `UsbPrinter._find` — точкой
# подмены из § 7.3: `argv[1]` `found` — поиск на шине находит устройство,
# `missing` — не находит (`None`). Дальше — тот же `main`, что у
# `python -m photoprint`, с `--check-printer`. Код клиента не меняется, а
# поставляемая libusb грузится по-настоящему: ветка «нет libusb» не
# берётся. Так обе строки M6 проверяются на любой машине — с принтером и
# без.
CHECK_PRINTER_WITH_FIND = (
    "import sys\n"
    "from photoprint.printer import UsbPrinter\n"
    "device = object() if sys.argv[1] == 'found' else None\n"
    "UsbPrinter._find = lambda self: device\n"
    "from photoprint.__main__ import main\n"
    "sys.exit(main(['--check-printer']))\n"
)
# M7: сколько тест двойного Ctrl+C ждёт, что сервер после первого Ctrl+C
# закроет слушающий сокет, с. Обычно — до 0,1 с: uvicorn замечает сигнал на
# очередном круге своего цикла.
LISTENER_CLOSE_WAIT = 5.0
# M7: пауза между нажатиями Ctrl+C посреди печати, с. Оператор нажал
# Ctrl+C, а окно молчит: uvicorn ждёт, пока закончится запрос печати
# (строку «Waiting for connections to close» скрывает уровень лога
# `warning`, M5), — и через полсекунды оператор жмёт снова.
PRINT_CTRL_C_GAP = 0.5
# Сколько тест ждёт метку клиента `HELD_PRINT_CLIENT` (`started`,
# `main-exited`), с: запуск Python и импорт модулей — до секунды, запас —
# на параллельные прогоны.
MARK_WAIT = 15.0
# Клиент, у которого печать идёт, пока тест её не отпустит (M7).
# - Сигналы — как у Python, запущенного из окна Терминала (L3): SIGINT —
#   `KeyboardInterrupt` (`default_int_handler`, его Python ставит сам при
#   старте с действием по умолчанию), SIGHUP — действие по умолчанию.
#   pytest в фоне неинтерактивной оболочки игнорирует SIGINT, и без этого
#   клиент унаследовал бы игнор (как в `TERMINAL_EXEC`).
# - Принтер — `Dummy` из escpos с профилем `TM-P80`, как у `RecordingDummy`,
#   через переопределённый `UsbPrinter._open` (§ 7.3): в USB не уходит ни
#   байта.
# - Первая запись печати кладёт в каталог меток `argv[1]` файл `started` и
#   ждёт файла `release`, не дольше 30 с: упавший тест не повесит процесс.
#   Пока ждёт, она следит за основным потоком: когда `main` вернул код и
#   интерпретатор на выходе ждёт незавершённые потоки
#   (`threading._shutdown`), основной поток уже «не жив», и появляется
#   метка `main-exited`.
# - Дальше — тот же `main`, что у `python -m photoprint`, с аргументами
#   `argv[2:]`; код клиента не меняется.
HELD_PRINT_CLIENT = (
    "import signal, sys, threading, time\n"
    "from pathlib import Path\n"
    "from escpos.printer import Dummy\n"
    "from photoprint.printer import UsbPrinter\n"
    "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
    "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
    "marks = Path(sys.argv[1])\n"
    "class HeldDummy(Dummy):\n"
    "    def _raw(self, msg):\n"
    "        if not (marks / 'started').exists():\n"
    "            (marks / 'started').touch()\n"
    "            deadline = time.monotonic() + 30\n"
    "            while not (marks / 'release').exists() and time.monotonic() < deadline:\n"
    "                if not threading.main_thread().is_alive():\n"
    "                    (marks / 'main-exited').touch()\n"
    "                time.sleep(0.01)\n"
    "        super()._raw(msg)\n"
    "UsbPrinter._open = lambda self: HeldDummy(profile='TM-P80')\n"
    "from photoprint.__main__ import main\n"
    "sys.exit(main(sys.argv[2:]))\n"
)
# Группа режимов в строке usage argparse: круглые скобки — группа
# обязательна, `|` — режимы исключают друг друга. Имя значения `--folder`
# (`FOLDER`, `PATH`) и порядок режимов спецификация не задаёт.
USAGE_GROUP = re.compile(r"\((--folder \S+ \| --check-printer|--check-printer \| --folder \S+)\)")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """Папка с картинками и в ней `good.jpg` 576×300; имя с пробелом и кириллицей, как у заказчика."""
    path = tmp_path / "Печать картинок"
    path.mkdir()
    make_jpeg(path / "good.jpg", 576, 300)
    return path


@pytest.fixture
def app(tmp_path: Path) -> Path:
    """Копия `app/` в `tmp_path / "root" / "app"`; её родитель — `<root>` клиента (§ 4.5)."""
    return app_copy(tmp_path)


@pytest.fixture
def base() -> int:
    """Первый порт диапазона клиента: все 20 портов от него свободны (M3)."""
    return free_base()


@pytest.fixture
def launch(app: Path, base: int) -> Iterator[Callable[..., Proc]]:
    """Дать тесту функцию `launch(*args, env_extra=None)`, запускающую клиент.

    Клиент — `python -m photoprint *args` в копии `app/` с
    `PHOTOPRINT_PORT=str(base)`; `env_extra` дополняет окружение. Каждый
    запущенный процесс останавливается в финализаторе, даже если тест
    упал: живой сервер держал бы порт.
    """
    started: List[Proc] = []

    def start(*args: str, env_extra: Optional[Dict[str, str]] = None) -> Proc:
        """Запустить `python -m photoprint *args` и запомнить процесс для остановки."""
        env = {"PHOTOPRINT_PORT": str(base)}
        env.update(env_extra or {})
        proc = Proc.start(app, ["-m", "photoprint", *args], env)
        started.append(proc)
        return proc

    yield start
    for proc in started:
        proc.stop()


@pytest.fixture
def launch_terminal(app: Path, base: int) -> Iterator[Callable[..., Proc]]:
    """Дать тесту функцию `launch_terminal(*args)`: клиент с сигналами, как в окне Терминала (M7).

    То же, что `launch`, но через `TERMINAL_EXEC`: SIGINT и SIGHUP у
    клиента — с действием по умолчанию, как у заказчика, как бы ни был
    запущен сам pytest. Каждый процесс останавливается в финализаторе.
    """
    started: List[Proc] = []

    def start(*args: str) -> Proc:
        """Запустить клиент через `TERMINAL_EXEC` с `PHOTOPRINT_PORT=str(base)` и запомнить процесс."""
        proc = Proc.start(app, ["-c", TERMINAL_EXEC, *args], {"PHOTOPRINT_PORT": str(base)})
        started.append(proc)
        return proc

    yield start
    for proc in started:
        proc.stop()


@pytest.fixture
def listen() -> Iterator[Callable[[str, int], socket.socket]]:
    """Дать тесту функцию `listen(host, port)`, занимающую порт слушающим сокетом.

    Сокет настоящий — `bind` и `listen`, как у чужой программы на этом
    порту (§ 7.3: сбои — занятыми сокетами). Все сокеты закрываются в
    финализаторе.
    """
    sockets: List[socket.socket] = []

    def occupy(host: str, port: int) -> socket.socket:
        """Занять `host:port` слушающим сокетом и вернуть его."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sockets.append(sock)
        sock.bind((host, port))
        sock.listen()
        return sock

    yield occupy
    for sock in sockets:
        sock.close()


@pytest.fixture
def lock_holder(app: Path) -> Iterator[Proc]:
    """Процесс `python -c`, который держит `flock` на `<root>/run.lock` и молчит (M2).

    Фикстура отдаёт процесс только после строки `locked`: к началу теста
    замок уже взят. В финализаторе процесс останавливается, даже если тест
    упал, — иначе он держал бы замок ещё 30 с.
    """
    holder = Proc.start(app, ["-c", HOLD_LOCK, str(app.parent / "run.lock")], {})
    try:
        holder.wait_for("locked")
        yield holder
    finally:
        holder.stop()


@contextlib.contextmanager
def _default_ports_held() -> Iterator[None]:
    """Взять замок `DEFAULT_PORTS_LOCK` и занять весь диапазон по умолчанию, 8766–8785 на `127.0.0.1`, слушающими сокетами (M3).

    Замок `flock` ждёт, пока другой прогон отпустит диапазон: пока один
    прогон держит порты, второй не занимает их вслед и не видит, как они
    освобождаются посреди его теста. Без замка второй прогон пропускал
    занятые первым порты, а когда первый их отпускал, клиент второго вставал
    на 8766 и тест ждал отказа 15 с зря (финальная проверка, 2026-09-28:
    2 из 60 при параллельных прогонах).

    Сокеты ставятся с `SO_REUSEADDR`: так они занимают и порт, который
    после прошлого прогона остался в `TIME_WAIT`. Для клиента такой порт
    свободен (M3), и без своего сокета тест ждал бы отказа зря. Если
    `bind` не удался и с `SO_REUSEADDR`, значит, `127.0.0.1:port` уже
    держит другая программа: клиенту этот порт тоже не достанется, и он
    пропускается. На выходе сокеты закрываются, и только потом снимается
    замок: следующий прогон берёт диапазон уже свободным. Дескриптор замка
    клиенты не наследуют (PEP 446), а упавший pytest замок не держит — ядро
    снимает его само.
    """
    lock_fd = os.open(DEFAULT_PORTS_LOCK, os.O_RDWR | os.O_CREAT, 0o666)
    held: List[socket.socket] = []
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        for port in range(DEFAULT_FIRST, DEFAULT_LAST + 1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                sock.listen()
            except OSError as error:
                sock.close()
                assert error.errno == errno.EADDRINUSE, f"порт {port}: {error}"
                continue
            held.append(sock)
        yield
    finally:
        for sock in held:
            sock.close()
        os.close(lock_fd)


@pytest.fixture
def default_ports_busy() -> Iterator[None]:
    """На время теста занять весь диапазон по умолчанию, 8766–8785 на `127.0.0.1`, слушающими сокетами, под общим замком (M3).

    Всё делает `_default_ports_held`: замок `DEFAULT_PORTS_LOCK`, затем
    сокеты с `SO_REUSEADDR` на каждый порт, который удалось занять.
    Параллельные прогоны этого теста ждут друг друга на замке, поэтому
    клиент каждого видит занятыми все 20 портов. Сокеты закрываются, а
    замок снимается в финализаторе, даже если тест упал.
    """
    with _default_ports_held():
        yield


@pytest.fixture
def held_print(tmp_path: Path, app: Path, folder: Path, base: int) -> Iterator[Tuple[Proc, Path]]:
    """Клиент `HELD_PRINT_CLIENT` на `base`, у которого печать `good.jpg` в полёте (M7).

    Фикстура отдаёт `(процесс, каталог меток)`, когда первая запись печати
    уже дошла до `Dummy` и ждёт (метка `started`): запрос печати идёт, и
    uvicorn его не закончил. Запрос — сырой `POST` без `Origin`, как у
    `curl` (W9 такой пропускает); тест держит соединение открытым и ответа
    не читает. В финализаторе — метка `release` (поток печати доделывает
    работу), сокет закрывается, процесс останавливается, даже если тест
    упал.
    """
    marks = tmp_path / "marks"
    marks.mkdir()
    proc = Proc.start(
        app,
        ["-c", HELD_PRINT_CLIENT, str(marks), "--folder", str(folder)],
        {"PHOTOPRINT_PORT": str(base)},
    )
    client: Optional[socket.socket] = None
    try:
        proc.wait_for(_ready(base))
        client = socket.create_connection(("127.0.0.1", base), timeout=5)
        request = (
            f"POST /api/print/good.jpg HTTP/1.1\r\nHost: 127.0.0.1:{base}\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        client.sendall(request.encode("ascii"))
        _wait_for_mark(marks / "started", proc)
        yield proc, marks
    finally:
        (marks / "release").touch()
        if client is not None:
            client.close()
        proc.stop()


def _url(port: int) -> str:
    """Адрес страницы клиента на `port` — тот, что клиент открывает и печатает (M5)."""
    return f"http://127.0.0.1:{port}/"


def _ready(port: int) -> str:
    """Строка «Готово…» для сервера на `port` (M5)."""
    return READY.format(url=_url(port))


def _relative(path: Path, app: Path) -> str:
    """Путь `path` относительно `app` — с `..`, ещё не приведённый.

    Клиент запускается с `cwd=app`, и такой путь указывает на ту же папку.
    `tmp_path` уже приведён, поэтому абсолютный путь не отличил бы код с
    `resolve()` от кода без него; относительный отличает (M1).
    """
    return os.path.relpath(path, app)


def _get_bytes(url: str) -> Tuple[int, bytes]:
    """Сделать `GET url` мимо прокси и вернуть `(код, тело байтами)`."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


def _raw_get(port: int, path: str) -> Tuple[int, bytes]:
    """Отправить `GET path` на `127.0.0.1:port` через `http.client` и вернуть `(код, тело)`.

    `http.client` отправляет путь как есть, без разбора и нормализации: так
    до сервера доходит буквальный `/api/images/..`.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _get_closed_by_server(port: int, path: str) -> bytes:
    """Отправить `GET path` с `Connection: close` на `127.0.0.1:port`, прочитать ответ до конца потока и вернуть его байты.

    Сокет теста закрывается только после конца потока — после FIN сервера.
    Так соединение первым закрывает сервер, и `TIME_WAIT` остаётся на его
    стороне, на `port`. urllib (`get_json`) дочитывает тело по
    `Content-Length` и закрывает свой сокет сразу, иногда раньше, чем
    пришёл FIN сервера. Тогда первым закрывает тест, `TIME_WAIT` уходит на
    его временный порт, и предпосылка «порт в `TIME_WAIT`» в тесте
    перезапуска (M3) под нагрузкой не выполнялась. `Host` с именем
    `127.0.0.1` — тот, что шлёт браузер на странице клиента.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        client.sendall(request.encode("ascii"))
        reply = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            reply += chunk
    return reply


def _closed_port() -> int:
    """Вернуть порт на `127.0.0.1`, который никто не слушает.

    Система выдаёт свободный порт на `bind(0)`, сокет сразу закрывается.
    Предпосылка проверяется: соединение с портом отклоняется.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        assert client.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED
    return port


def _lsof_names(pid: int, *selection: str) -> List[str]:
    """Вернуть имена открытых файлов процесса `pid` по `lsof` — поле `n` в порядке вывода.

    `selection` сужает выборку (например `-iTCP -sTCP:LISTEN` — только
    слушающие сокеты); `-a` требует всех условий сразу. `-n` и `-P`
    оставляют адреса и порты числами: `127.0.0.1:20000`, а не
    `localhost:…`. Для сокета имя — `адрес:порт`, для библиотеки — путь.
    """
    result = subprocess.run(
        [LSOF, "-nP", "-a", "-p", str(pid), *selection, "-Fn"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    # Код 1 у `lsof` — «ничего не нашлось»: для живого клиента это значит,
    # что проверять нечего, и тест должен упасть здесь с понятным текстом.
    assert result.returncode == 0, f"lsof: код {result.returncode}\n{result.stderr}"
    return [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]


def _lock_is_free(path: Path) -> bool:
    """Узнать, свободен ли замок M2 на `path`: взять `flock(LOCK_EX | LOCK_NB)` и сразу отпустить.

    Файл открывается без `O_CREAT`: его обязан был создать клиент, и
    проба не должна создавать его вместо клиента.
    """
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def _lock_released(path: Path) -> bool:
    """Узнать после выхода клиента, свободен ли замок M2: файла нет или `flock` на нём удаётся.

    § 4.5 M7 требует, чтобы замок освободился; удалять `run.lock` при
    выходе спецификация не требует и не запрещает. Если файла нет, новый
    запуск создаст его заново и возьмёт замок.
    """
    return not path.exists() or _lock_is_free(path)


def _assert_port_reusable(port: int) -> None:
    """Проверить, что `127.0.0.1:port` снова свободен для клиента — таким, каким его видит M3.

    Никто не слушает: `connect` отклонён (`ECONNREFUSED`). `bind` с
    `SO_REUSEADDR`, как у самого клиента, удаётся. Без `SO_REUSEADDR`
    проба была бы неверной: соединения, которые сервер закрыл сам (опросы
    наблюдателя, M5), остаются в `TIME_WAIT`, и `bind` отказал бы и после
    честной остановки. Пока клиент слушает `127.0.0.1:port`, такой `bind`
    отказывает (errno 48, `test_listens_on_loopback_only`), поэтому
    проверка не пустая.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        assert client.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


def _accept_health_poll(listener: socket.socket, timeout: float) -> socket.socket:
    """Дождаться на слушающем `listener` запроса `GET /api/health` и вернуть его соединение без ответа.

    Соединение, в котором такого запроса нет (проба `connect` без данных,
    M3), закрывается и пропускается. Запроса нет за `timeout` секунд →
    `AssertionError`. Ответа тест не шлёт: клиент ждёт его, пока тест
    держит соединение.
    """
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        assert left > 0, f"нет запроса /api/health за {timeout} с"
        listener.settimeout(left)
        try:
            connection, _ = listener.accept()
        except socket.timeout:
            continue
        connection.settimeout(max(deadline - time.monotonic(), 0.1))
        try:
            request = connection.recv(1024)
        except socket.timeout:
            request = b""
        if request.startswith(b"GET /api/health "):
            return connection
        connection.close()


def _wait_listener_closed(port: int, timeout: float = LISTENER_CLOSE_WAIT) -> None:
    """Дождаться, пока клиент на `port` перестанет принимать соединения: `connect` отклонён (M7).

    После первого Ctrl+C uvicorn на очередном круге своего цикла начинает
    остановку, и первое, что он делает, — закрывает слушающий сокет. Отказ
    в соединении (`ECONNREFUSED`) значит, что первый SIGINT обработан и
    остановка идёт. Проба — каждую миллисекунду; сокет пробы закрывается
    сразу. Порт не закрылся за `timeout` секунд → падение теста.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED:
                return
        time.sleep(0.001)
    pytest.fail(f"порт {port} не закрылся за {timeout} с после Ctrl+C")


def _wait_for_mark(mark: Path, proc: Proc) -> None:
    """Дождаться, пока клиент `HELD_PRINT_CLIENT` положит метку `mark` (M7).

    Метки нет за `MARK_WAIT` секунд → `AssertionError` с кодом выхода и
    всем выводом клиента: по ним видно, где он остановился.
    """
    deadline = time.monotonic() + MARK_WAIT
    while not mark.exists():
        assert time.monotonic() < deadline, (
            f"нет метки {mark.name} за {MARK_WAIT} с\n"
            f"код выхода: {proc.popen.poll()}\n"
            f"--- stdout ---\n{proc.stdout}--- stderr ---\n{proc.stderr}"
        )
        time.sleep(0.01)


def test_missing_folder_exits_2(tmp_path: Path, app: Path, launch: Callable[..., Proc]) -> None:
    """M1: папки нет → «папка не найдена: {путь}» в stderr, код 2, stdout пуст.

    Путь передан относительным (`../../nope`), а в тексте должен стоять
    путь после `resolve()` — абсолютный, без `..`.
    """
    missing = tmp_path / "nope"
    proc = launch("--folder", _relative(missing, app))

    assert proc.returncode_within(15) == 2, proc.stderr
    assert proc.stderr == f"папка не найдена: {missing.resolve()}\n"
    assert proc.stdout == ""


def test_folder_is_file_exits_2(folder: Path, launch: Callable[..., Proc]) -> None:
    """M1: `--folder` указывает на файл → это не каталог: текст в stderr, код 2, stdout пуст.

    Одного «путь существует» мало: файл вместо папки запустил бы сервер,
    и `/api/images` ответил бы ошибкой вместо понятного текста.
    """
    image = folder / "good.jpg"
    assert image.is_file()  # предпосылка: путь есть, но это файл
    proc = launch("--folder", str(image))

    assert proc.returncode_within(15) == 2, proc.stderr
    assert proc.stderr == f"папка не найдена: {image.resolve()}\n"
    assert proc.stdout == ""


def test_resolves_symlinked_folder(
    tmp_path: Path, app: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M1: папка, переданная символической ссылкой, приводится к настоящему пути.

    Ссылка — как псевдоним папки у заказчика. Сервер отвечает настоящим
    путём (`create_app` сам делает `resolve()`), поэтому наблюдатель (M5)
    дождётся своей папки, только если `main` тоже привёл путь; в
    `<root>/folder` (M4) лежит настоящий путь, а не ссылка.
    """
    link = tmp_path / "ссылка на папку"
    link.symlink_to(folder, target_is_directory=True)
    # Предпосылка: путь ссылки отличается от настоящего, иначе тест не
    # отличил бы код с `resolve()` от кода без него.
    assert str(link) != str(folder.resolve())
    proc = launch("--folder", str(link))

    proc.wait_for(_ready(base))
    assert get_json(_url(base) + "api/health") == (
        200,
        {"app": "photoprint", "folder": str(folder.resolve())},
    )
    assert (app.parent / "folder").read_text(encoding="utf-8") == str(folder.resolve())


def test_start_opens_browser_and_serves(
    app: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M5: наблюдатель открывает адрес командой `PHOTOPRINT_OPEN`, один раз печатает «Готово…», сервер молчит.

    До строки «Готово…» в stdout ровно адрес — его напечатал `echo`, то
    есть команда открытия запущена с адресом страницы. Сервер настоящий:
    `/api/health` отвечает своей папкой, `/api/images` — её картинкой,
    `/` — страницей из `<app>/static` (M5 «сервер»).

    Затем весь вывод до остановки сверяется целиком (M5): наблюдатель
    вышел после «Готово…» (повтор открыл бы у заказчика новую вкладку на
    каждом опросе), а сервер с `log_level="warning"` не пишет ни строки на
    запросы и остановку (при `info` на каждый опрос страницы в окне
    Терминала была бы строка).
    """
    proc = launch("--folder", str(folder))

    before = proc.wait_for(_ready(base))
    assert before == [_url(base)]
    assert get_json(_url(base) + "api/health") == (
        200,
        {"app": "photoprint", "folder": str(folder.resolve())},
    )
    status, body = get_json(_url(base) + "api/images")
    assert status == 200
    assert [image["name"] for image in body["images"]] == ["good.jpg"]
    assert body["images"][0]["ok"] is True
    assert _get_bytes(_url(base)) == (200, (app / "static" / "index.html").read_bytes())

    time.sleep(QUIET_WAIT)
    proc.stop()
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"
    assert proc.stderr == ""


def test_ready_even_if_open_command_fails(
    base: int, folder: Path, launch: Callable[..., Proc]
) -> None:
    """M5: команда открытия вышла с кодом 1 → «Готово…» с адресом всё равно напечатано, сервер отвечает, трассировки нет.

    `open` у заказчика может кончиться ошибкой: сбой LaunchServices,
    сломанный браузер по умолчанию. Сервер к этому моменту уже работает, и
    адрес в строке «Готово…» — единственное, по чему заказчик откроет
    страницу сам. Проверка кода выхода команды (`check=True`) уронила бы
    поток наблюдателя: в окне Терминала — трассировка Python
    `CalledProcessError`, строки с адресом нет, страница не открыта.

    Команда — настоящая `/usr/bin/false` через `PHOTOPRINT_OPEN` (§ 7.3);
    предпосылка проверяется: она и правда выходит с кодом 1. Весь вывод до
    остановки — одна строка «Готово…»: `false` ничего не печатает, а
    stderr пуст — трассировки нет.
    """
    # Предпосылка: без неё тест прошёл бы и у кода, который проверяет код
    # выхода, — команда открытия просто не кончалась бы ошибкой.
    assert subprocess.run([FAILING_OPEN]).returncode == 1
    proc = launch("--folder", str(folder), env_extra={"PHOTOPRINT_OPEN": FAILING_OPEN})

    assert proc.wait_for(_ready(base)) == []
    assert get_json(_url(base) + "api/health") == (
        200,
        {"app": "photoprint", "folder": str(folder.resolve())},
    )
    status, body = get_json(_url(base) + "api/images")
    assert status == 200
    assert [image["name"] for image in body["images"]] == ["good.jpg"]
    proc.stop()
    assert proc.stdout == _ready(base) + "\n"
    assert proc.stderr == ""


def test_server_warning_reaches_terminal(
    base: int, folder: Path, launch: Callable[..., Proc]
) -> None:
    """M5 «сервер»: при `log_level="warning"` предупреждение uvicorn доходит до окна Терминала — ровно одной строкой.

    `test_start_opens_browser_and_serves` держит уровень сверху: при
    `info` в окне была бы строка на каждый запрос. Этот тест держит его
    снизу. Пустой stderr прошёл бы и при `error` или `critical`, а при
    `critical` uvicorn молчит и о своих ошибках: «Exception in ASGI
    application» с трассировкой, сбой старта. Тогда «Клиент не запустился —
    причина в сообщениях выше.» (M5) указывала бы на пустое место.

    Сбой настоящий: после «Готово…» на порт клиента уходят байты, которые
    не разбираются как HTTP. Сервер отвечает 400 и пишет предупреждение.
    Весь stderr до остановки — ровно эта строка: при `error`/`critical`
    stderr пуст, при `info` в нём ещё и строки старта.
    """
    proc = launch("--folder", str(folder))
    proc.wait_for(_ready(base))

    with socket.create_connection(("127.0.0.1", base), timeout=5) as client:
        client.sendall(NOT_HTTP)
        # Ответ читается до закрытия: uvicorn шлёт 400 с
        # `connection: close` и сам закрывает соединение. Так начало ответа
        # не потеряется, даже если придёт несколькими кусками.
        reply = b""
        while True:
            chunk = client.recv(1024)
            if not chunk:
                break
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 400 "), reply

    deadline = time.monotonic() + WARNING_WAIT
    while proc.stderr == "" and time.monotonic() < deadline:
        time.sleep(0.05)
    proc.stop()
    assert proc.stderr == INVALID_REQUEST_WARNING


def test_listens_on_loopback_only(
    base: int, folder: Path, launch: Callable[..., Proc]
) -> None:
    """M3, § 6.3: сервер слушает только `127.0.0.1:{base}` — из сети печать недоступна.

    Две проверки снаружи процесса:
    - `lsof`: слушающие сокеты клиента — ровно `127.0.0.1:{base}`; у
      сокета на всех адресах было бы `*:{base}`;
    - сокет с `SO_REUSEADDR` не может занять `127.0.0.1:{base}`, пока там
      слушает клиент (errno 48). Поверх слушающего `0.0.0.0` macOS такой
      `bind` разрешает: отказ на `0.0.0.0` бывает только без
      `SO_REUSEADDR` (§ 8 «Сокеты и замки»), поэтому проба отличает
      `127.0.0.1` от всех адресов.
    """
    proc = launch("--folder", str(folder))
    proc.wait_for(_ready(base))

    assert _lsof_names(proc.popen.pid, "-iTCP", "-sTCP:LISTEN") == [f"127.0.0.1:{base}"]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError) as caught:
            probe.bind(("127.0.0.1", base))
    assert caught.value.errno == errno.EADDRINUSE


def test_port_not_shared_with_reuseport(
    base: int, folder: Path, launch: Callable[..., Proc]
) -> None:
    """M3: пока клиент слушает `127.0.0.1:{base}`, чужой сокет с `SO_REUSEPORT` этот адрес не займёт (errno 48).

    § 4.5 M3 называет именно `SO_REUSEADDR`. Если бы клиент ставил
    `SO_REUSEPORT`, macOS пустила бы на тот же `127.0.0.1:{base}` любую
    программу с тем же флагом, и та делила бы с клиентом соединения
    страницы или забирала их себе. Проба с `SO_REUSEADDR` из
    `test_listens_on_loopback_only` эту подмену не видит: ей ядро
    отказывает при любом из двух флагов у клиента.
    """
    proc = launch("--folder", str(folder))
    proc.wait_for(_ready(base))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        with pytest.raises(OSError) as caught:
            probe.bind(("127.0.0.1", base))
    assert caught.value.errno == errno.EADDRINUSE


def test_loads_libusb_from_app(
    app: Path, base: int, folder: Path, launch: Callable[..., Proc]
) -> None:
    """M5 «сервер», P2: `UsbPrinter` получает `<app>/libusb-1.0.0.dylib`, и процесс её загрузил.

    У заказчика нет другой libusb: не тот путь оставил бы бэкенд `None`, и
    печать всегда кончалась бы отказом «Не загрузилась библиотека USB…»
    (P3 п. 1). `lsof` видит загруженные библиотеки процесса; печать не
    вызывается, в принтер не уходит ни байта.
    """
    proc = launch("--folder", str(folder))
    proc.wait_for(_ready(base))

    names = _lsof_names(proc.popen.pid)
    assert [name for name in names if "libusb" in os.path.basename(name)] == [
        str(app / "libusb-1.0.0.dylib")
    ]


def test_skips_port_busy_on_loopback(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M3: первый порт слушает чужая программа на `127.0.0.1` → сервер на следующем."""
    listen("127.0.0.1", base)
    proc = launch("--folder", str(folder))

    proc.wait_for(_ready(base + 1))


def test_skips_port_busy_on_all_interfaces(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M3: порт, который слушают на `0.0.0.0`, тоже занят → сервер на следующем.

    Занятость видит проба `connect` к `127.0.0.1:port`: чужой `0.0.0.0`
    принимает и такое соединение. Одного `bind` с `SO_REUSEADDR` мало:
    macOS дала бы занять `127.0.0.1` поверх чужого `0.0.0.0` (§ 8
    «Сокеты и замки»), и порт делили бы две программы.
    """
    listen("0.0.0.0", base)
    proc = launch("--folder", str(folder))

    proc.wait_for(_ready(base + 1))


def test_uses_twentieth_port(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M3: заняты первые 19 портов → сервер на 20-м, `base + 19`, — он ещё в диапазоне.

    Закрепляет нижнюю границу длины диапазона: диапазон из 19 портов и
    меньше кончился бы на `base + 18`, и клиент сказал бы «порты заняты»,
    хотя свободный порт был. Верхнюю границу (не больше 20) держит
    `test_all_ports_busy`: там `base + 20` свободен, а ждут код 1.
    """
    for port in range(base, base + 19):
        listen("127.0.0.1", port)
    proc = launch("--folder", str(folder))

    proc.wait_for(_ready(base + 19))


def test_all_ports_busy(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M3: заняты все 20 портов диапазона → текст про порты в stdout, код 1."""
    for port in range(base, base + 20):
        listen("127.0.0.1", port)
    proc = launch("--folder", str(folder))

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout == PORTS_BUSY.format(first=base, last=base + 19) + "\n", proc.stderr


def test_port_taken_during_start_does_not_break_it(
    app: Path,
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M3, M5: сокет, занятый при выборе порта, и слушает сервер — чужая программа посреди старта его не отнимет.

    § 4.5 M3: первый удачный сокет не закрывается и уходит в uvicorn,
    «поэтому гонки „проверили — а занять не успели“ нет». Закрыть его и
    дать uvicorn занять `host`/`port` заново — обычный приём, но между
    закрытием и новым `bind` порт свободен. Чужая программа, занявшая его
    в эти доли секунды (`create_app`, загрузка libusb, старт
    uvicorn), — в пуле 1 это и вторая копия клиента от двойного двойного
    клика — оставила бы заказчику «Клиент не запустился…» вместо страницы.

    Момент выбран по `<root>/folder` (M4): файл появляется, когда порт уже
    выбран, а сервер ещё не слушает. Тест тут же пробует занять `base`
    слушающим сокетом, как чужая программа (§ 7.3: занятые сокеты).
    Держит клиент свой сокет — `bind` отказывает (errno 48), и сервер
    отвечает на `base`. Удалось занять — значит, клиент ещё не выбрал порт
    (M4 раньше M3 спецификация допускает: «перед стартом сервера»), и он
    обязан уйти на `base + 1`. В обоих случаях старт кончается «Готово…».
    """
    recorded = app.parent / "folder"
    # Предпосылка: файла нет до запуска — иначе проба `bind` ушла бы сразу,
    # раньше, чем клиент выбрал порт.
    assert not recorded.exists()
    proc = launch("--folder", str(folder))

    deadline = time.monotonic() + 15
    while not recorded.exists():
        # Клиент вышел, так и не записав файл, — ждать нечего: тест падает
        # сразу, с кодом выхода и выводом, а не через 15 с.
        assert proc.popen.poll() is None, f"клиент вышел до записи {recorded}\n{proc.stdout}{proc.stderr}"
        assert time.monotonic() < deadline, f"нет {recorded} за 15 с\n{proc.stdout}{proc.stderr}"
        time.sleep(START_POLL)
    try:
        listen("127.0.0.1", base)
    except OSError as error:
        assert error.errno == errno.EADDRINUSE, error
        port = base
    else:
        port = base + 1
    proc.wait_for(_ready(port))


def test_records_folder(
    app: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M4: к «Готово…» в `<root>/folder` лежит путь папки после `resolve()`, без `\\n`.

    Папка передана относительным путём: записан должен быть приведённый
    (M1) — по нему установщик найдёт перенесённую папку (I7).
    """
    proc = launch("--folder", _relative(folder, app))

    proc.wait_for(_ready(base))
    assert (app.parent / "folder").read_text(encoding="utf-8") == str(folder.resolve())


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_rejected_folder_keeps_recorded_folder(
    tmp_path: Path, app: Path, folder: Path, launch: Callable[..., Proc], kind: str
) -> None:
    """M4 «после M1»: запуск, отвергнутый M1 (пути нет; файл вместо папки), не трогает `<root>/folder`.

    В `<root>/folder` уже лежит путь папки, которую заказчик перенёс, —
    его записал прошлый удачный запуск. Запуск для пути, которого нет, или
    для файла кончается кодом 2, и запись остаётся прежней. Запись до
    проверки M1 подменила бы её плохим путём: при обновлении установщик
    (I1, I11) не нашёл бы по нему каталог и поставил бы ярлык в новую
    папку на Рабочем столе, а перенесённая папка с картинками была бы
    забыта.
    """
    recorded = app.parent / "folder"
    recorded.write_text(str(folder.resolve()), encoding="utf-8")
    if kind == "missing":
        bad = tmp_path / "нет такой папки"
        assert not bad.exists()  # предпосылка: пути нет
    else:
        bad = folder / "good.jpg"
        assert bad.is_file()  # предпосылка: путь есть, но это файл
    proc = launch("--folder", str(bad))

    assert proc.returncode_within(15) == 2, proc.stderr
    assert proc.stderr == f"папка не найдена: {bad.resolve()}\n"
    assert recorded.read_text(encoding="utf-8") == str(folder.resolve())


def test_ignores_proxy_settings(
    folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M5, § 4.5: `http_proxy`/`HTTP_PROXY` на закрытый порт не мешают старту.

    urllib по умолчанию послал бы запрос к `127.0.0.1` через этот прокси и
    не дождался бы `/api/health`; наблюдатель обязан ходить мимо прокси.
    Предпосылка: в окружении нет `no_proxy` — иначе urllib сам обошёл бы
    прокси, и тест прошёл бы и у кода с прокси.
    """
    bypass = {name: value for name, value in os.environ.items() if name.lower() == "no_proxy" and value}
    assert not bypass, f"в окружении {bypass}: тест прокси ничего не проверит"
    proxy = f"http://127.0.0.1:{_closed_port()}"
    proc = launch("--folder", str(folder), env_extra={"http_proxy": proxy, "HTTP_PROXY": proxy})

    proc.wait_for(_ready(base))


def test_percent_and_dot_names_on_real_server(
    folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """F7, W2, W3: имена с буквальным `%` отдаются, `..` — нет, на настоящем uvicorn.

    `100%.jpg` и `a%41.jpg` в адресе закодированы как `encodeURIComponent`
    (U8): `100%25.jpg`, `a%2541.jpg`. Сервер декодирует путь один раз и
    отдаёт байты именно этих файлов; двойное декодирование превратило бы
    `a%2541.jpg` в `aA.jpg`. Имя `..` — закодированное `%2E%2E` и буквальное
    в сыром пути — не совпадает ни с одной записью `scan`: 404 с текстом W2.
    """
    make_jpeg(folder / "100%.jpg", 576, 300, color=(0, 0, 0))
    make_jpeg(folder / "a%41.jpg", 576, 300, color=(128, 128, 128))
    proc = launch("--folder", str(folder))
    proc.wait_for(_ready(base))

    for name in ("100%.jpg", "a%41.jpg"):
        url = _url(base) + "api/images/" + quote(name, safe="")
        assert _get_bytes(url) == (200, (folder / name).read_bytes()), name
    assert get_json(_url(base) + "api/images/%2E%2E") == (404, DOT_DOT_404)
    status, body = _raw_get(base, "/api/images/..")
    assert status == 404
    assert json.loads(body.decode("utf-8")) == DOT_DOT_404


def test_no_arguments_exits_2_with_usage(launch: Callable[..., Proc]) -> None:
    """§ 4.5, argparse: без аргументов → usage с обоими режимами в stderr, код 2, stdout пуст.

    Режимы — обязательная взаимоисключающая группа `--folder` |
    `--check-printer`. В строке usage она стоит в круглых скобках через
    `|`, а отказ argparse называет оба режима. Ярлык (L3) и установщик (I8)
    всегда передают ровно один режим, поэтому пустой запуск — ошибка
    вызова: ни сервера, ни проверки принтера.
    """
    proc = launch()

    assert proc.returncode_within(15) == 2, proc.stderr
    assert proc.stdout == ""
    lines = proc.stderr.splitlines()
    assert lines[0].startswith("usage: "), proc.stderr
    # Длинную строку usage argparse переносит; пробельные знаки склеиваются
    # в один пробел, чтобы перенос не мешал найти группу.
    assert USAGE_GROUP.search(" ".join(proc.stderr.split())), proc.stderr
    assert "error: one of the arguments " in lines[-1], proc.stderr
    assert "--folder" in lines[-1] and "--check-printer" in lines[-1], proc.stderr
    assert lines[-1].endswith(" is required"), proc.stderr


def test_folder_and_check_printer_together_exit_2(
    folder: Path, launch: Callable[..., Proc]
) -> None:
    """§ 4.5, argparse: `--folder` и `--check-printer` вместе → отказ argparse, код 2, stdout пуст.

    Режимы исключают друг друга. Иначе один из них молча пропал бы (сервер
    без проверки принтера или проверка без сервера), и вызов с ошибкой
    выглядел бы удачным.
    """
    proc = launch("--folder", str(folder), "--check-printer")

    assert proc.returncode_within(15) == 2, proc.stderr
    assert proc.stdout == ""
    last = proc.stderr.splitlines()[-1]
    assert "not allowed with argument" in last, proc.stderr
    assert "--folder" in last and "--check-printer" in last, proc.stderr


def test_symlink_loop_exits_2(tmp_path: Path, app: Path, launch: Callable[..., Proc]) -> None:
    """M1: `--folder` — петля символических ссылок → «папка не найдена: {путь как передан}», код 2.

    `resolve()` на петле бросает `RuntimeError` (Python 3.9), и приведённого
    пути нет. Поэтому в тексте стоит путь ровно как передан: относительный,
    с `..`. Без обработки заказчик увидел бы трассировку Python и код 1.
    `<root>/folder` не появляется: M4 идёт после M1.
    """
    loop = tmp_path / "петля"
    loop.symlink_to(loop)
    # Предпосылка: это правда петля — `resolve()` в этом же Python падает.
    with pytest.raises(RuntimeError):
        loop.resolve()
    passed = _relative(loop, app)
    proc = launch("--folder", passed)

    assert proc.returncode_within(15) == 2, proc.stderr
    assert proc.stderr == f"папка не найдена: {passed}\n"
    assert proc.stdout == ""
    assert not (app.parent / "folder").exists()


def test_second_launch_same_folder_opens_first(
    folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M2: второй запуск для той же папки → «Клиент уже запущен — открываю страницу.», адрес первого, код 0.

    Второй запуск — повторный двойной клик по ярлыку, пока окно первого
    открыто. Копия `app/` та же, значит, и `<root>/run.lock` тот же: замок
    держит первый, и второй находит его по `/api/health` на `base`. Весь
    stdout второго — строка M2 и адрес первого, который напечатал `echo`
    (команда открытия). Свой сервер на `base + 1` второй не поднимает.
    Первый после этого работает как работал.
    """
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base))
    second = launch("--folder", str(folder))

    assert second.returncode_within(15) == 0, second.stderr
    assert second.stdout == ALREADY_RUNNING + "\n" + f"http://127.0.0.1:{base}/\n"
    assert second.stderr == ""
    assert first.popen.poll() is None
    assert get_json(_url(base) + "api/health") == (
        200,
        {"app": "photoprint", "folder": str(folder.resolve())},
    )


def test_second_launch_other_folder_refuses(
    tmp_path: Path, app: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M2: второй запуск для другой папки → «Уже запущен клиент другой папки: {папка первого}. …», код 1.

    Принтер один, поэтому и клиент на машине один. Второй не поднимает
    сервер и не открывает страницу первого: оператор ждал бы картинки своей
    папки, а увидел бы чужие. В тексте — папка первого, как её называет его
    `/api/health` (после `resolve()`).

    `<root>/folder` (M4) по-прежнему хранит папку работающего клиента.
    Отказанный запуск сервер не стартует и свою папку туда не пишет:
    иначе установщик при обновлении поставил бы ярлык в неё, а не в папку
    работающего клиента (I7).
    """
    other = tmp_path / "Другая папка"
    other.mkdir()
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base))
    second = launch("--folder", str(other))

    assert second.returncode_within(15) == 1, second.stderr
    assert second.stdout == OTHER_FOLDER.format(folder=str(folder.resolve())) + "\n"
    assert second.stderr == ""
    assert (app.parent / "folder").read_text(encoding="utf-8") == str(folder.resolve())
    assert first.popen.poll() is None


@pytest.mark.usefixtures("lock_holder")
def test_lock_held_by_silent_process(
    app: Path, folder: Path, launch: Callable[..., Proc]
) -> None:
    """M2: замок держит процесс, который не отвечает → «…но не отвечает. …», код 1 в пределах 10 с.

    Так выглядит зависший клиент: замок взят, а `/api/health` не отвечает
    ни на одном порту диапазона. Второй запуск не поднимает свой сервер
    (принтер один) и не ждёт вечно: заказчик должен узнать, что делать.

    Нижняя граница времени: клиент правда опрашивал около 5 с, а не сдался
    сразу. Первый клиент, который только что взял замок, отвечает не
    мгновенно, и без ожидания второй запуск назвал бы его зависшим.
    `<root>/folder` не появляется: сервер не стартовал.
    """
    started = time.monotonic()
    proc = launch("--folder", str(folder))

    assert proc.returncode_within(10) == 1, proc.stderr
    elapsed = time.monotonic() - started
    assert proc.stdout == NOT_RESPONDING + "\n"
    assert proc.stderr == ""
    assert elapsed >= LOCK_WAIT_MIN, f"отказ через {elapsed:.2f} с — опроса 5 с не было"
    assert not (app.parent / "folder").exists()


def test_second_launch_through_proxy(
    folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M2, § 4.5: `http_proxy`/`HTTP_PROXY` на закрытый порт у второго запуска не мешают найти первого.

    Опрос M2 обязан ходить к `127.0.0.1` мимо прокси, как наблюдатель M5.
    urllib по умолчанию послал бы его через прокси, не получил бы ответа за
    5 с и сказал бы «…не отвечает…», хотя первый клиент жив. Предпосылка: в
    окружении нет `no_proxy`, иначе urllib обошёл бы прокси сам.
    """
    bypass = {name: value for name, value in os.environ.items() if name.lower() == "no_proxy" and value}
    assert not bypass, f"в окружении {bypass}: тест прокси ничего не проверит"
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base))
    proxy = f"http://127.0.0.1:{_closed_port()}"
    second = launch("--folder", str(folder), env_extra={"http_proxy": proxy, "HTTP_PROXY": proxy})

    assert second.returncode_within(15) == 0, second.stderr
    assert second.stdout == ALREADY_RUNNING + "\n" + _url(base) + "\n"
    assert second.stderr == ""


def test_second_launch_finds_first_on_last_port(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M2: первый клиент на 20-м порту диапазона → второй находит его и открывает его адрес.

    § 4.5 M2: опрашиваются все порты диапазона. Первый ушёл на `base + 19`,
    потому что первые 19 держала чужая программа; к запуску второго она
    закрылась. Опрос одного `base` (или диапазона короче 20 портов) никого
    бы не нашёл и через 5 с выдал бы «…не отвечает…», хотя первый клиент
    жив. Чужие сокеты закрыты до второго запуска: на закрытом порту
    `connect` отклоняется сразу, и опрос не ждёт тайм-аут на каждом порту.
    """
    busy = [listen("127.0.0.1", port) for port in range(base, base + 19)]
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base + 19))
    for sock in busy:
        sock.close()
    second = launch("--folder", str(folder))

    assert second.returncode_within(15) == 0, second.stderr
    assert second.stdout == ALREADY_RUNNING + "\n" + _url(base + 19) + "\n"
    assert second.stderr == ""


def test_second_launch_when_rest_of_range_busy_opens_first(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M2 «перед M3»: остальные 19 портов диапазона заняты → второй запуск всё равно открывает первого, код 0.

    Первый клиент слушает `base`, а `base + 1 … base + 19` держит чужая
    программа. Замок берётся раньше выбора порта (M2; M3 — «с замком»), и
    второй запуск сразу уходит в опрос: первый отвечает ему на `base`. Если
    бы порт выбирался до замка, второй увидел бы занятыми все 20 портов и
    сказал бы «Порты … заняты» (код 1), хотя клиент жив и страницу можно
    открыть. Молчащие чужие порты опрос не ждёт: первый найден на `base`
    раньше них.
    """
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base))
    for port in range(base + 1, base + 20):
        listen("127.0.0.1", port)
    second = launch("--folder", str(folder))

    assert second.returncode_within(15) == 0, second.stderr
    assert second.stdout == ALREADY_RUNNING + "\n" + _url(base) + "\n"
    assert second.stderr == ""
    assert first.popen.poll() is None


def test_restart_right_after_stop_keeps_port(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    launch_terminal: Callable[..., Proc],
) -> None:
    """M3: перезапуск сразу после закрытия окна встаёт на тот же порт, хотя порт ещё в `TIME_WAIT`.

    Заказчик закрыл окно «Печать картинок» и тут же снова открыл ярлык.
    Соединения, которые сервер закрыл сам (опросы наблюдателя M5 и запрос
    теста), ещё 2 MSL (на macOS 30 с) стоят в `TIME_WAIT` на порту `base`.
    `bind` без `SO_REUSEADDR` на таком порту отказывает, и клиент ушёл бы
    на `base + 1`: вкладка браузера со старым адресом перестала бы
    работать. С `SO_REUSEADDR` (M3) клиент встаёт на `base`, и вкладка
    снова работает.

    Окно закрывается по-настоящему: SIGHUP группе процесса (M7).
    Предпосылка проверяется: после остановки `base` никто не слушает, но
    `bind` без `SO_REUSEADDR` отказывает — порт правда в `TIME_WAIT`.
    """
    first = launch_terminal("--folder", str(folder))
    first.wait_for(_ready(base))
    # Запрос, который сервер закрывает первым: `TIME_WAIT` остаётся у того,
    # кто закрыл первым, — здесь у сервера, на `base`.
    reply = _get_closed_by_server(base, "/api/health")
    assert reply.startswith(b"HTTP/1.1 200 "), reply
    os.killpg(first.popen.pid, signal.SIGHUP)
    first.returncode_within(5)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        assert client.connect_ex(("127.0.0.1", base)) == errno.ECONNREFUSED
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        with pytest.raises(OSError) as caught:
            probe.bind(("127.0.0.1", base))
    assert caught.value.errno == errno.EADDRINUSE
    second = launch("--folder", str(folder))

    second.wait_for(_ready(base))


@pytest.mark.usefixtures("default_ports_busy")
def test_default_first_port_is_8766(app: Path, folder: Path) -> None:
    """M3, § 4.5: без `PHOTOPRINT_PORT` диапазон — 8766–8785: все заняты → «Порты 8766–8785 …», код 1.

    Заказчик запускает клиент без переменных, и адрес страницы, который он
    видит и держит в закладках, — на 8766. Остальные тесты задают
    `PHOTOPRINT_PORT` и значения по умолчанию не видят. Клиент
    запускается не через `launch` (та ставит `PHOTOPRINT_PORT`), а
    предпосылка проверяет, что в окружении pytest этой переменной нет.

    Если другая программа держала порт диапазона во время подготовки и
    отпустила его до запуска клиента, клиент займёт этот порт и тест
    упадёт не из-за кода. Такой прогон нужно повторить.
    """
    assert "PHOTOPRINT_PORT" not in os.environ
    proc = Proc.start(app, ["-m", "photoprint", "--folder", str(folder)], {})
    try:
        code = proc.returncode_within(15)
    finally:
        proc.stop()

    assert code == 1, proc.stderr
    assert proc.stdout == PORTS_BUSY.format(first=DEFAULT_FIRST, last=DEFAULT_LAST) + "\n", proc.stderr


def test_start_despite_unwritable_folder_record(
    tmp_path: Path,
    app: Path,
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    chflags_to: Callable[[Path, int], None],
) -> None:
    """M4: запись `<root>/folder` не удалась → клиент всё равно запускается, без трассировки и без лишних строк.

    Файл `folder` нужен только установщику (I7), а заказчику нужна печать,
    поэтому `OSError` при записи не должен ронять запуск. Сбой настоящий
    (§ 7.3): на `<root>/folder` с путём прежней папки стоит флаг `uchg`
    (галочка «Защита» в Finder). Флаг запрещает и перезапись файла, и
    замену через `os.replace`, так что тест не зависит от способа записи.
    Каталог `<root>` остаётся доступным для записи: в нём клиент создаёт
    `run.lock` (M2).

    Итог: «Готово…» на `base`, сервер отвечает своей папкой. Весь вывод до
    остановки — как у обычного старта, stderr пуст. В файле остался
    прежний путь.
    """
    recorded = app.parent / "folder"
    old = str(tmp_path / "прежняя папка")
    recorded.write_text(old, encoding="utf-8")
    chflags_to(recorded, stat.UF_IMMUTABLE)
    # Предпосылка: запись правда не удаётся — флаг действует (не root).
    with pytest.raises(PermissionError):
        recorded.write_text(str(folder.resolve()), encoding="utf-8")
    proc = launch("--folder", str(folder))

    assert proc.wait_for(_ready(base)) == [_url(base)]
    assert get_json(_url(base) + "api/health") == (
        200,
        {"app": "photoprint", "folder": str(folder.resolve())},
    )
    proc.stop()
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"
    assert proc.stderr == ""
    assert recorded.read_text(encoding="utf-8") == old


def test_start_despite_folder_record_is_directory(
    app: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M4: на месте `<root>/folder` каталог → запись кончается `IsADirectoryError`, клиент всё равно запускается.

    § 4.5 M4 требует пережить любой `OSError` записи, а не только отказ в
    правах: диск полон (ENOSPC), том только для чтения (EROFS), каталог на
    месте файла (EISDIR). `test_start_despite_unwritable_folder_record`
    даёт `PermissionError`; здесь сбой другого рода — настоящий каталог
    (§ 7.3: сбой настоящий), и `IsADirectoryError` — `OSError`, но не
    `PermissionError`. Код, который ловит только отказ в правах, показал бы
    заказчику трассировку вместо страницы.

    Итог — как у обычного старта: «Готово…» на `base`, сервер отвечает
    своей папкой, весь вывод — адрес и «Готово…», stderr пуст. Каталог
    остался каталогом.
    """
    recorded = app.parent / "folder"
    recorded.mkdir()
    # Предпосылка: запись правда падает, и именно не отказом в правах.
    with pytest.raises(IsADirectoryError):
        recorded.write_text(str(folder.resolve()), encoding="utf-8")
    proc = launch("--folder", str(folder))

    assert proc.wait_for(_ready(base)) == [_url(base)]
    assert get_json(_url(base) + "api/health") == (
        200,
        {"app": "photoprint", "folder": str(folder.resolve())},
    )
    proc.stop()
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"
    assert proc.stderr == ""
    assert recorded.is_dir()


def test_check_printer_with_bundled_libusb(app: Path, launch: Callable[..., Proc]) -> None:
    """M6: `--check-printer` с поставляемой libusb → одна строка «…найден» или «…не найден…», код 0.

    Установщик вызывает эту проверку последним шагом (I8), когда принтер
    может быть и не подключён: оба ответа — код 0, клиент установлен.
    Проверка только ищет устройство на шине (P6) и ничего в него не пишет.
    Какой из двух ответов придёт, зависит от машины; тест принимает оба,
    но строка ровно одна и stderr пуст.

    Замок и сервер не трогаются: процесс выходит сам (сервер не вышел бы),
    а в свежей копии `<root>` не появились ни `run.lock`, ни `folder`.
    Установщик при обновлении запускает проверку и тогда, когда окно
    клиента открыто (I9 просит закрыть его уже после установки), и замок
    работающего клиента не должен ей мешать.
    """
    assert (app / "libusb-1.0.0.dylib").is_file()  # предпосылка: libusb на месте
    proc = launch("--check-printer")

    assert proc.returncode_within(15) == 0, proc.stderr
    assert proc.stdout.strip() in {PRINTER_FOUND, PRINTER_MISSING}
    assert proc.stdout in {PRINTER_FOUND + "\n", PRINTER_MISSING + "\n"}
    assert proc.stderr == ""
    assert not (app.parent / "run.lock").exists()
    assert not (app.parent / "folder").exists()


@pytest.mark.parametrize(
    ("bus", "line"), [("found", PRINTER_FOUND), ("missing", PRINTER_MISSING)], ids=["found", "missing"]
)
def test_check_printer_line_follows_find(app: Path, bus: str, line: str) -> None:
    """M6: строка `--check-printer` — ровно по ответу поиска на шине: устройство есть → «…найден», нет → «…не найден…», код 0.

    Это последняя строка, которую установщик показывает заказчику (I8).
    Переставленные строки сказали бы «Принтер не найден — проверьте
    кабель…» при подключённом принтере и «найден» — без него; строка «всегда
    найден» или «всегда не найден» тоже лгала бы в одном из двух случаев.
    `test_check_printer_with_bundled_libusb` принимает любую из двух строк,
    потому что шина у каждой машины своя. Здесь ответ шины задан
    переопределённым `UsbPrinter._find` (§ 7.3), остальное — настоящий
    `main` с поставляемой libusb (`CHECK_PRINTER_WITH_FIND`).
    """
    proc = Proc.start(app, ["-c", CHECK_PRINTER_WITH_FIND, bus], {})
    try:
        code = proc.returncode_within(15)
    finally:
        proc.stop()

    assert code == 0, proc.stderr
    assert proc.stdout == line + "\n"
    assert proc.stderr == ""


def test_check_printer_reports_bus_state(launch: Callable[..., Proc]) -> None:
    """M6: настоящий `python -m photoprint --check-printer` печатает строку, которая соответствует настоящей шине USB.

    Тест сам спрашивает шину тем же `UsbPrinter` с поставляемой libusb (P6:
    только поиск, в принтер ничего не пишется) и ждёт ровно ту строку, что
    соответствует ответу: без принтера (у разработчика) — «не найден», с
    принтером (приёмка 3.1) — «найден». Подмены нет: в отличие от
    `test_check_printer_line_follows_find`, здесь связаны настоящий запуск
    и настоящий поиск. В процессе pytest грузится та же libusb из
    репозитория, что у `test_printer.py` (P2: процесс запоминает первую
    загруженную libusb); копия в `app` — те же байты.
    """
    connected = UsbPrinter(BUNDLED).connected()
    proc = launch("--check-printer")

    assert proc.returncode_within(15) == 0, proc.stderr
    assert proc.stdout == (PRINTER_FOUND if connected else PRINTER_MISSING) + "\n"
    assert proc.stderr == ""


def test_check_printer_without_libusb(app: Path, launch: Callable[..., Proc]) -> None:
    """M6: `--check-printer` в копии `app/` без `libusb-1.0.0.dylib` → «Не загрузилась библиотека USB — …», код 1.

    Без поставляемой libusb бэкенд `None` (P2), и печать работать не
    будет. По коду 1 установщик считает шаг «библиотека USB» сбоем (I8).
    Файл удалён по-настоящему, из копии `app/`: системную libusb клиент
    не ищет (P2), поэтому вместо неё ничего не загрузится.
    """
    (app / "libusb-1.0.0.dylib").unlink()
    proc = launch("--check-printer")

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout.strip() == NO_LIBUSB
    assert proc.stdout == NO_LIBUSB + "\n"
    assert proc.stderr == ""


def test_sighup_stops_server_and_frees_lock_and_port(
    app: Path, folder: Path, base: int, launch_terminal: Callable[..., Proc]
) -> None:
    """M7, M2, M3: закрытие окна Терминала (SIGHUP группе процесса) останавливает клиент и освобождает порт и замок.

    Ярлык запускает клиент через `exec` (L3), и при закрытии окна SIGHUP
    приходит прямо в Python. Своих обработчиков у клиента нет, поэтому
    процесс завершается действием по умолчанию: код выхода `-SIGHUP`, за
    5 с. Обработчик, который глушит SIGHUP, оставил бы сервер жить без
    окна — заказчик не смог бы его закрыть.

    После остановки ярлык можно открыть снова: `base` никто не слушает, и
    клиент займёт его (`_assert_port_reusable`); `flock(LOCK_EX |
    LOCK_NB)` на `run.lock` удаётся. Предпосылка: пока клиент работает,
    замок занят. Иначе проверка «замок свободен» прошла бы и без
    остановки.
    """
    lock = app.parent / "run.lock"
    proc = launch_terminal("--folder", str(folder))
    proc.wait_for(_ready(base))
    assert lock.is_file(), f"клиент не создал {lock} (M2)"
    assert not _lock_is_free(lock), "работающий клиент не держит замок (M2)"

    os.killpg(proc.popen.pid, signal.SIGHUP)

    assert proc.returncode_within(5) == -signal.SIGHUP, proc.stderr
    assert proc.stderr == ""
    _assert_port_reusable(base)
    # Убитый сигналом процесс файл удалить не мог: `flock` проверяется на
    # том же `run.lock`, что держал клиент.
    assert _lock_is_free(lock)


def test_ctrl_c_stops_server(
    app: Path, folder: Path, base: int, launch_terminal: Callable[..., Proc]
) -> None:
    """M7: Ctrl+C (SIGINT группе процесса) → клиент завершается за 5 с с кодом 130, без трассировки Python.

    uvicorn перехватывает SIGINT, останавливает сервер и посылает сигнал
    снова. Python превращает его в `KeyboardInterrupt`, и без обработки в
    `main` заказчик увидел бы в окне Терминала трассировку. § 4.5 M7:
    исключение ловится, код выхода 130, как у оболочки для прерванной
    команды. Весь вывод — адрес и «Готово…», stderr пуст. Порт и замок
    после выхода свободны, как после SIGHUP.
    """
    lock = app.parent / "run.lock"
    proc = launch_terminal("--folder", str(folder))
    proc.wait_for(_ready(base))

    os.killpg(proc.popen.pid, signal.SIGINT)

    assert proc.returncode_within(5) == CTRL_C_EXIT, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"
    _assert_port_reusable(base)
    assert _lock_released(lock)


def test_double_ctrl_c_stops_server_without_traceback(
    app: Path, folder: Path, base: int, launch_terminal: Callable[..., Proc]
) -> None:
    """M7: Ctrl+C дважды подряд, второй — пока сервер останавливается, → код 130, без трассировки Python, порт и замок свободны.

    Нетерпеливый заказчик жмёт Ctrl+C два раза. Второй SIGINT приходит,
    пока uvicorn останавливается, и ставит `force_exit`, а с ним uvicorn
    пропускает `lifespan.shutdown()`. Будь lifespan включён, asyncio при
    выходе отменил бы висящую задачу lifespan, starlette ответил бы
    `lifespan.shutdown.failed`, и uvicorn написал бы в окно Терминала
    «ERROR: Traceback … CancelledError». § 4.5 M7 обещает Ctrl+C без
    трассировки, и двойное нажатие — тоже Ctrl+C. Весь вывод — адрес и
    «Готово…», stderr пуст.

    Второй SIGINT уходит сразу после того, как сервер перестал принимать
    соединения (`_wait_listener_closed`), а не через слепую паузу: так
    видно, что первый уже обработан (два сигнала не сольются в один), а до
    возврата обработчика SIGINT у uvicorn остаётся его пауза 0,1 с после
    закрытия сокета, с какой бы фазы цикла uvicorn ни началась остановка.
    Слепая пауза 50 мс оставляла от 55 до 105 мс запаса в зависимости от
    этой фазы (финальная проверка, 2026-09-28). Опоздавший второй сигнал —
    понятное падение теста, а не `PermissionError` из `killpg`.
    """
    lock = app.parent / "run.lock"
    proc = launch_terminal("--folder", str(folder))
    proc.wait_for(_ready(base))

    os.killpg(proc.popen.pid, signal.SIGINT)
    _wait_listener_closed(base)
    try:
        os.killpg(proc.popen.pid, signal.SIGINT)
    except (PermissionError, ProcessLookupError):
        # macOS отвечает EPERM на сигнал группе, чей процесс уже вышел, но
        # ещё не прибран (зомби), и ESRCH — прибранной.
        pytest.fail(f"второй Ctrl+C опоздал: клиент уже вышел — сбой теста, не клиента\n{proc._report()}")

    assert proc.returncode_within(5) == CTRL_C_EXIT, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"
    _assert_port_reusable(base)
    assert _lock_released(lock)


def test_double_ctrl_c_during_print_without_traceback(
    base: int, held_print: Tuple[Proc, Path]
) -> None:
    """M7: Ctrl+C дважды посреди печати (через 0,5 с) → код 130, без трассировки Python.

    Оператор нажал «Печать», потом Ctrl+C. uvicorn молча ждёт конца
    запроса печати — окно ничего не показывает, и оператор жмёт Ctrl+C ещё
    раз. Второй SIGINT ставит uvicorn `force_exit`: сервер больше не ждёт,
    и asyncio на выходе отменяет задачу запроса. uvicorn пишет эту отмену
    уровнем ERROR — «Exception in ASGI application» и трассировку до
    `CancelledError`, — и уровень `warning` пропустил бы её в окно
    Терминала. § 4.5 M7 обещает Ctrl+C без трассировки, и посреди печати
    тоже.

    Предпосылка проверяется меткой `main-exited`: `main` уже вернул код,
    пока печать всё ещё ждала теста, — второй Ctrl+C застал запрос в
    полёте, и запись об отмене, будь она напечатана, уже в stderr. Потом
    тест отпускает печать, и процесс выходит. Весь вывод — адрес и
    «Готово…».
    """
    proc, marks = held_print

    os.killpg(proc.popen.pid, signal.SIGINT)
    time.sleep(PRINT_CTRL_C_GAP)
    os.killpg(proc.popen.pid, signal.SIGINT)
    _wait_for_mark(marks / "main-exited", proc)
    (marks / "release").touch()

    assert proc.returncode_within(10) == CTRL_C_EXIT, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"


def test_triple_ctrl_c_during_print_ends_silently(
    base: int, held_print: Tuple[Proc, Path]
) -> None:
    """M7: третий Ctrl+C, пока клиент после двух ждёт конца печати, → процесс завершается сигналом SIGINT за 5 с, без вывода.

    После двух Ctrl+C посреди печати `main` вернул 130, но печать идёт в
    рабочем потоке AnyIO, а он не фоновый: интерпретатор на выходе ждёт
    его (`threading._shutdown`). Долгая печать или замолчавший принтер —
    и оператор жмёт Ctrl+C в третий раз. Обработчик SIGINT Python
    (`default_int_handler`) на этом месте бросил бы `KeyboardInterrupt`
    внутри `threading` — «Exception ignored in: <module 'threading'…>» и
    трассировку в окне Терминала.

    Своих обработчиков у клиента нет (M7), поэтому Ctrl+C здесь — действие
    по умолчанию: процесс завершается сигналом (код `-SIGINT`) сразу, не
    дожидаясь печати, и stderr пуст. Момент третьего нажатия выбран по
    метке `main-exited`: `main` уже вернул код, и интерпретатор ждёт поток
    печати. Печать тест не отпускает.
    """
    proc, marks = held_print

    os.killpg(proc.popen.pid, signal.SIGINT)
    time.sleep(PRINT_CTRL_C_GAP)
    os.killpg(proc.popen.pid, signal.SIGINT)
    _wait_for_mark(marks / "main-exited", proc)
    os.killpg(proc.popen.pid, signal.SIGINT)

    assert proc.returncode_within(5) == -signal.SIGINT, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"


def test_sigterm_stops_server(
    app: Path, folder: Path, base: int, launch_terminal: Callable[..., Proc]
) -> None:
    """M7: SIGTERM группе процесса → клиент завершается за 5 с этим сигналом (код `-SIGTERM`), stderr пуст, порт и замок свободны.

    SIGTERM шлёт `kill` без номера сигнала и система при выходе из учётной
    записи. Своих обработчиков у клиента нет (M7): uvicorn перехватывает
    SIGTERM, останавливает сервер и посылает сигнал себе снова, уже с
    прежним действием — по умолчанию, и процесс завершается этим сигналом.
    Проглоченный SIGTERM (`SIG_IGN`) дал бы код 0 после остановки сервера,
    а во время опроса M2 процесс не остановился бы вовсе. Весь вывод —
    адрес и «Готово…».
    """
    lock = app.parent / "run.lock"
    proc = launch_terminal("--folder", str(folder))
    proc.wait_for(_ready(base))

    os.killpg(proc.popen.pid, signal.SIGTERM)

    assert proc.returncode_within(5) == -signal.SIGTERM, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"
    _assert_port_reusable(base)
    assert _lock_is_free(lock)


@pytest.mark.usefixtures("lock_holder")
def test_ctrl_c_while_waiting_for_first_client(
    folder: Path,
    base: int,
    launch_terminal: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M7: Ctrl+C во время опроса M2 → тоже код 130, без трассировки и без текстов.

    § 4.5 M7: `KeyboardInterrupt` ловится во всём `main`, а не только
    вокруг сервера. Замок держит молчащий помощник, и клиент до 5 с
    опрашивает порты (M2). Заказчик, не дождавшись, жмёт Ctrl+C.

    Момент выбран по самому опросу: на `base` тест слушает сокетом, который
    принимает соединение и молчит. Как только клиент прислал на него
    `GET /api/health`, импорт позади, идёт M2 и клиент ждёт ответа — тут
    и приходит SIGINT. Ни «…не отвечает…», ни трассировки: вывод пуст.
    """
    silent = listen("127.0.0.1", base)
    proc = launch_terminal("--folder", str(folder))
    try:
        connection = _accept_health_poll(silent, 15)
    except AssertionError as error:
        raise AssertionError(f"{error} — опроса M2 нет\n{proc.stdout}{proc.stderr}") from None
    with connection:
        os.killpg(proc.popen.pid, signal.SIGINT)

        assert proc.returncode_within(5) == CTRL_C_EXIT, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""


# --- доработка контролёра по задаче 2.5 (M2 без замка, M5/M7 импорты, выжившие мутанты) ---

# M2: границы времени от первого опроса `/api/health` до выхода с «…не
# отвечает…», с. Опрос длится 5 с (`LOCK_WAIT`); начатый круг доходит до
# последнего порта, затем пауза 0,2 с и выход процесса — отсюда запас
# сверху. 4 с и 8 с опроса выходят за границы.
POLL_WAIT_MIN = 4.8
POLL_WAIT_MAX = 6.0


class _ForeignHealthHandler(http.server.BaseHTTPRequestHandler):
    """Чужая программа на порту диапазона: `GET /api/health` → 200 и тело `server.body` как JSON, остальное — 404 (M2, M3).

    Так отвечает любая программа заказчика, у которой тоже есть
    `/api/health`, но это не photoprint: тело задаёт тест.
    """

    def do_GET(self) -> None:
        """Ответить на `GET`: на `/api/health` — телом из сервера, на прочее — 404."""
        if self.path != "/api/health":
            self.send_error(404)
            return
        body: bytes = self.server.body  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Не писать журнал запросов: строка на каждый опрос клиента засорила бы вывод pytest."""


@pytest.fixture
def foreign_health() -> Iterator[Callable[[int, object], None]]:
    """Дать тесту функцию `foreign_health(port, data)`: чужой HTTP-сервер на `127.0.0.1:port` с `data` в `/api/health` (M2, M3).

    Сервер настоящий — `http.server` стандартной библиотеки в фоновом
    потоке теста (§ 7.3: занятые сокеты и настоящий HTTP-сервер на
    `127.0.0.1`); `data` уходит телом ответа в JSON. Каждый сервер
    останавливается и закрывает сокет в финализаторе, даже если тест упал:
    иначе он держал бы порт соседнего теста.
    """
    servers: List[Tuple[http.server.ThreadingHTTPServer, threading.Thread]] = []

    def serve(port: int, data: object) -> None:
        """Поднять чужой сервер на `127.0.0.1:port`, который на `/api/health` отвечает `data`."""
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _ForeignHealthHandler)
        server.body = json.dumps(data).encode("utf-8")  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        servers.append((server, thread))

    yield serve
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(5)


class _PollCloser:
    """Чужая программа на `127.0.0.1:port`, которая принимает соединение и сразу его закрывает (M2).

    Время первого запроса `GET /api/health` она запоминает в `first_poll`
    (часы `time.monotonic` процесса теста) и ставит событие `polled`. Ответа
    она не шлёт: для опроса M2 это «никто не ответил», как у зависшей
    программы, которая рвёт соединения.
    """

    def __init__(self, port: int) -> None:
        """Занять `127.0.0.1:port` слушающим сокетом и начать принимать соединения в фоновом потоке."""
        self.first_poll: Optional[float] = None
        self.polled = threading.Event()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen()
        # Короткий тайм-аут `accept` — чтобы поток замечал `close` теста.
        self._sock.settimeout(0.05)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """Принимать соединения, пока тест не позовёт `close`: прочитать запрос, отметить первый опрос, закрыть."""
        while not self._stop.is_set():
            try:
                connection, _ = self._sock.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(1)
                try:
                    request = connection.recv(1024)
                except OSError:
                    request = b""
                if request.startswith(b"GET /api/health ") and self.first_poll is None:
                    self.first_poll = time.monotonic()
                    self.polled.set()

    def close(self) -> None:
        """Остановить поток и закрыть слушающий сокет."""
        self._stop.set()
        self._thread.join(5)
        self._sock.close()


@pytest.fixture
def poll_closer(base: int) -> Iterator[_PollCloser]:
    """`_PollCloser` на `base`: рвёт каждое соединение и помнит время первого опроса M2; закрывается в финализаторе."""
    closer = _PollCloser(base)
    try:
        yield closer
    finally:
        closer.close()


def _assert_plain_start(proc: Proc, folder: Path, base: int) -> None:
    """Проверить, что клиент стартовал как обычно, и остановить его (M2 «без замка», M5).

    «Готово…» на `base`, сервер отвечает своей папкой. После остановки
    весь stdout — адрес и «Готово…», stderr пуст: ни трассировки, ни
    лишних строк.
    """
    assert proc.wait_for(_ready(base)) == [_url(base)]
    assert get_json(_url(base) + "api/health") == (
        200,
        {"app": "photoprint", "folder": str(folder.resolve())},
    )
    proc.stop()
    assert proc.stdout == f"{_url(base)}\n{_ready(base)}\n"
    assert proc.stderr == ""


def test_start_without_lock_when_root_unwritable(
    app: Path,
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    chmod_to: Callable[[Path, int], None],
) -> None:
    """M2: `run.lock` не открывается (`<root>` без права записи до первого запуска) → клиент запускается без замка.

    § 4.5 M2 (решение контролёра по задаче 2.5): без замка теряется только
    защита от второго окна, а трассировка вместо работы хуже — как при
    сбое записи M4. До задачи 2.5 такой клиент стартовал, и замок не
    должен это отнять. Сбой настоящий (§ 7.3): `chmod 555` на `<root>` в
    свежей копии, где `run.lock` ещё нет; права возвращаются в
    финализаторе. Запись `<root>/folder` (M4) тоже не удаётся — и это
    клиент переживает.

    Каталог `<root>/tmp` для `TMPDIR` клиента (`Proc.start`) создаётся до
    `chmod`: в каталоге без записи его не создать.
    """
    root = app.parent
    lock = root / "run.lock"
    assert not lock.exists()  # предпосылка: первый запуск, файла замка нет
    (root / "tmp").mkdir()
    chmod_to(root, 0o555)
    # Предпосылка: создать `run.lock` правда нельзя — права действуют (не root).
    with pytest.raises(PermissionError):
        os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    proc = launch("--folder", str(folder))

    _assert_plain_start(proc, folder, base)
    # Клиент правда шёл без замка, а не взял его как-то иначе.
    assert not lock.exists()
    assert not (root / "folder").exists()


def test_start_without_lock_when_lock_file_refuses_flock(
    app: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M2: `run.lock` открылся, но `flock` отказал не «занято», а ошибкой (`ENOTSUP`) → клиент запускается без замка.

    § 4.5 M2: `OSError` при `flock`, кроме `BlockingIOError` («замок
    занят»), — та же ветка «без замка», что и сбой `os.open`. Так бывает
    на томе без поддержки замков (сетевой диск). Сбой настоящий (§ 7.3,
    как каталог на месте файла у M4): на месте `<root>/run.lock` лежит
    именованный канал (FIFO). `os.open` с `O_RDWR` открывает его сразу, а
    `flock` на нём macOS отклоняет с `ENOTSUP`. Код, который считает любую
    ошибку `flock` «замок занят», 5 с опрашивал бы порты и сказал бы
    «…не отвечает…», хотя никакого клиента нет.
    """
    lock = app.parent / "run.lock"
    os.mkfifo(lock)
    # Предпосылка: `flock` на этом файле падает, и именно не «занято».
    fd = os.open(lock, os.O_RDWR)
    try:
        with pytest.raises(OSError) as caught:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    assert not isinstance(caught.value, BlockingIOError), caught.value
    assert caught.value.errno == errno.ENOTSUP
    proc = launch("--folder", str(folder))

    _assert_plain_start(proc, folder, base)
    assert stat.S_ISFIFO(os.stat(lock).st_mode)


def test_ctrl_c_during_startup_imports(
    app: Path, folder: Path, launch_terminal: Callable[..., Proc]
) -> None:
    """M7, M5: Ctrl+C в первые доли секунды запуска, посреди импорта модулей принтера и сервера → код 130, без трассировки и без текстов.

    Импорт uvicorn, FastAPI и escpos длится около 0,3 с — это самая долгая
    часть запуска до «Готово…». Заказчик, дважды кликнувший ярлык по
    ошибке, жмёт Ctrl+C сразу. Будь этот импорт в начале модуля, он шёл бы
    ещё до `try` в `main`: `KeyboardInterrupt` вышел бы из него
    трассировкой Python. § 4.5 M5: тяжёлые импорты — внутри `main`, и
    Ctrl+C здесь — тот же M7, что и во время работы.

    Момент — по настоящему следу импорта, без правки кода: escpos 3.1 при
    импорте делает `mkdtemp()` в `$TMPDIR` (у клиента из `Proc.start` —
    `<root>/tmp`) и открывает там на запись
    `<TMPDIR>/tmp*/3.9.6.capabilities.pickle` прямо перед примерно 0,1 с
    разбора таблицы возможностей принтеров на чистом Python
    (`yaml.safe_load`); за ним ещё грузятся pyusb, Pillow и FastAPI. Как
    только файл появился, тест шлёт SIGINT. Предпосылка — пустой stdout:
    сигнал пришёл до «Готово…», то есть правда посреди запуска.

    Не раньше, по самому каталогу `mkdtemp`: в следующие ~2 мс escpos
    грузит `importlib_resources` и `zipp`, и там CPython 3.9 изредка теряет
    или подменяет `KeyboardInterrupt`. Под параллельной нагрузкой тест с
    тем следом падал в 7 из 480 прогонов (финальная проверка, 2026-09-28)
    и в 3 из 480 (задача 3.4a), с этим — 0 из 480 оба раза. Узнать такой
    сбой, если он всё же повторится, можно по одному из трёх следов:
    - «Exception ignored in: <function _get_module_lock.<locals>.cb …>» с
      `KeyboardInterrupt` в stderr — прерывание потеряно, клиент
      запустился («Готово…»), процесс не вышел за 5 с;
    - код -2 при пустом выводе: `KeyboardInterrupt` возник в `eval` внутри
      `collections.namedtuple`, и Python 3.9 после кода 130 завершает
      процесс сигналом;
    - `RuntimeError: Error calling __set_name__ …` — Python 3.9 завернул
      прерывание в другое исключение: трассировка и код 1.
    """
    tmpdir = app.parent / "tmp"
    tmpdir.mkdir()
    assert list(tmpdir.iterdir()) == []  # предпосылка: следа импорта ещё нет
    proc = launch_terminal("--folder", str(folder))

    deadline = time.monotonic() + 15
    while not any(tmpdir.glob("*/*.capabilities.pickle")):
        # Клиент вышел, так и не начав импорт, — ждать нечего: тест падает
        # сразу, с кодом выхода и выводом.
        assert proc.popen.poll() is None, f"клиент вышел до импорта escpos\n{proc.stdout}{proc.stderr}"
        assert time.monotonic() < deadline, (
            f"нет файла возможностей escpos за 15 с\n{proc.stdout}{proc.stderr}"
        )
        time.sleep(START_POLL)
    os.killpg(proc.popen.pid, signal.SIGINT)

    assert proc.returncode_within(5) == CTRL_C_EXIT, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == ""


# Проба для `test_import_does_not_load_server_modules`: импортировать
# модуль запуска и напечатать через пробел, какие тяжёлые модули после
# этого уже загружены (M5): сервер, API, принтер и их библиотеки. Пакета
# верхнего уровня достаточно: импорт любого его модуля грузит и сам пакет.
IMPORT_PROBE = (
    "import sys\n"
    "import photoprint.__main__\n"
    "heavy = ('uvicorn', 'fastapi', 'starlette', 'escpos', 'usb', 'PIL', 'photoprint.web', 'photoprint.printer')\n"
    "print(' '.join(name for name in heavy if name in sys.modules))\n"
)


def test_import_does_not_load_server_modules(app: Path) -> None:
    """M5, M7: `import photoprint.__main__` в свежем процессе не грузит ни uvicorn, ни FastAPI, ни escpos, ни модули `web` и `printer`.

    Это причина, по которой Ctrl+C в первые доли секунды запуска
    попадает под M7 (`test_ctrl_c_during_startup_imports`): всё, что
    грузится при импорте модуля, грузится до `try` в `main`. § 4.5 M5:
    тяжёлые импорты — внутри `main`, после разбора аргументов; заодно
    `--check-printer` и ошибка аргументов не ждут сервер. Импорт одного
    `uvicorn` в начале модуля `test_ctrl_c_during_startup_imports` не
    заметил бы: его след — импорт escpos. Эта проба видит любой из них.
    """
    proc = Proc.start(app, ["-c", IMPORT_PROBE], {})
    try:
        code = proc.returncode_within(15)
    finally:
        proc.stop()

    assert code == 0, proc.stderr
    assert proc.stdout == "\n", f"при импорте загружены: {proc.stdout.strip()}"
    assert proc.stderr == ""


@pytest.mark.usefixtures("lock_holder")
def test_poll_ignores_foreign_json_list(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    foreign_health: Callable[[int, object], None],
) -> None:
    """M2: чужая программа на порту диапазона отвечает на `/api/health` JSON-списком → это не клиент: «…не отвечает…», код 1, без трассировки.

    Замок держит молчащий помощник, а на `base` чужой HTTP-сервер отдаёт
    `[]`. У списка нет `.get`: без проверки «ответ — объект»
    (`isinstance(data, dict)`) опрос M2 упал бы с `AttributeError`, и
    заказчик увидел бы трассировку Python вместо понятного текста.
    """
    foreign_health(base, [])
    proc = launch("--folder", str(folder))

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout == NOT_RESPONDING + "\n"
    assert proc.stderr == ""


def test_start_skips_port_of_foreign_json_list(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    foreign_health: Callable[[int, object], None],
) -> None:
    """M3, M5: без замка чужой сервер с JSON-списком в `/api/health` на `base` — просто занятый порт: клиент встаёт на `base + 1`.

    Порт `base` принимает соединения — M3 его пропускает. Наблюдатель M5
    ждёт свою папку на своём порту, а чужой ответ на `base` ему не мешает:
    «Готово…» с адресом `base + 1`, весь вывод — адрес и «Готово…», stderr
    пуст.
    """
    foreign_health(base, [])
    proc = launch("--folder", str(folder))

    assert proc.wait_for(_ready(base + 1)) == [_url(base + 1)]
    proc.stop()
    assert proc.stdout == f"{_url(base + 1)}\n{_ready(base + 1)}\n"
    assert proc.stderr == ""


@pytest.mark.usefixtures("lock_holder")
def test_poll_ignores_foreign_json_without_app(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    foreign_health: Callable[[int, object], None],
) -> None:
    """M2: чужой JSON-объект с той же папкой, но без `"app": "photoprint"` → это не клиент: «…не отвечает…», код 1.

    Замок держит молчащий помощник, а на `base` чужой HTTP-сервер отдаёт
    `{"folder": <папка запуска>}`. Без проверки `app` второй запуск принял
    бы его за свой клиент: «Клиент уже запущен — открываю страницу.», код 0
    и адрес чужой программы в браузере заказчика.
    """
    foreign_health(base, {"folder": str(folder.resolve())})
    proc = launch("--folder", str(folder))

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout == NOT_RESPONDING + "\n"
    assert proc.stderr == ""


@pytest.mark.usefixtures("lock_holder")
def test_poll_ignores_foreign_app_with_same_folder(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    foreign_health: Callable[[int, object], None],
) -> None:
    """M2: чужой JSON-объект со своим `"app"` (не `"photoprint"`) и той же папкой → не клиент: «…не отвечает…», код 1.

    Держит сравнение значения `app == "photoprint"`, а не одно наличие ключа
    `app` или его непустоту: `test_poll_ignores_foreign_json_without_app`
    ключа не даёт вовсе, и ослабленная проверка его тоже проходит. Без
    сравнения значения второй запуск принял бы чужую программу на порту
    диапазона за свой клиент: «Клиент уже запущен — открываю страницу.»,
    код 0 и её адрес в браузере заказчика (§ 7.3: настоящий `http.server`
    в потоке теста).
    """
    foreign_health(base, {"app": "other", "folder": str(folder.resolve())})
    proc = launch("--folder", str(folder))

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout == NOT_RESPONDING + "\n"
    assert proc.stderr == ""


def test_second_launch_same_folder_through_symlink(
    tmp_path: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M1, M2: второй запуск для той же папки через символическую ссылку → своя папка: «Клиент уже запущен…», адрес первого, код 0.

    Первый клиент отвечает в `/api/health` настоящим путём. Второй
    сравнивает с ним путь после `resolve()` (M1), а не как передан: иначе
    ссылка на ту же папку дала бы «Уже запущен клиент другой папки: …»
    (код 1). Во всех других тестах M2 путь уже настоящий (`tmp_path` —
    под `/private`), и этой разницы они не видят.
    """
    link = tmp_path / "ссылка на папку"
    link.symlink_to(folder, target_is_directory=True)
    # Предпосылка: путь ссылки отличается от настоящего.
    assert str(link) != str(folder.resolve())
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base))
    second = launch("--folder", str(link))

    assert second.returncode_within(15) == 0, second.stderr
    assert second.stdout == ALREADY_RUNNING + "\n" + _url(base) + "\n"
    assert second.stderr == ""
    assert first.popen.poll() is None


@pytest.mark.usefixtures("lock_holder")
def test_waiting_second_launch_holds_no_port(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M2 «перед M3»: пока второй запуск опрашивает (замок занят), он не держит ни одного порта — `bind` на `base` без `SO_REUSEADDR` удаётся.

    § 4.5 M3 — «с замком»: порт выбирает только владелец замка. Запуск, у
    которого замок занят, порт держать не должен: иначе на эти до 5 с
    опроса он отнимал бы порт у всех — например, у клиента, которого
    заказчик как раз закрыл и тут же открыл снова. Тот ушёл бы с прежнего
    порта, и вкладка браузера со старым адресом перестала бы работать.
    `test_second_launch_when_rest_of_range_busy_opens_first` ловит перенос
    M3 до замка только при занятом диапазоне; этот тест — и при свободном.

    Момент — по самому опросу: на `base + 1` тест слушает сокетом, который
    принимает соединение и молчит (`_accept_health_poll`). Когда туда
    пришёл `GET /api/health`, клиент уже в опросе M2 и `base` опросил перед
    ним. `bind` без `SO_REUSEADDR` отказал бы на порту, который держит
    хоть чей-то сокет с `bind`, — даже без `listen`.
    """
    silent = listen("127.0.0.1", base + 1)
    proc = launch("--folder", str(folder))
    try:
        connection = _accept_health_poll(silent, 15)
    except AssertionError as error:
        raise AssertionError(f"{error} — опроса M2 нет\n{proc.stdout}{proc.stderr}") from None
    with connection:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", base))


@pytest.mark.usefixtures("lock_holder")
def test_lock_wait_is_five_seconds_from_first_poll(
    folder: Path, launch: Callable[..., Proc], poll_closer: _PollCloser
) -> None:
    """M2: «…не отвечает…» приходит через 5 с опроса — не раньше 4,8 с и не позже 6 с после первого запроса `/api/health`.

    Замок держит молчащий помощник, а `base` слушает программа, которая
    рвёт каждое соединение, запомнив время первого опроса. Отсчёт — от
    первого опроса, а не от запуска процесса: так в срок не входят запуск
    Python и импорт модулей, и граница точнее, чем у
    `test_lock_held_by_silent_process` (от 4,5 с до 10 с от запуска). 4 с
    опроса — мало: первый клиент, который только что взял замок, мог ещё
    не ответить. 8 с — заказчик ждёт молча дольше обещанного. Запас сверху
    — на последний круг опроса, паузу 0,2 с и выход процесса (§ 4.5 M2:
    ответ может прийти чуть позже 5 с).
    """
    proc = launch("--folder", str(folder))
    assert poll_closer.polled.wait(15), f"нет опроса /api/health за 15 с\n{proc.stdout}{proc.stderr}"
    first_poll = poll_closer.first_poll
    assert first_poll is not None

    assert proc.returncode_within(15) == 1, proc.stderr
    elapsed = time.monotonic() - first_poll
    assert proc.stdout == NOT_RESPONDING + "\n"
    assert proc.stderr == ""
    assert POLL_WAIT_MIN <= elapsed <= POLL_WAIT_MAX, f"отказ через {elapsed:.2f} с после первого опроса"


# --- доработка контролёра по задаче 2.5, круг 2 (выжившие мутанты H3, H17, H12–H15) ---


def test_start_without_lock_when_lock_path_is_directory(
    app: Path, folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M2: на месте `<root>/run.lock` каталог — `os.open` отказывает `IsADirectoryError`, а не `PermissionError` → клиент запускается без замка.

    § 4.5 M2: ветка «без замка» — любой `OSError` при открытии `run.lock`,
    а не одно «нет права записи» (`test_start_without_lock_when_root_unwritable`).
    Так же отказывают полный диск (`ENOSPC` при `O_CREAT`) и том только для
    чтения (`EROFS`). Код, который ловит лишь `PermissionError`, на них
    печатал бы трассировку и выходил с кодом 1 — ровно тот сбой, который
    ветка «без замка» убирает. Сбой настоящий (§ 7.3, как каталог на месте
    `folder` у M4): настоящий каталог на месте файла.
    """
    lock = app.parent / "run.lock"
    lock.mkdir()
    # Предпосылка: ошибка открытия — правда не `PermissionError`.
    with pytest.raises(IsADirectoryError):
        os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    proc = launch("--folder", str(folder))

    _assert_plain_start(proc, folder, base)
    assert lock.is_dir()


@pytest.mark.usefixtures("lock_holder")
@pytest.mark.parametrize("body", [["photoprint"], "ok", True], ids=["list", "string", "true"])
def test_poll_ignores_foreign_json_not_object(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    foreign_health: Callable[[int, object], None],
    body: object,
) -> None:
    """M2: чужой `/api/health` отвечает JSON, который не объект и не «пустой» (непустой список, строка, `true`) → «…не отвечает…», код 1, без трассировки.

    `test_poll_ignores_foreign_json_list` отдаёт `[]`, а пустой список
    пропускает и неверная проверка «ответ не пустой» (`if not data or …`).
    У `"ok"` и `true` — частых ответов health-ручек — и у непустого списка
    `.get` тоже нет: без проверки типа (`isinstance(data, dict)`) опрос M2
    упал бы с `AttributeError`, и заказчик увидел бы трассировку Python.
    Замок держит молчащий помощник, чужой HTTP-сервер на `base` — настоящий
    (§ 7.3).
    """
    foreign_health(base, body)
    proc = launch("--folder", str(folder))

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout == NOT_RESPONDING + "\n"
    assert proc.stderr == ""


# Проба для `test_argument_error_does_not_load_server_modules`: исполнить
# модуль, как `python -m photoprint` без аргументов (`runpy`, `__name__ ==
# "__main__"`, тот же `sys.argv`), поймать выход argparse и напечатать его
# код и через пробел уже загруженные тяжёлые модули (M5).
ARGPARSE_IMPORT_PROBE = (
    "import runpy, sys\n"
    "code = None\n"
    "try:\n"
    "    runpy.run_module('photoprint', run_name='__main__', alter_sys=True)\n"
    "except SystemExit as exit:\n"
    "    code = exit.code\n"
    "heavy = ('uvicorn', 'fastapi', 'starlette', 'escpos', 'usb', 'PIL', 'photoprint.web', 'photoprint.printer')\n"
    "print(code, ' '.join(name for name in heavy if name in sys.modules))\n"
)


def test_argument_error_does_not_load_server_modules(app: Path) -> None:
    """M5, M7: запуск без режима (ошибка argparse, код 2) не грузит ни uvicorn, ни API, ни принтер — ни при импорте модуля, ни в `main` до разбора аргументов.

    § 4.5 M5: тяжёлые импорты — внутри `main`, после разбора аргументов.
    `test_import_does_not_load_server_modules` видит только импорт модуля,
    а `test_ctrl_c_during_startup_imports` — только импорт escpos. Импорт
    uvicorn в начале `main` до `try` или под `if __name__ == "__main__"` ни
    один из них не заметил бы, а там Ctrl+C посреди импорта (около 0,04 с)
    дал бы трассировку вместо кода 130 (M7). Импорт в `_main` до
    `parse_args` нарушает M5 прямо: ошибка аргументов ждала бы сервер.

    Здесь модуль исполняется по-настоящему, как `python -m photoprint`, до
    выхода argparse, и всё, что загрузилось до разбора аргументов, остаётся
    в `sys.modules`. Предпосылка — код 2 и usage в stderr: выход правда
    был ошибкой аргументов.
    """
    proc = Proc.start(app, ["-c", ARGPARSE_IMPORT_PROBE], {})
    try:
        code = proc.returncode_within(15)
    finally:
        proc.stop()

    assert code == 0, proc.stderr
    assert proc.stdout == "2 \n", f"загружены: {proc.stdout.strip()}"
    assert "usage:" in proc.stderr


# --- задача 2.7a: выжившие мутанты ворот 2 (M1, M2, M3, M5, M6) ---

# M2: срок опроса клиента, который держит замок, с (`LOCK_WAIT`, § 4.5).
POLL_DEADLINE = 5.0
# M2 (решение контролёра по задаче 2.5): сколько чужих программ, которые
# принимают соединение и молчат, стоит перед первым клиентом. Опрос ждёт
# каждую тайм-аут 1 с (§ 4.5), и первый круг доходит до клиента не раньше
# чем через 6 с — позже срока 5 с.
SILENT_BEFORE_CLIENT = 6
# M3: тайм-аут пробы `connect` у клиента, с (`CONNECT_TIMEOUT`, § 4.5):
# с ним тест проверяет предпосылку «проба не видит порт занятым».
CONNECT_PROBE = 0.3


def _http_reply(content_type: str, body: bytes, length: Optional[int] = None) -> bytes:
    """Собрать сырой ответ `HTTP/1.0 200` чужой программы с телом `body` (M2).

    `length` — длина, которую ответ заявляет в `Content-Length`; по
    умолчанию настоящая. Заявленная длина больше тела — оборванный ответ.
    """
    size = len(body) if length is None else length
    head = f"HTTP/1.0 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {size}\r\n\r\n"
    return head.encode("ascii") + body


# M2: ответы чужой программы на `GET /api/health`, после которых у опроса нет
# JSON-объекта, и исключение, которым кончается их разбор. Страница HTML (на
# 8766 работает веб-сервер, который на любой адрес отдаёт `index.html`) —
# `json.JSONDecodeError`, тело не UTF-8 — `UnicodeDecodeError`: оба
# `ValueError`. Строка не HTTP (любая другая служба TCP) —
# `http.client.BadStatusLine`, тело короче заявленной длины (сервер упал
# посреди ответа) — `http.client.IncompleteRead`: оба
# `http.client.HTTPException`. Ни одно из четырёх не `OSError`: urllib
# оборачивает в `URLError` только сбои отправки запроса, а сбои чтения ответа
# отдаёт как есть.
FOREIGN_REPLIES: Dict[str, Tuple[bytes, type]] = {
    "html": (_http_reply("text/html", b"<!doctype html><title>dev</title>"), json.JSONDecodeError),
    "not-utf8": (_http_reply("application/json", b"\xff\xfe\xfa"), UnicodeDecodeError),
    "not-http": (NOT_HTTP, http.client.BadStatusLine),
    "truncated": (_http_reply("application/json", b'{"app": "ph', length=100), http.client.IncompleteRead),
}


class _RawHealthHandler(http.server.BaseHTTPRequestHandler):
    """Чужая программа на порту диапазона: на `GET /api/health` пишет в сокет байты `server.reply` как есть, на прочее — 404 (M2).

    Ответ — ровно эти байты, без строки статуса и заголовков от
    `http.server`: так тест задаёт и ответ не HTTP, и оборванное тело.
    Протокол — `HTTP/1.0` (по умолчанию у `http.server`): после ответа
    соединение закрывается, и оборванное тело так и остаётся оборванным.
    """

    def do_GET(self) -> None:
        """Ответить на `GET`: на `/api/health` — сырыми байтами из сервера, на прочее — 404."""
        if self.path != "/api/health":
            self.send_error(404)
            return
        self.wfile.write(self.server.reply)  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        """Не писать журнал запросов: строка на каждый опрос клиента засорила бы вывод pytest."""


@pytest.fixture
def foreign_reply() -> Iterator[Callable[[int, bytes], None]]:
    """Дать тесту функцию `foreign_reply(port, reply)`: чужой HTTP-сервер на `127.0.0.1:port`, который на `/api/health` отвечает сырыми байтами `reply` (M2).

    Сервер настоящий — `http.server` стандартной библиотеки в фоновом
    потоке теста (§ 7.3), как у `foreign_health`, но ответ не проходит
    через JSON и даже через `send_response`. Каждый сервер
    останавливается и закрывает сокет в финализаторе, даже если тест упал:
    иначе он держал бы порт соседнего теста.
    """
    servers: List[Tuple[http.server.ThreadingHTTPServer, threading.Thread]] = []

    def serve(port: int, reply: bytes) -> None:
        """Поднять чужой сервер на `127.0.0.1:port` с ответом `reply` на `/api/health`."""
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _RawHealthHandler)
        server.reply = reply  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        servers.append((server, thread))

    yield serve
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _health_error(port: int) -> BaseException:
    """Спросить `/api/health` на `127.0.0.1:port` так же, как опрос M2, и вернуть исключение, которым кончился разбор ответа.

    Запрос — мимо прокси, ответ читается целиком и разбирается как JSON
    из UTF-8. Ответ разобрался → `AssertionError`: предпосылка теста не
    выполнена.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        return error
    raise AssertionError(f"ответ на порту {port} разобрался как JSON: {data!r}")


@pytest.mark.parametrize("reply", list(FOREIGN_REPLIES))
def test_second_launch_finds_first_behind_foreign_non_json(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    foreign_reply: Callable[[int, bytes], None],
    reply: str,
) -> None:
    """M2: чужая программа на `base` отвечает в `/api/health` не JSON (HTML, не UTF-8, не HTTP, оборванное тело) → второй запуск находит первого на `base + 1`: «Клиент уже запущен…», код 0, без трассировки.

    § 4.5 M2: ответ не JSON, оборванный ответ и молчание для опроса — одно
    «не ответил», и опрос идёт к следующему порту. Так бывает у заказчика:
    на 8766 работает чужой веб-сервер, который на любой адрес отдаёт
    страницу HTML, первый клиент ушёл на 8767 (M3), а повторный двойной
    клик опрашивает 8766 первым. Опрос, который не ловит `ValueError`
    (HTML, не UTF-8) или `http.client.HTTPException` (не HTTP, оборванное
    тело), упал бы на 8766 с трассировкой Python и кодом 1 вместо
    «Клиент уже запущен…» и страницы первого.

    Предпосылка: разбор ответа правда кончается названным исключением, и
    это не `OSError` — иначе ответ ничем не отличался бы от молчания, и
    тест ничего не проверил бы. Чужой сервер настоящий (§ 7.3).
    """
    data, error_type = FOREIGN_REPLIES[reply]
    foreign_reply(base, data)
    error = _health_error(base)
    assert isinstance(error, error_type), repr(error)
    assert not isinstance(error, OSError), repr(error)
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base + 1))
    second = launch("--folder", str(folder))

    assert second.returncode_within(15) == 0, second.stderr
    assert second.stdout == ALREADY_RUNNING + "\n" + _url(base + 1) + "\n"
    assert second.stderr == ""
    assert first.popen.poll() is None


def test_second_launch_finds_first_behind_silent_ports(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M2: шесть чужих программ перед первым клиентом принимают соединение и молчат → второй запуск доходит до клиента и после срока 5 с: «Клиент уже запущен…», код 0.

    § 4.5 M2 (решение контролёра по задаче 2.5): срок проверяется между
    кругами опроса, начатый круг доходит до последнего порта — иначе
    несколько молчащих чужих программ спрятали бы живой клиент, и на
    повторный двойной клик заказчик прочитал бы «…не отвечает. Закройте
    все окна…» о клиенте, который работает. Порты `base … base + 5` слушают
    сокеты теста без `accept`: соединение устанавливает ядро, ответа нет, и
    опрос каждого ждёт тайм-аут 1 с. Первый клиент для M3 видит их занятыми
    и встаёт на `base + 6`. Проверка срока перед каждым портом сдалась бы,
    не дойдя до `base + 6`; опрос без тайм-аута (§ 4.5: 1 с) повис бы на
    `base`.

    Предпосылка — время: второй запуск ответил позже 5 с от старта, то есть
    клиент правда найден после срока.
    """
    for port in range(base, base + SILENT_BEFORE_CLIENT):
        listen("127.0.0.1", port)
    first = launch("--folder", str(folder))
    first.wait_for(_ready(base + SILENT_BEFORE_CLIENT))
    started = time.monotonic()
    second = launch("--folder", str(folder))

    assert second.returncode_within(15) == 0, second.stderr
    elapsed = time.monotonic() - started
    assert second.stdout == ALREADY_RUNNING + "\n" + _url(base + SILENT_BEFORE_CLIENT) + "\n"
    assert second.stderr == ""
    assert elapsed > POLL_DEADLINE, f"ответ через {elapsed:.2f} с — срок 5 с не пройден, тест ничего не проверил"


@pytest.mark.usefixtures("lock_holder")
def test_hung_client_that_accepts_connections_is_not_responding(
    folder: Path,
    base: int,
    launch: Callable[..., Proc],
    listen: Callable[[str, int], socket.socket],
) -> None:
    """M2: замок занят, а на `base` сокет принимает соединения и молчит (зависший клиент) → «…не отвечает…», код 1 за 15 с, stderr пуст.

    Так выглядит клиент, у которого завис цикл событий: слушающий сокет
    жив, соединение устанавливает ядро, а ответа нет. § 4.5: у каждого
    запроса к `127.0.0.1` тайм-аут 1 с. Без него опрос M2 ждал бы ответа
    вечно, и окно второго запуска молчало бы, а не говорило заказчику, что
    делать (§ 7.2: «замок держит процесс, который не отвечает → код 1 за
    5 с»). `test_lock_held_by_silent_process` этого не видит: там помощник
    не слушает ни одного порта, и каждое соединение отклоняется сразу.
    """
    listen("127.0.0.1", base)
    proc = launch("--folder", str(folder))

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout == NOT_RESPONDING + "\n"
    assert proc.stderr == ""


def test_skips_port_bound_without_listen(
    folder: Path, base: int, launch: Callable[..., Proc]
) -> None:
    """M3: `base` занят чужим сокетом с `bind`, но без `listen` → проба `connect` кончается тайм-аутом, `bind` отказывает, сервер встаёт на `base + 1`.

    § 4.5 M3: «первый удачный сокет» — неудачный `bind` значит «следующий
    порт», а проба `connect`, которая не удалась любым способом, — «порт
    не слушают». На macOS SYN к порту с `bind` без `listen` молча
    отбрасывается: проба за 0,3 с кончается тайм-аутом (`socket.timeout`,
    не `ConnectionRefusedError`), а `bind` с `SO_REUSEADDR` поверх чужого
    сокета отказывает (errno 48). Так выглядит чужая программа, которая
    заняла порт и ещё не слушает, и второй клиент без замка (M2 «без
    замка») посреди старта первого. Код, который ловит только отказ в
    соединении или верит пробе и не ловит сбой `bind`, показал бы заказчику
    трассировку Python и код 1 вместо страницы. Обе предпосылки
    проверяются здесь же.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", base))
        with pytest.raises(socket.timeout):
            socket.create_connection(("127.0.0.1", base), timeout=CONNECT_PROBE).close()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError) as caught:
                probe.bind(("127.0.0.1", base))
        assert caught.value.errno == errno.EADDRINUSE
        proc = launch("--folder", str(folder))

        _assert_plain_start(proc, folder, base + 1)


def _wait_for_path(path: Path, proc: Proc) -> None:
    """M1, M4: дождаться, пока клиент `proc` создаст `path`; клиент вышел раньше или файла нет за 15 с → `AssertionError` с выводом."""
    deadline = time.monotonic() + 15
    while not path.exists():
        assert proc.popen.poll() is None, f"клиент вышел до {path}\n{proc.stdout}{proc.stderr}"
        assert time.monotonic() < deadline, f"нет {path} за 15 с\n{proc.stdout}{proc.stderr}"
        time.sleep(START_POLL)


def _read_fifo(path: Path, proc: Proc) -> str:
    """Прочитать из именованного канала `path` всё, что запишет в него клиент `proc` (M4), и вернуть текст.

    Канал открывается без ожидания (`O_NONBLOCK`): тест не повиснет, если
    клиент вышел, так и не открыв его. Пока писателя нет, чтение отдаёт
    пусто; пока писатель открыл канал, но не записал, — `BlockingIOError`.
    Путь клиент пишет одним коротким `write` (меньше `PIPE_BUF` — целиком),
    поэтому пусто после данных значит, что клиент закрыл файл. Нет записи
    за 15 с или клиент вышел → `AssertionError` с выводом.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        data = b""
        deadline = time.monotonic() + 15
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                chunk = None
            if chunk:
                data += chunk
            elif chunk == b"" and data:
                return data.decode("utf-8")
            assert proc.popen.poll() is None, f"клиент вышел до записи {path}\n{proc.stdout}{proc.stderr}"
            assert time.monotonic() < deadline, f"нет записи в {path} за 15 с\n{proc.stdout}{proc.stderr}"
            time.sleep(START_POLL)
    finally:
        os.close(fd)


def _wait_for_health(port: int, proc: Proc) -> Tuple[int, object]:
    """M5: дождаться ответа `/api/health` сервера клиента `proc` на `port` и вернуть `(код, JSON)`.

    Пока сервер не слушает, соединение не устанавливается (`OSError`) —
    тест спрашивает снова. Клиент вышел или ответа нет за 15 с →
    `AssertionError` с выводом.
    """
    deadline = time.monotonic() + 15
    while True:
        assert proc.popen.poll() is None, f"клиент вышел\n{proc.stdout}{proc.stderr}"
        assert time.monotonic() < deadline, f"сервер не ответил за 15 с\n{proc.stdout}{proc.stderr}"
        try:
            return get_json(_url(port) + "api/health")
        except OSError:
            time.sleep(0.05)


def test_ctrl_c_before_ready_ends_process(
    tmp_path: Path, app: Path, folder: Path, base: int, launch_terminal: Callable[..., Proc]
) -> None:
    """M5, M7: Ctrl+C, пока сервер уже работает, а наблюдатель ещё не дождался своей папки, → код 130 за 5 с, без вывода.

    § 4.5 M5: наблюдатель — фоновый поток (`daemon=True`), чтобы не держать
    процесс, если сервер остановился раньше «Готово…». Иначе после Ctrl+C
    `main` вернул бы 130, а интерпретатор на выходе ждал бы наблюдателя
    вечно: тот опрашивает уже закрытый порт, и окно Терминала висело бы до
    второго Ctrl+C. То же — после любого раннего выхода сервера. У
    заказчика это окно — доли секунды старта; тест держит его открытым
    по-настоящему, без правки кода и без гонки:

    - на месте `<root>/folder` — именованный канал (FIFO), настоящий объект
      файловой системы, как каталог на месте `folder` в тестах M4. Запись
      M4 открывает его и ждёт читателя: клиент стоит после M1 и до сборки
      сервера, пока тест не прочтёт канал;
    - появился `run.lock` — M1 позади (замок M2 берётся после неё). Тест
      переносит папку, ставит на её месте символическую ссылку на новое
      место и только потом читает канал;
    - сервер собирается после M4, `create_app` приводит путь заново и
      называет новое место, а наблюдатель ждёт путь из M1 и «Готово…» не
      скажет никогда.

    Предпосылки: M4 записал путь из M1, сервер отвечает новым путём,
    «Готово…» нет. Затем SIGINT группе процесса, как его шлёт Терминал.
    """
    recorded = app.parent / "folder"
    os.mkfifo(recorded)
    original = str(folder.resolve())
    moved = tmp_path / "перенесённая папка"
    proc = launch_terminal("--folder", str(folder))

    _wait_for_path(app.parent / "run.lock", proc)
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    assert _read_fifo(recorded, proc) == original
    assert _wait_for_health(base, proc) == (200, {"app": "photoprint", "folder": str(moved.resolve())})
    time.sleep(QUIET_WAIT)
    assert proc.stdout == ""

    os.killpg(proc.popen.pid, signal.SIGINT)

    assert proc.returncode_within(5) == CTRL_C_EXIT, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_check_printer_with_garbage_libusb(app: Path, launch: Callable[..., Proc]) -> None:
    """M6: `libusb-1.0.0.dylib` на месте, но не загружается (мусор вместо библиотеки) → «Не загрузилась библиотека USB — …», код 1.

    § 4.5 M6: отказ — по бэкенду (`None`), а не по наличию файла. Файл
    есть, а библиотека не грузится — повреждённая установка (§ 7.2: «файл
    с мусором вместо dylib → None»; § 7.3: битые байты). Проверка по одному
    наличию файла сказала бы «Принтер не найден — проверьте кабель…» с
    кодом 0, установщик (I8) счёл бы шаг «библиотека USB» удачным, и
    заказчик проверял бы кабель, хотя печать не будет работать никогда.
    `test_check_printer_without_libusb` удаляет файл, и там «нет файла» и
    «бэкенд `None`» совпадают.
    """
    (app / "libusb-1.0.0.dylib").write_bytes(b"garbage")
    proc = launch("--check-printer")

    assert proc.returncode_within(15) == 1, proc.stderr
    assert proc.stdout == NO_LIBUSB + "\n"
    assert proc.stderr == ""


# M1: клиент с удалённым текущим каталогом. Модуль запуска импортируется,
# пока текущий каталог — копия `app/` (оттуда `-c`, как `-m photoprint` у
# ярлыка, L3, находит пакет); затем процесс переходит в каталог `argv[1]` и
# удаляет его, а дальше — тот же `main`, что у `python -m photoprint`, с
# аргументами `argv[2:]`. Код клиента не меняется: удалённый текущий
# каталог — условие окружения, как у окна Терминала, чей каталог удалили.
DELETED_CWD_CLIENT = (
    "import os, sys\n"
    "from photoprint.__main__ import main\n"
    "os.chdir(sys.argv[1])\n"
    "os.rmdir(sys.argv[1])\n"
    "sys.exit(main(sys.argv[2:]))\n"
)


def test_relative_folder_with_deleted_cwd_exits_2(tmp_path: Path, app: Path) -> None:
    """M1: относительный `--folder` при удалённом текущем каталоге → `resolve()` не удался: «папка не найдена: x» (путь как передан) в stderr, код 2, без трассировки.

    На Python 3.9 `Path("x").resolve()` сначала спрашивает текущий каталог
    (`os.getcwd()`), а у удалённого его нет — `FileNotFoundError`. Это
    `OSError` в `except (OSError, RuntimeError)` у M1; петлю ссылок, где
    3.9 бросает `RuntimeError`, держит `test_symlink_loop_exits_2`. Без
    `OSError` там заказчик увидел бы трассировку Python и код 1 вместо
    понятного текста. Приведённого пути нет, поэтому в тексте путь как
    передан. M2 и M4 идут после M1: ни `run.lock`, ни `<root>/folder` не
    появляются. Предпосылка: каталог правда удалён.
    """
    gone = tmp_path / "удалённый каталог"
    gone.mkdir()
    proc = Proc.start(app, ["-c", DELETED_CWD_CLIENT, str(gone), "--folder", "x"], {})
    try:
        code = proc.returncode_within(15)
    finally:
        proc.stop()

    assert not gone.exists()
    assert code == 2, proc.stderr
    assert proc.stderr == "папка не найдена: x\n"
    assert proc.stdout == ""
    assert not (app.parent / "run.lock").exists()
    assert not (app.parent / "folder").exists()


# Проверка принтера (M6) с `UsbPrinter._find`, который бросает ошибку pyusb —
# точка подмены из § 7.3 (`_find` → исключение). Так кончается
# `usb.core.find` у заказчика, когда `libusb_get_device_list` не удался.
# Дальше — тот же `main`, что у `python -m photoprint`, с `--check-printer`;
# поставляемая libusb грузится по-настоящему, ветка «нет libusb» не берётся.
CHECK_PRINTER_WITH_FAILING_FIND = (
    "import sys\n"
    "import usb.core\n"
    "from photoprint.printer import UsbPrinter\n"
    "def failing_find(self):\n"
    "    raise usb.core.USBError('enumeration failed')\n"
    "UsbPrinter._find = failing_find\n"
    "from photoprint.__main__ import main\n"
    "sys.exit(main(['--check-printer']))\n"
)


def test_check_printer_bus_error_is_printer_missing(app: Path) -> None:
    """M6: поиск на шине кончился ошибкой pyusb (`USBError`) → «Принтер не найден — …», код 0, stderr пуст.

    § 4.5 M6: строка — по ответу `connected()`, а он всегда «да» или «нет»
    (P6): сбой опроса шины для оператора — то же, что «принтера нет».
    Прямой вызов поиска (`_find() is not None` вместо `connected()`)
    выпустил бы `USBError` трассировкой Python с кодом 1, и установщик (I8)
    счёл бы сбоем шаг «библиотека USB», хотя библиотека загрузилась и
    клиент установлен. `test_check_printer_line_follows_find` задаёт поиску
    только ответы «есть» и «нет», и этой разницы не видит.
    """
    proc = Proc.start(app, ["-c", CHECK_PRINTER_WITH_FAILING_FIND], {})
    try:
        code = proc.returncode_within(15)
    finally:
        proc.stop()

    assert code == 0, proc.stderr
    assert proc.stdout == PRINTER_MISSING + "\n"
    assert proc.stderr == ""


# --- задача 3.4a: детерминизм набора (финальная проверка, 2026-09-28) ---
#
# Помощники тестов, а не код клиента: параллельные прогоны набора (ворота,
# финальная проверка) не должны давать ложных падений, а проверки тестов при
# этом не ослабевают.

# Проба для `test_parallel_runs_get_disjoint_port_slices`: второй прогон
# pytest, которому не повезло с номером процесса. Проба делает `fork`, пока
# номер потомка по модулю `argv[2]` (число срезов) не станет `argv[1]` —
# остатком номера процесса pytest этого теста. Этот потомок заменяет себя (`exec`, номер процесса
# тот же) на `python -c`, который берёт основание `free_base()`, как любой
# тест, и печатает его; остальные потомки сразу выходят. Код выхода пробы —
# код этого потомка; `exec` не удался — 127.
SAME_PID_SLICE_PROBE = (
    "import os, sys\n"
    "target, slices = int(sys.argv[1]), int(sys.argv[2])\n"
    "for _ in range(5000):\n"
    "    pid = os.fork()\n"
    "    if pid == 0:\n"
    "        if os.getpid() % slices == target:\n"
    "            try:\n"
    "                os.execv(sys.executable, [sys.executable, '-c',\n"
    "                    'import helpers; print(helpers.free_base(), flush=True)'])\n"
    "            finally:\n"
    "                os._exit(127)\n"
    "        os._exit(0)\n"
    "    _, status = os.waitpid(pid, 0)\n"
    "    if pid % slices == target:\n"
    "        sys.exit(os.waitstatus_to_exitcode(status))\n"
    "sys.exit('нет потомка с нужным остатком номера за 5000 fork')\n"
)


def test_parallel_runs_get_disjoint_port_slices(tmp_path: Path) -> None:
    """Помощник `free_base`: два одновременных прогона pytest берут порты из разных срезов, даже с одним остатком номера процесса.

    Опрос M2 проходит все 20 портов диапазона, и клиент чужого прогона на
    одном из них отвечает «другая папка» вместо «не отвечает» — ложное
    падение (финальная проверка, 2026-09-28: 3 из 27 тестов M2 при общем
    срезе). Срез по номеру процесса (`pid % 16`) совпадает у двух прогонов с
    вероятностью 1/16, поэтому срез выдаёт замок `flock` на общем файле
    машины, а не номер процесса.

    Второй прогон — процесс `python -c` с `free_base()`, чей номер процесса
    подобран пробой `SAME_PID_SLICE_PROBE`: остаток по модулю 16 тот же, что
    у этого процесса pytest. Его основание — в другом срезе, чем основание
    этого прогона.
    """
    mine = free_base()
    my_slice = (mine - PORT_LOW) // PORT_SLICE_SIZE
    work = tmp_path / "work"
    work.mkdir()
    # Второй прогон импортирует `helpers` из этого же дерева, как pytest с
    # `pythonpath = app tests` (pytest.ini).
    pythonpath = os.pathsep.join([str(APP), str(REPO / "tests")])
    proc = Proc.start(
        work,
        ["-c", SAME_PID_SLICE_PROBE, str(os.getpid() % PORT_SLICES), str(PORT_SLICES)],
        {"PYTHONPATH": pythonpath},
    )
    try:
        code = proc.returncode_within(60)
    finally:
        proc.stop()

    assert code == 0, proc.stderr
    other = int(proc.stdout)
    assert (other - PORT_LOW) // PORT_SLICE_SIZE != my_slice, (
        f"оба прогона взяли срез {my_slice}: {mine} и {other}"
    )


# Сколько тест ждёт, что второй прогон дошёл до замка диапазона по
# умолчанию, с: запуск pytest и импорт модулей — секунды, запас — на
# параллельные прогоны.
CHILD_RUN_WAIT = 60.0
# Сколько второй прогон должен простоять на замке, пока первый держит
# диапазон, с. Без замка тест порта по умолчанию кончается за доли секунды
# после того, как открыл файл замка (а без замка он файл и не открывает).
BLOCKED_CHECK = 1.0


def _wait_until_open(proc: Proc, path: str, timeout: float) -> None:
    """Дождаться по `lsof`, что процесс `proc` открыл файл `path`.

    Процесс вышел, так и не открыв файл, или файла нет среди открытых за
    `timeout` секунд → `AssertionError` с кодом выхода и всем выводом.
    `lsof` показывает путь без ссылок, поэтому `path` сравнивается
    приведённым (`/tmp` на macOS — ссылка на `/private/tmp`).
    """
    target = "n" + os.path.realpath(path)
    deadline = time.monotonic() + timeout
    while True:
        assert proc.popen.poll() is None, f"процесс вышел, не открыв {path}\n{proc._report()}"
        listing = subprocess.run(
            [LSOF, "-nP", "-a", "-p", str(proc.popen.pid), "-Fn"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        # Код `lsof` не проверяется: код 1 («ничего не нашлось») значит, что
        # процесс уже вышел, и это покажет проверка `poll` на следующем круге.
        if target in listing.stdout.splitlines():
            return
        assert time.monotonic() < deadline, f"{path} не открыт за {timeout} с\n{proc._report()}"
        time.sleep(0.05)


def test_default_port_test_waits_for_parallel_run(tmp_path: Path) -> None:
    """Фикстура `default_ports_busy`: тест порта по умолчанию в параллельном прогоне ждёт, пока первый прогон отпустит 8766–8785, и потом проходит.

    Все прогоны набора делят один диапазон по умолчанию (M3), и без общего
    замка второй прогон пропускал порты, занятые первым, а клиент второго
    вставал на них, как только первый их отпускал: 15 с ожидания и ложное
    падение (финальная проверка, 2026-09-28).

    Первый прогон — этот тест: он держит замок и весь диапазон
    (`_default_ports_held`, как фикстура). Второй — настоящий pytest с
    `test_default_first_port_is_8766` в своём каталоге. Пока первый держит
    диапазон, второй открыл файл замка (`lsof`) и стоит на нём — не вышел.
    Когда первый отпустил, второй проходит: код 0, «1 passed».
    """
    child_tmp = tmp_path / "child-tmp"
    child_tmp.mkdir()
    command = [
        "-m", "pytest", "-q", "-p", "no:cacheprovider",
        f"--basetemp={tmp_path / 'child-basetemp'}",
        "tests/test_main.py::test_default_first_port_is_8766",
    ]
    child: Optional[Proc] = None
    try:
        with _default_ports_held():
            child = Proc.start(REPO, command, {"TMPDIR": str(child_tmp)})
            _wait_until_open(child, DEFAULT_PORTS_LOCK, CHILD_RUN_WAIT)
            time.sleep(BLOCKED_CHECK)
            assert child.popen.poll() is None, f"второй прогон не ждал замка\n{child._report()}"
        code = child.returncode_within(CHILD_RUN_WAIT)
    finally:
        if child is not None:
            child.stop()

    assert code == 0, child._report()
    assert re.search(r"^1 passed in ", child.stdout, re.MULTILINE), child._report()


# Проба для `test_client_subprocess_writes_no_bytecode`: импортировать модуль
# `argv[1]` и напечатать две строки — запрещена ли процессу запись байт-кода
# (`sys.dont_write_bytecode`, её и спрашивает импорт перед записью) и где
# лежал бы кэш этого модуля (`cache_from_source`: у Python из Command Line
# Tools — в `~/Library/Caches/com.apple.python/<полный путь>`).
BYTECODE_PROBE = (
    "import importlib, importlib.util, sys\n"
    "module = importlib.import_module(sys.argv[1])\n"
    "print(sys.dont_write_bytecode, flush=True)\n"
    "print(importlib.util.cache_from_source(module.__file__), flush=True)\n"
)


def test_client_subprocess_writes_no_bytecode(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Помощник `Proc.start`: процесс, запущенный тестом, не пишет байт-код — ни в копию `app/`, ни в настоящий `~/Library/Caches`.

    Python из Command Line Tools кладёт байт-код в
    `~/Library/Caches/com.apple.python/<полный путь исходника>`, а у каждого
    теста своя копия `app/` в `tmp_path`: кэш рос бы с каждым прогоном и
    никогда не чистился — в настоящем каталоге владельца, мимо изоляции
    тестов (финальная проверка, 2026-09-28: 167 МБ). Запрет записи — условие
    окружения, код клиента от него не меняется (§ 7.3).

    Прогон ворот уже ставит `PYTHONDONTWRITEBYTECODE=1`, а обычный
    `python -m pytest` — нет, поэтому тест убирает эту переменную из
    окружения самого pytest (не клиента) на время теста. Процесс из
    `Proc.start` импортирует модуль из копии `app/` с именем, которого не
    было ни в одном прежнем прогоне, — кэш по этому пути мог появиться
    только сейчас. Кэша нет, и процессу запись запрещена.
    """
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    name = f"bytecode_probe_{uuid.uuid4().hex}"
    (app / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = Proc.start(app, ["-c", BYTECODE_PROBE, name], {})
    try:
        code = proc.returncode_within(15)
    finally:
        proc.stop()

    assert code == 0, proc.stderr
    flag, cache = proc.stdout.splitlines()
    assert not Path(cache).exists(), f"процесс записал байт-код: {cache}"
    assert flag == "True"
