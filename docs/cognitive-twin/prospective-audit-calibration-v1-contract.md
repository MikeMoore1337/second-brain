# Prospective Operation Audit & Calibration v1 — Stage 9 Cognitive Twin v2

Статус документа: **DESIGN / NORMATIVE CONTRACT ONLY**. Это deliverable
Issue [#236](https://github.com/MikeMoore1337/second-brain/issues/236), а не
runtime-модуль. Stage 9A/9B/9C и Stage 9D реализованы поверх этого контракта
в staged implementation issues; этот документ фиксирует границы и не заменяет
их код, focused tests, integration QA или production closeout.

Контракт читается поверх current `main` и следующих merged boundaries:

- [design-roadmap-v1.md](design-roadmap-v1.md);
- [simulate-me-v1-contract.md](simulate-me-v1-contract.md);
- [retrospective-calibration-v1-contract.md](retrospective-calibration-v1-contract.md);
- [active-personal-learning-v1-contract.md](active-personal-learning-v1-contract.md);
- current Decision Journal / Outcome Observation contract в
  [`docs/architecture/vault-contract-v1.md`](../architecture/vault-contract-v1.md)
  и validators `decision_journal.py`;
- current provider-free `BuildSimulateMe`, его result validator и Web
  projection.

`ACCEPT` означает нормативную границу этого design contract, `CHANGE` —
уточнение status/sequencing без изменения Stage 1–8 semantics, `RISK` —
известное ограничение, которое нельзя обходить молча, `DEFER` — отдельный
будущий owner-approved gate.

## 1. Purpose и non-goals

### Purpose

Stage 9 добавляет только одну новую capability: policy-governed фиксацию
результата **явно вызванной foreground Simulate Me operation** до того, как
владелец позже reviewed-образом зафиксирует фактическое решение.

Нормативная цепочка:

```text
explicit Simulate Me request
  -> validated terminal prediction/abstention
  -> durable prospective audit event
  -> later explicit reviewed link to a Decision Journal
  -> exact prediction-vs-actual comparison
  -> deterministic prospective calibration aggregate
```

Audit event является `canonical operational/audit state`, но не становится
пользовательским знанием. `second-brain-vault` остаётся единственным
canonical source of truth для reviewed facts, decisions и outcomes.

### Non-goals

В этот Stage 9 v1 не входят:

- общий telemetry/event-sourcing framework Second Brain;
- автоматический audit `Assistant`, `Search`, `Retrieval`, Active Learning,
  всех Web действий или provider calls;
- изменение Stage 6 Simulate Me matching, option namespace, result DTO,
  policy или current Web/API;
- audit persistence в `second-brain-vault`, новый vault note type или новый
  canonical YAML field;
- автоматическое связывание prediction с Decision Journal;
- fuzzy matching, LLM semantic matching, similarity labels или timestamp-only
  matching;
- numeric confidence, probability calibration, Brier score, ECE, learned
  threshold, policy auto-tuning, ML training, hidden user score,
  personality/risk/domain scoring;
- Behavioral Self Model, Growth Engine, Adaptive Cognitive Twin или Stage 10+;
- vector DB, embeddings, heavy database/framework dependency или новый
  provider;
- background prediction, watcher, scheduler, silent consent, public telemetry,
  cloud export или browser storage as authority.

Если terminal result не был строго validated или не был durably committed,
он не считается prospective-audited operation и не попадает в calibration.

## 2. Terminology

| Термин | Нормативное значение |
| --- | --- |
| `Simulate Me request` | Текущий bounded `SimulateMeRequest` с caller-owned `{id, label}` options. IDs уникальны только внутри одного request. |
| `terminal result` | Валидированный current `SimulateMeResult` с exact Stage 6 identity. Допустимы `prediction` и три закрытых `abstention` codes. |
| `prediction` | `Simulate Me` result с ровно одним selected request-local option ID; это не recommendation и не fact. |
| `abstention` | Валидированный result без selected option с одним exact abstention code. `insufficient_or_invalid_current_context` тоже является terminal abstention, если его вернул validator. |
| `prospective audit event` | Immutable operational record одной audited terminal operation, созданный после validation и до возможного будущего actual decision. |
| `audit history` | Текущая verified append-only log generation событий и explicit link records вне vault. Это authority только для operational history. |
| `actual decision` | Later reviewed Decision Journal с exact Stage 2 `observed_decision` / `decision` semantics и explicit `evidence_at`. Outcome Observation не является actual-choice authority. |
| `explicit link` | Owner-reviewed operational record, который связывает ровно один audit event с ровно одной current Decision Journal UUID и explicit option mapping. |
| `pending/unlinked` | Active event, для которого нет accepted valid link и нет terminal invalid/unavailable link state. |
| `invalid linkage` | Link attempt/state, отклонённый из-за нарушения deterministic contract: malformed target, mapping, time или immutable fingerprint. |
| `unavailable linkage` | Link attempt/state, для которого required current target/source недоступен или был удалён; это не mismatch. |
| `retrospective calibration` | Current-vault replay исторических Journal cases с masking и cutoff; prediction не существовал в момент решения. |
| `prospective calibration` | Сравнение immutable pre-recorded audit prediction/abstention с later explicit reviewed actual decision. |
| `operational audit state` | Служебная history операции. Она не является `Personal Memory`, `Decision Journal`, `Outcome Observation`, Self Model evidence, user statement или canonical user fact. |

## 3. User evidence vs operational audit state matrix

| Представление | Authority / место | Статус | Что разрешено выводить |
| --- | --- | --- | --- |
| Reviewed Personal Memory note | `second-brain-vault` + existing Safe Write | Canonical user evidence | Только то, что пользователь явно подтвердил в reviewed note. |
| Decision Journal note | `second-brain-vault` + Stage 2 validator | Canonical reviewed decision | Situation, available options, choice и pre-choice fields по текущему contract. |
| Outcome Observation | `second-brain-vault` + Stage 2 relation | Canonical later observation | Поздний result/reassessment, но не изменение historical prediction и не actual-choice authority для Stage 9. |
| Current `SimulateMeResult` | Process/application memory | Ephemeral derived state | Prediction или abstention на текущую operation; сам по себе не history. |
| `ProspectiveAuditEventV1` | Separate owner-only operational store | **Canonical operational/audit state** | Что validated Simulate Me result был зафиксирован с конкретной policy/request namespace и digest. Не user fact. |
| Explicit `ProspectiveDecisionLinkV1` | Same operational boundary + current vault validation | Canonical operational relation | Что owner явно связал event с конкретным current Journal и mapping; не добавляет поле в Journal. |
| Prospective calibration aggregate | Rebuilt from active audit/link history | Derived read model | Только bounded counts/ratios с visible denominators. Не quality guarantee, confidence или personality claim. |
| Active Learning question, ignore/reject/answer | Stage 8 process/review boundary | Derived or ephemeral state until Safe Write | Не calibration outcome и не hidden actual decision. Только reviewed answer может стать new user evidence. |
| Browser payload, cache, Web response | Web transport | Non-authoritative projection | Никогда не создаёт, изменяет или доказывает audit history. |

Главное правило: deletion/rebuild calibration aggregate не удаляет
пользовательские notes, а deletion audit history не удаляет и не меняет
Decision Journal/Outcome. Обратная запись audit state в vault запрещена.

## 4. Prospective audit event DTO

### 4.1. Exact event shape

Имена ниже — нормативная shape v1. Реализация может использовать эквивалентные
Python types, но не может менять поля, закрытые значения, bounds или
immutability semantics.

```text
AuditHashV1 = "sha256:" + 64 lowercase hexadecimal characters

ProspectiveAuditOptionV1 {
  id:                 SimulateMeOption.id       # original request-local ID
  ordinal:            int                       # 0..7, caller order
  label:              string                    # normalized bounded label,
                                                # 1..256 UTF-8 bytes
  label_fingerprint:  AuditHashV1              # digest of normalized label
}

ProspectiveAuditRequestV1 {
  query_fingerprint:   AuditHashV1              # query body is not stored
  options:             tuple[ProspectiveAuditOptionV1, ...]  # 1..8
  options_fingerprint: AuditHashV1
  request_fingerprint: AuditHashV1
}

ProspectiveAuditResultV1 {
  kind:                "prediction" | "abstention"
  predicted_option_id: string | null             # exact stored option ID
  abstention_code:     "no_matching_evidence"
                     | "multiple_options_supported"
                     | "insufficient_or_invalid_current_context"
                     | null
}

ProspectiveAuditSourceV1 {
  result_fingerprint:       AuditHashV1
  source_refs_fingerprint:  AuditHashV1
  evidence_ref_count:       int                  # 0..20
  contextual_ref_count:     int                  # 0..20
  temporal_caveat_count:    int                  # 0..20
}

ProspectiveAuditEventV1 {
  contract_version:          "prospective-audit-calibration-v1"
  version:                   "1"
  event_type:                "simulate_me_terminal_result"
  event_id:                  UUIDv7 lowercase string
  operation_id_fingerprint: AuditHashV1
  created_at:                RFC3339 aware datetime, canonical UTC
  capture_mode:              "explicit-foreground-simulate-me-v1"

  derivation_version:        "simulate-me-v1"
  policy_id:                 "simulate-me-direct-exact-v1"
  policy_fingerprint:        "sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a"

  request:                   ProspectiveAuditRequestV1
  result:                    ProspectiveAuditResultV1
  source:                    ProspectiveAuditSourceV1
}
```

`event_id` — server/store-owned stable UUIDv7. Он не вычисляется из query,
labels, timestamp или result и не меняется при linking, correction или
retention. `operation_id_fingerprint` поддерживает explicit idempotency, но
raw operation token в store не сохраняется. Stage 9 wrapper создаёт или
получает operation ID до вызова Stage 6; current Stage 6 request schema этим
не изменяется.

`created_at` — точное operational prediction time: момент, когда validated
terminal result принимается в durable audit commit boundary. Это единственное
authority для ordering prediction и later link; storage sequence является
дополнительным integrity guard. Успешная audited operation не публикуется
caller-у до завершения commit/fsync.

Для `prediction`:

- `predicted_option_id` обязан ровно соответствовать одному `request.options.id`;
- `abstention_code` обязан быть `null`.

Для `abstention`:

- `predicted_option_id` обязан быть `null`;
- `abstention_code` обязан быть ровно одним закрытым code.

Нельзя хранить `selected_option` второй копией: human label берётся только из
immutable `request.options` по request-local ID. `label` сохраняется в
нормализованной Stage 6 форме только для explicit later review; он не является
canonical option identity и не используется как automatic semantic match.

`query` и note/evidence bodies не сохраняются. `query_fingerprint`,
`request_fingerprint`, `result_fingerprint` и `source_refs_fingerprint` не
обратимы в исходный текст при обычном использовании. `source` содержит только
bounded counts и digest validated Stage 6 refs: raw claim, note UUID, path,
title, body, URL и front matter в event не попадают.

### 4.2. Integrity envelope

На disk event сериализуется как один `AuditLogEnvelopeV1`:

```text
AuditLogEnvelopeV1 {
  generation_id:          UUIDv7 lowercase string
  sequence:               positive uint64, contiguous in one generation
  event:                  ProspectiveAuditEventV1
  previous_record_digest: AuditHashV1 | null
  record_digest:          AuditHashV1
}
```

`record_digest` — SHA-256 canonical UTF-8 JSON envelope без самого
`record_digest`; `previous_record_digest` образует chain. Canonical JSON имеет
`sort_keys=true`, separators `,`/`:`, direct UTF-8, no BOM, no trailing
newline, no floats, `NaN`, `Infinity` или dynamic keys. UUID, datetime, enum и
digest serialization фиксированы этим contract.

Fingerprint rules для event фиксированы так же: `operation_id_fingerprint`,
`label_fingerprint` и `query_fingerprint` — SHA-256 UTF-8 bytes normalized
value; `options_fingerprint`
— SHA-256 canonical JSON ordered list `{id, ordinal, label_fingerprint}`;
`request_fingerprint` — SHA-256 canonical JSON
`{query_fingerprint, options_fingerprint}`; `result_fingerprint` — SHA-256
canonical serialization полного validated `SimulateMeResult`; и
`source_refs_fingerprint` — SHA-256 canonical serialization его bounded
`evidence_refs`, `contextual_evidence_refs` и `temporal_caveats`. Raw source
values участвуют только в расчёте digest и не становятся stored fields;
нормализованные caller-owned option labels — единственное bounded text
исключение, явно перечисленное в DTO.

Chain обнаруживает partial append, изменение payload, пропуск sequence,
удаление середины и смену порядка. Это detection of corruption/tampering, а
не обещание защиты от владельца машины, который может переписать весь файл.
Для такого adversarial guarantee нужен отдельный key-management/security gate;
секреты и MAC keys в audit record v1 не хранятся.

## 5. Lifecycle / state machine

Неперсистентные состояния до terminal result не являются audit records.

```text
NOT_AUDITED
  -> REQUEST_VALIDATED
  -> TERMINAL_RESULT_VALIDATED
  -> AUDIT_COMMITTING
  -> ACTIVE_UNLINKED
       -> LINKED_VALID
       -> LINK_INVALID
       -> LINK_UNAVAILABLE
       -> EXPIRED
       -> TOMBSTONED
  -> AUDIT_REJECTED (no event)
```

Нормативные переходы:

| Состояние/событие | Результат |
| --- | --- |
| Invalid request или unknown/extra request field | `AUDIT_REJECTED`; event не создаётся. |
| Valid request, valid Stage 6 `prediction` | Один `prediction` event после durable commit. |
| Valid request, valid Stage 6 `no_matching_evidence` или `multiple_options_supported` | Один `abstention` event; отсутствие prediction не скрывается. |
| Valid request, valid `insufficient_or_invalid_current_context` | Один `abstention` event с этим exact code. Нельзя заменить его на `no_matching_evidence`. |
| Stage 6 result validator failure, policy mismatch или impossible DTO | `AUDIT_REJECTED`; результат не аудируется и не калибруется. |
| Cancellation до validated terminal result | `AUDIT_REJECTED`; event не создаётся. |
| Hard source/storage unavailable без validated terminal result | `AUDIT_REJECTED` или fixed unavailable error; synthetic abstention запрещён. |
| Same explicit operation retry с тем же operation ID и теми же canonical request/result fingerprints | Возвращается существующий immutable event; второй event не добавляется. |
| Same operation ID с любым отличающимся fingerprint | `AUDIT_IDEMPOTENCY_CONFLICT`; новый event не создаётся. |
| Старый/replayed `SimulateMeResult`, переданный в standalone writer | `AUDIT_STALE_OR_REPLAYED`; standalone result writer не является разрешённым capture boundary. |
| Store corruption, append/fsync failure или integrity mismatch | `AUDIT_STORE_UNAVAILABLE`/`AUDIT_STORE_CORRUPT`; partial aggregate и successful audited result запрещены. |
| No link before retention deadline | `ACTIVE_UNLINKED`; это pending, не mismatch и не abstention. |
| Explicit link passes all gates | `LINKED_VALID`; prediction/actual comparison становится evaluable. |
| Explicit link fails deterministic target/time/mapping validation | `LINK_INVALID` или `LINK_UNAVAILABLE`; event prediction не меняется. |
| Retention deadline reached | `EXPIRED`; event больше не входит в active aggregate и не может быть linked. |
| Owner delete/reset или explicit correction | Append tombstone, затем bounded physical purge; historical event не редактируется. |

Одна explicit foreground operation создаёт максимум один `event_id`. Ни один
переход не создаёт Personal Memory, Journal, Outcome, Self Model claim или
Active Learning answer.

## 6. Capture boundary и terminal-case policy

Единственная разрешённая boundary для будущего Stage 9A:

```text
Stage9 foreground wrapper
  -> validate current SimulateMeRequest
  -> execute current BuildSimulateMe exactly once
  -> validate returned SimulateMeResult and exact Stage 6 identity
  -> build event from request/result/fingerprints
  -> append + fsync + chain verification
  -> release audited terminal response
```

Не разрешено принимать result, который был передан caller-ом из browser,
cache, старого request или replay. A result object без proof of the same
one-shot operation не имеет audit authority.

| Case | Audit decision | Calibration meaning |
| --- | --- | --- |
| Prediction | Persist event with `kind=prediction`, selected request-local ID и exact Stage 6 policy. | `predictions += 1`. |
| Valid abstention | Persist event with `kind=abstention` and exact code, including insufficient current context. | `abstentions += 1`; это не invalid и не mismatch. |
| Request validation failure | No event. | Не входит в `audited_operations`. |
| Result validation/policy failure | No event; fixed safe error. | Не входит в aggregate. |
| Cancellation before terminal result | No event. | Не входит в aggregate. |
| Cancellation/storage failure after result but before commit | No audited success and no event unless idempotent lookup proves a prior committed event. | Не считать предсказанием. |
| Current source unavailable with a valid Stage 6 terminal abstention | Audit the returned abstention exactly. | Count as abstention; later actual link is allowed only through explicit link rules. |
| Current source unavailable without a validated result | No event. | Не превращать unavailable operation в abstention. |
| Duplicate/retry with explicit same operation ID | Reuse exact existing event only after request/result/policy fingerprints match. | Count once. |
| Retry without reusable operation ID | Treat as a new explicit operation; no request/time/fingerprint heuristic deduplication. | Separate events are valid and visible. |
| Stale/replayed result | Reject; never backdate `created_at` or reuse old `event_id`. | No calibration record. |

Audit capture никогда не вызывает Safe Write, не создаёт audit-derived user
evidence и не меняет current vault. Stage 6 current implementation remains
ephemeral until a separate Stage 9 runtime task composes this boundary.

## 7. Storage decision

### 7.1. Options

| Вариант | Плюсы | Минусы / решение |
| --- | --- | --- |
| Append-only local JSONL log outside vault | Stdlib-only, inspectable, natural append, deterministic replay, no new dependency, easy local/production format parity | Нужны cross-process lock, fsync, chain validation и explicit compaction for deletion. **Выбран.** |
| Small dedicated SQLite/operational DB | Transactions, indexes и удобные queries | Новая persistence technology, migrations, backup/locking/driver surface и больше state, чем нужно для bounded v1. **Отклонён.** |
| In-memory или browser-only history | Нет disk state | Не переживает process/browser restart, не даёт prospective calibration и нарушает audit authority. **Отклонён.** |
| `second-brain-vault` Markdown/YAML | Уже есть canonical storage | Смешивает user evidence и operational tracking, создаёт скрытую schema/write surface. **Запрещён.** |

### 7.2. Chosen boundary

Выбирается небольшой dedicated local operational store на базе append-only
JSONL streams вне configured vault root:

- `events` stream для `AuditLogEnvelopeV1`;
- `links` stream для immutable link decisions/tombstones;
- atomic `manifest` с `generation_id`, format version, last sequence и last
  digest.

Имена — логические relative entries под явно переданным application-owned
operational data root. Этот contract не задаёт абсолютный путь, не создаёт
новую environment variable и не разрешает default внутри vault, web static
или browser profile. Path containment, symlink rejection и owner-only file
permissions обязательны для будущего writer-а.

Authority и rebuildability разделены:

- verified active event/link streams — **non-rebuildable canonical operational
  history**; current vault не может восстановить, какой request/result реально
  был зафиксирован;
- prospective calibration aggregate — rebuildable derived projection из
  streams;
- Web/API response, cache и report — disposable projections.

### 7.3. Atomicity, corruption и platform parity

Будущий store обязан:

1. проверять manifest и всю chain под lock до read/write;
2. брать process-wide exclusive writer lock на Windows и Linux;
3. сериализовать один complete JSON line, append с exclusive handle, flush и
   fsync/эквивалентный durable flush;
4. считать record committed только после успешного flush и chain/read-back
   validation;
5. брать stable read snapshot под compatible lock до calibration; partial tail
   не принимается;
6. fail closed при malformed JSON, missing newline, duplicate event ID,
   sequence gap, wrong generation, digest mismatch, invalid timestamp,
   unknown field или manifest mismatch.

Нельзя молча игнорировать broken line, обрезать tail, пересчитывать chain,
продолжать после corrupt record или публиковать partial aggregate. Единственное
разрешённое восстановление — отдельный explicit owner reset/delete/recovery
gate; automatic repair и heuristic salvage не входят в Stage 9 v1.

Local и production используют тот же file format, bounds, lock, error и
retention contract. Shared multi-host filesystem, network replication и
несколько независимых writers без общей OS lock authority не поддерживаются;
это отдельный storage gate. Windows/Linux behavior должен быть покрыт future
integration QA, но technology-specific semantics не становятся user-facing.

## 8. Retention, delete и reset

### 8.1. Fixed retention

Stage 9 v1 принимает фиксированную policy:

```text
retention_policy = prospective-audit-retention-180d-v1
retention = 180 * 24 UTC hours from event.created_at
```

Retention относится к event и его link history. Нет indefinite retention,
automatic extension при linking или скрытого per-user tracking. Изменение
срока требует новой reviewed policy/version; runtime config не может молча
изменить denominator semantics.

После deadline event логически `EXPIRED` и немедленно исключается из
calibration/link UI. Physical purge выполняется под exclusive lock до выдачи
операции purge/reset success; если process не запускался, bytes могут ждать
следующего explicit store access, но никогда не читаются как active history.

### 8.2. Owner delete/reset

- Owner может удалить отдельный event или link history explicit action.
- Сначала добавляется fixed-code tombstone (`owner_delete`, `owner_reset` или
  `owner_invalidate`), затем bounded compaction физически удаляет payload.
- Tombstone не редактирует prediction и не превращает его в mismatch; deleted
  event исключается из всех active denominators. До physical purge он считается
  deleted, а не pending.
- Full reset под lock создаёт новую empty generation и физически удаляет
  прежние events, links и tombstones. После reset prospective calibration
  возвращает zero counts и `null` ratios; history не восстанавливается из
  vault, Journal или current Simulate Me.
- Удаление audit event не удаляет и не меняет Decision Journal, Outcome,
  Personal Memory или retrospective calibration.

Corruption не исправляется обычным delete одного guessed record: store остаётся
unavailable до explicit recovery/reset. Это предотвращает скрытое изменение
history.

### 8.3. Backup и machine boundary

Audit store не входит в `second-brain-vault`, Git, Vault Sync или обычный vault
backup. По умолчанию он остаётся на той же машине и не отправляется в сеть.
Если владелец отдельно включает application backup, audit files должны быть
отдельно помечены как operational/private data и наследовать retention,
delete/reset и access policy; backup не может быть публичным или silent cloud
export. Удаление считается завершённым только после обработки разрешённых
локальных backup copies либо явного fixed error, что это не выполнено.

## 9. Privacy и security

### 9.1. Что хранится

Хранятся только bounded data, объективно нужные для later review и
deterministic comparison:

- stable `event_id`, operation fingerprint, exact creation time и format/policy
  identity;
- request-local option IDs в caller order и normalized human labels с
  fingerprints — только чтобы owner мог review-ить explicit mapping;
- query/request/result/source fingerprints;
- prediction/abstention terminal state и bounded ref counts;
- integrity chain metadata;
- отдельный owner-created link с audit event ID, current Decision Journal UUID,
  explicit option mapping и target fingerprint.

Labels — caller-owned operational text, а не user evidence; они bounded,
retained только 180 дней и не используются для semantic matching.

### 9.2. Что категорически не хранится и не отправляется

Запрещены в event, link, error, log line и telemetry:

- raw `query` и произвольный prompt text;
- raw private evidence bodies, Journal sections, Outcome bodies, claim text,
  titles, paths, source URLs и front matter;
- raw evidence UUID lists — только bounded digest; UUID target в explicit link
  нужен для deterministic relation и остаётся owner-only;
- secrets, tokens, credentials, cookies, auth headers и absolute paths;
- LLM/provider payload, network response или browser local storage;
- automatic behavioral profile, hidden score, domain/personality/risk label;
- data from Assistant, Search/Retrieval, Active Learning questions or
  `ignore/reject/answer` as implicit outcome.

Ошибки используют fixed safe codes/messages и не содержат exception text,
request labels, event payload, filesystem path или private diagnostics. Для
production Web audit boundary — owner-only, same-origin/authenticated и
`no-store`; browser никогда не является persistence authority.

### 9.3. Operational privacy invariant

Audit создаётся только для explicit foreground Simulate Me request, при
включённой Stage 9 operation boundary. Нет watcher, scheduler, automatic audit
всех Web actions, background capture или export. Если owner отключил Stage 9
для operation, event не создаётся и operation не маскируется под audited
calibration sample.

## 10. Deterministic prediction -> actual Decision Journal linkage

### 10.1. Explicit link DTO

Link record хранится вне vault и создаётся только после owner review:

```text
ProspectiveOptionMappingV1 {
  audit_option_id:             string       # existing event option ID
  decision_option_index:       int          # 0..19, current Journal order
  decision_option_fingerprint: AuditHashV1  # current normalized label digest
}

ProspectiveDecisionLinkV1 {
  version:                     "1"
  link_id:                     UUIDv7 lowercase string
  audit_event_id:              UUIDv7 lowercase string
  decision_id:                 UUIDv7 lowercase string
  linked_at:                   RFC3339 aware datetime, canonical UTC
  decision_record_fingerprint: AuditHashV1
  actual_chosen_option_index:  int          # current Journal option index
  mapping:                     tuple[ProspectiveOptionMappingV1, ...]
  mapping_basis:               "owner-explicit-v1"
}
```

Backend обязан заново прочитать current canonical scan, разрешить ровно один
`decision_id` и подтвердить exact Stage 2 Decision Journal pair/body,
UUIDv7 identity, current options, chosen option и exact `evidence_at`. Client
не может прислать body, chosen option, labels, evidence refs или target path
как authority.

Для `prediction` mapping обязан покрывать одновременно:

1. predicted audit option ID;
2. actual chosen Journal option index.

Entries должны быть injective: один audit ID не может быть связан с двумя
Journal option indices и один Journal index — с двумя audit IDs. Для
`abstention` option mapping не нужна для самого link, потому что selected
option отсутствует; current actual Journal choice всё равно валидируется и
сохраняется в link snapshot.

`decision_record_fingerprint` включает current Journal UUID, exact decision
time, ordered available options и chosen option. При изменении или удалении
Journal target accepted link становится invalid/unavailable; система не
пересобирает mapping и не «освежает» его автоматически.

### 10.2. Запрещённые authority

Ни один из следующих механизмов не создаёт accepted link:

- совпадение human labels без explicit owner mapping;
- fuzzy, substring, synonym, embedding или LLM semantic matching;
- `decision_id` внутри Outcome Observation без отдельного link;
- совпадение только по timestamp, UUID order, path, title или filename;
- automatic closest/first/most-recent Journal selection;
- current Search hit, browser state или cached body.

Если в будущем понадобится additive `audit_event_id` field в Decision Journal,
это отдельный schema/design gate. Issue #236 schema change не создаёт.

## 11. Option comparison semantics

| Слой | Identity | Authority |
| --- | --- | --- |
| Simulate Me request option | `audit_option_id` + caller label в одном request | Current Stage 6 request; ID действует только внутри event. |
| Audit event option | Same ID, normalized bounded label и immutable label fingerprint | Immutable event; не canonical user fact. |
| Decision Journal option | Ordered body value с `decision_option_index`; отдельного canonical option ID нет | Current reviewed Journal body. |
| Chosen actual option | Journal `chosen_option`, resolved by exact Stage 2 parser to one index | Current canonical Journal, not Outcome. |
| Comparison key | Explicit mapping `audit_option_id -> decision_option_index` | Owner-confirmed link only. |

Сравнение выполняется только после validation обеих namespaces:

```text
mapped_predicted_index == actual_chosen_option_index
    -> exact_option_match
mapped_predicted_index != actual_chosen_option_index
    -> mismatch
missing/ambiguous/stale mapping
    -> invalid_or_unavailable_linkage, never mismatch
```

Label equality может быть показана владельцу для review и fingerprint drift
check, но не является сравнением. Разные labels могут быть связаны только
explicit owner mapping; одинаковые labels не создают mapping автоматически.
No lower/casefold, alias, translation, date normalization или semantic
guessing добавляется на link boundary.

Abstention с valid actual link учитывается как linked actual decision, но не
как match или mismatch: у abstention нет predicted option. Это позволяет
видеть distinction «система воздержалась» против «система ошиблась».

## 12. Temporal integrity

### 12.1. Authorities и ordering

- `audit_event.created_at` — store-assigned aware wall-clock UTC time durable
  capture; это prediction timestamp.
- `Decision Journal.evidence_at` с `evidence_at_precision=exact` — reviewed
  decision time authority. `created`, `updated`, file mtime и link time не
  подменяют его.
- `Decision Journal.created` — storage-time guard: для accepted prospective
  link target должен быть создан **строго после** `audit_event.created_at`.
- `link.linked_at` — store-assigned time explicit review завершения.
- `sequence`/link append order — integrity guard, но не замена event time.

Accepted link обязан удовлетворять одновременно:

```text
audit_event.created_at < decision.evidence_at <= link.linked_at
audit_event.created_at < decision.created <= link.linked_at
audit_event.sequence < link.sequence
```

Все timestamps валидируются как aware RFC3339 и сравниваются после UTC
normalization. Missing/invalid/unknown precision, equal boundary where strict
`<` нужен, invalid clock serialization или sequence ambiguity дают safe
invalid/unavailable linkage.

### 12.2. Что считается retroactive/invalid

Link отклоняется, если:

- Journal `created <= audit.created_at`: decision note могла быть известна до
  prediction;
- `decision.evidence_at <= audit.created_at`: actual time не позже durable
  prediction record;
- `decision.evidence_at` unknown/non-exact или `created` invalid;
- target body/identity changed после link fingerprint или target deleted;
- link пытается backdate event, заменить prediction или использовать old
  result;
- system clock/source не позволяет доказать строгий порядок.

Это conservative false negative policy. Она не утверждает, что физическое
решение пользователя невозможно было знать до созданной note: система знает
только reviewed Journal и operational timestamps. Любая такая uncertainty —
RISK и не исправляется heuristics.

Stage 9 wrapper не возвращает audited success до durable commit, поэтому
обычный user flow не может получить Stage 9 prediction и продолжить внутри
того же operation без audit record. External action outside system remains
outside this contract.

### 12.3. No retroactive editing

`created_at`, policy identity, request namespace, result kind,
`predicted_option_id`, fingerprints и event ID immutable. Нельзя принять old
result сегодня и записать его с прошлым timestamp. Correction означает
owner-explicit tombstone/invalidation и, если нужно, новая отдельная explicit
operation; historical prediction не переписывается.

## 13. Immutability, tombstones и correction

- Event and accepted link streams append-only.
- Ordinary metadata update отсутствует. Любая correction, invalidation или
  delete — новый fixed-shape tombstone/superseding record.
- Tombstone не является prediction и не входит в `audited_operations`.
- Active aggregate использует latest non-tombstoned event/link state within
  current generation; superseded failed link attempts не считаются рядом с
  later accepted link.
- `LINK_INVALID`/`LINK_UNAVAILABLE` не меняют event и не превращают prediction
  в mismatch.
- Corrupted record не пропускается и не исключается heuristic; вся store
  operation fail-closed.
- Physical purge после tombstone удаляет payload по explicit owner action и
  создаёт новую verified generation при необходимости. После purge старый
  event нельзя восстановить из current vault.
- Deleted/expired events исключаются из all active metric denominators. Если
  aggregate показывает diagnostic counts of deleted/expired records, они
  находятся отдельно от calibration metrics; после physical purge даже эти
  counts могут исчезнуть.

Изменение display metadata link не может менять historical prediction. Если
link mapping надо исправить, старый link tombstone-ится и создаётся новый
explicit mapping с новым `link_id`.

## 14. Prospective calibration DTO, metrics и formulas

### 14.1. Aggregate DTO

```text
ProspectiveCalibrationRatioV1 {
  numerator:   int >= 0
  denominator: int > 0
}

ProspectiveCalibrationMetricsV1 {
  audited_operations:             int >= 0
  predictions:                    int >= 0
  abstentions:                    int >= 0
  linked_actual_decisions:        int >= 0
  pending_unlinked_events:        int >= 0
  invalid_linkage_events:         int >= 0
  unavailable_linkage_events:     int >= 0
  exact_option_matches:           int >= 0
  mismatches:                     int >= 0

  coverage:                       ProspectiveCalibrationRatioV1 | null
  actual_linkage_coverage:        ProspectiveCalibrationRatioV1 | null
  evaluated_prediction_coverage:  ProspectiveCalibrationRatioV1 | null
  accuracy_non_abstained:         ProspectiveCalibrationRatioV1 | null
}

ProspectiveCalibrationResultV1 {
  contract_version:       "prospective-audit-calibration-v1"
  derivation_version:     "prospective-calibration-v1"
  policy_id:               "prospective-simulate-me-explicit-link-v1"
  policy_fingerprint:      AuditHashV1
  retention_policy:        "prospective-audit-retention-180d-v1"
  metrics:                 ProspectiveCalibrationMetricsV1
  invalid_linkage_by_code:     tuple[ProspectiveCalibrationCountV1, ...]
  unavailable_linkage_by_code: tuple[ProspectiveCalibrationCountV1, ...]
}

ProspectiveCalibrationCountV1 {
  code:  fixed enum string
  count: int >= 0
}
```

Fixed code order is part of the DTO and includes zero counts:

```text
invalid_linkage:
  decision_target_invalid
  decision_identity_conflict
  decision_time_invalid
  decision_precedes_prediction
  decision_note_created_before_prediction
  decision_record_changed
  option_mapping_invalid
  chosen_option_unmapped
  link_fingerprint_mismatch

unavailable_linkage:
  audit_event_missing_or_deleted
  decision_target_unavailable
  canonical_scan_unavailable
  decision_target_expired
```

The aggregate has no per-event body, query, labels, Journal text, Outcome
text, path, UUID list, raw mapping labels or user profile. Optional API event
list for explicit linking is a separate owner-only surface and is not the
calibration aggregate.

### 14.2. Population and exact counts

Aggregate берёт только current generation events, которые:

1. прошли full integrity validation;
2. не tombstoned, не expired и не physically deleted;
3. имеют valid `ProspectiveAuditEventV1` и exact Stage 6 identity;
4. попадают в явно выбранный bounded report window, если future API разрешит
   window; default — вся active retention window.

Для этой population:

```text
audited_operations = predictions + abstentions

linked_actual_decisions = linked_predictions + linked_abstentions

audited_operations = linked_actual_decisions
                    + pending_unlinked_events
                    + invalid_linkage_events
                    + unavailable_linkage_events

linked_predictions = exact_option_matches + mismatches
```

`invalid_linkage_events` и `unavailable_linkage_events` считаются по latest
active link state одного event, а не по числу попыток. One event не может
одновременно быть pending, linked, invalid или unavailable.

### 14.3. Ratios и denominators

```text
coverage
  = predictions / audited_operations
  # fraction of audited operations where Stage 6 produced a prediction;
  # abstentions remain in the denominator

actual_linkage_coverage
  = linked_actual_decisions / audited_operations
  # how much audited history currently has a valid reviewed actual decision;
  # this is linkage coverage, not model quality

evaluated_prediction_coverage
  = linked_predictions / predictions
  # fraction of non-abstained predictions that have a valid actual target

accuracy_non_abstained
  = exact_option_matches / linked_predictions
  # only linked predictions; abstentions, pending, invalid and unavailable
  # cases never become mismatches
```

Each ratio is represented as exact integer numerator/denominator, never as a
float or percentage. Ratio is `null` exactly when its denominator is zero:

- `coverage == null` iff `audited_operations == 0`;
- `actual_linkage_coverage == null` iff `audited_operations == 0`;
- `evaluated_prediction_coverage == null` iff `predictions == 0`;
- `accuracy_non_abstained == null` iff `linked_predictions == 0`.

No minimum-sample claim is made. No ratio is confidence, probability,
calibration quality guarantee, personality trait or hidden user score.

### 14.4. Exact policy separation

Prospective calibration uses its own `derivation_version`, `policy_id`,
retention/link rules and fingerprint. It must also validate the exact embedded
Stage 6 identity from every event. The report cannot combine its counts with
Retrospective Calibration into a universal `accuracy` score.

Normative prospective policy serialization is this one-line ASCII JSON, encoded
as UTF-8 with `sort_keys=true`, separators `,`/`:`, no BOM and no trailing
newline:

```json
{"actual_target":"stage2-decision-journal-explicit-link-v1","coverage":"prediction-over-audited-operations-v1","linkage":"latest-explicit-state-exclusive-v1","metrics":"counts-and-integer-ratios-no-confidence-v1","option_mapping":"owner-explicit-injective-index-v1","retention":"prospective-audit-retention-180d-v1","temporal":"audit-created-before-decision-evidence-and-note-created-v1","version":"1"}
```

`policy_fingerprint` — `sha256:` plus the lowercase SHA-256 digest of these
canonical bytes. It changes when retention, linkage, mapping, temporal or
metric semantics change; the v1 expected value is
`sha256:f6a3229ecdc547bb16f9426d1b78d6e5d06e2355954ca30a37be90bdaec1bbc1`;
it is not a model score.

`Outcome Observation`, actual result, reassessment и later outcome do not enter
any numerator/denominator. Stage 9 actual target is only the reviewed Journal
choice; outcome remains a separate canonical evidence relation.

## 15. Error и corruption behavior

Future public/application boundaries use fixed safe codes/messages only:

| Code | Fixed message | Scope |
| --- | --- | --- |
| `PROSPECTIVE_AUDIT_INVALID_REQUEST` | `prospective audit request failed validation` | Invalid bounded Stage 9 operation control. |
| `PROSPECTIVE_AUDIT_RESULT_INVALID` | `prospective audit result failed validation` | Stage 6 DTO/policy mismatch or impossible composition. |
| `PROSPECTIVE_AUDIT_STALE_OR_REPLAYED` | `prospective audit result is stale or replayed` | Result is not from current one-shot capture. |
| `PROSPECTIVE_AUDIT_IDEMPOTENCY_CONFLICT` | `prospective audit operation conflicts with existing record` | Same operation ID, different immutable content. |
| `PROSPECTIVE_AUDIT_STORE_UNAVAILABLE` | `prospective audit store is unavailable` | Lock, fsync, path, permissions or read-back failure. |
| `PROSPECTIVE_AUDIT_STORE_CORRUPT` | `prospective audit store is corrupt` | Chain, manifest, schema or JSON integrity failure. |
| `PROSPECTIVE_AUDIT_LINK_INVALID` | `prospective audit link failed validation` | Invalid target/time/mapping/fingerprint. |
| `PROSPECTIVE_AUDIT_LINK_UNAVAILABLE` | `prospective audit link source is unavailable` | Missing/deleted current target or unavailable canonical scan. |
| `PROSPECTIVE_CALIBRATION_UNAVAILABLE` | `prospective calibration source is unavailable` | No verified stable store snapshot. |
| `PROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE` | `prospective calibration result exceeds its byte limit` | Bounded result serialization would exceed fixed cap. |

Errors never include event payload, raw labels/query, UUID list, note body,
absolute path, exception repr, secret or provider detail. Store corruption or
calibration read failure returns no partial aggregate and no silently filtered
records. Invalid/unavailable link categories are per-event aggregate states;
they do not override a top-level corrupt-store failure.

## 16. Concurrency и idempotency

- Event append and link append use one OS-level exclusive lock per operational
  store; lock scope includes read validation, sequence allocation, write,
  flush/fsync and read-back.
- Event `sequence` is contiguous within `generation_id`; link sequence is
  separately monotonic and cannot be used as event time.
- Calibration takes a verified stable snapshot under read-compatible lock. It
  never reads a file while another writer can append a partial line.
- `operation_id` is allocated before Stage 6 execution and remains stable for
  retries of one logical operation. Store compares exact operation, request,
  result and policy fingerprints. It never deduplicates by query, labels,
  selected ID, time, UUID similarity or result equality alone.
- A successful first append followed by lost response is recovered by exact
  idempotent lookup. A conflicting retry fails closed. A retry without the
  original operation ID is a new operation.
- One event may have at most one latest accepted link state. Link corrections
  are serialized append-only supersessions; concurrent conflicting link
  actions fail closed and require fresh owner review.
- Multi-process same-host concurrency is supported only through the specified
  lock. Multi-host/shared-filesystem concurrency is out of scope.

## 17. Relationship to Retrospective Calibration

Сохраняются две different capabilities и две provenance models:

| Capability | Источник prediction | Actual target | Главная limitation |
| --- | --- | --- | --- |
| Retrospective Calibration v1 | Current replay, построенный позже из current vault с pre-choice masking | Historical Journal `chosen_option` | Prediction не существовал при original decision; current vault не является snapshot. |
| Prospective Calibration v1 | Immutable audit event, созданный после validated Simulate Me result до future link | Later explicit reviewed Journal `chosen_option` | Actual linkage и temporal proof зависят от owner review, current note и local clocks. |

Retrospective использует свои `retrospective-calibration-v1` identities,
eligibility, cutoff, exclusion codes и formulas. Prospective использует
`prospective-audit-calibration-v1`, immutable events, explicit mapping и
retention. Их aggregate, coverage и accuracy нельзя сложить, усреднить или
переименовать в общий user/model score.

Prospective event не может быть реконструирован из current vault после
удаления history; retrospective replay не может задним числом создать
prospective event.

## 18. Relationship to Stage 8 Active Personal Learning

Stage 8 остаётся отдельным workflow:

```text
question candidate -> ignore/reject/answer
  -> (только после review/Safe Write) new canonical evidence
```

Следующее запрещено:

- считать вопрос, показ, ignore, reject или answer calibration outcome;
- считать выбранный answer фактическим Decision Journal choice без отдельного
  reviewed Journal;
- создавать audit link из `QuestionResolutionV1` или `AnswerCaptureV1`;
- использовать prospective metrics как automatic Active Learning trigger.

Будущий explicit consumer, который захочет использовать calibration
diagnostics для Stage 8 question policy, требует отдельного versioned design
decision. Current Stage 8 runtime/API/UI semantics этим contract не меняются.

## 19. Web/API boundary implemented in Stage 9D

Stage 9D реализует ограниченный owner-only boundary поверх существующих core
слоёв. Доступны только следующие поверхности:

1. **Read-only prospective calibration aggregate.** Owner-only API возвращает
   versioned counts/ratios, retention/generation/policy identity и safe error.
   Query, labels, bodies, UUID lists и raw event rows по умолчанию не
   раскрываются.
2. **Explicit link review.** Отдельный owner-only read surface показывает
   bounded event option namespace и candidate current Decision Journal records;
   backend сам перечитывает vault и требует explicit mapping/confirmation.
   Browser payload не является authority. Link mutation — operational write,
   не vault write, и проходит CSRF/auth/idempotency/fingerprint checks.
3. **Audit history view.** Только при отдельном privacy decision; default
   surface может показывать event state, result kind, policy, timestamps,
   pending/linked status и safe counts без raw evidence.
4. **Reset/delete control.** Owner-only explicit confirmation, clear scope
   (one event, link history или full generation), retention/delete receipt и
   no-store response. Reset не трогает vault.

Никакие текущие `/api/simulate-me`, Stage 6 DTO или browser storage не
подменены Stage 9D. Production deployment использует уже существующий
явный `web.env`; новые env keys и systemd changes не вводятся. Audit history
view и reset/delete control остаются отдельными future decisions и не входят
в этот UI slice.

## 20. ACCEPT / CHANGE / RISK / DEFER

| Тема | Verdict | Нормативное решение |
| --- | --- | --- |
| Authority | **ACCEPT** | Vault canonical для user facts/decisions/outcomes; separate verified log canonical только для operational audit history. |
| Scope | **ACCEPT** | Только explicit foreground Simulate Me terminal prediction/abstention. |
| Event identity/time | **ACCEPT** | Store-owned UUIDv7, immutable canonical UTC `created_at`, contiguous integrity sequence. |
| Stage 6 identity | **ACCEPT** | Exact `simulate-me-v1`, `simulate-me-direct-exact-v1` и approved SHA-256 fingerprint; no policy substitution. |
| Request privacy | **ACCEPT** | Store bounded normalized option labels for explicit mapping review; never store raw query/private evidence bodies. |
| Capture boundary | **ACCEPT** | Validate terminal result first, append/fsync before audited success; errors/cancel/replay do not create events. |
| Storage | **ACCEPT** | Append-only local JSONL + manifest + chain outside vault; no DB/framework dependency. |
| Integrity | **ACCEPT** | Canonical JSON, SHA-256 record chain, lock, fsync, read-back; corrupt store fails closed. |
| Retention | **ACCEPT** | Fixed 180-day policy, owner delete/reset, no hidden indefinite history or default backup. |
| Linkage | **ACCEPT** | Explicit owner-reviewed external link with current UUID and explicit option mapping; no Decision Journal schema change now. |
| Option semantics | **ACCEPT** | Request IDs, human labels and Journal chosen option remain distinct; labels never infer authority. |
| Temporal proof | **ACCEPT** | Strict `audit.created_at < decision.evidence_at`, target `created > audit.created_at`, link sequence/time after event; conservative fail-closed clock policy. |
| Calibration | **ACCEPT** | Exact counts, linkage/prediction coverage and non-abstained accuracy; visible integer denominators; no probabilistic score. |
| Retrospective separation | **ACCEPT** | No shared universal accuracy; distinct policies, provenance and reports. |
| Stage 8 separation | **ACCEPT** | Questions, ignores, rejects and answers are not hidden actuals or calibration outcomes. |
| Roadmap status | **CHANGE** | Cognitive Twin v1 / Stages 1–8 and Cognitive Twin v2 / Stage 9A–9D are COMPLETE; Issue #236 remains the normative design source and historical Stage 1–8 semantics are unchanged. |
| Local clock / external action | **RISK** | Local timestamps and reviewed Journal can prove only the bounded system ordering; external real-world knowledge before capture is not observable. |
| Corrupt filesystem / multi-host store | **RISK** | Fail closed; recovery and shared multi-host storage need explicit operational gate. |
| Link UI/API, persistence runtime, metrics core | **ACCEPT** | Stage 9A–9D are separate implementation/release gates and are complete; #236 remains contract-only. |
| Additive Decision Journal field | **DEFER** | Not needed for v1 external link; any future schema field requires separate design/migration gate. |
| Provider, ML, confidence, tuning, telemetry, export | **DEFER / FORBIDDEN HERE** | New owner-approved contracts only. |

`HUMAN_REQUIRED: none` для зафиксированной contract-only границы. Риски не
являются разрешением ослабить fail-closed behavior.

## 21. Historical implementation decomposition and completed status

Исторически после отдельного merge этого design contract следующий issue был
ограничен **Stage 9A — prospective audit store/core**. Этот список сохраняет
исходную decomposition и не открывает новую работу:

- provider-free immutable `ProspectiveAuditEventV1`, strict validators и
  canonical fingerprint/serialization;
- one-shot wrapper boundary поверх существующего validated `BuildSimulateMe`
  result для prediction и всех valid abstentions;
- configured operational root outside vault; append-only JSONL events,
  manifest, generation, SHA-256 chain, Windows/Linux lock, flush/fsync и
  read-back validation;
- fixed safe error taxonomy, corruption fail-closed behavior и exact
  operation-id idempotency/conflict rules;
- fixed 180-day logical retention, explicit tombstone/delete/reset semantics
  только для audit store;
- focused synthetic tests для DTO bounds, policy identity, terminal case
  matrix, duplicate/retry, corruption, chain, atomicity seam, no-vault-write,
  no-provider/network и privacy-safe diagnostics.

Stage 9A **не включает**:

- Decision Journal scan/link/mapping или Outcome handling;
- automatic prediction-to-decision relation;
- prospective aggregate metrics (Stage 9C);
- API/Web/UI, browser persistence, deployment или production migration
  (Stage 9D);
- current Stage 6/Stage 8 behavior changes, vault schema, `second-brain-vault`,
  provider, dependency, network, ML or policy tuning.

Предполагаемая последующая decomposition остаётся:

```text
Stage 9A  audit store/core
Stage 9B  explicit reviewed prediction -> Decision Journal linkage
Stage 9C  prospective calibration aggregate core
Stage 9D  Web/API + integration QA/closeout
```

Эта decomposition выполнена последовательно: Stage 9A store/core, Stage 9B
explicit linkage, Stage 9C calibration и Stage 9D Web/API + integration QA.
После closeout Issue #244 Stage 9 runtime считается **COMPLETE** в production;
Stage 10+ и новые provider/ML/privacy/schema решения этим документом не
запускаются.
