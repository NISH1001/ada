"""Codegen agent + Monty validation + prompt building.

The codegen agent reads a tool spec (structured or prose-only), generates a
Python implementation, and validates it in the Monty sandbox. Monty is
validation-only — runtime uses real Python exec().

Affordance stubs/mocks/runtime impls are plain dicts passed to Monty's
external_functions and type_check_stubs. No custom registry class.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pydantic_monty
from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from ada.models import ToolSpec


# ── Affordance definitions (plain dicts) ──────────────────────────────
#
# Each affordance bundles three things:
#   - stubs: type stub string for Monty type-checking
#   - mock: async fn for Monty sandbox validation
#   - runtime: async fn injected into exec() at runtime
#
# To add a new affordance, add entries to all three dicts.


def _mock_http_response() -> str:
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
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, params=params, headers=headers or {})
        resp.raise_for_status()
        return resp.text


# Maps affordance name → type stub for Monty type-checking
MONTY_STUBS: dict[str, str] = {
    "http": (
        "from typing import Any\n\n"
        "async def http_request(method: str, url: str, params: dict[str, Any], "
        "headers: dict[str, str] | None = None) -> str:\n"
        "    raise NotImplementedError()\n"
    ),
}

# Maps affordance name → (function_name, mock_callable) for Monty validation
MONTY_MOCKS: dict[str, tuple[str, Callable]] = {
    "http": ("http_request", _mock_http_request),
}

# Maps affordance name → (function_name, real_callable) for runtime exec()
RUNTIME_IMPLS: dict[str, tuple[str, Callable]] = {
    "http": ("http_request", _real_http_request),
}


def stubs_for(affordances: list[str]) -> str:
    """Concatenate type stubs for the requested affordances."""
    parts = []
    for name in affordances:
        stub = MONTY_STUBS.get(name)
        if stub is None:
            logger.warning("[ToolBuild] unknown affordance: {}", name)
            continue
        parts.append(stub)
    return "\n".join(parts)


def mocks_for(affordances: list[str]) -> dict[str, Any]:
    """Build external_functions dict for Monty validation."""
    externals: dict[str, Any] = {}
    for name in affordances:
        entry = MONTY_MOCKS.get(name)
        if entry is None:
            continue
        fn_name, mock_fn = entry
        externals[fn_name] = mock_fn
    return externals


def runtime_for(affordances: list[str]) -> dict[str, Any]:
    """Build namespace injections for runtime exec()."""
    runtime: dict[str, Any] = {}
    for name in affordances:
        entry = RUNTIME_IMPLS.get(name)
        if entry is None:
            continue
        fn_name, real_fn = entry
        runtime[fn_name] = real_fn
    return runtime


# ── Monty validation ──────────────────────────────────────────────────


async def validate_with_monty(code: str, spec: ToolSpec) -> str | None:
    """Validate generated code in Monty sandbox. Returns None if valid, error string if not."""
    # Build test call with required params
    test_args = {}
    for p in spec.parameters:
        if p.required:
            test_args[p.name] = "test-query"
        elif p.default is not None:
            test_args[p.name] = p.default

    args_str = ", ".join(f"{k}={v!r}" for k, v in test_args.items())
    wrapper = f"{code}\n\nawait {spec.name}({args_str})\n"

    stubs = stubs_for(spec.affordances)
    externals = mocks_for(spec.affordances)

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


# ── Codegen agent ─────────────────────────────────────────────────────

CODEGEN_SYSTEM_PROMPT = """\
You are a tool code generator. Given a tool design spec, generate a single async Python function that implements it.

Rules:
- Function must be async and return str.
- Format the output as a human-readable string, following the spec's "Output format" section if present.
- Handle null/missing fields gracefully — fall back to "n/a".
- You may `import json` for parsing. NO other imports allowed (no httpx, no os, no requests, etc).
- For external I/O (HTTP, file reads, time, etc.), use ONLY the external functions listed in the prompt's "Available affordances" section. DO NOT import or define them. If no affordances are listed, write pure Python — no I/O at all.
- Do NOT wrap code in markdown fences. Return raw Python code only.

If the spec provides explicit parameters, use them exactly. If the spec is prose-only (no explicit parameters), infer the function name and parameters from the description.

After generating code, call `validate_code(code)` to check it. If the result is not "valid", read the error, fix the code, and validate again. Keep iterating until valid, then return the final code.
"""


class CodegenDeps(BaseModel):
    spec: ToolSpec


_codegen_agent: Agent[CodegenDeps, str] | None = None


def _get_codegen_agent() -> Agent[CodegenDeps, str]:
    """Lazy-init the codegen agent (defers OpenAI client creation until first use)."""
    global _codegen_agent
    if _codegen_agent is not None:
        return _codegen_agent

    agent = Agent(
        "openai:gpt-5.4-nano",
        system_prompt=CODEGEN_SYSTEM_PROMPT,
        deps_type=CodegenDeps,
        output_type=str,
    )

    @agent.tool
    async def validate_code(ctx: RunContext[CodegenDeps], code: str) -> str:
        """Validate generated code via Monty sandbox. Returns 'valid' or error details."""
        error = await validate_with_monty(code, ctx.deps.spec)
        if error is None:
            logger.debug("[ToolBuild] validate_code: valid")
            return "valid"
        logger.debug("[ToolBuild] validate_code: {}", error[:200])
        return f"invalid: {error}"

    _codegen_agent = agent
    return _codegen_agent


def build_prompt(spec: ToolSpec) -> str:
    """Build the user prompt for the codegen agent.

    Adapts based on whether the spec is structured (has explicit params)
    or unstructured (prose only — codegen infers the signature).
    """
    lines = [f"# Tool: {spec.name}"]

    if spec.description:
        lines.append(f"Description: {spec.description}")

    if spec.is_structured:
        lines.append("")
        lines.append("## Parameters")
        for p in spec.parameters:
            req = " (required)" if p.required else ""
            default = f", default={p.default!r}" if p.default is not None else ""
            lines.append(f"- {p.name}: {p.type}{req}{default} — {p.description}")
    else:
        lines.append("")
        lines.append("## Parameters")
        lines.append(
            "(not specified — infer the function name and parameters from the description below)"
        )

    # Available affordances
    lines.append("")
    lines.append("## Available affordances")
    if spec.affordances:
        for aff_name in spec.affordances:
            stub = MONTY_STUBS.get(aff_name)
            if stub is None:
                lines.append(f"- (unknown affordance: {aff_name})")
                continue
            lines.append(f"### {aff_name}")
            lines.append("```python")
            lines.append(stub.strip())
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
    lines.append(
        "Generate the async Python function. Validate it with validate_code before returning."
    )
    return "\n".join(lines)


async def generate_code(spec: ToolSpec) -> str:
    """Run the codegen agent to generate a tool implementation from a spec."""
    prompt = build_prompt(spec)
    agent = _get_codegen_agent()

    result = await agent.run(prompt, deps=CodegenDeps(spec=spec))
    code = result.output.strip()

    # Strip markdown fences if the agent added them
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return code
