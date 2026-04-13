---
name: search_github
description: Search public GitHub repositories
affordances: [http]
parameters:
  - name: query
    required: true
    type: string
    description: Search query (supports qualifiers like language:python, stars:>100, topic:machine-learning)
  - name: sort
    type: string
    default: stars
    description: "Sort by: stars, forks, help-wanted-issues, updated"
  - name: per_page
    type: integer
    default: 5
    description: Results per page (max 30)
---

# GitHub Search Tool

Search public GitHub repositories using the GitHub REST API.

## Endpoint
`GET https://api.github.com/search/repositories`

## Headers
- `Accept: application/vnd.github.v3+json`

## Query parameters sent to API
- `q` (required) — the `query` parameter
- `sort` — maps from `sort` parameter
- `order` — always `desc`
- `per_page` — the `per_page` parameter, capped at 30

## Response
JSON with `items` array (max `per_page` items) and `total_count` at top level.

Each item has:
- `full_name` (string)
- `html_url` (string)
- `stargazers_count` (integer)
- `language` (string, may be null)
- `license.spdx_id` (nested, may be null)
- `description` (string, may be null)

## Output format
Return a string formatted as:
```
Found {total_count} total. Top {N}:

- {full_name} ({html_url})
  stars: {stargazers_count} | lang: {language} | license: {license.spdx_id}
  {description}
```

If any field is null, display `n/a`.

## When to use
- User asks to find repos on GitHub specifically
- User wants to search by stars, language, or topics
- Default platform when user doesn't specify
