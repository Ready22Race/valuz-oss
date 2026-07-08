---
name: serenity-unified-skill
description: Serenity-inspired supply-chain bottleneck investment research framework. Use for theme-to-chain mapping, bottleneck scoring, candidate triage, and invalidation review. Research only; no investment advice.
version: 1
tags: [investment, supply-chain, research, official-template]
---

# serenity-unified-skill

This is a Valuz template skill for investment research teams that need a
structured supply-chain bottleneck workflow. It is inspired by public
Serenity-style supply-chain research, but performance claims about any author
must never be used as evidence for a current investment conclusion.

## Core Workflow

1. Define the theme, downstream demand driver, target market, and time horizon.
2. Work upstream from demand and capex before looking at tickers.
3. Map the chain into downstream application, system/integration, core
   components, equipment/tools, materials/resources, and base services.
4. Build a player census for every layer, including private, acquired, delisted,
   and uncertain-status companies.
5. Score bottleneck candidates by supply tightness, qualification barriers,
   value leverage, public-company purity, catalyst clarity, and evidence quality.
6. Red-team the thesis with substitute routes, second suppliers, customer
   in-house efforts, capacity additions, valuation risk, and stale information.
7. Return a research draft with sources, confidence levels, invalidation
   conditions, and missing-data items.

## Output Shape

Use this structure unless the Lead asks for something narrower:

- Theme and downstream demand driver
- Supply-chain layer map
- Key bottleneck nodes
- Candidate companies and public-market expression quality
- Evidence table with sources and confidence
- Catalysts and monitoring signals
- Red flags and invalidation conditions
- Missing data / next research tasks

## Guardrails

- Research support only. Do not give buy, sell, price target, or position-size
  advice.
- Separate fact, inference, and hypothesis.
- Mark unsupported claims as `[UNVERIFIED]`.
- If no clean listed-company expression exists, say so plainly.
