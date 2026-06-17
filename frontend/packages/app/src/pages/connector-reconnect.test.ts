import { describe, expect, it } from "vitest";
import type { ConnectorItem } from "@valuz/core";
import { reconnectAction } from "./connector-reconnect";

function makeConnector(overrides: Partial<ConnectorItem> = {}): ConnectorItem {
  return {
    id: "c1",
    slug: "valuz-search",
    display_name: "Valuz · Search",
    description: "Full-market search",
    connector_type: "builtin",
    transport: "http",
    url: "https://mcp.reportify.cn/search/mcp",
    auth_type: "none",
    has_api_key: false,
    command: null,
    args: [],
    working_dir: null,
    env: {},
    headers: [],
    params: [],
    enabled: true,
    status: "error",
    tool_count: null,
    last_tested_at: null,
    error_message: "Client error '401 Unauthorized'",
    created_at: 0,
    updated_at: 0,
    ...overrides,
  };
}

describe("reconnectAction", () => {
  it("re-runs the authorization flow when the connector uses OAuth", () => {
    const action = reconnectAction(makeConnector({ auth_type: "oauth" }));

    expect(action.kind).toBe("reauthorize");
    if (action.kind !== "reauthorize") throw new Error("unreachable");
    expect(action.payload).toEqual({
      slug: "valuz-search",
      display_name: "Valuz · Search",
      transport: "http",
      url: "https://mcp.reportify.cn/search/mcp",
      auth_type: "oauth",
      description: "Full-market search",
      connector_type: "builtin",
    });
  });

  it("re-probes in place for a non-OAuth connector", () => {
    const action = reconnectAction(makeConnector({ auth_type: "none" }));

    expect(action.kind).toBe("test");
  });

  it("falls back to http transport when the connector has none recorded", () => {
    const action = reconnectAction(
      makeConnector({ auth_type: "oauth", transport: "" }),
    );

    if (action.kind !== "reauthorize") throw new Error("unreachable");
    expect(action.payload.transport).toBe("http");
  });
});
