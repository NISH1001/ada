# NASA SMD Scientific Tool Search Agent

An agent that helps researchers discover relevant scientific tools, libraries, and code repositories across NASA's five Science Mission Directorate (SMD) divisions:

1. **Astrophysics** — Space telescopes, cosmic surveys, spectral analysis
2. **Biological and Physical Sciences** — Microgravity research, space biology, materials science
3. **Earth Science** — Remote sensing, climate modeling, geospatial analysis
4. **Heliophysics** — Solar observation, space weather, magnetospheric modeling
5. **Planetary Science** — Planetary data systems, mission data processing, orbital mechanics

The agent searches across version control platforms (GitHub, GitLab, etc.) to find open-source scientific tools relevant to these divisions.

## Artifacts

- **contexts/** — Domain expertise and search strategy knowledge
- **guardrails/** — Search boundaries and result quality rules
- **tools/** — Available VCS platform search capabilities
