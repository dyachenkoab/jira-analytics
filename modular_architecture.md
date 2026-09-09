# Пошаговый план реализации модульных вкладок

> План предназначен для выполнения другим AI по этапам. После каждого этапа исполнитель обязан остановиться, показать diff и результаты проверок и дождаться валидации владельца задачи. Коммиты без отдельной просьбы не делать.

## Цель

Сделать все вкладки Jira Analytics модулями одного формата. Встроенные и пользовательские модули должны обнаруживаться при запуске, добавляться в `TabBar` без изменения `Main.qml` и работать только через ограниченный Python API. Пользовательский Python-код и hot reload в первую версию не входят.

## Утверждённая архитектура

- Модуль состоит из каталога с `module.json` и одним корневым QML-файлом.
- Встроенные модули находятся в локальном `modules/` при запуске из checkout и в `/usr/share/jira-analytics/modules/` после установки DEB.
- Пользовательские модули находятся в `~/.local/share/jira-analytics/modules/`.
- Все пять текущих вкладок переводятся на тот же механизм, что и пользовательские.
- Поддерживаемый контракт QML-модуля состоит только из `api` (`ModuleApi`) и `theme`, переданных при создании через `Loader.setSource()`.
- Текущий `Backend` остаётся внутренней реализацией приложения и не является публичным API модулей.
- Ошибка одного пользовательского модуля не должна ронять приложение.
- Встроенный модуль нельзя заменить пользовательским модулем с тем же ID.
- QML не является песочницей. Устанавливать можно только доверенные модули.

`backend` пока остаётся context property оболочки login/settings, поэтому технически доверенный внешний QML может его увидеть через общий engine context. Это не поддерживаемый API и на него нельзя рассчитывать. Реальная изоляция требует отдельного QML engine/context и не входит в первую версию.

## Ограничения первой версии

- Нет `module.py` и загрузки произвольного Python-кода.
- Нет hot reload.
- Нет включения и отключения модулей в UI.
- Нет зависимостей между модулями.
- Нет настроек модулей в общем settings dialog.
- Нет отдельного механизма локализации внешних модулей.
- Нет версионирования модулей кроме проверки `apiVersion: 1`.
- Не добавлять сторонние зависимости: достаточно `json`, `dataclasses`, `pathlib`, PyQt5 и существующих классов проекта.

## Важное состояние рабочего дерева

Перед началом каждого этапа выполнить `git status --short` и изучить relevant diff. В рабочем дереве уже могут быть изменения пользователя, особенно в:

- `jira_analytics_gui.py`;
- `jira_period_analytics.py`;
- `qml/ChartsTab.qml`;
- `test_jira_analytics.py`.

Не откатывать и не перезаписывать эти изменения. Миграцию Charts строить поверх текущего `chartSlotsModel`, `build_chart_slots()` и ролей `chartSlotLabel`.

## Целевые файлы

### Создать

- `jira_modules.py` — dataclass манифеста, валидация и обнаружение модулей без зависимости от PyQt.
- `modules/target_end/module.json`
- `modules/target_end/TargetEndTab.qml`
- `modules/period_analytics/module.json`
- `modules/period_analytics/PeriodAnalyticsTab.qml`
- `modules/backlog_health/module.json`
- `modules/backlog_health/BacklogTab.qml`
- `modules/assignees/module.json`
- `modules/assignees/AssigneesTab.qml`
- `modules/charts/module.json`
- `modules/charts/ChartsTab.qml`

### Изменить

- `jira_analytics_gui.py` — `ModuleApi`, модель вкладок, discovery и регистрация context properties.
- `qml/Main.qml` — динамические кнопки и `Loader`, тема, сообщения об ошибках.
- `test_jira_analytics.py` — assert-тесты discovery и фасада, вызовы новых тестов в `__main__`.
- `scripts/extract_qml.py` не изменять: передавать ему полный список QML-файлов из оболочки команды.
- `debian/install` — установка `jira_modules.py` и каталога `modules/`.
- `translations/messages_qml.pot`
- `translations/ru/LC_MESSAGES/jira_qt.po`
- `translations/ru/LC_MESSAGES/jira_qt.mo`
- этот файл — после реализации заменить плановые статусы фактическими результатами, не удаляя описание API.

### Удалить после успешного переноса

- `qml/TargetEndTab.qml`
- `qml/PeriodAnalyticsTab.qml`
- `qml/BacklogTab.qml`
- `qml/AssigneesTab.qml`
- `qml/ChartsTab.qml`

Удалять исходные QML только в этапе динамической загрузки, после того как их копии в `modules/` проходят QML-проверку.

## Публичные контракты

### `module.json`

Минимальный валидный манифест:

```json
{
  "id": "charts",
  "title": "Charts",
  "qml": "ChartsTab.qml",
  "order": 40,
  "apiVersion": 1
}
```

Правила:

- `id`: строка по regex `^[a-z][a-z0-9_]*$`, уникальная среди всех загруженных модулей.
- `title`: непустая строка; встроенное название переводится существующим gettext context `Main.qml`, внешнее отображается как задано.
- `qml`: непустой относительный путь; абсолютный путь и выход через `..`/symlink за каталог модуля запрещены.
- `order`: целое число, `bool` не считать числом.
- `apiVersion`: строго `1`.
- Неизвестные поля игнорируются, чтобы добавление метаданных не ломало старый loader.

Порядок встроенных модулей:

| ID | Title | Order |
|---|---|---:|
| `target_end` | `Target end` | 0 |
| `period_analytics` | `Period analytics` | 10 |
| `backlog_health` | `Backlog health` | 20 |
| `assignees` | `Assignees` | 30 |
| `charts` | `Charts` | 40 |

