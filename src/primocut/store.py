"""Storage/cache abstraction for the pipeline artifacts.

`FileStore` reproduces the Phase 0 JSON cache (used by the CLI). `DbStore`
persists to SQLite (used by the worker) — it is the database-backed
"pay-once" cache: re-enqueueing skips stages whose artifact already exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .config import Settings
from .models import ChatSignal, Topic, Transcript, Video


class Store(Protocol):
    def get_transcript(self, video_id: str) -> Transcript | None: ...
    def save_transcript(self, transcript: Transcript) -> None: ...
    def get_chat_signal(self, video_id: str) -> ChatSignal | None: ...
    def save_chat_signal(self, signal: ChatSignal) -> None: ...
    def get_topics(self, video_id: str) -> list[Topic] | None: ...
    def save_topics(self, video_id: str, topics: list[Topic]) -> None: ...
    def save_video_meta(self, video: Video) -> None: ...


class FileStore:
    """On-disk cache (JSON) — identical behaviour to Phase 0.

    `base_dir` overrides where the JSON artifacts are written; when omitted it
    falls back to `settings.data_dir`. The CLI passes the run's `--output`
    directory so nothing lands in `data/`.
    """

    def __init__(self, settings: Settings, base_dir: Path | str | None = None) -> None:
        self.dir = Path(base_dir) if base_dir is not None else Path(settings.data_dir)

    def _p(self, video_id: str, name: str) -> Path:
        return self.dir / f"{video_id}.{name}.json"

    def _write(self, path: Path, text: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def get_transcript(self, video_id: str) -> Transcript | None:
        p = self._p(video_id, "transcript")
        return Transcript.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None

    def save_transcript(self, transcript: Transcript) -> None:
        self._write(self._p(transcript.video_id, "transcript"), transcript.model_dump_json())

    def get_topics(self, video_id: str) -> list[Topic] | None:
        p = self._p(video_id, "topics")
        return [Topic(**d) for d in json.loads(p.read_text(encoding="utf-8"))] if p.exists() else None

    def save_topics(self, video_id: str, topics: list[Topic]) -> None:
        self._write(self._p(video_id, "topics"),
                    json.dumps([t.model_dump() for t in topics], ensure_ascii=False))

    # Phase 0 cached neither chat nor video metadata → no-ops.
    def get_chat_signal(self, video_id: str) -> ChatSignal | None:
        return None

    def save_chat_signal(self, signal: ChatSignal) -> None:
        pass

    def save_video_meta(self, video: Video) -> None:
        pass


class DbStore:
    """Cache/persistence in SQLite (worker)."""

    def __init__(self, database) -> None:
        self.db = database

    def get_transcript(self, video_id: str) -> Transcript | None:
        from .db import repo
        with self.db.session() as s:
            return repo.get_transcript(s, video_id)

    def save_transcript(self, transcript: Transcript) -> None:
        from .db import repo
        with self.db.session() as s:
            repo.save_transcript(s, transcript)

    def get_chat_signal(self, video_id: str) -> ChatSignal | None:
        from .db import repo
        with self.db.session() as s:
            return repo.get_chat_signal(s, video_id)

    def save_chat_signal(self, signal: ChatSignal) -> None:
        from .db import repo
        with self.db.session() as s:
            repo.save_chat_signal(s, signal)

    def get_topics(self, video_id: str) -> list[Topic] | None:
        from .db import repo
        with self.db.session() as s:
            return repo.get_topics(s, video_id) if repo.get_topic_rows(s, video_id) else None

    def save_topics(self, video_id: str, topics: list[Topic]) -> None:
        from .db import repo
        with self.db.session() as s:
            repo.save_topics(s, video_id, topics)

    def save_video_meta(self, video: Video) -> None:
        from .db import repo
        with self.db.session() as s:
            repo.update_video_meta(s, video)
