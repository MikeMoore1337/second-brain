import { useEffect, useState, type ReactElement, type ReactNode } from "react";

import { SEMANTIC_GROUP_REVEAL_EVENT, SEMANTIC_TOOL_REVEAL_EVENT } from "./fold-section";
import { Icon, type IconName } from "./icons";

export type SemanticGroupId = "memory" | "self-understanding" | "decisions" | "growth";

type SemanticGroupDefinition = {
  readonly id: SemanticGroupId;
  readonly label: string;
  readonly description: string;
  readonly icon: IconName;
  readonly tools: readonly {
    readonly id: string;
    readonly label: string;
    readonly description: string;
    readonly target: `#${string}`;
    readonly icon: IconName;
    readonly availability: "available";
    readonly renderSurface: boolean;
  }[];
};

/** Single source of truth for the product's semantic navigation layer. */
export const semanticGroups = [
  {
    id: "memory",
    label: "Память",
    description: "Сохраняй события, решения, идеи и важный контекст, чтобы ничего не терялось.",
    icon: "memory",
    tools: [
      { id: "capture", label: "Добавить знание", description: "Преврати текст, голос или публичную страницу в проверяемый черновик.", target: "#capture", icon: "add", availability: "available", renderSurface: true },
      { id: "search", label: "Найти в памяти", description: "Найди заметку и открой её текущую версию только для чтения.", target: "#search", icon: "search", availability: "available", renderSurface: true },
      { id: "decision-journal", label: "Журнал решений", description: "Зафиксируй выбор сейчас и результат позже отдельными записями.", target: "#decision-journal", icon: "decision", availability: "available", renderSurface: true },
      { id: "timeline", label: "Личная хронология", description: "Посмотри события по каноническим свидетельствам.", target: "#timeline", icon: "timeline", availability: "available", renderSurface: true },
      { id: "self-retrieval", label: "Собрать контекст", description: "Верни текущие заметки и точные связи для следующего шага.", target: "#self-retrieval", icon: "self-retrieval", availability: "available", renderSurface: true },
    ],
  },
  {
    id: "self-understanding",
    label: "Понимание себя",
    description: "Увидь, что ты говоришь о себе, что реально делаешь и какие закономерности повторяются.",
    icon: "self-model",
    tools: [
      { id: "self-model", label: "Модель себя", description: "Увидь утверждения о предпочтениях, убеждениях и целях с источниками.", target: "#self-model", icon: "self-model", availability: "available", renderSurface: true },
      { id: "cognitive-twin", label: "Явное и наблюдаемое", description: "Сравни явное и наблюдаемое только после явного подтверждения.", target: "#cognitive-twin", icon: "copy", availability: "available", renderSurface: true },
      { id: "retrospective-calibration", label: "Ретроспективная проверка", description: "Проверь, как воспроизводятся прошлые решения и где есть оговорки.", target: "#retrospective-calibration", icon: "retrospective", availability: "available", renderSurface: true },
      { id: "diagnostics", label: "Диагностика", description: "Проверь доступность локальных слоёв без содержимого заметок.", target: "#diagnostics", icon: "diagnostics", availability: "available", renderSurface: true },
    ],
  },
  {
    id: "decisions",
    label: "Решения",
    description: "Сравни варианты, спрогнозируй свой выбор и посмотри на решение с разных сторон.",
    icon: "decision",
    tools: [
      { id: "simulate-me", label: "Прогноз", description: "Сравни варианты с текущими свидетельствами; результат может быть отказом.", target: "#simulate-me", icon: "simulate", availability: "available", renderSurface: true },
      { id: "prospective-audit", label: "Аудит прогноза", description: "Сохрани прогноз до решения и свяжи его с журналом явно.", target: "#prospective-audit", icon: "prospective-audit", availability: "available", renderSurface: true },
      { id: "assistant-compare", label: "Совет и сравнение", description: "Поставь независимый совет рядом с прогнозом твоего выбора.", target: "#assistant-compare", icon: "relation", availability: "available", renderSurface: true },
      { id: "decision-compass", label: "Компас решения", description: "Собери цель, варианты и контекст в проверяемый срез.", target: "#decision-compass", icon: "decision-compass", availability: "available", renderSurface: true },
      { id: "active-learning", label: "Уточнить модель выбора", description: "Ответь на один вопрос и реши сам, станет ли ответ частью памяти.", target: "#active-learning", icon: "info", availability: "available", renderSurface: false },
    ],
  },
  {
    id: "growth",
    label: "Развитие",
    description: "Задавай цели, измеряй прогресс, проверяй гипотезы и используй накопленный опыт.",
    icon: "growth",
    tools: [
      { id: "growth-engine", label: "Цели и прогресс", description: "Проверь цель, измеряемый прогресс, совет и следующий шаг.", target: "#growth-engine", icon: "growth", availability: "available", renderSurface: true },
      { id: "personal-experiments", label: "Личные эксперименты", description: "Проверь свою гипотезу и оцени изменения по правилу измерения прогресса цели.", target: "#personal-experiments", icon: "personal-experiments", availability: "available", renderSurface: true },
      { id: "adaptive-cognitive-twin", label: "Адаптивный профиль", description: "Собери проверяемое предложение для нового способа представления контекста.", target: "#adaptive-cognitive-twin", icon: "refresh", availability: "available", renderSurface: true },
    ],
  },
] as const satisfies readonly SemanticGroupDefinition[];

