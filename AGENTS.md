# Project Notes For Coding Agents

## Language

- Отвечать пользователю на русском.
- Комментарии, планы и review-файлы писать на русском, если пользователь не просит иначе.
- Пользовательскую документацию писать понятным русским языком; англоязычные термины оставлять только для точных имён API, технологий и общепринятых терминов Jira.

## Project

- Python + PyQt5/QML приложение для работы с Jira: мониторинг задач и аналитика.
- Основные зоны кода:
  - `jira_analytics.py` — конфиг, Jira API, сбор данных, интеграция с внешними источниками.
  - `jira_period_analytics.py` — чистые функции расчётов и dataclass-модели аналитики.
  - `jira_analytics_gui.py` — PyQt backend, модели, properties/signals, worker threads.
  - `jira_modules.py` — discovery и валидация manifest модулей.
  - `qml/Main.qml` — оболочка, dynamic TabBar и Loader модулей.
  - `modules/*` — встроенные QML-вкладки и их `module.json`.
  - `test_jira_analytics.py` — assert-based тесты без pytest.
  - `period_analytics_plan.md` — общий план развития.
  - `docs/charts.md` — формулы и описания графиков.
  - `docs/custom_metrics` — руководство для внешних вкладок и новых core-метрик.
  - `modular_architecture.md` — полный контракт `ModuleApi` и архитектура модулей.

## Commands

- Основная проверка: `python3 test_jira_analytics.py`.
- Компиляция Python: `python3 -m py_compile jira_analytics.py jira_period_analytics.py jira_analytics_gui.py jira_modules.py jira_analytics_cli.py logs_sanitizer.py test_logs_sanitizer.py scripts/extract_qml.py`.
- Whitespace check: `git diff --check`.
- При запуске тестов может быть ожидаемый stderr: `Failed to send notification: gdbus command not found`.
- QML smoke check в текущем окружении может упасть на отсутствующих QtQuick modules. Если ошибка дошла до `module "QtQuick" is not installed`, синтаксис QML уже прошёл дальше парсинга импортов.
- Для проверки переводов QML использовать context-aware gettext: `tr.pgettext("Main.qml", "Some string")`, не обычный `gettext()`.
- QML POT: `python3 scripts/extract_qml.py qml/Main.qml modules/*/*.qml > translations/messages_qml.pot`.
- Проверка PO: `msgfmt --check translations/ru/LC_MESSAGES/jira_qt.po -o /tmp/jira_qt_check.mo`.
- QML braces запускать для `qml/Main.qml` и каждого изменённого `modules/*/*.qml`.

## Git And Files

- Не коммитить без прямой просьбы.
- Не откатывать чужие изменения.
- Рабочее дерево часто бывает грязным: сначала смотреть `git status --short` и relevant diff.
- Для ручных правок использовать `apply_patch`.
- Review-файл создавать только по прямой просьбе пользователя; постоянного `review.md` в проекте нет.

## Architecture

- Расчёты держать в `jira_period_analytics.py` как чистые функции без PyQt и Jira API.
- Сбор данных и Jira-specific детали держать в `jira_analytics.py`.
- GUI-слой не должен дублировать бизнес-классификацию: QML получает уже посчитанные поля/списки.
- PyQt properties/signals должны соответствовать тому, что реально читает QML.
- Worker threads не должны напрямую менять UI; только signals → backend slots/properties.

## Adding Metrics

