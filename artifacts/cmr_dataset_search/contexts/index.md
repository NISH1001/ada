# Contexts

Domain knowledge for CMR dataset search. Load artifacts from subdirectories as needed.

- **terminology/** — CMR and Earth science terminology (collections vs granules, processing levels, identifiers, instruments). **Important:** Load `terminology/gcmd_keyword_hierarchy.md` before constructing CMR search queries — it explains how to use GCMD-normalized terms as CMR keyword parameters.
- **heuristics/** — Search strategy heuristics (zero results, too many results, tiebreakers, composites, literature refinement)
- **common_mistakes/** — Known pitfalls (chlorophyll ocean vs terrestrial, deprecated missions, HLS vs Landsat, temporal gaps)
- **references/** — Reference materials (Worldview/GIBS pathfinder)
- **reasoning_strategy.md** — The canonical reasoning loop for dataset discovery
