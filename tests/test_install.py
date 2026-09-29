"""Тесты установщика `install.sh` и ярлыка `launcher.command` (спецификация § 4.7, § 4.8).

Закрепляют правила пули 1:
- L1 — ярлык находит свою папку по своему положению: перенесённая папка
  работает, запуск по относительному пути — тоже, и при заданном
  `CDPATH`, и без него;
- L2 — клиента нет → «Клиент не установлен…», код 1; каждая половина
  проверки — отдельно: висячая ссылка на месте `venv/bin/python` при
  целом коде и окружение без кода;
- L3 — ярлык запускает `python -m photoprint` для своей папки через
  `exec`: `SIGTERM` ярлыку останавливает сам сервер (M7);
- I1 — ставится в `HOME/.photoprint`, текущий каталог не трогается; папка
  — из `.photoprint/folder`, если там существующий каталог, иначе
  `~/Desktop/Печать картинок`; путь с пробелом («Печать картинок») — одно
  слово: удалённая папка не воскрешается, и stderr пуст;
- I2 — Python старше 3.9 → «Нужен Python 3.9 или новее.», код 1, ничего
  не создано; `python3` раньше `/usr/bin` в `PATH` не вызывается;
  инструменты разработчика ищутся по исполняемому `python3` (статически);
- I3 — архив скачивается; короткий сбой сервера (503) повторяется
  (`--retry 3`); не скачался, оборван или в нём нет `app/photoprint` —
  сбой шага «скачивание»;
- I4 — код заменяется целиком, `app.old` не остаётся; сбой скачивания
  и сбой самой замены оставляют прежний код;
- I5 — рабочее окружение Python остаётся; сломанное (висячая ссылка или
  Python без pip) создаётся заново, и именно из `PHOTOPRINT_PYTHON`;
  проверка окружения ничего не пишет в stderr;
- I6 — строка шага «зависимости» и сама команда pip: её аргументы и
  каталог, из которого она вызвана (без сети — поддельный python
  окружения);
- I7 — ярлык — байты `app/launcher.command` с правами 755; картинки и
  счётчики в папке не трогаются; папку создать нельзя — причину печатает
  сам `mkdir` в stderr;
- I9 — «Готово. Папка для картинок: {F}…» последней строкой, код 0;
- I10 — каждый шаг начинается строкой «▸ …»; сбой каждого шага (§ 7.2
  «на каждом шаге») → «Установка не удалась на шаге «…»…» с его именем,
  для «папка и ярлык» — ещё подсказка, и так же в локали UTF-8
  Терминала заказчика; причину сбоя печатает сама команда в stderr, а
  удачная установка stderr не пишет; временный каталог `tmp.*` убран и
  при успехе, и при сбое; оборванный скрипт ничего не делает;
- I11 — повторная установка обновляет код и ярлык в перенесённой папке и
  не создаёт новую на Рабочем столе.

И правила пули 2 (задача 2.6):
- I5 — метка архитектуры `venv/.photoprint-arch`: после создания окружения
  в ней `uname -m`; чужая архитектура или нет метки → окружение создаётся
  заново, хотя `import pip` проходит (перенос с Intel-мака, Терминал под
  Rosetta); тесты окружения без pip и с висячей ссылкой пишут верную
  метку заранее — их пересоздание объясняется только проверкой pip;
  статически — метка пишется и сверяется через `uname -m`, литералов
  `arm64`/`x86_64` в установщике нет (на машине разработки литерал
  совпал бы с `uname -m`);
- I7 — ярлык при повторной установке — новый файл: расширенные атрибуты
  прежнего (у заказчика — метка карантина) не переходят на новый;
- I8 — шаг «библиотека USB»: `../venv/bin/python -m photoprint
  --check-printer` из `app`, его вывод доходит до заказчика — и stdout, и
  stderr упавшей на импорте проверки; код 1 → сбой шага, «Принтер не
  найден» установку не прерывает; с `PHOTOPRINT_SKIP_PIP=1` шага нет
  вовсе, а без самой переменной, как у заказчика, выполняются и
  «зависимости» — pip вызван командой I6 из Корня, — и проверка;
  `CDPATH` в Терминале заказчика не уводит проверку из `app` и не
  добавляет строк (без поддельного python — задача 3.2 проверит
  настоящий);
- I10, I3 — статически: шаги вызываются голой строкой, их столько же,
  сколько `STEP="…"`; адрес архива по умолчанию — дословно I3;
- I12 — оба скрипта: первая строка `#!/bin/bash`, `/bin/bash` 3.2 их
  разбирает, нет конструкций bash 4 — `grep -P` и ассоциативного массива
  и с соседними флагами (`grep -oP`, `local -A`, `declare -gA`), нет
  `$имя` прямо перед не-ASCII байтом; последняя строка установщика —
  вызов `main`;
- L2 — половина проверки «код клиента» — по `app/photoprint/__init__.py`,
  а не по каталогу `app`.

И выжившие мутанты ворот 2 (задача 2.7a):
- I1 — на месте записанной папки обычный файл (псевдоним Finder) → папка
  по умолчанию, stderr пуст;
- I2 — поддельный Python 3.8.9 не проходит проверку версии, 3.9.0 —
  проходит (граница `(3, 9)` целиком); статически — блок проверки
  инструментов дословно и сразу после `PY=…`: именно при `PY` =
  `/usr/bin/python3` и до первого вызова `"$PY"`; единственный вызов
  `xcode-select --install` — с `|| true`, за ним текст I2;
- I3 — архив с `app/`, но без пакета `app/photoprint` или без его
  `__init__.py` → сбой «скачивания», прежний код цел; три ответа 503
  подряд → архив с четвёртого запроса; 503 всегда → ровно четыре запроса
  и сбой «скачивания» с причиной `curl`;
- I10 — Ctrl+C посреди скачивания → текст сбоя ровно один раз, `tmp.*`
  убран.

И финальной проверки (задача 3.4a):
- I7 — ярлык с флагом «Защита» (`chflags uchg`) повторной установке не
  мешает: код 0, stderr пуст, ярлык — новый файл без флага.

Установщик выполняется так же, как команда из README (`curl … | bash`,
§ 7.2): `/bin/bash`, которому скрипт приходит через stdin (`run_install`).
`HOME` — каталог теста с пробелом и кириллицей в имени, текущий каталог —
другой пустой каталог, архив — `file://` на tar.gz, собранный из рабочего
дерева (`build_tarball`). Подменены только переменные из § 7.3: `HOME`,
`PHOTOPRINT_TARBALL`, `PHOTOPRINT_PYTHON`, `PHOTOPRINT_SKIP_PIP`, а в
тестах с запуском клиента — `PHOTOPRINT_PORT` и `PHOTOPRINT_OPEN=echo`
(браузер не открывается). `CDPATH`, `LANG`, `LC_ALL`, `PATH` с чужим
`python3` впереди и настоящий HTTP-сервер на `127.0.0.1` как адрес
`PHOTOPRINT_TARBALL` — не подмены кода, а условия, в которых заказчик
запускает ярлык и установщик в своём Терминале (§ 7.3); так же Ctrl+C —
`SIGINT` группе процессов установщика, запущенного «как из Терминала»
(`TERMINAL_BASH`). Окружение Python создаёт настоящий `/usr/bin/python3
-m venv`; сбои — настоящие: несуществующий архив, оборванный архив,
архив без пакета клиента, ответ 503 (и 503 всегда), файл на месте
каталога `Desktop` и на месте записанной папки, каталог кода и ярлык с
флагом «Защита» (`chflags uchg`), висячая ссылка на месте `python`, окружение
без pip, скрипты «Python», которые отказывают на проверке версии, на
создании окружения, на pip или на проверке принтера, настоящий
`/usr/bin/python3`, который называет себя другой версией, метка
окружения с чужой архитектурой. Атрибут,
которым тест I7 метит ярлык, — настоящий расширенный атрибут, но
безвредный (`com.example.photoprint-probe`), а не карантин: global.md
запрещает создавать файлы с меткой карантина. Настоящие `~/.photoprint` и
`~/Desktop` тесты не трогают.

Тексты для людей сверяются с дословными строками § 4.7–4.8 из этого
файла: расхождение текста в скрипте со спецификацией роняет тест.
"""
from __future__ import annotations

import contextlib
import gzip
import http.server
import io
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import threading
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from helpers import (
    APP,
    INSTALL_PATH,
    REPO,
    TARBALL_TOP,
    Proc,
    build_tarball,
    free_base,
    get_json,
    link_test_deps,
    make_jpeg,
    run_install,
)

