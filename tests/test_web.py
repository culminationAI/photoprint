"""Тесты API — `photoprint.web` (спецификация § 4.4).

Закрепляют правила пули 1:
- W1 — список картинок с пригодностью и счётчиками в порядке `scan`;
  и папка, и файл счётчиков читаются заново при каждом запросе (S2);
- W2 — имя ищется только через `find`, иначе 404 с текстом; файлы, которых
  нет в записях `scan` (двойник `._*`, `.print-stats.json`), не отдаются
  и не печатаются (§ 6.3);
- W3 — байты файла с типом по расширению пути (не имени: имя с `:`
  Python 3.9 разобрал бы как адрес) без учёта регистра (`Photo.JPG` —
  `image/jpeg`), неизвестное расширение — `application/octet-stream`,
  непригодная картинка тоже отдаётся, нечитаемый файл — 404, как в W2;
- W4 — ответ печати — первый сработавший из 404, 422, 409, 503, 500, 200,
  строго в этом порядке и во время чужой печати тоже; счётчики меняются
  только после попытки печати; имя печати — ровно то, что сервер один раз
  декодировал из адреса (`a%41.jpg` — не `aA.jpg`);
- W5 — замок печати берётся без ожидания и отпускается при любом исходе:
  после 200, 404, 422, 500 и обоих 503; вторая печать во время первой
  отвечает 409 сразу, а не дождавшись замка, и при любом чередовании двух
  одновременных печатей картинка печатается ровно один раз — в том числе
  пока первая пишет счётчик: оба `increment` под замком.

И `GET /` отдаёт страницу для показа, а не для скачивания (§ 4.4, M5).

И правила пули 2 (задача 2.4):
- W6 — у каждого ответа не 200 — JSON со строкой `detail`: у отказов
  W2–W4, у 403 W9, у 500 W7 и W8, у 404 и 405 самого FastAPI; адрес с
  косой чертой на конце — JSON 404, а не пустой 307 (решение контролёра
  по задаче 2.4);
- W7 — нет доступа к папке — 500 с текстом, как дать доступ, у всех трёх
  ручек, которые читают папку; вернули доступ — список снова работает;
- W8 — любое другое неперехваченное исключение — 500 с его классом и
  текстом, а не голый 500 Starlette; не только `OSError` (переименованная
  папка) и `RuntimeError` (сбой принтера, пропавшая страница), но и свой
  класс прямо от `Exception`, и после такого сбоя замок печати отпущен;
- W9 — чужое имя хоста в `Host` — 403 у любой ручки (подмена DNS), и
  тогда, когда соединение пришло с `127.0.0.1`, как всегда у заказчика;
  печать с чужим `Origin` — 403 (чужая страница в браузере оператора);
  печать без `Origin` и со своим `Origin` на любом порту проходит; своих
  имён ровно два и сравниваются они целиком (`0.0.0.0`, `::1`, пустое имя,
  пустой `Origin`, подстрока и `testserver` из `TestClient` — 403);
  неразбираемый `Host` или `Origin` — тоже 403, а не 500 W8 (решение
  контролёра по задаче 2.4);
- W2, F7 — имена в формах Юникода NFD и NFC находятся ровно такими, как
  на диске (решение по задаче 1.4, отложено в 2.4).

И правила финальной проверки (задача 3.4a, 2026-09-28):
- W1 — ручка списка читает файл счётчиков один раз на запрос:
  `stats.snapshot()` до цикла по картинкам, без `stats.get` в цикле;
  проверка статическая, по байт-коду ручки, — время не меряется;
- W9 — страница `GET /` отдаётся с `Cache-Control: no-cache` (после
  обновления браузер берёт новую страницу) и с `X-Frame-Options: DENY` и
  `Content-Security-Policy: frame-ancestors 'none'` (чужой сайт не встроит
  её в рамку); запрос вовсе без `Host` (HTTP/1.0) — 403 у любой ручки.

Кроме того — ручки `/`, `/api/health` и `/api/printer` из таблицы § 4.4,
печать только методом `POST`, приведение папки к `folder.resolve()`,
выключенные документация и схема API и то, что все ручки — обычные `def`.

Приложение настоящее, запросы идут через `client_for` из `helpers` —
`TestClient` с адресом `http://127.0.0.1`, как у браузера оператора:
`Host: testserver`, который `TestClient` шлёт по умолчанию, W9 отклоняет.
Подменён только принтер — `FakePrinter` из `helpers` (§ 7.2, § 7.3).
Картинки, файл счётчиков и сбои чтения и записи настоящие: права файла и
каталога меняет `chmod_to`, папку — `rename`. Имена в адресе кодируются
`quote(name, safe="")`, как их кодирует страница (`encodeURIComponent`,
U8). Имена с буквальным `%` через `TestClient` не проверить: starlette
0.48 декодирует путь дважды. Их, как и адрес соединения `127.0.0.1` (W9),
проверяют тесты на настоящем uvicorn — `_real_server`: сервер работает в
потоке теста на сокете `127.0.0.1` с портом, который выбрала система, и
запросы идут по TCP через `http.client`, а запрос HTTP/1.0 без `Host` —
байтами прямо в сокет (`_http10_request`). Приложение и принтер те же, что
у остальных тестов (`FakePrinter`, § 7.3); настоящий `UsbPrinter`, как у
подпроцесса задачи 1.6, здесь не нужен и не трогается.

Тесты, где печать идёт во время другого запроса, открывают клиент через
`with client_for(...)`: тогда все запросы идут в одном цикле событий, как
на uvicorn. Без `with` `TestClient` даёт каждому запросу свой цикл, и
ручка, которая держит цикл всю печать, такие тесты прошла бы.

Тесты гонки двух печатей (W5) — `test_print_race_prints_once_at_every_line`
и `test_print_race_counters_are_under_lock` — вызывают настоящую ручку
печати из двух потоков и расставляют их через `sys.settrace`: трассировка
только останавливает поток перед строкой ручки, код не меняется. Решение
контролёра (§ 7.3): `sys.settrace` — только в тестах гонки W5.
"""
from __future__ import annotations

import dis
import http.client
import inspect
import json
import linecache
import mimetypes
import os
import socket
import stat
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from types import CodeType
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from helpers import FakePrinter, client_for, make_jpeg, make_png, truncate
from photoprint import web
from photoprint.printer import PRINTER_FAILED, PRINTER_NOT_FOUND
from photoprint.stats import STATS_FILE, Counts, Stats
from photoprint.web import create_app

# Заглушка страницы: тест `/` сверяет, что отдан именно этот файл.
INDEX = "<html>stub</html>"
# Полный текст ошибки F3 для JPEG шириной 800 px — его показывают и список
# (W1), и отказ печати (W4 п. 2).
WIDE_ERROR = "неверный формат изображения: ширина 800 px, нужна 576"
# W5: сколько секунд печать, заставшая замок занятым, может идти до ответа,
# пока первая печать ещё держит замок. Без ожидания 409 приходит за
# миллисекунды — предел берётся с запасом для загруженной машины. Печать
# короткой картинки на настоящем принтере заканчивается быстрее 2 с (P7:
# принтер принял данные), поэтому ожидание замка дольше 1 с дождалось
# бы её конца и напечатало картинку второй раз.
NO_WAIT_LIMIT = 1.0
# W7, W8, W9: тексты для оператора дословно из § 4.4. Тесты сверяют с ними
# и ответ API, и одноимённую константу `photoprint.web`: опечатка в
# константе не пройдёт, а текст для людей по общим правилам проекта —
# константа модуля с именем из задачи (2.4: `NO_ACCESS`, `CLIENT_ERROR`,
# `FOREIGN_REQUEST`).
NO_ACCESS = (
    "Нет доступа к папке {folder}. Разрешите Терминалу доступ: Системные настройки"
    " → Конфиденциальность и безопасность → Файлы и папки → Терминал."
)
CLIENT_ERROR = (
    "Ошибка клиента: {kind}: {error}. Закройте окно «Печать картинок» и запустите его снова."
)
FOREIGN_REQUEST = "Запрос отклонён: клиент принимает запросы только с этого компьютера."


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """Пустая папка с картинками; имя с пробелом и кириллицей, как у заказчика."""
    path = tmp_path / "Печать картинок"
    path.mkdir()
    return path


@pytest.fixture
def static(tmp_path: Path) -> Path:
    """Каталог страницы с заглушкой `index.html`, отдельно от папки с картинками."""
    path = tmp_path / "static"
    path.mkdir()
    (path / "index.html").write_text(INDEX, encoding="utf-8")
    return path


def _client(folder: Path, printer: FakePrinter, static: Path) -> TestClient:
    """Собрать приложение для папки и принтера и вернуть клиент к нему.

    Клиент — `client_for`: запросы идут на `127.0.0.1`, как из браузера
    оператора, и проверка `Host` (W9) их пропускает.
    """
    return client_for(create_app(folder, printer, static))


def _image_url(name: str) -> str:
    """Адрес байтов картинки: имя закодировано целиком, как `encodeURIComponent`."""
    return "/api/images/" + quote(name, safe="")


def _print_url(name: str) -> str:
    """Адрес печати картинки: имя закодировано целиком, как `encodeURIComponent`."""
    return "/api/print/" + quote(name, safe="")


def _via_symlink(folder: Path) -> Path:
    """Вернуть символическую ссылку на `folder` — путь, который ещё не приведён.

    Через неё видно, что `create_app` приводит папку к `folder.resolve()`:
    без этого API показал бы путь ссылки, а не настоящей папки.
    """
    link = folder.parent / "ссылка на папку"
    link.symlink_to(folder, target_is_directory=True)
    # Предпосылка теста: путь ссылки и приведённый путь различаются.
    assert str(link.resolve()) != str(link)
    return link


def _stats_file(folder: Path) -> Path:
    """Путь к файлу счётчиков папки (§ 4.2)."""
    return folder / STATS_FILE


def _listed_names(client: TestClient) -> List[str]:
    """Имена картинок из `GET /api/images` в порядке ответа (W1)."""
    return [image["name"] for image in client.get("/api/images").json()["images"]]


@contextmanager
def _held_print(
    client: TestClient, fake: FakePrinter, name: str
) -> Iterator[List[httpx.Response]]:
    """Держать печать `name` в отдельном потоке, пока выполняется блок `with` (W5).

    Печать уходит из потока и останавливается в `FakePrinter(hold=True)`
    на событии `started`: замок печати занят, и блок `with` шлёт свои
    запросы именно в это время. На выходе принтер отпускается при любом
    исходе, поток дожидается, и в отданном списке оказывается ответ этой
    печати. Ошибка потока сохраняется, а не вылетает из него: pytest
    превратил бы её в предупреждение, а тест должен упасть с ней самой.
    """
    first: List[httpx.Response] = []
    errors: List[BaseException] = []

    def post_first() -> None:
        """Отправить печать из потока и сохранить ответ или ошибку."""
        try:
            first.append(client.post(_print_url(name)))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=post_first)
    thread.start()
    try:
        # Предпосылка: печать дошла до принтера, то есть замок уже занят.
        assert fake.started.wait(5)
        yield first
    finally:
        # Принтер отпускается при любом исходе, иначе поток ждал бы 5 с.
        fake.release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert errors == []


