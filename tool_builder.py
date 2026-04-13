"""
Codegen pipeline: artifact spec -> LLM-generated tool code -> Monty-validated -> cached.

Flow per tool:
  1. Parse YAML frontmatter + markdown body from tools/<name>/index.md
  2. If cached generated code matches spec hash, use it
  3. Else: codegen agent generates code, validates via Monty in a react loop
  4. Cache to _generated/<name>.py with content hash
  5. exec() the code and return the callable (with real httpx http_request injected)
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pydantic_monty
import yaml
from dotenv import load_dotenv
from loguru import logger
from pydantic_ai import Agent, RunContext

load_dotenv()


# ── Spec parsing ────────────────────────────────────────────────────


@dataclass
class ParamSpec:
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: list[ParamSpec] = field(default_factory=list)
    affordances: list[str] = field(default_factory=list)
    body: str = ""  # markdown below frontmatter
    extra_docs: dict[str, str] = field(default_factory=dict)
    tool_dir: Path | None = None


def parse_tool_spec(tool_dir: Path) -> ToolSpec | None:
    """Parse tools/<name>/index.md with YAML frontmatter into a ToolSpec."""
    index_file = tool_dir / "index.md"
    if not index_file.exists():
        return None

    content = index_file.read_text()
    parts = content.split("---", 2)
    if len(parts) < 3:
        logger.warning("[ToolBuild] no YAML frontmatter in {}", index_file)
        return None

    raw = yaml.safe_load(parts[1])
    if not raw or "name" not in raw:
        return None

    params = [ParamSpec(**p) for p in raw.get("parameters", [])]

    extra_docs = {}
    for md in sorted(tool_dir.glob("*.md")):
        if md.name != "index.md":
            extra_docs[md.name] = md.read_text()

    return ToolSpec(
        name=raw["name"],
        description=raw.get("description", ""),
        parameters=params,
        affordances=raw.get("affordances", []) or [],
        body=parts[2].strip(),
        extra_docs=extra_docs,
        tool_dir=tool_dir,
    )


# ── Affordance registry ────────────────────────────────────────────
#
# An "affordance" is what the generated tool code is allowed to do at the
# I/O boundary — make HTTP calls, read files, get the current time, etc.
#
# Each affordance bundles:
#   - stubs:    type stubs Monty uses to type-check the generated code
#   - mock_fn:  async fn injected during validation (returns mock data)
#   - runtime_fn: async fn injected at runtime (real implementation)
#
# To add a new affordance (e.g. file_read, time, etc.), add a single entry below.
# Tool specs reference affordances by name in their YAML frontmatter:
#   affordances: [http]
#
# Note: this is unrelated to pydantic-ai's `capabilities` (which are agent-level
# bundles like Thinking/WebSearch). Affordances live entirely inside the
# build pipeline and never reach the main agent.


@dataclass
class Affordance:
    """Bundles type stubs, mock impl, and runtime impl for one external function."""

    name: str  # the function name the generated code calls (e.g. "http_request")
    stubs: str  # type stub string for Monty type-check
    mock_fn: Any  # async fn used during validation
    runtime_fn: Any  # async fn used in production


def _mock_http_response() -> str:
    """Generic mock JSON response. Enough to let parsing/formatting code execute."""
    return json.dumps(
        {
            "items": [
                {
                    "full_name": "example/repo",
                    "html_url": "https://example.com/repo",
                    "stargazers_count": 100,
                    "language": "Python",
                    "license": {"spdx_id": "MIT"},
                    "description": "A sample repo",
                    "path_with_namespace": "example/repo",
                    "web_url": "https://example.com/repo",
                    "star_count": 100,
                    "last_activity_at": "2026-01-01T00:00:00Z",
                    "forks_count": 10,
                }
            ],
            "total_count": 1,
        }
    )


async def _mock_http_request(method, url, params, headers=None):
    return _mock_http_response()


async def _real_http_request(
    method: str, url: str, params: dict, headers: dict | None = None
) -> str:
    """Real HTTP implementation injected into generated code at runtime."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, params=params, headers=headers or {})
        resp.raise_for_status()
        return resp.text


AFFORDANCES: dict[str, Affordance] = {
    "http": Affordance(
        name="http_request",
        stubs="""\
from typing import Any

async def http_request(method: str, url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> str:
    raise NotImplementedError()
""",
        mock_fn=_mock_http_request,
        runtime_fn=_real_http_request,
    ),
    # Add new affordances here. Examples:
    #   "time": Affordance(name="now", stubs=..., mock_fn=..., runtime_fn=...),
    #   "file_read": Affordance(name="read_file", stubs=..., mock_fn=..., runtime_fn=...),
}


def _affordance_stubs(affordances: list[str]) -> str:
    """Concatenate type stubs for the requested affordances."""
    parts = []
    for aff_name in affordances:
        aff = AFFORDANCES.get(aff_name)
        if aff is None:
            logger.warning("[ToolBuild] unknown affordance: {}", aff_name)
            continue
        parts.append(aff.stubs)
    return "\n".join(parts)


