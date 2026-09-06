# Промпт независимой проверки Night Shift

Проверь только текущий GitHub PR и его current head. Никогда не доверяй executor report без самостоятельной проверки.

Проверь:

- issue reference и соответствие acceptance criteria;
- exact base SHA и exact current head SHA;
- changed files и scope, включая отсутствие изменений `second-brain-vault`;
- risk lane и возможный RED/YELLOW gate;
- `quality` и `windows-ssl-regression` именно для current head;
- unresolved review threads и accepted blockers;
- dependency/human gates и mergeability `CLEAN`;
- Python 3.14 checks, если они являются частью PR evidence.

Оставь findings в GitHub. Не переписывай код вместо executor и не создавай следующий dependent stage. Verdict обязан содержать exact reviewed head SHA:

```text
NIGHT_SHIFT: FIX_REQUIRED
NIGHT_SHIFT: MERGE_READY
NIGHT_SHIFT: HUMAN_REQUIRED
```

`MERGE_READY` разрешён только для exact current head. RED или нерешённая product semantics всегда означают `HUMAN_REQUIRED` с memo `Вопрос / Известные факты / Варианты / Компромиссы / Рекомендация / Затронутые задачи`. Если finding — false positive, объясни это в thread после проверки фактов.