def _wait_until(condition: Callable[[], bool], timeout: float) -> bool:
    """Ждать, пока `condition()` не станет истинным, не дольше `timeout` секунд.

    Тест гонки W5 ждёт так сразу нескольких событий потоков; предел не даёт
    зависшему потоку повесить весь набор — тест падает с понятным местом.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.001)
    return True


def _print_endpoint(app: FastAPI) -> Callable[[str], Any]:
    """Функция ручки `POST /api/print/{name}` — та самая, которую FastAPI зовёт в пуле потоков (W5)."""
    [endpoint] = [
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/api/print/{name}"
    ]
    return endpoint


@contextmanager
def _real_server(app: FastAPI) -> Iterator[int]:
    """Запустить `app` на настоящем uvicorn в потоке теста и отдать его порт (W9, W2, W4).

    Так, как у заказчика: сервер слушает `127.0.0.1` (§ 6.3), соединение
    приходит по TCP, и uvicorn сам декодирует путь запроса один раз и сам
    заполняет адрес соединения — `127.0.0.1`. `TestClient` этого не
    повторяет: адрес соединения у него `testclient`, а путь он декодирует
    дважды.

    Сокет занимается заранее с портом `0` — порт выбирает система, и
    соседние наборы, которые гоняются одновременно, его не займут. Настройки
    сервера — как у запуска (M5): `lifespan="off"`; `log_config=None` — чтобы
    не перенастраивать логи процесса pytest. Сервер останавливается при любом
    исходе блока `with`; поток, который не закончился за 10 с, — провал.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]})
        thread.start()
        try:
            # Предпосылка: сервер слушает, а не упал при старте.
            assert _wait_until(lambda: server.started or not thread.is_alive(), 10)
            assert server.started
            yield port
        finally:
            server.should_exit = True
            thread.join(10)
        assert not thread.is_alive()
    finally:
        sock.close()


def _raw_request(port: int, method: str, path: str, host: str) -> Tuple[int, str, bytes]:
    """Отправить запрос `method path` с заголовком `Host: host` по TCP и вернуть код, тип и тело.

    `http.client` шлёт путь байт в байт, как браузер после
    `encodeURIComponent` (U8), и `Host` ровно таким, как задан: так
    приходит запрос страницы, чьё имя подменили на `127.0.0.1` (W9).
    Соединение своё на каждый запрос и закрывается сразу.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, headers={"Host": host})
        response = connection.getresponse()
        return response.status, response.getheader("content-type", ""), response.read()
    finally:
        connection.close()


def _http10_request(
    port: int, method: str, path: str, host: Optional[str]
) -> Tuple[int, str, bytes]:
    """Отправить по TCP запрос HTTP/1.0 с `Host: host` или, при `None`, без `Host` (W9).

    Возвращает код, тип и тело ответа. `http.client` сам добавляет `Host`
    к каждому запросу, поэтому запрос собирается байтами. HTTP/1.0
    разрешает запрос без `Host` (так шлёт старая программа или скрипт на
    этом маке), и uvicorn передаёт его приложению. Тело пустое —
    `Content-Length: 0`, как у печати со страницы. Ответ разбирает
    `http.client.HTTPResponse` с того же сокета; сервер закрывает
    соединение после ответа на HTTP/1.0.
    """
    lines = ["{} {} HTTP/1.0".format(method, path)]
    if host is not None:
        lines.append("Host: " + host)
    lines.append("Content-Length: 0")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request)
        response = http.client.HTTPResponse(sock, method=method)
        try:
            response.begin()
            return response.status, response.getheader("content-type", ""), response.read()
        finally:
            response.close()


def _race_paused_at(
    folder: Path, static: Path, line: int, printer: Optional[FakePrinter] = None
) -> Optional[Tuple[Dict[str, int], int]]:
    """Две печати `good.jpg` сразу: первая стоит перед строкой `line` ручки, вторая идёт (W5).

    Приложение, замок, папка и счётчики настоящие, свои на каждый вызов;
    принтер — `FakePrinter(hold=True)`. Первый поток вызывает ручку печати
    под `sys.settrace` и останавливается перед строкой `line` — так
    планировщик мог бы отдать процессор другому запросу ровно в этом месте
    (трассировка только держит поток, код не меняется; § 7.3). Пока он
    стоит, второй поток вызывает ту же ручку, пока не получит ответ или не
    начнёт печатать: тогда замок у него, и принтер держит его печать.
    Потом первый продолжает, и у него `NO_WAIT_LIMIT` на ответ, пока
    вторая печать ещё идёт: без ожидания замка (W5) 409 приходит сразу, а
    ожидание замка дождалось бы конца второй печати и напечатало бы
    картинку ещё раз. В конце принтер отпускается при любом исходе.

    Возвращает коды ответов потоков (`first`, `second`) и число картинок,
    дошедших до принтера. `None` — первый поток дошёл до принтера раньше
    строки `line` (ветка отказа или код после печати): одновременного входа
    в этой точке нет, и второй поток не запускается.

    `printer` — принтер вместо `FakePrinter(hold=True)`. Принтер без `hold`
    не держит первую печать, и первый поток доходит до строк после
    принтера — записи счётчиков (W5); вторая печать тогда тоже не держится
    и отвечает сама.
    """
    fake = FakePrinter(hold=True) if printer is None else printer
    endpoint = _print_endpoint(create_app(folder, fake, static))
    paused = threading.Event()
    resume = threading.Event()
    codes: Dict[str, int] = {}
    errors: List[BaseException] = []

    def pause_at_line(frame: Any, event: str, arg: Any) -> Any:
        """Остановить поток перед строкой `line` ручки печати — только в первый раз."""
        if event == "line" and frame.f_lineno == line and not paused.is_set():
            paused.set()
            resume.wait(10)
        return pause_at_line

    def trace_endpoint(frame: Any, event: str, arg: Any) -> Any:
        """Трассировать только кадр ручки печати; прочий код потока идёт как есть."""
        return pause_at_line if frame.f_code is endpoint.__code__ else None

    def call(key: str, traced: bool) -> None:
        """Вызвать ручку печати `good.jpg` и сохранить код ответа или ошибку потока."""
        if traced:
            sys.settrace(trace_endpoint)
        try:
            codes[key] = endpoint("good.jpg").status_code
        except BaseException as exc:
            errors.append(exc)
        finally:
            sys.settrace(None)

    first = threading.Thread(target=call, args=("first", True))
    second = threading.Thread(target=call, args=("second", False))
    first.start()
    try:
        # Первый встал перед строкой, начал печатать или уже закончил.
        assert _wait_until(
            lambda: paused.is_set() or fake.started.is_set() or not first.is_alive(), 10
        ), line
        raced = paused.is_set()
        if raced:
            second.start()
            # Второй получил ответ или печатает — тогда замок у него.
            assert _wait_until(lambda: fake.started.is_set() or not second.is_alive(), 10), line
            resume.set()
            if second.is_alive():
                # Вторая печать держит замок: первый должен ответить сам, не
                # дожидаясь её конца.
                first.join(NO_WAIT_LIMIT)
    finally:
        # Всё отпускается при любом исходе, иначе потоки ждали бы свои 5–10 с.
        resume.set()
        fake.release.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive(), line
    assert not second.is_alive(), line
    assert errors == [], line
    return (codes, len(fake.images)) if raced else None


def test_health(folder: Path, static: Path) -> None:
    """§ 4.4: `/api/health` отвечает именем приложения и приведённой папкой.

    Приложение собрано по символической ссылке, а в ответе — настоящий
    путь папки: `create_app` делает `folder.resolve()`.
    """
    link = _via_symlink(folder)

    response = _client(link, FakePrinter(), static).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"app": "photoprint", "folder": str(link.resolve())}


def test_index_is_served(folder: Path, static: Path) -> None:
    """§ 4.4, M5: `GET /` отдаёт `index.html` из каталога страницы как HTML — для показа, а не скачивания.

    В `Content-Disposition` нет `attachment`: его ставит
    `FileResponse(..., filename=...)` — обычная запись из документации
    Starlette. С ним браузер, который открывает запуск (M5), не показал бы
    страницу, а скачал бы `index.html` в «Загрузки», и заказчик с первого
    запуска увидел бы вместо клиента пустую вкладку. Безвредный `inline`
    тест пропускает.
    """
    response = _client(folder, FakePrinter(), static).get("/")

    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment" not in response.headers.get("content-disposition", "")


def test_page_is_revalidated_after_update(folder: Path, static: Path) -> None:
    """W9, § 4.4 (финальная проверка, 2026-09-28): `GET /` — с `Cache-Control: no-cache`.

    Без этого заголовка браузер сам решает, сколько держать страницу в
    кэше: `FileResponse` шлёт только `Last-Modified` и `ETag`, и по RFC 9111
    § 4.2.2 страница считается свежей десятую часть своего возраста.
    Установщик распаковывает архив с датами коммита (I3), после обновления
    (I11) клиент поднимается на том же порту (M3), ярлык открывает тот же
    адрес (M5), а Chrome и Safari днями показывают прежнюю страницу:
    исправления страницы до заказчика не доходят, а старая страница
    говорит с новым сервером. `no-cache` велит браузеру сверяться с
    сервером при каждом открытии. Значение сверяется целиком: один
    заголовок, без добавок.
    """
    response = _client(folder, FakePrinter(), static).get("/")

    assert response.status_code == 200
    # Предпосылка: это сама страница, а не отказ.
    assert response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"


def test_page_refuses_to_be_framed(folder: Path, static: Path) -> None:
    """W9 (финальная проверка, 2026-09-28): страницу `GET /` нельзя встроить в рамку чужого сайта.

    Чужая страница, открытая в браузере оператора, может положить
    страницу клиента в невидимую рамку (`iframe`) поверх своей кнопки.
    Щелчок оператора попадает в «Печать» клиента, и браузер шлёт печать с
    собственным `Origin` клиента — проверка `Origin` W9 такую печать
    пропускает. Поэтому страница отдаётся с `X-Frame-Options: DENY` (для
    браузеров, которые знают только его) и `Content-Security-Policy:
    frame-ancestors 'none'` (нынешнее правило): рамку с ней браузер не
    показывает, и щелчку некуда попасть. Значения сверяются целиком.
    """
    response = _client(folder, FakePrinter(), static).get("/")

    assert response.status_code == 200
    # Предпосылка: это сама страница, а не отказ.
    assert response.text == INDEX
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_no_docs(folder: Path, static: Path) -> None:
    """§ 4.4: документации и схемы API на сервере нет.

    `docs_url`, `redoc_url` и `openapi_url` выключены: оператору они не
    нужны, и лишних адресов на сервере не остаётся. Каждый из трёх
    адресов FastAPI по умолчанию — 404, и этот 404 тоже JSON со строкой
    `detail` (W6).
    """
    client = _client(folder, FakePrinter(), static)

    for url in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(url)
        assert response.status_code == 404, url
        assert response.headers["content-type"] == "application/json", url
        assert response.json() == {"detail": "Not Found"}, url


def test_handlers_are_plain_def(folder: Path, static: Path) -> None:
    """§ 4.4, W5, Review Focus 4: все шесть ручек — обычные `def`, ни одной `async def`.

    FastAPI выполняет `def` в пуле потоков, а `async def` — прямо в цикле
    событий uvicorn. Ручка `async def` с долгой печатью или чтением
    большой папки остановила бы весь сервер: список не обновлялся бы, а
    вторая печать ждала бы первую вместо 409 и печатала картинку ещё раз.
    """
    app = create_app(folder, FakePrinter(), static)
    endpoints = {route.path: route.endpoint for route in app.routes if isinstance(route, APIRoute)}

    # Предпосылка: проверены именно шесть ручек таблицы § 4.4.
    assert sorted(endpoints) == [
        "/",
        "/api/health",
        "/api/images",
        "/api/images/{name}",
        "/api/print/{name}",
        "/api/printer",
    ]
    assert [path for path, endpoint in endpoints.items() if inspect.iscoroutinefunction(endpoint)] == []


def test_images_list_shape_and_order(folder: Path, static: Path) -> None:
    """W1, § 4.4: список — поля каждой картинки и порядок `scan` (F2).

    `wide.jpg` положен позже, поэтому он первый, хотя по имени второй.
    У непригодной картинки `ok` ложно и есть полный текст причины F3, у
    пригодной `ok` истинно и `error` — `null`. Счётчиков ещё нет — нули.
    `ctime` — число с плавающей точкой из `os.stat`, оно сверяется
    отдельно. Папка в ответе — приведённая, хотя приложение собрано по
    символической ссылке.
    """
    good = make_jpeg(folder / "good.jpg", 576, 300)
    wide = make_jpeg(folder / "wide.jpg", 800, 300)
    # Предпосылка теста: `wide.jpg` новее, поэтому по F2 он сверху.
    assert wide.stat().st_ctime > good.stat().st_ctime
    link = _via_symlink(folder)

    response = _client(link, FakePrinter(), static).get("/api/images")

    assert response.status_code == 200
    body = response.json()
    ctimes = [image.pop("ctime") for image in body["images"]]
    assert body == {
        "folder": str(link.resolve()),
        "images": [
            {"name": "wide.jpg", "ok": False, "error": WIDE_ERROR, "printed": 0, "failed": 0},
            {"name": "good.jpg", "ok": True, "error": None, "printed": 0, "failed": 0},
        ],
    }
    assert all(isinstance(ctime, float) for ctime in ctimes)
    assert ctimes == [wide.stat().st_ctime, good.stat().st_ctime]


def test_images_show_counters(folder: Path, static: Path) -> None:
    """W1: `printed` и `failed` в списке берутся из файла счётчиков папки."""
    make_jpeg(folder / "good.jpg", 576, 300)
    _stats_file(folder).write_text(
        json.dumps({"good.jpg": {"printed": 3, "failed": 1}}), encoding="utf-8"
    )

    response = _client(folder, FakePrinter(), static).get("/api/images")

    assert response.status_code == 200
    [image] = [i for i in response.json()["images"] if i["name"] == "good.jpg"]
    assert image["printed"] == 3
    assert image["failed"] == 1


def test_images_list_reads_folder_each_time(folder: Path, static: Path) -> None:
    """W1, F2, S2, U6: список читает папку заново при каждом запросе.

    Клиент уже работает, когда заказчик кладёт в папку `new.jpg`: следующий
    запрос списка показывает его сверху (F2), а удалённый `good.jpg` из
    списка пропадает. Список, собранный один раз при запуске, не показал
    бы картинку, положенную позже, — хотя страница обещает «картинка
    появится здесь сама» (U6), — а удалённая осталась бы мёртвой карточкой
    до перезапуска.
    """
    good = make_jpeg(folder / "good.jpg", 576, 300)
    client = _client(folder, FakePrinter(), static)
    assert _listed_names(client) == ["good.jpg"]

    new = make_jpeg(folder / "new.jpg", 576, 300)
    # Предпосылка: `new.jpg` новее, поэтому по F2 он сверху.
    assert new.stat().st_ctime > good.stat().st_ctime
    assert _listed_names(client) == ["new.jpg", "good.jpg"]

    good.unlink()
    assert _listed_names(client) == ["new.jpg"]


def test_images_counters_fresh_after_print_and_hand_edit(folder: Path, static: Path) -> None:
    """W1, S2, U4: счётчики в списке читаются из файла при каждом запросе.

    Страница берёт счётчики карточки только из списка и перезапрашивает
    его сразу после ответа печати (U4). Поэтому после 200 список
    показывает `printed == 1`, а после правки файла счётчиков руками при
    работающем клиенте — новые числа (S2). Счётчики, запомненные при
    первом запросе, оставили бы на карточке «напечатано: 0» навсегда, и
    оператор решил бы, что картинка не напечаталась, и напечатал её снова.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    client = _client(folder, FakePrinter(), static)

    def listed_counts() -> List[int]:
        """Счётчики `good.jpg` из `GET /api/images`: `[printed, failed]`."""
        [image] = client.get("/api/images").json()["images"]
        return [image["printed"], image["failed"]]

    assert listed_counts() == [0, 0]
    assert client.post(_print_url("good.jpg")).status_code == 200
    assert listed_counts() == [1, 0]

    _stats_file(folder).write_text(
        json.dumps({"good.jpg": {"printed": 7, "failed": 2}}), encoding="utf-8"
    )
    assert listed_counts() == [7, 2]