def _affordance_externals(affordances: list[str]) -> dict[str, Any]:
    """Build the external_functions dict for Monty validation."""
    externals = {}
    for aff_name in affordances:
        aff = AFFORDANCES.get(aff_name)
        if aff is None:
            continue
        externals[aff.name] = aff.mock_fn
    return externals


def _affordance_runtime(affordances: list[str]) -> dict[str, Any]:
    """Build the namespace injections for runtime exec()."""
    runtime = {}
    for aff_name in affordances:
        aff = AFFORDANCES.get(aff_name)
        if aff is None:
            continue
        runtime[aff.name] = aff.runtime_fn
    return runtime


# ── Monty validation ────────────────────────────────────────────────


async def validate_with_monty(code: str, spec: ToolSpec) -> str | None:
    """Validate generated code in Monty sandbox. Returns None if valid, error str if not.

    Stubs and external functions are dynamically assembled from the spec's declared
    affordances. Pure-compute tools (no affordances) validate as plain Python.
    """
    # Build test inputs for required params
    test_args = {}
    for p in spec.parameters:
        if p.required:
            test_args[p.name] = "test-query"
        elif p.default is not None:
            test_args[p.name] = p.default

    args_str = ", ".join(f"{k}={v!r}" for k, v in test_args.items())
    wrapper = f"{code}\n\nawait {spec.name}({args_str})\n"

    stubs = _affordance_stubs(spec.affordances)
    externals = _affordance_externals(spec.affordances)

    try:
        m = pydantic_monty.Monty(
            wrapper,
            type_check=True,
            type_check_stubs=stubs if stubs else None,
        )
    except pydantic_monty.MontySyntaxError as e:
        return f"SyntaxError: {e}"
    except pydantic_monty.MontyTypingError as e:
        return f"TypeError: {e}"

    try:
        result = await m.run_async(external_functions=externals)
        logger.debug("[ToolBuild] Monty result preview: {}", repr(result)[:120])
        return None
    except pydantic_monty.MontyRuntimeError as e:
        return f"RuntimeError: {e}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


# ── Codegen agent ───────────────────────────────────────────────────

CODEGEN_SYSTEM_PROMPT = """\
You are a tool code generator. Given a tool design spec, generate a single async Python function that implements it.

Rules:
- Function must be async, with parameters matching the spec's `parameters`.
- Return type must be str.
- Format the output as a human-readable string, following the spec's "Output format" section.
- Handle null/missing fields gracefully — fall back to "n/a".
- You may `import json` for parsing. NO other imports allowed (no httpx, no os, no requests, etc).
- For external I/O (HTTP, file reads, time, etc.), use ONLY the external functions listed in the prompt's "Available affordances" section. DO NOT import or define them. If no affordances are listed, write pure Python — no I/O at all.
- Do NOT wrap code in markdown fences. Return raw Python code only.

After generating code, call `validate_code(code)` to check it. If the result is not "valid", read the error, fix the code, and validate again. Keep iterating until valid, then return the final code.
"""


@dataclass
class CodegenDeps:
    spec: ToolSpec


codegen_agent = Agent(
    "openai:gpt-5.4-nano",
    system_prompt=CODEGEN_SYSTEM_PROMPT,
    deps_type=CodegenDeps,
    output_type=str,
)


@codegen_agent.tool
async def validate_code(ctx: RunContext[CodegenDeps], code: str) -> str:
    """Validate generated code via Monty sandbox. Returns 'valid' or error details."""
    error = await validate_with_monty(code, ctx.deps.spec)
    if error is None:
        logger.debug("[ToolBuild] validate_code: valid")
        return "valid"
    logger.debug("[ToolBuild] validate_code: {}", error[:200])
    return f"invalid: {error}"


def _spec_prompt(spec: ToolSpec) -> str:
    """Build the user prompt describing the tool spec to the codegen agent."""
    lines = [f"# Tool: {spec.name}", f"Description: {spec.description}", "", "## Parameters"]
    for p in spec.parameters:
        req = " (required)" if p.required else ""
        default = f", default={p.default!r}" if p.default is not None else ""
        lines.append(f"- {p.name}: {p.type}{req}{default} — {p.description}")

    # Available affordances — what external functions the generated code can use
    lines.append("")
    lines.append("## Available affordances")
    if spec.affordances:
        for aff_name in spec.affordances:
            aff = AFFORDANCES.get(aff_name)
            if aff is None:
                lines.append(f"- (unknown affordance: {aff_name})")
                continue
            lines.append(f"### {aff_name}")
            lines.append("```python")
            lines.append(aff.stubs.strip())
            lines.append("```")
    else:
        lines.append("(none — this is a pure compute tool. No external functions, no I/O.)")

    lines.append("")
    lines.append("## Spec body")
    lines.append(spec.body)

    for doc_name, doc_content in spec.extra_docs.items():
        lines.append(f"\n## Extra context: {doc_name}")
        lines.append(doc_content)

    lines.append("")
    lines.append("Generate the async Python function. Validate it with validate_code before returning.")
    return "\n".join(lines)