### `jira_modules.py`

Зафиксировать интерфейс:

```python
MODULE_API_VERSION = 1

@dataclass(frozen=True)
class ModuleDescriptor:
    module_id: str
    title: str
    qml_path: Path
    order: int
    builtin: bool


def discover_modules(
    builtin_dir: Path,
    user_dir: Path | None = None,
) -> tuple[list[ModuleDescriptor], list[str]]:
    ...
```

`discover_modules()` всегда обрабатывает встроенный каталог первым. Возвращаемые модули сортируются по `(order, module_id)`. Ошибки возвращаются как человекочитаемые строки с путём к проблемному manifest; функция не печатает и не логирует сама.

### Роли `moduleTabsModel`

Использовать существующий `DictListModel` с ролями:

```text
moduleId, title, qmlSource
```

`qmlSource` — строка из `QUrl.fromLocalFile(str(descriptor.qml_path)).toString()`. Порядок уже задан discovery, а признак `builtin` нужен только Python-коду, поэтому эти данные не публикуются в QML-модели. Имена `moduleId` и `qmlSource` выбраны специально, чтобы не конфликтовать со свойствами QML delegate/Loader.

### `ModuleApi`

Создать `ModuleApi(QObject)` в `jira_analytics_gui.py`. Фасад хранит ссылку на `Backend` и модели, но не копирует бизнес-логику.

Точная сигнатура конструктора:

```python
def __init__(
    self,
    backend: Backend,
    task_model: QObject,
    period_board_model: QObject,
    period_sprint_model: QObject,
    backlog_health_model: QObject,
    assignee_model: QObject,
    chart_slots_model: QObject,
    open_url: Callable[[QUrl], object] = QDesktopServices.openUrl,
    parent: QObject | None = None,
) -> None:
    ...
```

Вызвать `super().__init__(parent)`. Все шесть моделей передаются явно; фасад не достаёт private model fields из backend самостоятельно.

Read-only model properties с `constant=True`:

```text
taskModel
periodBoardModel
periodSprintModel
backlogHealthModel
assigneeModel
chartSlotsModel
```

Реактивные read-only properties и источники notify-сигналов:

| Property | Type | Backend signal |
|---|---|---|
| `checking` | `bool` | `checkingChanged` |
| `errorsText` | `str` | `errorsTextChanged` |
| `itemsText` | `str` | `itemsTextChanged` |
| `periodChecking` | `bool` | `periodCheckingChanged` |
| `periodStatusText` | `str` | `periodStatusTextChanged` |
| `periodErrorsText` | `str` | `periodErrorsTextChanged` |
| `periodFromCache` | `bool` | `periodFromCacheChanged` |
| `periodLastRefresh` | `str` | `periodLastRefreshChanged` |
| `periodKpi` | `QVariantMap` | `periodKpiChanged` |
| `selectedTeam` | `str` | `settingsChanged` |
| `backlogSort` | `str` | `backlogSortChanged` |
| `backlogAgeFilter` | `str` | `backlogAgeFilterChanged` |
| `backlogActiveStatuses` | `str` | `settingsChanged` |
| `forgottenAgeDays` | `int` | `settingsChanged` |
| `assigneeBoardNames` | `QVariantList` | `assigneeBoardNamesChanged` |
| `assigneeKpi` | `QVariantMap` | `assigneeKpiChanged` |

Публичные slots:

```python
@pyqtSlot()
def refreshTarget(self) -> None

@pyqtSlot(str)
def saveItems(self, text: str) -> None

@pyqtSlot()
def refreshPeriod(self) -> None

@pyqtSlot(str)
def selectTeam(self, team: str) -> None

@pyqtSlot(str)
def setBacklogSort(self, sort_key: str) -> None

@pyqtSlot(str)
def setAssigneeBoardFilter(self, name: str) -> None

@pyqtSlot(str, result=bool)
def openIssue(self, key: str) -> bool

@pyqtSlot(int, int, result=bool)
def openSprintReport(self, board_id: int, sprint_id: int) -> bool
```

Команды делегируют существующим методам backend. `openIssue()` принимает только Jira key, а не URL. `openSprintReport()` принимает только положительные ID. При неверном аргументе или пустом Jira URL методы возвращают `False` и ничего не открывают.

URL отчёта спринта должен сохранить текущий формат:

```text
{jira_url}/secure/RapidBoard.jspa#?rapidView={board_id}&view=reporting&chart=sprintRetrospective&sprint={sprint_id}
```

## Протокол выполнения и валидации

Для каждого этапа:

1. Пометить этап `in_progress` в своём task list.
2. Прочитать все затрагиваемые файлы и текущий diff.
3. Написать минимальный падающий тест до production-кода, если этап меняет Python-логику.
4. Запустить тест и зафиксировать ожидаемое падение.
5. Реализовать минимальное изменение.
6. Запустить целевые тесты и обязательные проверки этапа.
7. Просмотреть `git diff --check` и relevant diff.
8. Не делать commit.
9. Остановиться и передать владельцу задачи: список файлов, краткое описание решений, команды и результаты, известные ограничения.
10. Не начинать следующий этап до явного подтверждения владельца.

---

## Этап 1. Manifest discovery как чистая Python-логика

**Результат:** приложение ещё работает со статическими вкладками, но умеет безопасно находить и сортировать descriptors.

**Файлы:**

- создать `jira_modules.py`;
- изменить `test_jira_analytics.py`.

### Шаг 1.1. Добавить падающие тесты discovery

Добавить импорт `ModuleDescriptor, discover_modules` и тесты с `TemporaryDirectory`:

