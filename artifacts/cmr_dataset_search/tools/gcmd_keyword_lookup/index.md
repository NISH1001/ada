---
name: gcmd_keyword_lookup
description: Look up GCMD controlled vocabulary terms to normalize and expand search terms
affordances: []
parameters:
  - name: concept_scheme
    required: true
    type: string
    description: "One of: sciencekeywords, instruments, platforms"
  - name: query_term
    required: true
    type: string
    description: Free-text term to look up or normalize against GCMD vocabulary
  - name: format
    type: string
    default: json
    description: "Response format: json (default), xml, csv"
---

# GCMD Keyword Lookup

Retrieves controlled vocabulary terms from the local GCMD vocabulary file (gcmd.json) to normalize free-text variable names into GCMD-standard Science Keywords, and to expand synonyms before CMR search. Can also look up valid instrument and platform short names.

**Important:** Read from gcmd.json (bundled in contexts/gcmd.json). Do NOT call the live KMS API — the local file is the authoritative source.

## Concept schemes in gcmd.json
- **Science Keywords:** Category > Topic > Term > Variable_Level_1 > Variable_Level_2 > Variable_Level_3 > Detailed_Variable
- **Instruments:** Short_Name, Long_Name, URIs
- **Platforms:** Short_Name, Long_Name, URIs

## Fields to extract

For Science Keywords:
- Category, Topic, Term
- Variable_Level_1, Variable_Level_2, Variable_Level_3
- Detailed_Variable (if present)

For Instruments/Platforms:
- Short_Name, Long_Name, Associated URIs

## Output format
```
GCMD match: {Category} > {Topic} > {Term} > {Variable_Level_1}
Synonyms at same level: {list of sibling terms}
```

If no match found: return the original term with a note that it was not found in GCMD.

## Matching algorithm
Use case-insensitive **substring** matching, not exact match. For example, "sea surface salinity" should match "OCEAN SURFACE SALINITY" if the query words appear in the field values. Check all hierarchy levels (Category through Detailed_Variable) for matches. Return the most specific match (deepest level).

## Known issues
- Hierarchy depth is inconsistent — Variable_Level_3 and Detailed_Variable often absent
- May not include the newest missions (file refresh cadence TBD)

## Fallback
If a term is not found in gcmd.json, use the original free-text term for CMR search. Do not call the live KMS API as a fallback.

## Response patterns
See `responses/` directory for handling of success and term-not-found scenarios.

## When to use
- Always first, before any CMR search, for every science variable
- To normalize free-text terms into GCMD standard vocabulary
- To expand synonyms for broader CMR query coverage