export type SemanticGroup = (typeof semanticGroups)[number];
export type SemanticTool = SemanticGroup["tools"][number];

type ActiveSurface = {
  readonly groupId: SemanticGroupId;
  readonly surfaceId: string;
  readonly targetId: string;
};

function toolCount(count: number): string {
  if (count === 1) return "1 инструмент";
  if (count < 5) return `${count} инструмента`;
  return `${count} инструментов`;
}

function surfaceIdForTool(tool: SemanticTool): string | null {
  if (tool.id === "active-learning") return "simulate-me";
  return tool.renderSurface ? tool.id : null;
}

function findTool(targetId: string): { readonly group: SemanticGroup; readonly tool: SemanticTool; readonly surfaceId: string } | null {
  for (const group of semanticGroups) {
    for (const tool of group.tools) {
      if (tool.target.slice(1) !== targetId) continue;
      const surfaceId = surfaceIdForTool(tool);
      if (surfaceId) return { group, tool, surfaceId };
    }
  }
  return null;
}

function focusSurface(targetId: string): void {
  const destination = document.getElementById(targetId);
  if (!destination) return;
  destination.tabIndex = -1;
  destination.focus({ preventScroll: true });
  destination.scrollIntoView?.({ block: "start" });
}

function GroupSummary({ group, open, panelId, onToggle }: { readonly group: SemanticGroup; readonly open: boolean; readonly panelId: string; readonly onToggle: () => void }): ReactElement {
  const titleId = `${group.id}-direction-title`;
  const descriptionId = `${group.id}-direction-description`;
  return (
    <div className="semantic-group-summary">
      <Icon name={group.icon} size={56} className="semantic-group-icon" />
      <div className="semantic-group-copy">
        <h3 id={titleId}>{group.label}</h3>
        <p id={descriptionId}>{group.description}</p>
      </div>
      <span className="semantic-group-count">{toolCount(group.tools.length)}</span>
      <Icon name={open ? "collapse" : "expand"} size={20} className="semantic-group-chevron" />
      <button
        type="button"
        className="semantic-group-trigger"
        data-semantic-group-trigger
        aria-expanded={open}
        aria-controls={panelId}
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onClick={onToggle}
      >
        <span className="sr-only">{open ? "Свернуть" : "Раскрыть"} направление «{group.label}»</span>
      </button>
    </div>
  );
}

