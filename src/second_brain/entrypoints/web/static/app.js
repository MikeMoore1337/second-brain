"use strict";

// Draft values stay in the current DOM only; the browser has no storage or external network path.
document.documentElement.classList.add("js");

const panel = document.querySelector("[data-capture-panel]");

if (panel) {
  const form = panel.querySelector("[data-draft-form]");
  const urlInput = panel.querySelector("[data-url-input]");
  const textInput = panel.querySelector("[data-text-input]");
  const urlLabel = panel.querySelector("[data-url-label]");
  const textLabel = panel.querySelector("[data-text-label]");
  const submit = panel.querySelector("[data-submit]");
  const status = panel.querySelector("[data-status]");
  const error = panel.querySelector("[data-error]");
  const result = panel.querySelector("[data-result]");
  const modeButtons = Array.from(panel.querySelectorAll("[data-mode]"));
  let mode = "url";

  const textValue = (value, fallback = "—") =>
    typeof value === "string" && value.length > 0 ? value : fallback;

  const setError = (message) => {
    error.textContent = textValue(message, "Не удалось создать черновик.");
    error.hidden = false;
    status.textContent = "";
  };

  const clearFeedback = () => {
    error.textContent = "";
    error.hidden = true;
    status.textContent = "";
    result.replaceChildren();
    result.hidden = true;
  };

  const setLoading = (loading) => {
    submit.disabled = loading;
    urlInput.disabled = loading || mode !== "url";
    textInput.disabled = loading || mode !== "text";
    modeButtons.forEach((button) => {
      button.disabled = loading;
    });
    submit.setAttribute("aria-busy", String(loading));
    if (loading) {
      status.textContent = "Создаю черновик…";
    }
  };

  const addField = (parent, label, value, className = "") => {
    const item = document.createElement("div");
    item.className = "draft-field";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = `draft-field-value ${className}`.trim();
    content.textContent = textValue(value);
    item.append(name, content);
    parent.append(item);
  };

  const addListField = (parent, label, values) => {
    const item = document.createElement("div");
    item.className = "draft-field";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = "draft-field-value";
    if (!Array.isArray(values) || values.length === 0) {
      content.textContent = "—";
    } else {
      const list = document.createElement("ul");
      list.className = "draft-list";
      values.forEach((value) => {
        const itemValue = document.createElement("li");
        itemValue.textContent = textValue(value);
        list.append(itemValue);
      });
      content.append(list);
    }
    item.append(name, content);
    parent.append(item);
  };

  const renderSource = (source) => {
    const section = document.createElement("section");
    section.className = "provenance-block";
    const heading = document.createElement("h4");
    heading.textContent = "Источник";
    section.append(heading);
    const fields = document.createElement("dl");
    fields.className = "draft-fields provenance-fields";
    addField(fields, "URI", source.uri);
    addField(fields, "Тип", source.kind);
    addField(fields, "Получен", source.retrieved_at);
    addField(fields, "Опубликован", source.published_at);
    addField(fields, "Заголовок", source.title);
    addField(fields, "Автор", source.author);
    addField(fields, "Upstream ID", source.upstream_id);
    section.append(fields);
    return section;
  };

  const renderDraft = (payload) => {
    const draft = payload && typeof payload.draft === "object" ? payload.draft : {};
    result.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = "Черновик готов";
    result.append(heading);
    const fields = document.createElement("dl");
    fields.className = "draft-fields";
    addField(fields, "Название", draft.title);
    addField(fields, "Тип заметки", draft.note_type);
    addListField(fields, "Теги", draft.tags);
    addListField(fields, "Ссылки", draft.links);
    const contentItem = document.createElement("div");
    contentItem.className = "draft-field draft-content-field";
    const contentLabel = document.createElement("dt");
    contentLabel.className = "draft-field-label";
    contentLabel.textContent = "Содержание";
    const content = document.createElement("pre");
    content.className = "draft-content";
    content.textContent = textValue(draft.content);
    contentItem.append(contentLabel, content);
    fields.append(contentItem);
    result.append(fields);
    if (Array.isArray(payload && payload.sources) && payload.sources.length > 0) {
      payload.sources.forEach((source) => {
        if (source && typeof source === "object") {
          result.append(renderSource(source));
        }
      });
    }
    result.hidden = false;
  };

  const setMode = (nextMode) => {
    mode = nextMode === "text" ? "text" : "url";
    const isUrl = mode === "url";
    urlInput.classList.toggle("is-hidden", !isUrl);
    urlLabel.classList.toggle("is-hidden", !isUrl);
    textInput.classList.toggle("is-hidden", isUrl);
    textLabel.classList.toggle("is-hidden", isUrl);
    urlInput.disabled = !isUrl;
    textInput.disabled = isUrl;
    urlInput.required = isUrl;
    textInput.required = !isUrl;
    modeButtons.forEach((button) => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    clearFeedback();
  };

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFeedback();
    const value = mode === "url" ? urlInput.value : textInput.value;
    if (!value.trim()) {
      setError(mode === "url" ? "Укажи публичный URL." : "Введи текст материала.");
      return;
    }
    const endpoint = mode === "url" ? "/api/drafts/url" : "/api/drafts/text";
    const body = mode === "url" ? { url: value } : { text: value };
    setLoading(true);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setError(typeof message === "string" ? message : "Не удалось создать черновик.");
        return;
      }
      renderDraft(payload);
      status.textContent = "Готово";
    } catch (_error) {
      setError("Сервис черновиков недоступен.");
    } finally {
      setLoading(false);
    }
  });

  setMode("url");
}
