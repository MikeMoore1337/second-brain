import type { ReactElement } from "react";

export type PersonalMemoryTimeMode = "exact" | "unknown";

export type PersonalMemoryFieldValues = {
  readonly evidenceKind: string;
  readonly selfKind: string;
  readonly timeMode: PersonalMemoryTimeMode;
  readonly evidenceAt: string;
  readonly domain: string;
};

type PersonalMemoryMetadataFieldsProps = {
  readonly idPrefix: string;
  readonly values: PersonalMemoryFieldValues;
  readonly disabled?: boolean;
  readonly hidden?: boolean;
  readonly onEvidenceKindChange: (value: string) => void;
  readonly onSelfKindChange: (value: string) => void;
  readonly onTimeModeChange: (value: PersonalMemoryTimeMode) => void;
  readonly onEvidenceAtChange: (value: string) => void;
  readonly onDomainChange: (value: string) => void;
  readonly onNow: () => void;
};

export function PersonalMemoryMetadataFields({
  idPrefix,
  values,
  disabled = false,
  hidden = false,
  onEvidenceKindChange,
  onSelfKindChange,
  onTimeModeChange,
  onEvidenceAtChange,
  onDomainChange,
  onNow,
}: PersonalMemoryMetadataFieldsProps): ReactElement {
  const evidenceAtId = `${idPrefix}-evidence-at`;
  return (
    <div className="personal-memory-fields" id={`${idPrefix}-fields`} hidden={hidden}>
      <label className="review-field">
        <span className="draft-field-label">Основание</span>
        <select
          className="review-input"
          value={values.evidenceKind}
          disabled={disabled}
          onChange={(event) => onEvidenceKindChange(event.target.value)}
        >
          <option value="" disabled>Выбери тип основания</option>
          <option value="explicit_user_fact">Факт обо мне / моей ситуации</option>
          <option value="user_statement">Моё утверждение, мнение, цель или самоописание</option>
        </select>
      </label>
      <label className="review-field">
        <span className="draft-field-label">Что сохраняем о себе</span>
        <select
          className="review-input"
          value={values.selfKind}
          disabled={disabled}
          onChange={(event) => onSelfKindChange(event.target.value)}
        >
          <option value="" disabled>Выбери тип памяти</option>
          <option value="memory">Память</option>
          <option value="preference">Предпочтение</option>
          <option value="belief">Убеждение</option>
          <option value="goal">Цель</option>
        </select>
      </label>
      <label className="review-field">
        <span className="draft-field-label">Домен (необязательно)</span>
        <input
          className="review-input"
          type="text"
          value={values.domain}
          disabled={disabled}
          onChange={(event) => onDomainChange(event.target.value)}
        />
      </label>
      <label className="review-field">
        <span className="draft-field-label">Время факта</span>
        <select
          className="review-input"
          value={values.timeMode}
          disabled={disabled}
          onChange={(event) => onTimeModeChange(event.target.value as PersonalMemoryTimeMode)}
        >
          <option value="exact">Точное время</option>
          <option value="unknown">Время неизвестно</option>
        </select>
      </label>
      {values.timeMode === "exact" ? (
        <div className="personal-memory-time-field">
          <label className="draft-field-label" htmlFor={evidenceAtId}>Время факта в формате RFC3339</label>
          <input
            className="review-input"
            id={evidenceAtId}
            type="text"
            value={values.evidenceAt}
            disabled={disabled}
            onChange={(event) => onEvidenceAtChange(event.target.value)}
          />
          <button className="review-button review-button-secondary" type="button" disabled={disabled} onClick={onNow}>
            Сейчас
          </button>
        </div>
      ) : null}
    </div>
  );
}