```python
def write_module(root: Path, name: str, manifest: dict, qml: str = "import QtQuick 2.12\nItem {}\n") -> Path:
    module_dir = root / name
    module_dir.mkdir(parents=True)
    (module_dir / manifest.get("qml", "Module.qml")).write_text(qml, encoding="utf-8")
    (module_dir / "module.json").write_text(json.dumps(manifest), encoding="utf-8")
    return module_dir
```

Обязательные сценарии:

- два валидных встроенных модуля сортируются по `(order, id)`;
- отсутствующий встроенный каталог даёт ошибку и пустой список;
- отсутствующий пользовательский каталог не считается ошибкой;
- malformed JSON пропускается и даёт одну ошибку;
- отсутствующее обязательное поле пропускает модуль;
- неверный тип каждого поля пропускает модуль;
- `apiVersion != 1` пропускает модуль;
- отсутствующий QML пропускает модуль;
- абсолютный QML path и `../outside.qml` пропускаются;
- symlink на QML вне module directory пропускается;
- duplicate ID среди встроенных оставляет первый модуль;
- пользовательский duplicate ID не заменяет встроенный;
- два пользовательских duplicate ID оставляют первый в стабильном порядке;
- неизвестные manifest fields не мешают загрузке.

Добавить вызовы новых тестов в ручной runner под `if __name__ == "__main__"`.

### Шаг 1.2. Подтвердить RED

Запустить:

```bash
python3 test_jira_analytics.py
```

Ожидаемый результат: импорт `jira_modules` или новых символов падает. Не продолжать, если тест падает по другой причине.

### Шаг 1.3. Реализовать discovery

Требования к реализации:

- использовать только stdlib;
- обходить только непосредственные дочерние каталоги, отсортированные по имени;
- отсутствующий built-in root вернуть как одну ошибку, отсутствующий user root игнорировать;
- каталог без `module.json` игнорировать без warning;
- читать JSON как UTF-8 и требовать object верхнего уровня;
- проверять `type(order) is int`, чтобы исключить `bool`;
- вычислять `module_dir.resolve()` и `qml_path.resolve()`;
- проверять принадлежность QML каталогу через `qml_path.is_relative_to(module_dir)`;
- хранить `seen_ids`, обрабатывая built-in root перед user root;
- не падать из-за одного модуля;
- не создавать каталоги на диске.

Не добавлять `ModuleLoader` class: одна чистая функция достаточна.

### Шаг 1.4. Подтвердить GREEN

```bash
python3 test_jira_analytics.py
python3 -m py_compile jira_modules.py
git diff --check
```

### Контрольная точка 1

Передать владельцу diff только `jira_modules.py` и `test_jira_analytics.py`. Владелец проверяет порядок приоритетов, path traversal, точность ошибок и отсутствие PyQt в discovery.

---

## Этап 2. Ограниченный `ModuleApi`

**Результат:** фасад покрыт тестами, но QML пока продолжает использовать текущие context properties.

**Файлы:**

- изменить `jira_analytics_gui.py`;
- изменить `test_jira_analytics.py`.

### Шаг 2.1. Добавить fake backend для тестов фасада

Не поднимать Jira workers и не создавать реальное окно. Сделать минимальный `QObject` fake с нужными сигналами, значениями и списком вызовов. Модели можно передать как `QObject()` sentinels.

Проверить:

- каждая model property возвращает тот же объект;
- каждая state property читает актуальное значение fake backend, а не snapshot;
- backend signal вызывает соответствующий signal фасада;
- каждый простой slot делегирует ровно один вызов с ожидаемыми аргументами;
- `refreshTarget()` вызывает `backend.refresh(False)`;
- `openIssue("ABC-123")` строит `/browse/ABC-123` и возвращает `True`;
- `openIssue()` возвращает `False` для пустой строки, URL, JQL, lowercase key и при пустом Jira URL;
- `openSprintReport()` возвращает `False` при `board_id <= 0`, `sprint_id <= 0` и пустом Jira URL;
- валидный sprint report URL совпадает с утверждённым форматом.

Для проверки открытия URL не monkeypatch-ить Qt глобально. Передать в `ModuleApi.__init__` необязательный callable `open_url=QDesktopServices.openUrl`; в production используется default, в тесте — список вызовов. Это единственная разрешённая инъекция зависимости.

### Шаг 2.2. Подтвердить RED

```bash
python3 test_jira_analytics.py
```

Ожидается failure импорта `ModuleApi`.

### Шаг 2.3. Реализовать `ModuleApi`

Разместить класс рядом с `Backend`, до `AppController`.

Требования:

- не наследовать `Backend`;
- не дублировать вычисления KPI, фильтрацию и работу с потоками;
- model properties объявить constant;
- для state properties объявить собственные notify signals и соединить backend signals в `__init__`;
- signal forwarding не должен менять thread affinity и данные;
- действия должны вызывать публичные backend slots, кроме открытия URL;
- нормализовать Jira URL удалением завершающих `/`;
- для issue key использовать уже существующее правило проекта, не вводить второй несовместимый regex;
- не публиковать credentials, config, env, workers, timers и весь backend через property.

### Шаг 2.4. Подключить фасад в `AppController`

В `AppController.__init__` создать `self.module_api` после `self.backend`. Передать `task_model`, `period_board_model`, `period_sprint_model`, `backlog_health_model`, proxy-модель assignees и текущий `_chart_slots_model`; parent установить в `self`.

В `setup()` временно добавить context property:

```python
self.engine.rootContext().setContextProperty("moduleApi", self.module_api)
```

