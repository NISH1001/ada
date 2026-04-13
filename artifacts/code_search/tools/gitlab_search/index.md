---
name: search_gitlab
description: Search public GitLab repositories
affordances: [http]
parameters:
  - name: search
    required: true
    type: string
    description: Search query string
  - name: order_by
    type: string
    default: last_activity_at
    description: "Sort by: name, created_at, updated_at, last_activity_at, similarity"
  - name: per_page
    type: integer
    default: 5
    description: Results per page (max 20)
---

# GitLab Search Tool

Search public GitLab repositories using the GitLab REST API.

## Endpoint
`GET https://gitlab.com/api/v4/projects`

## Query parameters sent to API
- `search` (required) — the `search` parameter
- `order_by` — the `order_by` parameter
- `sort` — always `desc`
- `per_page` — the `per_page` parameter, capped at 20
- `visibility` — always `public`

## Response
JSON is a bare array (not wrapped). Each item has:
- `path_with_namespace` (string)
- `web_url` (string)
- `star_count` (integer)
- `last_activity_at` (string, ISO date)
- `forks_count` (integer)
- `description` (string, may be null)

## Output format
Return a string formatted as:
```
Found {N} results:

- {path_with_namespace} ({web_url})
  stars: {star_count} | updated: {last_activity_at[:10]} | forks: {forks_count}
  {description}
```

If any field is null, display `n/a`.

## When to use
- User explicitly asks to search GitLab
- User wants to find self-hosted or enterprise-friendly alternatives
- User is looking for projects that may not be on GitHub
