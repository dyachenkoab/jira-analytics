# Графики квартальной аналитики Jira

Все графики используют данные спринтов, попадающих в окно квартала (`period_start` – `period_end` из конфига). Фильтр команд (`chartTeam`) применяет тот же код классификации, что и таблицы — расхождения исключены.

---

## 1. Velocity (done SP per sprint)

**Что показывает:** сумму закрытых story points в каждом спринте — производительность команды в SP.

**Формула:**

```
velocity = Σ done_points(i)   для всех done-задач спринта
```

`done_points` = `issue_points(issue, points_field)` — Story Points из конфигурируемого поля Jira (по умолчанию `Story points`).

Задача считается done, если её статус входит в `done_statuses` (с учётом `historical_done` — исторического статуса на момент закрытия спринта для уже прошедших спринтов).

**Код:** `summarize_sprint()` → `SprintSummary.done_points` → `jira_period_analytics.py:373`

**Как читать:** высокие столбцы = продуктивные спринты. Сравнивать с Committed vs Completed для оценки % выполнения.

---

## 2. Completion (% per sprint)

**Что показывает:** долю закрытых SP от всего объёма спринта.

**Формула:**

```
completion = done_points / total_points × 100%
```

Если `total_points = 0` (все задачи без оценок), fallback:

```
completion = done_issues / total_issues × 100%
```

**Код:** `completion_pct()` → `jira_period_analytics.py:271`

**Как читать:** 100% = весь запланированный объём выполнен. Падение по спринтам = проблемы с доставкой или переоценка ёмкости.

---

## 3. Flow Efficiency (% per sprint)

**Что показывает:** долю времени, которое задачи провели в активной работе, от полного цикла жизни задачи (создание → закрытие).

**Формула для одной задачи:**

```
active_time   = Σ время в статусах из active_statuses
waiting_time  = Σ время в статусах из waiting_statuses
total_time    = active_time + waiting_time

flow_efficiency = active_time / total_time × 100%
```

Время в done-статусах не учитывается (не активно и не ожидание). Неизвестные статусы считаются waiting с предупреждением в `errors`.

**Per-sprint:** среднее арифметическое `flow_efficiency` всех done-задач спринта.

**Per-board / portfolio:** взвешенное среднее по `done_issues` каждого спринта:

```
portfolio_flow = Σ (ef_sprint × done_issues_sprint) / Σ done_issues_sprint
```

**Определение `end`:** преимущественно `resolutiondate` из Jira. Если поле отсутствует — последний переход задачи в done-статус из changelog. Для закрытого спринта события после `complete_date` не учитываются.

**Код:** `flow_efficiency_for_issue()` → `jira_period_analytics.py:187`, `compute_flow_efficiency_by_sprint()` → L421, `_weighted_flow_efficiency()` → L580

**Как читать:** низкие значения (10-30%) — задачи долго ждут в очередях. Высокие (>50%) — мало времени простоя. Рост тренда = улучшение потока.

---

## 4. Committed vs Completed (SP per sprint)

**Что показывает:** сравнение запланированного объёма SP (committed) с фактически выполненным (completed).

**Формула:**

```
committed = total_points спринта (весь scope, включая mid-sprint добавки)
completed = done_points спринта
```

**Примечание:** поле `committed` в Jira API (исходный scope на старте спринта) недоступно без дополнительных запросов. Используется `total_points` как приближение — это «весь объём, оказавшийся в спринте», включая задачи, добавленные mid-sprint.

**Код:** `SprintSummary.total_points` / `SprintSummary.done_points` → `jira_period_analytics.py:40-41`

**Как читать:** столбцы рядом показывают разрыв план/факт. Если committed стабильно выше completed — команда перегружена или оценивает оптимистично.

---

## 5. Throughput (done issues per sprint)

**Что показывает:** количество закрытых задач в спринте, без учёта их веса в SP.

**Формула:**

```
throughput = count(issue) для всех done-задач спринта
```