def _nested_codes(code: CodeType) -> List[CodeType]:
    """Вложенные объекты кода `code` на любой глубине: включения, `lambda`, функции."""
    found: List[CodeType] = []
    for const in code.co_consts:
        if isinstance(const, CodeType):
            found.append(const)
            found.extend(_nested_codes(const))
    return found


def test_images_list_reads_stats_once(folder: Path, static: Path) -> None:
    """W1, S2 (финальная проверка, 2026-09-28): список читает файл счётчиков раз на запрос.

    Счётчики списка — из одного `stats.snapshot()`, взятого до цикла по
    картинкам, а не `stats.get` на каждую картинку. `get` читает и
    разбирает весь файл счётчиков, а записи удалённых картинок в нём
    остаются навсегда (S6): при тысяче картинок и опросе раз в 3 с (U5)
    список иначе занимал бы почти секунду процессора, и чем дольше
    работает клиент, тем медленнее. S2 держится: файл читается заново при
    каждом запросе списка (`test_images_counters_fresh_after_print_and_hand_edit`).

    Проверка статическая, по байт-коду ручки: у ручки ровно одно
    обращение к `stats` — `snapshot`, и оно не внутри цикла (ни один
    переход назад не возвращается к нему или раньше). Вложенный код ручки
    (включение, `lambda`) `stats` не трогает: иначе чтение ушло бы в
    него. Время не меряется: под нагрузкой машины такой тест падал бы
    ложно.
    """
    app = create_app(folder, FakePrinter(), static)
    [endpoint] = [
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/api/images"
    ]
    code = endpoint.__code__
    instructions = list(dis.get_instructions(code))

    def where(offset: int) -> str:
        """Строка ручки с байт-кодом по смещению `offset` — для сообщения о провале."""
        line = max(line for start, line in dis.findlinestarts(code) if start <= offset)
        return "{}: {}".format(line, linecache.getline(code.co_filename, line).strip())

    # Каждое обращение к `stats` в ручке — имя атрибута сразу за ним.
    uses = [
        (instructions[i + 1].argval, instructions[i].offset)
        for i, instruction in enumerate(instructions)
        if instruction.opname == "LOAD_DEREF" and instruction.argval == "stats"
    ]
    assert [name for name, _ in uses] == ["snapshot"], [where(offset) for _, offset in uses]
    [(_, offset)] = uses
    # Предпосылка: цикл по картинкам в ручке есть, и проверка ниже не пустая.
    assert any(instruction.opname == "FOR_ITER" for instruction in instructions)
    # Цикл — это переход назад: из точки после чтения к нему или раньше.
    loops_back = [
        where(instruction.offset)
        for instruction in instructions
        if instruction.opcode in dis.hasjabs + dis.hasjrel
        and instruction.argval <= offset < instruction.offset
    ]
    assert loops_back == [], where(offset)
    assert [c.co_name for c in _nested_codes(code) if "stats" in c.co_freevars] == []


def test_image_bytes_and_type(folder: Path, static: Path) -> None:
    """W3: картинка отдаётся байт в байт, тип — по расширению файла.

    Непригодные картинки (PNG, JPEG шириной 800 и HEIC) тоже отдаются:
    миниатюра нужна и им, чтобы оператор видел, о каком файле речь.
    Расширение, которого не знает `mimetypes` (`.heic` на Python 3.9),
    отдаётся с общим двоичным типом `application/octet-stream`, а не без
    типа вовсе.

    Регистр расширения не важен: `Photo.JPG` (так называют снимки
    фотоаппараты и программы Windows, и F1 его берёт) — тоже `image/jpeg`.
    Поиск типа по суффиксу с учётом регистра отдал бы его как
    `application/octet-stream`, и «открыть картинку в новой вкладке»
    скачивало бы файл вместо показа.
    """
    good = make_jpeg(folder / "good.jpg", 576, 300)
    # Другая заливка — другие байты: видно, что отдан именно этот файл.
    upper = make_jpeg(folder / "Photo.JPG", 576, 300, color=(0, 0, 0))
    logo = make_png(folder / "logo.png", 576, 300)
    wide = make_jpeg(folder / "wide.jpg", 800, 300)
    heic = folder / "photo.heic"
    # Содержимое HEIC не важно: W3 отдаёт байты как есть, не разбирая их.
    heic.write_bytes(b"not really heic")
    # Предпосылка: `mimetypes` этой машины `.heic` не знает — иначе тип
    # пришёл бы из него, а не из запасного значения W3.
    assert mimetypes.guess_type("photo.heic") == (None, None)
    client = _client(folder, FakePrinter(), static)

    response = client.get(_image_url("good.jpg"))
    assert response.status_code == 200
    assert response.content == good.read_bytes()
    assert response.headers["content-type"] == "image/jpeg"

    response = client.get(_image_url("Photo.JPG"))
    assert response.status_code == 200
    assert response.content == upper.read_bytes()
    assert response.headers["content-type"] == "image/jpeg"

    response = client.get(_image_url("logo.png"))
    assert response.status_code == 200
    assert response.content == logo.read_bytes()
    assert response.headers["content-type"] == "image/png"

    response = client.get(_image_url("wide.jpg"))
    assert response.status_code == 200
    assert response.content == wide.read_bytes()

    response = client.get(_image_url("photo.heic"))
    assert response.status_code == 200
    assert response.content == heic.read_bytes()
    assert response.headers["content-type"] == "application/octet-stream"


