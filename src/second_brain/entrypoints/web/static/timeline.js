"use strict";

const timelineSurface = document.querySelector("[data-timeline-surface]");

if (timelineSurface) {
  const orderSelect = timelineSurface.querySelector("[data-timeline-order]");
  const refreshButton = timelineSurface.querySelector("[data-timeline-refresh]");
  const status = timelineSurface.querySelector("[data-timeline-status]");
  const error = timelineSurface.querySelector("[data-timeline-error]");
  const knownTotal = timelineSurface.querySelector("[data-timeline-known-total]");
  const unknownTotal = timelineSurface.querySelector("[data-timeline-unknown-total]");
  const knownList = timelineSurface.querySelector("[data-timeline-known-list]");
  const unknownList = timelineSurface.querySelector("[data-timeline-unknown-list]");
  const knownEmpty = timelineSurface.querySelector("[data-timeline-known-empty]");
  const unknownEmpty = timelineSurface.querySelector("[data-timeline-unknown-empty]");
  const timelineHeaders = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Second-Brain-Request": "timeline-v1",
  };
  let busy = false;

  const textValue = (value, fallback = "—") =>
    typeof value === "string" && value.length > 0 ? value : fallback;

  const setError = (message) => {
    error.textContent = textValue(message, "Не удалось загрузить Timeline.");
    error.hidden = false;
    status.textContent = "";
  };

  const focusError = () => {
    error.focus({ preventScroll: true });
  };

  const clearFeedback = () => {
    error.textContent = "";
    error.hidden = true;
    status.textContent = "";
  };

  const setBusy = (value) => {
    busy = value;
    timelineSurface.setAttribute("aria-busy", String(value));
    orderSelect.disabled = value;
    refreshButton.disabled = value;
    refreshButton.setAttribute("aria-busy", String(value));
    if (value) {
      status.textContent = "Обновляю текущую Timeline…";
    }
  };

  const readPayload = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const appendField = (parent, label, value) => {
    const item = document.createElement("div");
    item.className = "timeline-field";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = "timeline-field-value";
    content.textContent = textValue(value);
    item.append(name, content);
    parent.append(item);
  };

  const appendRelatedNotes = (parent, relatedNoteIds) => {
    if (!Array.isArray(relatedNoteIds) || relatedNoteIds.length === 0) {
      return;
    }
    appendField(parent, "Decision", relatedNoteIds.join(", "));
  };

  const renderItem = (item, known) => {
    if (!item || typeof item !== "object") {
      throw new Error("invalid timeline item");
    }
    const article = document.createElement("article");
    article.className = known ? "timeline-item" : "timeline-item timeline-item-unknown";

    const heading = document.createElement("h4");
    heading.className = "timeline-item-title";
    heading.textContent = known ? "Событие" : "Время неизвестно";
    article.append(heading);

    const fields = document.createElement("dl");
    fields.className = "timeline-fields";
    if (known) {
      appendField(fields, "Время события", item.event_at);
    }
    appendField(fields, "Событие", item.event_kind);
    appendField(fields, "Evidence", item.evidence_kind);
    appendField(fields, "Summary", item.summary);
    appendField(fields, "Домен", item.domain);
    appendField(fields, "Путь", item.relative_path);
    appendField(fields, "UUID", item.id);
    appendField(fields, "Сохранено", item.storage_created_at);
    if (item.storage_updated_at) {
      appendField(fields, "Обновлено в хранилище", item.storage_updated_at);
    }
    appendRelatedNotes(fields, item.related_note_ids);
    article.append(fields);
    return article;
  };

  const renderTotal = (target, shown, total) => {
    const truncated = shown < total;
    target.textContent = truncated
      ? `Показано: ${shown} / ${total} — история ограничена лимитом`
      : `Показано: ${shown} / ${total}`;
  };

  const renderResponse = (payload) => {
    if (
      !payload ||
      typeof payload !== "object" ||
      !Array.isArray(payload.known_items) ||
      !Array.isArray(payload.unknown_items) ||
      !Number.isInteger(payload.known_total) ||
      !Number.isInteger(payload.unknown_total)
    ) {
      throw new Error("invalid timeline response");
    }

    knownList.replaceChildren();
    unknownList.replaceChildren();
    payload.known_items.forEach((item) => knownList.append(renderItem(item, true)));
    payload.unknown_items.forEach((item) => unknownList.append(renderItem(item, false)));
    knownEmpty.hidden = payload.known_items.length !== 0;
    unknownEmpty.hidden = payload.unknown_items.length !== 0;
    renderTotal(knownTotal, payload.known_items.length, payload.known_total);
    renderTotal(unknownTotal, payload.unknown_items.length, payload.unknown_total);
  };

  const loadTimeline = async (userInitiated = false) => {
    if (busy) {
      return;
    }
    clearFeedback();
    setBusy(true);
    try {
      const response = await fetch("/api/timeline", {
        method: "POST",
        headers: timelineHeaders,
        body: JSON.stringify({
          order: orderSelect.value,
          known_limit: 100,
          unknown_limit: 100,
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setError(typeof message === "string" ? message : "Не удалось загрузить Timeline.");
        if (userInitiated) {
          focusError();
        }
        return;
      }
      renderResponse(payload);
      status.textContent = "Показана текущая Timeline из vault.";
    } catch (_error) {
      setError("Сервис Timeline недоступен.");
      if (userInitiated) {
        focusError();
      }
    } finally {
      setBusy(false);
    }
  };

  refreshButton.addEventListener("click", () => loadTimeline(true));
  orderSelect.addEventListener("change", () => loadTimeline(true));
  loadTimeline();
}
