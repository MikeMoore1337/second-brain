# Second Brain v4 — Personal Cognitive OS

Статус после Phase 17.6: **STAGE 16 COMPLETE / STAGE 17 COMPLETE / SECOND BRAIN v4 IN PROGRESS**.

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

Завершённый owner-approved этап. Exact one-Goal context, provider-free Executive
Context Pack, явный preview и strategy reasoning через существующую Advisor
границу, bounded Strategy Proposal, owner review/edit/select и versioned
accepted Strategy Snapshot. Без планирования и execution.

### Stage 17 — Personal Planning & Commitments — COMPLETE

Текущий owner-approved этап. Exact accepted Stage16 Strategy Snapshots,
явно выбранные 1–8 Goals, локальный bounded горизонт, capacity и ограничения
собираются в provider-free Planning Context Pack, затем в previewed Planning
Proposal и owner-reviewed Planning Portfolio Snapshot. Это operational intent,
не execution evidence и не canonical vault state. Нормативный контракт:
[`personal-planning-v1-contract.md`](./personal-planning-v1-contract.md).

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

Этот блок фиксирует исторический design gate Phase 16.0. Runtime-статус Stage 16
изменён только фактическим closeout Phase 16.6 после exact-head CI, стандартного
deploy и production smoke:

```text
Cognitive Twin v3 = COMPLETE
Second Brain v4 = IN PROGRESS
Stage 16 = COMPLETE
Stage 17–20 = PLANNED / NOT STARTED
```

Stage 17 = COMPLETE; Stage 18–20 = PLANNED / NOT STARTED.

Stage 17 Issue не создаётся автоматически.

## Phase 17.0 design gate

До runtime-реализации Stage 17 должны быть merged и проверены этот нормативный
контракт, минимальный status update roadmap, post-merge CI на exact head и
повторный fetch текущего `origin/main`. Runtime-слои 17.1–17.6 не должны
расширять authority Stage 16, Cognitive Twin или vault.

## Phase 16 factual delivery ledger

Каждая runtime-фаза 16.0–16.5 доставлялась отдельным PR от изолированной ветки.
В таблице указаны только финальные exact-head CI и post-merge CI; deploy
запускался автоматически от соответствующего merge SHA. Evidence финального
closeout Phase 16.6 фиксируется в closeout PR и финальном отчёте после получения
его собственных merge/CI/deploy идентификаторов.

| Phase | PR | Merge SHA | PR CI | Post-merge CI | Deploy |
| --- | --- | --- | --- | --- | --- |
| 16.0 | [#340](https://github.com/MikeMoore1337/second-brain/pull/340) | `8b3e8ab7fe4e7294294be44c36a02eb4c49dc86e` | `35057554104` | `35057756065` | `35057913595` |
| 16.1 | [#341](https://github.com/MikeMoore1337/second-brain/pull/341) | `b12d8fa5379cf3ca0160b9aa071ffec5c04c52d2` | `35058866284` | `35059072965` | `35059241332` |
| 16.2 | [#342](https://github.com/MikeMoore1337/second-brain/pull/342) | `91491b5b79f460a871b9921478b2ebb3c03e5912` | `35060300561` | `35060514564` | `35060713890` |
| 16.3 | [#343](https://github.com/MikeMoore1337/second-brain/pull/343) | `05a30aa687e9cdb5aceff7690c0ffcde1fbc9373` | `35062757876` | `35063009719` | `35063238859` |
| 16.4 | [#344](https://github.com/MikeMoore1337/second-brain/pull/344) | `b15da49213224a23b2aad6899c50634faaee7f91` | `35067551979` | `35067848504` | `35068068955` |
| 16.5 | [#345](https://github.com/MikeMoore1337/second-brain/pull/345) | `e9eee57d5e14b37aecb3fd0d6794637571b84117` | `35069788511` | `35070002528` | `35070279603` |
Промежуточные PR используют `Refs #339`; финальный closeout PR должен использовать
только `Closes #339`. Исторические transient failures отдельных попыток CI не
являются merged evidence: для каждой фазы в ledger сохранён последний зелёный
exact-head результат.

## Phase 17 factual delivery ledger

Каждая runtime-фаза 17.0–17.5 доставлена отдельным PR из свежего
изолированного worktree. В таблице сохранены финальные зелёные exact-head PR
CI, post-merge CI и стандартный production deploy соответствующего merge SHA.

| Phase | PR | Merge SHA | PR CI | Post-merge CI | Deploy |
| --- | --- | --- | --- | --- | --- |
| 17.0 | [#350](https://github.com/MikeMoore1337/second-brain/pull/350) | `cf5776f91ac2af1d523a7265db8b942b2065b580` | [35078641030](https://github.com/MikeMoore1337/second-brain/actions/runs/35078641030) | [35078919710](https://github.com/MikeMoore1337/second-brain/actions/runs/35078919710) | [35079216691](https://github.com/MikeMoore1337/second-brain/actions/runs/35079216691) |
| 17.1 | [#351](https://github.com/MikeMoore1337/second-brain/pull/351) | `cc6cadaf1d67584e4c0f2adec10278ce4baa1b60` | [35081513361](https://github.com/MikeMoore1337/second-brain/actions/runs/35081513361) | [35081831705](https://github.com/MikeMoore1337/second-brain/actions/runs/35081831705) | [35082177879](https://github.com/MikeMoore1337/second-brain/actions/runs/35082177879) |
| 17.2 | [#352](https://github.com/MikeMoore1337/second-brain/pull/352) | `aec9323a40fa4f531cd16d67e480a2fc39cc5025` | [35084518436](https://github.com/MikeMoore1337/second-brain/actions/runs/35084518436) | [35084823535](https://github.com/MikeMoore1337/second-brain/actions/runs/35084823535) | [35085154751](https://github.com/MikeMoore1337/second-brain/actions/runs/35085154751) |
| 17.3 | [#353](https://github.com/MikeMoore1337/second-brain/pull/353) | `d53a070eaff589bb53747707e72da876d32f5720` | [35087421580](https://github.com/MikeMoore1337/second-brain/actions/runs/35087421580) | [35087684590](https://github.com/MikeMoore1337/second-brain/actions/runs/35087684590) | [35087951240](https://github.com/MikeMoore1337/second-brain/actions/runs/35087951240) |
| 17.4 | [#354](https://github.com/MikeMoore1337/second-brain/pull/354) | `8944484d578ffee4e54c9aa806354301d3293f6d` | [35093329446](https://github.com/MikeMoore1337/second-brain/actions/runs/35093329446) | [35093653010](https://github.com/MikeMoore1337/second-brain/actions/runs/35093653010) | [35093965938](https://github.com/MikeMoore1337/second-brain/actions/runs/35093965938) |
| 17.5 | [#355](https://github.com/MikeMoore1337/second-brain/pull/355) | `b0cd4c5cee7775ef26402edaf4723c011e4119e7` | [35096545806](https://github.com/MikeMoore1337/second-brain/actions/runs/35096545806) | [35096818330](https://github.com/MikeMoore1337/second-brain/actions/runs/35096818330) | [35097060936](https://github.com/MikeMoore1337/second-brain/actions/runs/35097060936) |

Финальный closeout 17.6 выполняется отдельным PR с `Closes #348`; его merge
SHA, exact-head CI, post-merge CI, deploy, production smoke и закрытие Issue
#348 фиксируются в финальном delivery report после завершения workflow.