def test_thumbnail_type_by_path(folder: Path, static: Path) -> None:
    """W3: тип миниатюры — по пути файла, а не по имени: `data:menu.jpg` — это `image/jpeg`.

    Двоеточие в имени файла на Mac бывает: так на диске хранится `/`,
    набранный в имени в Finder. `mimetypes.guess_type` на Python 3.9
    разбирает строку как адрес, и имя `data:menu.jpg` для него — адрес со
    схемой `data:` без запятой, у которого типа нет: отдался бы общий
    двоичный `application/octet-stream`. Путь начинается с `/`, схемы в
    нём нет, и тип даёт расширение `.jpg`. Байты — ровно те, что на диске.
    """
    path = make_jpeg(folder / "data:menu.jpg", 576, 300)
    # Предпосылка: по имени `mimetypes` типа не даёт — иначе тест не отличил
    # бы тип по пути от типа по имени.
    assert mimetypes.guess_type("data:menu.jpg") == (None, None)
    client = _client(folder, FakePrinter(), static)

    response = client.get(_image_url("data:menu.jpg"))

    assert response.status_code == 200
    assert response.content == path.read_bytes()
    assert response.headers["content-type"] == "image/jpeg"


def test_image_unknown_name_is_404(folder: Path, static: Path) -> None:
    """W2: имени нет в папке — 404 и текст с этим именем в «ёлочках»."""
    make_jpeg(folder / "good.jpg", 576, 300)

    response = _client(folder, FakePrinter(), static).get(_image_url("nope.jpg"))

    assert response.status_code == 404
    assert response.json() == {"detail": "файла «nope.jpg» нет в папке"}


def test_image_unreadable_is_404(
    folder: Path, static: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """W3: файл есть в папке, но не читается, — 404 с текстом W2, а не 500.

    Права файла `000`: `stat` работает, поэтому `scan` видит файл и `find`
    его находит, а `read_bytes` бросает `PermissionError`. Ручка отвечает
    тем же 404, что и для имени, которого нет. Байты читаются в самой
    ручке, а не через `FileResponse`: тот читал бы файл уже после выхода
    из ручки, и сбой чтения дал бы голый 500.
    """
    good = make_jpeg(folder / "good.jpg", 576, 300)
    chmod_to(good, 0o000)
    client = _client(folder, FakePrinter(), static)
    # Предпосылка: файл в списке — 404 даёт чтение байтов, а не `find`.
    assert _listed_names(client) == ["good.jpg"]

    response = client.get(_image_url("good.jpg"))

    assert response.status_code == 404
    assert response.json() == {"detail": "файла «good.jpg» нет в папке"}


def test_only_scan_entries_are_served_or_printed(folder: Path, static: Path) -> None:
    """W2, F7, § 6.3: отдаются и печатаются только файлы из записей `scan`.

    Кроме `good.jpg`, в папке лежат файлы, которые на диске есть, но в
    список не попадают (F1): двойник `._good.jpg` с флешки — настоящий
    JPEG 576 px (Review Focus 2), `notes.txt` и файл счётчиков
    `.print-stats.json`. По каждому из этих имён и миниатюра, и печать —
    404 с текстом W2: имя ищется через `find`, а не собирается в путь
    `folder / name`. Принтер не вызван, файл счётчиков не изменился.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    make_jpeg(folder / "._good.jpg", 576, 300)
    (folder / "notes.txt").write_text("заметки", encoding="utf-8")
    stats_text = json.dumps({"good.jpg": {"printed": 1, "failed": 0}})
    _stats_file(folder).write_text(stats_text, encoding="utf-8")
    fake = FakePrinter()
    client = _client(folder, fake, static)
    # Предпосылка: в списке только `good.jpg`, остальные файлы `scan` не берёт.
    assert _listed_names(client) == ["good.jpg"]

    for name in ("._good.jpg", "notes.txt", STATS_FILE):
        detail = {"detail": "файла «{}» нет в папке".format(name)}
        response = client.get(_image_url(name))
        assert response.status_code == 404, name
        assert response.json() == detail
        response = client.post(_print_url(name))
        assert response.status_code == 404, name
        assert response.json() == detail

    assert fake.images == []
    assert _stats_file(folder).read_text(encoding="utf-8") == stats_text


def test_names_with_cyrillic_space_hash_plus(folder: Path, static: Path) -> None:
    """W2, W3, W4, Review Focus 5: имя с кириллицей, пробелом, `#`, `+` и `?`.

    Закодированное имя доходит до `find` ровно таким, как на диске:
    миниатюра отдаётся, печать проходит, и счётчик ведётся под этим именем.
    """
    name = "кот #1 + 2?.jpg"
    path = make_jpeg(folder / name, 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.get(_image_url(name))
    assert response.status_code == 200
    assert response.content == path.read_bytes()

    response = client.post(_print_url(name))
    assert response.status_code == 200
    assert response.json() == {"name": name, "printed": 1, "failed": 0}
    assert len(fake.images) == 1


def test_unicode_forms_round_trip(folder: Path, static: Path) -> None:
    """W2, F7, S6, Review Focus 5: имена в формах NFD и NFC — миниатюра и печать по точному имени.

    Одна и та же кириллица бывает в двух формах Юникода: разложенной NFD
    («й» — это «и» и отдельный знак краткой; так пишут имена диски HFS+ и
    многие программы Mac) и составной NFC. APFS хранит имя в той форме, в
    какой его записали, `os.listdir` отдаёт его как есть, страница
    отправляет его как есть (U8), а `find` сравнивает точно (F7). Если
    бы имя из адреса приводилось к одной форме — любой из двух, — файлы
    другой формы давали бы 404 и на миниатюре, и на печати. Поэтому здесь
    обе: `йогурт.jpg` в NFD и `ёлка.jpg` в NFC. Счётчик ведётся под точным
    именем (S6). Отложено из задачи 1.4: тест закрывает выжившего мутанта
    ворот 1 «сравнение имён после NFC».
    """
    nfd = unicodedata.normalize("NFD", "йогурт.jpg")
    nfc = unicodedata.normalize("NFC", "ёлка.jpg")
    # Предпосылка: у каждого имени другая форма — другая строка, иначе тест
    # проверил бы одну и ту же форму дважды.
    assert nfd != unicodedata.normalize("NFC", nfd)
    assert nfc != unicodedata.normalize("NFD", nfc)
    paths = {name: make_jpeg(folder / name, 576, 300) for name in (nfd, nfc)}
    # Предпосылка: диск сохранил обе формы как записаны.
    assert sorted(os.listdir(folder)) == sorted([nfd, nfc])
    fake = FakePrinter()
    client = _client(folder, fake, static)
    assert sorted(_listed_names(client)) == sorted([nfd, nfc])

    for name, path in paths.items():
        # `ascii(name)` в сообщении показывает, какая форма не прошла.
        response = client.get(_image_url(name))
        assert response.status_code == 200, ascii(name)
        assert response.content == path.read_bytes(), ascii(name)
        response = client.post(_print_url(name))
        assert response.status_code == 200, ascii(name)
        assert response.json() == {"name": name, "printed": 1, "failed": 0}, ascii(name)
        assert Stats(folder).get(name) == Counts(1, 0), ascii(name)

    assert len(fake.images) == 2
    # S6, W1: и список берёт счётчики под точным именем. Счётчики списка
    # читаются одним снимком файла (`Stats.snapshot`), и ключ ищет сама
    # ручка: приведи она имя к NFC (или к NFD), у картинки другой формы на
    # странице стоял бы ноль, хотя печать прошла (финальная проверка).
    listed = {i["name"]: (i["printed"], i["failed"]) for i in client.get("/api/images").json()["images"]}
    assert listed == {nfd: (1, 0), nfc: (1, 0)}


def test_print_name_with_literal_percent_on_real_server(folder: Path, static: Path) -> None:
    """W2, W4, F7, S6, Review Focus 5: печать берёт имя ровно таким, каким его один раз декодировал сервер.

    Имена с буквальным `%` бывают у файлов, скачанных из интернета
    (`My%20Photo.jpg`). Страница кодирует имя (`encodeURIComponent`, U8):
    `a%41.jpg` уходит как `a%2541.jpg`. uvicorn декодирует путь один раз и
    передаёт ручке `a%41.jpg`. Ручка, которая декодирует имя ещё раз,
    превратила бы `a%41.jpg` в `aA.jpg` и напечатала чужую картинку с её
    счётом, а `My%20Photo.jpg` — в `My Photo.jpg` и ответила бы 404, хотя
    миниатюра видна.

    Сервер настоящий (`_real_server`): `TestClient` сам декодирует путь
    дважды. Картинки разной высоты — по размеру на принтере видно, какой
    файл напечатан. Счётчик ведётся под точным именем (S6); у двойника
    `aA.jpg`, который лежит в той же папке, — нули, на принтер он не попал.
    Миниатюра `a%41.jpg` — тоже байты именно этого файла (W2, W3).
    """
    make_jpeg(folder / "a%41.jpg", 576, 300)
    make_jpeg(folder / "aA.jpg", 576, 200)
    make_jpeg(folder / "My%20Photo.jpg", 576, 100)
    fake = FakePrinter()

    with _real_server(create_app(folder, fake, static)) as port:
        host = "127.0.0.1:{}".format(port)
        for name, height in (("a%41.jpg", 300), ("My%20Photo.jpg", 100)):
            status, kind, body = _raw_request(port, "POST", _print_url(name), host)
            assert status == 200, (name, body)
            assert kind == "application/json", name
            assert json.loads(body.decode("utf-8")) == {"name": name, "printed": 1, "failed": 0}
            assert fake.images[-1].size == (576, height), name
            assert Stats(folder).get(name) == Counts(1, 0), name
        status, _, body = _raw_request(port, "GET", _image_url("a%41.jpg"), host)
        assert status == 200
        assert body == (folder / "a%41.jpg").read_bytes()

    assert len(fake.images) == 2
    assert Stats(folder).get("aA.jpg") == Counts(0, 0)


def test_print_success(folder: Path, static: Path) -> None:
    """W4 п. 6, W5: удачная печать — 200 с новыми счётчиками и +1 в файле.

    Принтер получил ровно одну картинку, уже загруженную `load_printable`,
    размером 576×300. `printed` записан в файл счётчиков папки. После
    удачной печати замок отпущен: вторая печать тоже 200, а не 409, и счёт
    идёт дальше.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.post(_print_url("good.jpg"))

    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 1, "failed": 0}
    assert len(fake.images) == 1
    assert fake.images[0].size == (576, 300)
    saved = json.loads(_stats_file(folder).read_text(encoding="utf-8"))
    assert saved["good.jpg"]["printed"] == 1

    # W5: замок после 200 отпущен.
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 2, "failed": 0}
    assert len(fake.images) == 2


