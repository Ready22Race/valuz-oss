"""Marketplace — the normalized discovery/import catalog.

Aggregates three supply sources behind one item shape (see
``api/openapi.yaml`` → ``MarketplaceItem``):

- SkillHub skills (remote catalog, curated category allowlist only),
- Valuz official skills (already indexed in ``valuz_skill_index``),
- Valuz-curated agent / agent-team templates (local resources).

The frontend never calls SkillHub directly; installs are delegated to the
existing pipelines (skill URL-import, agent packs, agent library).
"""
