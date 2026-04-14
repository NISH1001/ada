"""Artifact store — loads and indexes markdown artifacts from a directory tree."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict, model_validator


class ArtifactStore(BaseModel):
    """Loads all .md artifacts from a directory tree and provides key-based access."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    artifacts: dict[str, str] = {}

    @model_validator(mode="after")
    def _load_artifacts(self) -> "ArtifactStore":
        self.root = Path(self.root)
        for md in sorted(self.root.rglob("*.md")):
            key = str(md.relative_to(self.root))
            self.artifacts[key] = md.read_text()
            logger.debug("[Store] indexed: {}", key)
        logger.info(
            "[Store] ready — {} artifacts indexed from {}", len(self.artifacts), self.root
        )
        return self

    def get(self, key: str) -> str | None:
        return self.artifacts.get(key)

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self.artifacts if k.startswith(prefix)]

    def root_index(self) -> str:
        return self.artifacts.get("index.md", "No root index found.")
