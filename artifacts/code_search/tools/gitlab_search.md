# GitLab Search Tool

Search public GitLab repositories using the GitLab REST API.

## Endpoint
`GET https://gitlab.com/api/v4/projects`

## Parameters
- **search** (required) — Search query string
- **order_by** — Sort by: `id`, `name`, `created_at`, `updated_at`, `last_activity_at`, `similarity`. Default: `created_at`
- **sort** — `desc` or `asc`. Default: `desc`
- **per_page** — Results per page (max 20 for this agent)

## When to use
- User explicitly asks to search GitLab
- User wants to find self-hosted or enterprise-friendly alternatives
- User is looking for projects that may not be on GitHub
