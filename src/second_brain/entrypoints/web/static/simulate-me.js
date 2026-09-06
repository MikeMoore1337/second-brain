"use strict";

const simulateMeSurface = document.querySelector("[data-simulate-me-surface]");

if (simulateMeSurface) {
  const form = simulateMeSurface.querySelector("[data-simulate-me-form]");
  const query = simulateMeSurface.querySelector("[data-simulate-me-query]");
  const options = simulateMeSurface.querySelector("[data-simulate-me-options]");
  const addOption = simulateMeSurface.querySelector("[data-simulate-me-add]");
  const submit = simulateMeSurface.querySelector("[data-simulate-me-submit]");
  const status = simulateMeSurface.querySelector("[data-simulate-me-status]");
  const error = simulateMeSurface.querySelector("[data-simulate-me-error]");
  const result = simulateMeSurface.querySelector("[data-simulate-me-result]");
  const resultContent = simulateMeSurface.querySelector("[data-simulate-me-result-content]");
  const headers = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Second-Brain-Request": "simulate-me-v1",
  };
  const MAX_OPTIONS = 8;
  let busy = false;

  const displayValue = (value, fallback = "—") => {
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
    return fallback;
  };

  const setError = (message) => {
    error.textContent = displayValue(message, "Не удалось получить прогноз.");
    error.hidden = false;
    status.textContent = "";
    error.scrollIntoView({ block: "nearest" });
    error.focus();
  };

  const clearFeedback = () => {
    error.textContent = "";
    error.hidden = true;
    status.textContent = "";
  };

  const setBusy = (value) => {
    busy = value;
    simulateMeSurface.setAttribute("aria-busy", String(value));
    submit.disabled = value;
    addOption.disabled = value || options.children.length >= MAX_OPTIONS;
    submit.setAttribute("aria-busy", String(value));
    if (value) {
      status.textContent = "Строю текущий прогноз…";
    }
  };

  const readPayload = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const optionRows = () => Array.from(options.querySelectorAll("[data-simulate-me-option]"));

  const updateOptionControls = () => {
    const rows = optionRows();
    rows.forEach((row) => {
      const remove = row.querySelector("[data-simulate-me-remove]");
      remove.hidden = rows.length === 1 || busy;
      remove.disabled = busy;
    });
    addOption.disabled = busy || rows.length >= MAX_OPTIONS;
  };

  const createOptionRow = () => {
    const row = document.createElement("div");
    row.className = "simulate-me-option-row";
    row.setAttribute("data-simulate-me-option", "");

    const idLabel = document.createElement("label");
    idLabel.className = "simulate-me-field";
    const idName = document.createElement("span");
    idName.className = "draft-field-label";
    idName.textContent = "Option id";
    const idInput = document.createElement("input");
    idInput.className = "review-input";
    idInput.type = "text";
    idInput.autocomplete = "off";
    idInput.setAttribute("data-simulate-me-option-id", "");
    idLabel.append(idName, idInput);

    const label = document.createElement("label");
    label.className = "simulate-me-field";
    const labelName = document.createElement("span");
    labelName.className = "draft-field-label";
    labelName.textContent = "Label";
    const labelInput = document.createElement("input");
    labelInput.className = "review-input";
    labelInput.type = "text";
    labelInput.autocomplete = "off";
    labelInput.required = true;
    labelInput.setAttribute("data-simulate-me-option-label", "");
    label.append(labelName, labelInput);

    const remove = document.createElement("button");
    remove.className = "review-button review-button-quiet";
    remove.type = "button";
    remove.textContent = "Удалить";
    remove.setAttribute("data-simulate-me-remove", "");
    remove.addEventListener("click", () => {
      if (busy || optionRows().length === 1) {
        return;
      }
      row.remove();
      updateOptionControls();
    });

    row.append(idLabel, label, remove);
    return row;
  };

  const collectOptions = () =>
    optionRows().map((row) => ({
      id: row.querySelector("[data-simulate-me-option-id]").value,
      label: row.querySelector("[data-simulate-me-option-label]").value,
    }));

  const appendField = (parent, label, value) => {
    const item = document.createElement("div");
    item.className = "simulate-me-field-value";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = "simulate-me-value";
    content.textContent = displayValue(value);
    item.append(name, content);
    parent.append(item);
  };

  const renderEvidence = (ref) => {
    if (!ref || typeof ref !== "object" || !Array.isArray(ref.note_ids)) {
      throw new Error("invalid simulate me evidence");
    }
    const item = document.createElement("li");
    item.className = "simulate-me-evidence-item";
    const fields = document.createElement("dl");
    fields.className = "simulate-me-fields";
    appendField(fields, "Claim UUID", ref.claim_id);
    appendField(fields, "Dimension", ref.dimension);
    appendField(fields, "Canonical note UUIDs", ref.note_ids.join(", "));
    appendField(fields, "Evidence time", ref.evidence_at);
    item.append(fields);
    return item;
  };

  const renderReferenceGroup = (parent, title, refs) => {
    if (!Array.isArray(refs) || refs.length === 0) {
      return;
    }
    const heading = document.createElement("h4");
    heading.textContent = title;
    const list = document.createElement("ul");
    list.className = "simulate-me-evidence-list";
    refs.forEach((ref) => list.append(renderEvidence(ref)));
    parent.append(heading, list);
  };

  const renderResponse = (payload) => {
    if (
      !payload ||
      typeof payload !== "object" ||
      !Array.isArray(payload.evidence_refs) ||
      !Array.isArray(payload.contextual_evidence_refs) ||
      !Array.isArray(payload.temporal_caveats) ||
      (payload.kind !== "prediction" && payload.kind !== "abstention")
    ) {
      throw new Error("invalid simulate me response");
    }

    resultContent.replaceChildren();
    const heading = document.createElement("h4");
    heading.className = "simulate-me-result-heading";
    heading.textContent = payload.kind === "prediction" ? "ПРОГНОЗ" : "Прогноз не построен";
    resultContent.append(heading);

    const fields = document.createElement("dl");
    fields.className = "simulate-me-fields";
    if (payload.kind === "prediction") {
      if (!payload.selected_option || typeof payload.selected_option !== "object") {
        throw new Error("invalid simulate me selected option");
      }
      appendField(fields, "Предсказанный вариант", payload.selected_option.label);
      appendField(fields, "Option id", payload.selected_option.id);
    } else {
      appendField(fields, "Состояние", "Недостаточно evidence");
      appendField(fields, "Abstention", payload.abstention_code);
    }
    appendField(fields, "Derivation", payload.derivation_version);
    appendField(fields, "Policy", payload.policy_id);
    appendField(fields, "Policy fingerprint", payload.policy_fingerprint);
    resultContent.append(fields);

    const evidence = document.createElement("div");
    evidence.className = "simulate-me-evidence";
    renderReferenceGroup(evidence, "Supporting evidence", payload.evidence_refs);
    renderReferenceGroup(evidence, "Contextual belief evidence", payload.contextual_evidence_refs);
    if (payload.temporal_caveats.length > 0) {
      const caveatHeading = document.createElement("h4");
      caveatHeading.textContent = "Temporal caveats";
      const caveats = document.createElement("ul");
      caveats.className = "simulate-me-caveats";
      payload.temporal_caveats.forEach((caveat) => {
        if (!caveat || typeof caveat !== "object") {
          throw new Error("invalid simulate me caveat");
        }
        const item = document.createElement("li");
        item.textContent = `${displayValue(caveat.code)} · claim ${displayValue(caveat.claim_id)}`;
        caveats.append(item);
      });
      evidence.append(caveatHeading, caveats);
    }
    resultContent.append(evidence);
    result.hidden = false;
  };

  addOption.addEventListener("click", () => {
    if (busy || optionRows().length >= MAX_OPTIONS) {
      return;
    }
    options.append(createOptionRow());
    updateOptionControls();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    clearFeedback();
    result.hidden = true;
    resultContent.replaceChildren();
    setBusy(true);
    try {
      const response = await fetch("/api/simulate-me", {
        method: "POST",
        headers,
        body: JSON.stringify({ query: query.value, options: collectOptions() }),
      });
      const payload = await readPayload(response);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setError(typeof message === "string" ? message : "Не удалось получить прогноз.");
        return;
      }
      renderResponse(payload);
      status.textContent = "Показан текущий результат core.";
    } catch (_error) {
      setError("Сервис Simulate Me недоступен.");
    } finally {
      setBusy(false);
      updateOptionControls();
    }
  });

  updateOptionControls();
}
