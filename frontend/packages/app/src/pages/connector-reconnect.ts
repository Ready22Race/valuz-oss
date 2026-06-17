// Decide how the Connectors page should reconnect an already-installed
// connector when the user clicks "Connect" on one that isn't currently
// connected.
//
// The subtlety is OAuth: an OAuth connector stores an access token, and once
// that token expires a bare re-probe (`POST /connectors/{id}/test`) replays the
// stale `Authorization: Bearer …` header — the MCP server answers 401 and the
// UI surfaces it as a plain "connection failed". The fix is to re-run the
// authorization flow instead, which `create_connector` already supports for an
// existing connector (it reuses the saved client and returns a fresh
// `authorization_url` for re-consent). Non-OAuth connectors carry no expiring
// token, so re-probing in place stays the correct action.

import type { ConnectorItem, CreateConnectorRequest } from "@valuz/core";

export type ReconnectAction =
  | { kind: "reauthorize"; payload: CreateConnectorRequest }
  | { kind: "test" };

export function reconnectAction(connector: ConnectorItem): ReconnectAction {
  if (connector.auth_type === "oauth") {
    return {
      kind: "reauthorize",
      // Mirror the field-less catalog connect payload. The backend keys off
      // the slug to reuse the saved OAuth client + metadata, so no credentials
      // need to be re-entered for re-consent.
      payload: {
        slug: connector.slug,
        display_name: connector.display_name,
        transport: connector.transport || "http",
        url: connector.url ?? "",
        auth_type: "oauth",
        description: connector.description,
        connector_type: connector.connector_type,
      },
    };
  }
  return { kind: "test" };
}