export function SemanticNavigation({ renderTool }: { readonly renderTool: (tool: SemanticTool) => ReactNode }): ReactElement {
  const [openGroupId, setOpenGroupId] = useState<SemanticGroupId | null>(null);
  const [activeSurface, setActiveSurface] = useState<ActiveSurface | null>(null);

  const selectTool = (group: SemanticGroup, tool: SemanticTool): void => {
    const surfaceId = surfaceIdForTool(tool);
    if (!surfaceId) return;
    setOpenGroupId(group.id);
    setActiveSurface({ groupId: group.id, surfaceId, targetId: tool.target.slice(1) });
  };

  const selectTarget = (targetId: string): void => {
    const match = findTool(targetId);
    if (!match) return;
    setOpenGroupId(match.group.id);
    setActiveSurface({ groupId: match.group.id, surfaceId: match.surfaceId, targetId });
  };

  useEffect(() => {
    const onGroupReveal = (event: Event) => {
      const groupId = (event as CustomEvent<{ readonly groupId?: string }>).detail?.groupId;
      if (groupId === "memory" || groupId === "self-understanding" || groupId === "decisions" || groupId === "growth") {
        setOpenGroupId(groupId);
        setActiveSurface((current) => current?.groupId === groupId ? current : null);
      }
    };
    const onToolReveal = (event: Event) => {
      const targetId = (event as CustomEvent<{ readonly targetId?: string }>).detail?.targetId;
      if (targetId) selectTarget(targetId);
    };
    document.addEventListener(SEMANTIC_GROUP_REVEAL_EVENT, onGroupReveal);
    document.addEventListener(SEMANTIC_TOOL_REVEAL_EVENT, onToolReveal);
    return () => {
      document.removeEventListener(SEMANTIC_GROUP_REVEAL_EVENT, onGroupReveal);
      document.removeEventListener(SEMANTIC_TOOL_REVEAL_EVENT, onToolReveal);
    };
  }, []);

  useEffect(() => {
    if (!activeSurface) return;
    focusSurface(activeSurface.targetId);
  }, [activeSurface]);

  return (
    <section className="semantic-navigation" id="semantic-navigation" aria-labelledby="semantic-navigation-title">
      <div className="semantic-navigation-heading">
        <div>
          <h2 id="semantic-navigation-title">Четыре направления</h2>
          <p>От сохранённого опыта к ясному выбору и следующему шагу — в одном спокойном цикле.</p>
        </div>
        <p className="semantic-cycle" aria-label="Запоминаю, понимаю себя, принимаю решения, развиваюсь и возвращаюсь к важному.">
          <span>Запоминаю</span><span className="semantic-cycle-arrow" aria-hidden="true">→</span>
          <span>Понимаю себя</span><span className="semantic-cycle-arrow" aria-hidden="true">→</span>
          <span>Принимаю решения</span><span className="semantic-cycle-arrow" aria-hidden="true">→</span>
          <span>Развиваюсь</span><span className="semantic-cycle-return" aria-hidden="true">↺</span>
        </p>
      </div>
      <div className="semantic-group-list">
        {semanticGroups.map((group) => {
          const open = openGroupId === group.id;
          const panelId = `${group.id}-direction-panel`;
          return (
            <section
              className={`semantic-group semantic-group-${group.id}${open ? " is-open" : ""}`}
              id={`semantic-group-${group.id}`}
              data-semantic-group={group.id}
              aria-labelledby={`${group.id}-direction-title`}
              key={group.id}
            >
              <GroupSummary
                group={group}
                open={open}
                panelId={panelId}
                onToggle={() => {
                  const nextGroupId = open ? null : group.id;
                  setOpenGroupId(nextGroupId);
                  if (nextGroupId !== activeSurface?.groupId) setActiveSurface(null);
                }}
              />
              <div
                className="semantic-group-panel"
                id={panelId}
                data-semantic-group-panel
                role="region"
                aria-labelledby={group.id + "-direction-title"}
                aria-hidden={!open}
                inert={!open}
              >
                <div className="semantic-group-panel-inner">
                  <nav className="semantic-tool-links" aria-label={`Инструменты направления «${group.label}»`}>
                    {group.tools.map((tool) => (
                      <a className="semantic-tool-link" data-semantic-tool-link={tool.id} href={tool.target} key={tool.id} onClick={() => selectTool(group, tool)}>
                        <Icon name={tool.icon} size={36} />
                        <span className="semantic-tool-copy"><strong>{tool.label}</strong><span>{tool.description}</span></span>
                        <span className="semantic-tool-open">Открыть <Icon name="open" size={16} /></span>
                      </a>
                      ))}
                  </nav>
                  <div className="semantic-surface-list">
                    {group.tools.filter((tool) => tool.renderSurface).map((tool) => {
                      const surfaceId = surfaceIdForTool(tool);
                      const active = activeSurface?.groupId === group.id && activeSurface.surfaceId === surfaceId;
                      return (
                        <div
                          className="semantic-functional-surface"
                          data-semantic-functional-surface={tool.id}
                          hidden={!active}
                          aria-hidden={!active}
                          key={tool.id}
                        >
                          <div className="semantic-functional-heading">
                            <Icon name={tool.icon} size={32} aria-hidden="true" />
                            <h3>{tool.label}</h3>
                          </div>
                          {renderTool(tool)}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}