Старые context properties пока не удалять: они нужны статическим вкладкам до этапа 3.

В `cleanup()` установить `moduleApi` в `None` до `clearComponentCache()`.

### Шаг 2.5. Проверки

```bash
python3 test_jira_analytics.py
python3 -m py_compile jira_analytics_gui.py
git diff --check
```

### Контрольная точка 2

Владелец проверяет публичную поверхность API, forwarding всех notify-сигналов, отсутствие credentials и точные URL. Следующий этап не начинать без подтверждения.

---

## Этап 3. Перевести существующие QML на явные `api` и `theme`

**Результат:** вкладки ещё создаются статически, но больше не зависят от неявного scope `Main.qml`, `backend` и глобальных моделей.

**Файлы:**

- изменить `qml/Main.qml`;
- изменить пять `qml/*Tab.qml`;
- изменить сигнатуру `Backend.saveSettings()` и соответствующие тесты.

### Шаг 3.1. Создать theme object в `Main.qml`

Сохранить текущие цвета окна для login/settings. Добавить отдельный `QtObject`:

```qml
QtObject {
    id: moduleTheme
    readonly property color bg: root.bg
    readonly property color card: root.card
    readonly property color border: root.border
    readonly property color text: root.text
    readonly property color muted: root.muted
    readonly property color blue: root.blue
    readonly property color red: root.red
    readonly property color redSoft: root.redSoft
    readonly property color amber: root.amber
    readonly property color amberSoft: root.amberSoft
}
```

Не передавать `root` целиком.

### Шаг 3.2. Объявить контракт корня каждой вкладки

В каждом QML root добавить:

```qml
property var api
property var theme
```

При статическом создании передать:

```qml
TargetEndTab { api: moduleApi; theme: moduleTheme; ... }
```

Повторить для остальных вкладок.

### Шаг 3.3. Перевести `TargetEndTab.qml`

- удалить обе проверки `mainTabs.currentIndex`;
- заменить `backend` на `api`;
- заменить `taskModel` на `api.taskModel`;
- `backend.refresh(false)` заменить на `api.refreshTarget()`;
- `backend.openIssue(url)` заменить на `api.openIssue(key)`;
- заменить цвета `root.*` на `theme.*`;
- перенести `riskColor`, `riskBg`, `riskText` в локальные функции root вкладки;
- сохранить локальное состояние editor внутри вкладки.

### Шаг 3.4. Удалить обратную связь Main -> TargetEnd internals

Сейчас `Main.qml.saveSettings()` читает `itemsEditor.text` и `sidePanel.itemsTouched`, хотя эти ID принадлежат другому QML component scope.

Изменить `Backend.saveSettings()` и QML-вызов так, чтобы общий settings dialog больше не принимал `items_text/items_touched`. Список источников сохраняется только `TargetEndTab` через `api.saveItems(text)`. `saveCredentials()` на login screen не менять, если его собственный editor остаётся внутри `Main.qml`.

Новая сигнатура должна быть точной:

```python
@pyqtSlot(str, str, str, str, int, int, int, int, int, str, bool)
def saveSettings(
    self,
    jira_url: str,
    username: str,
    token: str,
    password: str,
    warn_days: int,
    first_delay: int,
    interval: int,
    timeout: int,
    forgotten_age_days: int,
    target_field: str,
    include_active_sprints: bool,
) -> None:
    ...
```

Добавить Python regression test: вызов `saveSettings()` сохраняет существующий `config.items` без доступа к QML и не запускает target refresh из-за списка.

### Шаг 3.5. Перевести аналитические вкладки

Для `PeriodAnalyticsTab.qml`:

- модели: `api.periodBoardModel`, `api.periodSprintModel`;
- команды: `api.refreshPeriod()`, `api.selectTeam(team)`, `api.openIssue(key)`, `api.openSprintReport(boardId, sprintId)`;
- не строить Jira URL в QML;
- для responsive columns использовать `periodAnalyticsTab.width`, не `root.width`.

Для `BacklogTab.qml`:

- модель: `api.backlogHealthModel`;
- команды: `api.setBacklogSort()`, `api.refreshPeriod()`, `api.openIssue(key)`;
- state брать только из `api`;
- сохранить текущую case-insensitive forgotten classification и chart-slot изменения пользователя не затрагивать.

Для `AssigneesTab.qml`:

- модель: `api.assigneeModel`;
- команды: `api.setAssigneeBoardFilter()`, `api.refreshPeriod()`, `api.openIssue(key)`;
- responsive columns вычислять от `assigneesTab.width`.

Для `ChartsTab.qml`:

- `sprintModel()` должен возвращать `api.chartSlotsModel`;
- сохранить роли `chartSlotLabel` и существующую подписку на `modelReset`;
- заменить только palette/theme dependencies, не переписывать canvas-графики;
- сохранить локальное `selectedTeams`.

Во всех пяти файлах заменить обращения к `root` из `Main.qml` (`root.card/border/text/...`) на `theme.*`. Не делать механическую замену локально объявленных ID; для ясности корневые ID вкладок называть `targetEndTab`, `periodAnalyticsTab`, `backlogTab`, `assigneesTab`, `chartsTab`.

### Шаг 3.6. Статические проверки зависимостей

После миграции следующие команды не должны находить совпадений в tab files, кроме локально обоснованных ID:

```bash
rg -n 'backend|mainTabs|taskModel|periodBoardModel|periodSprintModel|backlogHealthModel|assigneeModel|chartSlotsModel' qml/*Tab.qml
rg -n 'root\.(card|border|text|muted|blue|red|redSoft|amber|amberSoft|width)' qml/*Tab.qml
```

