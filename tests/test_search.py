"""Deterministic tests for Search/Retrieval v1."""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from second_brain.adapters.search import (
    MAX_SNIPPET_CHARS,
    SqliteFts5SearchIndex,
)
from second_brain.adapters.search.sqlite_fts5 import _bounded_plain_text
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.ports import (
    RetrievedNote,
    SearchDocument,
    SearchHit,
    SearchIdentityConflictError,
    SearchIndexFailedError,
    SearchInvalidRequestError,
    SearchNotFoundError,
    SearchRequest,
)
from second_brain.application.search import (
    RetrieveManagedNote,
    SearchVault,
    build_literal_match_expression,
    project_search_documents,
)
from second_brain.application.validation import build_report
from second_brain.config import ConfigurationError
from second_brain.domain.models import NoteType
from second_brain.entrypoints.cli.app import app
from second_brain.entrypoints.web.app import create_app
from tests.conftest import (
    SECOND_NOTE_ID,
    VALID_NOTE_ID,
    create_vault,
    managed_note,
    write_note,
)

VALID_UUID = UUID(VALID_NOTE_ID)
SECOND_UUID = UUID(SECOND_NOTE_ID)
CREATED = datetime(2026, 9, 2, 12, tzinfo=UTC)
cli_runner = CliRunner()


def _document(
    note_id: UUID,
    *,
    title: str,
    path: str,
    body: str,
    tags: tuple[str, ...] = (),
) -> SearchDocument:
    return SearchDocument(
        note_id=note_id,
        note_type=NoteType.RESOURCE,
        relative_path=path,
        title=title,
        body=body,
        tags=tags,
        created=CREATED,
    )


def test_projection_indexes_only_stable_managed_notes_and_keeps_exact_fields(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/FastAPI Testing.md",
        managed_note()
        .replace("tags: []", "tags: [python, qa]")
        .replace("Текст заметки.", "FastAPI тестирование и pytest."),
    )
    write_note(vault, "00 Inbox/raw.md", "FastAPI из unmanaged Inbox.")
    write_note(
        vault,
        "30 Resources/Metadata.md",
        managed_note(SECOND_NOTE_ID, "resource")
        .replace("tags: []", "tags: [knowledge]\nsecret: do-not-index")
        .replace("Текст заметки.", "Канонический body без секрета metadata."),
    )

    report = build_report(FileSystemVaultReader(vault).scan())
    documents = project_search_documents(report)

    assert [document.title for document in documents] == ["FastAPI Testing", "Metadata"]
    assert documents[0].relative_path == "10 Projects/FastAPI Testing.md"
    assert documents[0].tags == ("python", "qa")
    assert documents[0].body == "# Тестовая заметка\n\nFastAPI тестирование и pytest.\n"
    assert "do-not-index" not in documents[1].body

    index = SqliteFts5SearchIndex()
    try:
        index.rebuild(documents)
        fastapi_hits = index.search(SearchRequest("fastapi"))
        secret_hits = index.search(SearchRequest("do-not-index"))
    finally:
        index.close()
    assert [hit.note_id for hit in fastapi_hits] == [VALID_UUID]
    assert secret_hits == ()


