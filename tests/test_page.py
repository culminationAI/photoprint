"""Тесты страницы оператора — `app/static/index.html` (спецификация § 4.6).

Страница — один файл со встроенными CSS и JS. Её поведение U1–U8 целиком
проверяется в браузере на настоящем сервере по чек-листу пули 3 (§ 7.2,
§ 7.4 п. 5). Здесь pytest читает сам файл и закрепляет то, что видно без
браузера:
- все тексты для оператора из § 4.6 стоят в коде дословно и целиком, а
  тексты с подстановкой — шаблонными строками JS (U1, U3, U4, U6, U7);
- других русских текстов в коде нет, и каждый текст выводится на своём
  месте: константа не может остаться на месте, пока на экран идёт другая
  строка (U1, U3, U4, U6, U7);
- определение каждой константы текста закреплено дословно — имя вместе со
  значением: обмен значениями двух констант не проходит (U1, U3, U4, U6,
  U7);
- файл не ходит ни на какие внешние адреса и работает без интернета
  (§ 4.6);
- данные попадают на страницу только через `textContent`, никакой способ
  разобрать строку как разметку не встречается (U8);
- четыре запроса страницы — дословно как в правилах задачи, имя в обоих
  адресах закодировано (U3, U4, U8);
- опрос: оба запроса сразу при загрузке, список каждые 3 с, кроме времени
  печати и пока не вернулся прежний запрос списка, принтер каждые 5 с;
  обработчики таймеров — дословно (U5);
- ответ не 200 и отказ `fetch` дают тексты U4 и U7 (W6);
- карточка пересоздаётся только при смене ключа, строка ошибки печати
  переносится в новую карточку (U5);
- проводка страницы закреплена построчно вместе с разметкой и
  CSS-состояниями (§ 7.2, ворота 1): просмотр во весь экран с верха
  картинки (U3), кнопки «Печать» (U3, U4), печать от нажатия до ответа и
  возврат фокуса (U4, U5), сбой и возврат списка (U7), защита ответа списка
  (U4, U5), индикатор принтера (U1), расстановка карточек (U2, U5) и
  всплывающее «Напечатано» (U4). Браузера и node в тестовом окружении нет,
  и ошибка проводки иначе дошла бы только до чек-листа 3.3 или до
  заказчика;
- второй щелчок двойного щелчка не доходит ни до одного обработчика (U3,
  U4), а щелчок мышью по «Печать» сразу после сдвига списка не считается
  (U4) — финальная проверка, 2026-09-28;
- `GET /` настоящего приложения отдаёт именно этот файл и для показа, а не
  для скачивания (§ 4.4, M5).

Положительные проверки ищут в коде без комментариев: комментарии страницы
цитируют тексты для оператора дословно, и опечатка в самой константе иначе
прошла бы. Запреты (внешние адреса, разметка из строки) действуют на весь
файл, с комментариями: так строже. Блоки проводки сравниваются целиком,
без учёта отступов и переносов строк (`_squash`, `_block`).

Подменён только принтер — `FakePrinter` из `helpers` (§ 7.3); браузер
тесты не открывают.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

from helpers import APP, FakePrinter, client_for
from photoprint.web import create_app

# Файл страницы в рабочем дереве: этот же каталог `<app>/static` запуск
# отдаёт в `create_app` (M5), а установщик кладёт его заказчику.
PAGE = APP / "static" / "index.html"

# Точные тексты страницы (§ 4.6, таблица задачи 1.5); `{…}` — подстановка,
# которую страница делает шаблонной строкой JS.
PAGE_TEXTS = (
    "Печать картинок",
    "принтер готов",
    "принтер не найден",
    "напечатано: {printed} · ошибок печати: {failed}",
    "Печать",
    "Печатаю…",
    "Напечатано",
    "Ошибка клиента: HTTP {status}. Запустите «Печать картинок» ещё раз.",
    "Клиент не отвечает. Запустите «Печать картинок» ещё раз.",
    "В папке нет картинок. Положите JPEG шириной 576 px в папку {folder} — "
    "картинка появится здесь сама.",
)

# Определения текстов страницы — дословно, каждое целой строкой кода. Таблица
# PAGE_TEXTS говорит, какие строки есть в коде, но не какой константе какая
# досталась: обмен значениями `PRINTER_READY` и `PRINTER_MISSING` оставил бы
# все тексты на месте и все вызовы нетронутыми, а оператор увидел бы
# «принтер готов» без принтера (U1). Поэтому закреплено имя константы вместе
# с её значением, а у шаблонов — и порядок параметров.
TEXT_DEFINITIONS = (
    'const PRINTER_READY = "принтер готов";',
    'const PRINTER_MISSING = "принтер не найден";',
    "const COUNTS = (printed, failed) => `напечатано: ${printed} · ошибок печати: ${failed}`;",
    'const PRINT = "Печать";',
    'const PRINTING = "Печатаю…";',
    'const PRINTED = "Напечатано";',
    "const CLIENT_ERROR = (status) => `Ошибка клиента: HTTP ${status}. "
    "Запустите «Печать картинок» ещё раз.`;",
    'const NO_ANSWER = "Клиент не отвечает. Запустите «Печать картинок» ещё раз.";',
    "const EMPTY = (folder) => `В папке нет картинок. Положите JPEG шириной 576 px в папку "
    "${folder} — картинка появится здесь сама.`;",
)

# Подстановка в строке таблицы: всё от `{` до ближайшей `}`.
PLACEHOLDER = re.compile(r"\{[^}]*\}")

# Комментарии страницы: `<!-- … -->` в HTML, `/* … */` в CSS и JS и целая
# строка `// …` в JS. Одно выражение с тремя ветвями: что началось раньше,
# то и комментарий, поэтому `//` внутри `/* … */` и `/*` в комментарии `// …`
# разбираются верно. `//` вырезается, только когда начинает строку файла:
# `//` посреди строки кода (например, в строковом литерале JS) комментарием
# не считается, а что таких `//` в коде нет, проверяет `_page_code`.
COMMENT = re.compile(r"<!--.*?-->|/\*.*?\*/|^[ \t]*//[^\n]*", re.S | re.M)

# Русская буква. Все тексты для оператора в § 4.6 русские, а имена в коде
# страницы английские: русская буква в коде без комментариев — это текст,
# который увидит оператор.
CYRILLIC = re.compile("[А-Яа-яЁё]")

# Блок `<script>…</script>` целиком, с тегами: строки JS ищутся только в нём,
# а узлы текста разметки — только вне его.
SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.S)

# Способы вставить строку на страницу как разметку (U8). Кроме четырёх
# запрещённых правилом задачи — те, что делают то же другим путём:
# `Range.createContextualFragment`, `DOMParser`, `Element.setHTML` и
# `setHTMLUnsafe`, `Document.parseHTMLUnsafe`, `iframe.srcdoc`.
# `document.write` покрывает и `document.writeln`.
MARKUP_SINKS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "createContextualFragment",
    "DOMParser",
    "setHTML",
    "parseHTMLUnsafe",
    "srcdoc",
)


def _page_text() -> str:
    """Прочитать `app/static/index.html` целиком как UTF-8.

    Кодировка задана явно (глобальное правило «Тексты»): тексты страницы
    русские, а кодировка по умолчанию зависит от окружения.
    """
    return PAGE.read_text(encoding="utf-8")


def _page_code() -> str:
    """Текст страницы без комментариев HTML, CSS и JS.

    Комментарии страницы цитируют тексты для оператора («Печатаю…»,
    «Напечатано», «принтер не найден») и объясняют код его же словами.
    Положительная проверка, которая нашла бы нужное в комментарии, не
    заметила бы ошибку в самом коде.
    """
    code = COMMENT.sub("", _page_text())
    # Комментарий `//` в конце строки кода остался бы в коде и мог бы
    # подтвердить текст, которого в коде нет. Поэтому комментарии `//` в
    # странице пишутся только целой строкой, и здесь это проверяется: иначе
    # падают все проверки кода, а не проходят молча.
    assert "//" not in code
    return code


def _template_pattern(line: str) -> str:
    """Регулярное выражение шаблонной строки JS для строки таблицы с `{…}`.

    Строка целиком стоит в обратных кавычках; куски между подстановками —
    дословно, а на месте `{x}` — `${x}` или `${<объект>.x}`. Имя подстановки
    закреплено: шаблон с переставленными `${failed}` и `${printed}` показал
    бы оператору счётчики наоборот.
    """
    pieces = PLACEHOLDER.split(line)
    names = [match.group(0)[1:-1] for match in PLACEHOLDER.finditer(line)]
    pattern = "`" + re.escape(pieces[0])
    for name, piece in zip(names, pieces[1:]):
        pattern += r"\$\{(?:[\w.]*\.)?" + re.escape(name) + r"\}" + re.escape(piece)
    return pattern + "`"


def _squash(text: str) -> str:
    """Сжать каждый промежуток из пробелов, отступов и переносов строк в один пробел.

    Проводка страницы закрепляется построчно (§ 7.2), но не по отступам:
    блок в тесте записан так же, как в странице, и сравнение не зависит от
    ширины отступа и пустых строк, оставшихся на месте комментариев.
    """
    return re.sub(r"\s+", " ", text).strip()


def _block(code: str, head: str) -> str:
    """§ 7.2 (ворота 1): блок кода от заголовка head до его закрывающей `}`, пробелы сжаты (`_squash`).

    - head (например, `function place(els) {`) встречается в коде ровно
      один раз;
    - если head объявляет функцию, её имя объявлено в коде ровно один раз
      (`const`, `let`, `var`, `function`): второе объявление ниже заменило
      бы закреплённое, и работала бы непроверенная версия;
    - конец блока — `}`, на которой счёт скобок от head возвращается к
      нулю. В закреплённых блоках скобки внутри строк JS парные (шаблоны
      `${…}`, `{method: "POST"}`), поэтому простой счёт верен; блок без
      пары — провал теста, а не пустое сравнение.
    Сравнение с ожидаемым блоком через `==`: лишняя, пропавшая или
    изменённая строка внутри функции видна в отчёте pytest.

    Ещё: в коде нет `return`, за которым сразу перевод строки. `_squash`
    превращает перевод строки в пробел и такой разрыв не видит, а JS после
    `return` на конце строки ставит `;` сам (ASI) — `makeCard` вернула бы
    `undefined`, и не нарисовалась бы ни одна карточка.
    """
    assert re.search(r"\breturn[ \t]*\n", code) is None
    assert code.count(head) == 1, head
    declared = re.match(r"(?:async )?function (\w+)\(", head)
    if declared is not None:
        name = declared.group(1)
        assert len(re.findall(r"\b(?:const|let|var|function)\s+" + name + r"\b", code)) == 1, name
    start = code.index(head)
    depth = 0
    for end in range(start, len(code)):
        if code[end] == "{":
            depth += 1
        elif code[end] == "}":
            depth -= 1
            if depth == 0:
                return _squash(code[start:end + 1])
    raise AssertionError("нет закрывающей скобки: " + head)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """Пустая папка с картинками; имя с пробелом и кириллицей, как у заказчика."""
    path = tmp_path / "Печать картинок"
    path.mkdir()
    return path


def test_page_has_all_texts() -> None:
    """U1, U3, U4, U6, U7: каждый текст для оператора стоит в коде страницы дословно и целиком.

    Поиск идёт по коду без комментариев. Для каждой строки таблицы:
    - каждый кусок между подстановками длиной от 3 символов есть в коде —
      с пробелами и знаками вокруг подстановки, их оператор видит (§ 4.6);
    - строка без подстановки — целый литерал: в кавычках JS или между `>` и
      `<` разметки. Так «Печать» не находится внутри «Печать картинок», а
      хвост CLIENT_ERROR — внутри NO_ANSWER;
    - строка с подстановкой — одна шаблонная строка JS в обратных кавычках,
      где на месте `{x}` стоит `${…x}` (правило задачи «подстановки — через
      шаблонные строки JS»). Без `$` оператор увидел бы «{folder}», в
      обычных кавычках — «${printed}», а обрезанный хвост CLIENT_ERROR не
      нашёлся бы внутри NO_ANSWER.
    «Печать картинок» к тому же — заголовок `<h1>` в шапке (U1): тот же текст
    в `<title>` шапку не заменяет.
    """
    code = _page_code()
    missing: List[str] = []
    for line in PAGE_TEXTS:
        pieces = PLACEHOLDER.split(line)
        for piece in pieces:
            # Куски короче 3 символов (пустой хвост, одна точка) нашлись бы
            # в любом файле и ничего бы не доказали.
            if len(piece) >= 3 and piece not in code:
                missing.append(piece)
        if len(pieces) == 1:
            pattern = "[\"'`>]" + re.escape(line) + "[\"'`<]"
        else:
            pattern = _template_pattern(line)
        if re.search(pattern, code) is None:
            missing.append(line)
    assert missing == []

    header = re.search(r"<header\b.*?</header>", code, re.S)
    assert header is not None
    assert ">Печать картинок</h1>" in header.group(0)


def test_page_has_only_table_texts() -> None:
    """U1, U3, U4, U6, U7: в коде страницы нет русского текста, которого нет в таблице § 4.6.

    test_page_has_all_texts доказывает, что каждый текст таблицы стоит в коде
    целым литералом, но не что до оператора доходит именно он: константа
    `PRINTED` осталась бы на месте, а в вызове стояло бы `toast("Готово")`.
    Поэтому из кода без комментариев вырезаются все разрешённые места
    текстов:
    - внутри `<script>` — строка JS в двойных кавычках, равная тексту без
      подстановки, и шаблонная строка, целиком совпадающая с
      `_template_pattern` текста с подстановкой;
    - вне `<script>` — узел текста разметки между `>` и `<`, равный тексту
      без подстановки (пробелы по краям узла браузер не показывает).
    После этого в коде не остаётся ни одной русской буквы: ни в другой
    строке JS в любых кавычках, ни в атрибуте, ни в CSS, ни в разметке.
    Так не проходят «Печатать» на кнопке, «Печатаю...» с тремя точками
    вместо «…» и «Готово» во всплывающем сообщении при живых константах
    `PRINT`, `PRINTING` и `PRINTED`.
    """
    code = _page_code()
    plain = [line for line in PAGE_TEXTS if PLACEHOLDER.search(line) is None]
    templated = [line for line in PAGE_TEXTS if PLACEHOLDER.search(line) is not None]
    js_texts = re.compile(
        "|".join(
            ['"' + re.escape(line) + '"' for line in plain]
            + [_template_pattern(line) for line in templated]
        )
    )
    # Альтернатива «Печать» стоит раньше «Печать картинок», но в узле
    # «Печать картинок» проверка `(?=<)` после «Печать» не проходит, и
    # выражение переходит к следующей альтернативе.
    html_texts = re.compile(
        r"(?<=>)\s*(?:" + "|".join(re.escape(line) for line in plain) + r")\s*(?=<)"
    )

    rest: List[str] = []
    start = 0
    for block in SCRIPT.finditer(code):
        rest.append(html_texts.sub("", code[start:block.start()]))
        rest.append(js_texts.sub("", block.group(0)))
        start = block.end()
    rest.append(html_texts.sub("", code[start:]))
    stray = [line.strip() for line in "".join(rest).splitlines() if CYRILLIC.search(line)]
    assert stray == []


def test_page_texts_in_place() -> None:
    """U1, U3, U4, U6, U7: каждый текст для оператора выводится на своём месте и с верными данными.

    test_page_has_only_table_texts не пускает в код чужой текст, но текст
    таблицы не на своём месте прошёл бы и его. Поэтому закреплены сами
    константы и места вывода:
    - каждое определение из TEXT_DEFINITIONS — целая строка кода верхнего
      уровня скрипта (без отступа), ровно одна, а каждое имя объявлено ровно
      один раз. Обмен значениями двух констант («принтер готов» у
      PRINTER_MISSING, «Напечатано» у PRINT), переставленные параметры
      COUNTS и одноимённая константа с чужим текстом внутри функции, которая
      закрыла бы верхнюю, не проходят, хотя таблица текстов цела и вызовы те
      же;
    - индикатор: при `connected` — PRINTER_READY, иначе PRINTER_MISSING (U1);
      переставленные константы показали бы «принтер готов» без принтера;
    - счётчики карточки — COUNTS(printed, failed) именно в этом порядке (U3);
    - кнопка — PRINT при создании; у печатаемой картинки PRINTING, у
      остальных PRINT (U3, U4);
    - итог печати: без отказа — всплывающее PRINTED, при отказе — красная
      строка с его текстом под карточкой (U4); обратное условие показало бы
      «Напечатано» при 409 и 422;
    - пустой список — EMPTY с путём папки из ответа, а не с объектом ответа
      (U6);
    - удачный ответ списка — карточки, сбой — его собственный текст на месте
      списка (U7): «Нет доступа к папке…» (W7) не подменяется «Клиент не
      отвечает…».
    """
    code = _page_code()

    for definition in TEXT_DEFINITIONS:
        assert len(re.findall("(?m)^" + re.escape(definition) + "$", code)) == 1, definition
        name = re.match(r"const (\w+) = ", definition).group(1)
        # Второе объявление того же имени (`const`, `let`, `var`, `function`)
        # внутри функции закрыло бы верхнее, и его текст ушёл бы на экран.
        declared = re.findall(r"\b(?:const|let|var|function)\s+" + name + r"\b", code)
        assert len(declared) == 1, name

    assert '$("printerText").textContent = connected ? PRINTER_READY : PRINTER_MISSING;' in code
    assert "meta.textContent = COUNTS(image.printed, image.failed);" in code
    assert "button.textContent = PRINT;" in code
    assert "card.button.textContent = name === printing ? PRINTING : PRINT;" in code
    assert re.search(
        r"if \(failure === null\) \{\s*toast\(PRINTED\);\s*\} else \{\s*"
        r"card\.fail = makeFail\(failure\);",
        code,
    )
    assert "showNote(data.images.length === 0 ? EMPTY(data.folder) : null, false);" in code
    assert re.search(
        r"if \(failure === null\) \{\s*showList\(data\);\s*\} else \{\s*showListFailure\(failure\);",
        code,
    )


def test_page_is_offline() -> None:
    """§ 4.6 и правило задачи «один файл»: страница не ссылается ни на какой внешний адрес.

    Заказчик может печатать без интернета: скрипты, шрифты и стили
    встроены в файл. Кроме адресов со схемой запрещены:
    - адрес без схемы `//host/…` сразу после кавычки, `(` или `=` — браузер
      дополнил бы его схемой страницы и пошёл на внешний хост;
    - `<script src>`, `<link>` и `@import` с любым адресом — сервер отдаёт
      только `index.html`, соседний файл страница бы не получила.
    """
    text = _page_text()

    assert "http://" not in text
    assert "https://" not in text
    assert "//cdn" not in text
    assert re.search(r"[\"'(=]\s*//", text) is None
    assert re.search(r"<script\b[^>]*\bsrc\s*=", text) is None
    assert "<link" not in text
    assert "@import" not in text


def test_page_is_safe() -> None:
    """U8: данные — только через `textContent`, ни одна строка не разбирается как разметка.

    Имя файла приходит из папки и может содержать `<`, `&` или кавычки:
    ни один способ вставить строку как разметку в странице не встречается,
    даже в комментарии. Кодирование имени в адресах миниатюры и печати по
    месту закрепляет test_page_requests.
    """
    text = _page_text()

    for forbidden in MARKUP_SINKS:
        assert forbidden not in text
    assert "encodeURIComponent(" in text
    assert "textContent" in text


def test_page_requests() -> None:
    """§ 4.4, U3, U4, U8: четыре запроса страницы — дословно как в правилах задачи.

    - список и принтер — `fetch("/api/images")` и `fetch("/api/printer")`;
    - миниатюра — `"/api/images/" + encodeURIComponent(<имя>) + "?v=" +
      <ctime>`: `?v=` меняет адрес, когда файл заменили под тем же именем,
      и браузер не покажет старую миниатюру из кэша (U3);
    - печать — `fetch("/api/print/" + encodeURIComponent(<имя>), {method:
      "POST"})`;
    - просмотр во весь экран берёт адрес самой миниатюры, `thumb.src`, уже
      закодированный: свой адрес из имени без кодирования не открыл бы
      «кот #1.jpg» (U3).
    Каждый адрес закреплён на своём месте: одного `encodeURIComponent(` на
    файл мало. Без кодирования в любом из двух адресов имя с `#`, `?`, `+`
    или `%` уходит чужим путём (Review Focus 5): «кот #1.jpg» ушёл бы как
    `/api/print/кот ` и получил 404; `encodeURI` оставляет `#` и `?` как есть.
    """
    code = _page_code()

    assert 'fetch("/api/images")' in code
    assert 'fetch("/api/printer")' in code
    assert re.search(
        r'"/api/images/" \+ encodeURIComponent\((?:[\w.]*\.)?name\) \+ "\?v=" \+ (?:[\w.]*\.)?ctime\b',
        code,
    )
    assert re.search(
        r'fetch\("/api/print/" \+ encodeURIComponent\((?:[\w.]*\.)?name\), \{method: "POST"\}\)',
        code,
    )
    assert 'thumb.addEventListener("click", () => openPeek(thumb.src, image.name));' in code


def test_page_timers() -> None:
    """U1, U5: оба запроса сразу при загрузке, список каждые 3 с кроме времени печати и запроса в полёте, принтер каждые 5 с.

    Каждый вызов `setInterval` в коде разбирается на обработчик и период:
    - вызовов ровно два, с периодами 3000 и 5000: «3000» как подстрока
      нашлась бы и в «30000», а `setTimeout` вместо `setInterval` опросил бы
      принтер один раз — после переподключения кабеля индикатор бы не
      обновился (U1);
    - обработчики сравниваются целиком, а не по подстроке. Обработчик 3000 —
      ровно `() => { if (printing === null && listPending === 0)
      loadImages(); }`: запрос списка и пропуск тика, пока идёт печать и
      пока не вернулся прежний запрос списка (U5). Обработчик 5000 — ровно
      сама функция `loadPrinter`. Подстрока «loadPrinter» нашлась бы и в `loadPrinter()`
      (функция вызывается один раз, когда выполняется сам вызов
      `setInterval`; тот получает Promise, превращает его в строку
      «[object Promise]» и на каждом тике падает с SyntaxError), и в
      `() => loadPrinter` (функция не вызывается вовсе). В обоих случаях
      индикатор принтера не обновляется после загрузки (U1). Точно так же
      «loadImages» нашлось бы в `loadImages;` без вызова, а
      «printing === null» — в обратном условии
      `if (printing === null) return;`.
    Первые запросы — вызовы `loadImages();` и `loadPrinter();` отдельными
    строками верхнего уровня скрипта: без них страница ждала бы первого тика
    (U5 «оба запроса уходят сразу при загрузке»).

    Запрос списка в полёте (U5 «пока не вернулся предыдущий запрос списка —
    медленный диск не копит запросы»): `listPending` — сколько запросов
    списка ещё не вернулось. Закреплено:
    - объявление `let listPending = 0;` верхнего уровня;
    - `loadImages` целиком: `listPending += 1;` до `fetch`, а `listPending
      -= 1;` — в `finally` того же `try`, что ждёт `fetch` и тело ответа. Без
      `finally` любой путь мимо уменьшения навсегда оставил бы счётчик
      больше нуля, и список бы больше не обновлялся; рост после `await
      fetch` не закрыл бы тик на время самого запроса;
    - имя `listPending` встречается в коде ровно 4 раза — объявление, рост,
      уменьшение и условие тика: лишний сброс `listPending = 0` где-то ещё
      снова пустил бы тики в очередь.
    Счётчик, а не флаг «да/нет»: запрос после печати (U4 «сразу») уходит,
    даже если запрос тика ещё в полёте. Флаг сбросил бы тот из двух, что
    вернулся первым, и тик отправил бы новый запрос при живом старом; после
    каждой такой печати в полёте оставалось бы на один запрос больше.
    """
    text = _page_text()
    code = _page_code()

    assert "3000" in text
    assert "5000" in text
    # re.S: обработчик в несколько строк тоже разбирается, а не пропадает из
    # счёта; `.*?` берёт ближайшие `, <число>);` — конец этого же вызова.
    calls = re.findall(r"setInterval\((.*?),\s*(\d+)\);", code, re.S)
    assert code.count("setInterval(") == 2
    assert len(calls) == 2
    handlers = {period: handler.strip() for handler, period in calls}
    assert sorted(handlers) == ["3000", "5000"]
    # Точное равенство строже прежних проверок подстрок «loadImages»,
    # «printing === null» и «loadPrinter» и включает их все.
    assert handlers == {
        "3000": "() => { if (printing === null && listPending === 0) loadImages(); }",
        "5000": "loadPrinter",
    }
    assert re.search(r"(?m)^loadImages\(\);$", code)
    assert re.search(r"(?m)^loadPrinter\(\);$", code)

    # U5: запрос списка в полёте — счётчик растёт до `fetch` и убывает в
    # `finally` того же `try`.
    assert re.search(r"(?m)^let listPending = 0;$", code)
    assert re.search(
        r"async function loadImages\(\) \{\s*const seq = \+\+listAsked;\s*listPending \+= 1;\s*"
        r"let data = null;\s*let failure = null;\s*"
        r'try \{\s*const response = await fetch\("/api/images"\);\s*'
        r"if \(response\.status === 200\) \{\s*data = await response\.json\(\);\s*"
        r"\} else \{\s*failure = await problemText\(response\);\s*\}\s*"
        r"\} catch \(err\) \{\s*failure = NO_ANSWER;\s*\} finally \{\s*listPending -= 1;\s*\}",
        code,
    )
    assert code.count("listPending") == 4


def test_page_failure_texts() -> None:
    """U4, U7, W6: ответ не 200 и отказ `fetch` дают тексты § 4.6, а не «Напечатано».

    - Успех — ровно код 200: `=== 200` в запросах списка и принтера,
      `!== 200` в печати, других сравнений кода ответа нет. При `>= 500`
      отказы 409 (две вкладки, Review Focus 4), 422 (файл ещё копируется,
      Review Focus 3) и 404 показали бы «Напечатано».
    - Ответ не 200 в печати и в списке разбирает `problemText`: из тела
      берётся только строка `detail` (W6), иначе — CLIENT_ERROR с кодом
      ответа. Тело читается внутри `try` самой `problemText`: без него тело
      не JSON (голый 500) бросило бы исключение в `catch` вызывающего, и
      оператор увидел бы «Клиент не отвечает…» от живого сервера.
    - Отказ `fetch` в списке и в печати — ровно два блока `catch` с
      `failure = NO_ANSWER;`: CLIENT_ERROR(0) показал бы «HTTP 0» вместо
      «Клиент не отвечает…».
    """
    code = _page_code()

    comparisons = re.findall(r"response\.status\s*([!=<>]=*)\s*(\d+)", code)
    assert sorted(comparisons) == [("!==", "200"), ("===", "200"), ("===", "200")]
    assert "if (response.status !== 200) failure = await problemText(response);" in code
    assert code.count("failure = await problemText(response);") == 2
    assert re.search(
        r"async function problemText\(response\) \{\s*try \{\s*const body = await response\.json\(\);",
        code,
    )
    assert 'if (body !== null && typeof body.detail === "string") return body.detail;' in code
    assert "return CLIENT_ERROR(response.status);" in code
    assert len(re.findall(r"catch \(\w+\) \{\s*failure = NO_ANSWER;\s*\}", code)) == 2


def test_page_card_key() -> None:
    """U5: карточка пересоздаётся, только если изменился её ключ `ctime|error|printed|failed`.

    - Ключ — шаблонная строка из четырёх полей картинки, как в правиле
      задачи: без `printed` и `failed` новые счётчики не появились бы на
      карточке после печати (U4).
    - Карточка с тем же ключом берётся старая — миниатюра не мигает.
    - Строка ошибки печати переносится в новую карточку: неудачная печать
      сама меняет ключ («ошибок печати» +1), а строка живёт до следующего
      нажатия на этой карточке.
    """
    code = _page_code()

    assert "`${image.ctime}|${image.error}|${image.printed}|${image.failed}`" in code
    assert re.search(
        r"if \(old !== undefined && old\.key === key\) \{\s*next\.set\(image\.name, old\);",
        code,
    )
    assert re.search(
        r"if \(old !== undefined && old\.fail !== null\) \{\s*"
        r"card\.fail = old\.fail;\s*card\.el\.append\(old\.fail\);",
        code,
    )


def test_page_thumb_keeps_image_proportions() -> None:
    """U3: миниатюра — в пропорциях картинки: ширина 120 px, высота по картинке, не больше 160 px.

    Картинки заказчика шириной 576 бывают и горизонтальными (576×300), и
    длинными, как чек. Рамка с заданной высотой и `object-fit:cover`
    обрезала бы горизонтальную картинку по бокам — оператор не узнал бы её
    в списке (решение владельца 2026-09-28). `height:auto` сохраняет
    пропорции, `max-height` не даёт длинному чеку растянуть карточку, а
    `object-position:top` показывает у него верх. Колонка сетки карточки —
    той же ширины, что миниатюра, иначе миниатюра вылезла бы на имя.
    """
    code = _page_code()
    rule = re.search(r"(?ms)^\.thumb\{(.*?)\}", code)
    assert rule is not None
    body = rule.group(1)
    assert re.search(r"\bwidth:120px;", body)
    assert re.search(r"\bheight:auto;", body)
    assert re.search(r"\bmax-height:160px;", body)
    assert "object-fit:cover;" in body
    assert "object-position:top;" in body
    assert re.search(r"(?m)^\s*display:grid; grid-template-columns:120px 1fr auto;", code)


def test_page_peek_closes() -> None:
    """U3: просмотр во весь экран открывается с верха картинки, закрывается кликом и Esc — и правда исчезает с экрана.

    Проводка просмотра закреплена построчно:
    - разметка: один элемент с классом `peek` (его красит CSS) и id `peek`
      (его находит скрипт), внутри — картинка `peekImg`;
    - `openPeek` ставит адрес и подпись картинки и `data-open="true"`,
      `closePeek` — `data-open="false"`; других записей `data-open` нет.
      Третье и последнее упоминание `dataset.open` — чтение в `printImage`
      (фокус не возвращается на «Печать» под открытым просмотром, U3, U4);
      ту строку целиком закрепляет блок `printImage` в test_page_print_flow;
    - `openPeek` сбрасывает прокрутку просмотра (`scrollTop = 0`) после
      показа, и это единственная запись `scrollTop` (U3 «с верха картинки»,
      финальная проверка, 2026-09-28). Браузер помнит прокрутку спрятанного
      (`display:none`) просмотра и возвращает её при показе: прокрутив один
      длинный чек до середины, оператор открывал бы следующий на той же
      глубине — среди пустых строк, без шапки чека, и принял бы его за
      чужую или пустую картинку. Сброс до `data-open="true"` не работает: у
      спрятанного элемента нет блока прокрутки (CSSOM), и WebKit и Chromium
      его пропускают (проверено в финальной проверке);
    - клик по самому просмотру и `keydown` с `event.key === "Escape"`
      вызывают `closePeek`. Safari и Chrome присылают на Esc именно
      "Escape": со старым значением IE "Esc" оператор жал бы Esc, а картинка
      так и оставалась бы на весь экран;
    - CSS прячет просмотр (`display:none`) и показывает его только при
      `data-open="true"`; других селекторов состояния у `.peek` нет.
      Селектор по одному наличию атрибута `.peek[data-open]` совпал бы и с
      "false": после первого же открытия затемнение не закрылось бы ни
      кликом, ни Esc и закрыло бы все кнопки «Печать» до перезагрузки
      страницы.
    Открытие по клику на миниатюру закрепляет test_page_requests.
    """
    code = _page_code()

    assert '<div class="peek" id="peek"><img id="peekImg" alt=""></div>' in code
    assert _block(code, "function openPeek(src, name) {") == _squash("""
        function openPeek(src, name) {
          const img = $("peekImg");
          img.src = src;
          img.alt = name;
          $("peek").dataset.open = "true";
          $("peek").scrollTop = 0;
        }
    """)
    assert _block(code, "function closePeek() {") == _squash("""
        function closePeek() {
          $("peek").dataset.open = "false";
        }
    """)
    # Две записи (openPeek, closePeek) и одно чтение в printImage.
    assert code.count("dataset.open") == 3
    assert code.count("scrollTop") == 1
    assert _squash("""
        $("peek").addEventListener("click", closePeek);
        document.addEventListener("keydown", (event) => {
          if (event.key === "Escape") closePeek();
        });
    """) in _squash(code)

    assert re.search(r"(?m)^\.peek\{[^}]*\bdisplay:none;", code)
    assert re.findall(r"\.peek\[[^\]]*\]", code) == ['.peek[data-open="true"]']
    assert re.search(r'(?m)^\.peek\[data-open="true"\]\{display:flex\}$', code)


def test_page_buttons_during_print() -> None:
    """U3, U4: пока идёт печать, недоступны все кнопки «Печать»; у непригодной картинки кнопки нет.

    - `setButtons` закреплена целиком:
      - `disabled = printing !== null` — недоступна каждая кнопка, а не
        только печатаемая: иначе остальные выглядели бы живыми, а нажатие
        на них молча глотала бы проверка в начале `printImage` — оператор
        жмёт «Печать», и ничего не происходит;
      - `if (card.button === null) continue;` — у непригодной картинки
        кнопки нет. Без пропуска одна картинка шириной 800 px в папке роняла
        бы `setButtons` с TypeError до `fetch`: ничего не печатается,
        «Печатаю…» висит, список больше не обновляется, и любое следующее
        нажатие игнорируется до перезагрузки страницы;
    - в `makeCard` кнопка есть только у пригодной картинки (`image.ok`), её
      нажатие печатает эту картинку по имени, а в карточку уходит та же
      кнопка или `null` — на них опирается `setButtons`; у непригодной
      вместо кнопки — причина из ответа (U3);
    - нажатие не печатает, если это щелчок мышью (`event.detail` не 0) в
      первые `LIST_SETTLE_MS` после сдвига списка, а высоту карточки с
      создания видит наблюдатель сдвигов `cardResizes` (U4; почему — в
      test_page_click_after_list_moved_is_ignored);
    - CSS: недоступная кнопка серая, и правило `.go:disabled` стоит ниже
      `.go:hover` той же специфичности. Иначе при наведении недоступная
      кнопка во время печати выглядела бы живой.
    """
    code = _page_code()

    assert _block(code, "function setButtons() {") == _squash("""
        function setButtons() {
          for (const [name, card] of cards) {
            if (card.button === null) continue;
            card.button.disabled = printing !== null;
            card.button.textContent = name === printing ? PRINTING : PRINT;
          }
        }
    """)
    # Хвост makeCard вместе с её закрывающей скобкой: лишняя строка перед
    # концом функции тоже не проходит.
    assert _squash("""
          let button = null;
          let side;
          if (image.ok) {
            button = document.createElement("button");
            button.className = "go";
            button.textContent = PRINT;
            button.addEventListener("click", (event) => {
              if (event.detail !== 0 && performance.now() - listMovedAt < LIST_SETTLE_MS) return;
              printImage(image.name);
            });
            side = button;
          } else {
            side = document.createElement("div");
            side.className = "bad";
            side.textContent = image.error;
          }

          el.append(thumb, info, side);
          cardResizes.observe(el);
          return { key, el, button, fail: null };
        }
    """) in _squash(code)

    disabled = ".go:disabled{cursor:wait; background:var(--ink-soft); border-color:var(--ink-soft); color:var(--paper)}"
    assert re.search("(?m)^" + re.escape(disabled) + "$", code)
    assert code.count(".go:hover{") == 1
    assert code.index(".go:hover{") < code.index(disabled)


def test_page_second_click_of_double_click_is_ignored() -> None:
    """U3, U4: второй и следующие щелчки одного двойного щелчка (`event.detail > 1`) не доходят ни до одного обработчика.

    Первый щелчок сам открывает или закрывает просмотр и сам запускает
    печать, поэтому второй попадает в то, что после первого оказалось под
    указателем (финальная проверка, 2026-09-28; воспроизведено в WebKit и
    Chromium):
    - двойной щелчок по миниатюре — первый открывает просмотр, второй
      попадает в затемнение и сразу его закрывает: картинка мигает, и
      заказчик, привыкший к двойному щелчку в Finder, решает, что просмотр
      сломан (U3 «второй щелчок двойного щелчка просмотр не закрывает»);
    - двойной щелчок по затемнению над кнопкой «Печать» — первый закрывает
      просмотр, второй нажимает «Печать»: печать, которую не просили, и +1
      к «напечатано» (U4);
    - двойной щелчок по «Печать» при быстром ответе — без принтера 503
      приходит за миллисекунды, кнопки снова доступны до второго щелчка, и
      он уходит второй печатью: «ошибок печати» +2 вместо +1, а пункты 4 и
      5 приёмки ждут +1 (U4 «второй и следующие щелчки одного двойного
      щелчка ничего не печатают», Review Focus 4).
    Закреплено:
    - слушатель `click` на `document` в фазе захвата (третий аргумент
      `true`) останавливает щелчок с `event.detail > 1` целиком, дословно.
      В фазе всплытия обработчики миниатюры, просмотра и кнопки успели бы
      раньше него. `>= 1` или `!== 0` остановили бы и одиночный щелчок —
      страница перестала бы отвечать на мышь; `!== 1` остановил бы нажатие
      с клавиатуры (`detail` 0); `> 2` пропустил бы двойной щелчок;
    - слушатель `click` на `document` ровно один, `event.detail` в коде —
      ровно в двух местах: здесь и в защите от сдвига списка
      (test_page_click_after_list_moved_is_ignored);
    - слушатель стоит в коде раньше слушателя просмотра — рядом с
      остальной проводкой щелчков.
    """
    code = _page_code()

    guard = _squash("""
        document.addEventListener("click", (event) => {
          if (event.detail > 1) event.stopPropagation();
        }, true);
    """)
    assert guard in _squash(code)
    assert code.count('document.addEventListener("click"') == 1
    assert code.count("event.detail") == 2
    assert _squash(code).index(guard) < _squash(code).index('$("peek").addEventListener("click", closePeek);')


def test_page_click_after_list_moved_is_ignored() -> None:
    """U4: щелчок мышью по «Печать» в первые 0,8 с после сдвига списка не считается — кнопка могла уехать из-под указателя.

    Опрос раз в 3 с вставляет новую картинку наверх (F2 — новые сверху;
    смена атрибутов файла тоже поднимает его наверх), и список сдвигается
    под неподвижным указателем. Оператор навёл на «Печать» одной картинки,
    а щелчок пришёлся на «Печать» другой — напечаталась чужая картинка, и
    страница сказала «Напечатано» (финальная проверка, 2026-09-28;
    воспроизведено в WebKit и Chromium, наверху списка — при любой
    прокрутке). Закреплено:
    - `const LIST_SETTLE_MS = 800;` — 0,8 с из U4, константа верхнего
      уровня, и `let listMovedAt = -Infinity;` — «ещё не сдвигался»:
      разница с ним бесконечна, и щелчок считается;
    - `listMoved` целиком — запоминает `performance.now()`: часы, которые
      не прыгают при переводе времени на маке, в отличие от `Date.now()`;
    - `sameNames` целиком — те же имена в том же порядке: вызов в
      `showList` (закреплён в test_page_list_failure_and_return) отмечает
      сдвиг, когда опрос вставил, убрал или переставил карточки, а
      пересоздание карточки на её же месте сдвигом не считает;
    - сдвиг без перестановки — карточка на виду сменила высоту: у новой
      карточки миниатюра догружается позже самой карточки и сдвигает всё
      ниже неё второй раз (проверка видела 70 px, затем ещё 19 px через
      17 мс, а на медленном диске — позже 0,8 с), появилась строка ошибки,
      изменилось окно. Его ловит `cardsResized` — обработчик
      `ResizeObserver`, который наблюдает каждую карточку с `makeCard` до
      ухода со страницы (`unobserve` в `showList`, иначе ушедшие карточки
      копились бы в наблюдателе). `cardsResized` закреплена целиком:
      - первое наблюдение карточки (`seen === undefined`) — не сдвиг: её
        появление уже отметил `sameNames`, а пересозданная карточка
        получает в `cardHeights` высоту прежней (`showList`). Без этого
        сдвигом считалось бы и пересоздание карточки с новыми счётчиками
        сразу после печати, и повторный щелчок по «Печать» той же картинки
        сразу после «Напечатано» пропадал бы (проверено в WebKit и
        Chromium: печать проходит);
      - карточка, чей верх ниже окна (`top < window.innerHeight` не
        выполнено), двигает только то, что тоже ниже окна. Без этого
        условия миниатюры, которые браузер догружает при первой прокрутке
        длинного списка (`loading="lazy"`), глушили бы щелчок по кнопке на
        экране: на 200 картинках в WebKit и Chromium пропадали 3 щелчка из
        4 в первые 0,7 с после прокрутки.
      Одна перестановка не меняет высоту карточек (файл переименовали,
      файл из середины поднялся наверх) — её ловит `sameNames`. Наблюдатель
      и таблица высот — константы верхнего уровня; `WeakMap` не держит в
      памяти карточки, ушедшие со страницы;
    - обработчик «Печать» (он же закреплён в test_page_buttons_during_print)
      не печатает, если это щелчок мышью (`event.detail !== 0`) раньше
      `LIST_SETTLE_MS` после сдвига. Нажатие с клавиатуры (`detail` 0)
      печатает всегда: фокус остаётся на той же кнопке, куда бы она ни
      уехала. Обратное условие печатало бы только сразу после сдвига;
    - имена используются ровно там, где закреплено: `listMovedAt` —
      объявление, запись и проверка; `listMoved` — объявление и вызовы в
      `showList` и `cardsResized`; `LIST_SETTLE_MS` — объявление и
      проверка; `cardResizes` — объявление, `observe` в `makeCard` и
      `unobserve` в `showList`. Лишняя запись `listMovedAt` где-то ещё
      (например, при каждом опросе) глушила бы все щелчки.
    """
    code = _page_code()

    assert re.search(r"(?m)^const LIST_SETTLE_MS = 800;$", code)
    assert re.search(r"(?m)^let listMovedAt = -Infinity;$", code)
    assert _block(code, "function listMoved() {") == _squash("""
        function listMoved() {
          listMovedAt = performance.now();
        }
    """)
    assert _block(code, "function sameNames(a, b) {") == _squash("""
        function sameNames(a, b) {
          return a.length === b.length && a.every((name, i) => name === b[i]);
        }
    """)
    assert re.search(r"(?m)^const cardHeights = new WeakMap\(\);$", code)
    assert re.search(r"(?m)^const cardResizes = new ResizeObserver\(cardsResized\);$", code)
    assert _block(code, "function cardsResized(entries) {") == _squash("""
        function cardsResized(entries) {
          for (const entry of entries) {
            const height = entry.contentRect.height;
            const seen = cardHeights.get(entry.target);
            cardHeights.set(entry.target, height);
            if (seen !== undefined && seen !== height && entry.target.getBoundingClientRect().top < window.innerHeight) listMoved();
          }
        }
    """)
    assert _squash("""
        button.addEventListener("click", (event) => {
          if (event.detail !== 0 && performance.now() - listMovedAt < LIST_SETTLE_MS) return;
          printImage(image.name);
        });
    """) in _squash(code)
    assert "if (!sameNames(Array.from(cards.keys()), Array.from(next.keys()))) listMoved();" in code

    assert "cardResizes.observe(el);" in code
    assert "cardResizes.unobserve(card.el);" in code

    assert len(re.findall(r"\blistMovedAt\b", code)) == 3
    assert len(re.findall(r"\blistMoved\b", code)) == 3
    assert len(re.findall(r"\bLIST_SETTLE_MS\b", code)) == 2
    assert len(re.findall(r"\bsameNames\b", code)) == 2
    assert len(re.findall(r"\bcardResizes\b", code)) == 3
    assert len(re.findall(r"\bcardsResized\b", code)) == 2
    # Таблица высот: объявление, чтение и запись в cardsResized, перенос
    # высоты прежней карточки в showList (запись и чтение).
    assert len(re.findall(r"\bcardHeights\b", code)) == 5


def test_page_print_flow() -> None:
    """U4, U5: печать от нажатия до ответа — строка ошибки, кнопки и список на своих местах.

    `printImage` закреплена целиком, потому что каждая её строка видна
    оператору:
    - при нажатии прежняя строка ошибки печати снимается (U5 «до следующего
      нажатия»): иначе после удачного повтора под карточкой так и осталась
      бы красная строка прошлого отказа рядом с «Напечатано»;
    - при отказе красная строка не только собирается, но и вставляется в
      карточку (U4): 404, 409, 422 и отказ `fetch` не меняют ключ карточки,
      и без `append` оператор не увидел бы никакого итога — ни «принтер
      занят…» при второй вкладке, ни «…файл не читается» у файла, который
      ещё копируется, ни «Клиент не отвечает…» у остановленного клиента;
    - `setButtons()` после ответа (U4 «кнопки снова доступны в любом
      исходе»): без него кнопки карточек с прежним ключом остались бы
      недоступными, а после 409 или 422 печатаемая — с «Печатаю…»; печатать
      дальше можно было бы только после перезагрузки страницы;
    - `if (answered) loadImages();` (U4 «после любого ответа сервера список
      сразу перезапрашивается»): без него «напечатано: N» отставало бы от
      «Напечатано» до следующего тика, а при отказе `fetch` (ответа не было)
      лишний запрос не уходит;
    - фокус клавиатуры после ответа — снова на кнопке этой карточки (U4,
      финальная проверка, 2026-09-28; отложенный пункт задачи 1.5):
      `keepFocus` запоминается до первого `setButtons()`, потому что
      `disabled` снимает фокус с кнопки (он уходит на `body`) и сам не
      возвращается. Без возврата следующий пробел листал бы страницу на
      экран вниз, а не печатал снова (воспроизведено в WebKit и Chromium).
      Возврат — после второго `setButtons()`: на недоступную кнопку фокус не
      встаёт. Условие `document.activeElement === document.body` не
      отнимает фокус, если оператор за время печати перевёл его сам.
      `preventScroll` не дёргает прокрутку страницы. Пересоздаст карточку
      ответ списка — фокус переносит `showList`
      (test_page_list_failure_and_return).
    - пока открыт просмотр во весь экран (`data-open="true"`), фокус не
      возвращается (U3, U4; исправление по проверке задачи 3.4a): кнопка
      «Печать» лежит под затемнением, и пробел или Enter нажали бы её —
      та же картинка напечаталась бы ещё раз, лишний чек. В Finder пробел
      закрывает Quick Look, и оператор мака жмёт пробел, чтобы закрыть
      просмотр. Воспроизведено в WebKit (печать с клавиатуры) и в Chromium
      (обычный щелчок мышью по «Печать»): просмотр открыт во время печати,
      пробел после ответа — «напечатано» +2 вместо +1. С условием фокус
      остаётся на `body`, и пробел ничего не печатает.
    Ещё закреплены:
    - `makeFail` целиком: красная строка с текстом отказа, а не пустая
      полоса;
    - `printing` объявлен один раз и присваивается ровно в двух местах
      `printImage`: лишний сброс где-то ещё снял бы блок кнопок и списка
      посреди печати.
    """
    code = _page_code()

    assert _block(code, "async function printImage(name) {") == _squash("""
        async function printImage(name) {
          if (printing !== null) return;
          const card = cards.get(name);
          const keepFocus = document.activeElement === card.button;
          printing = name;
          if (card.fail !== null) {
            card.fail.remove();
            card.fail = null;
          }
          setButtons();

          let failure = null;
          let answered = false;
          try {
            const response = await fetch("/api/print/" + encodeURIComponent(name), {method: "POST"});
            answered = true;
            if (response.status !== 200) failure = await problemText(response);
          } catch (err) {
            failure = NO_ANSWER;
          }

          printing = null;
          if (failure === null) {
            toast(PRINTED);
          } else {
            card.fail = makeFail(failure);
            card.el.append(card.fail);
          }
          setButtons();
          if (keepFocus && document.activeElement === document.body && $("peek").dataset.open !== "true") card.button.focus({preventScroll: true});
          if (answered) loadImages();
        }
    """)
    assert _block(code, "function makeFail(text) {") == _squash("""
        function makeFail(text) {
          const fail = document.createElement("p");
          fail.className = "fail";
          fail.textContent = text;
          return fail;
        }
    """)
    assert re.search(r"(?m)^let printing = null;$", code)
    assert len(re.findall(r"\bprinting = ", code)) == 3
    # U4: фокус ставят ровно два места — возврат в printImage и перенос в
    # showList; оба без прокрутки страницы.
    assert len(re.findall(r"\.focus\(", code)) == 2
    assert code.count(".focus({preventScroll: true});") == 2


def test_page_list_failure_and_return() -> None:
    """U1, U6, U7: сбой списка прячет карточки, текст встаёт на их место, удачный ответ возвращает список.

    - разметка: список `list`, текст `note` (спрятан до первого ответа) и
      путь папки `folder` в шапке — те элементы, которые находит скрипт;
    - `[hidden]{display:none!important}` — целой строкой. Без `!important`
      правило `.list{display:flex}` той же специфичности, стоящее ниже,
      перебило бы `hidden`: при сбое прежние карточки остались бы на экране
      и нажимались, а текст сбоя ушёл бы под длинный список;
    - `showListFailure` прячет список атрибутом `hidden` и показывает
      красный текст сбоя;
    - `showNote` ставит класс, текст и `hidden` текста: без снятия `hidden`
      оператор не увидел бы ни текста сбоя, ни текста пустой папки;
    - `showList` целиком: путь папки в шапке, карточки по ключу (U5),
      удаление ушедших, новая карта `cards`, расстановка и снятие `hidden`
      со списка. Без новой карты нажатие «Печать» у новой картинки падало
      бы с TypeError (её карточки нет в карте), и страница больше не
      печатала бы и не обновлялась до перезагрузки. Без снятия `hidden`
      после одного сбоя (перезапуск сервера, медленный iCloud) страница
      навсегда осталась бы пустой и без текста;
    - в `showList` же — перенос фокуса (U4, финальная проверка,
      2026-09-28): если в фокусе была кнопка пересоздаваемой карточки, после
      расстановки фокус встаёт на кнопку её новой карточки. У новой
      карточки новая кнопка, а старая уходит из документа вместе с фокусом:
      после печати с клавиатуры (счётчик изменился — карточка пересоздана)
      следующий пробел листал бы страницу. Ушла картинка из папки или стала
      непригодной (кнопки нет) — фокус никуда не ставится;
    - в `showList` же — сдвиг списка (U4): если имена карточек не те же в
      том же порядке (опрос вставил, убрал или переставил карточки),
      отмечается `listMoved()` — до `cards = next`, пока в `cards` прежний
      порядок. Пересоздание карточки на её же месте (новые счётчики после
      печати) сдвигом не считается: иначе повторный щелчок по «Печать» той
      же картинки сразу после «Напечатано» пропадал бы. Поэтому же новая
      карточка получает в `cardHeights` высоту прежней, а ушедшая перестаёт
      наблюдаться (`unobserve`) — подробно в
      test_page_click_after_list_moved_is_ignored;
    - `$("list").hidden` пишется ровно в двух местах: сбой и удачный ответ.
    """
    code = _page_code()

    assert '<span class="folder" id="folder"></span>' in code
    assert '<ul class="list" id="list"></ul>' in code
    assert '<p class="empty" id="note" hidden></p>' in code
    assert re.search(r"(?m)^\[hidden\]\{display:none!important\}$", code)

    assert _block(code, "function showListFailure(text) {") == _squash("""
        function showListFailure(text) {
          $("list").hidden = true;
          showNote(text, true);
        }
    """)
    assert _block(code, "function showNote(text, failure) {") == _squash("""
        function showNote(text, failure) {
          const note = $("note");
          note.className = failure ? "fail" : "empty";
          note.textContent = text === null ? "" : text;
          note.hidden = text === null;
        }
    """)
    assert _block(code, "function showList(data) {") == _squash("""
        function showList(data) {
          $("folder").textContent = data.folder;

          const next = new Map();
          for (const image of data.images) {
            const key = `${image.ctime}|${image.error}|${image.printed}|${image.failed}`;
            const old = cards.get(image.name);
            if (old !== undefined && old.key === key) {
              next.set(image.name, old);
              continue;
            }
            const card = makeCard(image, key);
            if (old !== undefined) cardHeights.set(card.el, cardHeights.get(old.el));
            if (old !== undefined && old.fail !== null) {
              card.fail = old.fail;
              card.el.append(old.fail);
            }
            next.set(image.name, card);
          }
          let refocus = null;
          for (const [name, card] of cards) {
            if (next.get(name) === card) continue;
            if (card.button !== null && card.button === document.activeElement && next.has(name)) {
              refocus = next.get(name).button;
            }
            cardResizes.unobserve(card.el);
            card.el.remove();
          }
          if (!sameNames(Array.from(cards.keys()), Array.from(next.keys()))) listMoved();
          cards = next;
          place(Array.from(next.values(), (card) => card.el));
          if (refocus !== null) refocus.focus({preventScroll: true});

          $("list").hidden = false;
          showNote(data.images.length === 0 ? EMPTY(data.folder) : null, false);
        }
    """)
    assert code.count('$("list").hidden = ') == 2


def test_page_list_answer_guard() -> None:
    """U4, U5: ответ списка не показывается во время печати и не затирает более свежий.

    `loadImages` закреплена целиком (часть до `finally` закрепляет и
    test_page_timers), главное — условие между `finally` и показом:
    - `printing !== null` — ответ тика, который был в полёте в момент
      нажатия, не меняет список во время печати: иначе новая карточка
      появилась бы с доступной кнопкой «Печать» (U4 «все кнопки
      недоступны»), нажатие на неё молча пропало бы, а пересозданная
      печатаемая карточка потеряла бы строку ошибки печати;
    - `seq <= listShown` — ответ тика, ушедший до печати и вернувшийся
      после свежего списка (медленный диск), не возвращает на карточку
      старые счётчики: оператор видел бы «напечатано: 0» после
      «Напечатано», и миниатюра мигала бы (U5).
    Номера запросов: `listAsked` объявлен и растёт только в `loadImages`,
    `listShown` объявлен и пишется только при показе. Лишний сброс где-то
    ещё снова пустил бы старый ответ на экран.
    """
    code = _page_code()

    assert re.search(r"(?m)^let listAsked = 0;$", code)
    assert re.search(r"(?m)^let listShown = 0;$", code)
    assert _block(code, "async function loadImages() {") == _squash("""
        async function loadImages() {
          const seq = ++listAsked;
          listPending += 1;
          let data = null;
          let failure = null;
          try {
            const response = await fetch("/api/images");
            if (response.status === 200) {
              data = await response.json();
            } else {
              failure = await problemText(response);
            }
          } catch (err) {
            failure = NO_ANSWER;
          } finally {
            listPending -= 1;
          }
          if (printing !== null || seq <= listShown) return;
          listShown = seq;
          if (failure === null) {
            showList(data);
          } else {
            showListFailure(failure);
          }
        }
    """)
    assert len(re.findall(r"\blistAsked\b", code)) == 2
    assert len(re.findall(r"\blistShown\b", code)) == 3


def test_page_printer_indicator() -> None:
    """U1: индикатор принтера — по полю `connected` ответа 200; огонёк и текст согласны.

    - `loadPrinter` целиком: «принтер готов» только при `connected === true`
      в теле ответа 200. Сервер отвечает 200 `{"connected": false}` при
      вынутом кабеле, и проверка одного кода показала бы «принтер готов» без
      принтера — оператор жал бы «Печать» и получал бы отказ;
    - огонёк: `ready` при `connected`, `missing` иначе — из того же
      `connected`, что и текст; CSS красит `ready` белым, `missing` красным,
      и других состояний у `.led` нет. Переставленные значения дали бы
      красный огонёк рядом с «принтер готов» и белый рядом с «принтер не
      найден»;
    - разметка: огонёк `led` и текст `printerText` — те элементы, которые
      находит скрипт.
    """
    code = _page_code()

    assert (
        '<span class="printer"><span class="led" id="led"></span><span id="printerText"></span></span>'
        in code
    )
    assert _block(code, "async function loadPrinter() {") == _squash("""
        async function loadPrinter() {
          let connected = false;
          try {
            const response = await fetch("/api/printer");
            if (response.status === 200) connected = (await response.json()).connected === true;
          } catch (err) {
          }
          $("led").dataset.state = connected ? "ready" : "missing";
          $("printerText").textContent = connected ? PRINTER_READY : PRINTER_MISSING;
        }
    """)
    assert code.count("dataset.state") == 1

    assert re.findall(r"\.led\[[^\]]*\]", code) == ['.led[data-state="ready"]', '.led[data-state="missing"]']
    assert re.search(r'(?m)^\.led\[data-state="ready"\]\{background:var\(--paper\)\}$', code)
    assert re.search(r'(?m)^\.led\[data-state="missing"\]\{background:var\(--alert\)\}$', code)


def test_page_place_keeps_order() -> None:
    """U2, U5: `place` ставит каждую карточку на её место в порядке ответа, не трогая стоящие.

    Закреплена целиком. На первой загрузке список пуст, и любой способ
    вставки выглядит верно; ошибка видна только при обновлении. Вставка
    `appendChild` вместо `insertBefore(el, at)` уносила бы карточку в хвост,
    а цикл ниже удалял бы хвост: новый файл в папке так и не появился бы на
    странице, а карточка, пересозданная после печати, пропала бы с неё.
    """
    code = _page_code()

    assert _block(code, "function place(els) {") == _squash("""
        function place(els) {
          const list = $("list");
          let at = list.firstChild;
          for (const el of els) {
            if (el === at) {
              at = at.nextSibling;
            } else {
              list.insertBefore(el, at);
            }
          }
          while (at !== null) {
            const next = at.nextSibling;
            at.remove();
            at = next;
          }
        }
    """)


def test_page_toast() -> None:
    """U4: всплывающее «Напечатано» появляется с текстом, держится 2,2 с и уходит.

    До приёмки у заказчика (пункт 2: «внизу страницы на 2 с —
    «Напечатано»») это единственная проверка строки успеха, поэтому
    закреплены:
    - разметка: элемент с классом `toast` (его красит CSS) и id `toast` (его
      находит скрипт);
    - `toast` целиком: текст — через `textContent`, `data-show="true"`,
      прежний таймер снимается, новый через 2200 мс ставит `"false"`. Без
      текста всплыла бы пустая чёрная полоса; без `clearTimeout` таймер
      прошлой печати убрал бы «Напечатано» следующей раньше срока; без
      таймера или с другим сроком строка висела бы на экране или мелькала
      бы;
    - таймер объявлен на верхнем уровне скрипта, а `data-show` пишется
      только здесь; `toast` вызывается ровно один раз — с PRINTED (место
      вызова закрепляет test_page_texts_in_place);
    - CSS: сообщение прозрачно (`opacity:0`) и видно только при
      `data-show="true"`; других селекторов состояния у `.toast` нет.
      Селектор `.toast[data-show]` совпал бы и с "false": после первой
      печати «Напечатано» не ушло бы никогда, и оператор не отличил бы
      следующую удачную печать от прошлой;
    - CSS: `pointer-events:none` — прозрачная строка после первой печати
      остаётся поверх низа страницы (`position:fixed`, `z-index:50`), и без
      этого свойства клик по миниатюре или кнопке «Печать» под ней уходил бы
      в невидимую строку.
    """
    code = _page_code()

    assert '<div class="toast" id="toast" role="status"></div>' in code
    assert re.search(r"(?m)^let toastTimer = 0;$", code)
    assert _block(code, "function toast(text) {") == _squash("""
        function toast(text) {
          const el = $("toast");
          el.textContent = text;
          el.dataset.show = "true";
          clearTimeout(toastTimer);
          toastTimer = setTimeout(() => { el.dataset.show = "false"; }, 2200);
        }
    """)
    assert code.count("dataset.show") == 2
    assert len(re.findall(r"\btoast\(", code)) == 2

    assert re.search(r"(?m)^\.toast\{[^}]*\bopacity:0;", code)
    assert re.search(r"(?m)^\.toast\{[^}]*\bpointer-events:none;", code)
    assert re.findall(r"\.toast\[[^\]]*\]", code) == ['.toast[data-show="true"]']
    assert re.search(r'(?m)^\.toast\[data-show="true"\]\{opacity:1\}$', code)


def test_page_served_by_app(folder: Path) -> None:
    """§ 4.4, § 4.6: `GET /` настоящего приложения отдаёт эту страницу.

    Каталог страницы — `app/static` рабочего дерева, тот же, что запуск
    передаёт в `create_app` (M5): так проверяется, что отдаётся настоящий
    файл, а не заглушка из тестов API. Запрос идёт через `client_for` — на
    `127.0.0.1`, как из браузера оператора: `Host: testserver`, который
    `TestClient` шлёт по умолчанию, W9 отклоняет.

    Страница отдаётся для показа: тип — HTML, и в `Content-Disposition`
    нет `attachment` (его ставит `FileResponse(..., filename=...)`). Иначе
    браузер, который открывает запуск (M5), скачал бы `index.html` в
    «Загрузки», и заказчик не увидел бы клиента.
    """
    response = client_for(create_app(folder, FakePrinter(), APP / "static")).get("/")

    assert response.status_code == 200
    assert "Печать картинок" in response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment" not in response.headers.get("content-disposition", "")