`api.taskModel` и другие свойства содержат искомые слова, поэтому первый результат просмотреть вручную: запрещены только голые глобальные имена и `backend`.

После каждого QML edit применить обязательный skill `qml-braces`.

### Шаг 3.7. Проверки этапа

```bash
python3 test_jira_analytics.py
python3 -m py_compile jira_analytics.py jira_period_analytics.py jira_analytics_gui.py jira_modules.py
git diff --check
```

Запустить offscreen QML smoke. Ошибка `module "QtQuick" is not installed` допустима в текущем окружении только после отсутствия более ранней ошибки `Expected token ...` или `ReferenceError`.

### Контрольная точка 3

Владелец проверяет каждую вкладку отдельно, особенно `TargetEndTab` settings coupling, Charts modelReset и отсутствие построения Jira URL в QML.

---

## Этап 4. Единый dynamic TabBar и встроенные manifests

**Результат:** все пять встроенных и внешние пользовательские вкладки загружаются одним механизмом.

**Файлы:**

- создать `modules/*` и manifests;
- изменить `jira_analytics_gui.py`;
- изменить `qml/Main.qml`;
- изменить `test_jira_analytics.py`;
- удалить старые `qml/*Tab.qml` после проверки копий.

### Шаг 4.1. Создать встроенные каталоги

Переместить каждый tab QML без изменения basename в соответствующий каталог `modules/<id>/`. Добавить manifests из таблицы порядка.

Не оставлять вторые копии после успешного переключения loader: дубли QML быстро расходятся.

### Шаг 4.2. Добавить тест built-in manifests

Тест должен вызвать discovery для repository `modules/` и проверить точный список:

```python
assert [module.module_id for module in modules] == [
    "target_end",
    "period_analytics",
    "backlog_health",
    "assignees",
    "charts",
]
assert errors == []
```

Тест обязан выполняться и из repository root, и при импорте файла через абсолютный `Path(__file__)`.

### Шаг 4.3. Определить каталоги runtime

Добавить функции рядом с `qml_path()`:

```python
def builtin_modules_path() -> Path:
    local = Path(__file__).resolve().parent / "modules"
    return local if local.exists() else Path("/usr/share/jira-analytics/modules")


def user_modules_path() -> Path:
    return Path.home() / ".local" / "share" / "jira-analytics" / "modules"
```

Не создавать пользовательский каталог автоматически.

### Шаг 4.4. Создать модель вкладок в `AppController`

В `__init__`:

- вызвать discovery;
- сохранить warnings;
- проверить, что присутствуют все пять встроенных IDs;
- создать `DictListModel(["moduleId", "title", "qmlSource"])`;
- импортировать `_c` из `translations` и для built-in title применить `_c("Main.qml", descriptor.title)`; внешний title оставить без изменения;
- заполнить `qmlSource` через `QUrl.fromLocalFile()`.

Обязательные ID проверять только по `found_builtin_ids = {module.module_id for module in modules if module.builtin}`. Пользовательский модуль не может компенсировать отсутствующий встроенный. Если отсутствует хотя бы один обязательный built-in ID, вывести список отсутствующих ID в stderr и вернуть `False` из `setup()`. Не запускать UI с частичным набором встроенных вкладок.

Не смешивать warnings модулей с Jira `errorsText` или `periodErrorsText`. Каждую строку discovery errors вывести в stderr. Для оболочки создать `moduleWarningsText`: пустая строка при отсутствии ошибок, иначе `_("Some modules were skipped: %s") % len(errors)`.

До `engine.load(Main.qml)` зарегистрировать:

```python
context.setContextProperty("moduleApi", self.module_api)
context.setContextProperty("moduleTabsModel", self.module_tabs_model)
context.setContextProperty("moduleWarningsText", self.module_warnings_text)
```

### Шаг 4.5. Preflight встроенных QML

До загрузки `Main.qml` создать `QQmlComponent` для каждого built-in `qmlSource` и проверить `status()/errors()`. Если обнаружена syntax/import error встроенного component, вывести ошибки в stderr и вернуть `False` из `setup()`.

Preflight не обещает обнаружить JavaScript/runtime error из `Component.onCompleted`; такая ошибка диагностируется engine/Loader уже после создания. Полная runtime-проверка каждого компонента не входит в первую версию.

Не preflight-ить пользовательские QML как fatal. Syntax/import/creation error, которая переводит Loader в `Loader.Error`, показывает placeholder соответствующей вкладки. Произвольная JavaScript runtime error может только попасть в stderr и не обязана менять статус Loader.

### Шаг 4.6. Заменить статический TabBar

Кнопки:

```qml
TabBar {
    id: mainTabs
    Layout.fillWidth: true

    Repeater {
        model: moduleTabsModel
        TabButton { text: title }
    }
}
```

Контент должен быть sibling-level элементом основного layout, не внутри delegate другой вкладки:

```qml
StackLayout {
    id: moduleStack
    currentIndex: mainTabs.currentIndex
    Layout.fillWidth: true
    Layout.fillHeight: true

    Repeater {
        model: moduleTabsModel

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Loader {
                id: moduleLoader
                anchors.fill: parent
                Component.onCompleted: setSource(qmlSource, {
                    "api": moduleApi,
                    "theme": moduleTheme
                })
            }

            Label {
                anchors.centerIn: parent
                visible: moduleLoader.status === Loader.Error
                text: qsTr("Failed to load module: %1").arg(title)
                color: moduleTheme.red
            }
        }
    }
}
```

Использовать только роль `qmlSource`; роль `source` не вводить.

Loader-элементы не деактивировать при переключении: локальное состояние Charts, ComboBox и раскрытых строк должно сохраняться.