# ── Caching ─────────────────────────────────────────────────────────


def _spec_hash(tool_dir: Path) -> str:
    content = ""
    for md in sorted(tool_dir.glob("*.md")):
        content += md.read_text()
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def _cache_file(tool_dir: Path, output_dir: Path | None = None) -> Path:
    """
    Cache file location for a tool's generated implementation.

    Default: lives inside the tool's own directory as _impl_gen.py
    (each tool is self-contained — copy the dir, copy the cache).

    If `output_dir` is passed: cache lives at output_dir/<tool_name>.py instead.
    Useful if you want all generated impls collected in one place.
    """
    if output_dir is not None:
        return output_dir / f"{tool_dir.name}.py"
    return tool_dir / "_impl_gen.py"


def _read_cached(path: Path, expected_hash: str) -> str | None:
    if not path.exists():
        return None
    text = path.read_text()
    first_line = text.split("\n", 1)[0]
    if f"spec_hash: {expected_hash}" in first_line:
        # strip header
        lines = text.split("\n")
        body = "\n".join(l for l in lines if not l.startswith("#"))
        return body.strip()
    return None


def _write_cache(path: Path, code: str, hash_val: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# auto-generated | spec_hash: {hash_val}\n"
        f"# do not edit — regenerate by changing the tool spec\n\n"
    )
    path.write_text(header + code + "\n")
    logger.info("[ToolBuild] cached to {}", path)


# ── Build pipeline ──────────────────────────────────────────────────

TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _make_callable(code: str, spec: ToolSpec):
    """exec the generated code, inject affordance runtime impls, synthesize signature for FastMCP."""
    namespace: dict = {"json": json}
    namespace.update(_affordance_runtime(spec.affordances))
    exec(code, namespace)
    fn = namespace[spec.name]

    # Wrap so that every call logs
    orig_fn = fn

    async def logged_fn(**kwargs):
        logger.info("[ToolCall] {}({})", spec.name, ", ".join(f"{k}={v!r}" for k, v in kwargs.items()))
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
                    p.name, inspect.Parameter.KEYWORD_ONLY, default=p.default, annotation=py_type
                )
            )
    logged_fn.__signature__ = inspect.Signature(params, return_annotation=str)
    logged_fn.__name__ = spec.name
    logged_fn.__doc__ = spec.description
    return logged_fn


async def build_tool(
    tool_dir: Path, output_dir: Path | None = None
) -> tuple[ToolSpec, Any] | None:
    """Build a single tool: parse spec, generate+validate code (or use cache), return (spec, callable).

    By default the generated impl is cached inside `tool_dir/_impl_gen.py`.
    Pass `output_dir` to put it in a central location instead.
    """
    spec = parse_tool_spec(tool_dir)
    if spec is None:
        return None

    hash_val = _spec_hash(tool_dir)
    cache_path = _cache_file(tool_dir, output_dir)

    cached_code = _read_cached(cache_path, hash_val)
    if cached_code is not None:
        logger.info("[ToolBuild] cache hit: {} (hash: {})", spec.name, hash_val)
        fn = _make_callable(cached_code, spec)
        return spec, fn

    logger.info("[ToolBuild] generating: {} (hash: {})", spec.name, hash_val)
    prompt = _spec_prompt(spec)

    try:
        result = await codegen_agent.run(prompt, deps=CodegenDeps(spec=spec))
    except Exception as e:
        logger.error("[ToolBuild] codegen failed for {}: {}", spec.name, e)
        return None

    code = result.output.strip()
    # Strip markdown fences if the agent added them
    if code.startswith("```"):
        lines = code.split("\n")
        # drop first line (```python) and last line (```)
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Final validation (codegen agent should have validated, but double-check)
    error = await validate_with_monty(code, spec)
    if error is not None:
        logger.error("[ToolBuild] final validation failed for {}: {}", spec.name, error[:300])
        return None

    logger.info("[ToolBuild] validated: {}", spec.name)
    _write_cache(cache_path, code, hash_val)
    fn = _make_callable(code, spec)
    return spec, fn


async def build_tools(
    artifacts_root: Path, output_dir: Path | None = None
) -> list[tuple[ToolSpec, Any]]:
    """Build all tools from artifacts_root/tools/*/index.md.

    By default each tool's generated impl is cached in its own directory
    (artifacts/.../tools/<name>/_impl_gen.py). Pass `output_dir` to instead
    collect all impls in one folder.
    """
    tools_dir = artifacts_root / "tools"
    if not tools_dir.exists():
        logger.info("[ToolBuild] no tools/ directory in {}", artifacts_root)
        return []

    results = []
    for child in sorted(tools_dir.iterdir()):
        if child.is_dir():
            built = await build_tool(child, output_dir)
            if built is not None:
                results.append(built)

    logger.info("[ToolBuild] built {} tool(s) from {}", len(results), tools_dir)
    return results
