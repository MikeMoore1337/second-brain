"use strict";

// Draft values and the review token stay in this page's JavaScript memory only.
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
  const voicePanel = panel.querySelector("[data-voice-panel]");
  const recordButton = panel.querySelector("[data-record]");
  const stopButton = panel.querySelector("[data-stop]");
  const audioFileInput = panel.querySelector("[data-audio-file]");
  const transcribeButton = panel.querySelector("[data-transcribe]");
  const voiceStatus = panel.querySelector("[data-voice-status]");
  const requestHeaders = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Second-Brain-Request": "draft-v1",
  };
  const noteTypes = ["project", "area", "resource", "zettel"];
  const personalMemoryEvidenceKinds = [
    ["explicit_user_fact", "Факт обо мне / моей ситуации"],
    ["user_statement", "Моё утверждение, мнение, цель или самоописание"],
  ];
  const personalMemorySelfKinds = [
    ["memory", "Память"],
    ["preference", "Предпочтение"],
    ["belief", "Убеждение"],
    ["goal", "Цель"],
  ];
  const personalMemoryTimeModes = [
    ["exact", "Точное время"],
    ["unknown", "Время неизвестно"],
  ];
  const audioMediaTypes = [
    "audio/webm",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/x-m4a",
  ];
  const maxAudioBytes = 15 * 1024 * 1024;
  const RECORDING_TIMESLICE_MS = 1000;
  let mode = "url";
  let reviewToken = null;
  let confirmationToken = null;
  let reviewState = null;
  let busy = false;
  let recorder = null;
  let recordingStream = null;
  let audioChunks = [];
  let audioBlob = null;
  let audioMediaType = null;
  let recording = false;
  let microphonePending = false;
  let microphoneRequestGeneration = 0;
  let recordingBytes = 0;
  let recordingTooLarge = false;

  const canRecord = Boolean(
    typeof navigator !== "undefined" &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function" &&
    typeof window !== "undefined" &&
    typeof window.MediaRecorder === "function",
  );

  const recordingMimeCandidates = [
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/webm",
    "audio/ogg",
  ];

  const textValue = (value, fallback = "—") =>
    typeof value === "string" && value.length > 0 ? value : fallback;

  const arrayValue = (value) =>
    Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];

  const setError = (message) => {
    error.textContent = textValue(message, "Не удалось выполнить операцию.");
    error.hidden = false;
    status.textContent = "";
  };

  const clearFeedback = () => {
    error.textContent = "";
    error.hidden = true;
    status.textContent = "";
  };

  const clearReviewState = () => {
    reviewToken = null;
    confirmationToken = null;
    reviewState = null;
    result.replaceChildren();
    result.hidden = true;
  };

  const stopAudioTracks = (stream) => {
    if (stream && typeof stream.getTracks === "function") {
      stream.getTracks().forEach((track) => {
        if (track && typeof track.stop === "function") {
          track.stop();
        }
      });
    }
  };

  const stopRecordingStream = () => {
    stopAudioTracks(recordingStream);
    recordingStream = null;
  };

  const clearAudioState = () => {
    microphoneRequestGeneration += 1;
    microphonePending = false;
    if (recording && recorder) {
      recorder.onstop = null;
      recorder.stop();
    }
    stopRecordingStream();
    recorder = null;
    audioChunks = [];
    audioBlob = null;
    audioMediaType = null;
    recording = false;
    recordingBytes = 0;
    recordingTooLarge = false;
    if (audioFileInput) {
      audioFileInput.value = "";
    }
    if (voiceStatus) {
      voiceStatus.textContent = "";
    }
  };

  const isCurrentMicrophoneRequest = (requestGeneration) =>
    requestGeneration === microphoneRequestGeneration && mode === "voice" && microphonePending;

  const updateVoiceControls = () => {
    if (!voicePanel) {
      return;
    }
    recordButton.disabled = busy || recording || microphonePending || !canRecord;
    stopButton.disabled = busy || !recording;
    stopButton.hidden = !recording;
    audioFileInput.disabled = busy || recording || microphonePending;
    transcribeButton.disabled = busy || recording || !audioBlob;
  };

  const setCaptureLoading = (loading) => {
    submit.disabled = loading;
    urlInput.disabled = loading || mode !== "url";
    textInput.disabled = loading || mode !== "text";
    modeButtons.forEach((button) => {
      button.disabled = loading;
    });
    submit.setAttribute("aria-busy", String(loading));
    updateVoiceControls();
  };

  const setReviewLoading = (loading) => {
    if (!reviewState) {
      return;
    }
    [
      reviewState.title,
      reviewState.noteType,
      reviewState.tags,
      reviewState.links,
      reviewState.content,
      reviewState.previewButton,
      reviewState.prepareButton,
      reviewState.confirmButton,
    ].forEach((control) => {
      control.disabled = loading || reviewState.saved;
    });
    reviewState.personalMemoryControls.forEach((control) => {
      control.disabled = loading || reviewState.saved;
    });
    reviewState.addButton.disabled = loading;
    reviewState.prepareButton.setAttribute("aria-busy", String(loading));
    reviewState.confirmButton.setAttribute("aria-busy", String(loading));
    reviewState.previewButton.setAttribute("aria-busy", String(loading));
  };

  const setLoading = (loading) => {
    busy = loading;
    setCaptureLoading(loading);
    setReviewLoading(loading);
    if (loading) {
      status.textContent = "Выполняю операцию…";
    }
  };

  const chooseRecordingMimeType = () => {
    if (!canRecord || typeof window.MediaRecorder.isTypeSupported !== "function") {
      return "";
    }
    return recordingMimeCandidates.find((value) => window.MediaRecorder.isTypeSupported(value)) || "";
  };

  const normalizedAudioType = (value) => {
    if (typeof value !== "string") {
      return "";
    }
    return value.split(";", 1)[0].trim().toLowerCase();
  };

  const audioTypeAllowed = (value) => audioMediaTypes.includes(normalizedAudioType(value));

  const setVoiceReadyStatus = (message) => {
    if (voiceStatus) {
      voiceStatus.textContent = message;
    }
  };

  const finishRecording = (completedRecorder) => {
    stopRecordingStream();
    recording = false;
    recorder = null;
    const exceededLimit = recordingTooLarge;
    recordingBytes = 0;
    recordingTooLarge = false;
    const type =
      (completedRecorder && typeof completedRecorder.mimeType === "string" && completedRecorder.mimeType) ||
      audioMediaType ||
      "audio/webm";
    if (exceededLimit) {
      audioChunks = [];
      audioBlob = null;
      audioMediaType = null;
      setVoiceReadyStatus("Запись превысила лимит 15 MiB.");
    } else if (audioChunks.length === 0) {
      audioBlob = null;
      audioMediaType = null;
      setVoiceReadyStatus("Запись не содержит audio-данных.");
    } else {
      audioBlob = new Blob(audioChunks, { type });
      audioMediaType = normalizedAudioType(type) || "audio/webm";
      audioChunks = [];
      setVoiceReadyStatus("Аудио готово к распознаванию.");
    }
    updateVoiceControls();
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

  const renderSource = (source) => {
    const section = document.createElement("section");
    section.className = "provenance-block";
    const heading = document.createElement("h4");
    heading.textContent = "Источник (только чтение)";
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

  const editorField = (parent, label, id, tagName = "input") => {
    const wrapper = document.createElement("div");
    wrapper.className = "review-field";
    const labelElement = document.createElement("label");
    labelElement.className = "draft-field-label";
    labelElement.htmlFor = id;
    labelElement.textContent = label;
    const control = document.createElement(tagName);
    control.className = "review-input";
    control.id = id;
    wrapper.append(labelElement, control);
    parent.append(wrapper);
    return control;
  };

  const createEditor = (draft, allowPersonalMemory) => {
    const section = document.createElement("section");
    section.className = "review-editor";
    const heading = document.createElement("h4");
    heading.textContent = "Проверь и отредактируй";
    section.append(heading);

    const fields = document.createElement("div");
    fields.className = "review-fields";
    const title = editorField(fields, "Название", "review-title");
    title.type = "text";
    title.autocomplete = "off";
    title.value = typeof draft.title === "string" ? draft.title : "";

    const noteType = editorField(fields, "Тип заметки", "review-note-type", "select");
    noteTypes.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      noteType.append(option);
    });
    noteType.value = noteTypes.includes(draft.note_type) ? draft.note_type : "resource";

    const tags = editorField(fields, "Теги (один на строку)", "review-tags", "textarea");
    tags.rows = 3;
    tags.value = arrayValue(draft.tags).join("\n");

    const links = editorField(fields, "Ссылки (одна на строку)", "review-links", "textarea");
    links.rows = 3;
    links.value = arrayValue(draft.links).join("\n");

    const content = editorField(fields, "Содержание", "review-content", "textarea");
    content.rows = 12;
    content.value = typeof draft.content === "string" ? draft.content : "";
    section.append(fields);

    let personalMemoryToggle = null;
    let personalMemoryEvidenceKind = null;
    let personalMemorySelfKind = null;
    let personalMemoryTimeMode = null;
    let personalMemoryEvidenceAt = null;
    let personalMemoryDomain = null;
    let personalMemoryNowButton = null;
    const personalMemoryControls = [];

    if (allowPersonalMemory) {
      const personalMemoryPanel = document.createElement("section");
      personalMemoryPanel.className = "personal-memory-panel";
      const personalMemoryHeading = document.createElement("h5");
      personalMemoryHeading.textContent = "Личная память";
      const personalMemoryDescription = document.createElement("p");
      personalMemoryDescription.className = "personal-memory-description";
      personalMemoryDescription.textContent =
        "Включи режим только после проверки draft и явно укажи Stage 1 metadata.";
      const toggleLabel = document.createElement("label");
      toggleLabel.className = "personal-memory-toggle";
      personalMemoryToggle = document.createElement("input");
      personalMemoryToggle.type = "checkbox";
      personalMemoryToggle.id = "personal-memory-toggle";
      personalMemoryToggle.dataset.personalMemoryToggle = "";
      personalMemoryToggle.setAttribute("aria-controls", "personal-memory-fields");
      const toggleText = document.createElement("span");
      toggleText.textContent = "Сохранить как Personal Memory";
      toggleLabel.append(personalMemoryToggle, toggleText);

      const metadataFields = document.createElement("div");
      metadataFields.className = "personal-memory-fields";
      metadataFields.id = "personal-memory-fields";
      metadataFields.dataset.personalMemoryFields = "";
      metadataFields.hidden = true;

      const addOptions = (select, options, placeholder) => {
        if (placeholder) {
          const emptyOption = document.createElement("option");
          emptyOption.value = "";
          emptyOption.textContent = placeholder;
          emptyOption.disabled = true;
          emptyOption.selected = true;
          select.append(emptyOption);
        }
        options.forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          select.append(option);
        });
      };

      personalMemoryEvidenceKind = editorField(
        metadataFields,
        "Основание",
        "personal-memory-evidence-kind",
        "select",
      );
      addOptions(
        personalMemoryEvidenceKind,
        personalMemoryEvidenceKinds,
        "Выбери тип основания",
      );

      personalMemorySelfKind = editorField(
        metadataFields,
        "Что сохраняем о себе",
        "personal-memory-self-kind",
        "select",
      );
      addOptions(personalMemorySelfKind, personalMemorySelfKinds, "Выбери тип памяти");

      personalMemoryDomain = editorField(
        metadataFields,
        "Домен (необязательно)",
        "personal-memory-domain",
      );
      personalMemoryDomain.type = "text";
      personalMemoryDomain.autocomplete = "off";

      personalMemoryTimeMode = editorField(
        metadataFields,
        "Время факта",
        "personal-memory-time-mode",
        "select",
      );
      addOptions(personalMemoryTimeMode, personalMemoryTimeModes);
      personalMemoryTimeMode.value = "unknown";

      const evidenceAtWrapper = document.createElement("div");
      evidenceAtWrapper.className = "personal-memory-time-field";
      metadataFields.append(evidenceAtWrapper);
      const evidenceAtLabel = document.createElement("label");
      evidenceAtLabel.className = "draft-field-label";
      evidenceAtLabel.htmlFor = "personal-memory-evidence-at";
      evidenceAtLabel.textContent = "RFC3339 время факта";
      personalMemoryEvidenceAt = document.createElement("input");
      personalMemoryEvidenceAt.className = "review-input";
      personalMemoryEvidenceAt.id = "personal-memory-evidence-at";
      personalMemoryEvidenceAt.type = "text";
      personalMemoryEvidenceAt.autocomplete = "off";
      personalMemoryEvidenceAt.placeholder = "2026-09-05T15:30:00Z";
      personalMemoryEvidenceAt.value = "unknown";
      personalMemoryNowButton = document.createElement("button");
      personalMemoryNowButton.className = "review-button review-button-secondary";
      personalMemoryNowButton.type = "button";
      personalMemoryNowButton.textContent = "Сейчас";
      evidenceAtWrapper.append(
        evidenceAtLabel,
        personalMemoryEvidenceAt,
        personalMemoryNowButton,
      );

      personalMemoryPanel.append(
        personalMemoryHeading,
        personalMemoryDescription,
        toggleLabel,
        metadataFields,
      );
      section.append(personalMemoryPanel);

      const syncPersonalMemoryTime = () => {
        const exact = personalMemoryTimeMode.value === "exact";
        evidenceAtWrapper.hidden = !exact;
        personalMemoryEvidenceAt.required = exact && personalMemoryToggle.checked;
        personalMemoryNowButton.hidden = !exact;
        if (!exact) {
          personalMemoryEvidenceAt.value = "unknown";
        } else if (personalMemoryEvidenceAt.value === "unknown") {
          personalMemoryEvidenceAt.value = "";
        }
      };

      const syncPersonalMemoryVisibility = () => {
        const enabled = personalMemoryToggle.checked;
        metadataFields.hidden = !enabled;
        personalMemoryEvidenceKind.required = enabled;
        personalMemorySelfKind.required = enabled;
        personalMemoryTimeMode.required = enabled;
        syncPersonalMemoryTime();
      };

      personalMemoryToggle.addEventListener("change", () => {
        syncPersonalMemoryVisibility();
        invalidatePreparedPlan();
      });
      personalMemoryTimeMode.addEventListener("change", () => {
        syncPersonalMemoryTime();
        invalidatePreparedPlan();
      });
      personalMemoryNowButton.addEventListener("click", () => {
        personalMemoryEvidenceAt.value = new Date().toISOString();
        invalidatePreparedPlan();
      });
      [
        personalMemoryEvidenceKind,
        personalMemorySelfKind,
        personalMemoryEvidenceAt,
        personalMemoryDomain,
      ].forEach((control) => {
        control.addEventListener("input", invalidatePreparedPlan);
        control.addEventListener("change", invalidatePreparedPlan);
      });
      personalMemoryControls.push(
        personalMemoryToggle,
        personalMemoryEvidenceKind,
        personalMemorySelfKind,
        personalMemoryTimeMode,
        personalMemoryEvidenceAt,
        personalMemoryDomain,
        personalMemoryNowButton,
      );
      syncPersonalMemoryVisibility();
    }

    const personalMemoryEnabled = () =>
      personalMemoryToggle !== null && personalMemoryToggle.checked;
    const personalMemoryPayload = () => {
      if (
        !personalMemoryEnabled() ||
        personalMemoryEvidenceKind === null ||
        personalMemorySelfKind === null ||
        personalMemoryTimeMode === null ||
        personalMemoryEvidenceAt === null ||
        personalMemoryDomain === null
      ) {
        return null;
      }
      const unknownTime = personalMemoryTimeMode.value === "unknown";
      return {
        evidence_kind: personalMemoryEvidenceKind.value,
        self_kind: personalMemorySelfKind.value,
        evidence_at: unknownTime ? "unknown" : personalMemoryEvidenceAt.value,
        evidence_at_precision: unknownTime ? "unknown" : "exact",
        domain: personalMemoryDomain.value === "" ? null : personalMemoryDomain.value,
      };
    };

    const actions = document.createElement("div");
    actions.className = "review-actions";
    const previewButton = document.createElement("button");
    previewButton.className = "review-button review-button-secondary";
    previewButton.type = "button";
    previewButton.textContent = "Предпросмотр";
    const prepareButton = document.createElement("button");
    prepareButton.className = "review-button review-button-primary";
    prepareButton.type = "button";
    prepareButton.textContent = "Подготовить сохранение";
    const confirmButton = document.createElement("button");
    confirmButton.className = "review-button review-button-primary";
    confirmButton.type = "button";
    confirmButton.textContent = "Подтвердить сохранение";
    confirmButton.hidden = true;
    confirmButton.disabled = true;
    const addButton = document.createElement("button");
    addButton.className = "review-button review-button-quiet";
    addButton.type = "button";
    addButton.textContent = "Добавить ещё";
    actions.append(previewButton, prepareButton, confirmButton, addButton);
    section.append(actions);

    const previewStatus = document.createElement("p");
    previewStatus.className = "review-status";
    previewStatus.setAttribute("role", "status");
    previewStatus.setAttribute("aria-live", "polite");
    section.append(previewStatus);
    const preview = document.createElement("div");
    preview.className = "markdown-preview";
    preview.hidden = true;
    section.append(preview);
    const plan = document.createElement("section");
    plan.className = "save-plan";
    plan.hidden = true;
    section.append(plan);

    reviewState = {
      title,
      noteType,
      tags,
      links,
      content,
      previewButton,
      prepareButton,
      confirmButton,
      addButton,
      previewStatus,
      preview,
      plan,
      personalMemoryControls,
      personalMemoryEnabled,
      personalMemoryPayload,
      preparedPersonalMemory: false,
      saved: false,
    };

    const editedDraft = () => {
      const lines = (value) => value.split(/\r?\n/).filter((item) => item.length > 0);
      return {
        title: title.value,
        note_type: noteType.value,
        content: content.value,
        tags: lines(tags.value),
        links: lines(links.value),
      };
    };

    const invalidatePreparedPlan = () => {
      confirmationToken = null;
      reviewState.preparedPersonalMemory = false;
      confirmButton.hidden = true;
      confirmButton.disabled = true;
      plan.replaceChildren();
      plan.hidden = true;
      previewStatus.textContent = "Изменения требуют новой подготовки Safe Write.";
    };

    [title, noteType, tags, links, content].forEach((control) => {
      control.addEventListener("input", invalidatePreparedPlan);
      control.addEventListener("change", invalidatePreparedPlan);
    });

    const renderPlan = (payload) => {
      if (
        !payload ||
        payload.status !== "dry-run" ||
        typeof payload.confirmation_token !== "string" ||
        payload.note === null ||
        typeof payload.note !== "object" ||
        typeof payload.diff !== "string"
      ) {
        throw new Error("invalid dry-run response");
      }
      plan.replaceChildren();
      const planHeading = document.createElement("h4");
      planHeading.textContent = reviewState.preparedPersonalMemory
        ? "План Personal Memory Safe Write (dry-run)"
        : "План Safe Write (dry-run)";
      const planFields = document.createElement("dl");
      planFields.className = "draft-fields";
      addField(planFields, "Тип", payload.note.type);
      addField(planFields, "Путь", payload.note.relative_path);
      const explanation = document.createElement("p");
      explanation.className = "save-plan-explanation";
      explanation.textContent =
        "ID и created принадлежат этому dry-run; при подтверждении Safe Write создаст новые значения.";
      const diffHeading = document.createElement("h5");
      diffHeading.textContent = "Предлагаемый Markdown-файл";
      const diff = document.createElement("pre");
      diff.className = "save-diff";
      diff.textContent = payload.diff;
      plan.append(planHeading, planFields, explanation, diffHeading, diff);
      plan.hidden = false;
    };

    previewButton.addEventListener("click", async () => {
      if (busy || !reviewState || reviewState.saved) {
        return;
      }
      setLoading(true);
      previewStatus.textContent = "Готовлю безопасный preview…";
      try {
        const response = await fetch("/api/drafts/preview", {
          method: "POST",
          headers: requestHeaders,
          body: JSON.stringify({ content: content.value }),
        });
        const payload = await response.json();
        if (!response.ok) {
          const message = payload && payload.error && payload.error.message;
          setError(typeof message === "string" ? message : "Не удалось построить preview.");
          previewStatus.textContent = "";
          return;
        }
        const html = payload && typeof payload.html === "string" ? payload.html : "";
        // This is the single dedicated container allowed to receive server-safe preview HTML.
        preview.innerHTML = html;
        preview.hidden = false;
        previewStatus.textContent = "Preview готов";
      } catch (_error) {
        setError("Сервис preview недоступен.");
        previewStatus.textContent = "";
      } finally {
        setLoading(false);
      }
    });

    prepareButton.addEventListener("click", async () => {
      if (busy || !reviewState || reviewState.saved || typeof reviewToken !== "string") {
        return;
      }
      invalidatePreparedPlan();
      const originalReviewToken = reviewToken;
      const personalMemoryMode = reviewState.personalMemoryEnabled();
      const body = { review_token: originalReviewToken, draft: editedDraft() };
      let endpoint = "/api/drafts/save/prepare";
      if (personalMemoryMode) {
        const metadata = reviewState.personalMemoryPayload();
        if (
          metadata === null ||
          !metadata.evidence_kind ||
          !metadata.self_kind ||
          (metadata.evidence_at_precision === "exact" && !metadata.evidence_at)
        ) {
          setError("Укажи все обязательные Personal Memory metadata.");
          return;
        }
        endpoint = "/api/drafts/personal-memory/save/prepare";
        body.personal_memory = metadata;
      }
      setLoading(true);
      previewStatus.textContent = "Готовлю Safe Write dry-run…";
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: requestHeaders,
          body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok) {
          const message = payload && payload.error && payload.error.message;
          setError(typeof message === "string" ? message : "Не удалось подготовить сохранение.");
          return;
        }
        reviewState.preparedPersonalMemory = personalMemoryMode;
        renderPlan(payload);
        confirmationToken = payload.confirmation_token;
        confirmButton.hidden = false;
        confirmButton.disabled = false;
        previewStatus.textContent = "План подготовлен; проверь diff и подтверди сохранение.";
      } catch (_error) {
        setError("Сервис подготовки сохранения недоступен.");
      } finally {
        setLoading(false);
      }
    });

    confirmButton.addEventListener("click", async () => {
      if (
        busy ||
        !reviewState ||
        reviewState.saved ||
        typeof reviewToken !== "string" ||
        typeof confirmationToken !== "string"
      ) {
        return;
      }
      const originalReviewToken = reviewToken;
      const preparedConfirmationToken = confirmationToken;
      const savedAsPersonalMemory = reviewState.preparedPersonalMemory;
      const body = {
        review_token: originalReviewToken,
        confirmation_token: preparedConfirmationToken,
        draft: editedDraft(),
      };
      let endpoint = "/api/drafts/save/apply";
      if (savedAsPersonalMemory) {
        const metadata = reviewState.personalMemoryPayload();
        if (metadata === null) {
          return;
        }
        endpoint = "/api/drafts/personal-memory/save/apply";
        body.personal_memory = metadata;
      }
      setLoading(true);
      status.textContent = "Сохраняю в vault…";
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: requestHeaders,
          body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok) {
          const message = payload && payload.error && payload.error.message;
          setError(typeof message === "string" ? message : "Не удалось сохранить заметку.");
          return;
        }
        if (
          !payload ||
          payload.status !== "created" ||
          payload.note === null ||
          typeof payload.note !== "object"
        ) {
          setError("Сервис сохранения вернул неполный результат.");
          return;
        }
        reviewToken = null;
        confirmationToken = null;
        reviewState.saved = true;
        reviewState.preparedPersonalMemory = false;
        confirmButton.hidden = true;
        const saved = document.createElement("section");
        saved.className = "saved-note";
        const savedHeading = document.createElement("h4");
        savedHeading.textContent = "Сохранено";
        saved.append(savedHeading);
        if (savedAsPersonalMemory) {
          const savedMode = document.createElement("p");
          savedMode.className = "saved-note-mode";
          savedMode.textContent = "Сохранено как Personal Memory";
          saved.append(savedMode);
        }
        const savedFields = document.createElement("dl");
        savedFields.className = "draft-fields";
        addField(savedFields, "Путь", payload.note.relative_path);
        addField(savedFields, "ID", payload.note.id);
        addField(savedFields, "Создано", payload.note.created);
        saved.append(savedFields);
        section.append(saved);
        status.textContent = savedAsPersonalMemory
          ? "Personal Memory сохранена"
          : "Заметка сохранена";
      } catch (_error) {
        setError("Сервис сохранения недоступен.");
      } finally {
        setLoading(false);
      }
    });

    addButton.addEventListener("click", () => {
      clearAudioState();
      clearReviewState();
      urlInput.value = "";
      textInput.value = "";
      clearFeedback();
      setMode(mode);
    });

    return section;
  };

  const renderDraft = (payload) => {
    const draft =
      payload && payload.draft !== null && typeof payload.draft === "object"
        ? payload.draft
        : {};
    if (!payload || typeof payload.review_token !== "string") {
      setError("Сервер не выдал review token.");
      return;
    }
    clearReviewState();
    reviewToken = payload.review_token;
    const heading = document.createElement("h3");
    heading.textContent = "Черновик готов";
    const sourceFreeText = Array.isArray(payload.sources) && payload.sources.length === 0;
    result.append(heading, createEditor(draft, sourceFreeText));
    if (Array.isArray(payload.sources) && payload.sources.length > 0) {
      payload.sources.forEach((source) => {
        if (source && typeof source === "object") {
          result.append(renderSource(source));
        }
      });
    }
    result.hidden = false;
  };

  const setMode = (nextMode) => {
    const normalizedMode = ["url", "text", "voice"].includes(nextMode) ? nextMode : "url";
    if (mode === "voice" && normalizedMode !== "voice") {
      clearAudioState();
    }
    mode = normalizedMode;
    const isUrl = mode === "url";
    const isVoice = mode === "voice";
    urlInput.classList.toggle("is-hidden", !isUrl);
    urlLabel.classList.toggle("is-hidden", !isUrl);
    textInput.classList.toggle("is-hidden", isUrl || isVoice);
    textLabel.classList.toggle("is-hidden", isUrl || isVoice);
    form.classList.toggle("is-hidden", isVoice);
    voicePanel.classList.toggle("is-hidden", !isVoice);
    urlInput.disabled = !isUrl || busy;
    textInput.disabled = isUrl || isVoice || busy;
    urlInput.required = isUrl;
    textInput.required = !isUrl && !isVoice;
    modeButtons.forEach((button) => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    clearReviewState();
    clearFeedback();
    updateVoiceControls();
  };

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (!busy) {
        setMode(button.dataset.mode);
      }
    });
  });

  recordButton.addEventListener("click", async () => {
    if (busy || recording || microphonePending || !canRecord) {
      return;
    }
    clearReviewState();
    clearFeedback();
    clearAudioState();
    const requestGeneration = microphoneRequestGeneration;
    microphonePending = true;
    updateVoiceControls();
    setVoiceReadyStatus("Запрашиваю доступ к микрофону…");
    try {
      const acquiredStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!isCurrentMicrophoneRequest(requestGeneration)) {
        stopAudioTracks(acquiredStream);
        return;
      }
      recordingStream = acquiredStream;
      const mimeType = chooseRecordingMimeType();
      recorder = mimeType
        ? new window.MediaRecorder(recordingStream, { mimeType })
        : new window.MediaRecorder(recordingStream);
      audioChunks = [];
      recordingBytes = 0;
      recordingTooLarge = false;
      audioMediaType = recorder.mimeType || mimeType || "audio/webm";
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          if (recordingTooLarge) {
            return;
          }
          const nextBytes = recordingBytes + event.data.size;
          if (nextBytes > maxAudioBytes) {
            recordingTooLarge = true;
            setVoiceReadyStatus("Запись превысила лимит 15 MiB; останавливаю запись…");
            if (recorder && recorder.state === "recording") {
              recorder.stop();
            }
            stopRecordingStream();
            return;
          }
          recordingBytes = nextBytes;
          audioChunks.push(event.data);
        }
      };
      recorder.onstop = () => finishRecording(recorder);
      recorder.onerror = () => {
        stopRecordingStream();
        recorder = null;
        audioChunks = [];
        audioBlob = null;
        audioMediaType = null;
        recording = false;
        recordingBytes = 0;
        recordingTooLarge = false;
        setVoiceReadyStatus("Не удалось записать audio.");
        updateVoiceControls();
      };
      recorder.start(RECORDING_TIMESLICE_MS);
      recording = true;
      setVoiceReadyStatus("Идёт запись. Нажми «Остановить», когда закончишь.");
    } catch (_error) {
      if (!isCurrentMicrophoneRequest(requestGeneration)) {
        return;
      }
      stopRecordingStream();
      recorder = null;
      audioChunks = [];
      audioBlob = null;
      audioMediaType = null;
      recording = false;
      recordingBytes = 0;
      recordingTooLarge = false;
      setVoiceReadyStatus("");
      setError("Не удалось получить доступ к микрофону.");
    } finally {
      if (requestGeneration === microphoneRequestGeneration) {
        microphonePending = false;
        updateVoiceControls();
      }
    }
  });

  stopButton.addEventListener("click", () => {
    if (!busy && recording && recorder) {
      recorder.stop();
    }
  });

  audioFileInput.addEventListener("change", () => {
    if (busy || recording || microphonePending) {
      return;
    }
    const file = audioFileInput.files && audioFileInput.files[0];
    if (!file) {
      return;
    }
    clearReviewState();
    clearFeedback();
    if (file.size <= 0) {
      clearAudioState();
      setError("Выбери непустой audio-файл.");
      return;
    }
    if (file.size > maxAudioBytes) {
      clearAudioState();
      setError("Audio-файл превышает лимит 15 MiB.");
      return;
    }
    if (!audioTypeAllowed(file.type)) {
      clearAudioState();
      setError("Этот MIME-тип audio не поддерживается.");
      return;
    }
    audioBlob = file;
    audioMediaType = normalizedAudioType(file.type);
    setVoiceReadyStatus("Файл готов к распознаванию.");
    updateVoiceControls();
  });

  transcribeButton.addEventListener("click", async () => {
    if (busy || recording || !audioBlob || !audioTypeAllowed(audioMediaType)) {
      return;
    }
    const selectedAudio = audioBlob;
    const selectedMediaType = audioMediaType;
    setLoading(true);
    setVoiceReadyStatus("Распознаю audio…");
    try {
      const response = await fetch("/api/transcriptions/audio", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": selectedMediaType,
          "X-Second-Brain-Request": "voice-v1",
        },
        body: selectedAudio,
      });
      const payload = await response.json();
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setError(typeof message === "string" ? message : "Не удалось распознать audio.");
        setVoiceReadyStatus("");
        return;
      }
      const transcript =
        payload && payload.transcript && typeof payload.transcript.text === "string"
          ? payload.transcript.text
          : "";
      if (!transcript.trim()) {
        setError("Сервис распознавания вернул пустой transcript.");
        setVoiceReadyStatus("");
        return;
      }
      clearAudioState();
      setMode("text");
      textInput.value = transcript;
      status.textContent = "Проверь расшифровку и затем создай черновик";
      textInput.focus();
    } catch (_error) {
      setError("Сервис распознавания недоступен.");
      setVoiceReadyStatus("");
    } finally {
      setLoading(false);
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    clearReviewState();
    clearFeedback();
    const value = mode === "url" ? urlInput.value : textInput.value;
    if (!value.trim()) {
      setError(mode === "url" ? "Укажи публичный URL." : "Введи текст материала.");
      return;
    }
    const endpoint = mode === "url" ? "/api/drafts/url" : "/api/drafts/text";
    const body = mode === "url" ? { url: value } : { text: value };
    setLoading(true);
    status.textContent = "Создаю черновик…";
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: requestHeaders,
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setError(typeof message === "string" ? message : "Не удалось создать черновик.");
        return;
      }
      renderDraft(payload);
      status.textContent = "Проверь draft перед сохранением";
    } catch (_error) {
      setError("Сервис черновиков недоступен.");
    } finally {
      setLoading(false);
    }
  });

  setMode("url");
}