### Шаг 4.7. Исправить progress banner

Условие `mainTabs.currentIndex >= 1` больше не является контрактом. Добавить helper, который безопасно получает текущий module ID через `moduleTabsModel.get(index)`. Скрывать period progress только для `target_end`, сохраняя текущее поведение; для внешних вкладок progress можно показывать.

Обработать ветви:

- модель пустая -> helper возвращает `""`;
- index вне диапазона -> `""`;
- выбран `target_end` -> banner скрыт;
- выбран любой другой модуль и `periodChecking` -> banner виден.

### Шаг 4.8. Удалить старые globals

После успешной миграции удалить из `setup()` context properties:

```text
taskModel
periodBoardModel
periodSprintModel
periodAllSprintModel
assigneeModel
backlogHealthModel
chartSlotsModel
```

Оставить внутренний `backend` для login/settings оболочки. Он не является API модулей.

Удалить симметричные cleanup-вызовы для удалённых context properties. `moduleApi` и `moduleTabsModel` очищать явно.

### Шаг 4.9. Runtime error UI

- Manifest warnings показать одной компактной строкой/панелью в оболочке, только если строка непустая.
- Не показывать абсолютные home paths постоянно; подробности остаются в stderr/log, UI может показывать количество и краткую причину.
- Loader error внешнего модуля показывает placeholder только в его вкладке.
- Ошибка внешнего модуля не меняет индексы уже созданной модели до перезапуска.

### Шаг 4.10. Проверки этапа

```bash
python3 test_jira_analytics.py
python3 -m py_compile jira_analytics.py jira_period_analytics.py jira_analytics_gui.py jira_modules.py
git diff --check
```

Обязательно:

- `qml-braces` для `qml/Main.qml` и всех `modules/**/*.qml`;
- offscreen smoke;
- ручной запуск с пятью встроенными модулями;
- временный пользовательский QML-модуль без Python;
- malformed user manifest;
- user duplicate `charts`;
- user QML с синтаксической ошибкой;
- временно переданный preflight built-in QML с синтаксической ошибкой возвращает `False` и пишет путь/ошибку в stderr;
- отсутствие одного обязательного built-in ID возвращает `False` и пишет его ID в stderr;
- отсутствие `~/.local/share/jira-analytics/modules`.

Временные тестовые пользовательские модули удалить после проверки; не добавлять их в repository.

### Контрольная точка 4

Владелец проверяет структуру sibling-level layout, Loader scope, сохранение состояния вкладок, приоритет built-ins и отсутствие старых globals.

---

## Этап 5. Поставка, переводы и документация API

**Результат:** feature устанавливается DEB-пакетом и имеет достаточную документацию для автора внешней вкладки.

**Файлы:**

- изменить `debian/install`;
- обновить gettext artifacts;
- дополнить этот документ фактическим API и примером;
- при наличии package-build workflow выполнить его.

### Шаг 5.1. Обновить DEB install

Добавить:

```text
jira_modules.py usr/lib/python3/dist-packages/
modules usr/share/jira-analytics/
```

Удалить отдельные строки установки старых `qml/*Tab.qml`. `qml/Main.qml` оставить в `/usr/share/jira-analytics/qml/`.

Проверить итоговую структуру staging package, если доступен `dpkg-buildpackage`/`debuild`.

### Шаг 5.2. Обновить QML gettext

Extractor должен получать и `qml/Main.qml`, и все `modules/*/*.qml`. Context остаётся basename файла, поэтому существующие переводы вкладок сохраняются. Сам `scripts/extract_qml.py` не усложнять.

Точная команда обновления QML POT из repository root:

```bash
python3 scripts/extract_qml.py qml/Main.qml modules/*/*.qml > translations/messages_qml.pot
```

Новые/изменённые entries из `messages_qml.pot` перенести в общий `translations/ru/LC_MESSAGES/jira_qt.po`, не удаляя Python entries, которых нет в QML POT.

Добавить перевод новых строк оболочки, включая:

```text
Failed to load module: %1
Some modules were skipped: %s
```

Первая строка относится к context `Main.qml`, вторая извлекается из `jira_analytics_gui.py` обычным Python gettext workflow.

Для QML использовать context-aware gettext `tr.pgettext("Main.qml", "...")` при ручной проверке. Пересобрать `.mo`.

Команда сборки каталога:

```bash
msgfmt translations/ru/LC_MESSAGES/jira_qt.po -o translations/ru/LC_MESSAGES/jira_qt.mo
```

Не добавлять `title_ru` в manifest: внешняя локализация отложена.

### Шаг 5.3. Добавить минимальный пример внешнего модуля в документ

```qml
import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ColumnLayout {
    property var api
    property var theme

    Label {
        text: "Custom analytics"
        color: theme.text
    }

    ListView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        model: api.periodSprintModel
        delegate: Label { text: team + ": " + sprint }
    }

    Button {
        text: "Refresh"
        enabled: !api.periodChecking
        onClicked: api.refreshPeriod()
    }
}
```

Документировать:

- точный manifest schema;
- каталог установки;
- полный список properties/models/slots `ModuleApi`;
- роли каждой модели или ссылку на актуальный список ролей в `AppController`;
- реактивность через notify signals;
- необходимость перезапуска;
- отсутствие Python plugins и sandbox;
- правило совместимости `apiVersion: 1`;
- поведение при duplicate ID и ошибке QML.

### Шаг 5.4. Финальные проверки

```bash
python3 test_jira_analytics.py
python3 -m py_compile jira_analytics.py jira_period_analytics.py jira_analytics_gui.py jira_modules.py scripts/extract_qml.py
git diff --check
```

