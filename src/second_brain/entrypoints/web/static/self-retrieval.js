"use strict";

const selfRetrievalSurface = document.querySelector("[data-self-retrieval-surface]");

if (selfRetrievalSurface) {
  const form = selfRetrievalSurface.querySelector("[data-self-retrieval-form]");
  const input = selfRetrievalSurface.querySelector("[data-self-retrieval-input]");
  const submit = selfRetrievalSurface.querySelector("[data-self-retrieval-submit]");
  const status = selfRetrievalSurface.querySelector("[data-self-retrieval-status]");
  const error = selfRetrievalSurface.querySelector("[data-self-retrieval-error]");
  const summary = selfRetrievalSurface.querySelector("[data-self-retrieval-summary]");
  const empty = selfRetrievalSurface.querySelector("[data-self-retrieval-empty]");
  const results = selfRetrievalSurface.querySelector("[data-self-retrieval-results]");
  const headers = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Second-Brain-Request": "self-retrieval-v1",
  };
  let busy = false;

  const displayValue = (value, fallback = "—") => {
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
    if (typeof value === "boolean") {
      return value ? "да" : "нет";
    }
    return fallback;
  };

  const setError = (message) => {
    error.textContent = displayValue(message, "Не удалось собрать контекст.");
    error.hidden = false;
    status.textContent = "";
  };

  const clearFeedback = () => {
    error.textContent = "";
    error.hidden = true;
    status.textContent = "";
  };

  const setBusy = (value) => {
    busy = value;
    input.disabled = value;
    submit.disabled = value;
    selfRetrievalSurface.setAttribute("aria-busy", String(value));
    submit.setAttribute("aria-busy", String(value));
    if (value) {
      status.textContent = "Собираю текущий контекст…";
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
    item.className = "self-retrieval-field";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = "self-retrieval-field-value";
    content.textContent = displayValue(value);
    item.append(name, content);
    parent.append(item);
  };

  const appendTags = (parent, tags) => {
    const list = document.createElement("div");
    list.className = "search-tags";
    if (Array.isArray(tags)) {
      tags.forEach((tag) => {
        if (typeof tag === "string") {
          const item = document.createElement("span");
          item.className = "search-tag";
          item.textContent = tag;
          list.append(item);
        }
      });
    }
    if (!list.children.length) {
      const emptyTag = document.createElement("span");
      emptyTag.className = "search-tag search-tag-empty";
      emptyTag.textContent = "без тегов";
      list.append(emptyTag);
    }
    parent.append(list);
  };

  const renderClaim = (claim) => {
    if (!claim || typeof claim !== "object" || !Array.isArray(claim.supporting_note_ids)) {
      throw new Error("invalid self retrieval claim");
    }
    const item = document.createElement("li");
    item.className = "self-retrieval-claim";
    const fields = document.createElement("dl");
    fields.className = "self-retrieval-fields";
    appendField(fields, "Dimension", claim.dimension);
    appendField(fields, "Claim (already derived)", claim.claim);
    appendField(fields, "Supporting UUIDs", claim.supporting_note_ids.join(", "));
    appendField(fields, "Derivation", claim.derivation_version);
    appendField(fields, "Policy fingerprint", claim.policy_fingerprint);
    item.append(fields);
    return item;
  };

  const renderItem = (contextItem) => {
    if (
      !contextItem ||
      typeof contextItem !== "object" ||
      typeof contextItem.note_id !== "string" ||
      typeof contextItem.body !== "string" ||
      !Array.isArray(contextItem.self_model_claims)
    ) {
      throw new Error("invalid self retrieval item");
    }
    const article = document.createElement("article");
    article.className = "self-retrieval-item";
    const heading = document.createElement("h3");
    heading.className = "self-retrieval-item-title";
    heading.textContent = displayValue(contextItem.title, "Без названия");
    article.append(heading);

    const fields = document.createElement("dl");
    fields.className = "self-retrieval-fields";
    appendField(fields, "Canonical UUID", contextItem.note_id);
    appendField(fields, "Тип заметки", contextItem.note_type);
    appendField(fields, "Search order (audit only)", contextItem.search_rank);
    appendField(fields, "Создано", contextItem.created);
    appendField(fields, "Обновлено", contextItem.updated);
    article.append(fields);
    appendTags(article, contextItem.tags);

    const bodyHeading = document.createElement("h4");
    bodyHeading.textContent = "Current reread body (только чтение)";
    const body = document.createElement("pre");
    body.className = "self-retrieval-body";
    body.textContent = contextItem.body;
    article.append(bodyHeading, body);

    const claimsHeading = document.createElement("h4");
    claimsHeading.textContent = "Exact Self Model claim links";
    article.append(claimsHeading);
    const claims = document.createElement("ul");
    claims.className = "self-retrieval-claims";
    contextItem.self_model_claims.forEach((claim) => claims.append(renderClaim(claim)));
    if (!claims.children.length) {
      const noClaims = document.createElement("li");
      noClaims.className = "self-retrieval-no-claims";
      noClaims.textContent = "Для этой заметки нет supporting UUID link.";
      claims.append(noClaims);
    }
    article.append(claims);
    return article;
  };

  const renderExclusions = (exclusions) => {
    if (!Array.isArray(exclusions) || exclusions.length === 0) {
      return;
    }
    const section = document.createElement("section");
    section.className = "self-retrieval-exclusions";
    const heading = document.createElement("h3");
    heading.textContent = "Исключения текущей сборки";
    section.append(heading);
    const list = document.createElement("ul");
    exclusions.forEach((exclusion) => {
      if (!exclusion || typeof exclusion !== "object") {
        throw new Error("invalid self retrieval exclusion");
      }
      const item = document.createElement("li");
      item.textContent = `Search order ${displayValue(exclusion.search_rank)} · ${displayValue(exclusion.reason)}`;
      list.append(item);
    });
    section.append(list);
    results.append(section);
  };

  const renderResponse = (payload) => {
    if (
      !payload ||
      typeof payload !== "object" ||
      !Array.isArray(payload.items) ||
      !Array.isArray(payload.exclusions) ||
      !Number.isInteger(payload.candidate_count) ||
      !Number.isInteger(payload.included_count) ||
      !Number.isInteger(payload.excluded_count)
    ) {
      throw new Error("invalid self retrieval response");
    }
    results.replaceChildren();
    payload.items.forEach((item) => results.append(renderItem(item)));
    renderExclusions(payload.exclusions);
    empty.hidden = payload.items.length !== 0;
    summary.textContent =
      `Candidates: ${displayValue(payload.candidate_count)} · Current items: ${displayValue(payload.included_count)} ` +
      `· Excluded: ${displayValue(payload.excluded_count)} · Content bytes: ${displayValue(payload.content_bytes)} ` +
      `· Truncated: ${displayValue(payload.truncated)} · Derivation: ${displayValue(payload.self_model_derivation_version)} ` +
      `· Policy fingerprint: ${displayValue(payload.self_model_policy_fingerprint)}`;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    const query = input.value;
    if (!query.trim()) {
      setError("Введи поисковый запрос.");
      return;
    }
    clearFeedback();
    results.replaceChildren();
    summary.textContent = "";
    empty.hidden = true;
    setBusy(true);
    try {
      const response = await fetch("/api/self-retrieval", {
        method: "POST",
        headers,
        body: JSON.stringify({ query, limit: 20, max_content_bytes: 65536 }),
      });
      const payload = await readPayload(response);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setError(typeof message === "string" ? message : "Не удалось собрать контекст.");
        return;
      }
      renderResponse(payload);
      status.textContent = "Показан текущий bounded context из vault.";
    } catch (_error) {
      setError("Сервис Self Retrieval недоступен.");
    } finally {
      setBusy(false);
    }
  });
}
