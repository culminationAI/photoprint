"""Запуск клиента: `python -m photoprint --folder <путь>` — сервер для одной папки; `--check-printer` — проверка принтера.

Ярлык `Печать картинок.command` из папки заказчика вызывает этот модуль со
своей папкой, а установщик последним шагом проверяет им libusb и принтер
(спецификация § 4.5). Для папки модуль берёт замок одного клиента на
машину, занимает первый свободный порт на `127.0.0.1`, записывает путь
папки для установщика, запускает сервер и, как только сервер ответил,
открывает страницу в браузере.

`<app>` — родитель каталога пакета `photoprint`, `<root>` — родитель
`<app>`; у заказчика это `~/.photoprint/app` и `~/.photoprint`.

Окружение:
- `PHOTOPRINT_PORT` — первый порт диапазона из 20, по умолчанию `8766`;
- `PHOTOPRINT_OPEN` — команда открытия адреса, по умолчанию `open`; тесты
  ставят `echo`, и адрес печатается, а не открывается.

Правила спецификации, которые держит модуль:
- режимы — обязательная взаимоисключающая группа `--folder PATH` |
  `--check-printer`; без режима или с обоими — usage и код 2 (argparse);
- M1 — папка приводится к `resolve()`; не каталог — текст в stderr с
  приведённым путём, код 2; `resolve` не удался (петля ссылок) — тот же
  текст с путём как передан;
- M2 — один клиент на машину: `flock` на `<root>/run.lock`. Замок занят —
  до 5 с опрос `/api/health` на всех портах диапазона: своя папка —
  открыть адрес первого, код 0; другая — её путь в тексте, код 1; никто не
  ответил — текст, код 1. `run.lock` не открывается или не берёт `flock`
  (ошибка, а не «занято») — клиент запускается без замка;
- M3 — порт занят, если к `127.0.0.1:port` удаётся подключиться; иначе
  сокет с `SO_REUSEADDR` делает `bind`, и этот же сокет уходит в uvicorn;
  все 20 портов заняты — текст, код 1;
- M4 — перед стартом сервера путь папки пишется в `<root>/folder`;
  запись не удалась — клиент всё равно запускается;
- M5 — наблюдатель ждёт свою папку в `/api/health`, открывает страницу и
  печатает «Готово…»; сервер завершился раньше — текст, код 1. Модули
  сервера, API и принтера импортируются внутри `main` после разбора
  аргументов, а не при импорте этого модуля;
- M6 — `--check-printer`: без libusb — текст, код 1; иначе одна строка
  «найден» или «не найден», код 0; замок и сервер не трогаются;
- M7 — своих обработчиков сигналов нет: SIGHUP (закрыто окно Терминала),
  SIGINT и SIGTERM завершают процесс; Ctrl+C — код 130 без трассировки,
  и посреди импорта модулей сервера и принтера тоже (они грузятся внутри
  `main`); остаются только первые ~0,04 с — старт интерпретатора и
  стандартные модули.
  Повторный Ctrl+C — тоже без трассировки, и посреди печати: lifespan
  uvicorn выключен, а запись uvicorn об отменённом запросе
  (`CancelledError`) в окно не идёт. После первого `KeyboardInterrupt`
  SIGINT возвращается к действию по умолчанию: пока интерпретатор на
  выходе ждёт незаконченную печать, следующий Ctrl+C завершает процесс
  сразу и молча.

Все сообщения печатаются с `flush=True`: M1 — в stderr, остальные — в
stdout. Запросы к `127.0.0.1` идут мимо прокси.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import http.client
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

# M5, M7: uvicorn, `photoprint.web` и `photoprint.printer` (с FastAPI,
# escpos, Pillow и pyusb) здесь не импортируются — только внутри `main`,
# там, где нужны (решение контролёра по задаче 2.5). Их импорт — около
# 0,3 с, самая долгая часть запуска, а всё, что грузится при импорте
# модуля, грузится ещё до `try` в `main`: Ctrl+C в эти доли секунды дал
# бы трассировку Python вместо кода 130. Заодно `--check-printer`,
# ошибка аргументов и отказы M1–M3 не ждут импорта сервера.

# § 4.5: `<app>` — каталог с пакетом, страницей и libusb; `<root>` — над ним,
# там лежат `folder` (M4) и `run.lock` (M2). `resolve()` — чтобы запуск
# через символическую ссылку писал в настоящий корень.
APP = Path(__file__).resolve().parent.parent
ROOT = APP.parent
# P2, M5, M6: поставляемая libusb — у заказчика другой нет.
LIBUSB = APP / "libusb-1.0.0.dylib"

DEFAULT_PORT = 8766   # первый порт диапазона, если `PHOTOPRINT_PORT` не задан
PORT_COUNT = 20       # M3: сколько портов подряд пробовать
POLL_INTERVAL = 0.2   # M2, M5: пауза между опросами `/api/health`, с
HEALTH_TIMEOUT = 1    # § 4.5: тайм-аут одного запроса к `127.0.0.1`, с
CONNECT_TIMEOUT = 0.3  # M3: тайм-аут пробы `connect` к порту, с
LOCK_WAIT = 5         # M2: сколько ждать ответа клиента, который держит замок, с
CTRL_C_EXIT = 130     # M7: код выхода после Ctrl+C — как у оболочки, 128 + SIGINT

# Тексты для людей — дословно из § 4.5. Их читает заказчик в окне
# Терминала, поэтому каждый говорит простыми словами, что случилось.
FOLDER_NOT_FOUND = "папка не найдена: {path}"
ALREADY_RUNNING = "Клиент уже запущен — открываю страницу."
OTHER_FOLDER = "Уже запущен клиент другой папки: {folder}. Закройте его окно и запустите снова."
NOT_RESPONDING = "Клиент уже запущен в другом окне, но не отвечает. Закройте все окна «Печать картинок» и запустите снова."
PORTS_BUSY = "Порты {first}–{last} на 127.0.0.1 заняты другими программами. Закройте лишние программы и запустите снова."
READY = "Готово. Страница открыта в браузере: {url}. Не закрывайте это окно, пока идёт работа."
NOT_STARTED = "Клиент не запустился — причина в сообщениях выше."
NO_LIBUSB = "Не загрузилась библиотека USB — печать работать не будет."
PRINTER_FOUND = "Принтер XP-160LL найден"
PRINTER_MISSING = "Принтер не найден — проверьте кабель и питание. Клиент всё равно установлен."


def _page_url(port: int) -> str:
    """Адрес страницы клиента на `port` — его открывают M2 и M5 и печатает «Готово…»."""
    return f"http://127.0.0.1:{port}/"


def _open_page(url: str) -> None:
    """Открыть `url` в браузере командой из `PHOTOPRINT_OPEN` (M2, M5)."""
    # § 4.5: команда открытия — из `PHOTOPRINT_OPEN`, чтобы тесты подменяли
    # браузер на `echo`. Код её выхода не проверяется: сервер уже работает,
    # а адрес заказчик увидит в строке «Готово…» или откроет сам.
    subprocess.run([os.environ.get("PHOTOPRINT_OPEN", "open"), url])


def _check_printer() -> int:
    """Проверить для установщика libusb и принтер на шине и вернуть код выхода (M6).

    1 — libusb не загрузилась, печать работать не будет; 0 — libusb на
    месте, а принтер найден или нет: без принтера клиент тоже установлен.
    """
    # M5, M7: модуль принтера — только здесь, а сервер и API проверке не
    # нужны вовсе (см. комментарий к импортам в начале модуля).
    from photoprint.printer import UsbPrinter

    printer = UsbPrinter(LIBUSB)
    # M6, I8: по коду 1 установщик считает шаг «библиотека USB» сбоем.
    if printer.backend is None:
        print(NO_LIBUSB, flush=True)
        return 1
    # M6, P6: `connected()` только ищет устройство на шине и в принтер
    # ничего не пишет.
    print(PRINTER_FOUND if printer.connected() else PRINTER_MISSING, flush=True)
    return 0


def _lock_instance() -> bool:
    """Взять замок одного клиента на машину — `flock` на `<root>/run.lock` (M2).

    `False` — замок держит другой процесс: клиент уже запущен. `True` —
    замок взят, и тогда он держится до конца процесса, или замок взять
    нельзя вовсе (файл не открывается, `flock` кончился ошибкой, а не
    «занято»), и клиент работает без замка.
    """
    try:
        # M2: `os.open` и без `O_CLOEXEC` во флагах даёт ненаследуемый
        # дескриптор (PEP 446): команда открытия страницы и браузер замок не
        # унаследуют и не продержат его после выхода клиента.
        fd = os.open(ROOT / "run.lock", os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        # M2: `run.lock` не открывается — например, `<root>` без права
        # записи до первого запуска. Клиент запускается без замка, как при
        # сбое записи M4: без замка теряется только защита от второго окна,
        # а трассировка вместо работы хуже (решение контролёра по задаче
        # 2.5). Ловится любой `OSError`, а не один `PermissionError`: полный
        # диск (`ENOSPC` при `O_CREAT`), том только для чтения (`EROFS`) и
        # каталог на месте файла (`EISDIR`) — тот же случай «замка нет».
        return True
    try:
        # M2: без ожидания — занятый замок значит «клиент уже есть», и это
        # надо сказать заказчику, а не висеть.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    except OSError:
        # M2: файл открылся, но замков не поддерживает (`ENOTSUP`: том без
        # `flock`, например сетевой диск) — та же ветка «без замка» и по
        # той же причине. Дескриптор без замка ничего не держит, он
        # закрывается. Ветка «занято» — только `BlockingIOError` выше:
        # иначе заказчик 5 с ждал бы и читал «…не отвечает…» о клиенте,
        # которого нет.
        os.close(fd)
        return True
    # M2: дескриптор не закрывается до конца процесса — ни здесь, ни
    # где-либо ещё: замок держится, пока жив клиент, и ядро само отпускает
    # его при любом выходе, даже при убийстве процесса (M7).
    return True


def _served_folder(port: int) -> Optional[str]:
    """Спросить `/api/health` на `127.0.0.1:port` и вернуть папку, если ответил photoprint.

    Нет ответа, ответ не JSON или отвечает не photoprint — `None`.
    """
    # § 4.5: только opener без прокси. urllib по умолчанию берёт прокси из
    # окружения и системных настроек macOS и пошёл бы к `127.0.0.1` через
    # него — ни наблюдатель, ни опрос M2 не дождались бы ответа.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=HEALTH_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, http.client.HTTPException, ValueError):
        # M2, M5: сервер ещё не слушает, оборвал ответ или прислал не JSON —
        # для опроса это одно и то же «пока не ответил».
        return None
    # M2, M5: на порту диапазона может отвечать чужая программа, и её JSON —
    # не обязательно объект: у списка, строки, числа или `true` нет `.get`,
    # и `AttributeError` уронил бы наблюдатель или весь запуск. Такой ответ —
    # «не photoprint». Проверяется именно тип, а не «ответ не пустой»:
    # непустой список или `"ok"` — частый ответ health-ручек — тоже не объект.
    if not isinstance(data, dict) or data.get("app") != "photoprint":
        return None
    return data.get("folder")


def _find_running(first: int) -> Optional[Tuple[int, str]]:
    """Найти клиент, который держит замок, на портах `first … first + 19` (M2).

    Возвращает `(порт, папка)` первого ответившего photoprint; никто не
    ответил за 5 с — `None`.
    """
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        # M2: опрашиваются все порты диапазона — первый клиент мог уйти
        # дальше `first`, если начало диапазона было занято (M3). Начатый
        # круг доходит до конца и после 5 с: чужая программа, которая
        # молчит до тайм-аута, не должна спрятать живой клиент за собой.
        for port in range(first, first + PORT_COUNT):
            served = _served_folder(port)
            if served is not None:
                return port, served
        # M2: 5 с хватает клиенту, который только что взял замок, чтобы
        # подняться и ответить; дольше заказчик ждал бы молча.
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_INTERVAL)


def _port_answers(port: int) -> bool:
    """Узнать, слушает ли кто-то `127.0.0.1:port`: подключение за 0,3 с удалось (M3)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _bind_first_free(first: int) -> Optional[socket.socket]:
    """Занять первый свободный порт из `first … first + 19` на `127.0.0.1` (M3).

    Возвращает сокет с выполненным `bind`, ещё без `listen`; все порты
    заняты — `None`.
    """
    for port in range(first, first + PORT_COUNT):
        # M3: занятость видит проба `connect`: её примет и чужая программа
        # на `127.0.0.1`, и на `0.0.0.0`. Одного `bind` с `SO_REUSEADDR` мало
        # — поверх чужого `0.0.0.0` macOS его разрешает, и порт делили бы
        # две программы (§ 8 «Сокеты и замки»).
        if _port_answers(port):
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # M3: `SO_REUSEADDR` — чтобы порт, на котором после закрытого окна
        # ещё стоят соединения в `TIME_WAIT`, снова был свободен: клиент
        # встаёт на прежний порт, и старая вкладка браузера оживает сама.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            sock.close()
            continue
        # M3: сокет не закрывается и уходит в uvicorn (M5) как есть — между
        # проверкой порта и стартом сервера его никто не займёт.
        return sock
    return None