const searchSurface = document.querySelector("[data-search-surface]");

if (searchSurface) {
  const searchForm = searchSurface.querySelector("[data-search-form]");
  const searchInput = searchSurface.querySelector("[data-search-input]");
  const searchSubmit = searchSurface.querySelector("[data-search-submit]");
  const searchStatus = searchSurface.querySelector("[data-search-status]");
  const searchError = searchSurface.querySelector("[data-search-error]");
  const searchEmpty = searchSurface.querySelector("[data-search-empty]");
  const searchResults = searchSurface.querySelector("[data-search-results]");
  const retrievedNote = searchSurface.querySelector("[data-retrieved-note]");
  const searchHeaders = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Second-Brain-Request": "search-v1",
  };
  let searchBusy = false;

  const searchTextValue = (value, fallback = "—") =>
    typeof value === "string" && value.length > 0 ? value : fallback;

  const setSearchError = (message) => {
    searchError.textContent = searchTextValue(message, "Не удалось выполнить поиск.");
    searchError.hidden = false;
    searchStatus.textContent = "";
  };

  const clearSearchFeedback = () => {
    searchError.textContent = "";
    searchError.hidden = true;
    searchStatus.textContent = "";
  };

  const setSearchBusy = (busy) => {
    searchBusy = busy;
    searchInput.disabled = busy;
    searchSubmit.disabled = busy;
    searchSubmit.setAttribute("aria-busy", String(busy));
    if (busy) {
      searchStatus.textContent = "Ищу в памяти…";
    }
  };

  const readSearchPayload = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const addSearchField = (parent, label, value) => {
    const item = document.createElement("div");
    item.className = "search-field";
    const name = document.createElement("dt");
    name.className = "draft-field-label";
    name.textContent = label;
    const content = document.createElement("dd");
    content.className = "search-field-value";
    content.textContent = searchTextValue(value);
    item.append(name, content);
    parent.append(item);
  };

  const appendSearchTags = (parent, tags) => {
    const list = document.createElement("div");
    list.className = "search-tags";
    if (Array.isArray(tags) && tags.length > 0) {
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
      const empty = document.createElement("span");
      empty.className = "search-tag search-tag-empty";
      empty.textContent = "без тегов";
      list.append(empty);
    }
    parent.append(list);
  };

  const renderRetrievedNote = (note) => {
    if (!note || typeof note !== "object") {
      throw new Error("invalid retrieved note");
    }
    retrievedNote.replaceChildren();
    const heading = document.createElement("h3");
    heading.id = "retrieved-note-title";
    heading.textContent = searchTextValue(note.title, "Заметка");
    retrievedNote.append(heading);
    const fields = document.createElement("dl");
    fields.className = "search-fields";
    addSearchField(fields, "ID", note.id);
    addSearchField(fields, "Тип", note.type);
    addSearchField(fields, "Путь", note.relative_path);
    addSearchField(fields, "Создано", note.created);
    addSearchField(fields, "Обновлено", note.updated);
    appendSearchTags(retrievedNote, note.tags);
    retrievedNote.append(fields);
    const bodyLabel = document.createElement("h4");
    bodyLabel.textContent = "Содержание (только чтение)";
    const body = document.createElement("pre");
    body.className = "retrieved-note-body";
    body.textContent = searchTextValue(note.content, "");
    retrievedNote.append(bodyLabel, body);
    retrievedNote.hidden = false;
  };

  const openNote = async (noteId) => {
    if (searchBusy || typeof noteId !== "string" || !noteId) {
      return;
    }
    setSearchBusy(true);
    searchStatus.textContent = "Открываю текущую заметку…";
    try {
      const response = await fetch("/api/retrieval/note", {
        method: "POST",
        headers: searchHeaders,
        body: JSON.stringify({ id: noteId }),
      });
      const payload = await readSearchPayload(response);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setSearchError(typeof message === "string" ? message : "Не удалось открыть заметку.");
        return;
      }
      renderRetrievedNote(payload && payload.note);
      searchStatus.textContent = "Показана текущая версия заметки.";
    } catch (_error) {
      setSearchError("Сервис retrieval недоступен.");
    } finally {
      setSearchBusy(false);
    }
  };

  const renderSearchHit = (hit) => {
    if (!hit || typeof hit !== "object" || typeof hit.id !== "string") {
      throw new Error("invalid search hit");
    }
    const article = document.createElement("article");
    article.className = "search-hit";
    const heading = document.createElement("h3");
    heading.className = "search-hit-title";
    heading.textContent = searchTextValue(hit.title, "Без названия");
    article.append(heading);
    const fields = document.createElement("dl");
    fields.className = "search-fields";
    addSearchField(fields, "ID", hit.id);
    addSearchField(fields, "Тип", hit.type);
    addSearchField(fields, "Путь", hit.relative_path);
    addSearchField(fields, "Фрагмент", hit.snippet);
    article.append(fields);
    appendSearchTags(article, hit.tags);
    const actions = document.createElement("div");
    actions.className = "search-hit-actions";
    const openButton = document.createElement("button");
    openButton.className = "review-button review-button-secondary";
    openButton.type = "button";
    openButton.textContent = "Открыть";
    openButton.addEventListener("click", () => openNote(hit.id));
    actions.append(openButton);
    article.append(actions);
    return article;
  };

  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (searchBusy) {
      return;
    }
    const query = searchInput.value;
    if (!query.trim()) {
      setSearchError("Введи поисковый запрос.");
      return;
    }
    clearSearchFeedback();
    searchResults.replaceChildren();
    searchEmpty.hidden = true;
    retrievedNote.replaceChildren();
    retrievedNote.hidden = true;
    setSearchBusy(true);
    try {
      const response = await fetch("/api/search", {
        method: "POST",
        headers: searchHeaders,
        body: JSON.stringify({ query, limit: 20 }),
      });
      const payload = await readSearchPayload(response);
      if (!response.ok) {
        const message = payload && payload.error && payload.error.message;
        setSearchError(typeof message === "string" ? message : "Не удалось выполнить поиск.");
        return;
      }
      if (!payload || !Array.isArray(payload.hits)) {
        throw new Error("invalid search response");
      }
      payload.hits.forEach((hit) => searchResults.append(renderSearchHit(hit)));
      searchEmpty.hidden = payload.hits.length !== 0;
      searchStatus.textContent = payload.hits.length
        ? `Найдено результатов: ${payload.hits.length}`
        : "Поиск завершён";
    } catch (_error) {
      setSearchError("Сервис поиска недоступен.");
    } finally {
      setSearchBusy(false);
    }
  });
}
