"""ToolBuilder — orchestrates spec → codegen → validate → cache → callable."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from ada.codegen import generate_code, runtime_for, validate_with_monty
from ada.models import ToolSpec

TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


class ToolBuilder:
    """Builds tool callables from artifact specs.

    For each tool directory: parse spec, generate code (or use cache),
    validate via Monty, and produce a callable with synthesized signature.
    """

    # ── Cache helpers ─────────────────────────────────────────────

    @staticmethod
    def _cache_file(tool_dir: Path, output_dir: Path | None = None) -> Path:
        if output_dir is not None:
            return output_dir / f"{tool_dir.name}.py"
        return tool_dir / "_impl_gen.py"

    @staticmethod
    def _read_cached(path: Path, expected_hash: str) -> str | None:
        if not path.exists():
            return None
        text = path.read_text()
        first_line = text.split("\n", 1)[0]
        if f"spec_hash: {expected_hash}" in first_line:
            lines = text.split("\n")
            body = "\n".join(l for l in lines if not l.startswith("#"))
            return body.strip()
        return None

    @staticmethod
    def _write_cache(path: Path, code: str, hash_val: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# auto-generated | spec_hash: {hash_val}\n"
            f"# do not edit — regenerate by changing the tool spec\n\n"
        )
        path.write_text(header + code + "\n")
        logger.info("[ToolBuild] cached to {}", path)

    # ── Make callable ─────────────────────────────────────────────

    @staticmethod
    def _make_callable(code: str, spec: ToolSpec) -> Callable:
        """exec() the generated code with runtime affordances injected, synthesize signature."""
        namespace: dict[str, Any] = {"json": json}
        namespace.update(runtime_for(spec.affordances))
        exec(code, namespace)
        fn = namespace[spec.name]

        # Wrap with logging
        orig_fn = fn

        async def logged_fn(**kwargs):
            logger.info(
                "[ToolCall] {}({})",
                spec.name,
                ", ".join(f"{k}={v!r}" for k, v in kwargs.items()),
            )
            return await orig_fn(**kwargs)

        # Synthesize signature for FastMCP schema
        params = []
        for p in spec.parameters:
            py_type = TYPE_MAP.get(p.type, str)
            if p.required:
                params.append(
                    inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY, annotation=py_type)
                )
            else:
                params.append(
                    inspect.Parameter(
                        p.name,
                        inspect.Parameter.KEYWORD_ONLY,
                        default=p.default,
                        annotation=py_type,
                    )
                )
        logged_fn.__signature__ = inspect.Signature(params, return_annotation=str)
        logged_fn.__name__ = spec.name
        logged_fn.__doc__ = spec.description
        return logged_fn

    # ── Build pipeline ────────────────────────────────────────────

    async def build(
        self, tool_dir: Path, output_dir: Path | None = None
    ) -> tuple[ToolSpec, Callable] | None:
        """Build a single tool from a tool directory."""
        spec = ToolSpec.from_tool_dir(tool_dir)
        if spec is None:
            return None

        hash_val = spec.spec_hash()
        cache_path = self._cache_file(tool_dir, output_dir)

        # Check cache
        cached_code = self._read_cached(cache_path, hash_val)
        if cached_code is not None:
            logger.info("[ToolBuild] cache hit: {} (hash: {})", spec.name, hash_val)
            fn = self._make_callable(cached_code, spec)
            return spec, fn

        # Generate
        logger.info("[ToolBuild] generating: {} (hash: {})", spec.name, hash_val)
        try:
            code = await generate_code(spec)
        except Exception as e:
            logger.error("[ToolBuild] codegen failed for {}: {}", spec.name, e)
            return None

        # Final validation
        error = await validate_with_monty(code, spec)
        if error is not None:
            logger.error(
                "[ToolBuild] final validation failed for {}: {}", spec.name, error[:300]
            )
            return None

        logger.info("[ToolBuild] validated: {}", spec.name)
        self._write_cache(cache_path, code, hash_val)
        fn = self._make_callable(code, spec)
        return spec, fn

    async def build_all(
        self, artifacts_root: Path, output_dir: Path | None = None
    ) -> list[tuple[ToolSpec, Callable]]:
        """Build all tools from artifacts_root/tools/*/index.md."""
        tools_dir = artifacts_root / "tools"
        if not tools_dir.exists():
            logger.info("[ToolBuild] no tools/ directory in {}", artifacts_root)
            return []

        results = []
        for child in sorted(tools_dir.iterdir()):
            if child.is_dir():
                built = await self.build(child, output_dir)
                if built is not None:
                    results.append(built)

        logger.info("[ToolBuild] built {} tool(s) from {}", len(results), tools_dir)
        return results