def test_print_invalid_is_422(folder: Path, static: Path) -> None:
    """W4 п. 2, W5: непригодная картинка — 422 с текстом F3, принтер не вызван.

    Счётчики не меняются: файла счётчиков так и нет. Отказ 422 замок не
    держит: следующая печать пригодной картинки проходит, а не получает 409.
    """
    make_jpeg(folder / "wide.jpg", 800, 300)
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.post(_print_url("wide.jpg"))

    assert response.status_code == 422
    assert response.json() == {"detail": WIDE_ERROR}
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # W5: замок после 422 свободен.
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 1, "failed": 0}


def test_print_truncated_is_422(folder: Path, static: Path) -> None:
    """W4 п. 2, F6, Review Focus 3: файл ещё копируется — 422 «файл не читается».

    Обрезанный до половины JPEG с целым заголовком в списке пригоден
    (F4), и только полное декодирование при печати находит нехватку
    данных. Принтер не вызван, счётчики не меняются.
    """
    path = make_jpeg(folder / "copying.jpg", 576, 2000, noise=True)
    truncate(path, len(path.read_bytes()) // 2)
    fake = FakePrinter()
    client = _client(folder, fake, static)
    # Предпосылка теста: в списке файл пригоден — 422 даёт декодирование,
    # а не проверка заголовка.
    [image] = client.get("/api/images").json()["images"]
    assert image["ok"] is True

    response = client.post(_print_url("copying.jpg"))

    assert response.status_code == 422
    assert response.json() == {"detail": "неверный формат изображения: файл не читается"}
    assert fake.images == []
    assert not _stats_file(folder).exists()


def test_print_unknown_is_404(folder: Path, static: Path) -> None:
    """W4 п. 1, W2, W5: печать имени, которого нет в папке, — 404, принтер не вызван.

    Отказ 404 замок не держит: следующая печать картинки из папки
    проходит, а не получает 409.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.post(_print_url("nope.jpg"))

    assert response.status_code == 404
    assert response.json() == {"detail": "файла «nope.jpg» нет в папке"}
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # W5: замок после 404 свободен.
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 1, "failed": 0}


def test_print_is_post_only(folder: Path, static: Path) -> None:
    """§ 4.4, § 6.3: печать — только `POST`; `GET /api/print/{name}` — 405, принтер не вызван.

    `GET` шлёт не кнопка «Печать», а адрес, набранный или подсказанный
    браузером, или `<img src>` чужой страницы. Такой запрос не печатает и
    не трогает счётчики: иначе заказчик получал бы лишние чеки от одного
    открытия адреса, а проверка `Origin` из W9 смотрит только `POST`.
    Сервер называет единственный метод — `Allow: POST`, а отказ, как и
    любой ответ не 200, — JSON со строкой `detail` (W6).
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.get(_print_url("good.jpg"))

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Method Not Allowed"}
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # Предпосылка: та же картинка печатается через `POST` — 405 дал метод,
    # а не имя или содержимое файла.
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 200
    assert len(fake.images) == 1


def test_print_printer_error_counts_failed(folder: Path, static: Path) -> None:
    """W4 п. 4: сбой принтера — 503 с его текстом и +1 к `failed` в файле.

    Второй сбой подряд прибавляет ещё 1: счёт ведётся, а не ставится.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter(error=PRINTER_NOT_FOUND)
    client = _client(folder, fake, static)

    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 503
    assert response.json() == {"detail": PRINTER_NOT_FOUND}
    assert len(fake.images) == 1
    assert Stats(folder).get("good.jpg") == Counts(0, 1)

    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 503
    assert response.json() == {"detail": PRINTER_NOT_FOUND}
    assert Stats(folder).get("good.jpg") == Counts(0, 2)


def test_print_busy_is_409(folder: Path, static: Path) -> None:
    """W4 п. 3, W5, § 4.4, Review Focus 4: вторая печать во время первой — 409.

    Первая печать держит замок, пока тест не отпустит принтер. Вторая
    (другой картинки) не ждёт замка, а сразу получает 409 и ничего не
    печатает и не считает. Потом первая заканчивается успешно: напечатана
    ровно одна картинка.

    Клиент открыт через `with`, поэтому все запросы идут в одном цикле
    событий, как на uvicorn. Ручка печати `async def` держала бы этот цикл
    всю печать: вторая печать ждала бы первую и печатала картинку ещё раз
    вместо 409. Пока печать идёт, список тоже отвечает 200: ручки — `def`
    в пуле потоков (§ 4.4).
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    make_jpeg(folder / "other.jpg", 576, 300)
    fake = FakePrinter(hold=True)

    with client_for(create_app(folder, fake, static)) as client:
        with _held_print(client, fake, "good.jpg") as first:
            assert client.get("/api/images").status_code == 200
            second = client.post(_print_url("other.jpg"))
            assert second.status_code == 409
            assert second.json() == {"detail": "принтер занят: дождитесь конца печати"}

    assert first[0].status_code == 200
    assert first[0].json() == {"name": "good.jpg", "printed": 1, "failed": 0}
    assert len(fake.images) == 1
    assert Stats(folder).get("other.jpg") == Counts(0, 0)


def test_print_busy_answers_without_waiting(folder: Path, static: Path) -> None:
    """W5, W4 п. 3, Review Focus 4: двойной клик — вторая печать не ждёт замка даже недолго.

    Первая печать `good.jpg` держит замок; вторая печать той же картинки
    уходит из своего потока, и у неё `NO_WAIT_LIMIT` (1 с), пока первая
    ещё идёт. Потом первая печать заканчивается. Замок без ожидания
    (`acquire(blocking=False)`) отвечает 409 за миллисекунды. Настоящая
    печать короткой картинки занимает меньше 2 с (P7), поэтому замок с
    ожиданием — `acquire(timeout=2)` или просто `acquire()` — дождался бы
    конца первой печати, взял бы замок и напечатал картинку второй раз: у
    заказчика два одинаковых чека и «напечатано: 2» на карточке. Тест
    `test_print_busy_is_409` этого не видит: там первая печать держит замок,
    пока не пришёл ответ второй.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter(hold=True)
    second: List[httpx.Response] = []
    errors: List[BaseException] = []

    with client_for(create_app(folder, fake, static)) as client:

        def post_second() -> None:
            """Отправить вторую печать из потока и сохранить ответ или ошибку."""
            try:
                second.append(client.post(_print_url("good.jpg")))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=post_second)
        with _held_print(client, fake, "good.jpg") as first:
            thread.start()
            thread.join(NO_WAIT_LIMIT)
            # Ответила ли вторая печать, пока первая ещё держит замок.
            answered_while_held = not thread.is_alive()
        # Первая печать закончилась: запрос, который ждал замка, взял бы его сейчас.
        thread.join(10)

    assert not thread.is_alive()
    assert errors == []
    assert first[0].status_code == 200
    assert first[0].json() == {"name": "good.jpg", "printed": 1, "failed": 0}
    assert second[0].status_code == 409
    assert second[0].json() == {"detail": "принтер занят: дождитесь конца печати"}
    assert len(fake.images) == 1
    assert Stats(folder).get("good.jpg") == Counts(1, 0)
    assert answered_while_held


def test_print_race_prints_once_at_every_line(folder: Path, static: Path) -> None:
    """W5, Review Focus 4: две печати сразу — при любом чередовании одна печать и один 409.

    Поток пула FastAPI может уступить процессор другому запросу перед
    любой строкой ручки печати. Тест перебирает все строки ручки: первая
    печать `good.jpg` останавливается перед строкой (`sys.settrace`, § 7.3),
    вторая идёт, пока первая стоит, потом первая продолжает (подробно — в
    `_race_paused_at`). В каждой точке итог один: на принтере одна
    картинка, ответы 200 и 409. Проверка «замок свободен?» отдельно от
    взятия замка (`locked()`, потом `acquire()`) пропускает оба запроса,
    если поток уступил между этими строками: второй печатает, первый ждёт
    замок и печатает ту же картинку ещё раз — у заказчика два чека.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    code = _print_endpoint(create_app(folder, FakePrinter(), static)).__code__
    lines = sorted({line for _, line in dis.findlinestarts(code)})
    first_codes: List[int] = []

    for line in lines:
        where = "{}: {}".format(line, linecache.getline(code.co_filename, line).strip())
        outcome = _race_paused_at(folder, static, line)
        if outcome is None:
            continue
        codes, printed = outcome
        assert sorted(codes.values()) == [200, 409], where
        assert printed == 1, where
        first_codes.append(codes["first"])

    # Предпосылка: перебраны точки и до замка (печатает второй, первому
    # 409), и после него (печатает первый, второму 409).
    assert sorted(set(first_codes)) == [200, 409]


def test_print_race_counters_are_under_lock(folder: Path, static: Path) -> None:
    """W5, W4 п. 4–5, Review Focus 4: пока первая печать пишет счётчик, вторая получает 409.

    W5: оба `increment` выполняются под замком. Первая печать уже прошла
    принтер и пишет счётчик — «напечатано» после удачи, «ошибок печати»
    после сбоя принтера, — а вторая печать той же картинки приходит ровно
    в это время: первый поток стоит перед строкой `increment`
    (`_race_paused_at`, `sys.settrace`, § 7.3). Итог: у первой её код
    (200 или 503), у второй 409, на принтере одна картинка. Замок,
    отпущенный до записи счётчика («не держать принтер на время записи на
    диск»), пустил бы вторую печать, пока первая пишет файл (`mkstemp`,
    запись, `fsync`, `replace`): у заказчика два чека и «напечатано: 2»
    вместо одного чека и 409.

    `test_print_race_prints_once_at_every_line` этих точек не проходит: его
    принтер держит первую печать (`hold`), и первый поток встаёт в
    принтере раньше, чем доходит до счётчиков. Здесь принтер без `hold`.
    Строки ищутся по тексту: правка, которая выносит запись за `finally`,
    сдвигает их номера.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    code = _print_endpoint(create_app(folder, FakePrinter(), static)).__code__
    lines = sorted({line for _, line in dis.findlinestarts(code)})
    increments = [
        line for line in lines if "stats.increment(" in linecache.getline(code.co_filename, line)
    ]
    # Предпосылка: найдены обе записи счётчиков — «напечатано» и «ошибок печати».
    kinds = sorted('"failed"' in linecache.getline(code.co_filename, line) for line in increments)
    assert kinds == [False, True], increments

    for line in increments:
        where = "{}: {}".format(line, linecache.getline(code.co_filename, line).strip())
        failed = '"failed"' in where
        printer = FakePrinter(error=PRINTER_FAILED if failed else None)
        outcome = _race_paused_at(folder, static, line, printer)
        # Предпосылка: первый поток встал на записи счётчика.
        assert outcome is not None, where
        codes, printed = outcome
        assert codes == {"first": 503 if failed else 200, "second": 409}, where
        assert printed == 1, where


def test_print_refusals_come_before_busy(folder: Path, static: Path) -> None:
    """W4 п. 1–3 строго по порядку: во время чужой печати 404 и 422 — не 409.

    Пока первая печать держит замок, печать имени, которого нет в папке,
    всё равно получает 404, а непригодной картинки — 422 с причиной F3.
    Оператор видит настоящую причину, а не «принтер занят», а картинка
    проверяется до замка и не держит его, пока декодируется. Ни один
    отказ не дошёл до принтера и не попал в файл счётчиков.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    make_jpeg(folder / "wide.jpg", 800, 300)
    fake = FakePrinter(hold=True)

    with client_for(create_app(folder, fake, static)) as client:
        with _held_print(client, fake, "good.jpg") as first:
            unknown = client.post(_print_url("nope.jpg"))
            assert unknown.status_code == 404
            assert unknown.json() == {"detail": "файла «nope.jpg» нет в папке"}
            wide = client.post(_print_url("wide.jpg"))
            assert wide.status_code == 422
            assert wide.json() == {"detail": WIDE_ERROR}

    assert first[0].status_code == 200
    assert first[0].json() == {"name": "good.jpg", "printed": 1, "failed": 0}
    assert len(fake.images) == 1
    saved = json.loads(_stats_file(folder).read_text(encoding="utf-8"))
    assert sorted(saved) == ["good.jpg"]


def test_print_lock_released_after_error(folder: Path, static: Path) -> None:
    """W5: после сбоя принтера замок отпущен — следующая печать проходит.

    Если бы замок остался занятым, вторая печать получила бы 409.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter(error=PRINTER_FAILED)
    client = _client(folder, fake, static)

    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 503

    fake.error = None
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 1, "failed": 1}


def test_print_counter_write_failure_is_500(
    folder: Path, static: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """W4 п. 5, W5: напечатано, а счётчик не записался — 500 с причиной S5.

    Папка только для чтения (`chmod 555`): список и чтение картинки
    работают, а временный файл счётчиков создать нельзя. Картинка при
    этом ушла на принтер ровно один раз. После 500 замок отпущен:
    следующая печать снова доходит до принтера и снова даёт 500, а не 409.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    chmod_to(folder, 0o555)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.post(_print_url("good.jpg"))

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Напечатано, но счётчик не сохранился: Permission denied"
    }
    assert len(fake.images) == 1
    assert not _stats_file(folder).exists()

    # W5: замок после 500 отпущен.
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Напечатано, но счётчик не сохранился: Permission denied"
    }
    assert len(fake.images) == 2