def test_nfkc_index_normalization_is_symmetric_but_metadata_and_retrieval_stay_exact(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    fullwidth_path = write_note(
        vault,
        "30 Resources/ＦａｓｔＡＰＩ.md",
        managed_note()
        .replace("tags: []", "tags: [ｐｙｔｈｏｎ]")
        .replace("Текст заметки.", "Канонический ＦａｓｔＡＰＩ body. Русский поиск."),
    )
    write_note(
        vault,
        "30 Resources/FastAPI.md",
        managed_note(SECOND_NOTE_ID, "resource").replace(
            "Текст заметки.", "Обычный FastAPI body. Русский поиск."
        ),
    )
    reader = FileSystemVaultReader(vault)
    index = SqliteFts5SearchIndex()
    try:
        ascii_query_hits = SearchVault(reader, index).execute(SearchRequest("FastAPI"))
        fullwidth_query_hits = SearchVault(reader, index).execute(SearchRequest("ＦａｓｔＡＰＩ"))
        russian_hits = SearchVault(reader, index).execute(SearchRequest("Русский"))
    finally:
        index.close()

    ascii_ids = {hit.note_id for hit in ascii_query_hits}
    fullwidth_ids = {hit.note_id for hit in fullwidth_query_hits}
    assert ascii_ids == {VALID_UUID, SECOND_UUID}
    assert fullwidth_ids == {VALID_UUID, SECOND_UUID}
    assert {hit.note_id for hit in russian_hits} == {VALID_UUID, SECOND_UUID}

    fullwidth_hit = next(hit for hit in ascii_query_hits if hit.note_id == VALID_UUID)
    assert fullwidth_hit.title == "ＦａｓｔＡＰＩ"
    assert fullwidth_hit.relative_path == "30 Resources/ＦａｓｔＡＰＩ.md"
    assert fullwidth_hit.tags == ("ｐｙｔｈｏｎ",)
    ascii_hit = next(hit for hit in ascii_query_hits if hit.note_id == SECOND_UUID)
    assert ascii_hit.title == "FastAPI"
    assert ascii_hit.relative_path == "30 Resources/FastAPI.md"
    assert ascii_hit.tags == ()

    retrieved = RetrieveManagedNote(reader).execute(VALID_UUID)
    assert retrieved.body == (
        "# Тестовая заметка\n\nКанонический ＦａｓｔＡＰＩ body. Русский поиск.\n"
    )
    assert "ＦａｓｔＡＰＩ" in fullwidth_path.read_text(encoding="utf-8")


def test_search_hit_snippet_replaces_terminal_controls_after_html_cleanup() -> None:
    raw_snippet = (
        "Русский\n\t"
        "\x1b[31mANSI\x1b[0m "
        "\x1b]8;;https://evil.example\x07safe\x1b]8;;\x07 "
        "BEL\x85C1\u202eCf <mark>HTML</mark> " + ("long " * 100)
    )
    cleaned = _bounded_plain_text(raw_snippet)
    assert "Русский" in cleaned
    assert "ANSI" in cleaned
    assert "HTML" in cleaned
    assert "<" not in cleaned
    assert "\n" not in cleaned
    assert "\t" not in cleaned
    assert len(cleaned) <= MAX_SNIPPET_CHARS
    assert all(unicodedata.category(character) not in {"Cc", "Cf"} for character in cleaned)

    index = SqliteFts5SearchIndex()
    try:
        index.rebuild(
            (_document(VALID_UUID, title="Safe", path="10 Projects/Safe.md", body=raw_snippet),)
        )
        hit = index.search(SearchRequest("safe"))[0]
    finally:
        index.close()
    assert "safe" in hit.snippet
    assert all(unicodedata.category(character) not in {"Cc", "Cf"} for character in hit.snippet)


@pytest.mark.parametrize(
    "query",
    ["", "   ", "hello\x00world", "line\nfeed", "x" * 4097, "*"],
)
def test_query_policy_rejects_blank_controls_oversize_and_operator_only(query: str) -> None:
    with pytest.raises(SearchInvalidRequestError):
        build_literal_match_expression(query)

    index = SqliteFts5SearchIndex()
    try:
        index.rebuild(())
        with pytest.raises(SearchInvalidRequestError):
            index.search(SearchRequest(query))
    finally:
        index.close()


def test_raw_fts_operators_quotes_columns_and_sql_text_are_literal_terms() -> None:
    index = SqliteFts5SearchIndex()
    index.rebuild(
        (
            _document(
                VALID_UUID,
                title="OR",
                path="10 Projects/Operators.md",
                body="NEAR title literal text",
            ),
            _document(
                SECOND_UUID,
                title="Other",
                path="30 Resources/Other.md",
                body="ordinary text",
            ),
        )
    )
    try:
        assert build_literal_match_expression("OR") == '"OR"'
        assert index.search(SearchRequest("OR"))[0].note_id == VALID_UUID
        assert index.search(SearchRequest("title:ordinary")) == ()
        assert index.search(SearchRequest("' OR 1=1 --")) == ()
        assert index.search(SearchRequest('"NEAR"'))[0].note_id == VALID_UUID
    finally:
        index.close()


def test_limit_is_bounded_and_bool_is_not_an_integer() -> None:
    index = SqliteFts5SearchIndex()
    try:
        index.rebuild(())
        for request in (SearchRequest("x", 0), SearchRequest("x", 51), SearchRequest("x", True)):
            with pytest.raises(SearchInvalidRequestError):
                index.search(request)
    finally:
        index.close()


def test_title_ranks_above_body_and_snippet_is_bounded_plain_text() -> None:
    long_body = "python " + ("word " * 500) + "<mark>unsafe</mark>"
    index = SqliteFts5SearchIndex()
    index.rebuild(
        (
            _document(
                VALID_UUID,
                title="Python Guide",
                path="10 Projects/Guide.md",
                body="A short body.",
            ),
            _document(
                SECOND_UUID,
                title="Other Guide",
                path="30 Resources/Body.md",
                body=long_body,
            ),
        )
    )
    try:
        hits = index.search(SearchRequest("python"))
    finally:
        index.close()
    assert [hit.note_id for hit in hits] == [VALID_UUID, SECOND_UUID]
    assert len(hits[1].snippet) <= MAX_SNIPPET_CHARS
    assert "<mark>" not in hits[1].snippet
    assert "<" not in hits[1].snippet
    assert "<" not in hits[0].snippet


def test_tags_and_path_rank_above_body_and_ties_are_deterministic() -> None:
    tag_id = UUID("0198f4c5-6a00-7000-8000-000000000004")
    path_id = UUID("0198f4c5-6a00-7000-8000-000000000005")
    body_id = UUID("0198f4c5-6a00-7000-8000-000000000006")
    tie_b_id = UUID("0198f4c5-6a00-7000-8000-000000000007")
    tie_a_id = UUID("0198f4c5-6a00-7000-8000-000000000008")
    index = SqliteFts5SearchIndex()
    try:
        index.rebuild(
            (
                _document(
                    tag_id,
                    title="Tag match",
                    path="30 Resources/Tag.md",
                    body="ordinary text",
                    tags=("python",),
                ),
                _document(
                    path_id,
                    title="Path match",
                    path="10 Python/Path.md",
                    body="ordinary text",
                ),
                _document(
                    body_id,
                    title="Body match",
                    path="30 Resources/Body.md",
                    body="python",
                ),
                _document(
                    tie_b_id,
                    title="Zeta",
                    path="30 Resources/Zeta.md",
                    body="tie",
                ),
                _document(
                    tie_a_id,
                    title="Alpha",
                    path="30 Resources/Alpha.md",
                    body="tie",
                ),
            )
        )
        weighted_hits = index.search(SearchRequest("python"))
        tie_hits = index.search(SearchRequest("tie"))
    finally:
        index.close()

    assert [hit.note_id for hit in weighted_hits] == [tag_id, path_id, body_id]
    assert [hit.note_id for hit in tie_hits] == [tie_a_id, tie_b_id]


def test_duplicate_documents_fail_closed_without_replacing_existing_index() -> None:
    duplicate_id = UUID("0198f4c5-6a00-7000-8000-000000000009")
    index = SqliteFts5SearchIndex()
    try:
        index.rebuild(
            (_document(duplicate_id, title="Stable", path="10 Projects/Stable.md", body="stable"),)
        )
        with pytest.raises(SearchIdentityConflictError):
            index.rebuild(
                (
                    _document(
                        duplicate_id,
                        title="First",
                        path="10 Projects/First.md",
                        body="first",
                    ),
                    _document(
                        duplicate_id,
                        title="Second",
                        path="10 Projects/Second.md",
                        body="second",
                    ),
                )
            )
        hits = index.search(SearchRequest("stable"))
    finally:
        index.close()

    assert [hit.note_id for hit in hits] == [duplicate_id]


def test_rebuild_is_fresh_and_failed_rebuild_does_not_mix_state() -> None:
    index = SqliteFts5SearchIndex()
    try:
        index.rebuild((_document(VALID_UUID, title="Old", path="10 Projects/Old.md", body="old"),))
        assert index.search(SearchRequest("old"))[0].note_id == VALID_UUID
        index.rebuild(())
        assert index.search(SearchRequest("old")) == ()

        index.rebuild(
            (_document(VALID_UUID, title="Stable", path="10 Projects/Stable.md", body="stable"),)
        )
        with pytest.raises(SearchIndexFailedError):
            index.rebuild((object(),))  # type: ignore[arg-type]
        assert index.search(SearchRequest("stable"))[0].note_id == VALID_UUID
    finally:
        index.close()


def test_search_ignores_unrelated_diagnostics_and_retrieval_rereads_current_body(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    path = write_note(
        vault,
        "10 Projects/Current.md",
        managed_note() + "\n[[Missing note]]\n",
    )
    reader = FileSystemVaultReader(vault)
    index = SqliteFts5SearchIndex()
    try:
        hits = SearchVault(reader, index).execute(SearchRequest("Текст"))
    finally:
        index.close()
    assert hits[0].note_id == VALID_UUID

    path.write_text(
        managed_note().replace("Текст заметки.", "НОВОЕ canonical содержимое."), encoding="utf-8"
    )
    retrieved = RetrieveManagedNote(reader).execute(VALID_UUID)
    assert retrieved.body.endswith("НОВОЕ canonical содержимое.\n")
    assert retrieved.body != hits[0].snippet


def test_duplicate_searchable_uuid_fails_closed_for_search_and_retrieval(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/One.md", managed_note(VALID_NOTE_ID, "project"))
    write_note(vault, "10 Projects/Two.md", managed_note(VALID_NOTE_ID, "resource"))
    reader = FileSystemVaultReader(vault)
    index = SqliteFts5SearchIndex()
    try:
        with pytest.raises(SearchIdentityConflictError):
            SearchVault(reader, index).execute(SearchRequest("Текст"))
    finally:
        index.close()
    with pytest.raises(SearchIdentityConflictError):
        RetrieveManagedNote(reader).execute(VALID_UUID)


def test_retrieval_missing_uuid_is_safe_not_found(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/One.md", managed_note())

    with pytest.raises(SearchNotFoundError):
        RetrieveManagedNote(FileSystemVaultReader(vault)).execute(SECOND_UUID)


def test_cli_search_supports_text_json_russian_and_limit_without_writes(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "30 Resources/Russian Search.md",
        managed_note().replace("Текст заметки.", "Русский поиск находит точную лексическую форму."),
    )
    env_file = tmp_path / ".env"
    env_file.write_text(f"SECOND_BRAIN_VAULT_PATH={vault}\n", encoding="utf-8")

    text_result = cli_runner.invoke(
        app,
        ["--env-file", str(env_file), "search", "Русский", "--limit", "1", "--format", "text"],
    )
    json_result = cli_runner.invoke(
        app,
        ["--env-file", str(env_file), "search", "точную", "--format", "json"],
    )

    assert text_result.exit_code == 0
    assert "Russian Search" in text_result.stdout
    assert "30 Resources/Russian Search.md" in text_result.stdout
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert list(payload) == ["hits"]
    assert payload["hits"][0]["title"] == "Russian Search"
    assert "body" not in payload["hits"][0]


def test_web_actual_search_uses_current_vault_for_retrieval(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    path = write_note(vault, "10 Projects/Current.md", managed_note())
    headers = {
        "Content-Type": "application/json",
        "X-Second-Brain-Request": "search-v1",
    }
    with TestClient(
        create_app(vault_path_override=str(vault)), base_url="http://127.0.0.1"
    ) as client:
        search_response = client.post("/api/search", json={"query": "Текст"}, headers=headers)
        path.write_text(
            managed_note().replace("Текст заметки.", "Обновлённый current body."),
            encoding="utf-8",
        )
        retrieval_response = client.post(
            "/api/retrieval/note", json={"id": VALID_NOTE_ID}, headers=headers
        )

    assert search_response.status_code == 200
    assert search_response.json()["hits"][0]["id"] == VALID_NOTE_ID
    assert retrieval_response.status_code == 200
    assert retrieval_response.json()["note"]["content"].endswith("Обновлённый current body.\n")


class _FakeSearchService:
    def __init__(self, hit: SearchHit, note: RetrievedNote) -> None:
        self.hit = hit
        self.note = note
        self.search_requests: list[SearchRequest] = []
        self.retrieve_ids: list[UUID] = []

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        self.search_requests.append(request)
        return (self.hit,)

    def retrieve(self, note_id: UUID) -> RetrievedNote:
        self.retrieve_ids.append(note_id)
        return self.note


def _fake_search_service() -> _FakeSearchService:
    hit = SearchHit(
        note_id=VALID_UUID,
        note_type=NoteType.RESOURCE,
        relative_path="30 Resources/FastAPI.md",
        title="FastAPI",
        tags=("python", "qa"),
        created=CREATED,
        updated=None,
        snippet="Безопасный snippet",
    )
    note = RetrievedNote(
        note_id=VALID_UUID,
        note_type=hit.note_type,
        relative_path=hit.relative_path,
        title=hit.title,
        body="exact **current** body",
        tags=hit.tags,
        created=CREATED,
        updated=None,
    )
    return _FakeSearchService(hit, note)


def test_web_search_and_retrieval_are_scoped_strict_local_post_and_no_store() -> None:
    service = _fake_search_service()
    with TestClient(create_app(search_service=service), base_url="http://127.0.0.1") as client:
        headers = {
            "Content-Type": "application/json",
            "X-Second-Brain-Request": "search-v1",
            "Origin": "http://127.0.0.1",
        }
        response = client.post(
            "/api/search", json={"query": "FastAPI", "limit": 20}, headers=headers
        )
        opened = client.post(
            "/api/retrieval/note",
            json={"id": VALID_NOTE_ID},
            headers=headers,
        )
        missing_header = client.post(
            "/api/search",
            json={"query": "FastAPI", "limit": 20},
            headers={"Content-Type": "application/json"},
        )
        foreign_origin = client.post(
            "/api/search",
            json={"query": "FastAPI", "limit": 20},
            headers={**headers, "Origin": "https://evil.example"},
        )
        unknown_field = client.post(
            "/api/search",
            json={"query": "FastAPI", "limit": 20, "extra": True},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["hits"][0]["relative_path"] == "30 Resources/FastAPI.md"
    assert "body" not in response.json()["hits"][0]
    assert opened.status_code == 200
    assert opened.json()["note"]["content"] == "exact **current** body"
    assert service.search_requests == [SearchRequest("FastAPI", 20)]
    assert service.retrieve_ids == [VALID_UUID]
    assert missing_header.status_code == 400
    assert missing_header.json()["error"]["code"] == "SEARCH_INVALID_REQUEST"
    assert foreign_origin.status_code == 400
    assert foreign_origin.json()["error"]["code"] == "SEARCH_INVALID_REQUEST"
    assert unknown_field.status_code == 400


def test_web_search_is_lazy_until_request_and_raw_body_cap_is_before_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fail_load_config(**kwargs: object) -> object:
        calls.append(kwargs)
        raise ConfigurationError("vault path sentinel must not leak")

    monkeypatch.setattr("second_brain.entrypoints.web.search.load_config", fail_load_config)
    application = create_app()
    assert calls == []
    with TestClient(application, base_url="http://127.0.0.1") as client:
        headers = {
            "Content-Type": "application/json",
            "X-Second-Brain-Request": "search-v1",
        }
        root = client.get("/")
        too_large = client.post(
            "/api/search",
            content=json.dumps({"query": "x" * 100_000}).encode("utf-8"),
            headers=headers,
        )
        missing_vault = client.post(
            "/api/search",
            json={"query": "x"},
            headers=headers,
        )
    assert root.status_code == 200
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "SEARCH_CONTENT_TOO_LARGE"
    assert missing_vault.status_code == 503
    assert missing_vault.json()["error"]["code"] == "SEARCH_BACKEND_UNAVAILABLE"
    assert "sentinel" not in missing_vault.text
    assert calls