**Код:** `SprintSummary.done_issues` → `jira_period_analytics.py:39`

**Как читать:** полезен для команд с неравномерным sizing'ом задач. Высокий throughput при низком velocity = много мелких задач. Падение = снижение потока.

---

## 6. Lead time p50 (days per sprint)

**Что показывает:** медианное время от создания до закрытия задачи в днях (для done-задач спринта).

**Формула:**

```
lead_time(i) = resolutiondate(i) − created(i)  (в днях)
lead_time_p50 = percentile(lead_times, 50)
lead_time_p85 = percentile(lead_times, 85)
lead_time_p95 = percentile(lead_times, 95)
```

**Перцентиль** — линейная интерполяция:

```
k = (n − 1) × p / 100
pth_percentile = sorted[k_floor] + (sorted[k_ceil] − sorted[k_floor]) × fractional_part(k)
```

**Код:** `summarize_sprint()` (сбор lead_times) → `jira_period_analytics.py:379`, `_percentile()` → L279

**Как читать:** p50 = половина задач закрывается быстрее, половина — медленнее. p95 = «худший случай» (95% задач закрываются быстрее). Рост любой из линий = доставка замедляется.

---

## 7. Aging (days, not-done tasks)

**Что показывает:** возраст незакрытых задач на конец спринта.

**Формула:**

```
age(i) = today − created(i)  (в днях)
aging_avg = Σ age(i) / count(not_done)
aging_max = max(age(i))
```

Для done-задач age не считается (нет незавершённой работы).

**Код:** `summarize_sprint()` (сбор ages) → `jira_period_analytics.py:382-383`

**Как читать:** высокий `aging_avg` и особенно `aging_max` — сигнал, что старые задачи зависли и не двигаются. Рост = работа накапливается.

---

## 8. Cumulative Flow (issues by status per sprint)

**Что показывает:** распределение задач по статусам на конец каждого спринта. Это упрощённый CFD — не непрерывная диаграмма, а «снимки» на конец спринта.

**Формула:**

```
status_distribution = {status_name: count} для всех задач спринта
```

**Код:** `summarize_sprint()` → `SprintSummary.status_distribution` → `jira_period_analytics.py:365`

**Как читать:** stacked bars показывают структуру спринта. Широкая полоса «To Do»/«Backlog» — много не начатых задач. Рост done-полосы (зелёные) = прогресс. Стабильно высокая доля waiting-статусов = bottleneck в начале потока.

---

## 9. Predictability (completion ±1σ per sprint)

**Что показывает:** стабильность выполнения спринтов — средний completion % и разброс вокруг него.

**Формула:**

```
μ  = Σ completion(i) / n
σ  = sqrt( Σ (completion(i) − μ)² / n )

band = [μ − σ, μ + σ]    (≈68% спринтов попадают внутрь)
```

**Код:** вычисляется в QML Canvas onPaint, данные из `sprint.completion` (результат `completion_pct`).

**Как читать:** узкая полоса ±σ = команда стабильно выдаёт предсказуемый результат. Широкая полоса = непредсказуемая доставка (то 100%, то 40%). Пунктирная линия μ — средний уровень за период.

---

## 10. Spillover totals by team

**Что показывает:** куда делись незакрытые задачи спринта.

**Категории:**

| Категория | Определение |
|-----------|------------|
| **Spillover** | Not-done задача, которая появилась в будущем спринте той же команды |
| **Spillover completed** | Из spillover — те, что в итоге были закрыты в будущем спринте |
| **Completed later** | Независимый outcome: после cutoff подтверждён переход в done; не входит в stacked total |
| **Active** | Not-done задача без будущего спринта, которая сейчас находится в effective `active_statuses` team-scope снимка |
| **Backlog** | Not-done задача без будущего спринта, которая сейчас находится в effective `waiting_statuses` team-scope снимка |
| **Other board** | Задача обнаружена в будущем спринте другой настроенной доски; это наблюдение, а не доказанный transfer |
| **Not observed** | Не найдена в успешно загруженных будущих спринтах и backlog-JQL |
| **Unknown** | Маршрут нельзя определить из-за неполной загрузки спринтов/backlog или активного исходного спринта |
| **Reassigned** | Deprecated legacy-role: `other_board + not_observed + unknown`; встроенный UI не показывает это название |
| **Removed** | Задача была удалена из спринта по Jira Sprint Report (`puntedIssues`) |