# Строки шагов I10 дословно, в порядке исполнения (I3–I7). Строки шага
# «библиотека USB» (I8) здесь нет: тесты ставят клиент с
# `PHOTOPRINT_SKIP_PIP=1`, и этот шаг пропускается целиком (`USB_STEP_LINE`).
STEP_LINES = [
    "▸ скачивание…",
    "▸ замена кода…",
    "▸ окружение…",
    "▸ зависимости — это может занять несколько минут…",
    "▸ папка и ярлык…",
]
# Тексты § 4.7–4.8 дословно; `{F}`, `{STEP}` и `{url}` — подстановки.
DONE = (
    "Готово. Папка для картинок: {F}. Положите в неё JPEG и дважды щёлкните "
    "«Печать картинок». Если окно «Печать картинок» было открыто — закройте "
    "его и откройте снова."
)
FAILED = (
    "Установка не удалась на шаге «{STEP}». Причина — в сообщениях выше. "
    "Выполните команду установки ещё раз."
)
DESKTOP_HINT = (
    "Если выше «Operation not permitted» — разрешите Терминалу доступ к "
    "Рабочему столу: Системные настройки → Конфиденциальность и безопасность "
    "→ Файлы и папки → Терминал."
)
NO_APP_IN_ARCHIVE = "В скачанном архиве нет app/photoprint."
OLD_PYTHON = "Нужен Python 3.9 или новее."
NEED_TOOLS = (
    "Нужны инструменты разработчика Apple. В открывшемся окне нажмите "
    "«Установить» (не «Получить Xcode»), при запросе введите пароль от мака и "
    "дождитесь конца — 10–30 минут. Потом выполните команду установки ещё раз."
)
NOT_INSTALLED = "Клиент не установлен. Выполните команду установки из README."
# Строка M5 запущенного клиента: по ней сквозной тест знает, что сервер
# ответил своей папкой.
READY = "Готово. Страница открыта в браузере: {url}. Не закрывайте это окно, пока идёт работа."
# Имена папки и ярлыка у заказчика (§ 3.3, I1, I7).
FOLDER_NAME = "Печать картинок"
LAUNCHER_NAME = "Печать картинок.command"
# Адрес, по которому архива заведомо нет: `curl` не скачает его (I3).
MISSING_TARBALL = "file:///nonexistent/photoprint.tar.gz"
# Последняя строка установщика — вызов `main` (I10, § 4.8): без неё
# оборванный скрипт ничего не исполняет.
MAIN_CALL = b'main "$@"; exit'
# Сколько сквозной тест ждёт «Готово…» от ярлыка: первый запуск нового
# окружения ещё пишет байт-код клиента в `.photoprint/app`.
READY_TIMEOUT = 30.0
# Локаль Терминала заказчика: Терминал macOS по умолчанию ставит `LANG` с
# UTF-8 (у заказчика — русский). В ней bash 3.2 читает первый байт `»` как
# часть имени переменной, и `«$STEP»` без фигурных скобок под `set -u`
# уронил бы `on_exit` вместо текста I10. Без этих переменных тесты идут в
# локали C, где этой опасности нет.
CUSTOMER_LOCALE = {"LANG": "ru_RU.UTF-8", "LC_ALL": "ru_RU.UTF-8"}
# Команда I6 дословно, как её получает `venv/bin/python` (§ 4.8).
PIP_ARGS = [
    "-m",
    "pip",
    "install",
    "--disable-pip-version-check",
    "--quiet",
    "--only-binary=:all:",
    "-r",
    "app/requirements.txt",
]
# I8, I10: строка шага «библиотека USB» дословно. Шаг идёт после «папки и
# ярлыка» и только вместе с зависимостями: при `PHOTOPRINT_SKIP_PIP=1`
# проверять нечем, и строки нет.
USB_STEP_LINE = "▸ библиотека USB…"
# I10: имена шагов в порядке исполнения — значения `STEP="…"` (I3–I8).
STEP_NAMES = [
    "скачивание",
    "замена кода",
    "окружение",
    "зависимости",
    "папка и ярлык",
    "библиотека USB",
]
# M6: тексты `python -m photoprint --check-printer` дословно (§ 4.5). Их
# печатает сама проверка, а установщик не глушит её вывод (I8).
CHECK_NO_LIBUSB = "Не загрузилась библиотека USB — печать работать не будет."
CHECK_NOT_FOUND = (
    "Принтер не найден — проверьте кабель и питание. Клиент всё равно установлен."
)
# I8: аргументы проверки, как их получает python окружения клиента.
CHECK_ARGS = ["-m", "photoprint", "--check-printer"]
# I8, I10: так падает проверка, у которой не загрузился сам клиент, —
# например, колёса Pillow под другой архитектурой после переноса с
# Intel-мака или в Терминале под Rosetta (§ 4.8 I5). Текста M6 нет: stdout
# пуст, вся причина — трассировка Python в stderr. Заглушённый stderr
# проверки оставил бы текст I10 «Причина — в сообщениях выше» ни с чем.
CHECK_CRASH = (
    "Traceback (most recent call last):\n"
    '  File "photoprint/printer.py", line 1, in <module>\n'
    "ImportError: dlopen(PIL/_imaging.cpython-39-darwin.so, 0x0002): "
    "mach-o file, but is an incompatible architecture "
    "(have 'arm64', need 'x86_64')\n"
)
# I3: адрес архива по умолчанию дословно. Все прочие тесты подменяют его
# через `PHOTOPRINT_TARBALL`, поэтому он сверяется по тексту скрипта.
DEFAULT_TARBALL = (
    "${PHOTOPRINT_TARBALL:-"
    "https://codeload.github.com/culminationAI/photoprint/tar.gz/refs/heads/main}"
)
# Скрипты, которые у заказчика исполняет `/bin/bash` 3.2 (I12, § 4.7).
SCRIPTS = [REPO / "install.sh", APP / "launcher.command"]
# I12: конструкции bash 4 и чужих утилит — фикстура брифа 2.6 дословно. В
# bash 3.2 macOS их нет (или нет утилиты), и скрипт упал бы у заказчика,
# хотя на машине с Homebrew-bash работал бы. `timeout ` — с пробелом: слово
# «тайм-аут» в комментариях этим не задевается.
BASH4_FEATURES = [
    "declare -A",
    "mapfile",
    "readarray",
    ",,}",
    "^^}",
    "&>>",
    "|&",
    "[[ -v",
    "timeout ",
    "realpath",
    "grep -P",
    "${a[-1]}",
]
# I12: отрицательный индекс массива с любым именем (`${arr[-1]}`) —
# обобщение строки `${a[-1]}` фикстуры: в bash 3.2 это ошибка.
NEGATIVE_INDEX = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\[-")
# I12: `grep -P` с любыми соседними флагами (`grep -oP`, `grep -Po`,
# `grep -E -P`) — обобщение строки `grep -P` фикстуры: она ловит только
# буквальное написание. BSD `grep` мака (2.6.0-FreeBSD) отвечает на `-P`
# «invalid option» и кодом 2 (проверено).
GREP_PERL_FLAG = re.compile(r"\bgrep(\s+-[A-Za-z]+)*\s+-[A-Za-z]*P")
# I12: ассоциативный массив через `declare`, `local` или `typeset` с любыми
# соседними флагами (`local -A`, `declare -gA`, `typeset -A`) — обобщение
# строки `declare -A` фикстуры. `bash -n` такое пропускает, а bash 3.2
# отвечает «invalid option» только при исполнении (проверено).
ASSOC_ARRAY_FLAG = re.compile(r"\b(declare|local|typeset)\s+(-[A-Za-z]+\s+)*-[A-Za-z]*A")
# I12: `$имя` сразу перед байтом не-ASCII. В локали UTF-8 bash 3.2 берёт
# первый байт символа в имя переменной, и `$STEP»` под `set -u` падает с
# «unbound variable» (проверено в задаче 1.7).
BARE_VAR_BEFORE_NON_ASCII = re.compile(rb"\$[A-Za-z_][A-Za-z0-9_]*[\x80-\xff]")
# I5: метка архитектуры в тексте `install.sh` дословно — запись после
# создания окружения и сверка в проверке окружения: обе через `uname -m`
# этого запуска.
ARCH_MARK_WRITE = "uname -m > venv/.photoprint-arch"
ARCH_MARK_CHECK = '[ "$(cat venv/.photoprint-arch 2>/dev/null)" = "$(uname -m)" ]'
# I5: названия архитектур мака, которые `uname -m` возвращает сам; в
# `install.sh` их быть не должно — ни в коде, ни в комментариях.
ARCH_LITERAL = re.compile(r"arm64|x86_64")
# I10: вызов функции шага и её определение в тексте `install.sh`.
STEP_CALL = re.compile(r"\bstep_[A-Za-z0-9_]*")
STEP_DEF = re.compile(r"^\s*(step_[A-Za-z0-9_]+)\(\)\s*\{")
# I10: строка `STEP="<непусто>"` — начало шага.
STEP_ASSIGN = re.compile(r'STEP="([^"]+)"')
# I7: безвредный расширенный атрибут, которым тест метит прежний ярлык. Не
# карантин: global.md запрещает создавать файлы с меткой карантина, даже
# если их потом не запускают.
PROBE_XATTR = "com.example.photoprint-probe"
# I3: пакет клиента в архиве. Проверка I3 — по его `__init__.py`, а не по
# каталогу `app`: в архиве сломанной ветки `app/` может остаться без пакета.
ARCHIVE_PACKAGE = f"{TARBALL_TOP}/app/photoprint"
# I3: путь, по которому `curl` просит архив у сервера теста на `127.0.0.1`.
ARCHIVE_PATH = "/photoprint.tar.gz"
# I10, § 7.3: установщик «как из Терминала» — обёртка ставит SIGINT и SIGHUP
# на действие по умолчанию и делает `exec` в `/bin/bash`, как `TERMINAL_EXEC`
# в test_main.py делает для клиента (решение контролёра по задаче 2.5). Код
# установщика не меняется. Без неё pytest, запущенный фоном
# неинтерактивной оболочки (`pytest &`), передал бы установщику игнор
# SIGINT, а игнор, полученный при запуске, bash не снимает ни ловушкой, ни
# `trap -`: Ctrl+C до установщика не дошёл бы, и тест ждал бы тайм-аута.
TERMINAL_BASH = (
    "import os, signal\n"
    "signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
    "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
    "os.execv('/bin/bash', ['/bin/bash'])\n"
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Пустой `HOME` установщика: пробел и кириллица в пути, как у заказчика (§ 7.2)."""
    path = tmp_path / "дом пользователя"
    path.mkdir()
    return path


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    """Пустой текущий каталог установщика — не `HOME` и не `.photoprint` (§ 7.2, I1)."""
    path = tmp_path / "elsewhere"
    path.mkdir()
    return path


@pytest.fixture
def desktop_folder(home: Path) -> Path:
    """Путь папки по умолчанию `HOME/Desktop/Печать картинок` (I1); сама папка не создаётся."""
    return home / "Desktop" / FOLDER_NAME


@pytest.fixture
def started() -> Iterator[List[Proc]]:
    """Список запущенных ярлыков; каждый останавливается в финализаторе, даже если тест упал.

    Живой клиент держал бы порт и замок, а незавершённый `Popen` дал бы
    `ResourceWarning`.
    """
    procs: List[Proc] = []
    yield procs
    for proc in procs:
        proc.stop()


def _lines(result: subprocess.CompletedProcess) -> List[str]:
    """Строки stdout установщика или ярлыка без переводов строки."""
    return result.stdout.decode("utf-8").splitlines()


def _report(result: subprocess.CompletedProcess) -> str:
    """Код выхода и весь вывод процесса — текст для сообщения упавшего теста."""
    return (
        f"код выхода: {result.returncode}\n"
        f"--- stdout ---\n{result.stdout.decode('utf-8', 'replace')}"
        f"--- stderr ---\n{result.stderr.decode('utf-8', 'replace')}"
    )


def _tmp_dirs(home: Path) -> List[Path]:
    """Временные каталоги установщика `HOME/.photoprint/tmp.*`, оставшиеся после него (I10)."""
    return sorted((home / ".photoprint").glob("tmp.*"))


def _venv_python(home: Path, code: str) -> subprocess.CompletedProcess:
    """Выполнить `HOME/.photoprint/venv/bin/python -c code` и вернуть итог.

    Окружение — только `HOME` и системный `PATH`: окружение клиента должно
    работать само, без тестового.
    """
    return subprocess.run(
        [str(home / ".photoprint" / "venv" / "bin" / "python"), "-c", code],
        env={"HOME": str(home), "PATH": INSTALL_PATH},
        capture_output=True,
        timeout=60,
    )


def _machine() -> str:
    """Архитектура этого мака по `uname -m` — то, что установщик пишет в метку окружения (I5).

    Её спрашивают у той же `/usr/bin/uname`, что находит установщик в
    `INSTALL_PATH`, из процесса pytest — того же родителя, что у
    `run_install`: под Rosetta оба увидели бы `x86_64`, без неё — `arm64`.
    """
    result = subprocess.run(
        ["/usr/bin/uname", "-m"],
        env={"PATH": INSTALL_PATH},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, _report(result)
    return result.stdout.decode("utf-8").strip()


def _other_machine() -> str:
    """Чужая архитектура для метки I5: `x86_64` на маке с `arm64`, иначе `arm64` (бриф 2.6)."""
    return "x86_64" if _machine() == "arm64" else "arm64"


def _arch_mark_path(home: Path) -> Path:
    """Путь метки архитектуры окружения `HOME/.photoprint/venv/.photoprint-arch` (I5)."""
    return home / ".photoprint" / "venv" / ".photoprint-arch"


def _arch_mark(home: Path) -> Optional[str]:
    """Текст метки архитектуры окружения (I5) целиком; `None`, если файла нет.

    `None` вместо исключения: тест сравнивает метку с ожидаемой строкой, и
    её отсутствие должно быть понятным сбоем сравнения, а не ошибкой теста.
    """
    try:
        return _arch_mark_path(home).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _xattr(*args: str) -> subprocess.CompletedProcess:
    """Выполнить системный `/usr/bin/xattr` с аргументами `args` и вернуть итог (тест I7).

    В Python 3.9 на macOS нет `os.setxattr`, поэтому атрибуты ставит и
    читает та же утилита, что у заказчика.
    """
    return subprocess.run(
        ["/usr/bin/xattr", *args],
        env={"PATH": INSTALL_PATH},
        capture_output=True,
        timeout=30,
    )


def _url(port: int) -> str:
    """Адрес страницы клиента на `port` (M5)."""
    return f"http://127.0.0.1:{port}/"


def _fake_python(tmp_path: Path, body: str) -> Path:
    """Записать исполняемый `tmp_path/fakepy` для `PHOTOPRINT_PYTHON` и вернуть его путь.

    Скрипт проходит проверку версии I2 — это единственный вызов с `-c` —
    и дальше делает то, что написано в `body`: так тест доводит установку
    до нужного шага и роняет именно его (§ 7.2 «на каждом шаге»).
    `PHOTOPRINT_PYTHON` — подмена из § 7.3.
    """
    path = tmp_path / "fakepy"
    path.write_text(
        "#!/bin/sh\n"
        "# I2: проверка версии `-c '…'` проходит.\n"
        'case "$1" in -c) exit 0 ;; esac\n' + body,
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _fake_python_with_venv(tmp_path: Path, pip_code: int, record: Path) -> Path:
    """Поддельный `PHOTOPRINT_PYTHON`, чей `-m venv <каталог>` кладёт поддельный `<каталог>/bin/python`.

    Шаг «окружение» (I5) с ним проходит, а шаг «зависимости» (I6) вызывает
    уже поддельный python окружения. Тот на `-m pip …` пишет в `record`
    свой физический текущий каталог и все аргументы, по строке на каждый,
    и выходит с кодом `pip_code`; при отказе он, как настоящий pip, пишет
    причину в stderr. Так шаг «зависимости» проверяется без сети: ни
    настоящего pip, ни скачивания пакетов. На любой другой вызов — в том
    числе проверку принтера шага «библиотека USB» (I8) — он молча выходит
    с кодом 0, и установка доходит до «Готово…».
    """
    venv_python = tmp_path / "venv-python"
    venv_python.write_text(
        "#!/bin/sh\n"
        "# Поддельный python окружения клиента (тест I6).\n"
        'if [ "$1" = -m ] && [ "$2" = pip ]; then\n'
        f'  {{ pwd -P; printf "%s\\n" "$@"; }} > {shlex.quote(str(record))}\n'
        f'  [ {pip_code} -eq 0 ] || echo "pip: отказ поддельного pip" >&2\n'
        f"  exit {pip_code}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    venv_python.chmod(0o755)
    return _fake_python_making_venv(tmp_path, venv_python)


def _fake_python_making_venv(tmp_path: Path, venv_python: Path) -> Path:
    """Поддельный `PHOTOPRINT_PYTHON`, чей `-m venv <каталог>` кладёт копию `venv_python` в `<каталог>/bin/python`.

    Шаг «окружение» (I5) с ним проходит, а следующие шаги вызывают уже
    `venv_python`. Любой другой вызов, кроме проверки версии I2, — отказ с
    причиной в stderr: так видно, что шаги после I5 берут python
    окружения, а не `PHOTOPRINT_PYTHON`.
    """
    return _fake_python(
        tmp_path,
        'if [ "$1" = -m ] && [ "$2" = venv ]; then\n'
        '  mkdir -p "$3/bin" && '
        f'cp {shlex.quote(str(venv_python))} "$3/bin/python" && '
        'chmod 755 "$3/bin/python"\n'
        "  exit $?\n"
        "fi\n"
        'echo "fakepy: неожиданный вызов: $*" >&2\n'
        "exit 1\n",
    )


def _fake_python_with_check(
    tmp_path: Path, check_code: int, check_output: str, record: Path
) -> Path:
    """Поддельный `PHOTOPRINT_PYTHON` для шага «библиотека USB» (I8) без настоящего `--check-printer`.

    Его окружение получает поддельный python: `-m pip …` удачен и молчит
    (шаг «зависимости» проходит без сети), а на `-m photoprint …` он пишет
    в `record` свой физический текущий каталог и все аргументы, по строке
    на каждый, печатает `check_output` в stdout — как M6 печатает свой
    текст — и выходит с кодом `check_code`. Дополнение контролёра к
    задаче 2.6 разрешает такой тест: настоящая проверка принтера с
    настоящим pip — задача 3.2.
    """
    venv_python = tmp_path / "venv-python"
    venv_python.write_text(
        "#!/bin/sh\n"
        "# Поддельный python окружения клиента (тест I8).\n"
        'if [ "$1" = -m ] && [ "$2" = photoprint ]; then\n'
        f'  {{ pwd -P; printf "%s\\n" "$@"; }} > {shlex.quote(str(record))}\n'
        f"  printf '%s\\n' {shlex.quote(check_output)}\n"
        f"  exit {check_code}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    venv_python.chmod(0o755)
    return _fake_python_making_venv(tmp_path, venv_python)


def _fake_python_with_crashing_check(tmp_path: Path, record: Path) -> Path:
    """Поддельный `PHOTOPRINT_PYTHON`, чья проверка принтера (I8) падает, как Python на `ImportError`.

    Как `_fake_python_with_check`, но на `-m photoprint …` python окружения
    ничего не печатает в stdout, пишет `CHECK_CRASH` в stderr и выходит с
    кодом 1 — так выходит Python, которому не загрузился модуль клиента.
    В `record` он пишет свой физический текущий каталог и все аргументы,
    по строке на каждый: так видно, что трассировку дала именно проверка.
    Отдельный помощник, а не параметр `_fake_python_with_check`: та
    проверка всегда печатает строку M6 в stdout, а здесь stdout проверки
    пуст, и единственная причина сбоя — её stderr.
    """
    venv_python = tmp_path / "venv-python"
    venv_python.write_text(
        "#!/bin/sh\n"
        "# Поддельный python окружения клиента: проверка падает (тест I8).\n"
        'if [ "$1" = -m ] && [ "$2" = photoprint ]; then\n'
        f'  {{ pwd -P; printf "%s\\n" "$@"; }} > {shlex.quote(str(record))}\n'
        f"  printf '%s' {shlex.quote(CHECK_CRASH)} >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    venv_python.chmod(0o755)
    return _fake_python_making_venv(tmp_path, venv_python)


def _fake_python_with_pip_and_check(
    tmp_path: Path, pip_record: Path, check_record: Path
) -> Path:
    """Поддельный `PHOTOPRINT_PYTHON`, чей python окружения записывает и вызов pip (I6), и вызов проверки принтера (I8).

    Python окружения на `-m pip …` дописывает в `pip_record`, а на
    `-m photoprint …` — в `check_record` свой физический текущий каталог и
    все аргументы, по строке на каждый. Проверка ещё печатает
    `CHECK_NOT_FOUND` в stdout, как M6. Оба вызова удачны, поэтому ни сети,
    ни принтера не нужно, а установка доходит до «Готово…».

    Отличия от `_fake_python_with_check`, с которым шаг «зависимости» не
    оставлял следа:
    - запись pip — отдельный файл: по нему видно, что pip вообще вызван;
    - записи дописываются (`>>`), а не перезаписываются: второй вызов pip
      или проверки дал бы лишние строки, и сравнение записи упало бы;
    - любой другой вызов — отказ с причиной в stderr: у пустого `HOME`
      окружения до шага I5 нет, и других вызовов python окружения быть не
      должно.
    """
    venv_python = tmp_path / "venv-python"
    venv_python.write_text(
        "#!/bin/sh\n"
        "# Поддельный python окружения клиента (тест I6 и I8 без PHOTOPRINT_SKIP_PIP).\n"
        'if [ "$1" = -m ] && [ "$2" = pip ]; then\n'
        f'  {{ pwd -P; printf "%s\\n" "$@"; }} >> {shlex.quote(str(pip_record))}\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = -m ] && [ "$2" = photoprint ]; then\n'
        f'  {{ pwd -P; printf "%s\\n" "$@"; }} >> {shlex.quote(str(check_record))}\n'
        f"  printf '%s\\n' {shlex.quote(CHECK_NOT_FOUND)}\n"
        "  exit 0\n"
        "fi\n"
        'echo "venv-python: неожиданный вызов: $*" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    venv_python.chmod(0o755)
    return _fake_python_making_venv(tmp_path, venv_python)


def _run_install_without_skip_pip(
    home: Path, tarball: Path, *, cwd: Path, env_extra: Dict[str, str]
) -> subprocess.CompletedProcess:
    """Выполнить установщик, как `run_install`, но без переменной `PHOTOPRINT_SKIP_PIP` в окружении.

    У заказчика этой переменной нет вовсе. `run_install` же задаёт её
    всегда (`"1"`), и `env_extra` может только заменить значение: тест с
    пустой строкой не отличит `${PHOTOPRINT_SKIP_PIP:-}` от
    `$PHOTOPRINT_SKIP_PIP`. А без `:-` bash 3.2 под `set -u` обрывает
    установку на незаданной переменной — и выходит с кодом 0, без «Готово…»
    и без текста сбоя I10: `on_exit` видит `$?` = 0.

    Остальное — как у `run_install` (helpers.py задача 2.6 не меняет):
    `/bin/bash` получает `install.sh` рабочего дерева через stdin в
    каталоге `cwd`; окружение — `HOME`, системный `PATH`,
    `PHOTOPRINT_TARBALL` на архив теста, `TMPDIR` рядом с `cwd`, затем
    `env_extra`; предел — 120 с.
    """
    tmpdir = cwd.parent / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": INSTALL_PATH,
        "PHOTOPRINT_TARBALL": Path(tarball).as_uri(),
        "TMPDIR": str(tmpdir),
        **env_extra,
    }
    # Весь смысл помощника — переменной нет; `env_extra` не должен вернуть её.
    assert "PHOTOPRINT_SKIP_PIP" not in env, env_extra
    return subprocess.run(
        ["/bin/bash"],
        input=(REPO / "install.sh").read_bytes(),
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=120,
    )


def _run_launcher(launcher: Path, home: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Выполнить `/bin/bash <launcher>` в каталоге `cwd`, как Терминал по двойному щелчку, и вернуть итог.

    Окружение — `HOME` и системный `PATH`. `PHOTOPRINT_OPEN=echo` — на
    случай, если ярлык по ошибке дойдёт до запуска клиента: браузер не
    должен открыться на экране владельца.
    """
    return subprocess.run(
        ["/bin/bash", str(launcher)],
        cwd=cwd,
        env={"HOME": str(home), "PATH": INSTALL_PATH, "PHOTOPRINT_OPEN": "echo"},
        capture_output=True,
        timeout=30,
    )


def _assert_server_stopped(proc: Proc, port: int) -> None:
    """L3, M7: после `proc.stop()` на `127.0.0.1:port` никто не слушает — сервер остановлен.

    `Proc.stop` шлёт `SIGTERM` процессу, который запустил тест, — ярлыку.
    С `exec` (L3) на месте bash уже Python, и сигнал останавливает сам
    сервер. Без `exec` сигнал получил бы только bash, а сервер остался бы
    сиротой и держал порт. Тогда функция убивает этого сироту — процесс,
    который слушает этот порт и состоит в группе процессов ярлыка (у
    `Proc` своя сессия), то есть ровно запущенный тестом клиент, чтобы на
    машине не остался живой сервер, — и роняет тест.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    except ConnectionRefusedError:
        return
    listeners = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.split()
    for pid in listeners:
        try:
            # Только процесс из группы ярлыка: чужой процесс на этом порту
            # тест не трогает.
            if os.getpgid(int(pid)) == proc.popen.pid:
                os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Процесс уже вышел сам или он чужой — убирать нечего.
            pass
    raise AssertionError(
        f"после SIGTERM ярлыку порт {port} всё ещё слушают (процессы {listeners}): "
        f"сервер пережил ярлык, `exec` не сработал (L3)\n{proc.stdout}{proc.stderr}"
    )


def _assert_customer_locale_active() -> None:
    """Предпосылка тестов локали: в `CUSTOMER_LOCALE` bash 3.2 действительно роняет `«$STEP»`.

    Не будь на машине локали `ru_RU.UTF-8`, bash молча остался бы в C, и
    тест локали прошёл бы, ничего не проверив. Здесь видно, что опасность
    настоящая: без фигурных скобок `$STEP»` — другое, незаданное имя, и
    `set -u` роняет bash с «unbound variable».
    """
    probe = subprocess.run(
        ["/bin/bash", "-c", 'set -u; STEP=x; echo "«$STEP»"'],
        env={"PATH": INSTALL_PATH, **CUSTOMER_LOCALE},
        capture_output=True,
        timeout=30,
    )
    assert probe.returncode != 0, _report(probe)
    assert b"unbound variable" in probe.stderr, _report(probe)


def _tarball_without(tarball: Path, dest: Path, drop: Callable[[str], bool]) -> Path:
    """Собрать `dest/photoprint.tar.gz` — копию архива `tarball` без записей, чьё имя `drop` отбрасывает (тест I3).

    Прочие записи — те же заголовки и байты в том же порядке: архив
    отличается от собранного `build_tarball` только тем, чего в нём нет.
    Сжатие — gzip, как у архива ветки на GitHub. Каталог `dest` создаётся.
    """
    dest.mkdir(parents=True)
    result = dest / "photoprint.tar.gz"
    with tarfile.open(tarball) as src, tarfile.open(result, "w:gz") as dst:
        for member in src.getmembers():
            if drop(member.name):
                continue
            dst.addfile(member, src.extractfile(member) if member.isfile() else None)
    return result


@contextlib.contextmanager
def _flaky_tarball_server(
    body: bytes, failures: Optional[int]
) -> Iterator[Tuple[str, List[str]]]:
    """Настоящий сервер архива на `127.0.0.1` и свободном порту (§ 7.3) с коротким сбоем: первые `failures` GET — 503 (I3).

    Остальные запросы получают архив `body`; `failures=None` — 503 на
    каждый запрос, архива сервер не отдаёт вовсе. Блок `with` получает
    адрес архива для `PHOTOPRINT_TARBALL` и список путей всех принятых
    запросов по порядку: по нему тест считает повторы `curl`. Сервер
    останавливается на выходе из блока, даже если тест упал.
    """
    requests: List[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        """Сервер архива: 503 на первые `failures` GET (или на все), затем архив."""

        def do_GET(self) -> None:  # noqa: N802
            """Записать запрос и ответить 503 или архивом."""
            requests.append(self.path)
            if failures is None or len(requests) <= failures:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Не писать журнал запросов в stderr pytest."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}{ARCHIVE_PATH}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _fake_python_version(tmp_path: Path, version: Tuple[int, int, int]) -> Path:
    """Поддельный `PHOTOPRINT_PYTHON` версии `version` (тест I2): настоящий `/usr/bin/python3`, который называет другую версию.

    На `-c <код>` он выполняет код настоящим `/usr/bin/python3`, но
    `sys.version_info` в нём — `version` (выпуск «final»), и проверка
    версии I2 видит Python этой версии, а не 3.9.6 машины разработки.
    Любой другой вызов — `-m venv venv` шага «окружение» — тот же
    настоящий `/usr/bin/python3`: пропущенный проверкой Python строит
    настоящее окружение, и установка идёт до конца. `PHOTOPRINT_PYTHON` —
    подмена из § 7.3; § 7.2 называет для ветки версии исполняемый скрипт
    с кодом 1 (`test_install_old_python`), этот — такой же исполняемый
    скрипт, только он отвечает по `sys.version_info`, а не отказывает на
    любом вызове.
    """
    dotted = ".".join(str(part) for part in version)
    path = tmp_path / f"python-{dotted}"
    path.write_text(
        "#!/bin/sh\n"
        f"# Поддельный Python {dotted} (тест I2): проверка `-c` видит версию {dotted}.\n"
        'if [ "$1" = -c ]; then\n'
        "  exec /usr/bin/python3 -c '\n"
        "import collections, sys\n"
        'sys.version_info = collections.namedtuple("version_info", '
        '"major minor micro releaselevel serial")'
        f'({version[0]}, {version[1]}, {version[2]}, "final", 0)\n'
        "exec(sys.argv[1])\n"
        "' \"$2\"\n"
        "fi\n"
        "# Всё прочее — настоящий python3.\n"
        'exec /usr/bin/python3 "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _function_body(name: str) -> List[str]:
    """Строки кода функции `name` из `install.sh` — без отступов, пустых строк и комментариев (статические тесты I2).

    Тело — от строки `name() {` до первой строки ровно `}`: функции в
    `install.sh` закрываются `}` в начале строки, а фигурные скобки внутри
    тел — только в одну строку (`{ …; }`).
    """
    lines = (REPO / "install.sh").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}() {{")
    end = lines.index("}", start)
    return [
        line.strip()
        for line in lines[start + 1 : end]
        if line.strip() and not line.strip().startswith("#")
    ]


def test_install_fresh_home(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I1, I3–I7, I9, I10: установка в пустой `HOME` — код, окружение, папка с ярлыком, «Готово».

    - код клиента лежит в `HOME/.photoprint/app` (I3, I4);
    - `HOME/.photoprint/venv/bin/python` запускается (I5);
    - ярлык в `~/Desktop/Печать картинок` — ровно байты
      `app/launcher.command` с правами 755 (I7: `cat` и `chmod 755`);
    - пять строк шагов «▸ …» — каждая по одному разу и по порядку (I10);
    - последняя строка — «Готово…» с путём папки (I9);
    - временного каталога `tmp.*` не осталось (I10, `on_exit`);
    - stderr пуст (I5, I10): при первой установке `venv/bin/python` ещё
      нет, и проверка окружения `venv/bin/python -c 'import pip'` не
      находит его — это не сбой, а повод создать окружение. Её stderr
      глушится; иначе заказчик увидел бы «No such file or directory»
      посреди удачной установки, хотя причину печатают только упавшие
      команды.
    """
    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert result.stderr == b"", _report(result)
    assert (home / ".photoprint" / "app" / "photoprint" / "__init__.py").is_file()
    python = _venv_python(home, "import sys")
    assert python.returncode == 0, _report(python)
    launcher = desktop_folder / LAUNCHER_NAME
    assert launcher.read_bytes() == (APP / "launcher.command").read_bytes()
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755
    lines = _lines(result)
    # Из вывода берутся только строки шагов: каждая должна встретиться ровно
    # один раз и в порядке исполнения. Прочие строки (в задаче 2.6 — шаг
    # «библиотека USB») эту проверку не задевают.
    assert [line for line in lines if line in STEP_LINES] == STEP_LINES, _report(result)
    assert lines[-1] == DONE.format(F=desktop_folder), _report(result)
    assert _tmp_dirs(home) == []


def test_install_ignores_current_directory(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I1: установщик работает в `HOME/.photoprint` и ничего не пишет в текущий каталог.

    Команду из README заказчик вводит в Терминале из любого каталога;
    скачанный архив, распакованный код или окружение в нём были бы мусором
    у заказчика и признаком того, что относительные пути I3–I8 считаются
    не от `$R`.

    Код 0 сам по себе не доказывает, что установка дошла до конца: bash 3.2,
    остановившись на «unbound variable», выходит с кодом 0 (комментарий у
    `set -euo pipefail` в `install.sh`). Поэтому ещё — «Готово…» последней
    строкой и пустой stderr: иначе пустой текущий каталог означал бы лишь,
    что установка молча оборвалась до I3.
    """
    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert os.listdir(cwd) == []
    assert _lines(result)[-1] == DONE.format(F=desktop_folder), _report(result)
    assert result.stderr == b"", _report(result)


def test_install_keeps_images_and_stats(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I7, I11: картинки и `.print-stats.json` в папке не меняются, добавляется только ярлык.

    Папка уже есть — так выглядит обновление у заказчика. После установки
    байты картинки и счётчиков прежние, и в папке нет ничего, кроме них и
    ярлыка.
    """
    desktop_folder.mkdir(parents=True)
    image = make_jpeg(desktop_folder / "a.jpg", 576, 300)
    stats = desktop_folder / ".print-stats.json"
    stats.write_bytes(
        json.dumps({"a.jpg": {"printed": 5, "failed": 0}}).encode("utf-8")
    )
    image_bytes = image.read_bytes()
    stats_bytes = stats.read_bytes()

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert image.read_bytes() == image_bytes
    assert stats.read_bytes() == stats_bytes
    assert sorted(os.listdir(desktop_folder)) == sorted(
        ["a.jpg", ".print-stats.json", LAUNCHER_NAME]
    )


def test_reinstall_replaces_code_and_reuses_venv(
    tmp_path: Path, home: Path, cwd: Path
) -> None:
    """I4, I5, I11: повторная установка заменяет `app` целиком и оставляет рабочее окружение.

    - `app/stale.txt` — файл, которого нет в новом архиве: после замены
      его нет (I4: `app` заменяется, а не дописывается);
    - `venv/marker` остался: рабочее окружение не пересоздаётся (I5);
    - `app.old` после замены убран (I4 п. 4);
    - первая установка записала метку архитектуры — ровно вывод `uname -m`
      с переводом строки (I5: `uname -m > venv/.photoprint-arch`); с ней
      окружение и признано рабочим при повторной установке.
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    assert _arch_mark(home) == _machine() + "\n"
    root = home / ".photoprint"
    (root / "app" / "stale.txt").write_text("старый код", encoding="utf-8")
    (root / "venv" / "marker").write_text("окружение первой установки", encoding="utf-8")

    second = run_install(home, tarball, cwd=cwd)

    assert second.returncode == 0, _report(second)
    assert not (root / "app" / "stale.txt").exists()
    assert (root / "venv" / "marker").exists()
    assert (root / "app" / "photoprint" / "__init__.py").is_file()
    assert not (root / "app.old").exists()


def test_install_recreates_broken_venv(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I5: `venv/bin/python` — висячая ссылка → окружение создаётся заново, и в нём есть pip.

    Так выглядит окружение после переустановки Command Line Tools: ссылка
    указывает на Python, которого больше нет. Установщик должен заметить,
    что окружение не работает, и создать его заново. stderr пуст (I5,
    I10): отказ висячей ссылки в проверке окружения — повод пересоздать
    его, а не сбой, и строка bash «No such file or directory» заказчику
    не показывается.

    Метка архитектуры (I5) — верная, `uname -m` этого мака: окружение
    пересоздаётся только потому, что его python не запускается. Без метки
    тест не отличил бы проверку pip от проверки метки, и реализация «при
    верной метке окружение не пересоздаётся никогда» прошла бы его.
    """
    bin_dir = home / ".photoprint" / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    _arch_mark_path(home).write_text(_machine() + "\n", encoding="utf-8")
    (bin_dir / "python").symlink_to(tmp_path / "удалённый python3")
    # Предпосылка: ссылка есть, а того, на что она указывает, нет.
    assert os.path.lexists(bin_dir / "python")
    assert not (bin_dir / "python").exists()

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert result.stderr == b"", _report(result)
    pip = _venv_python(home, "import pip")
    assert pip.returncode == 0, _report(pip)


def test_install_recreates_venv_without_pip(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I5: `venv/bin/python` запускается, но pip в окружении нет → окружение создаётся заново.

    Второй случай I5 (первый — висячая ссылка, тест выше): после
    переустановки Command Line Tools с другой версией Python прежнее
    окружение запускается, но не находит своих пакетов. Проверка здоровья
    окружения — именно `import pip`: проверка «файл исполняемый» или
    «python запускается» оставила бы такое окружение, и шаг «зависимости»
    падал бы у заказчика при каждой установке. `marker` пропал — окружение
    действительно создано заново, а не дополнено. stderr пуст (I5, I10):
    трассировка `ModuleNotFoundError` из проверки окружения — повод его
    пересоздать, а не сбой, и в Терминал заказчика она не попадает.

    Метка архитектуры (I5) — верная, `uname -m` этого мака: окружение
    пересоздаётся только из-за проверки pip. Без метки его пересоздание
    объяснялось бы её отсутствием, и реализация «при верной метке
    окружение не пересоздаётся никогда» прошла бы тест.
    """
    venv = home / ".photoprint" / "venv"
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(exist_ok=True)
    # Настоящее окружение без pip от того же `/usr/bin/python3`, что берёт
    # установщик по умолчанию (I2).
    made = subprocess.run(
        ["/usr/bin/python3", "-m", "venv", "--without-pip", str(venv)],
        env={"HOME": str(home), "PATH": INSTALL_PATH, "TMPDIR": str(tmpdir)},
        capture_output=True,
        timeout=120,
    )
    assert made.returncode == 0, _report(made)
    # Предпосылка: python окружения запускается, а pip в нём нет.
    runs = _venv_python(home, "import sys")
    assert runs.returncode == 0, _report(runs)
    assert _venv_python(home, "import pip").returncode != 0
    marker = venv / "marker"
    marker.write_text("окружение без pip", encoding="utf-8")
    _arch_mark_path(home).write_text(_machine() + "\n", encoding="utf-8")

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert result.stderr == b"", _report(result)
    pip = _venv_python(home, "import pip")
    assert pip.returncode == 0, _report(pip)
    assert not marker.exists()


def test_install_recreates_venv_for_other_arch(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I5: метка окружения называет другую архитектуру → окружение создаётся заново с меткой `uname -m`.

    Так выглядит окружение, перенесённое с Intel-мака Ассистентом миграции
    или созданное в Терминале под Rosetta: `import pip` в нём проходит,
    pip считает колёса установленными, а Pillow и pydantic-core под этой
    архитектурой не загружаются — клиент падает с `ImportError`, и без
    метки повторная установка его не чинила бы (ломатель задачи 1.7).
    Здесь окружение настоящее и рабочее (предпосылка: `import pip`
    проходит), чужая только метка. После повторной установки `marker` нет
    — окружение создано заново, а не оставлено; в метке — архитектура
    этого запуска; pip в новом окружении есть; stderr пуст (I5, I10:
    проверка окружения ничего не пишет).
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    marker = home / ".photoprint" / "venv" / "marker"
    marker.write_text("окружение другой архитектуры", encoding="utf-8")
    _arch_mark_path(home).write_text(_other_machine() + "\n", encoding="utf-8")
    # Предпосылка: само окружение рабочее — пересоздать его может только
    # чужая метка.
    runs = _venv_python(home, "import pip")
    assert runs.returncode == 0, _report(runs)

    second = run_install(home, tarball, cwd=cwd)

    assert second.returncode == 0, _report(second)
    assert second.stderr == b"", _report(second)
    assert not marker.exists()
    assert _arch_mark(home) == _machine() + "\n"
    pip = _venv_python(home, "import pip")
    assert pip.returncode == 0, _report(pip)


def test_install_recreates_venv_without_arch_mark(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I5: метки архитектуры нет → окружение создаётся заново, и метка появляется.

    Так выглядит окружение, созданное установщиком пули 1 до появления
    метки, или окружение, у которого метку удалили: чья у него
    архитектура, неизвестно, и установщик не должен считать его рабочим
    только потому, что `import pip` проходит. Предпосылка: первая
    установка метку поставила — удаляется именно она. После повторной
    установки `marker` нет, метка — `uname -m` этого запуска, stderr пуст:
    `cat` несуществующей метки в Терминал заказчика не пишет (I5, I10).
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    marker = home / ".photoprint" / "venv" / "marker"
    marker.write_text("окружение без метки", encoding="utf-8")
    # Предпосылка: метка есть, и удаляется именно она.
    assert _arch_mark(home) == _machine() + "\n"
    _arch_mark_path(home).unlink()

    second = run_install(home, tarball, cwd=cwd)

    assert second.returncode == 0, _report(second)
    assert second.stderr == b"", _report(second)
    assert not marker.exists()
    assert _arch_mark(home) == _machine() + "\n"


def test_reinstall_launcher_is_new_file(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I7, I11: повторная установка кладёт ярлык новым файлом — расширенные атрибуты прежнего не остаются.

    `>` в существующий файл сохраняет его расширенные атрибуты. У
    заказчика это метка карантина: папка пришла через AirDrop, мессенджер
    или архив из браузера, и Gatekeeper не даёт открыть ярлык двойным
    щелчком. Без `rm -f` перед `cat` переустановка оставила бы ярлык
    заблокированным. Здесь вместо карантина — безвредный настоящий атрибут
    `com.example.photoprint-probe` (global.md: файлов с меткой карантина
    тесты не создают; ярлык в тесте и не запускается). После повторной
    установки атрибута нет, а ярлык — байты `app/launcher.command` с
    правами 755.
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    launcher = desktop_folder / LAUNCHER_NAME
    marked = _xattr("-w", PROBE_XATTR, "1", str(launcher))
    assert marked.returncode == 0, _report(marked)
    # Предпосылка: атрибут действительно стоит на прежнем ярлыке.
    probe = _xattr("-p", PROBE_XATTR, str(launcher))
    assert probe.returncode == 0, _report(probe)

    second = run_install(home, tarball, cwd=cwd)

    assert second.returncode == 0, _report(second)
    probe = _xattr("-p", PROBE_XATTR, str(launcher))
    assert probe.returncode != 0, _report(probe)
    assert launcher.read_bytes() == (APP / "launcher.command").read_bytes()
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755


def test_reinstall_over_locked_launcher(
    tmp_path: Path,
    home: Path,
    cwd: Path,
    desktop_folder: Path,
    chflags_to: Callable[[Path, int], None],
) -> None:
    """I7, I11: ярлык с флагом «Защита» (`chflags uchg`) не мешает повторной установке.

    Заказчик мог поставить галочку «Защита» в свойствах ярлыка в Finder —
    это флаг `uchg` (§ 7.3: настоящий флаг, снимает его финализатор
    `chflags_to`). С ним `rm -f` ярлыка отказывает с «Operation not
    permitted», установка падала на шаге «папка и ярлык», а подсказка I10
    отправляла заказчика давать Терминалу доступ к Рабочему столу, который
    у Терминала уже есть: повтор команды падал так же. Установщик снимает
    флаг перед `rm -f` (финальная проверка, 2026-09-28). После повторной
    установки: код 0, stderr пуст, «Готово…» последней строкой, ярлык —
    новый файл без флага, байты `app/launcher.command` с правами 755.
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    launcher = desktop_folder / LAUNCHER_NAME
    chflags_to(launcher, stat.UF_IMMUTABLE)
    # Предпосылка: флаг действительно стоит на прежнем ярлыке.
    assert launcher.stat().st_flags & stat.UF_IMMUTABLE

    second = run_install(home, tarball, cwd=cwd)

    assert second.returncode == 0, _report(second)
    assert second.stderr == b"", _report(second)
    assert _lines(second)[-1] == DONE.format(F=desktop_folder), _report(second)
    assert launcher.stat().st_flags & stat.UF_IMMUTABLE == 0
    assert launcher.read_bytes() == (APP / "launcher.command").read_bytes()
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755


def test_install_uses_recorded_folder(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I1, I7, I9, I11: `.photoprint/folder` называет существующий каталог → ярлык ставится туда.

    Это папка, которую заказчик перенёс (её путь записал клиент, M4). Новой
    папки на Рабочем столе не появляется, и «Готово…» называет перенесённую
    папку.
    """
    moved = tmp_path / "Перенесённая"
    moved.mkdir()
    (home / ".photoprint").mkdir()
    # M4: путь записывается без перевода строки.
    (home / ".photoprint" / "folder").write_text(str(moved), encoding="utf-8")

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert (moved / LAUNCHER_NAME).is_file()
    assert not desktop_folder.exists()
    assert _lines(result)[-1] == DONE.format(F=moved), _report(result)


def test_install_ignores_missing_recorded_folder(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I1: `.photoprint/folder` называет несуществующий каталог → ярлык в `~/Desktop/Печать картинок`.

    Заказчик мог удалить папку. Записанный путь не воскрешается: каталог
    по нему не создаётся, иначе папка появилась бы там, где её уже никто
    не ищет.
    """
    missing = tmp_path / "Удалённая"
    (home / ".photoprint").mkdir()
    (home / ".photoprint" / "folder").write_text(str(missing), encoding="utf-8")

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert (desktop_folder / LAUNCHER_NAME).is_file()
    assert not missing.exists()


def test_install_ignores_missing_recorded_folder_with_space(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I1: `.photoprint/folder` называет удалённую папку «Печать картинок» → ярлык на Рабочем столе, stderr пуст.

    Папка у заказчика всегда называется «Печать картинок» — с пробелом.
    Заказчик перенёс её в «Документы» (клиент записал этот путь, M4), а
    потом удалил. Проверка существования должна брать путь одним словом
    (`[ ! -d "$F" ]`): без кавычек `[` отказывает на лишнем слове с кодом 2,
    условие ложно, и установщик воскрешает удалённую папку по старому пути
    — там, где её уже никто не ищет, — и «Готово…» называет её. Ожидается
    папка по умолчанию на Рабочем столе, «Готово…» называет именно её, а
    stderr пуст: сообщение `[` о синтаксисе заказчику не нужно (I10).
    """
    documents = tmp_path / "Документы"
    documents.mkdir()
    missing = documents / FOLDER_NAME
    (home / ".photoprint").mkdir()
    (home / ".photoprint" / "folder").write_text(str(missing), encoding="utf-8")
    # Предпосылка: в записанном пути есть пробел, а каталога по нему нет.
    assert " " in str(missing)
    assert not missing.exists()

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert (desktop_folder / LAUNCHER_NAME).is_file()
    assert not missing.exists()
    assert _lines(result)[-1] == DONE.format(F=desktop_folder), _report(result)
    assert result.stderr == b"", _report(result)


def test_install_uses_recorded_folder_with_space(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I1, I11: записанная папка «Печать картинок» перенесена и существует → ярлык ставится туда, stderr пуст.

    Обычное обновление у заказчика: папку «Печать картинок» (с пробелом в
    имени) перенесли в «Документы», клиент записал её путь (M4). Путь в
    проверке `[ ! -d "$F" ]` — одно слово; без кавычек `[` при каждой
    переустановке печатал бы в Терминал заказчика «binary operator
    expected», хотя папка выбрана верно. Ярлык — в перенесённой папке,
    новой на Рабочем столе нет, «Готово…» называет перенесённую.
    """
    moved = tmp_path / "Документы" / FOLDER_NAME
    moved.mkdir(parents=True)
    (home / ".photoprint").mkdir()
    (home / ".photoprint" / "folder").write_text(str(moved), encoding="utf-8")

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert (moved / LAUNCHER_NAME).is_file()
    assert not desktop_folder.exists()
    assert _lines(result)[-1] == DONE.format(F=moved), _report(result)
    assert result.stderr == b"", _report(result)


def test_install_ignores_recorded_path_that_is_file(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I1, I11: `.photoprint/folder` называет обычный файл, а не каталог → ярлык в `~/Desktop/Печать картинок`, stderr пуст.

    I1 берёт записанную папку, только если это существующий каталог. На
    месте папки может оказаться файл: заказчик унёс папку «Печать
    картинок» из «Документов» на Рабочий стол и оставил на старом месте
    псевдоним Finder с тем же именем (Cmd+Option при перетаскивании), а
    клиент с нового места ещё не запускал. Псевдоним Finder — обычный
    файл, не каталог. Проверка «путь существует» (`-e`) приняла бы его:
    `mkdir -p` отказал бы «File exists», и обновление падало бы на шаге
    «папка и ярлык» с неверной подсказкой про доступ к Рабочему столу — и
    так при каждой переустановке. Ожидается папка по умолчанию, «Готово…»
    называет её, файл не тронут. Записанный путь — не путь по умолчанию:
    на нём обе проверки падали бы одинаково.
    """
    recorded = tmp_path / "Документы" / FOLDER_NAME
    recorded.parent.mkdir()
    recorded.write_bytes(b"not a folder")
    (home / ".photoprint").mkdir()
    (home / ".photoprint" / "folder").write_text(str(recorded), encoding="utf-8")
    # Предпосылка: путь существует (`-e` истинно), но это не каталог.
    assert recorded.exists() and not recorded.is_dir()

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode == 0, _report(result)
    assert (desktop_folder / LAUNCHER_NAME).is_file()
    assert recorded.read_bytes() == b"not a folder"
    assert _lines(result)[-1] == DONE.format(F=desktop_folder), _report(result)
    assert result.stderr == b"", _report(result)


def test_install_download_failure(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I3, I10: архив не скачался → сбой шага «скачивание», кода нет, `tmp.*` убран.

    Весь stdout — строка шага и текст сбоя: после сбоя следующие шаги не
    идут, а `on_exit` печатает текст последним. Причину печатает сам `curl`
    в stderr — она не глушится (I10).
    """
    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_TARBALL": MISSING_TARBALL}
    )

    assert result.returncode != 0, _report(result)
    assert _lines(result) == [
        "▸ скачивание…",
        FAILED.format(STEP="скачивание"),
    ], _report(result)
    assert "curl:" in result.stderr.decode("utf-8", "replace"), _report(result)
    assert not (home / ".photoprint" / "app").exists()
    assert _tmp_dirs(home) == []


def test_install_archive_without_app(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I3, I10: в архиве нет `app/photoprint` → свой текст и сбой шага «скачивание».

    Архив скачался и распаковался, но кода клиента в нём нет: без проверки
    шаг «замена кода» заменил бы рабочий клиент пустым местом.
    """
    tarball = build_tarball(tmp_path / "dist", include_app=False)
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode != 0, _report(result)
    assert _lines(result) == [
        "▸ скачивание…",
        NO_APP_IN_ARCHIVE,
        FAILED.format(STEP="скачивание"),
    ], _report(result)
    assert not (home / ".photoprint" / "app").exists()
    assert _tmp_dirs(home) == []


@pytest.mark.parametrize("missing", ["package", "init"], ids=["no-package", "no-init"])
def test_install_archive_without_package(
    tmp_path: Path, home: Path, cwd: Path, missing: str
) -> None:
    """I3, I4, I10: в архиве есть `app/`, но нет пакета клиента → свой текст, сбой «скачивания», прежний код цел.

    Проверка I3 — по файлу `app/photoprint/__init__.py`, а не по каталогу
    `app`: в архиве сломанной ветки `main` (пакет перенесли или
    переименовали, неудачное слияние) остаются `app/static`,
    `app/requirements.txt` и `app/launcher.command`. Установщик качает
    `main` в любой момент, и проверка существует ровно для этого случая.
    Проверка по каталогу пропустила бы такой архив: «замена кода»
    поставила бы вместо рабочего клиента дерево без пакета, установка
    сказала бы «Готово…», а ярлык — «Клиент не установлен…» (близнец L2,
    `test_launcher_app_without_package`). Два архива:
    - `no-package` — нет всего каталога `app/photoprint`;
    - `no-init` — пакет на месте, нет только `__init__.py`: такой архив
      пропустила бы и проверка каталога `app/photoprint`, ловит его только
      проверка файла, а ярлык без `__init__.py` тоже говорит «Клиент не
      установлен…» (L2).
    Сначала удачная установка — так выглядит обновление у заказчика;
    после сбоя её код не тронут, `app.old` и `tmp.*` нет. Архив приходит
    через `PHOTOPRINT_TARBALL` (§ 7.3).
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    init = home / ".photoprint" / "app" / "photoprint" / "__init__.py"
    init_bytes = init.read_bytes()

    init_name = f"{ARCHIVE_PACKAGE}/__init__.py"
    if missing == "package":
        broken = _tarball_without(
            tarball,
            tmp_path / "broken",
            lambda name: name == ARCHIVE_PACKAGE or name.startswith(ARCHIVE_PACKAGE + "/"),
        )
    else:
        broken = _tarball_without(tarball, tmp_path / "broken", lambda name: name == init_name)
    with tarfile.open(broken) as archive:
        names = archive.getnames()
    # Предпосылка: `app/` в архиве есть, а `__init__.py` пакета — нет; у
    # `no-package` нет всего пакета, у `no-init` пакет с кодом на месте.
    assert f"{TARBALL_TOP}/app/launcher.command" in names, names
    assert f"{TARBALL_TOP}/app/requirements.txt" in names, names
    assert init_name not in names, names
    if missing == "package":
        assert [name for name in names if name.startswith(ARCHIVE_PACKAGE)] == [], names
    else:
        assert f"{ARCHIVE_PACKAGE}/__main__.py" in names, names

    second = run_install(home, broken, cwd=cwd)

    assert second.returncode != 0, _report(second)
    assert _lines(second) == [
        "▸ скачивание…",
        NO_APP_IN_ARCHIVE,
        FAILED.format(STEP="скачивание"),
    ], _report(second)
    assert init.read_bytes() == init_bytes
    assert not (home / ".photoprint" / "app.old").exists()
    assert _tmp_dirs(home) == []


def test_install_retries_transient_http_error(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I3: сервер архива один раз ответил 503 → `curl --retry 3` повторяет запрос, установка удачна.

    Короткий сбой codeload.github.com (502, 503, 504, тайм-аут) не должен
    ронять установку заказчика на шаге «скачивание»: без повтора заказчику
    пришлось бы самому заметить сбой и ввести команду ещё раз. Адреса
    `file://` у прочих тестов повтор не проходят, поэтому здесь сервер
    настоящий, на `127.0.0.1` и свободном порту: первый GET — 503, второй —
    архив. `PHOTOPRINT_TARBALL` — подмена из § 7.3, HTTP-сервер на
    `127.0.0.1` — условие окружения (§ 7.3); переменных прокси в окружении
    `run_install` нет. Повтор стоит около секунды паузы `curl`.
    """
    tarball = build_tarball(tmp_path / "dist")
    body = tarball.read_bytes()
    requests: List[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        """Сервер архива: на первый GET — 503, на остальные — архив."""

        def do_GET(self) -> None:  # noqa: N802
            """Записать запрос; ответить 503 на первый и архивом на остальные."""
            requests.append(self.path)
            if len(requests) == 1:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Не писать журнал запросов в stderr pytest."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/photoprint.tar.gz"
        result = run_install(
            home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_TARBALL": url}
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)

    assert result.returncode == 0, _report(result)
    # Ровно два запроса: 503 и повтор, который получил архив.
    assert requests == ["/photoprint.tar.gz", "/photoprint.tar.gz"], requests
    assert (home / ".photoprint" / "app" / "photoprint" / "__init__.py").is_file()
    assert _lines(result)[-1] == DONE.format(F=desktop_folder), _report(result)


def test_install_survives_three_transient_http_errors(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I3: сервер архива трижды подряд ответил 503 → три повтора `curl --retry 3`, четвёртый запрос получает архив, установка удачна.

    I3 задаёт ровно три повтора — против коротких сбоев
    codeload.github.com, которые идут и по нескольку подряд.
    `test_install_retries_transient_http_error` отвечает 503 один раз и
    проходит при любом `--retry` от 1: с `--retry 1` или `--retry 2` два-три
    сбоя подряд роняли бы установку заказчика на шаге «скачивание», и ему
    пришлось бы самому вводить команду ещё раз. Здесь сервер отвечает 503
    трижды, а архив отдаёт на четвёртый запрос: запросов ровно четыре,
    код клиента на месте, stdout — все строки шагов и «Готово…». Сервер
    настоящий, на `127.0.0.1` (§ 7.3). Паузы `curl` между повторами — 1, 2
    и 4 с; короче их не сделать: `Retry-After` curl 8.7 их только
    удлиняет, а `--retry-delay` в установщике нет, — отсюда около 7 с.
    """
    tarball = build_tarball(tmp_path / "dist")
    with _flaky_tarball_server(tarball.read_bytes(), 3) as (url, requests):
        result = run_install(home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_TARBALL": url})

    assert result.returncode == 0, _report(result)
    assert requests == [ARCHIVE_PATH] * 4, requests
    assert (home / ".photoprint" / "app" / "photoprint" / "__init__.py").is_file()
    assert _lines(result) == STEP_LINES + [DONE.format(F=desktop_folder)], _report(result)
    assert _tmp_dirs(home) == []


def test_install_gives_up_after_three_retries(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I3, I10: сервер архива всё время отвечает 503 → ровно четыре запроса (`--retry 3`), затем сбой «скачивания» с причиной `curl`.

    Число повторов — ровно три, как в I3: `--retry N` делает N + 1
    запросов, и только сервер, который не отдаёт архив вовсе, отличает
    `--retry 3` от `--retry 5` (при нём заказчик ждал бы отказа около 31 с
    вместо 7 — паузы 1+2+4+8+16 с) и от меньшего числа. Затем `curl` отказывает
    сам: с `-f` ответ 503 — его ошибка, и её текст («curl: (22) …»)
    остаётся в stderr (`-S`, I10: причину печатает сама команда). Без `-f`
    `curl` сохранил бы пустое тело ответа 503 как архив и вышел бы с кодом
    0, bsdtar «распаковал» бы пустой файл без ошибки, и заказчик прочитал
    бы «В скачанном архиве нет app/photoprint.» вместо настоящей причины —
    сбоя сервера. Весь stdout — строка шага и текст сбоя, кода клиента
    нет, `tmp.*` убран. Сервер настоящий, на `127.0.0.1` (§ 7.3); около
    7 с — паузы `curl`.
    """
    tarball = build_tarball(tmp_path / "dist")
    with _flaky_tarball_server(tarball.read_bytes(), None) as (url, requests):
        result = run_install(home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_TARBALL": url})

    assert result.returncode != 0, _report(result)
    assert requests == [ARCHIVE_PATH] * 4, requests
    assert _lines(result) == [
        "▸ скачивание…",
        FAILED.format(STEP="скачивание"),
    ], _report(result)
    assert "curl:" in result.stderr.decode("utf-8", "replace"), _report(result)
    assert not (home / ".photoprint" / "app").exists()
    assert _tmp_dirs(home) == []


def test_failed_update_keeps_old_code(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I4, I11: обновление, у которого не скачался архив, оставляет прежний код целым.

    Первая установка удачна; повторная — с несуществующим архивом. Сбой
    случился до замены кода (I4: «сбой до шага 2 оставляет прежний код
    целым»), и клиент по-прежнему запускается ярлыком.
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    init = home / ".photoprint" / "app" / "photoprint" / "__init__.py"
    init_bytes = init.read_bytes()

    second = run_install(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_TARBALL": MISSING_TARBALL}
    )

    assert second.returncode != 0, _report(second)
    assert init.is_file()
    assert init.read_bytes() == init_bytes
    assert not (home / ".photoprint" / "app.old").exists()
    assert _tmp_dirs(home) == []


def test_install_truncated_archive_keeps_old_code(
    tmp_path: Path, home: Path, cwd: Path
) -> None:
    """I3, I4, I10: архив оборван сразу после `app/photoprint/__init__.py` → сбой «скачивания», прежний код цел.

    Так выглядит повреждённый архив: `tar` успевает распаковать
    `__init__.py` и отказывает только на обрыве. Проверка I3 по
    `__init__.py` такой обрыв не заметит — его ловит лишь код выхода `tar`
    под `set -e`. Без него «замена кода» поставила бы вместо рабочего
    клиента обрывок без `__main__.py`, `static/` и `requirements.txt`,
    установка сказала бы «Готово», а ярлык потом не запустил бы клиент.
    Причину печатает сам `tar` в stderr (I10). Архив приходит через
    `PHOTOPRINT_TARBALL` (§ 7.3), оборванные байты — настоящий сбой.
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    main_py = home / ".photoprint" / "app" / "photoprint" / "__main__.py"
    main_bytes = main_py.read_bytes()

    raw = gzip.decompress(tarball.read_bytes())
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
    names = [member.name for member in members]
    init = names.index(f"{TARBALL_TOP}/app/photoprint/__init__.py")
    # Предпосылка: следом за `__init__.py` идёт `__main__.py` (`tarfile`
    # кладёт каталог по алфавиту) — обрыв отрезает точку входа клиента.
    assert names[init + 1] == f"{TARBALL_TOP}/app/photoprint/__main__.py"
    # Обрыв посреди 512-байтового заголовка `__main__.py`: `__init__.py`
    # целиком в сохранённой части. Сжатие — заново, чтобы отказал именно
    # `tar` на обрыве, а не распаковка gzip.
    cut = members[init + 1].offset + 256
    truncated = tmp_path / "cut" / "photoprint.tar.gz"
    truncated.parent.mkdir()
    truncated.write_bytes(gzip.compress(raw[:cut]))

    second = run_install(home, truncated, cwd=cwd)

    assert second.returncode != 0, _report(second)
    assert _lines(second) == [
        "▸ скачивание…",
        FAILED.format(STEP="скачивание"),
    ], _report(second)
    assert "tar:" in second.stderr.decode("utf-8", "replace"), _report(second)
    assert main_py.read_bytes() == main_bytes
    assert not (home / ".photoprint" / "app.old").exists()
    assert _tmp_dirs(home) == []


def test_install_replace_code_failure(
    tmp_path: Path, home: Path, cwd: Path, chflags_to: Callable[[Path, int], None]
) -> None:
    """I4, I10, § 7.2: код заменить нельзя → сбой шага «замена кода», прежний код цел, `tmp.*` убран.

    Первая установка удачна. Затем на каталог кода ставится флаг «Защита»
    (`chflags uchg`, § 7.3): переименовать его нельзя, и `mv app app.old`
    отказывает по-настоящему. Установка должна остановиться на этом шаге
    с его именем в тексте (I10: `set -e` действует внутри шага, а сбой
    называется своим шагом, а не предыдущим). Причину печатает сам `mv` в
    stderr. Прежний код не тронут (I4: сбой до шага 2 оставляет прежний
    код целым), `app.old` не появился.
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    app = home / ".photoprint" / "app"
    init = app / "photoprint" / "__init__.py"
    init_bytes = init.read_bytes()
    # Флаг снимает финализатор `chflags_to`: иначе pytest не удалил бы
    # `tmp_path`.
    chflags_to(app, stat.UF_IMMUTABLE)

    second = run_install(home, tarball, cwd=cwd)

    assert second.returncode != 0, _report(second)
    assert _lines(second) == STEP_LINES[:2] + [
        FAILED.format(STEP="замена кода"),
    ], _report(second)
    assert "mv:" in second.stderr.decode("utf-8", "replace"), _report(second)
    assert init.read_bytes() == init_bytes
    assert not (home / ".photoprint" / "app.old").exists()
    assert _tmp_dirs(home) == []


def test_install_venv_failure(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I5, I10, § 7.2: окружение не создаётся → сбой шага «окружение», `tmp.*` убран.

    `PHOTOPRINT_PYTHON` проходит проверку версии I2 и отказывает на
    `-m venv venv` со своей причиной в stderr. Установка останавливается
    на этом шаге с его именем в тексте: окружение создаётся именно из
    `PHOTOPRINT_PYTHON`, а не из `/usr/bin/python3`, и сбой его создания
    не пропускается (I10: `set -e` действует внутри шага).
    """
    fake_python = _fake_python(
        tmp_path, 'echo "fakepy: окружение не создаётся" >&2\nexit 1\n'
    )

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_PYTHON": str(fake_python)}
    )

    assert result.returncode != 0, _report(result)
    assert _lines(result) == STEP_LINES[:3] + [
        FAILED.format(STEP="окружение"),
    ], _report(result)
    assert "fakepy: окружение не создаётся" in result.stderr.decode(
        "utf-8", "replace"
    ), _report(result)
    assert _tmp_dirs(home) == []


def test_install_deps_failure(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I6, I10, § 7.2: pip отказал → сбой шага «зависимости», `tmp.*` убран.

    Шаг не пропускается (`PHOTOPRINT_SKIP_PIP` пуст). `PHOTOPRINT_PYTHON`
    создаёт окружение с поддельным python, чей pip выходит с кодом 1 и
    пишет причину в stderr, — сети тест не трогает. Установка
    останавливается на этом шаге с его именем в тексте, до «папки и
    ярлыка».
    """
    fake_python = _fake_python_with_venv(tmp_path, 1, tmp_path / "pip-call")

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home,
        tarball,
        cwd=cwd,
        env_extra={"PHOTOPRINT_PYTHON": str(fake_python), "PHOTOPRINT_SKIP_PIP": ""},
    )

    assert result.returncode != 0, _report(result)
    assert _lines(result) == STEP_LINES[:4] + [
        FAILED.format(STEP="зависимости"),
    ], _report(result)
    assert "pip: отказ поддельного pip" in result.stderr.decode(
        "utf-8", "replace"
    ), _report(result)
    assert _tmp_dirs(home) == []


def test_install_deps_command(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I6: шаг «зависимости» вызывает pip окружения ровно командой § 4.8 из `HOME/.photoprint`.

    `PHOTOPRINT_SKIP_PIP` пуст, и шаг выполняется; pip поддельный и
    удачный, поэтому сети нет, а установка доходит до «Готово…». Pip
    записал свой каталог и аргументы: каталог — Корень (там лежит
    `app/requirements.txt`, путь к нему относительный), аргументы —
    дословно I6, с `--only-binary=:all:` (только колёса, без сборки на
    маке заказчика).
    """
    record = tmp_path / "pip-call"
    fake_python = _fake_python_with_venv(tmp_path, 0, record)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home,
        tarball,
        cwd=cwd,
        env_extra={"PHOTOPRINT_PYTHON": str(fake_python), "PHOTOPRINT_SKIP_PIP": ""},
    )

    assert result.returncode == 0, _report(result)
    assert _lines(result)[-1] == DONE.format(F=desktop_folder), _report(result)
    assert record.read_text(encoding="utf-8").splitlines() == [
        str((home / ".photoprint").resolve()),
        *PIP_ARGS,
    ]


def test_install_skips_printer_check_without_pip(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I8: с `PHOTOPRINT_SKIP_PIP=1` шага «библиотека USB» нет целиком — ни строки, ни проверки.

    Без зависимостей в окружении нет ни escpos, ни pyusb: проверять
    принтер нечем, и настоящий `--check-printer` упал бы на импорте. Шаг
    пропускается вместе со строкой «▸ библиотека USB…» (в отличие от
    «зависимостей», чья строка печатается и при пропуске). Весь stdout —
    пять строк шагов I3–I7 и «Готово…», код 0, stderr пуст.
    """
    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_SKIP_PIP": "1"}
    )

    assert result.returncode == 0, _report(result)
    assert USB_STEP_LINE not in result.stdout.decode("utf-8"), _report(result)
    assert _lines(result) == STEP_LINES + [DONE.format(F=desktop_folder)], _report(result)
    assert result.stderr == b"", _report(result)


def test_install_printer_check_command(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I8, I10: шаг «библиотека USB» — `../venv/bin/python -m photoprint --check-printer` из `app`; «Принтер не найден» не прерывает установку.

    `PHOTOPRINT_SKIP_PIP` пуст, и оба шага, «зависимости» и «библиотека
    USB», выполняются; python окружения поддельный (`_fake_python_with_check`),
    поэтому ни сети, ни настоящего принтера. Проверка:
    - вызвана python окружения — не `PHOTOPRINT_PYTHON`, тот отказал бы
      на неожиданном вызове — из каталога кода `app` (там `-m photoprint`
      находит пакет, как в L3) с аргументами M6 дословно;
    - её строка «Принтер не найден…» дошла до stdout: вывод проверки не
      глушится, по нему заказчик узнаёт о принтере;
    - код проверки 0, и установка доходит до «Готово…»: без принтера
      клиент всё равно установлен (M6, I8).
    Шаг идёт последним, после «папки и ярлыка», со своей строкой «▸ …».
    """
    record = tmp_path / "check-call"
    fake_python = _fake_python_with_check(tmp_path, 0, CHECK_NOT_FOUND, record)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home,
        tarball,
        cwd=cwd,
        env_extra={"PHOTOPRINT_PYTHON": str(fake_python), "PHOTOPRINT_SKIP_PIP": ""},
    )

    assert result.returncode == 0, _report(result)
    assert _lines(result) == STEP_LINES + [
        USB_STEP_LINE,
        CHECK_NOT_FOUND,
        DONE.format(F=desktop_folder),
    ], _report(result)
    assert record.read_text(encoding="utf-8").splitlines() == [
        str((home / ".photoprint" / "app").resolve()),
        *CHECK_ARGS,
    ]


def test_install_printer_check_failure(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I8, I10, § 7.2: `--check-printer` вышел с кодом 1 (libusb не загрузилась) → сбой шага «библиотека USB».

    Проверка печатает свой текст M6 «Не загрузилась библиотека USB…» и
    выходит с кодом 1. Установщик не глушит её вывод — это и есть причина
    сбоя, на которую ссылается текст I10 «Причина — в сообщениях выше», —
    и останавливается с именем шага в тексте, без подсказки про Рабочий
    стол (она — только для «папки и ярлыка»). `tmp.*` убран.
    """
    record = tmp_path / "check-call"
    fake_python = _fake_python_with_check(tmp_path, 1, CHECK_NO_LIBUSB, record)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home,
        tarball,
        cwd=cwd,
        env_extra={"PHOTOPRINT_PYTHON": str(fake_python), "PHOTOPRINT_SKIP_PIP": ""},
    )

    assert result.returncode != 0, _report(result)
    assert _lines(result) == STEP_LINES + [
        USB_STEP_LINE,
        CHECK_NO_LIBUSB,
        FAILED.format(STEP="библиотека USB"),
    ], _report(result)
    assert _tmp_dirs(home) == []


def test_install_printer_check_stderr_not_silenced(
    tmp_path: Path, home: Path, cwd: Path
) -> None:
    """I8, I10: stderr проверки принтера не глушится — упавшая на импорте проверка сама называет причину сбоя.

    Проверка не дошла до своего текста M6: Python клиента упал на импорте
    (`_fake_python_with_crashing_check`) — stdout пуст, трассировка в
    stderr, код 1. Так бывает, когда колёса Pillow или pydantic-core не
    под ту архитектуру (перенос с Intel-мака, Терминал под Rosetta). Эта
    трассировка — единственная причина сбоя, на которую ссылается текст
    I10 «Причина — в сообщениях выше», и она доходит до заказчика
    байт в байт: I8 «вывод команды не глушится» — это и stdout, и stderr
    (I10: «Причину печатают сами команды»). С `2>/dev/null` у проверки
    заказчик увидел бы текст сбоя без единой строки причины.

    Весь stdout — строки шагов и текст сбоя шага «библиотека USB»; весь
    stderr — трассировка проверки, установщик сам в stderr не пишет.
    Проверку вызвал python окружения из `app` с аргументами M6; `tmp.*`
    убран.
    """
    record = tmp_path / "check-call"
    fake_python = _fake_python_with_crashing_check(tmp_path, record)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home,
        tarball,
        cwd=cwd,
        env_extra={"PHOTOPRINT_PYTHON": str(fake_python), "PHOTOPRINT_SKIP_PIP": ""},
    )

    # I10: `set -e` выходит с кодом упавшей команды, `on_exit` его сохраняет.
    assert result.returncode == 1, _report(result)
    assert _lines(result) == STEP_LINES + [
        USB_STEP_LINE,
        FAILED.format(STEP="библиотека USB"),
    ], _report(result)
    assert result.stderr == CHECK_CRASH.encode("utf-8"), _report(result)
    assert record.read_text(encoding="utf-8").splitlines() == [
        str((home / ".photoprint" / "app").resolve()),
        *CHECK_ARGS,
    ]
    assert _tmp_dirs(home) == []


def test_install_printer_check_without_skip_pip_variable(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I6, I8, I10: без переменной `PHOTOPRINT_SKIP_PIP` вовсе — как у заказчика — оба шага выполняются, и установка доходит до «Готово…».

    Все прочие тесты задают переменную (`run_install` — `"1"`, тесты I6 и
    I8 — пустую строку), а у заказчика её нет. Под `set -u` проверка
    `"$PHOTOPRINT_SKIP_PIP"` без `:-` обрывает bash 3.2 на незаданной
    переменной, и `on_exit` видит `$?` = 0: установка молча кончается кодом
    0 после строки шага — без «Готово…», без текста сбоя I10 и без
    проверки принтера. Поэтому здесь переменной в окружении нет
    (`_run_install_without_skip_pip`): шаг «зависимости» вызывает pip, шаг
    «библиотека USB» — проверку (оба поддельные, `_fake_python_with_check`:
    сети и принтера нет). Весь stdout — шесть строк шагов, «Принтер не
    найден…» и «Готово…»; stderr пуст; проверка вызвана из `app` с
    аргументами M6.
    """
    record = tmp_path / "check-call"
    fake_python = _fake_python_with_check(tmp_path, 0, CHECK_NOT_FOUND, record)

    tarball = build_tarball(tmp_path / "dist")
    result = _run_install_without_skip_pip(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_PYTHON": str(fake_python)}
    )

    assert result.returncode == 0, _report(result)
    assert result.stderr == b"", _report(result)
    assert _lines(result) == STEP_LINES + [
        USB_STEP_LINE,
        CHECK_NOT_FOUND,
        DONE.format(F=desktop_folder),
    ], _report(result)
    assert record.read_text(encoding="utf-8").splitlines() == [
        str((home / ".photoprint" / "app").resolve()),
        *CHECK_ARGS,
    ]


def test_install_deps_without_skip_pip_variable(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I6, I8, I10: без переменной `PHOTOPRINT_SKIP_PIP` вовсе — как у заказчика — pip вызван командой I6 из Корня, затем проверка принтера.

    Соседний тест (`test_install_printer_check_without_skip_pip_variable`)
    записывает только проверку принтера, а pip его поддельного окружения
    молчит и следа не оставляет. Поэтому `step_deps`, который пропускает
    pip, когда переменная не задана (например,
    `"${PHOTOPRINT_SKIP_PIP-1}" = 1`: пустая строка — pip, отсутствие —
    пропуск), проходил весь набор: все прочие тесты I6 задают переменную
    пустой строкой. У заказчика переменной нет, и зависимости не ставились
    бы никогда: и проверка принтера I8, и сам клиент падали бы на импорте
    своих пакетов.

    Здесь переменной в окружении нет (`_run_install_without_skip_pip`), а
    python окружения записывает оба вызова в отдельные файлы
    (`_fake_python_with_pip_and_check`):
    - запись pip — Корень `HOME/.photoprint` (путь `app/requirements.txt`
      относительный) и аргументы I6 дословно, ровно один вызов — как в
      `test_install_deps_command`;
    - запись проверки — каталог `app` и аргументы M6, ровно один вызов.
    stdout дословно — шесть строк шагов, «Принтер не найден…» и «Готово…»;
    stderr пуст; код 0.
    """
    pip_record = tmp_path / "pip-call"
    check_record = tmp_path / "check-call"
    fake_python = _fake_python_with_pip_and_check(tmp_path, pip_record, check_record)

    tarball = build_tarball(tmp_path / "dist")
    result = _run_install_without_skip_pip(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_PYTHON": str(fake_python)}
    )

    assert result.returncode == 0, _report(result)
    assert result.stderr == b"", _report(result)
    assert result.stdout.decode("utf-8") == "".join(
        line + "\n"
        for line in STEP_LINES
        + [USB_STEP_LINE, CHECK_NOT_FOUND, DONE.format(F=desktop_folder)]
    ), _report(result)
    # Сначала — что pip вызван вовсе: без записи чтение ниже упало бы с
    # `FileNotFoundError` вместо сообщения с выводом установщика.
    assert pip_record.exists(), _report(result)
    assert pip_record.read_text(encoding="utf-8").splitlines() == [
        str((home / ".photoprint").resolve()),
        *PIP_ARGS,
    ]
    assert check_record.read_text(encoding="utf-8").splitlines() == [
        str((home / ".photoprint" / "app").resolve()),
        *CHECK_ARGS,
    ]


@pytest.mark.parametrize(
    "cdpath",
    ["{projects}", ".:{projects}"],
    ids=["other-app", "dot-first"],
)
def test_install_printer_check_with_cdpath(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path, cdpath: str
) -> None:
    """I8, § 7.3: заданный в Терминале заказчика `CDPATH` не уводит проверку принтера из `HOME/.photoprint/app` и не добавляет строк в вывод.

    `CDPATH` — условие окружения заказчика (§ 7.3), не подмена: его могут
    экспортировать из `~/.zshrc`, и bash установщика его наследует. Для
    относительного `cd app` bash сначала ищет `app` в каталогах `CDPATH`,
    а найдя там, печатает найденный путь в stdout:
    - `other-app` — `CDPATH` называет чужой каталог, где тоже есть `app`:
      `cd app` ушёл бы туда, `../venv/bin/python` не нашёлся бы, и
      установка падала бы на шаге «библиотека USB» при каждом запуске;
    - `dot-first` — частая форма `.:<каталог>`: `cd app` находит верный
      каталог через `.`, но печатает его путь лишней строкой между «▸
      библиотека USB…» и текстом проверки.
    Путь, начинающийся с `./`, bash в `CDPATH` не ищет (так же ярлык
    защищён в L1). Проверка вызвана из `HOME/.photoprint/app` с
    аргументами M6; stdout — ровно шесть строк шагов, «Принтер не найден…»
    и «Готово…»; stderr пуст.
    """
    projects = tmp_path / "projects"
    (projects / "app").mkdir(parents=True)
    record = tmp_path / "check-call"
    fake_python = _fake_python_with_check(tmp_path, 0, CHECK_NOT_FOUND, record)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home,
        tarball,
        cwd=cwd,
        env_extra={
            "PHOTOPRINT_PYTHON": str(fake_python),
            "PHOTOPRINT_SKIP_PIP": "",
            "CDPATH": cdpath.format(projects=projects),
        },
    )

    assert result.returncode == 0, _report(result)
    assert result.stderr == b"", _report(result)
    assert _lines(result) == STEP_LINES + [
        USB_STEP_LINE,
        CHECK_NOT_FOUND,
        DONE.format(F=desktop_folder),
    ], _report(result)
    assert record.read_text(encoding="utf-8").splitlines() == [
        str((home / ".photoprint" / "app").resolve()),
        *CHECK_ARGS,
    ]


def test_install_desktop_not_writable(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I7, I10: папку создать нельзя → сбой шага «папка и ярлык» и подсказка про доступ к Рабочему столу.

    `HOME/Desktop` — обычный файл, поэтому `mkdir -p` папки отказывает по-
    настоящему. У заказчика так же отказывает macOS, когда Терминалу не
    дали доступ к Рабочему столу, — отсюда подсказка сразу после текста
    сбоя. Все пять шагов успели начаться; `tmp.*` убран.

    Причину печатает сам `mkdir` в stderr (I10): подсказка отсылает к
    «Operation not permitted» «выше», и эту строку заказчику показывает
    именно `mkdir`. Заглушённый `mkdir` оставил бы подсказку без того, на
    что она ссылается, и заказчик решил бы, что доступ давать не нужно.
    """
    (home / "Desktop").write_text("не каталог", encoding="utf-8")

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd)

    assert result.returncode != 0, _report(result)
    assert _lines(result) == STEP_LINES + [
        FAILED.format(STEP="папка и ярлык"),
        DESKTOP_HINT,
    ], _report(result)
    assert "mkdir:" in result.stderr.decode("utf-8", "replace"), _report(result)
    assert _tmp_dirs(home) == []


def test_install_download_failure_in_customer_locale(
    tmp_path: Path, home: Path, cwd: Path
) -> None:
    """I10: в локали UTF-8 Терминала заказчика сбой «скачивания» печатает тот же текст I10.

    Заказчик вводит команду в Терминале, а тот ставит `LANG` с UTF-8. В
    такой локали bash 3.2 считает первый байт `»` частью имени переменной:
    `«$STEP»` без фигурных скобок уронил бы `on_exit` с «unbound variable»,
    и заказчик не увидел бы, на каком шаге сбой. Предпосылка показывает,
    что локаль на машине есть и опасность настоящая.
    """
    _assert_customer_locale_active()

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home,
        tarball,
        cwd=cwd,
        env_extra={**CUSTOMER_LOCALE, "PHOTOPRINT_TARBALL": MISSING_TARBALL},
    )

    assert result.returncode != 0, _report(result)
    assert _lines(result) == [
        "▸ скачивание…",
        FAILED.format(STEP="скачивание"),
    ], _report(result)
    assert _tmp_dirs(home) == []


def test_install_desktop_not_writable_in_customer_locale(
    tmp_path: Path, home: Path, cwd: Path
) -> None:
    """I7, I10: в локали UTF-8 Терминала заказчика сбой «папки и ярлыка» печатает текст I10 и подсказку.

    То же, что `test_install_desktop_not_writable`, но в локали Терминала
    заказчика: все строки шагов, текст сбоя с именем шага и подсказка про
    доступ к Рабочему столу дошли до stdout целыми, а причина, на которую
    ссылается подсказка, — строка `mkdir` — до stderr (I10).
    """
    _assert_customer_locale_active()
    (home / "Desktop").write_text("не каталог", encoding="utf-8")

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd, env_extra=dict(CUSTOMER_LOCALE))

    assert result.returncode != 0, _report(result)
    assert _lines(result) == STEP_LINES + [
        FAILED.format(STEP="папка и ярлык"),
        DESKTOP_HINT,
    ], _report(result)
    assert "mkdir:" in result.stderr.decode("utf-8", "replace"), _report(result)
    assert _tmp_dirs(home) == []


def test_install_ctrl_c_during_download(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I10: Ctrl+C посреди скачивания → текст сбоя шага «скачивание» ровно один раз, `tmp.*` убран, кода клиента нет.

    `on_exit` висит только на `EXIT` (§ 4.8 I1: `trap on_exit EXIT`) и при
    Ctrl+C выполняется один раз: bash, чей `curl` убит `SIGINT`, сам
    выполняет ловушку `EXIT` и выходит. Привычная запись
    `trap on_exit EXIT INT TERM` вызвала бы `on_exit` дважды — из ловушки
    `INT`, чей `exit`, в свою очередь, вызывает `EXIT`, — и заказчик,
    прервавший долгую установку, прочитал бы «Установка не удалась…» два
    раза подряд. Ctrl+C у заказчика вероятнее всего на долгом шаге; здесь
    — на скачивании, где его момент можно выбрать без пауз.

    Сервер архива — настоящий, на `127.0.0.1` (§ 7.3): принимает запрос и
    молчит, `curl` ждёт ответа. Тогда тест посылает `SIGINT` всей группе
    процессов установщика — так Терминал доставляет Ctrl+C всей команде
    `curl … | bash`. Установщик — в своей сессии, чтобы сигнал не попал в
    pytest, и запущен «как из Терминала» (`TERMINAL_BASH`: SIGINT и SIGHUP
    — по умолчанию, § 7.3); остальное окружение — как у `run_install`.
    Сигнал уходит только после того, как сервер получил запрос, поэтому
    момент Ctrl+C не зависит от скорости машины.
    """
    got_request = threading.Event()
    release = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        """Сервер архива, который принял запрос и молчит, пока тест его не отпустит."""

        def do_GET(self) -> None:  # noqa: N802
            """Отметить запрос и ждать, не отвечая: `curl` висит на скачивании."""
            got_request.set()
            release.wait(60)

        def log_message(self, *args: object) -> None:
            """Не писать журнал запросов в stderr pytest."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Окружение — как у `run_install`: `TMPDIR` рядом с `cwd`, без pip.
    tmpdir = cwd.parent / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": INSTALL_PATH,
        "PHOTOPRINT_TARBALL": f"http://127.0.0.1:{server.server_address[1]}{ARCHIVE_PATH}",
        "PHOTOPRINT_SKIP_PIP": "1",
        "TMPDIR": str(tmpdir),
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", TERMINAL_BASH],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    def interrupt() -> None:
        """Когда `curl` ждёт ответа, нажать Ctrl+C: `SIGINT` всей группе процессов установщика."""
        if got_request.wait(30):
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError:
                # Установщик уже вышел сам — прерывать нечего; что он
                # напечатал, покажут проверки теста.
                pass

    killer = threading.Thread(target=interrupt, daemon=True)
    killer.start()
    try:
        try:
            stdout, stderr = proc.communicate(
                input=(REPO / "install.sh").read_bytes(), timeout=60
            )
        except subprocess.TimeoutExpired:
            # Ctrl+C не остановил установщик: убрать всю его группу (с
            # `curl`) и дочитать вывод — проверки ниже покажут, что было.
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
    finally:
        release.set()
        killer.join(5)
        server.shutdown()
        server.server_close()
        thread.join(5)
    result = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

    # Предпосылка: запрос дошёл до сервера, и Ctrl+C пришёл посреди скачивания.
    assert got_request.is_set(), _report(result)
    assert result.returncode != 0, _report(result)
    assert _lines(result) == [
        "▸ скачивание…",
        FAILED.format(STEP="скачивание"),
    ], _report(result)
    assert not (home / ".photoprint" / "app").exists()
    assert _tmp_dirs(home) == []


def test_install_old_python(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I2, I10: Python не прошёл проверку версии → «Нужен Python 3.9 или новее.», код 1, ничего не создано.

    `PHOTOPRINT_PYTHON` — настоящий исполняемый скрипт, который выходит с
    кодом 1, как Python старше 3.9 на проверке `sys.version_info`. I2 идёт
    до создания `.photoprint` и печатает только свой текст: `STEP` ещё
    пуст, и текста сбоя шага нет.
    """
    fake_python = tmp_path / "fakepy"
    fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_python.chmod(0o755)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_PYTHON": str(fake_python)}
    )

    assert result.returncode == 1, _report(result)
    assert _lines(result) == [OLD_PYTHON], _report(result)
    assert "Установка не удалась" not in result.stdout.decode("utf-8")
    assert not (home / ".photoprint").exists()
    assert not (home / "Desktop").exists()


def test_install_python_38(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I2: Python 3.8 → «Нужен Python 3.9 или новее.», код 1, ничего не создано.

    Command Line Tools старых macOS (Catalina, Big Sur) ставят
    `/usr/bin/python3` 3.7–3.8. `test_install_old_python` отказывает на
    любом вызове и не отличает проверку версии от проверки, которая
    сверяет только старшую цифру (`sys.version_info[0] >= 3`) или не
    проверяет ничего. Здесь `PHOTOPRINT_PYTHON` — настоящий
    `/usr/bin/python3`, который называет себя 3.8.9, как Python
    инструментов Big Sur (`_fake_python_version`). Без проверки версии
    заказчик прошёл бы I2, получил бы код и окружение, а pip упал бы на
    шаге «зависимости» (Pillow 11 на 3.8 не ставится) с советом повторить
    команду — вместо текста I2.
    """
    fake_python = _fake_python_version(tmp_path, (3, 8, 9))
    # Предпосылка: код `-c` видит версию 3.8.9 — и кортежем, и полем.
    probe = subprocess.run(
        [
            str(fake_python),
            "-c",
            "import sys; print(tuple(sys.version_info[:3]), sys.version_info.minor)",
        ],
        env={"PATH": INSTALL_PATH},
        capture_output=True,
        timeout=30,
    )
    assert probe.stdout.decode("utf-8") == "(3, 8, 9) 8\n", _report(probe)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_PYTHON": str(fake_python)}
    )

    assert result.returncode == 1, _report(result)
    assert _lines(result) == [OLD_PYTHON], _report(result)
    assert not (home / ".photoprint").exists()
    assert not (home / "Desktop").exists()


def test_install_python_390_passes_version_check(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I2: Python 3.9.0 проходит проверку версии — граница `(3, 9)` включительно, и установка доходит до «Готово…».

    Контроль к `test_install_python_38`: тот же поддельный Python
    (`_fake_python_version`), только с версией 3.9.0. Он проходит I2 и
    строит настоящее окружение (`-m venv` — настоящий `/usr/bin/python3`),
    поэтому отказ 3.8.9 объясняется версией, а не тем, что подделка не
    работает. И граница — ровно `(3, 9)` из I2, любой 3.9 проходит:
    сравнение с микроверсией машины разработки (`>= (3, 9, 6)`) проходило
    бы все прочие тесты — они идут на настоящем 3.9.6.
    """
    fake_python = _fake_python_version(tmp_path, (3, 9, 0))

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(
        home, tarball, cwd=cwd, env_extra={"PHOTOPRINT_PYTHON": str(fake_python)}
    )

    assert result.returncode == 0, _report(result)
    assert _lines(result) == STEP_LINES + [DONE.format(F=desktop_folder)], _report(result)
    assert result.stderr == b"", _report(result)
    python = _venv_python(home, "import sys")
    assert python.returncode == 0, _report(python)


def test_install_ignores_python3_on_path(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """I2, I5: `python3` раньше `/usr/bin` в `PATH` не вызывается — установка идёт на `/usr/bin/python3`.

    В Терминале заказчика `PATH` начинается с `/usr/local/bin`
    (`/etc/paths`), куда свой `python3` кладут установщик python.org и
    Homebrew на Intel-маке (§ 7.3: `PATH` — условие окружения). I2 берёт
    именно `/usr/bin/python3` и перед ним проверяет инструменты
    разработчика. Возьми установщик `python3` из `PATH`, проверка
    инструментов пропускалась бы, окружение строилось бы на чужом Python
    (после `brew upgrade` — висячая ссылка), а старый чужой `python3`
    отказал бы в установке при исправном системном. Здесь `python3` в
    начале `PATH` — старый: записывает вызов и выходит с кодом 1.
    Установка должна пройти и ни разу его не вызвать.
    """
    local_bin = tmp_path / "usr-local-bin"
    local_bin.mkdir()
    called = tmp_path / "python3-on-path-called"
    decoy = local_bin / "python3"
    decoy.write_text(
        "#!/bin/sh\n"
        "# Чужой старый python3 из /usr/local/bin: записывает вызов, отказывает.\n"
        f'echo "$*" >> {shlex.quote(str(called))}\n'
        "exit 1\n",
        encoding="utf-8",
    )
    decoy.chmod(0o755)
    path = f"{local_bin}:{INSTALL_PATH}"
    # Предпосылка: в этом `PATH` имя `python3` действительно находит приманку.
    found = subprocess.run(
        ["/bin/sh", "-c", "command -v python3"],
        env={"PATH": path},
        capture_output=True,
        timeout=30,
    )
    assert found.stdout.decode("utf-8").strip() == str(decoy), _report(found)

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd, env_extra={"PATH": path})

    assert result.returncode == 0, _report(result)
    assert not called.exists(), called.read_text(encoding="utf-8")
    assert _lines(result)[-1] == DONE.format(F=desktop_folder), _report(result)


def test_install_tools_check_is_by_file() -> None:
    """I2: инструменты разработчика ищутся по исполняемому `python3`, а не по коду выхода `xcode-select -p`.

    `xcode-select -p` возвращает 0 и для несуществующего пути — после
    удаления Command Line Tools или их поломки при обновлении macOS. С
    проверкой по коду выхода установщик вызвал бы заглушку
    `/usr/bin/python3` и сказал бы заказчику «Нужен Python 3.9 или
    новее.» вместо того, чтобы открыть окно установки инструментов. Ветка
    «нет инструментов» на машине разработки недостижима: инструменты
    стоят, убрать их без root нельзя, а обратный опыт открыл бы окно
    `xcode-select --install` на экране владельца. Поэтому условие
    сверяется по тексту `install.sh` дословно с I2: вывод `xcode-select
    -p` только читается в `DEV`, а решают обе проверки `-x`.
    """
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    code = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # Вне комментариев `xcode-select -p` встречается ровно один раз — в
    # присваивании `DEV`: его код выхода ничего не решает.
    assert [line for line in code if "xcode-select -p" in line] == [
        'DEV="$(xcode-select -p 2>/dev/null || true)"'
    ]
    assert (
        "if [ -x /Library/Developer/CommandLineTools/usr/bin/python3 ] || "
        '{ [ -n "$DEV" ] && [ -x "$DEV/usr/bin/python3" ]; }; then'
    ) in code


def test_install_tools_check_only_for_system_python() -> None:
    """I2: проверка инструментов — первой в `check_python`, ровно при `PY` = `/usr/bin/python3`, до любого вызова `"$PY"`; весь её блок дословно.

    У заказчика `PHOTOPRINT_PYTHON` не задан, и `PY` — всегда
    `/usr/bin/python3`. Без Command Line Tools (обычное состояние мака не
    разработчика при первой установке) это заглушка: её вызов открывает
    системное окно и отказывает, и заказчик прочитал бы «Нужен Python 3.9
    или новее.» вместо текста I2 про инструменты — и, может быть, пошёл бы
    ставить Python с python.org. Блок проверки инструментов тесты с
    установкой не различают: на машине разработки инструменты стоят, и
    исполняется только ветка «инструменты есть». Перевёрнутое условие
    (`!=`), `tools=yes` с самого начала или пропущенный `exit 1` проходили
    бы их все, а обратный опыт открыл бы окно `xcode-select --install` на
    экране владельца. Поэтому — по тексту `install.sh`, как
    `test_install_tools_check_is_by_file`: тело `check_python` начинается
    с `PY=…` из I2, сразу за ним — весь блок `if [ "$PY" =
    /usr/bin/python3 ]; then … fi` дословно (шаги I2: `DEV=…`, обе
    проверки `-x`, при их провале `xcode-select --install … || true`,
    текст I2 и `exit 1`). Раз блок идёт вторым, до него `"$PY"` не
    вызывается нигде: заглушку до проверки инструментов не зовут.
    """
    body = _function_body("check_python")
    tools_block = [
        'if [ "$PY" = /usr/bin/python3 ]; then',
        "local DEV",
        'DEV="$(xcode-select -p 2>/dev/null || true)"',
        "local tools=no",
        "if [ -x /Library/Developer/CommandLineTools/usr/bin/python3 ] || "
        '{ [ -n "$DEV" ] && [ -x "$DEV/usr/bin/python3" ]; }; then',
        "tools=yes",
        "fi",
        'if [ "$tools" = no ]; then',
        "xcode-select --install >/dev/null 2>&1 || true",
        f'echo "{NEED_TOOLS}"',
        "exit 1",
        "fi",
        "fi",
    ]

    assert body[: 1 + len(tools_block)] == [
        'PY="${PHOTOPRINT_PYTHON:-/usr/bin/python3}"',
        *tools_block,
    ], body


def test_install_tools_install_refusal_keeps_text() -> None:
    """I2: вызов `xcode-select --install` — дословно с `|| true`: его отказ не обрывает установщик до текста I2.

    `xcode-select --install` отказывает с кодом 1, если окно установки уже
    открыто или если система считает инструменты установленными, а
    `python3` в них нет (сломались при обновлении macOS — ровно случай,
    ради которого проверка идёт по `-x`). Без `|| true` `set -e` оборвал
    бы установщик молча: `STEP` ещё пуст, stderr вызова заглушён — заказчик
    не увидел бы ни строки, только код 1. Ветка на машине разработки
    недостижима, а обратный опыт открыл бы окно на экране владельца,
    поэтому — по тексту `install.sh`, как
    `test_install_tools_check_is_by_file`: вне комментариев
    `xcode-select --install` встречается во всём файле ровно в одной
    строке, она — дословно I2, с `|| true`, и следующая строка кода
    `check_python` печатает текст I2.
    """
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    code = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    call = "xcode-select --install >/dev/null 2>&1 || true"
    body = _function_body("check_python")

    assert [line for line in code if "xcode-select --install" in line] == [call]
    assert call in body, body
    assert body[body.index(call) + 1] == f'echo "{NEED_TOOLS}"', body


def test_install_arch_mark_is_uname() -> None:
    """I5: метка архитектуры пишется и сверяется через `uname -m` этого запуска, а не литералом `arm64`/`x86_64`.

    Метка нужна для мака заказчика, перенесённого с Intel Ассистентом
    миграции, и для Терминала под Rosetta (§ 4.8 I5): там `uname -m` —
    `x86_64`, и окружение с колёсами другой архитектуры должно быть
    создано заново. На машине разработки `uname -m` — `arm64`, поэтому
    литерал совпадает с настоящим ответом, и тесты с установкой его не
    отличают: мутанты «сравнивать метку с литералом `"arm64"`» и «писать
    литерал `arm64` вместо `uname -m`» проходят их все. У заказчика оба
    ломают I5: с первым окружение с колёсами `arm64` считалось бы рабочим
    в Терминале под Rosetta, а на Intel-маке пересоздавалось бы при каждой
    установке; со вторым окружение, созданное под Rosetta, получало бы
    метку `arm64` и считалось бы рабочим в обычном Терминале — ровно то, от
    чего метка защищает.

    Поэтому сверка — по тексту `install.sh`, как `test_install_tools_check_is_by_file`:
    - вне комментариев метка `venv/.photoprint-arch` встречается ровно в
      двух строках, и обе — в `step_venv`;
    - запись — строка ровно `uname -m > venv/.photoprint-arch`;
    - проверка — строка `if …`, в которой содержимое метки сравнивается с
      `"$(uname -m)"`;
    - в файле нет ни `arm64`, ни `x86_64` — даже в комментариях.
    """
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    lines = text.splitlines()
    code = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]
    # Тело `step_venv` — от её определения до первой `}` в начале строки.
    start = lines.index("step_venv() {")
    end = lines.index("}", start)
    body = [
        line.strip()
        for line in lines[start + 1 : end]
        if line.strip() and not line.strip().startswith("#")
    ]
    marked = [line for line in code if ".photoprint-arch" in line]

    assert len(marked) == 2, marked
    assert all(line in body for line in marked), (marked, body)
    assert marked[1] == ARCH_MARK_WRITE, marked
    assert marked[0].startswith("if "), marked
    assert ARCH_MARK_CHECK in marked[0], marked
    assert ARCH_LITERAL.findall(text) == []


def test_install_default_url() -> None:
    """I3: адрес архива по умолчанию — дословно `codeload…/tar.gz/refs/heads/main` из § 4.8.

    Все прочие тесты подменяют адрес через `PHOTOPRINT_TARBALL`, поэтому
    опечатка или другая ветка в адресе по умолчанию прошла бы их все, а у
    заказчика установка упала бы на «скачивании» (или поставила бы не ту
    ветку). Сверка — по тексту `install.sh`.
    """
    text = (REPO / "install.sh").read_text(encoding="utf-8")

    assert DEFAULT_TARBALL in text


def test_install_last_line_calls_main() -> None:
    """§ 4.8, I10: последняя непустая строка `install.sh` — ровно `main "$@"; exit`.

    Вызов `main` — последним: у оборванного при скачивании скрипта этой
    строки нет, и bash ничего не исполняет. `exit` на той же строке — bash
    выходит сразу после `main` и больше ничего не читает из stdin.
    """
    lines = [
        line
        for line in (REPO / "install.sh").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert lines[-1] == MAIN_CALL.decode("utf-8")


def test_install_steps_called_as_bare_lines() -> None:
    """I10: каждый шаг вызывается голой строкой — не из `if`, `||`, `&&`, `!`; вызовов столько же, сколько `STEP="…"`.

    `set -e` не действует в условии `if`/`while`, в левой части `||` и
    `&&`, после `!`: шаг, вызванный так, терял бы его для всех своих
    команд, и его сбой проходил бы молча (ломатели I5f и I10b задачи 1.7
    выживали и под пробами). Поэтому каждая строка кода, где вызывается
    `step_…` (не определение `step_…() {` и не комментарий), после
    `strip()` — ровно имя одной из функций шагов, и таких строк столько
    же, сколько строк `STEP="<непусто>"`: у каждого шага своя функция.
    Значения `STEP` — шесть имён I10 по порядку, с «библиотекой USB» (I8)
    последней.
    """
    lines = (REPO / "install.sh").read_text(encoding="utf-8").splitlines()
    code = [line for line in lines if not line.strip().startswith("#")]
    defined = [match.group(1) for match in map(STEP_DEF.match, code) if match]
    calls = [
        line.strip()
        for line in code
        if STEP_CALL.search(line) and not STEP_DEF.match(line)
    ]
    steps = [
        match.group(1)
        for match in (STEP_ASSIGN.fullmatch(line.strip()) for line in code)
        if match
    ]

    assert [call for call in calls if call not in defined] == [], calls
    assert len(calls) == len(steps), (calls, steps)
    assert steps == STEP_NAMES


def test_scripts_start_with_bin_bash() -> None:
    """I12, § 4.7: первая строка `install.sh` и `launcher.command` — ровно `#!/bin/bash`.

    Ярлык Терминал исполняет по первой строке: `#!/usr/bin/env bash` взял
    бы bash из `PATH` (у заказчика с Homebrew — bash 5), и ярлык,
    работающий там, мог бы не работать в `/bin/bash` 3.2 — и наоборот.
    Установщик приходит в `bash` через stdin, но строка та же, чтобы оба
    скрипта писались и проверялись под один и тот же bash 3.2.
    """
    for script in SCRIPTS:
        first = script.read_text(encoding="utf-8").splitlines()[0]
        assert first == "#!/bin/bash", script.name


def test_scripts_parse_with_bash_32() -> None:
    """I12: `/bin/bash` этого мака — 3.2, и он разбирает оба скрипта без ошибок (`bash -n`).

    Предпосылка: `/bin/bash --version` говорит `version 3.2` — иначе
    проверка разбора ничего не говорила бы о bash заказчика. `bash -n`
    читает скрипт целиком, ничего не исполняя: синтаксис bash 4, которого
    нет в 3.2, дал бы код ≠ 0. stderr пуст — без предупреждений разбора.
    """
    version = subprocess.run(
        ["/bin/bash", "--version"],
        env={"PATH": INSTALL_PATH},
        capture_output=True,
        timeout=30,
    )
    assert version.returncode == 0, _report(version)
    assert "version 3.2" in version.stdout.decode("utf-8"), _report(version)

    for script in SCRIPTS:
        parsed = subprocess.run(
            ["/bin/bash", "-n", str(script)],
            env={"PATH": INSTALL_PATH},
            capture_output=True,
            timeout=30,
        )
        assert parsed.returncode == 0, _report(parsed)
        assert parsed.stderr == b"", _report(parsed)


def test_scripts_avoid_bash4_features() -> None:
    """I12: в `install.sh` и `launcher.command` нет ни одной конструкции bash 4 из фикстуры брифа 2.6.

    `bash -n` ловит не всё: `mapfile`, `readarray`, `declare -A`,
    `timeout`, `realpath`, `grep -P` для разбора — обычные команды, а в
    bash 3.2 и BSD-утилитах мака их нет, и скрипт упал бы только у
    заказчика, на своём шаге. Поэтому строки фикстуры ищутся в тексте
    обоих файлов целиком; отрицательный индекс — ещё и с любым именем
    массива (`NEGATIVE_INDEX`); `grep -P` и ассоциативный массив — ещё и с
    соседними флагами: `grep -oP`, `grep -Po`, `local -A`, `declare -gA`,
    `typeset -A` (`GREP_PERL_FLAG`, `ASSOC_ARRAY_FLAG`, доработка
    контролёра к задаче 2.6).
    """
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        found = [feature for feature in BASH4_FEATURES if feature in text]
        assert found == [], f"{script.name}: {found}"
        assert NEGATIVE_INDEX.findall(text) == [], script.name
        # I12: `grep -P` и ассоциативный массив и с другими флагами рядом
        # (`GREP_PERL_FLAG`, `ASSOC_ARRAY_FLAG`): строки фикстуры не ловят
        # `grep -oP`, `local -A`, `declare -gA`.
        variants = [
            match.group(0)
            for pattern in (GREP_PERL_FLAG, ASSOC_ARRAY_FLAG)
            for match in pattern.finditer(text)
        ]
        assert variants == [], f"{script.name}: {variants}"


def test_bash4_flag_patterns_catch_variants() -> None:
    """I12: образцы `GREP_PERL_FLAG` и `ASSOC_ARRAY_FLAG` ловят `grep -P` и ассоциативный массив с любыми соседними флагами и не ловят разрешённое.

    `test_scripts_avoid_bash4_features` доказывает отсутствие конструкций
    только тем, что образец ничего не нашёл; сломанный образец не нашёл бы
    ничего и в скрипте с `grep -oP`. Поэтому сами образцы проверяются на
    строках: каждое написание из доработки контролёра к задаче 2.6 и
    буквальные строки фикстуры пойманы; разрешённые в bash 3.2 и BSD
    `grep` — `grep -o`, `grep -E`, `local` без флагов, `declare -a`
    (обычный массив), `typeset -i` — нет.
    """
    grep_perl = [
        "grep -P 'x' f",
        "grep -oP '\\d+' f",
        "grep -Po '\\d+' f",
        "grep -E -P 'x' f",
        'ids=$(echo "$v" | grep -oP "\\d+")',
    ]
    assoc_array = [
        "declare -A map",
        "local -A map",
        "declare -gA map",
        "typeset -A map",
        "declare -r -A map",
    ]
    allowed = [
        "grep -o 'x' f",
        "grep -E 'x' f",
        "grep -F -x 'x' f",
        "local DEV",
        "local tools=no",
        "local -r name=x",
        "declare -a list",
        "typeset -i n",
    ]

    assert [line for line in grep_perl if not GREP_PERL_FLAG.search(line)] == []
    assert [line for line in assoc_array if not ASSOC_ARRAY_FLAG.search(line)] == []
    assert [
        line
        for line in allowed
        if GREP_PERL_FLAG.search(line) or ASSOC_ARRAY_FLAG.search(line)
    ] == []


def test_scripts_brace_vars_before_non_ascii() -> None:
    """I12: в байтах обоих скриптов нет `$имя` прямо перед байтом ≥ 0x80 — рядом с русским текстом только `${имя}`.

    В локали UTF-8 Терминала заказчика bash 3.2 считает первый байт
    не-ASCII символа частью имени переменной: `«$STEP»` читается как
    другая, незаданная переменная, и под `set -u` bash падает с «unbound
    variable» вместо текста для заказчика (проверено в задаче 1.7). Тест
    локали ловит это только в тех строках, до которых доходит; здесь —
    каждое место обоих файлов. Проверка — по байтам: так она не зависит
    от того, как bash разбирает строку.
    """
    for script in SCRIPTS:
        data = script.read_bytes()
        lines = data.splitlines()
        # Номер и текст строки каждого найденного места — для сообщения теста.
        hits = [
            (
                data.count(b"\n", 0, match.start()) + 1,
                lines[data.count(b"\n", 0, match.start())].decode("utf-8", "replace").strip(),
            )
            for match in BARE_VAR_BEFORE_NON_ASCII.finditer(data)
        ]
        assert hits == [], f"{script.name}: {hits}"


def test_truncated_script_does_nothing(tmp_path: Path, home: Path, cwd: Path) -> None:
    """I10, § 4.8: скрипт, оборванный перед последней строкой, ничего не делает и ничего не пишет.

    Если `curl … | bash` оборвался посередине, bash получает только часть
    скрипта. Весь код — в функциях, а вызов `main` — последняя строка,
    поэтому без неё ничего не исполняется: ни `.photoprint`, ни папки на
    Рабочем столе, ни строки вывода.
    """
    lines = (REPO / "install.sh").read_bytes().splitlines(keepends=True)
    # Предпосылка: последняя строка файла — ровно вызов `main` (§ 4.8).
    assert lines[-1].rstrip(b"\n") == MAIN_CALL
    script = b"".join(lines[:-1])

    tarball = build_tarball(tmp_path / "dist")
    result = run_install(home, tarball, cwd=cwd, script=script)

    assert result.returncode == 0, _report(result)
    assert result.stdout == b"", _report(result)
    assert not (home / ".photoprint").exists()
    assert not (home / "Desktop").exists()


def test_launcher_without_client(tmp_path: Path, home: Path, cwd: Path) -> None:
    """L2: клиента в `HOME/.photoprint` нет → «Клиент не установлен…» в stdout, код 1.

    Копия ярлыка лежит в папке с кириллицей, `HOME` пуст — так бывает,
    если папку с ярлыком перенесли на другой мак без установки.
    """
    folder = tmp_path / "папка"
    folder.mkdir()
    launcher = folder / LAUNCHER_NAME
    # Байты, как `cat` в I7: ярлык у заказчика получается именно так.
    launcher.write_bytes((APP / "launcher.command").read_bytes())

    result = subprocess.run(
        ["/bin/bash", str(launcher)],
        cwd=cwd,
        env={"HOME": str(home), "PATH": INSTALL_PATH},
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 1, _report(result)
    assert result.stdout.decode("utf-8") == NOT_INSTALLED + "\n"
    assert os.listdir(home) == []


def test_launcher_broken_venv_with_app(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """L2: код клиента на месте, а `venv/bin/python` — висячая ссылка → «Клиент не установлен…», код 1.

    Первая половина проверки L2 сама по себе. Так выглядит клиент после
    переустановки Command Line Tools: ссылка окружения указывает на
    Python, которого больше нет (L2 называет этот случай прямо). Без
    проверки `-x` ярлык дошёл бы до `exec` и показал бы заказчику «No such
    file or directory» вместо текста L2.
    """
    tarball = build_tarball(tmp_path / "dist")
    installed = run_install(home, tarball, cwd=cwd)
    assert installed.returncode == 0, _report(installed)
    python = home / ".photoprint" / "venv" / "bin" / "python"
    python.unlink()
    python.symlink_to(tmp_path / "удалённый python3")
    # Предпосылка: код цел, ссылка есть, а того, на что она указывает, нет.
    assert (home / ".photoprint" / "app" / "photoprint" / "__init__.py").is_file()
    assert os.path.lexists(python)
    assert not python.exists()

    result = _run_launcher(desktop_folder / LAUNCHER_NAME, home, cwd)

    assert result.returncode == 1, _report(result)
    assert result.stdout.decode("utf-8") == NOT_INSTALLED + "\n", _report(result)


def test_launcher_venv_without_app(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """L2: окружение Python рабочее, а кода клиента нет → «Клиент не установлен…», код 1.

    Вторая половина проверки L2 сама по себе: `.photoprint/app` удалён
    после удачной установки. Без проверки `__init__.py` ярлык упал бы на
    `cd` в несуществующий каталог и ничего не сказал бы заказчику в
    stdout.
    """
    tarball = build_tarball(tmp_path / "dist")
    installed = run_install(home, tarball, cwd=cwd)
    assert installed.returncode == 0, _report(installed)
    shutil.rmtree(home / ".photoprint" / "app")
    # Предпосылка: python окружения запускается.
    runs = _venv_python(home, "import sys")
    assert runs.returncode == 0, _report(runs)

    result = _run_launcher(desktop_folder / LAUNCHER_NAME, home, cwd)

    assert result.returncode == 1, _report(result)
    assert result.stdout.decode("utf-8") == NOT_INSTALLED + "\n", _report(result)


def test_launcher_app_without_package(
    tmp_path: Path, home: Path, cwd: Path, desktop_folder: Path
) -> None:
    """L2: каталог `.photoprint/app` есть, а пакета `app/photoprint` нет → «Клиент не установлен…», код 1.

    Половина проверки L2 «код клиента» — по файлу
    `app/photoprint/__init__.py`, а не по каталогу `app`: каталог без
    пакета (заказчик или чистильщик удалил часть файлов) проходил бы
    проверку `-d app`, и ярлык дошёл бы до `exec` — заказчик увидел бы
    трассировку Python «No module named photoprint» вместо текста L2
    (мутант `-d app` выживал в задаче 1.7). Удалён только
    `.photoprint/app/photoprint`; окружение рабочее.
    """
    tarball = build_tarball(tmp_path / "dist")
    installed = run_install(home, tarball, cwd=cwd)
    assert installed.returncode == 0, _report(installed)
    app = home / ".photoprint" / "app"
    shutil.rmtree(app / "photoprint")
    # Предпосылка: каталог `app` остался, python окружения запускается.
    assert app.is_dir()
    runs = _venv_python(home, "import sys")
    assert runs.returncode == 0, _report(runs)

    result = _run_launcher(desktop_folder / LAUNCHER_NAME, home, cwd)

    assert result.returncode == 1, _report(result)
    assert result.stdout.decode("utf-8") == NOT_INSTALLED + "\n", _report(result)


def test_launcher_relative_path_with_cdpath(
    tmp_path: Path,
    home: Path,
    cwd: Path,
    desktop_folder: Path,
    started: List[Proc],
) -> None:
    """L1, L3, M7: ярлык по относительному пути при заданном `CDPATH` берёт свою папку; `SIGTERM` ярлыку останавливает сервер.

    - L1: ярлык запущен как `/bin/bash "Печать картинок/Печать
      картинок.command"` из `~/Desktop`, а `CDPATH` называет `~/Desktop`.
      Относительный путь папки ярлыка должен стать абсолютным до
      `cd "$R/app"`, а строка, которую `cd` печатает при найденном через
      `CDPATH` каталоге, не должна попасть в путь папки (L1: «`CDPATH` не
      мешают»). Клиент отвечает папкой на Рабочем столе и её картинкой.
    - L3, M7: после `SIGTERM` процессу ярлыка порт клиента свободен. С
      `exec` сигнал получает сам Python; без него сервер остался бы
      сиротой (`_assert_server_stopped`).
    """
    tarball = build_tarball(tmp_path / "dist")
    installed = run_install(home, tarball, cwd=cwd)
    assert installed.returncode == 0, _report(installed)
    link_test_deps(home)
    make_jpeg(desktop_folder / "good.jpg", 576, 300)
    desktop = home / "Desktop"
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(exist_ok=True)

    base = free_base()
    proc = Proc.start(
        cwd=desktop,
        args=["/bin/bash", f"{FOLDER_NAME}/{LAUNCHER_NAME}"],
        env_extra={
            "HOME": str(home),
            "PHOTOPRINT_PORT": str(base),
            "CDPATH": str(desktop),
            # Временные файлы клиента — в каталоге теста, а не в `HOME`,
            # куда их положил бы `Proc.start` по умолчанию (рядом с `cwd`).
            "TMPDIR": str(tmpdir),
        },
    )
    started.append(proc)
    proc.wait_for(READY.format(url=_url(base)), timeout=READY_TIMEOUT)
    status, body = get_json(_url(base) + "api/images")
    assert status == 200, body
    assert body["folder"] == str(desktop_folder.resolve())
    assert [image["name"] for image in body["images"]] == ["good.jpg"]

    proc.stop()
    _assert_server_stopped(proc, base)


def test_launcher_relative_path_without_cdpath(
    tmp_path: Path,
    home: Path,
    cwd: Path,
    desktop_folder: Path,
    started: List[Proc],
) -> None:
    """L1: ярлык по относительному пути без `CDPATH` берёт свою папку.

    `/bin/bash "Печать картинок/Печать картинок.command"` из `~/Desktop`,
    `CDPATH` пуст (для `cd` bash пустой `CDPATH` — то же, что его нет).
    Относительный путь папки ярлыка должен стать абсолютным до
    `cd "$R/app"`: после него тот же относительный путь уже не найдётся, и
    ярлык молча вышел бы с кодом 1 (`set -e` на присваивании) — заказчик,
    запустивший ярлык из Терминала, увидел бы пустое окно без единой
    строки. В тесте с `CDPATH` = `~/Desktop` этот порядок не виден: `cd`
    находит папку через `CDPATH` из любого каталога.
    """
    tarball = build_tarball(tmp_path / "dist")
    installed = run_install(home, tarball, cwd=cwd)
    assert installed.returncode == 0, _report(installed)
    link_test_deps(home)
    make_jpeg(desktop_folder / "good.jpg", 576, 300)
    desktop = home / "Desktop"
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(exist_ok=True)

    base = free_base()
    proc = Proc.start(
        cwd=desktop,
        args=["/bin/bash", f"{FOLDER_NAME}/{LAUNCHER_NAME}"],
        env_extra={
            "HOME": str(home),
            "PHOTOPRINT_PORT": str(base),
            # Пустой: даже если `CDPATH` задан в окружении pytest, `cd`
            # ярлыка ищет только от текущего каталога.
            "CDPATH": "",
            # Временные файлы клиента — в каталоге теста (как в тесте выше).
            "TMPDIR": str(tmpdir),
        },
    )
    started.append(proc)
    proc.wait_for(READY.format(url=_url(base)), timeout=READY_TIMEOUT)
    status, body = get_json(_url(base) + "api/images")
    assert status == 200, body
    assert body["folder"] == str(desktop_folder.resolve())
    assert [image["name"] for image in body["images"]] == ["good.jpg"]

    proc.stop()
    _assert_server_stopped(proc, base)


def test_end_to_end_install_launch_move_reinstall(
    tmp_path: Path,
    home: Path,
    cwd: Path,
    desktop_folder: Path,
    started: List[Proc],
) -> None:
    """Сквозной тест пули 1 (§ 7.2): установка → ярлык → список → перенос папки → повторная установка.

    1. Установка без pip; пакеты клиента — из тестового окружения через
       `test-deps.pth`; в папку на Рабочем столе кладётся JPEG 576×300.
    2. Ярлык из папки запускает клиент (L3), тот отвечает своей папкой и
       этой картинкой, пригодной к печати.
    3. Папка перенесена в другое место — ярлык из неё запускает клиент для
       новой папки (L1), и клиент записал её путь (M4).
    4. Повторная установка обновляет ярлык в перенесённой папке и не
       создаёт новую папку на Рабочем столе (I1, I11).

    После каждой остановки ярлыка порт клиента свободен: `SIGTERM` ярлыку
    через `exec` дошёл до самого сервера (L3, M7).

    Ярлык запускается `/bin/bash`, как его запускает Терминал по двойному
    клику; браузер не открывается (`PHOTOPRINT_OPEN=echo`).
    """
    tarball = build_tarball(tmp_path / "dist")
    first = run_install(home, tarball, cwd=cwd)
    assert first.returncode == 0, _report(first)
    link_test_deps(home)
    make_jpeg(desktop_folder / "good.jpg", 576, 300)

    base = free_base()
    proc = Proc.start(
        cwd=cwd,
        args=["/bin/bash", str(desktop_folder / LAUNCHER_NAME)],
        env_extra={"HOME": str(home), "PHOTOPRINT_PORT": str(base)},
    )
    started.append(proc)
    proc.wait_for(READY.format(url=_url(base)), timeout=READY_TIMEOUT)
    status, body = get_json(_url(base) + "api/images")
    assert status == 200, body
    assert body["folder"] == str(desktop_folder.resolve())
    assert [image["name"] for image in body["images"]] == ["good.jpg"]
    assert body["images"][0]["ok"] is True

    proc.stop()
    # L3, M7: `SIGTERM` ярлыку остановил сам сервер (`exec`), сирот нет.
    _assert_server_stopped(proc, base)
    moved = tmp_path / "Перенесено сюда"
    desktop_folder.rename(moved)
    # Новое основание: порт прежнего сервера ещё может быть в `TIME_WAIT`, а
    # тесту нужен заранее известный адрес «Готово…».
    base = free_base()
    proc = Proc.start(
        cwd=cwd,
        args=["/bin/bash", str(moved / LAUNCHER_NAME)],
        env_extra={"HOME": str(home), "PHOTOPRINT_PORT": str(base)},
    )
    started.append(proc)
    proc.wait_for(READY.format(url=_url(base)), timeout=READY_TIMEOUT)
    status, body = get_json(_url(base) + "api/images")
    assert status == 200, body
    assert body["folder"] == str(moved.resolve())
    assert [image["name"] for image in body["images"]] == ["good.jpg"]
    assert (home / ".photoprint" / "folder").read_text(encoding="utf-8") == str(
        moved.resolve()
    )

    proc.stop()
    _assert_server_stopped(proc, base)
    # Ярлык прежней версии: по байтам после установки видно, что она его
    # действительно перезаписала, а не оставила как было.
    (moved / LAUNCHER_NAME).write_bytes(b"#!/bin/bash\necho old launcher\n")
    second = run_install(home, tarball, cwd=cwd)
    assert second.returncode == 0, _report(second)
    assert (moved / LAUNCHER_NAME).read_bytes() == (APP / "launcher.command").read_bytes()
    assert not desktop_folder.exists()
