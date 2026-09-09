"""Локальный production React GUI с синтетическими DI-ответами, без записи и сети.

Запуск из корня: uv run python -m web.qa.serve
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import uvicorn
from fastapi.responses import JSONResponse

from second_brain.application.llm import NoteDraft
from second_brain.application.ports import RetrievedNote, SearchHit
from second_brain.application.research import SourceProvenance
from second_brain.application.research_draft import ResearchDraftResult
from second_brain.domain.models import NoteType
from second_brain.entrypoints.web.app import create_app
from second_brain.entrypoints.web.auth import load_web_auth_config
from tests.test_web_review_save import (
    RecordingSaveService,
    make_created_result,
    make_dry_run_result,
    make_source,
)
from tests.test_web_transcription import RecordingTranscriptionService

NOTE_ID = UUID("0198f4c5-6a00-7000-8000-000000000010")
STAMP = datetime(2026, 9, 9, tzinfo=UTC)
TITLE = "Как идеи становятся знанием"
BODY = (
    f"# {TITLE}\n\nМысль становится полезнее, когда мы возвращаемся к ней.\n\n"
    "## Небольшая практика\n\n- Сохранить наблюдение.\n- Проверить формулировку.\n"
    "- Найти связь с уже известным.\n\nЭто синтетический материал для проверки интерфейса."
)


class Draft:
    def draft_text(self, text):
        return NoteDraft(TITLE, NoteType.RESOURCE, BODY, ("идеи", "практика"), ())

    def draft_url(self, url):
        source = replace(make_source(), title="Синтетическая статья", author="Автор примера")
        return ResearchDraftResult(
            draft=self.draft_text(""), source=SourceProvenance.from_research_source(source)
        )


class Save(RecordingSaveService):
    def prepare_text(self, draft):
        result = make_dry_run_result()
        return replace(
            result,
            plan=replace(
                result.plan,
                title=draft.title,
                relative_path=f"30 Resources/{draft.title}.md",
                content=f"---\nid: {NOTE_ID}\ntype: resource\n---\n{draft.content}",
            ),
        )

    def apply_text(self, draft):
        result = make_created_result()
        return replace(result, plan=replace(self.prepare_text(draft).plan, created=STAMP))

    def prepare_research(self, draft, source):
        return self.prepare_text(draft)

    def apply_research(self, draft, source):
        return self.apply_text(draft)


class Search:
    def search(self, request):
        if request.query == "пусто":
            return ()
        return (
            SearchHit(
                NOTE_ID,
                NoteType.RESOURCE,
                "30 Resources/Идеи.md",
                TITLE,
                ("идеи", "практика"),
                STAMP,
                None,
                "Мысль становится полезнее, когда мы возвращаемся к ней.",
            ),
        )

    def retrieve(self, note_id):
        return RetrievedNote(
            note_id,
            NoteType.RESOURCE,
            "30 Resources/Идеи.md",
            TITLE,
            BODY,
            ("идеи", "практика"),
            STAMP,
            None,
        )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="second-brain-design-qa-") as temporary:
        app = create_app(
            draft_service=Draft(),
            save_service=Save(),
            search_service=Search(),
            transcription_service=RecordingTranscriptionService(),
            web_auth_config=load_web_auth_config(),
            env_file=Path(temporary) / "absent.env",
            vault_path_override=str(Path(temporary) / "absent-vault"),
        )
        allowed = {
            "/api/drafts/text",
            "/api/drafts/url",
            "/api/drafts/preview",
            "/api/drafts/save/prepare",
            "/api/drafts/save/apply",
            "/api/transcriptions/audio",
            "/api/search",
            "/api/retrieval/note",
        }

        @app.middleware("http")
        async def synthetic_only(request, call_next):
            if request.url.path.startswith("/api/") and request.url.path not in allowed:
                return JSONResponse(
                    {"error": {"message": "Возможность недоступна в синтетическом окружении."}},
                    status_code=503,
                )
            return await call_next(request)

        uvicorn.run(app, host="127.0.0.1", port=8137, access_log=False)