def _watch(port: int, folder: Path, ready: threading.Event) -> None:
    """Дождаться, пока сервер на `port` назовёт папку `folder`, открыть страницу и сказать «Готово» (M5).

    `ready` ставится, как только пришёл ответ со своей папкой: по нему
    основной поток отличает «сервер отработал» от «сервер не запустился».
    """
    url = _page_url(port)
    # M5: сравнивается и папка, а не только «это photoprint»: страница
    # открывается, только когда сервер отвечает ровно для той папки, ради
    # которой его запустили.
    while _served_folder(port) != str(folder):
        time.sleep(POLL_INTERVAL)
    ready.set()
    _open_page(url)
    print(READY.format(url=url), flush=True)


def _not_cancelled_request(record: logging.LogRecord) -> bool:
    """Фильтр лога `uvicorn.error`: `False` — запись об отменённом запросе, её в окно Терминала не пускать (M7).

    Такая запись — «Exception in ASGI application» с исключением
    `asyncio.CancelledError`. Все остальные записи проходят как были.
    """
    # M7: `CancelledError` доходит до лога uvicorn только одним путём. После
    # второго Ctrl+C uvicorn ставит `force_exit` и перестаёт ждать запросы,
    # и `asyncio.run` на выходе отменяет задачу незаконченного запроса.
    # Других отмен нет: тайм-аут остановки (`timeout_graceful_shutdown`) не
    # задан. Отмену заказал сам оператор. Поток печати (рабочий поток AnyIO)
    # она не прерывает. Предупреждения и ошибки uvicorn, на которые
    # ссылается «Клиент не запустился…» (M5), фильтр не трогает.
    exc_info = record.exc_info
    return not (exc_info and isinstance(exc_info[1], asyncio.CancelledError))


