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

  const stopRecordingStream = () => {
    if (recordingStream && typeof recordingStream.getTracks === "function") {
      recordingStream.getTracks().forEach((track) => {
        if (track && typeof track.stop === "function") {
          track.stop();
        }
      });
    }
    recordingStream = null;
  };

  const clearAudioState = () => {
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

  const updateVoiceControls = () => {
    if (!voicePanel) {
      return;
    }
    recordButton.disabled = busy || recording || microphonePending || !canRecord;
    stopButton.disabled = busy || !recording;
    stopButton.hidden = !recording;
    audioFileInput.disabled = busy || recording;
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

  const createEditor = (draft) => {
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
      planHeading.textContent = "План Safe Write (dry-run)";
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
      setLoading(true);
      previewStatus.textContent = "Готовлю Safe Write dry-run…";
      try {
        const response = await fetch("/api/drafts/save/prepare", {
          method: "POST",
          headers: requestHeaders,
          body: JSON.stringify({ review_token: originalReviewToken, draft: editedDraft() }),
        });
        const payload = await response.json();
        if (!response.ok) {
          const message = payload && payload.error && payload.error.message;
          setError(typeof message === "string" ? message : "Не удалось подготовить сохранение.");
          return;
        }
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
      setLoading(true);
      status.textContent = "Сохраняю в vault…";
      try {
        const response = await fetch("/api/drafts/save/apply", {
          method: "POST",
          headers: requestHeaders,
          body: JSON.stringify({
            review_token: originalReviewToken,
            confirmation_token: preparedConfirmationToken,
            draft: editedDraft(),
          }),
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
        confirmButton.hidden = true;
        const saved = document.createElement("section");
        saved.className = "saved-note";
        const savedHeading = document.createElement("h4");
        savedHeading.textContent = "Сохранено";
        saved.append(savedHeading);
        const savedFields = document.createElement("dl");
        savedFields.className = "draft-fields";
        addField(savedFields, "Путь", payload.note.relative_path);
        addField(savedFields, "ID", payload.note.id);
        addField(savedFields, "Создано", payload.note.created);
        saved.append(savedFields);
        section.append(saved);
        status.textContent = "Заметка сохранена";
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
    result.append(heading, createEditor(draft));
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
    microphonePending = true;
    updateVoiceControls();
    setVoiceReadyStatus("Запрашиваю доступ к микрофону…");
    try {
      recordingStream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
      recorder.start();
      recording = true;
      setVoiceReadyStatus("Идёт запись. Нажми «Остановить», когда закончишь.");
    } catch (_error) {
      stopRecordingStream();
      recorder = null;
      recording = false;
      setVoiceReadyStatus("");
      setError("Не удалось получить доступ к микрофону.");
    } finally {
      microphonePending = false;
      updateVoiceControls();
    }
  });

  stopButton.addEventListener("click", () => {
    if (!busy && recording && recorder) {
      recorder.stop();
    }
  });

  audioFileInput.addEventListener("change", () => {
    if (busy || recording) {
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