def test_print_error_and_counter_failure(
    folder: Path, static: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """W4 п. 4, W5: сбой принтера и сбой записи `failed` — 503 с обоими текстами.

    Папка только для чтения (`chmod 555`), поэтому `increment` после сбоя
    печати бросает `StatsWriteError`, и к тексту принтера добавляется
    причина, по которой счётчик ошибок не сохранился. После такого 503
    замок отпущен: следующая печать снова доходит до принтера и снова даёт
    этот 503, а не 409.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    chmod_to(folder, 0o555)
    fake = FakePrinter(error=PRINTER_FAILED)
    client = _client(folder, fake, static)

    response = client.post(_print_url("good.jpg"))

    assert response.status_code == 503
    assert response.json() == {
        "detail": PRINTER_FAILED + " Счётчик ошибок не сохранился: Permission denied"
    }
    assert not _stats_file(folder).exists()

    # W5: замок после 503 с несохранённым счётчиком отпущен.
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 503
    assert response.json() == {
        "detail": PRINTER_FAILED + " Счётчик ошибок не сохранился: Permission denied"
    }
    assert len(fake.images) == 2


def test_printer_state(folder: Path, static: Path) -> None:
    """§ 4.4: `/api/printer` отвечает, подключён ли принтер, — при каждом запросе.

    Один и тот же принтер сначала отключён, потом подключён: ответ меняется
    вместе с ним, то есть API спрашивает принтер, а не помнит ответ.
    """
    fake = FakePrinter(connected=False)
    client = _client(folder, fake, static)

    response = client.get("/api/printer")
    assert response.status_code == 200
    assert response.json() == {"connected": False}

    fake.is_connected = True
    response = client.get("/api/printer")
    assert response.status_code == 200
    assert response.json() == {"connected": True}


def test_no_access_everywhere(
    folder: Path, static: Path, chmod_to: Callable[[Path, int], None]
) -> None:
    """W7, W6, F9: нет доступа к папке — у всех трёх ручек папки 500 с текстом, как дать доступ.

    Так бывает, когда macOS не дала Терминалу доступ к папке на Рабочем
    столе: `os.listdir` бросает `PermissionError`, и `scan` его не
    перехватывает (F9). Здесь это настоящий `chmod 000` на папку. Список,
    миниатюра и печать ищут файл через `scan`, и каждая отвечает одним и
    тем же 500 — JSON с текстом W7, где названа приведённая папка
    (приложение собрано по символической ссылке) и путь в Системных
    настройках. Принтер не вызван.

    Клиент — с `raise_server_exceptions=True`, как по умолчанию: свой
    обработчик `PermissionError` отвечает сам и дальше ничего не бросает.
    Если бы этот текст давал общий обработчик `Exception`, Starlette после
    ответа пробросил бы исключение (W8), и тест упал бы на нём.

    Оператор дал доступ — следующий опрос списка снова 200, без
    перезапуска клиента; файла счётчиков не появилось.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(_via_symlink(folder), fake, static)
    # Исходные права папки: их тест вернёт сам, когда «оператор дал доступ».
    mode = stat.S_IMODE(os.stat(folder).st_mode)
    expected = {"detail": NO_ACCESS.format(folder=str(folder.resolve()))}
    chmod_to(folder, 0o000)

    for method, url in (
        ("GET", "/api/images"),
        ("GET", _image_url("good.jpg")),
        ("POST", _print_url("good.jpg")),
    ):
        response = client.request(method, url)
        assert response.status_code == 500, url
        assert response.headers["content-type"] == "application/json", url
        assert response.json() == expected, url
    assert fake.images == []

    # Доступ вернули — тот же клиент снова видит папку.
    chmod_to(folder, mode)
    assert _listed_names(client) == ["good.jpg"]
    assert not _stats_file(folder).exists()
    # Текст W7 — константа модуля под своим именем.
    assert web.NO_ACCESS == NO_ACCESS


def test_foreign_host_is_refused(folder: Path, static: Path) -> None:
    """W9, W6, § 6.3: чужое имя хоста в `Host` — 403 с текстом W9 у каждой ручки.

    Подмена DNS (DNS rebinding): чужая страница открыта в браузере
    оператора по своему имени, а потом это имя начинает указывать на
    `127.0.0.1`. Браузер считает запросы к клиенту запросами той страницы
    к её же серверу и отдаёт ей ответы: список картинок, сами картинки,
    печать. Отличить такой запрос можно только по `Host` — в нём чужое
    имя. Поэтому каждая ручка — страница `/`, `/api/health`, список,
    миниатюра, принтер и печать — отвечает 403 JSON, ничего не печатает и
    не пишет счётчики. Имена `127.0.0.1.evil.example` и
    `localhost.evil.example` только начинаются как свои: сравнивается имя
    целиком.

    Контраст: то же приложение с `Host` `127.0.0.1:8766` и `localhost:8766`
    отвечает 200 — имя сравнивается без порта (порт выбирается при
    запуске, M3), и `localhost` — тоже этот компьютер.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    app = create_app(folder, fake, static)

    for base_url in (
        "http://evil.example",
        "http://127.0.0.1.evil.example:8766",
        "http://localhost.evil.example",
    ):
        foreign = TestClient(app, base_url=base_url)
        for method, url in (
            ("GET", "/"),
            ("GET", "/api/health"),
            ("GET", "/api/images"),
            ("GET", _image_url("good.jpg")),
            ("GET", "/api/printer"),
            ("POST", _print_url("good.jpg")),
        ):
            response = foreign.request(method, url)
            assert response.status_code == 403, (base_url, url)
            assert response.headers["content-type"] == "application/json", (base_url, url)
            assert response.json() == {"detail": FOREIGN_REQUEST}, (base_url, url)
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # Контраст: свои имена с портом — 200, отказ выше дало имя хоста.
    for base_url in ("http://127.0.0.1:8766", "http://localhost:8766"):
        response = TestClient(app, base_url=base_url).get("/api/images")
        assert response.status_code == 200, base_url
        assert [image["name"] for image in response.json()["images"]] == ["good.jpg"]
    # Текст W9 — константа модуля под своим именем.
    assert web.FOREIGN_REQUEST == FOREIGN_REQUEST


def test_foreign_host_is_refused_on_real_server(folder: Path, static: Path) -> None:
    """W9 п. 1, W6, § 6.3: чужой `Host` — 403, хотя соединение пришло с `127.0.0.1`.

    Безопасность. У заказчика другого адреса соединения не бывает: сервер
    слушает только `127.0.0.1` (§ 6.3), и запрос страницы с подменённым DNS
    браузер оператора тоже шлёт с `127.0.0.1`. Отличить его можно только
    по имени в `Host`. Проверка, которая доверяет «своему» адресу
    соединения и отказывает, только когда чужие и `Host`, и адрес, у
    заказчика не отказала бы никому: чужая страница прочитала бы список и
    картинки и печатала бы. `test_foreign_host_is_refused` этого не видит:
    адрес соединения у `TestClient` — `testclient`, чужой и сам по себе.

    Поэтому здесь сервер настоящий (`_real_server`), и запрос идёт по TCP с
    `127.0.0.1`, как у заказчика. Каждая ручка — страница, `/api/health`,
    список, миниатюра, принтер и печать — отвечает 403 JSON с текстом W9;
    принтер не вызван, файла счётчиков нет.

    Контраст: тот же сервер со своим `Host` (`127.0.0.1` и `localhost` с
    портом) — 200: отказ выше дало имя хоста, а не соединение.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()

    with _real_server(create_app(folder, fake, static)) as port:
        for host in ("evil.example", "127.0.0.1.evil.example:{}".format(port)):
            for method, url in (
                ("GET", "/"),
                ("GET", "/api/health"),
                ("GET", "/api/images"),
                ("GET", _image_url("good.jpg")),
                ("GET", "/api/printer"),
                ("POST", _print_url("good.jpg")),
            ):
                status, kind, body = _raw_request(port, method, url, host)
                assert status == 403, (host, url, body)
                assert kind == "application/json", (host, url)
                assert json.loads(body.decode("utf-8")) == {"detail": FOREIGN_REQUEST}, (host, url)
        assert fake.images == []
        assert not _stats_file(folder).exists()

        # Контраст: свои имена с портом — 200.
        for host in ("127.0.0.1:{}".format(port), "localhost:{}".format(port)):
            status, _, body = _raw_request(port, "GET", "/api/images", host)
            assert status == 200, host
            images = json.loads(body.decode("utf-8"))["images"]
            assert [image["name"] for image in images] == ["good.jpg"], host


def test_request_without_host_is_refused_on_real_server(folder: Path, static: Path) -> None:
    """W9, W6 (финальная проверка, 2026-09-28): запрос без `Host` (HTTP/1.0) — 403, печати нет.

    Без заголовка `Host` Starlette собирает адрес запроса из адреса
    сервера — `127.0.0.1:<порт>`, и проверка имени хоста видела бы своё
    имя: такой запрос читал бы список и картинки и печатал бы. Браузер
    `Host` шлёт всегда, но своё имя в запросе без `Host` — не довод, что
    он с этого компьютера, а W9 пропускает только запрос, назвавший своё
    имя. Поэтому каждая ручка — страница, `/api/health`, список,
    миниатюра, принтер и печать — отвечает 403 JSON с текстом W9;
    принтер не вызван, файла счётчиков нет.

    Сервер настоящий (`_real_server`), запрос — байты HTTP/1.0 по TCP:
    `TestClient` и `http.client` без `Host` не шлют. Контраст: тот же
    запрос HTTP/1.0 со своим `Host` — 200 и у списка, и у печати: отказ
    выше дало отсутствие `Host`, а не версия протокола.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()

    with _real_server(create_app(folder, fake, static)) as port:
        for method, url in (
            ("GET", "/"),
            ("GET", "/api/health"),
            ("GET", "/api/images"),
            ("GET", _image_url("good.jpg")),
            ("GET", "/api/printer"),
            ("POST", _print_url("good.jpg")),
        ):
            status, kind, body = _http10_request(port, method, url, None)
            assert status == 403, (url, body)
            assert kind == "application/json", url
            assert json.loads(body.decode("utf-8")) == {"detail": FOREIGN_REQUEST}, url
        assert fake.images == []
        assert not _stats_file(folder).exists()

        # Контраст: HTTP/1.0 со своим `Host` — 200.
        host = "127.0.0.1:{}".format(port)
        status, _, body = _http10_request(port, "GET", "/api/images", host)
        assert status == 200, body
        images = json.loads(body.decode("utf-8"))["images"]
        assert [image["name"] for image in images] == ["good.jpg"]
        status, _, body = _http10_request(port, "POST", _print_url("good.jpg"), host)
        assert status == 200, body
        assert json.loads(body.decode("utf-8")) == {"name": "good.jpg", "printed": 1, "failed": 0}
        assert len(fake.images) == 1


def test_cross_site_post_is_refused(folder: Path, static: Path) -> None:
    """W9, W6: печать с чужим `Origin` — 403, принтер не вызван, счётчики не тронуты.

    Любая страница, открытая в браузере оператора, может вслепую отправить
    `POST` на `127.0.0.1` (форма или `fetch` без чтения ответа), и браузер
    приложит к нему `Origin` этой страницы. Отказ получают чужое имя
    хоста (`http://evil.example`), чужая схема при своём имени
    (`https://127.0.0.1`: клиент работает только по `http`), `null`
    (страница, открытая из файла, или песочница) и имя, которое только
    начинается как своё (`127.0.0.1.evil.example`). Ни одна печать не
    дошла до принтера, файла счётчиков нет.

    Контраст: `GET` списка с тем же чужим `Origin` — 200 (W9 смотрит
    `Origin` только у `POST`, а прочитать ответ чужой странице не даст сам
    браузер), и печать без `Origin` — 200: так шлют `curl` и тесты, и
    отказы выше дал именно `Origin`.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    for origin in (
        "http://evil.example",
        "https://127.0.0.1",
        "null",
        "http://127.0.0.1.evil.example:8766",
    ):
        response = client.post(_print_url("good.jpg"), headers={"Origin": origin})
        assert response.status_code == 403, origin
        assert response.headers["content-type"] == "application/json", origin
        assert response.json() == {"detail": FOREIGN_REQUEST}, origin
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # Контраст: `GET` с чужим `Origin` и печать без `Origin` — 200.
    response = client.get("/api/images", headers={"Origin": "http://evil.example"})
    assert response.status_code == 200
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 1, "failed": 0}
    assert len(fake.images) == 1
    # Текст W9 — константа модуля под своим именем.
    assert web.FOREIGN_REQUEST == FOREIGN_REQUEST