- Сначала выбрать путь по `docs/custom_metrics`: внешняя QML-вкладка на готовом `ModuleApi` или core-метрика с новым расчётом/полем.
- Не менять Python, если достаточно presentation-only агрегации уже опубликованных ролей.
- Новую бизнес-метрику начинать с точного определения scope, cutoff, числителя, знаменателя, дедупликации и поведения при missing data.
- Перед production-кодом добавить минимальный assert-based regression test в `test_jira_analytics.py`.
- Чистый расчёт и dataclass-поля добавлять в `jira_period_analytics.py`; Jira-specific сбор — в `jira_analytics.py`.
- Проводить значение одним потоком: result dataclass → GUI serialization/model role → `ModuleApi` при необходимости → QML.
- При изменении `PeriodReport` или вложенных dataclass обновлять ручное восстановление в `load_period_cache()` и тестировать равенство fresh/cached результата.
- При изменении структуры или семантики cached report добавлять/увеличивать schema/calculation marker в `_config_signature()` и тестировать, что cache старого формата отклоняется.
- Если новая метрика зависит от config, включать эти настройки в `_config_signature()`.
- Не вычислять независимо один и тот же счётчик в Python и QML. QML форматирует готовые значения и строит presentation-only агрегаты.
- Учитывать типы ролей: board/sprint/assignee модели содержат formatted strings, а `chartSlotsModel` публикует числовые значения для арифметики.
- `periodSprintModel` фильтруется через `selectedTeam`; `assigneeModel` — через board filter и `show_external_assignees`; не использовать их как стабильный portfolio source.
- `backlogHealthModel` содержит только board с настроенным и успешно загруженным `backlog_jql`; отсутствие строки не равно нулевому backlog.
- При добавлении role обновлять точный список roles в `jira_analytics_gui.py`, документацию `ModuleApi` и все serializers, которые заполняют модель.
- Property, читаемый QML, должен иметь корректный notify signal; модель должна посылать reset/data signals.
- Для нового графика записать формулу, ограничения и интерпретацию в `docs/charts.md`.
- Изменение публичного API отражать в `modular_architecture.md` и `docs/custom_metrics`.

## Modules

- Встроенные вкладки находятся только в `modules/<id>/`; не создавать копии в `qml/`.
- Внешние модули: `~/.local/share/jira-analytics/modules/<id>/` и только QML + `module.json`.
- Поддерживаемый контракт модуля — только переданные `api` и `theme`; `backend` и context globals не использовать.
- Не добавлять Python plugins, sandbox, hot reload или новый `apiVersion` без прямого решения владельца.
- Пользовательский duplicate ID не заменяет built-in; malformed user module не должен ронять остальные вкладки.
- Новую команду или данные публиковать через `ModuleApi` только при доказанном use case, не для гипотетического расширения.
- Не копировать business-фильтры из `BacklogTab.qml`: существующие forgotten/age presentation filters не являются образцом для новой core-классификации.

## Analytics Invariants

- Доски/команды не смешивать: настройки, backlog и sprint context относятся к конкретной board/team.
- Для закрытых спринтов считать статус на релевантный historical cutoff; для активных — текущий статус.
- Ошибки частичной загрузки не должны ронять весь отчёт: добавлять warning/error в `errors` и продолжать, где возможно.
- Per-sprint метрики считают задачу в каждом спринте, где она есть.
- Board/portfolio unique totals дедуплицируют задачи по key.
- Done totals требуют явного sprint context или явного правила последнего релевантного состояния; нельзя бездумно делать `any()` по историческим значениям одного key.
- Если story points одной задачи менялись между спринтами, total/done points должны брать согласованный источник, иначе completion может стать больше 100%.
- Счётчики и drill-down/detail items должны вычисляться одной и той же классификацией, чтобы UI не расходился с цифрами.

## QML Notes

- Верхний `TabBar` и `StackLayout` создаются динамически из `moduleTabsModel`; оба блока должны оставаться sibling-level элементами основного layout.
- Не вставлять блок одной вкладки внутрь delegate/list item другой вкладки.
- Осторожно с QML `MouseArea`: элемент, объявленный позже и растянутый на всю карточку, перехватывает клики внутренних строк.
- Если QML не парсится, offscreen check обычно показывает строку вида `Expected token ...`; это blocker, даже если сама идея размещения UI верная.
- Новые QML строки добавлять в `.pot`, `.po` и пересобирать `.mo`.
- Context QML-перевода равен basename файла модуля, например `ChartsTab.qml`.

## Review Workflow

- Если пользователь просит code review, findings идут первыми, по severity, с file/line references.
- Отличать product decision от blocker-синтаксиса: спорное размещение UI может быть допустимым, но невалидный QML остаётся blocker.
- Проверять не только тесты, но и ручные edge cases для изменённой аналитической семантики.
- Для UI changes по возможности запускать Python compile, tests, diff check и QML smoke до ошибки отсутствующих QtQuick modules.
- Для изменения аналитической семантики вручную проверять active/closed sprint, duplicate issue key, missing points и partial Jira error.

## Plans

- Общий план: `period_analytics_plan.md`.
- Детальные временные планы можно держать отдельными `*_plan.md`, но при изменении приоритетов обновлять общий план.
