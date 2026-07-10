# ModelScope Connector Marketplace Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Connector marketplace backed by Valuz official connectors and ModelScope MCP, with one-click connection into the existing Connector Library.

**Architecture:** Extend the normalized marketplace contract with a `connector` item type and a typed, non-secret connector configuration preview. A backend ModelScope adapter anonymously reads public list/detail APIs and only accepts directly runnable `npx`/`uvx` or HTTPS Streamable HTTP/SSE configurations. The frontend reuses `/v1/connectors` for creation so credential storage, OAuth discovery, probing, and status handling remain owned by the connector module.

**Tech Stack:** FastAPI, Pydantic, httpx, React, TypeScript, existing Connector API and dialogs.

---

### Task 1: Product and API contract

**Files:**
- Modify: `docs/plans/2026-07-07-skillhub-marketplace-product-prototype.md`
- Modify: `docs/plans/2026-07-08-skillhub-marketplace-implementation.md`
- Modify: `api/openapi.yaml`

**Steps:**
1. Add `Connectors` as the third scoped marketplace tab and document ModelScope + Valuz official supply.
2. Document direct-config eligibility: `npx`, `uvx`, HTTPS Streamable HTTP, and HTTPS SSE.
3. Add connector item/source/install-target/configuration schemas to OpenAPI.
4. Validate the OpenAPI document through the existing generation/typecheck path.

### Task 2: ModelScope upstream adapter

**Files:**
- Create: `backend/valuz_agent/modules/marketplace/modelscope.py`
- Test: `backend/tests/modules/marketplace/test_modelscope.py`

**Steps:**
1. Write failing tests for anonymous list/detail normalization and upstream failures.
2. Implement an uncached async client for `PUT /mcp/servers` and `GET /mcp/servers/{id}` with bounded timeouts.
3. Preserve ModelScope's default order, filter to hosted entries, and page through its documented first 100 results while retaining server-side search.
4. Run the focused client tests.

### Task 3: Normalize connector marketplace items

**Files:**
- Modify: `backend/valuz_agent/modules/marketplace/models.py`
- Modify: `backend/valuz_agent/modules/marketplace/service.py`
- Modify: `backend/valuz_agent/api/routes/marketplace.py`
- Modify: `backend/tests/modules/marketplace/test_marketplace_service.py`

**Steps:**
1. Write failing tests for ModelScope cards, installed flags, supported direct configs, required environment fields, and unsupported hosted-only details.
2. Add ModelScope connector normalization; existing Valuz built-ins remain supplied by the Connector Library catalog.
3. Parse only `npx`/`uvx`, HTTPS Streamable HTTP, and HTTPS SSE configurations; represent placeholders as typed input fields and never copy upstream test secrets.
4. Mark unsupported/hosted-only details as locked instead of attempting deployment.
5. Run marketplace service tests.

### Task 4: Connector marketplace UI

**Files:**
- Modify: `frontend/packages/core/src/api/marketplace-api.ts`
- Modify: `frontend/packages/app/src/pages/MarketplacePage.tsx`
- Create: `frontend/packages/app/src/components/MarketplaceConnectorDialog.tsx`
- Modify: `frontend/packages/app/src/pages/ConnectorsPage.tsx`
- Modify: `i18n/locales/zh-CN.json`
- Modify: `i18n/locales/en-US.json`

**Steps:**
1. Add a visible `Marketplace` action to the Connector Library and preserve the custom connector `+` menu.
2. Add a third `Connectors` tab with independent search state and paged cards.
3. Build a connector preview/configuration dialog that maps typed fields into `CreateConnectorRequest`.
4. On success, navigate to `/connectors`; hosted-only items show a clear unsupported message.
5. Regenerate i18n types and run frontend typecheck/lint.

### Task 5: End-to-end verification

**Files:**
- Verify all files above.

**Steps:**
1. Run backend marketplace and ModelScope tests.
2. Run i18n schema checks and targeted frontend typecheck/lint.
3. Start the desktop app and verify Connector Library -> Marketplace -> Connectors.
4. Verify a direct `uvx` detail can be connected and lands in the Connector Library without a ModelScope account.
5. Run repository-level checks and report any pre-existing baseline failures separately.