def _main(argv: Optional[List[str]]) -> int:
    """Выполнить запуск по аргументам `argv` и вернуть код выхода; Ctrl+C отсюда уходит исключением.

    Для `--folder`: 2 — папки нет (M1); 1 — клиент другой папки или
    зависший клиент держит замок (M2), все порты заняты (M3) или сервер не
    запустился (M5); 0 — открыт уже запущенный клиент (M2) или свой сервер
    работал и остановился. Для `--check-printer` — коды M6.
    """
    parser = argparse.ArgumentParser()
    # § 4.5: режимов запуска два — сервер для папки и проверка принтера.
    # Ровно один обязателен: ярлык (L3) и установщик (I8) всегда передают
    # один, а оба сразу или ни одного — ошибка вызова, argparse отвечает
    # usage и кодом 2.
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--folder", metavar="PATH")
    mode.add_argument("--check-printer", action="store_true")
    args = parser.parse_args(argv)

    # M6: проверка принтера идёт до замка и сервера и их не трогает:
    # установщик при обновлении вызывает её и при открытом окне клиента.
    if args.check_printer:
        return _check_printer()

    # M1: путь приводится до проверки: ярлык может прийти через
    # символическую ссылку или с `..`, а сравнивать с ответом сервера (M2,
    # M5) и записывать для установщика (M4) нужно настоящий путь.
    try:
        folder = Path(args.folder).resolve()
    except (OSError, RuntimeError):
        # M1: на петле ссылок `resolve()` в Python 3.9–3.12 бросает
        # `RuntimeError` (в 3.13 и новее — ничего, и петлю ловит проверка
        # `is_dir()` ниже); `OSError` — относительный путь при удалённом
        # текущем каталоге (`os.getcwd()` → `FileNotFoundError`). Приведённого
        # пути нет, поэтому в тексте путь как передан.
        print(FOLDER_NOT_FOUND.format(path=args.folder), file=sys.stderr, flush=True)
        return 2
    if not folder.is_dir():
        print(FOLDER_NOT_FOUND.format(path=folder), file=sys.stderr, flush=True)
        return 2

    first = int(os.environ.get("PHOTOPRINT_PORT", str(DEFAULT_PORT)))

    # M2: замок — до порта (M3) и записи папки (M4): отказанный запуск не
    # занимает порт и не подменяет в `<root>/folder` папку работающего
    # клиента, по которой установщик ставит ярлык (I7).
    if not _lock_instance():
        running = _find_running(first)
        if running is None:
            print(NOT_RESPONDING, flush=True)
            return 1
        port, served = running
        # M2: принтер один, поэтому и клиент один: для другой папки второй
        # клиент не поднимается, а страница первого показала бы оператору
        # чужие картинки.
        if served != str(folder):
            print(OTHER_FOLDER.format(folder=served), flush=True)
            return 1
        print(ALREADY_RUNNING, flush=True)
        _open_page(_page_url(port))
        return 0

    sock = _bind_first_free(first)
    if sock is None:
        print(PORTS_BUSY.format(first=first, last=first + PORT_COUNT - 1), flush=True)
        return 1
    port = sock.getsockname()[1]

    # M4: путь папки — для установщика: при обновлении он по этому файлу
    # находит папку, которую заказчик перенёс (I7). Без перевода строки —
    # файл читается целиком как путь.
    try:
        (ROOT / "folder").write_text(str(folder), encoding="utf-8")
    except OSError:
        # M4: файл нужен только установщику, а заказчику нужна печать —
        # защищённый или недоступный файл запуск не останавливает.
        pass

    # M5, M7: сервер, API и принтер импортируются только сейчас, когда они
    # нужны, — внутри `try` в `main` и после разбора аргументов (§ 4.5 M5;
    # см. комментарий к импортам в начале модуля). Не в начале `main` до
    # `try` и не под `if __name__ == "__main__"`: оттуда Ctrl+C посреди
    # импорта ушёл бы мимо `except KeyboardInterrupt` трассировкой.
    import uvicorn

    from photoprint.printer import UsbPrinter
    from photoprint.web import create_app

    app = create_app(folder, UsbPrinter(LIBUSB), APP / "static")
    # M5: уровень `warning` — в окне Терминала остаются только сбои
    # сервера, без строки на каждый запрос страницы. Тише нельзя: при
    # `error` пропали бы предупреждения, а при `critical` и ошибки uvicorn,
    # на которые ссылается «Клиент не запустился — причина в сообщениях выше.».
    # M7: `lifespan="off"` — у приложения нет обработчиков старта и
    # остановки (`create_app`), а включённый lifespan печатал трассировку
    # при двойном Ctrl+C. Второй SIGINT ставит uvicorn `force_exit`, и он
    # пропускает `lifespan.shutdown()`; asyncio на выходе отменяет висящую
    # задачу lifespan, и uvicorn пишет её `CancelledError` уровнем ERROR,
    # который `warning` пропускает в окно Терминала.
    config = uvicorn.Config(app, log_level="warning", lifespan="off")
    # M7: запрос в полёте при двойном Ctrl+C asyncio тоже отменяет, и
    # uvicorn пишет эту отмену с трассировкой. Фильтр стоит на логгере
    # `uvicorn.error`: в него пишет и сервер, и протокол HTTP (`h11_impl`),
    # а фильтр логгера видит только записи, созданные на нём самом.
    logging.getLogger("uvicorn.error").addFilter(_not_cancelled_request)
    server = uvicorn.Server(config)
    ready = threading.Event()
    # M5: наблюдатель — фоновый поток: основной занят сервером до конца
    # работы, а `daemon=True` не даёт наблюдателю держать процесс, если
    # сервер так и не ответил.
    threading.Thread(target=_watch, args=(port, folder, ready), daemon=True).start()
    try:
        # M3, M5: сервер слушает ровно тот сокет, что занял `_bind_first_free`.
        server.run(sockets=[sock])
    except SystemExit:
        # M5: uvicorn при сбое старта пишет причину в лог и вызывает
        # `sys.exit(1)`; ниже это превращается в «Клиент не запустился…».
        pass
    # M5: сервер вернул управление раньше, чем наблюдатель получил свою
    # папку, — страница не открывалась, и заказчику нужен понятный итог
    # под сообщениями uvicorn.
    if not ready.is_set():
        print(NOT_STARTED, flush=True)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Запустить клиент (`--folder`) или проверку принтера (`--check-printer`) и вернуть код выхода процесса.

    Коды — как у `_main`; Ctrl+C — 130 (M7).
    """
    # M7: своих обработчиков сигналов нет, и SIGHUP (закрыто окно
    # Терминала) завершает процесс действием по умолчанию. SIGINT и SIGTERM
    # во время работы сервера перехватывает uvicorn: он останавливает
    # сервер и посылает сигнал себе снова, уже с прежним обработчиком. Для
    # SIGINT это `KeyboardInterrupt` Python — как и во время опроса M2 или
    # старта. Исключение ловится вокруг всего запуска, а не только вокруг
    # сервера: заказчик в окне Терминала не должен видеть трассировку.
    try:
        return _main(argv)
    except KeyboardInterrupt:
        # M7: процесс после Ctrl+C выходит не сразу. Если шла печать,
        # интерпретатор на выходе ждёт её рабочий поток AnyIO (он не
        # фоновый), потом выполняет atexit (pyusb закрывает libusb).
        # Обработчик SIGINT Python бросил бы на следующем Ctrl+C
        # `KeyboardInterrupt` прямо там: «Exception ignored in: <module
        # 'threading'…>» и трассировка в окне Терминала. Возврат к действию по
        # умолчанию — не свой обработчик, а снятие обработчика Python:
        # следующий Ctrl+C завершает процесс сразу и молча, как любую
        # программу в Терминале.
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        return CTRL_C_EXIT


if __name__ == "__main__":
    sys.exit(main())
