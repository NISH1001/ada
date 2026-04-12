# GitHub Search Tool

Search public GitHub repositories using the GitHub REST API.

## Endpoint
`GET https://api.github.com/search/repositories`

## Parameters
- **q** (required) — Search query. Supports GitHub search qualifiers like `language:python`, `stars:>100`, `topic:machine-learning`
- **sort** — Sort by: `stars`, `forks`, `help-wanted-issues`, `updated`. Default: best match
- **order** — `desc` or `asc`. Default: `desc`
- **per_page** — Results per page (max 30 for this agent)

## When to use
- User asks to find repos on GitHub specifically
- User wants to search by stars, language, or topics
- Default platform when user doesn't specify
