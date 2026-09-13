const VALUE_LABELS: Readonly<Record<string, string>> = {
  area: "Область",
  absent: "Отсутствует",
  active: "Действующее",
  abstention: "Отказ от прогноза",
  aligned: "Сопоставлено",
  belief: "Убеждение",
  candidate_not_found: "Кандидат не найден",
  changed: "Изменённое",
  context_budget_exceeded: "Превышен лимит контекста",
  current: "Текущее",
  current_source_revalidated: "Текущий источник перепроверен",
  current_vault_rebuild: "Перестроение по текущему хранилищу",
  created_after_cutoff_excluded: "Исключено: создано после отсечения",
  decision: "Решение",
  decision_rule: "Правило решения",
  deleted: "Удалённое",
  divergent: "Не совпадает",
  exact: "Точное",
  edited_after_cutoff_excluded: "Исключено: изменено после отсечения",
  evidence_at_unknown: "Время свидетельства неизвестно",
  explicit_user_fact: "Явный факт о пользователе",
  future: "Будущее время",
  future_or_invalid_time_excluded: "Исключено будущее или некорректное время",
  goal: "Цель",
  historical: "Историческое",
  historical_snapshot_unavailable: "Исторический снимок недоступен",
  invalid: "Некорректное",
  invalidated: "Признанное недействительным",
  later_evidence_excluded: "Исключено более позднее свидетельство",
  mixed: "Смешанное",
  mixed_no_winner: "Нет единственного варианта",
  memory: "Память",
  note: "Заметка",
  multiple_options_supported: "Поддерживается несколько вариантов",
  no_matching_evidence: "Подходящих свидетельств не найдено",
  not_assessed: "Не оценивалось",
  not_comparable: "Нельзя сопоставить",
  not_comparable_under_v1: "Нельзя сопоставить по текущей политике",
  observed_decision: "Наблюдаемое решение",
  outcome_presence_only: "Учитывается только наличие результата",
  outcome: "Результат",
  outcome_later_observation: "Позднее наблюдение результата",
  outside_horizon: "Вне временного диапазона",
  outside_horizon_excluded: "Исключено из-за временного диапазона",
  present: "Есть",
  prediction: "Прогноз",
  preference: "Предпочтение",
  project: "Проект",
  resource: "Ресурс",
  self: "Себя",
  stable: "Стабильное",
  stated_observed_mapping_missing: "Явная связь отсутствует",
  support_is_descriptive: "Поддержка имеет описательный характер",
  superseded: "Заменённое",
  temporal_state_is_cohort_local: "Временное состояние относится к этой группе",
  user_statement: "Высказывание пользователя",
  unknown: "Неизвестно",
  unknown_evidence_excluded: "Исключено свидетельство с неизвестным временем",
  insufficient_or_invalid_current_context: "Недостаточно или некорректен актуальный контекст",
  insufficient_comparable_evidence: "Недостаточно сопоставимых свидетельств",
  web: "Веб-источник",
  zettel: "Зеттель",
};

export function presentValue(value: unknown, fallback = "—"): string {
  if (typeof value === "string" && value.length > 0) return VALUE_LABELS[value] ?? value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "да" : "нет";
  return fallback;
}

export function presentCode(value: unknown, fallback = "—"): string {
  if (typeof value !== "string" || value.length === 0) return fallback;
  return VALUE_LABELS[value] ?? `Код: ${value}`;
}

export function presentError(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : "";
  return /[А-Яа-яЁё]/u.test(message) ? message : fallback;
}
