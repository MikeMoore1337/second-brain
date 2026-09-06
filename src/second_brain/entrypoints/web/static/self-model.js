"use strict";

const selfModelSurface = document.querySelector("[data-self-model-surface]");

if (selfModelSurface) {
  const refreshButton = selfModelSurface.querySelector("[data-self-model-refresh]");
  const status = selfModelSurface.querySelector("[data-self-model-status]");
  const error = selfModelSurface.querySelector("[data-self-model-error]");
  const summary = selfModelSurface.querySelector("[data-self-model-summary]");
  const claimList = selfModelSurface.querySelector("[data-self-model-list]");
  const empty = selfModelSurface.querySelector("[data-self-model-empty]");
  const selfModelHeaders = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Second-Brain-Request": "self-model-v1",
  };
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
    error.textContent = displayValue(message, "Не удалось построить Self Model.");
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
    refreshButton.disabled = value;
    refreshButton.setAttribute("aria-busy", String(value));
    if (value) {
      status.textContent = "Строю текущий Self Model…";
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
    item.className = "self-model-field";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = "self-model-field-value";
    content.textContent = displayValue(value);
    item.append(name, content);
    parent.append(item);
  };

  const renderEvidence = (ref) => {
    if (!ref || typeof ref !== "object") {
      throw new Error("invalid self model evidence");
    }
    const item = document.createElement("li");
    item.className = "self-model-evidence";
    const fields = document.createElement("dl");
    fields.className = "self-model-fields";
    appendField(fields, "Canonical UUID", ref.id);
    appendField(fields, "Evidence", ref.evidence_kind);
    appendField(fields, "Self kind", ref.self_kind);
    appendField(fields, "Домен", ref.domain);
    appendField(fields, "Evidence time", ref.evidence_at);
    appendField(fields, "Precision", ref.evidence_at_precision);
    if (Array.isArray(ref.related_note_ids) && ref.related_note_ids.length > 0) {
      appendField(fields, "Related UUIDs", ref.related_note_ids.join(", "));
    }
    item.append(fields);
    return item;
  };

  const appendEvidenceRole = (parent, label, refs) => {
    if (!Array.isArray(refs)) {
      throw new Error("invalid self model evidence role");
    }
    if (refs.length === 0) {
      return;
    }
    const heading = document.createElement("h4");
    heading.className = "self-model-evidence-heading";
    heading.textContent = label;
    const list = document.createElement("ul");
    list.className = "self-model-evidence-list";
    refs.forEach((ref) => list.append(renderEvidence(ref)));
    parent.append(heading, list);
  };

  const renderClaim = (claim) => {
    if (
      !claim ||
      typeof claim !== "object" ||
      !claim.confidence ||
      typeof claim.confidence !== "object" ||
      !claim.temporal_context ||
      typeof claim.temporal_context !== "object"
    ) {
      throw new Error("invalid self model claim");
    }

    const article = document.createElement("article");
    article.className = "self-model-claim";
    const heading = document.createElement("h3");
    heading.className = "self-model-claim-title";
    heading.textContent = displayValue(claim.dimension, "Claim");
    article.append(heading);

    const claimText = document.createElement("p");
    claimText.className = "self-model-claim-text";
    claimText.textContent = displayValue(claim.claim);
    article.append(claimText);

    const fields = document.createElement("dl");
    fields.className = "self-model-fields";
    appendField(fields, "Домен", claim.domain);
    appendField(fields, "Confidence", claim.confidence.state);
    appendField(fields, "Confidence policy", claim.confidence.policy_version);
    appendField(fields, "Supporting evidence", claim.confidence.supporting_evidence_count);
    appendField(fields, "Contradicting evidence", claim.confidence.contradicting_evidence_count);
    appendField(fields, "Unknown time", claim.confidence.unknown_time_count);
    appendField(fields, "Earliest evidence time", claim.temporal_context.earliest_known_evidence_at);
    appendField(fields, "Latest evidence time", claim.temporal_context.latest_known_evidence_at);
    appendField(fields, "Known evidence", claim.temporal_context.known_evidence_count);
    appendField(fields, "Unknown evidence", claim.temporal_context.unknown_evidence_count);
    appendField(fields, "Generated", claim.generated_at);
    appendField(fields, "Derivation", claim.derivation_version);
    article.append(fields);

    const evidence = document.createElement("div");
    evidence.className = "self-model-evidence";
    appendEvidenceRole(evidence, "Supporting evidence", claim.supporting_evidence);
    appendEvidenceRole(evidence, "Contradicting evidence", claim.contradicting_evidence);
    appendEvidenceRole(evidence, "Contextual evidence", claim.contextual_evidence);
    article.append(evidence);
    return article;
  };

  const renderResponse = (payload) => {
    if (
      !payload ||
      typeof payload !== "object" ||
      !Array.isArray(payload.claims) ||
      !Number.isInteger(payload.eligible_evidence_count) ||
      !Number.isInteger(payload.represented_evidence_count)
    ) {
      throw new Error("invalid self model response");
    }

    claimList.replaceChildren();
    payload.claims.forEach((claim) => claimList.append(renderClaim(claim)));
    empty.hidden = payload.claims.length !== 0;
    summary.textContent =
      `Claims: ${payload.claims.length} · Eligible evidence: ${displayValue(payload.eligible_evidence_count)} ` +
      `· Represented evidence: ${displayValue(payload.represented_evidence_count)} · Generated: ` +
      `${displayValue(payload.generated_at)} · Derivation: ${displayValue(payload.derivation_version)} ` +
      `· Policy fingerprint: ${displayValue(payload.policy_fingerprint)}`;
  };

  const loadSelfModel = async () => {
    if (busy) {
      return;
    }
    clearFeedback();
    setBusy(true);
    try {
      const response = await fetch("/api/self-model", {
        method: "POST",
        headers: selfModelHeaders,
        body: JSON.stringify({
          max_claims: 200,
          max_evidence_refs_per_claim: 200,
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setError(typeof message === "string" ? message : "Не удалось построить Self Model.");
        return;
      }
      renderResponse(payload);
      status.textContent = "Показан текущий Self Model из vault.";
    } catch (_error) {
      setError("Сервис Self Model недоступен.");
    } finally {
      setBusy(false);
    }
  };

  refreshButton.addEventListener("click", loadSelfModel);
}