def test_same_origin_post_is_allowed(folder: Path, static: Path) -> None:
    """W9: печать со своим `Origin` проходит — на любом порту и с именем `localhost`.

    Страница клиента открыта на `http://127.0.0.1:<порт>` (M5), и браузер
    прикладывает к кнопке «Печать» этот `Origin`. Порт в `Origin` не
    проверяется — он выбирается при запуске (M3): здесь `Origin` с портом
    8767 проходит, хотя в `Host` порта нет. `localhost` — тоже этот
    компьютер. Обе печати дошли до принтера, счёт идёт.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.post(_print_url("good.jpg"), headers={"Origin": "http://127.0.0.1:8767"})
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 1, "failed": 0}

    response = client.post(_print_url("good.jpg"), headers={"Origin": "http://localhost:8766"})
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 2, "failed": 0}
    assert len(fake.images) == 2


def test_local_names_are_exactly_two(folder: Path, static: Path) -> None:
    """W9, W6: свои имена — ровно `127.0.0.1` и `localhost`, целиком; любое другое — 403.

    Решение контролёра по задаче 2.4 (W9 п. 4). В `Host` отказ получают:
    `0.0.0.0` и `::1` — тоже адреса этого компьютера, но сервер слушает
    только `127.0.0.1` (§ 6.3), и набор из двух имён их не знает; пустой
    `Host` — в нём нет имени вовсе; `local` и `127.0.0` — части своих имён:
    сравнение подстрокой (`_LOCAL_HOSTS` строкой, а не кортежем) их
    пропустило бы; `testserver` — имя, которое `TestClient` шлёт по
    умолчанию: третье «своё» имя, добавленное, чтобы прошли старые тесты,
    — тоже нарушение W9 п. 4: имя из одного слова через поисковый домен
    или DNS роутера заказчика может вести к чужой странице, а подмена DNS
    потом направит его на `127.0.0.1`. Каждый такой запрос — и список, и
    печать — 403 JSON с текстом W9.

    Печать с `Origin` получает отказ, когда `Origin` пустой (проверка
    «заголовок есть, но пустой — как нет» его пропустила бы), когда в нём
    схема `http` без имени (`http://`), подстрока `local` и `[::1]`.

    IPv6-имя передаётся в заголовке `Host`, а не в адресе клиента:
    `TestClient` Starlette на `base_url` `http://[::1]:8766` падает сам.
    Клиент — с `raise_server_exceptions=True`, как по умолчанию: ни одна
    проверка не бросила исключение (иначе был бы 500 W8 и трассировка).
    Ни одна печать не дошла до принтера, файла счётчиков нет.

    Контраст: тот же клиент с `Host: 127.0.0.1:8766` и печать с `Origin`
    `http://127.0.0.1:8766` — 200: отказы выше дало имя.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    for host in ("0.0.0.0:8766", "[::1]:8766", "", "local:8766", "127.0.0:8766", "testserver"):
        for method, url in (("GET", "/api/images"), ("POST", _print_url("good.jpg"))):
            response = client.request(method, url, headers={"Host": host})
            # Предпосылка: заголовок ушёл ровно таким, а не адресом клиента.
            assert response.request.headers["host"] == host, (host, url)
            assert response.status_code == 403, (host, url)
            assert response.headers["content-type"] == "application/json", (host, url)
            assert response.json() == {"detail": FOREIGN_REQUEST}, (host, url)

    for origin in ("", "http://", "http://local", "http://[::1]:8766"):
        response = client.post(_print_url("good.jpg"), headers={"Origin": origin})
        # Предпосылка: `Origin` ушёл, в том числе пустой.
        assert response.request.headers["origin"] == origin, origin
        assert response.status_code == 403, origin
        assert response.headers["content-type"] == "application/json", origin
        assert response.json() == {"detail": FOREIGN_REQUEST}, origin
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # Контраст: своё имя в `Host` и в `Origin` — 200.
    response = client.get("/api/images", headers={"Host": "127.0.0.1:8766"})
    assert response.status_code == 200
    assert [image["name"] for image in response.json()["images"]] == ["good.jpg"]
    response = client.post(_print_url("good.jpg"), headers={"Origin": "http://127.0.0.1:8766"})
    assert response.status_code == 200
    assert response.json() == {"name": "good.jpg", "printed": 1, "failed": 0}


