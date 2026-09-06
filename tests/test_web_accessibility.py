"""Static accessibility contracts for the local Web GUI."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"


class MarkupContractParser(HTMLParser):
    """Collect enough deterministic DOM shape to test labels and landmarks."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.ids: set[str] = set()
        self.label_depth = 0
        self.controls_inside_labels: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.elements.append((tag, attributes))
        element_index = len(self.elements) - 1
        if self.label_depth and tag in {"input", "select", "textarea"}:
            self.controls_inside_labels.add(element_index)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if tag == "label":
            self.label_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self.label_depth -= 1


def _read(name: str) -> str:
    """Read one packaged static asset as UTF-8."""

    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _assert_revealed_before_focus(script: str, target: str) -> None:
    """Keep focus changes observable when the target is outside the viewport."""

    reveal_index = script.index(f"{target}.scrollIntoView")
    focus_index = script.index(f"{target}.focus()")
    assert reveal_index < focus_index


def test_static_dom_has_landmarks_headings_and_named_controls() -> None:
    """Keep the initial document navigable by landmarks and headings."""

    html = _read("index.html")
    parser = MarkupContractParser()
    parser.feed(html)

    assert html.count("<main") == 1
    assert html.count("<h1") == 1
    assert 'href="#main-content"' in html
    assert '<main id="main-content">' in html

    labels = {attrs["for"] for tag, attrs in parser.elements if tag == "label" and attrs.get("for")}
    for index, (tag, attrs) in enumerate(parser.elements):
        if tag not in {"input", "select", "textarea"}:
            continue
        assert attrs.get("type") != "hidden"
        assert index in parser.controls_inside_labels or (
            attrs.get("id") and attrs["id"] in labels
        ), f"unlabeled static control: {tag} {attrs}"

    for tag, attrs in parser.elements:
        if tag != "button":
            continue
        assert attrs.get("type") in {"button", "submit", "reset"}


def test_static_aria_relationships_and_live_regions_have_targets() -> None:
    """Keep described/controlled sections connected to stable IDs."""

    html = _read("index.html")
    parser = MarkupContractParser()
    parser.feed(html)

    for _tag, attrs in parser.elements:
        for attribute in ("aria-controls", "aria-describedby", "aria-labelledby"):
            value = attrs.get(attribute, "")
            for target in value.split():
                if target == "retrieved-note-title":
                    # The current note heading is created with this ID after retrieval.
                    assert 'heading.id = "retrieved-note-title"' in _read("app.js")
                else:
                    assert target in parser.ids, f"missing {attribute} target: {target}"
        if attrs.get("role") in {"status", "alert"}:
            assert attrs.get("aria-live") or attrs.get("role") == "alert"
        if attrs.get("role") == "alert":
            assert attrs.get("tabindex") == "-1"

    assert 'aria-controls="decision-form"' in html
    assert 'aria-controls="outcome-form"' in html
    assert 'aria-describedby="add-description"' in html
    assert 'aria-describedby="voice-description"' in html
    assert 'id="voice-description"' in html


def test_hidden_states_focus_contract_and_reduced_motion_are_explicit() -> None:
    """Keep hidden content out of the tree and preserve visible keyboard focus."""

    css = _read("app.css")
    assert ".is-hidden {\n  display: none !important;\n}" in css
    assert ".journal-form[hidden] {\n  display: none !important;\n}" in css
    assert ".capture-input:focus-visible," in css
    assert ".review-input:focus-visible" in css
    assert (
        ".voice-file-label:focus-within {\n"
        "  outline: var(--sb-focus-width) solid var(--sb-color-accent);"
    ) in css
    assert (
        ":focus-visible {\n  outline: var(--sb-focus-width) solid var(--sb-color-accent);"
    ) in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation-duration: 0.01ms !important;" in css
    assert "transition-duration: 0.01ms !important;" in css

    app_js = _read("app.js")
    journal_js = _read("decision-journal.js")
    timeline_js = _read("timeline.js")
    self_model_js = _read("self-model.js")
    simulate_me_js = _read("simulate-me.js")

    for script in (app_js, journal_js, timeline_js, self_model_js, simulate_me_js):
        assert 'setAttribute("aria-busy", String(' in script

    assert 'personalMemoryToggle.setAttribute("aria-expanded", "false")' in app_js
    assert 'personalMemoryToggle.setAttribute("aria-expanded", String(enabled))' in app_js
    _assert_revealed_before_focus(app_js, "error")
    _assert_revealed_before_focus(app_js, "result")
    _assert_revealed_before_focus(app_js, "searchError")
    _assert_revealed_before_focus(app_js, "retrievedNote")
    assert "addButton.focus()" in app_js
    assert "saved.focus" not in app_js
    assert 'preview.scrollIntoView({ block: "nearest" })' in app_js
    assert 'plan.scrollIntoView({ block: "nearest" })' in app_js
    assert "confirmButton.focus(" in app_js
    assert 'retrievedNote.scrollIntoView({ block: "start" })' in app_js
    _assert_revealed_before_focus(journal_js, "target")

    assert "decisionConfirm.focus(" in journal_js
    assert "outcomeConfirm.focus(" in journal_js
    assert 'plan.scrollIntoView({ block: "nearest" })' in journal_js
    _assert_revealed_before_focus(timeline_js, "error")
    _assert_revealed_before_focus(self_model_js, "error")
    _assert_revealed_before_focus(simulate_me_js, "error")

    assert "const loadTimeline = async (userInitiated = false)" in timeline_js
    assert "if (userInitiated)" in timeline_js
    assert 'refreshButton.addEventListener("click", () => loadTimeline(true))' in timeline_js
    assert 'orderSelect.addEventListener("change", () => loadTimeline(true))' in timeline_js
    assert "loadTimeline();" in timeline_js


def test_packaged_ui_stays_local_storage_free_and_keeps_safe_rendering() -> None:
    """Accessibility changes must not add persistence, polling, or unsafe rendering."""

    scripts = "\n".join(path.read_text(encoding="utf-8") for path in STATIC_DIR.glob("*.js"))
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "serviceWorker",
        "setInterval",
    ):
        assert forbidden not in scripts
    assert _read("app.js").count("innerHTML") == 1
