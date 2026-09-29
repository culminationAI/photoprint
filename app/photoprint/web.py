"""API клиента: страница, список картинок, миниатюры, печать и состояние принтера.

Браузер не видит ни папку, ни USB-принтер — всё это страница получает от
сервера по HTTP (спецификация § 4.4). `create_app` собирает приложение
FastAPI для одной папки и одного принтера. Принтер приходит снаружи как
протокол `Printer`, а не как `UsbPrinter` (§ 3.1): в тестах на его место
встаёт поддельный.

Ручки (таблица § 4.4):
- `GET /` — страница `index.html`;
- `GET /api/health` — «это photoprint, и вот его папка»;
- `GET /api/images` — список картинок папки;
- `GET /api/images/{name}` — байты картинки для миниатюры;
- `POST /api/print/{name}` — печать картинки;
- `GET /api/printer` — подключён ли принтер.

Правила спецификации, которые держит модуль:
- W1 — у каждой картинки в списке пригодность, причина и оба счётчика,
  порядок — как у `scan` (F2); счётчики — из одного `Stats.snapshot()` на
  запрос, а не из чтения файла на каждую картинку;
- W2 — имя из адреса ищется только через `find` (F7); не нашлось — 404 с
  текстом;
- W3 — байты файла читаются в самой ручке, тип — по расширению пути;
  непригодная картинка тоже отдаётся;
- W4 — ответ печати — первый сработавший из 404, 422, 409, 503, 500, 200;
  счётчики меняются только после попытки печати;
- W5 — один замок печати на приложение: берётся без ожидания, отпускается
  в `finally`, оба `increment` выполняются под ним;
- W6 — у каждого ответа не 200 — JSON со строкой `detail`: у отказов
  W2–W4 и W9, у 500 W7 и W8, у 404 и 405 самого FastAPI, в том числе у
  адреса с косой чертой на конце (`redirect_slashes=False`: 404, а не
  пустой 307). Исключение одно — `GET /` с заголовком `Range`: страницу
  отдаёт `FileResponse`, и его 206 или 416 — не JSON; страница и браузер
  такой запрос к `/` не шлют (решение контролёра по задаче 2.4);
- W7 — `PermissionError` в любой ручке (macOS не дала Терминалу доступ к
  папке, F9) — 500 с текстом, как дать доступ;
- W8 — любое другое неперехваченное исключение — 500 с его классом и
  текстом и советом перезапустить клиент;
- W9 — только этот компьютер: запрос с чужим именем хоста в `Host` и
  печать с чужим `Origin` отклоняются с 403 ещё до ручек. Своих имён ровно
  два, `127.0.0.1` и `localhost`, и сравниваются они целиком; пустой и
  неразбираемый заголовок и запрос вовсе без `Host` — тоже 403, а не 500 W8
  и не пропуск. Страница `GET /` отдаётся с `Cache-Control: no-cache`,
  `X-Frame-Options: DENY` и `Content-Security-Policy: frame-ancestors
  'none'`: после обновления браузер берёт новую страницу, а чужой сайт не
  встроит её в рамку.
"""
from __future__ import annotations

import mimetypes
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from photoprint.folder import InvalidImage, find, load_printable, scan
from photoprint.printer import Printer, PrinterError
from photoprint.stats import Counts, Stats, StatsWriteError

# Тексты для людей — дословно из § 4.4 (W2, W4). Страница показывает их
# оператору как есть (U4), поэтому каждый говорит, что случилось.
NOT_IN_FOLDER = "файла «{name}» нет в папке"
BUSY = "принтер занят: дождитесь конца печати"
FAILED_NOT_SAVED = "{error} Счётчик ошибок не сохранился: {reason}"
PRINTED_NOT_SAVED = "Напечатано, но счётчик не сохранился: {reason}"
# W7, W8, W9: тексты для людей — дословно из § 4.4. W7 называет путь в
# Системных настройках, W8 — класс и текст исключения и что делать
# оператору, W9 — почему запрос не выполнен.
NO_ACCESS = (
    "Нет доступа к папке {folder}. Разрешите Терминалу доступ: Системные настройки"
    " → Конфиденциальность и безопасность → Файлы и папки → Терминал."
)
CLIENT_ERROR = (
    "Ошибка клиента: {kind}: {error}. Закройте окно «Печать картинок» и запустите его снова."
)
FOREIGN_REQUEST = "Запрос отклонён: клиент принимает запросы только с этого компьютера."