def test_unparsable_host_or_origin_is_403(folder: Path, static: Path) -> None:
    """W9, W6: заголовок, который не разбирается как адрес, — 403 W9, а не 500 W8.

    Решение контролёра по задаче 2.4 (W9 п. 4). `Host: [` и `Origin:
    http://[` — открытая скобка IPv6 без закрывающей: разбор имени в
    Python бросает `ValueError` («Invalid IPv6 URL»). Без перехвата на
    такой запрос отвечал бы общий обработчик `Exception` — 500 «Ошибка
    клиента: ValueError…» и трассировка в окне Терминала, будто сломан сам
    клиент. Имени этого компьютера в таком заголовке нет, поэтому ответ —
    тот же отказ W9, что и чужому имени.

    Клиент — с `raise_server_exceptions=True`, как по умолчанию: проверка
    отвечает сама и ничего не бросает. Список не прочитан, печать не дошла
    до принтера, файла счётчиков нет.

    Контраст: печать без `Origin` у того же клиента — 200: отказ дал
    заголовок.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = _client(folder, fake, static)

    response = client.get("/api/images", headers={"Host": "["})
    # Предпосылка: `Host` ушёл ровно таким.
    assert response.request.headers["host"] == "["
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": FOREIGN_REQUEST}

    response = client.post(_print_url("good.jpg"), headers={"Origin": "http://["})
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": FOREIGN_REQUEST}
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # Контраст: без `Origin` та же печать проходит.
    response = client.post(_print_url("good.jpg"))
    assert response.status_code == 200
    assert len(fake.images) == 1


def test_unexpected_error_is_json(folder: Path, static: Path) -> None:
    """W8, W6: папку переименовали при работающем клиенте — 500 JSON с классом и текстом ошибки.

    Пример из W8: приложение уже собрано, а папку переименовали.
    `os.listdir` в `scan` бросает `FileNotFoundError` — это не
    `PermissionError` (W7), и отвечает общий обработчик `Exception`: JSON,
    где `detail` — «Ошибка клиента: FileNotFoundError: [Errno 2] …» с
    текстом исключения как есть и советом перезапустить клиент. Без
    обработчика Starlette ответил бы голым текстом «Internal Server Error»,
    и оператор увидел бы только «Ошибка клиента: HTTP 500» без причины
    (U4). Печать ищет файл так же и отвечает тем же 500, не дойдя до
    принтера.

    Starlette после ответа обработчика `Exception` пробрасывает исключение
    дальше (в Терминале остаётся трассировка), поэтому клиент — с
    `raise_server_exceptions=False` (W8).
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    app = create_app(folder, fake, static)
    resolved = folder.resolve()
    folder.rename(folder.parent / "Печать картинок (старая)")
    client = client_for(app, raise_server_exceptions=False)
    # W8: `{exc}` — текст исключения как есть; так его пишет `os.listdir`
    # для приведённой папки, которую помнит приложение.
    expected = CLIENT_ERROR.format(
        kind="FileNotFoundError",
        error="[Errno 2] No such file or directory: {!r}".format(str(resolved)),
    )

    for method, url in (("GET", "/api/images"), ("POST", _print_url("good.jpg"))):
        response = client.request(method, url)
        assert response.status_code == 500, url
        assert response.headers["content-type"] == "application/json", url
        detail = response.json()["detail"]
        assert detail.startswith("Ошибка клиента: FileNotFoundError: "), url
        assert detail.endswith(". Закройте окно «Печать картинок» и запустите его снова."), url
        assert response.json() == {"detail": expected}, url
    assert fake.images == []
    # Текст W8 — константа модуля под своим именем.
    assert web.CLIENT_ERROR == CLIENT_ERROR


class _CrashingPrinter(FakePrinter):
    """Поддельный принтер (§ 7.3), чьи опрос и печать бросают не `PrinterError` и не `OSError`.

    Так выглядит сбой, которого API не ждёт: ни W4 п. 4 (`PrinterError`),
    ни W7 (`PermissionError`) его не перехватывают, и ответить на него
    может только общий обработчик `Exception` (W8). Картинка попадает в
    `images` при каждой печати, как у `FakePrinter`: по списку видно, что
    запрос дошёл до принтера.
    """

    def connected(self) -> bool:
        """Бросить `RuntimeError` вместо ответа, подключён ли принтер."""
        raise RuntimeError("сбой опроса шины")

    def print_image(self, image: Any) -> None:
        """Запомнить картинку и бросить `RuntimeError` вместо `PrinterError`."""
        self.images.append(image)
        raise RuntimeError("сбой драйвера")


def test_unexpected_non_os_error_is_json(folder: Path, static: Path) -> None:
    """W8, W6, W5, W4: исключение не из семейства `OSError` — тоже 500 JSON с его классом и текстом.

    W8 — обработчик именно `Exception`, а не `OSError`: сбой с
    переименованной папкой (`FileNotFoundError`, тест выше) прошёл бы и
    обработчик `OSError` или `FileNotFoundError`, а `RuntimeError` такой
    обработчик отдал бы голым текстом «Internal Server Error», и оператор
    увидел бы только «Ошибка клиента: HTTP 500» без причины (U4).

    Здесь `RuntimeError` бросают опрос принтера (`/api/printer`) и печать.
    Печать — дважды подряд: оба раза тот же 500, а не 409, то есть замок
    после такого сбоя отпущен (W5). `RuntimeError` — не `PrinterError`,
    поэтому W4 п. 4 его не считает: счётчиков нет, файла счётчиков тоже.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    crash = _CrashingPrinter()
    # W8: Starlette после ответа обработчика `Exception` пробрасывает
    # исключение дальше — клиент его не поднимает.
    client = client_for(create_app(folder, crash, static), raise_server_exceptions=False)

    response = client.get("/api/printer")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "detail": CLIENT_ERROR.format(kind="RuntimeError", error="сбой опроса шины")
    }

    for attempt in range(2):
        response = client.post(_print_url("good.jpg"))
        assert response.status_code == 500, attempt
        assert response.headers["content-type"] == "application/json", attempt
        assert response.json() == {
            "detail": CLIENT_ERROR.format(kind="RuntimeError", error="сбой драйвера")
        }, attempt
    # Обе печати дошли до принтера: вторая не упёрлась в замок (W5).
    assert len(crash.images) == 2
    assert not _stats_file(folder).exists()


def test_missing_page_is_json(folder: Path, static: Path) -> None:
    """W8, W6: страница `index.html` пропала при работающем клиенте — 500 JSON, а не голый текст.

    Сбой настоящий: файл страницы удалён после сборки приложения.
    `FileResponse` Starlette на отсутствующий файл бросает `RuntimeError`
    («File at path … does not exist.»), а не `OSError`, — ответить на него
    может только обработчик `Exception` (W8). Обработчик `OSError` отдал
    бы голый текст «Internal Server Error». Точка перед советом
    перезапустить клиент вторая: так задан шаблон W8 — `{exc}.`, а текст
    Starlette уже кончается точкой.
    """
    app = create_app(folder, FakePrinter(), static)
    page = static / "index.html"
    page.unlink()
    client = client_for(app, raise_server_exceptions=False)

    response = client.get("/")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "detail": CLIENT_ERROR.format(
            kind="RuntimeError", error="File at path {} does not exist.".format(page)
        )
    }


class _DriverBug(Exception):
    """Тестовое исключение прямо от `Exception` — не `OSError` и не `RuntimeError` (W8).

    Так выглядит ошибка, класс которой заранее не знает никто: например,
    своё исключение библиотеки драйвера. Имя класса попадает в текст W8 как
    есть — `type(exc).__name__`.
    """


class _BuggyPrinter(FakePrinter):
    """Поддельный принтер (§ 7.3), чьи опрос и печать бросают `_DriverBug`.

    `_DriverBug` — не `PrinterError` (W4 п. 4), не `PermissionError` (W7),
    не `OSError` и не `RuntimeError`: ответить на него может только
    обработчик самого `Exception` (W8). Картинка попадает в `images` при
    каждой печати, как у `FakePrinter`: по списку видно, что запрос дошёл
    до принтера.
    """

    def connected(self) -> bool:
        """Бросить `_DriverBug` вместо ответа, подключён ли принтер."""
        raise _DriverBug("опрос принтера не удался")

    def print_image(self, image: Any) -> None:
        """Запомнить картинку и бросить `_DriverBug` вместо `PrinterError`."""
        self.images.append(image)
        raise _DriverBug("драйвер не принял картинку")


def test_unexpected_any_exception_is_json(folder: Path, static: Path) -> None:
    """W8, W6, W5: исключение любого класса, унаследованного прямо от `Exception`, — 500 JSON.

    Решение контролёра по задаче 2.4. Тесты выше бросают `OSError`
    (переименованная папка) и `RuntimeError` (сбой принтера, пропавшая
    страница) — их прошёл бы и обработчик, зарегистрированный только на
    эти два класса. Здесь класс исключения свой, `_DriverBug(Exception)`:
    такой обработчик отдал бы голый текст «Internal Server Error», и
    оператор увидел бы только «Ошибка клиента: HTTP 500» без причины (U4).
    Обработчик `Exception` (W8) отвечает JSON с именем класса и текстом.

    Опрос принтера — 500 с текстом W8; печать — дважды подряд, оба раза
    тот же 500, а не 409: замок после такого сбоя отпущен (W5). `_DriverBug`
    не `PrinterError`, поэтому W4 п. 4 его не считает: файла счётчиков нет.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    buggy = _BuggyPrinter()
    # W8: Starlette после ответа обработчика `Exception` пробрасывает
    # исключение дальше — клиент его не поднимает.
    client = client_for(create_app(folder, buggy, static), raise_server_exceptions=False)

    response = client.get("/api/printer")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "detail": CLIENT_ERROR.format(kind="_DriverBug", error="опрос принтера не удался")
    }

    for attempt in range(2):
        response = client.post(_print_url("good.jpg"))
        assert response.status_code == 500, attempt
        assert response.headers["content-type"] == "application/json", attempt
        assert response.json() == {
            "detail": CLIENT_ERROR.format(kind="_DriverBug", error="драйвер не принял картинку")
        }, attempt
    # Обе печати дошли до принтера: вторая не упёрлась в замок (W5).
    assert len(buggy.images) == 2
    assert not _stats_file(folder).exists()


def test_trailing_slash_is_json_404(folder: Path, static: Path) -> None:
    """W6: адрес с косой чертой на конце — 404 JSON со строкой `detail`, а не пустой 307.

    Решение контролёра по задаче 2.4. По умолчанию FastAPI на
    `/api/images/` отвечает перенаправлением 307 на `/api/images` — без
    тела и без `detail`, и правило W6 («у каждого ответа не 200 есть JSON
    со строкой `detail`») нарушено. Приложение собрано с
    `redirect_slashes=False`: такой адрес — обычный 404 FastAPI, JSON
    `{"detail": "Not Found"}`. Клиент не следует перенаправлениям
    (`follow_redirects=False`), иначе 307 было бы не видно.

    Печать с косой чертой на конце — тоже 404: 307 сохраняет метод, и
    перенаправленный `POST` напечатал бы картинку. Принтер не вызван,
    файла счётчиков нет.

    Контраст: те же адреса без косой черты — 200.
    """
    make_jpeg(folder / "good.jpg", 576, 300)
    fake = FakePrinter()
    client = client_for(create_app(folder, fake, static), follow_redirects=False)

    for method, url in (
        ("GET", "/api/images/"),
        ("GET", "/api/health/"),
        ("POST", _print_url("good.jpg") + "/"),
    ):
        response = client.request(method, url)
        assert response.status_code == 404, url
        assert response.headers["content-type"] == "application/json", url
        assert response.json() == {"detail": "Not Found"}, url
    assert fake.images == []
    assert not _stats_file(folder).exists()

    # Контраст: без косой черты на конце — 200.
    for method, url in (
        ("GET", "/api/images"),
        ("GET", "/api/health"),
        ("POST", _print_url("good.jpg")),
    ):
        assert client.request(method, url).status_code == 200, url
    assert len(fake.images) == 1