**Формула:**

```
not_done = spillover + other_board + active + backlog + not_observed + unknown
reassigned = other_board + not_observed + unknown
```

Spillover считается **per-board** — доски не смешиваются.

**Код:** `compute_spillover()` → `jira_period_analytics.py:285`

**Как читать:** большой spillover означает перенос между спринтами исходной команды. Other board показывает только наблюдение на другой доске: Jira board является filter view и не доказывает смену команды. Not observed означает отсутствие в полностью загруженном окне, Unknown — недостаток данных. Removed — отдельный churn и не меняет done/completion.

---

## Источники данных

| Данные | Источник |
|--------|----------|
| `done_points`, `total_points` | Поле Story Points из Jira API + `issue_points()` |
| `done_statuses`, `active_statuses`, `waiting_statuses` | Конфиг `jira-analytics.yaml` |
| `historical_done` | Разница между текущим статусом задачи и историческим на момент `complete_date` спринта. Строится из changelog API Jira. |
| `completed_later` | Первый подтверждённый переход в настроенный done-статус после `complete_date`; последующее reopening не отменяет факт перехода |
| `lead_time` | `resolutiondate − created` из полей задачи Jira; для закрытого спринта конец ограничен `complete_date` |
| `flow_efficiency` | Changelog статусов задачи до `complete_date` закрытого спринта: подсчёт секунд в каждом статусе, классификация по `active_statuses` / `waiting_statuses` |
| `aging` | `today − created`, только для not-done задач |
| `status_distribution` | `status.name` из полей задачи Jira |

## Инварианты

- Доски/команды **не смешиваются** в per-board расчётах (spillover, backlog, aggregations)
- Ошибки частичной загрузки **не роняют** отчёт — добавляются в `errors`
- Per-sprint метрики считают задачу **в каждом спринте**, где она есть (одна задача в нескольких спринтах учитывается многократно)
- Portfolio unique totals **дедуплицируют** по ключу задачи
- Счётчики и drill-down/detail items вычисляются **одной и той же классификацией** — расхождений нет
- `completed_later` является outcome и не прибавляется к взаимоисключающим маршрутным категориям
- `backlog_jql` — сохранённое для совместимости имя team-scope JQL всех задач команды, а не authoritative Jira backlog
- Каждая задача team-scope получает backend-группу `done`, `active`, `waiting` или `unknown`; исходное имя Jira status сохраняется отдельно
- Backlog Health считает только задачи группы `waiting`; active-задачи опубликованы backend-ролями, но встроенный QML пока их не отображает
- Sprint horizon включает `future`, `active` и `closed`; future-спринты используются только для routing и не входят в KPI/модели отчётных спринтов
- Partial failure любой доски делает ненаблюдённый cross-board маршрут `unknown`; raw dump сохраняет sprint/team-scope completeness раздельно

## Кеш и обновление

- При запуске `period_cache.json` позволяет сразу показать последний рассчитанный отчёт.
- Обычный `Refresh analytics` повторно использует issues и removed issues закрытых спринтов, но обновляет активные спринты и backlog.
- Новые закрытые спринты добавляются в `period_source_cache.json`; частичная ошибка Jira не удаляет ранее сохранённые данные.
- `Full refresh history` в настройках обходит source cache и changelog cache, но заменяет только успешно загруженные части.
- `401` возвращает форму credentials; `403` останавливает автоматические обновления и оставляет cached-данные на экране.