Дополнительно:

- QML brace check всех QML:

```bash
python3 /home/user4/rules/.opencode/skills/qml-braces/scripts/qml_braces.py --all --qml-dir /home/user4/jira/qml
python3 /home/user4/rules/.opencode/skills/qml-braces/scripts/qml_braces.py --all --qml-dir /home/user4/jira/modules
```

- offscreen smoke до допустимой ошибки отсутствующих QtQuick modules;
- проверить отсутствие старых файлов и ссылок на них;
- проверить `debian/install` на существование каждого source path;
- проверить, что git diff не содержит секретов, cache files, `.env`, пользовательских тестовых модулей или `__pycache__`.

### Контрольная точка 5

Передать владельцу полный diff и матрицу проверок. Владелец выполняет итоговый code review и только после этого решает вопрос о commit.

---

## Критерии приёмки

- Пять встроенных вкладок отображаются в прежнем порядке и сохраняют текущее поведение.
- Добавление валидного каталога в `~/.local/share/jira-analytics/modules/` создаёт вкладку после перезапуска без изменения исходников.
- Модуль не может зарегистрировать Python backend.
- Встроенные вкладки используют только поддерживаемые `api` и `theme`, без `backend`, `root` из `Main.qml`, `mainTabs` и глобальных моделей.
- Пользовательский duplicate ID не заменяет встроенный модуль.
- Ошибочный пользовательский manifest или QML не роняет остальные вкладки.
- Syntax/import error встроенного QML блокирует запуск с понятной диагностикой.
- Jira URL строятся в Python API, не в QML.
- Charts продолжает использовать `chartSlotsModel`, `chartSlotLabel` и реагировать на `modelReset`.
- Локальное состояние вкладки сохраняется при переключении.
- DEB устанавливает `Main.qml`, `jira_modules.py` и весь каталог встроенных модулей.
- Основные тесты, Python compile, QML braces, smoke и `git diff --check` проходят.

## Вопросы, которые нельзя решать молча во время реализации

Остановиться и спросить владельца, если:

- для нужной вкладки не хватает согласованного свойства или команды `ModuleApi`;
- требуется открыть credentials/config/env внешнему модулю;
- существующие незакоммиченные изменения конфликтуют с переносом QML;
- Qt 5.12 не поддерживает выбранный способ передачи initial properties в `Loader.setSource()`;
- preflight `QQmlComponent` ведёт себя асинхронно в целевом окружении;
- для сохранения текущего поведения требуется менять бизнес-расчёты аналитики;
- package layout отличается от описанного в `debian/install`.

Не расширять API, не добавлять fallback globals и не вводить backward compatibility без подтверждения владельца.

---

## Статус реализации

Этапы 1–5 выполнены.

| Этап | Статус | Коммит |
|------|--------|--------|
| 1. Manifest discovery | ✅ | `a310897` |
| 2. ModuleApi | ✅ | незакоммичен |
| 3. Явные api/theme в QML | ✅ | незакоммичен |
| 4. Dynamic TabBar + built-in manifests | ✅ | незакоммичен |
| 5. Поставка, переводы, документация API | ✅ | незакоммичен |

---

## API ModuleApi

Модуль получает два свойства при создании через `Loader.setSource()`:

```qml
property var api    // ModuleApi (QObject)
property var theme  // QtObject с цветовой палитрой
```

### Model properties (constant)

| Property | Тип | QML-использование |
|----------|-----|-------------------|
| `taskModel` | `TaskModel` (8 ролей) | `ListView { model: api.taskModel }`. Роли: `key`, `summary`, `status`, `target_end`, `days`, `kind`, `url`, `assignee` |
| `periodBoardModel` | `DictListModel` (20 ролей) | `model.get(row)` → `QVariantMap`, `model.count`. Роли: `team`, `sprints`, `totalPoints`, `donePoints`, `avgVelocity`, `completion`, `notDone`, `spillover`, `spilloverCompleted`, `active`, `backlog`, `reassigned`, `unestimated`, `removed`, `removedPoints`, `flowEfficiency`, `completedLater`, `observedOtherBoard`, `notObserved`, `unknown` |
| `periodSprintModel` | `DictListModel` (33 роли) | `get(row)`/`count`. Роли: `team`, `sprint`, `sprintId`, `boardId`, `startDate`, `endDate`, `period`, `periodSprintLabel`, `dates`, `total`, `done`, `points`, `totalPoints`, `completion`, `notDone`, `spillover`, `spilloverCompleted`, `active`, `backlog`, `reassigned`, `removed`, `removedPoints`, `flowEfficiency`, `leadTimeP50`, `leadTimeP85`, `leadTimeP95`, `agingAvg`, `agingMax`, `statusDistribution`, `completedLater`, `observedOtherBoard`, `notObserved`, `unknown` |
| `backlogHealthModel` | `DictListModel` (10 ролей) | `get(row)`/`count`. Роли: `team`, `totalOpen`, `aging30`, `aging90`, `aging180`, `aging365`, `agingAvg`, `agingMax`, `staleCount`, `items` |
| `assigneeModel` | `QSortFilterProxyModel` (17 ролей) | `ListView { model: api.assigneeModel }`. Роли: `name`, `team`, `tasks`, `done`, `doneSp`, `totalSp`, `completion`, `spillover`, `spilloverCompleted`, `active`, `backlog`, `reassigned`, `items`, `completedLater`, `observedOtherBoard`, `notObserved`, `unknown` |
| `chartSlotsModel` | `DictListModel` (30 ролей) | `get(row)`/`count`. Роли: `team`, `period`, `startDate`, `endDate`, `chartSlotLabel`, `done`, `total`, `totalPoints`, `donePoints`, `points`, `spillover`, `spilloverCompleted`, `active`, `backlog`, `reassigned`, `notDone`, `removed`, `removedPoints`, `completion`, `flowEfficiency`, `leadTimeP50`, `leadTimeP85`, `leadTimeP95`, `agingAvg`, `agingMax`, `statusDistribution`, `completedLater`, `observedOtherBoard`, `notObserved`, `unknown` |

