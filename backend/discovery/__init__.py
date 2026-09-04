"""
backend/discovery — Phase 1 Universal Lead Discovery.

Public entry points:
  planner.DiscoveryPlanner().plan(...)   -> DiscoveryPlan
  adapters.get_registry()                -> SourceRegistry (9 wrapped scrapers)
  merge_dedup.save_candidate(...)        -> merge-aware lead save + provenance
  quick_search.run_quick_search(...)     -> JobQueue handler for Quick Search

See docs/superpowers/specs/2026-08-25-lead-discovery-planner-design.md.
"""