# W9: имена хоста этого компьютера. Сервер слушает только `127.0.0.1`
# (§ 6.3), а браузер оператора может прийти к нему и как `localhost`.
# W9 п. 4: ровно эти два имени — без `0.0.0.0` и `::1`. Набор — кортеж, а
# не строка: `in` сравнивает имя целиком, и `local` или `127.0.0` не
# пройдут как подстроки.
_LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _detail(text: str, status_code: int) -> JSONResponse:
    """Ответ-отказ: JSON `{"detail": text}` с кодом `status_code` (W2, W4).

    Строку `detail` страница показывает оператору как есть (W6, U4).
    """
    return JSONResponse({"detail": text}, status_code=status_code)


def create_app(folder: Path, printer: Printer, static_dir: Path) -> FastAPI:
    """Собрать приложение для папки `folder`, принтера `printer` и страницы из `static_dir`.

    Приложение держит один объект счётчиков папки и один замок печати на
    всё время работы; папку и файл счётчиков ручки читают заново при каждом
    запросе (S2), поэтому новые файлы и правка счёта руками видны сразу.
    """
    # § 4.4: папка приводится один раз, при сборке. Ярлык может прийти к
    # ней через символическую ссылку, а `/api/health` и `/api/images`
    # должны называть настоящий путь: его запуск сравнивает со своей
    # приведённой папкой (M1, M2), а страница показывает оператору (U1, U6).
    folder = folder.resolve()
    # S3: один `Stats` на всё приложение — замок внутри него защищает
    # `increment` только тогда, когда все запросы идут через один объект.
    stats = Stats(folder)
    # W5: принтер один и физический — одновременно печатает только один
    # запрос. Замок из `threading`, потому что ручки синхронные и работают
    # в потоках пула FastAPI, а не в цикле событий.
    lock = threading.Lock()
    # § 4.4: документация и схема API оператору не нужны, и лишних адресов
    # на сервере не остаётся.
    # W6, решение контролёра по задаче 2.4: `redirect_slashes=False`. Иначе
    # FastAPI на адрес с косой чертой на конце (`/api/images/`) отвечает
    # пустым 307 без `detail`, а перенаправленный `POST` печати сохранил бы
    # метод и напечатал. Без перенаправления такой адрес — обычный JSON 404.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    @app.middleware("http")
    async def only_this_computer(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Пропустить к ручкам только запросы с этого компьютера, остальным — 403 (W9).

        Промежуточный слой, а не проверка в ручках: отказ приходит до любой
        ручки, поэтому чужой запрос не читает папку, не печатает и не
        трогает счётчики. Слой — `async def`, как требует Starlette; он
        только сравнивает заголовки и ничего не ждёт.
        """
        # W9 п. 1: подмена DNS (DNS rebinding) — чужая страница, чьё имя
        # стало указывать на `127.0.0.1`. Браузер пускает её к клиенту как к
        # её же серверу, но в `Host` шлёт её имя. `request.url.hostname` —
        # имя из `Host` без порта: порт выбирается при запуске (M3). Пустой
        # `Host` даёт `None`, и его в наборе тоже нет (W9 п. 4).
        # W9 (финальная проверка, 2026-09-28): запрос вовсе без `Host`
        # (HTTP/1.0) — отказ до разбора. Для такого запроса Starlette
        # собирает `request.url` из адреса сервера, `127.0.0.1`, и проверка
        # имени ниже его пропустила бы, хотя своего имени он не назвал.
        if request.headers.get("host") is None:
            return _detail(FOREIGN_REQUEST, 403)
        try:
            host = request.url.hostname
        except ValueError:
            # W9 п. 4, решение контролёра по задаче 2.4: `Host`, который не
            # разбирается (`[` — скобка IPv6 без пары), бросает
            # `ValueError`. Имени этого компьютера в нём нет — это тот же
            # чужой запрос, а не сбой клиента: 403, а не 500 W8 с
            # трассировкой в окне Терминала.
            return _detail(FOREIGN_REQUEST, 403)
        if host not in _LOCAL_HOSTS:
            return _detail(FOREIGN_REQUEST, 403)
        # W9 п. 2: любая страница в браузере оператора может вслепую
        # отправить `POST` на `127.0.0.1`, и браузер приложит её `Origin`.
        # Своя страница — только `http` с именем этого компьютера; `null`
        # (страница из файла, песочница) и пустой `Origin` дают пустую схему
        # и тоже отклонены (W9 п. 4): заголовок есть — значит, проверяется.
        # Порт не сравнивается (W9 п. 3): он выбирается при запуске.
        origin = request.headers.get("origin")
        if request.method == "POST" and origin is not None:
            try:
                parts = urlsplit(origin)
                origin_host = parts.hostname
            except ValueError:
                # W9 п. 4, решение контролёра по задаче 2.4: `Origin`,
                # который не разбирается (`http://[`), — тот же отказ, что и
                # чужой, а не 500 W8.
                return _detail(FOREIGN_REQUEST, 403)
            if parts.scheme != "http" or origin_host not in _LOCAL_HOSTS:
                return _detail(FOREIGN_REQUEST, 403)
        # W9 п. 3: `POST` без `Origin` пропускается — так шлют `curl` и
        # тесты; `GET` чужой странице не опасен: прочитать ответ ей не даст
        # сам браузер.
        return await call_next(request)

    # W7, W8: обработчики — `async def`, в отличие от ручек (§ 4.4): они не
    # читают ни папку, ни принтер, а только собирают ответ, поэтому
    # отвечают прямо в цикле событий и не занимают поток пула.
    @app.exception_handler(PermissionError)
    async def no_access(request: Request, exc: PermissionError) -> JSONResponse:
        """Ответить на `PermissionError` любой ручки 500 с текстом, как дать доступ к папке (W7).

        Так бывает, когда macOS не дала Терминалу доступ к папке на Рабочем
        столе: `scan` такой сбой не перехватывает (F9). Обработчик свой, а
        не ветка общего обработчика `Exception` (W8): свой отвечает и на
        этом заканчивает, а после общего Starlette пробрасывает исключение
        дальше.
        """
        # W7: папка — приведённая при сборке, та же, что в `/api/images`
        # (§ 4.4): оператор видит в тексте тот же путь, что в шапке
        # страницы (U1), а не путь ссылки, по которой пришёл ярлык.
        return _detail(NO_ACCESS.format(folder=str(folder)), 500)

    @app.exception_handler(Exception)
    async def client_error(request: Request, exc: Exception) -> JSONResponse:
        """Ответить на любое другое неперехваченное исключение 500 с его классом и текстом (W8).

        Пример из W8: папку переименовали при работающем клиенте. Без
        обработчика Starlette ответил бы голым текстом «Internal Server
        Error», и страница показала бы только код ответа, без причины (W6,
        U4). Трассировку Starlette после ответа всё равно пробрасывает
        дальше, и она остаётся в окне Терминала.
        """
        return _detail(CLIENT_ERROR.format(kind=type(exc).__name__, error=str(exc)), 500)

    # § 4.4: все ручки — обычные `def`. FastAPI выполняет их в пуле потоков,
    # поэтому долгая печать или чтение большой папки не останавливают
    # остальные запросы: пока идёт печать, список обновляется, а вторая
    # печать сразу получает 409 (W5).

    @app.get("/")
    def index() -> FileResponse:
        """Отдать страницу оператора `index.html` (§ 4.4, § 4.6, W9)."""
        return FileResponse(
            static_dir / "index.html",
            headers={
                # W9 (финальная проверка, 2026-09-28): браузер сверяется с
                # сервером при каждом открытии. Без этого после обновления
                # (I11) ярлык открывает тот же адрес (M3, M5), а браузер
                # днями показывает прежнюю страницу из кэша: `FileResponse`
                # шлёт только `Last-Modified` и `ETag`.
                "Cache-Control": "no-cache",
                # W9 (финальная проверка, 2026-09-28): чужой сайт не
                # встроит страницу в рамку поверх своей кнопки — иначе
                # щелчок оператора ушёл бы в «Печать» со своим `Origin`, и
                # проверка `Origin` в `only_this_computer` его пропустила бы.
                # `X-Frame-Options` — для браузеров, которые не знают
                # `frame-ancestors`.
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "frame-ancestors 'none'",
            },
        )

    @app.get("/api/health")
    def health() -> JSONResponse:
        """Ответить, что здесь работает photoprint и для какой папки (§ 4.4).

        По этому ответу запуск узнаёт свой сервер и уже запущенный клиент
        (M2, M5).
        """
        return JSONResponse({"app": "photoprint", "folder": str(folder)})

    @app.get("/api/images")
    def images() -> JSONResponse:
        """Вернуть папку и её картинки с пригодностью и счётчиками (W1).

        Порядок — как у `scan`: сверху самые свежие (F2).
        """
        # W1 (финальная проверка, 2026-09-28): файл счётчиков читается один
        # раз на запрос, до цикла, а не `get` на каждую картинку: записи
        # удалённых картинок остаются навсегда (S6), и при тысяче картинок
        # опрос раз в 3 с (U5) иначе занимал бы почти секунду процессора.
        # S2 держится: снимок новый при каждом запросе списка.
        snapshot = stats.snapshot()
        items: List[Dict[str, Any]] = []
        for entry in scan(folder):
            # S1: записи нет — ноль, как у `get`.
            counts = snapshot.get(entry.name, Counts(0, 0))
            items.append({
                "name": entry.name,
                "ctime": entry.ctime,
                # W1: пригодна — значит, у `check` нет причины (F3).
                "ok": entry.error is None,
                "error": entry.error,
                "printed": counts.printed,
                "failed": counts.failed,
            })
        return JSONResponse({"folder": str(folder), "images": items})

    @app.get("/api/images/{name}")
    def image(name: str) -> Response:
        """Отдать байты картинки `name` для миниатюры, с типом по расширению пути (W2, W3).

        Непригодная картинка тоже отдаётся: миниатюра нужна и ей, чтобы
        оператор видел, о каком файле строка с ошибкой.
        """
        # W2, F7: файл берётся только из записи `scan` — имя с `..` или `/`
        # не совпадёт ни с одной, и выйти за пределы папки нельзя.
        entry = find(folder, name)
        if entry is None:
            return _detail(NOT_IN_FOLDER.format(name=name), 404)
        # W3: байты читаются здесь, а не `FileResponse`: тот читает файл уже
        # после выхода из ручки, и файл, исчезнувший или закрытый для чтения
        # уже после `find`, дал бы голый 500 при отправке. Здесь такой
        # файл — тот же 404, что и W2.
        try:
            data = entry.path.read_bytes()
        except OSError:
            return _detail(NOT_IN_FOLDER.format(name=name), 404)
        # W3: тип — по расширению полного пути, а не имени: Python 3.9
        # разбирает строку как адрес, и имя с `:` (так Finder хранит `/`,
        # например `data:menu.jpg`) получило бы неверный тип. Путь
        # начинается с `/`, и его так не разобрать. Регистр не важен
        # (`Photo.JPG` — тоже `image/jpeg`); расширение, которого
        # `mimetypes` не знает (`.heic`), — общий двоичный тип.
        media_type = mimetypes.guess_type(str(entry.path))[0] or "application/octet-stream"
        return Response(data, media_type=media_type)

    @app.post("/api/print/{name}")
    def print_file(name: str) -> JSONResponse:
        """Напечатать картинку `name` и вернуть её новые счётчики (W4, W5).

        Ответ — первый сработавший из W4: 404, 422, 409, 503, 500, иначе 200.
        Счётчики меняются только после попытки печати: 404, 422 и 409 их
        не трогают.
        """
        # W4 п. 1, W2: имя ищется только среди картинок папки (F7).
        entry = find(folder, name)
        if entry is None:
            return _detail(NOT_IN_FOLDER.format(name=name), 404)
        # W4 п. 2: картинка читается и проверяется до замка. Непригодный или
        # недокопированный файл принтер не занимает и счёт не трогает.
        try:
            picture = load_printable(entry.path)
        except InvalidImage as exc:
            return _detail(str(exc), 422)
        # W4 п. 3, W5: замок без ожидания. Двойной клик или вторая вкладка
        # сразу получают 409, а не встают в очередь и не печатают картинку
        # второй раз.
        if not lock.acquire(blocking=False):
            return _detail(BUSY, 409)
        # W5: замок отпускается в `finally` при любом исходе, иначе после
        # первого же сбоя все следующие печати получали бы 409.
        try:
            try:
                printer.print_image(picture)
            except PrinterError as exc:
                # W4 п. 4, W5: попытка печати была — «ошибок печати» +1, под
                # тем же замком. Текст принтера уходит оператору как есть.
                try:
                    stats.increment(name, "failed")
                except StatsWriteError as e:
                    # W4 п. 4: оператор видит и сбой принтера, и то, что он
                    # не попал в счёт, — с причиной записи (S5).
                    return _detail(FAILED_NOT_SAVED.format(error=str(exc), reason=str(e)), 503)
                return _detail(str(exc), 503)
            # W4 п. 5, W5: принтер принял данные (P7) — «напечатано» +1, под
            # замком. Код 500, а не 503: печать уже состоялась, не записан
            # только счёт.
            try:
                counts = stats.increment(name, "printed")
            except StatsWriteError as e:
                return _detail(PRINTED_NOT_SAVED.format(reason=str(e)), 500)
        finally:
            lock.release()
        # W4 п. 6: новые значения обоих счётчиков — страница сразу обновит
        # карточку.
        return JSONResponse({"name": name, "printed": counts.printed, "failed": counts.failed})

    @app.get("/api/printer")
    def printer_state() -> JSONResponse:
        """Ответить, подключён ли принтер прямо сейчас (§ 4.4, P6).

        Принтер опрашивается при каждом запросе: кабель могут вынуть и
        вставить в любой момент, и индикатор на странице должен это видеть (U1).
        """
        return JSONResponse({"connected": printer.connected()})

    return app