`reassigned` — deprecated legacy-role, равная `observedOtherBoard + notObserved + unknown`. Новые графики должны использовать взаимоисключающие роли и не складывать legacy-сумму с её компонентами.

`DictListModel` поддерживает `get(row)` → `QVariantMap` и property `count`. `TaskModel`, `AssigneeFilterProxy` — стандартные Qt model/view модели для использования через `ListView.model`.

### State properties (notify-сигналы)

| Property | Тип | Сигнал |
|----------|-----|--------|
| `checking` | `bool` | `checkingChanged` |
| `errorsText` | `str` | `errorsTextChanged` |
| `itemsText` | `str` | `itemsTextChanged` |
| `periodChecking` | `bool` | `periodCheckingChanged` |
| `periodStatusText` | `str` | `periodStatusTextChanged` |
| `periodErrorsText` | `str` | `periodErrorsTextChanged` |
| `periodFromCache` | `bool` | `periodFromCacheChanged` |
| `periodLastRefresh` | `str` | `periodLastRefreshChanged` |
| `periodKpi` | `QVariantMap` | `periodKpiChanged` |
| `selectedTeam` | `str` | `selectedTeamChanged` |
| `backlogSort` | `str` | `backlogSortChanged` |
| `backlogAgeFilter` | `str` | `backlogAgeFilterChanged` |
| `backlogActiveStatuses` | `str` | `backlogActiveStatusesChanged` |
| `forgottenAgeDays` | `int` | `forgottenAgeDaysChanged` |
| `assigneeBoardNames` | `QVariantList` | `assigneeBoardNamesChanged` |
| `assigneeKpi` | `QVariantMap` | `assigneeKpiChanged` |

### Slots

| Slot | Аргументы | Возврат | Описание |
|------|-----------|---------|---------|
| `refreshTarget()` | — | — | Запустить проверку Target end |
| `saveItems(text)` | `text: str` | — | Сохранить список источников |
| `refreshPeriod()` | — | — | Запустить period analytics refresh |
| `selectTeam(team)` | `team: str` | — | Выбрать/снять команду |
| `setBacklogSort(sort_key)` | `sort_key: str` | — | Фильтр бэклога: `stale`, `aging365`, `forgotten` |
| `setAssigneeBoardFilter(name)` | `name: str` | — | Фильтр по доске для Assignees |
| `openIssue(key)` | `key: str` | `bool` | Открыть задачу по ключу (например `ABC-123`). Возвращает `False` если key невалидный или Jira URL не задан |
| `openSprintReport(boardId, sprintId)` | `boardId: int`, `sprintId: int` | `bool` | Открыть отчёт спринта. Возвращает `False` если ID ≤ 0 или Jira URL не задан |

### Theme

```qml
QtObject {
    readonly property color bg         // фон окна
    readonly property color card       // фон карточек
    readonly property color border     // границы
    readonly property color text       // основной текст
    readonly property color muted      // второстепенный текст
    readonly property color blue       // акцентный
    readonly property color red        // опасность
    readonly property color redSoft    // мягкий красный фон
    readonly property color amber      // предупреждение
    readonly property color amberSoft  // мягкий янтарный фон
}
```

### Правила совместимости

- Модуль должен иметь `module.json` с `apiVersion: 1`
- Только один корневой QML-файл
- Python-код не загружается (нет sandbox)
- Изменения вступают в силу после перезапуска

### Duplicate ID

- Среди двух встроенных модулей с одинаковым `id` остаётся первый в лексикографическом порядке каталогов
- Среди двух пользовательских модулей с одинаковым `id` — первый в лексикографическом порядке
- Пользовательский `id`, совпадающий с уже загруженным встроенным, игнорируется (приоритет built-in)
- Duplicate пропускается молча, без warning

### Обработка ошибок

- Невалидный JSON, отсутствующее поле, неверный тип поля, `apiVersion != 1`, отсутствующий или недоступный QML → общий `invalid manifest` в stderr; модуль пропускается
- Malformed manifest внешнего модуля → одна ошибка в stderr, остальные вкладки работают
- Syntax/import error встроенного QML → приложение не запускается, ошибка в stderr с путём к файлу
- Syntax/import error пользовательского QML → `Loader.Error` placeholder только в его вкладке
- Manifest warnings показываются компактной строкой `Some modules were skipped: N` в UI
- `backend` доступен как context property оболочки login/settings — **не поддерживаемый API**

---

## Пример внешнего модуля

`~/.local/share/jira-analytics/modules/my_analytics/module.json`:

```json
{
  "id": "my_analytics",
  "title": "My Analytics",
  "qml": "Main.qml",
  "order": 100,
  "apiVersion": 1
}
```

`~/.local/share/jira-analytics/modules/my_analytics/Main.qml`:

```qml
import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ColumnLayout {
    property var api
    property var theme

    Label {
        text: "Custom analytics"
        color: theme.text
    }

    ListView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        model: api.periodSprintModel
        delegate: Label { text: team + ": " + sprint }
    }

    Button {
        text: "Refresh"
        enabled: !api.periodChecking
        onClicked: api.refreshPeriod()
    }
}
```
