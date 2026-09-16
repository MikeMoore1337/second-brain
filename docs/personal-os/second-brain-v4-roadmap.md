# Second Brain v4 — Personal Cognitive OS

Статус после Phase 16.0: **DESIGN GATE COMPLETE / RUNTIME NOT IMPLEMENTED**.

Этот документ задаёт переход от завершённого Cognitive Twin v3 к новой
продуктовой генерации. Он не переименовывает и не переопределяет Cognitive
Twin v3: Cognitive Twin остаётся завершённой фундаментальной подсистемой,
которая поставляет проверенные bounded-проекции для последующих слоёв.

## North Star: Digital Self

Долгосрочная цель проекта — построить наиболее полный, доказательный,
обновляемый и функционально верный цифровой модельный контур владельца,
технически достижимый на практике: память, идентичность, ценности,
предпочтения, убеждения, цели, стиль решений, структуры рассуждений,
поведение, коммуникацию и агентность.

Это практическая попытка приблизиться к **Digital Self** и сохранить
функциональную непрерывность самости в программной системе.

Граница знания остаётся обязательной:

```text
functional digital self continuity = цель проекта
subjective consciousness transfer = НЕ установлено и НЕ утверждается
```

Second Brain не заявляет, что загрузил, перенёс, скопировал или воспроизвёл
субъективное сознание владельца. Модель обязана сохранять противоречия,
неопределённость и временные изменения, а не превращать владельца в одну
неизменную вымышленную личность.

Будущая оценка Digital Self ведётся по независимым измерениям:

1. точность предсказания;
2. точность предпочтений и ценностей;
3. точность текущего/временного состояния;
4. точность структуры рассуждения, если она явно зафиксирована;
5. точность коммуникации в применимых сценариях;
6. точность действия и агентности;
7. точность происхождения данных и исправления ошибок.

Измерения не сворачиваются в непрозрачный единый показатель «процент меня».

## Место основных слоёв

```text
Digital Memory
  canonical reviewed evidence and owner knowledge
       |
       v
Cognitive Twin
  bounded descriptive model of observed/stated self
       |
       v
Digital Self
  updateable cross-domain functional continuity model
       |
       v
Agency
  owner-controlled decisions and, only in later stages, explicitly gated actions
```

* **Digital Memory** — каноническая память и проверенные записи владельца.
  Её authority остаётся за `second-brain-vault` и существующими типами записей.
* **Cognitive Twin** — завершённый Stage 1–15 слой описательных,
  сопоставимых и адаптивных bounded-проекций. Он не становится скрытым
  исполнителем и не получает новую семантику задним числом.
* **Digital Self** — долгосрочная функциональная композиция памяти,
  само-модели, динамики, предпочтений и агентности. Это направление, а не
  основание для метафизического заявления.
* **Agency** — только явные owner-controlled решения. Производный совет не
  равен намерению владельца, обязательству или внешнему действию.

## Authority model

| Слой | Пример | Authority |
| --- | --- | --- |
| `canonical` | reviewed note, Goal, Goal Progress, Experiment | существующие канонические источники и их контракты |
| `operational` | Stage 9 calibration, Stage 15 adaptive profile, Stage 16 accepted strategy | bounded versioned state вне vault; не user evidence |
| `derived` | Executive Context Pack, Strategy Proposal, projections | пересобираемый результат exact source bindings |
| `provider_output` | текст и структурированный ответ Advisor | непроверенная производная выдача до строгой валидации и owner review |

Ни один производный или provider слой не повышает свой authority автоматически.
Принятие владельцем создаёт только предусмотренное operational состояние; оно
не превращается в каноническую память, Goal, Decision Journal evidence или
скрытую модель обучения.

## Product loop

Общий целевой цикл v4:

```text
REMEMBER -> UNDERSTAND -> DECIDE -> PLAN -> ACT -> OBSERVE -> LEARN -> ADAPT
```

Stage 16 реализует только:

```text
UNDERSTAND -> DECIDE WHAT TO DO
```

То есть система может показать bounded стратегические варианты, исследование,
уточнение, кандидат эксперимента или безопасную паузу. Она не планирует,
расписывает и не выполняет их.

## Принципы agency и риска действия

До внешнего действия обязательны отдельные будущие контракты для:

* явного намерения и области действия;
* проверки актуальности и адресата;
* preview и consent перед каждым consequential action;
* идемпотентности, отмены и восстановления;
* минимизации полномочий и раздельных credential boundaries;
* аудита факта выполнения без подмены канонической памяти;
* fail-closed поведения при неопределённости, конфликте или stale source.

Stage 16 не создаёт календарь, задачи, commitments, интеграции, Action
Gateway и автономного агента. Provider не получает права запускать команды,
писать в GitHub, отправлять сообщения или менять Stage 1–15.

## Privacy и security

Приватные данные владельца передаются только по явному owner action через
минимизированный previewed payload. Browser private state живёт только в
памяти страницы: `localStorage`, `sessionStorage`, `IndexedDB`, Cache Storage,
background sync и service-worker cache для private response запрещены.

Каждый private API использует существующую owner-only границу: session,
trusted Host, same-origin Origin, exact `X-Second-Brain-Request`, strict
method/content type, bounded body, no-store, current CSP/security headers и
safe bounded errors. В логи, ошибки и telemetry не попадают raw notes, paths,
secrets, provider payload/result или accepted private snapshot.

## Roadmap v4

### Stage 16 — Personal Strategy / Executive Layer

Текущий owner-approved этап. Exact one-Goal context, provider-free Executive
Context Pack, явный preview и strategy reasoning через существующую Advisor
границу, bounded Strategy Proposal, owner review/edit/select и versioned
accepted Strategy Snapshot. Без планирования и execution.

### Stage 17 — Personal Planning & Commitments

Будущий слой преобразования принятых стратегических решений в owner-controlled
планы, commitments и bounded portfolio view. Не начат и не проектируется в
рамках Stage 16.

### Stage 18 — Execution & Feedback

Будущий слой явного выполнения согласованных внутренних действий и сбора
проверяемой обратной связи. Он должен получить отдельный контракт обучения и
калибровки; принятие Stage 16 само по себе не является training evidence.

### Stage 19 — Controlled External Integrations & Action Gateway

Будущая изолированная граница Calendar/Email/GitHub и других внешних систем с
отдельными consent, credentials, target validation, dry-run, audit и rollback
правилами. Stage 16 её не подключает.

### Stage 20 — Personal Agent / Chief of Staff

Будущий owner-controlled orchestration layer поверх Stage 16–19. Он не может
возникнуть как автоматическое продолжение Stage 16 и не является текущей
архитектурной целью для реализации.

## Phase 16.0 design gate

До runtime-реализации должны быть merged и проверены (выполнено этой Phase
16.0):

* этот roadmap;
* [`executive-strategy-v1-contract.md`](./executive-strategy-v1-contract.md);
* post-merge CI на exact head;
* повторный fetch текущего `origin/main`.

После завершения этого gate статус Stage 16 runtime остаётся незавершённым и
меняется только фактическим closeout:

```text
Cognitive Twin v3 = COMPLETE
Second Brain v4 = IN PROGRESS
Stage 16 = COMPLETE
Stage 17–20 = PLANNED / NOT STARTED
```

Stage 17 Issue не создаётся автоматически.
