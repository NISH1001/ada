"""Core data models for tool specs and parameters."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field


class ParamSpec(BaseModel):
    """A single parameter in a tool spec."""

    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""


class ToolSpec(BaseModel):
    """A tool specification parsed from a markdown artifact.

    Can be structured (YAML frontmatter with explicit params) or unstructured
    (prose only — codegen agent infers the function signature).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str = ""
    parameters: list[ParamSpec] = Field(default_factory=list)
    affordances: list[str] = Field(default_factory=list)
    body: str = ""
    extra_docs: dict[str, str] = Field(default_factory=dict)
    tool_dir: Path | None = None

    @property
    def is_structured(self) -> bool:
        """True if the spec has explicit parameters from frontmatter."""
        return len(self.parameters) > 0

    def spec_hash(self) -> str:
        """SHA-256 hash of all .md files in the tool directory."""
        if self.tool_dir is None:
            return hashlib.sha256(self.body.encode()).hexdigest()[:12]
        content = ""
        for md in sorted(self.tool_dir.glob("*.md")):
            content += md.read_text()
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    @classmethod
    def from_tool_dir(cls, tool_dir: Path) -> ToolSpec | None:
        """Parse tools/<name>/index.md into a ToolSpec.

        Handles both structured (YAML frontmatter) and unstructured (prose only) specs.
        If no frontmatter is found, returns a spec with name from dir and body from content.
        """
        index_file = tool_dir / "index.md"
        if not index_file.exists():
            return None

        content = index_file.read_text()

        # Collect extra docs from sibling .md files
        extra_docs = {}
        for md in sorted(tool_dir.glob("*.md")):
            if md.name != "index.md":
                extra_docs[md.name] = md.read_text()

        # Try to parse YAML frontmatter
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw = yaml.safe_load(parts[1])
            if raw and isinstance(raw, dict) and "name" in raw:
                params = [ParamSpec(**p) for p in raw.get("parameters", [])]
                return cls(
                    name=raw["name"],
                    description=raw.get("description", ""),
                    parameters=params,
                    affordances=raw.get("affordances", []) or [],
                    body=parts[2].strip(),
                    extra_docs=extra_docs,
                    tool_dir=tool_dir,
                )

        # Unstructured: no valid frontmatter — codegen infers everything
        logger.info("[ToolBuild] no frontmatter in {}, codegen will infer", index_file)
        return cls(
            name=tool_dir.name,
            description="",
            parameters=[],
            affordances=[],
            body=content.strip(),
            extra_docs=extra_docs,
            tool_dir=tool_dir,
        )
