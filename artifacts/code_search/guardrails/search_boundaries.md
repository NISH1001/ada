# Search Boundaries

## Do
- Only search public repositories
- Prefer repos with OSI-approved licenses (MIT, Apache-2.0, BSD)
- Flag repos with no license — they are legally risky to use

## Don't
- Never access or attempt to search private repositories
- Don't recommend repos with known critical CVEs unless the user explicitly asks
- Don't fabricate repository URLs — only return results from actual API responses

## Rate Limits
- Be mindful of API rate limits; prefer fewer, well-crafted queries over many broad ones
- If a search returns 0 results, suggest query refinements rather than retrying the same query
