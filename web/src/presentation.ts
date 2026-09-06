const VALUE_LABELS: Readonly<Record<string, string>> = {
  area: "Область",
  abstention: "Отказ от прогноза",
  belief: "Убеждение",
  candidate_not_found: "Кандидат не найден",
  context_budget_exceeded: "Превышен лимит контекста",
  decision: "Решение",
  decision_rule: "Правило решения",
  exact: "Точное",
  evidence_at_unknown: "Время свидетельства неизвестно",
  explicit_user_fact: "Явный факт о пользователе",
  goal: "Цель",
  memory: "Память",
  note: "Заметка",
  multiple_options_supported: "Поддерживается несколько вариантов",
  no_matching_evidence: "Подходящих свидетельств не найдено",
  not_assessed: "Не оценивалось",
  observed_decision: "Наблюдаемое решение",
  outcome: "Результат",
  outcome_later_observation: "Позднее наблюдение результата",
  prediction: "Прогноз",
  preference: "Предпочтение",
  project: "Проект",
  resource: "Ресурс",
  self: "Себя",
  user_statement: "Высказывание пользователя",
  unknown: "Неизвестно",
  insufficient_or_invalid_current_context: "Недостаточно или некорректен актуальный контекст",
  web: "Веб-источник",
  zettel: "Зеттель",
};

export function presentValue(value: unknown, fallback = "—"): string {
  if (typeof value === "string" && value.length > 0) return VALUE_LABELS[value] ?? value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "да" : "нет";
  return fallback;
}
