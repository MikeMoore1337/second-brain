"use strict";

// Structured Stage 2 Decision Journal UI. All state remains in page memory.

const decisionJournalSurface = document.querySelector("[data-decision-journal-surface]");

if (decisionJournalSurface) {
  const decisionForm = decisionJournalSurface.querySelector("[data-decision-form]");
  const outcomeForm = decisionJournalSurface.querySelector("[data-outcome-form]");
  const journalModeButtons = Array.from(
    decisionJournalSurface.querySelectorAll("[data-journal-mode]"),
  );
  const decisionTitle = decisionForm.querySelector("[data-decision-title]");
  const decisionNoteType = decisionForm.querySelector("[data-decision-note-type]");
  const decisionTags = decisionForm.querySelector("[data-decision-tags]");
  const decisionLinks = decisionForm.querySelector("[data-decision-links]");
  const decisionTimeMode = decisionForm.querySelector("[data-decision-time-mode]");
  const decisionEvidenceWrapper = decisionForm.querySelector(
    "[data-decision-evidence-wrapper]",
  );
  const decisionEvidenceAt = decisionForm.querySelector("[data-decision-evidence-at]");
  const decisionNow = decisionForm.querySelector("[data-decision-now]");
  const decisionDomain = decisionForm.querySelector("[data-decision-domain]");
  const decisionSituation = decisionForm.querySelector("[data-decision-situation]");
  const decisionOptions = decisionForm.querySelector("[data-decision-options]");
  const decisionInformation = decisionForm.querySelector("[data-decision-information]");
  const decisionCriteria = decisionForm.querySelector("[data-decision-criteria]");
  const decisionChosen = decisionForm.querySelector("[data-decision-chosen]");
  const decisionReasons = decisionForm.querySelector("[data-decision-reasons]");
  const decisionConfidence = decisionForm.querySelector("[data-decision-confidence]");
  const decisionExpected = decisionForm.querySelector("[data-decision-expected]");
  const decisionPrepare = decisionForm.querySelector("[data-decision-prepare]");
  const decisionConfirm = decisionForm.querySelector("[data-decision-confirm]");
  const decisionStatus = decisionForm.querySelector("[data-decision-status]");
  const decisionError = decisionForm.querySelector("[data-decision-error]");
  const decisionPlan = decisionForm.querySelector("[data-decision-plan]");
  const decisionSaved = decisionForm.querySelector("[data-decision-saved]");

  const outcomeTitle = outcomeForm.querySelector("[data-outcome-title]");
  const outcomeNoteType = outcomeForm.querySelector("[data-outcome-note-type]");
  const outcomeTags = outcomeForm.querySelector("[data-outcome-tags]");
  const outcomeLinks = outcomeForm.querySelector("[data-outcome-links]");
  const outcomeDecisionId = outcomeForm.querySelector("[data-outcome-decision-id]");
  const outcomeTimeMode = outcomeForm.querySelector("[data-outcome-time-mode]");
  const outcomeEvidenceWrapper = outcomeForm.querySelector(
    "[data-outcome-evidence-wrapper]",
  );
  const outcomeEvidenceAt = outcomeForm.querySelector("[data-outcome-evidence-at]");
  const outcomeNow = outcomeForm.querySelector("[data-outcome-now]");
  const outcomeDomain = outcomeForm.querySelector("[data-outcome-domain]");
  const outcomeActual = outcomeForm.querySelector("[data-outcome-actual]");
  const outcomeReassessment = outcomeForm.querySelector("[data-outcome-reassessment]");
  const outcomeNotes = outcomeForm.querySelector("[data-outcome-notes]");
  const outcomePrepare = outcomeForm.querySelector("[data-outcome-prepare]");
  const outcomeConfirm = outcomeForm.querySelector("[data-outcome-confirm]");
  const outcomeStatus = outcomeForm.querySelector("[data-outcome-status]");
  const outcomeError = outcomeForm.querySelector("[data-outcome-error]");
  const outcomePlan = outcomeForm.querySelector("[data-outcome-plan]");
  const outcomeSaved = outcomeForm.querySelector("[data-outcome-saved]");

  const journalHeaders = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Second-Brain-Request": "draft-v1",
  };
  const journalControls = [
    ...journalModeButtons,
    ...Array.from(decisionJournalSurface.querySelectorAll("input, textarea, select, button")),
  ];
  let journalMode = "decision";
  let journalBusy = false;
  let decisionConfirmationToken = null;
  let outcomeConfirmationToken = null;
  let decisionSavedState = false;
  let outcomeSavedState = false;
  let savedDecisionId = null;

  const lines = (value) => value.split(/\r?\n/).filter((item) => item.length > 0);

  const setJournalError = (target, statusTarget, message) => {
    target.textContent = typeof message === "string" && message ? message : "Не удалось выполнить операцию.";
    target.hidden = false;
    statusTarget.textContent = "";
    target.focus({ preventScroll: true });
  };

  const clearJournalError = (target) => {
    target.textContent = "";
    target.hidden = true;
  };

  const readJournalPayload = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const setJournalBusy = (loading) => {
    journalBusy = loading;
    decisionJournalSurface.setAttribute("aria-busy", String(loading));
    journalControls.forEach((control) => {
      const savedControl =
        (decisionSavedState && decisionForm.contains(control)) ||
        (outcomeSavedState && outcomeForm.contains(control));
      control.disabled = loading || savedControl;
    });
    journalModeButtons.forEach((button) => {
      button.disabled = loading;
    });
  };

  const setJournalMode = (nextMode) => {
    journalMode = nextMode === "outcome" ? "outcome" : "decision";
    decisionForm.classList.toggle("is-hidden", journalMode !== "decision");
    outcomeForm.classList.toggle("is-hidden", journalMode !== "outcome");
    journalModeButtons.forEach((button) => {
      const active = button.dataset.journalMode === journalMode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  };

  const syncDecisionTime = () => {
    const exact = decisionTimeMode.value === "exact";
    decisionEvidenceWrapper.hidden = !exact;
    decisionEvidenceAt.hidden = !exact;
    decisionEvidenceAt.required = exact;
    decisionNow.hidden = !exact;
    if (!exact) {
      decisionEvidenceAt.value = "unknown";
    } else if (decisionEvidenceAt.value === "unknown") {
      decisionEvidenceAt.value = "";
    }
  };

  const syncOutcomeTime = () => {
    const exact = outcomeTimeMode.value === "exact";
    outcomeEvidenceWrapper.hidden = !exact;
    outcomeEvidenceAt.hidden = !exact;
    outcomeEvidenceAt.required = exact;
    outcomeNow.hidden = !exact;
    if (!exact) {
      outcomeEvidenceAt.value = "unknown";
    } else if (outcomeEvidenceAt.value === "unknown") {
      outcomeEvidenceAt.value = "";
    }
  };

  const syncChosenOptions = () => {
    const previous = decisionChosen.value;
    const optionValues = lines(decisionOptions.value);
    decisionChosen.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = optionValues.length ? "Выбери вариант" : "Сначала добавь варианты";
    placeholder.disabled = true;
    placeholder.selected = true;
    decisionChosen.append(placeholder);
    optionValues.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      decisionChosen.append(option);
    });
    if (optionValues.includes(previous)) {
      decisionChosen.value = previous;
    }
  };

  const decisionPayload = () => {
    const exact = decisionTimeMode.value === "exact";
    return {
      title: decisionTitle.value,
      note_type: decisionNoteType.value,
      tags: lines(decisionTags.value),
      links: lines(decisionLinks.value),
      evidence_at: exact ? decisionEvidenceAt.value : "unknown",
      evidence_at_precision: exact ? "exact" : "unknown",
      domain: decisionDomain.value.trim() ? decisionDomain.value : null,
      situation: decisionSituation.value,
      available_options: lines(decisionOptions.value),
      information_known_at_decision_time: decisionInformation.value,
      criteria: lines(decisionCriteria.value),
      chosen_option: decisionChosen.value,
      reasons: decisionReasons.value,
      confidence: decisionConfidence.value,
      expected_result: decisionExpected.value,
    };
  };

  const outcomePayload = () => {
    const exact = outcomeTimeMode.value === "exact";
    return {
      title: outcomeTitle.value,
      note_type: outcomeNoteType.value,
      tags: lines(outcomeTags.value),
      links: lines(outcomeLinks.value),
      decision_id: outcomeDecisionId.value,
      evidence_at: exact ? outcomeEvidenceAt.value : "unknown",
      evidence_at_precision: exact ? "exact" : "unknown",
      domain: outcomeDomain.value.trim() ? outcomeDomain.value : null,
      actual_result: outcomeActual.value,
      reassessment: outcomeReassessment.value,
      notes: outcomeNotes.value,
    };
  };

  const clearPlan = (plan, confirm, tokenName, statusTarget, message) => {
    if (tokenName === "decision") {
      decisionConfirmationToken = null;
    } else {
      outcomeConfirmationToken = null;
    }
    confirm.hidden = true;
    confirm.disabled = true;
    plan.replaceChildren();
    plan.hidden = true;
    if (message) {
      statusTarget.textContent = message;
    }
  };

  const invalidateDecisionPlan = () => {
    if (!decisionSavedState) {
      clearPlan(
        decisionPlan,
        decisionConfirm,
        "decision",
        decisionStatus,
        "Изменения требуют новой подготовки Safe Write.",
      );
    }
  };

  const invalidateOutcomePlan = () => {
    if (!outcomeSavedState) {
      clearPlan(
        outcomePlan,
        outcomeConfirm,
        "outcome",
        outcomeStatus,
        "Изменения требуют новой подготовки Safe Write.",
      );
    }
  };

  const appendJournalField = (parent, label, value) => {
    const item = document.createElement("div");
    item.className = "draft-field";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = "draft-field-value";
    content.textContent = typeof value === "string" && value ? value : "—";
    item.append(name, content);
    parent.append(item);
  };

  const renderJournalPlan = (plan, payload, title) => {
    if (
      !payload ||
      payload.status !== "dry-run" ||
      typeof payload.confirmation_token !== "string" ||
      !payload.note ||
      typeof payload.note !== "object" ||
      typeof payload.diff !== "string"
    ) {
      throw new Error("invalid journal dry-run response");
    }
    plan.replaceChildren();
    const heading = document.createElement("h4");
    heading.textContent = title;
    const fields = document.createElement("dl");
    fields.className = "draft-fields";
    appendJournalField(fields, "Тип", payload.note.type);
    appendJournalField(fields, "Путь", payload.note.relative_path);
    const diffHeading = document.createElement("h5");
    diffHeading.textContent = "Предлагаемый Markdown-файл";
    const diff = document.createElement("pre");
    diff.className = "save-diff";
    diff.textContent = payload.diff;
    plan.append(heading, fields, diffHeading, diff);
    plan.hidden = false;
    plan.tabIndex = -1;
    plan.focus({ preventScroll: true });
  };

  const renderSavedJournalNote = (target, payload, label, addOutcome) => {
    if (
      !payload ||
      payload.status !== "created" ||
      !payload.note ||
      typeof payload.note !== "object" ||
      typeof payload.note.id !== "string"
    ) {
      throw new Error("invalid saved journal response");
    }
    target.replaceChildren();
    const heading = document.createElement("h4");
    heading.textContent = label;
    target.append(heading);
    const fields = document.createElement("dl");
    fields.className = "draft-fields";
    appendJournalField(fields, "Путь", payload.note.relative_path);
    appendJournalField(fields, "ID", payload.note.id);
    appendJournalField(fields, "Создано", payload.note.created);
    target.append(fields);
    if (addOutcome) {
      const button = document.createElement("button");
      button.className = "review-button review-button-secondary";
      button.type = "button";
      button.textContent = "Добавить outcome";
      button.addEventListener("click", () => {
        if (typeof savedDecisionId !== "string") {
          return;
        }
        outcomeDecisionId.value = savedDecisionId;
        invalidateOutcomePlan();
        setJournalMode("outcome");
        outcomeDecisionId.focus();
      });
      target.append(button);
    }
    target.hidden = false;
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
  };

  const responseError = (payload, fallback) =>
    payload && payload.error && typeof payload.error.message === "string"
      ? payload.error.message
      : fallback;

  journalModeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (!journalBusy) {
        setJournalMode(button.dataset.journalMode);
      }
    });
  });

  decisionOptions.addEventListener("input", () => {
    syncChosenOptions();
    invalidateDecisionPlan();
  });
  decisionTimeMode.addEventListener("change", () => {
    syncDecisionTime();
    invalidateDecisionPlan();
  });
  decisionNow.addEventListener("click", () => {
    decisionEvidenceAt.value = new Date().toISOString();
    invalidateDecisionPlan();
  });
  outcomeTimeMode.addEventListener("change", () => {
    syncOutcomeTime();
    invalidateOutcomePlan();
  });
  outcomeNow.addEventListener("click", () => {
    outcomeEvidenceAt.value = new Date().toISOString();
    invalidateOutcomePlan();
  });
  [
    decisionTitle,
    decisionNoteType,
    decisionTags,
    decisionLinks,
    decisionEvidenceAt,
    decisionDomain,
    decisionSituation,
    decisionInformation,
    decisionCriteria,
    decisionChosen,
    decisionReasons,
    decisionConfidence,
    decisionExpected,
  ].forEach((control) => {
    control.addEventListener("input", invalidateDecisionPlan);
    control.addEventListener("change", invalidateDecisionPlan);
  });
  [
    outcomeTitle,
    outcomeNoteType,
    outcomeTags,
    outcomeLinks,
    outcomeDecisionId,
    outcomeEvidenceAt,
    outcomeDomain,
    outcomeActual,
    outcomeReassessment,
    outcomeNotes,
  ].forEach((control) => {
    control.addEventListener("input", invalidateOutcomePlan);
    control.addEventListener("change", invalidateOutcomePlan);
  });

  decisionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (journalBusy || decisionSavedState || !decisionForm.reportValidity()) {
      return;
    }
    clearJournalError(decisionError);
    clearPlan(decisionPlan, decisionConfirm, "decision", decisionStatus, "");
    setJournalBusy(true);
    decisionStatus.textContent = "Готовлю Decision Journal dry-run…";
    try {
      const response = await fetch("/api/drafts/decision-journal/save/prepare", {
        method: "POST",
        headers: journalHeaders,
        body: JSON.stringify({ decision: decisionPayload() }),
      });
      const payload = await readJournalPayload(response);
      if (!response.ok) {
        setJournalError(
          decisionError,
          decisionStatus,
          responseError(payload, "Не удалось подготовить Decision Journal."),
        );
        return;
      }
      renderJournalPlan(
        decisionPlan,
        payload,
        "План Decision Journal Safe Write (dry-run)",
      );
      decisionConfirmationToken = payload.confirmation_token;
      decisionConfirm.hidden = false;
      decisionConfirm.disabled = false;
      decisionStatus.textContent = "Проверь полный diff и подтверди сохранение.";
    } catch (_error) {
      setJournalError(decisionError, decisionStatus, "Сервис Decision Journal недоступен.");
    } finally {
      setJournalBusy(false);
    }
  });

  decisionConfirm.addEventListener("click", async () => {
    if (journalBusy || decisionSavedState || typeof decisionConfirmationToken !== "string") {
      return;
    }
    clearJournalError(decisionError);
    setJournalBusy(true);
    decisionStatus.textContent = "Сохраняю Decision Journal в vault…";
    try {
      const response = await fetch("/api/drafts/decision-journal/save/apply", {
        method: "POST",
        headers: journalHeaders,
        body: JSON.stringify({
          confirmation_token: decisionConfirmationToken,
          decision: decisionPayload(),
        }),
      });
      const payload = await readJournalPayload(response);
      if (!response.ok) {
        setJournalError(
          decisionError,
          decisionStatus,
          responseError(payload, "Не удалось сохранить Decision Journal."),
        );
        return;
      }
      savedDecisionId = payload && payload.note && payload.note.id;
      decisionConfirmationToken = null;
      decisionSavedState = true;
      decisionConfirm.hidden = true;
      decisionConfirm.disabled = true;
      renderSavedJournalNote(decisionSaved, payload, "Decision Journal сохранён", true);
      decisionStatus.textContent = "Decision Journal сохранён. Можно добавить outcome.";
      decisionForm.querySelectorAll("input, textarea, select, [data-decision-prepare]").forEach((control) => {
        control.disabled = true;
      });
    } catch (_error) {
      setJournalError(decisionError, decisionStatus, "Сервис сохранения недоступен.");
    } finally {
      setJournalBusy(false);
    }
  });

  outcomeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (journalBusy || outcomeSavedState || !outcomeForm.reportValidity()) {
      return;
    }
    clearJournalError(outcomeError);
    clearPlan(outcomePlan, outcomeConfirm, "outcome", outcomeStatus, "");
    setJournalBusy(true);
    outcomeStatus.textContent = "Готовлю Outcome dry-run…";
    try {
      const response = await fetch("/api/drafts/outcome-observation/save/prepare", {
        method: "POST",
        headers: journalHeaders,
        body: JSON.stringify({ outcome: outcomePayload() }),
      });
      const payload = await readJournalPayload(response);
      if (!response.ok) {
        setJournalError(
          outcomeError,
          outcomeStatus,
          responseError(payload, "Не удалось подготовить Outcome."),
        );
        return;
      }
      renderJournalPlan(
        outcomePlan,
        payload,
        "План Outcome Observation Safe Write (dry-run)",
      );
      outcomeConfirmationToken = payload.confirmation_token;
      outcomeConfirm.hidden = false;
      outcomeConfirm.disabled = false;
      outcomeStatus.textContent = "Проверь полный diff и подтверди сохранение.";
    } catch (_error) {
      setJournalError(outcomeError, outcomeStatus, "Сервис Outcome недоступен.");
    } finally {
      setJournalBusy(false);
    }
  });

  outcomeConfirm.addEventListener("click", async () => {
    if (journalBusy || outcomeSavedState || typeof outcomeConfirmationToken !== "string") {
      return;
    }
    clearJournalError(outcomeError);
    setJournalBusy(true);
    outcomeStatus.textContent = "Сохраняю Outcome в vault…";
    try {
      const response = await fetch("/api/drafts/outcome-observation/save/apply", {
        method: "POST",
        headers: journalHeaders,
        body: JSON.stringify({
          confirmation_token: outcomeConfirmationToken,
          outcome: outcomePayload(),
        }),
      });
      const payload = await readJournalPayload(response);
      if (!response.ok) {
        setJournalError(
          outcomeError,
          outcomeStatus,
          responseError(payload, "Не удалось сохранить Outcome."),
        );
        return;
      }
      outcomeConfirmationToken = null;
      outcomeSavedState = true;
      outcomeConfirm.hidden = true;
      outcomeConfirm.disabled = true;
      renderSavedJournalNote(outcomeSaved, payload, "Outcome Observation сохранён", false);
      outcomeStatus.textContent = "Outcome сохранён отдельной связанной заметкой.";
      outcomeForm.querySelectorAll("input, textarea, select, [data-outcome-prepare]").forEach((control) => {
        control.disabled = true;
      });
    } catch (_error) {
      setJournalError(outcomeError, outcomeStatus, "Сервис сохранения недоступен.");
    } finally {
      setJournalBusy(false);
    }
  });

  syncDecisionTime();
  syncOutcomeTime();
  syncChosenOptions();
  setJournalMode("decision");
}
