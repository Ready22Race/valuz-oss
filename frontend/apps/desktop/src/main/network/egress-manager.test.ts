import { createServer } from "node:http";
import { describe, expect, it } from "vitest";
import { EgressDiagnostics, redactProxyUrl } from "./diagnostics";
import {
  captureProxyEnvironment,
  EgressManager,
  resolveInitialEgressMode,
} from "./egress-manager";

describe("egress diagnostics", () => {
  it("redacts credentials and every URL component after host/port", () => {
    expect(
      redactProxyUrl("http://user:secret@proxy.example:8080/private?q=token#x"),
    ).toBe("http://proxy.example:8080");
    expect(redactProxyUrl("socks5://proxy.example:1080")).toBe(
      "socks5://proxy.example:1080",
    );
  });

  it("bounds diagnostics by age and count and returns defensive copies", () => {
    let now = 100;
    const diagnostics = new EgressDiagnostics({
      maxEntries: 2,
      maxAgeMs: 10,
      now: () => now,
    });
    const event = (id: string, timestamp: number) => ({
      event: "egress.attempt.started" as const,
      connectionAttemptId: id,
      clientId: "client",
      runtime: "codex" as const,
      frontend: "shadow" as const,
      targetOrigin: "https://api.example",
      mode: "auto" as const,
      timestamp,
    });
    diagnostics.record(event("old", 89));
    diagnostics.record(event("one", 99));
    diagnostics.record(event("two", 100));
    diagnostics.record(event("three", 100));

    const snapshot = diagnostics.snapshot();
    expect(snapshot.map((item) => item.connectionAttemptId)).toEqual([
      "two",
      "three",
    ]);
    snapshot[0].clientId = "mutated";
    expect(diagnostics.snapshot()[0].clientId).toBe("client");

    now = 111;
    expect(diagnostics.snapshot()).toEqual([]);
  });
});

describe("EgressManager shadow mode", () => {
  it("captures only proxy-related environment keys", () => {
    expect(
      captureProxyEnvironment({
        HTTPS_PROXY: "http://proxy.example:8080",
        NO_PROXY: "localhost",
        OPENAI_API_KEY: "must-not-be-copied",
      }),
    ).toEqual({
      HTTPS_PROXY: "http://proxy.example:8080",
      NO_PROXY: "localhost",
    });
  });

  it("only accepts off as the emergency environment override", () => {
    expect(resolveInitialEgressMode({ VALUZ_EGRESS_MODE: " off " })).toBe("off");
    expect(resolveInitialEgressMode({ VALUZ_EGRESS_MODE: "direct" })).toBe(
      "auto",
    );
  });

  it("does not resolve or emit diagnostics while off", async () => {
    const manager = new EgressManager({
      mode: "off",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
    });
    manager.start();

    await expect(
      manager.resolveShadow({
        targetUrl: "https://api.example/v1",
        clientId: "client-1",
        runtime: "codex",
      }),
    ).resolves.toMatchObject({
      status: "unknown",
      reason: "egress_manager_off",
    });
    expect(manager.isStarted()).toBe(false);
    expect(manager.getDiagnostics()).toEqual([]);
  });

  it("records allowlisted shadow resolution events and a runtime snapshot", async () => {
    let now = 100;
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => {
        now = 107;
        return "PROXY user:secret@proxy.example:8080; DIRECT";
      },
      now: () => now,
    });
    manager.start();

    await expect(
      manager.resolveShadow({
        targetUrl: "https://api.example/private?prompt=secret",
        clientId: "client-1",
        runtime: "claude",
      }),
    ).resolves.toMatchObject({
      status: "unknown",
      reason: "invalid_pac_proxy_endpoint",
    });
    expect(manager.getDiagnostics()).toEqual([
      expect.objectContaining({
        event: "egress.attempt.started",
        targetOrigin: "https://api.example",
      }),
      expect.objectContaining({
        event: "egress.resolve.failed",
        targetOrigin: "https://api.example",
        resolveMs: 7,
      }),
    ]);
    expect(JSON.stringify(manager.getDiagnostics())).not.toContain("prompt");
    expect(JSON.stringify(manager.getDiagnostics())).not.toContain("secret");
    expect(manager.getSnapshots()).toEqual([
      expect.objectContaining({
        runtime: "claude",
        route: "unknown",
        health: "unknown",
      }),
    ]);
  });

  it("records a redacted proxy on successful resolution", async () => {
    const manager = new EgressManager({
      mode: "auto",
      env: { HTTPS_PROXY: "http://user:secret@proxy.example:8080/private" },
      resolveSystemProxy: async () => "DIRECT",
    });
    manager.start();
    await manager.resolveShadow({
      targetUrl: "https://api.example/v1",
      clientId: "client-1",
      runtime: "deepagents",
    });

    const resolved = manager
      .getDiagnostics()
      .find((event) => event.event === "egress.route.resolved");
    expect(resolved).toMatchObject({
      redactedProxy: "http://proxy.example:8080",
      route: "http_proxy",
      source: "env",
    });
    expect(JSON.stringify(resolved)).not.toContain("secret");
    expect(JSON.stringify(resolved)).not.toContain("private");
  });

  it("feature-gates real frontends and records their connection health", async () => {
    const upstream = createServer((_request, response) => response.end("ok"));
    await new Promise<void>((resolve) =>
      upstream.listen(0, "127.0.0.1", resolve),
    );
    const address = upstream.address();
    if (!address || typeof address === "string") throw new Error("missing address");
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
    });
    try {
      await manager.start();
      const descriptor = manager.registerModelIngress({
        clientId: "claude-runtime-1",
        runtime: "claude",
        upstreamBaseUrl: `http://127.0.0.1:${address.port}/v1`,
        supportsWebSocket: true,
      });

      await expect(fetch(`${descriptor.baseUrl}/messages`)).resolves.toMatchObject({
        status: 200,
      });
      expect(
        manager.getDiagnostics().map((event) => event.event),
      ).toEqual([
        "egress.attempt.started",
        "egress.route.resolved",
        "egress.stream.established",
      ]);
      expect(manager.getSnapshots()).toEqual([
        expect.objectContaining({
          runtime: "claude",
          frontend: "model_ingress",
          route: "direct",
          health: "healthy",
        }),
      ]);
    } finally {
      await manager.stop();
      await new Promise<void>((resolve) => upstream.close(() => resolve()));
    }
  });

  it("stamps runtime phases on receipt for a cross-process timeline", async () => {
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
      now: () => 1_234,
    });
    try {
      await manager.start();
      const bootstrap = manager.getBootstrap();
      expect(bootstrap).not.toBeNull();
      const response = await fetch(`${bootstrap!.controlEndpoint}/v1/runtime-phase`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${bootstrap!.bootstrapToken}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          turnAttemptId: "turn-attempt-1234",
          clientId: "runtime-client-12",
          phase: "dispatch",
          monotonicMs: 99,
        }),
      });

      expect(response.status).toBe(202);
      expect(manager.getRuntimePhases()).toEqual([
        expect.objectContaining({
          phase: "dispatch",
          monotonicMs: 99,
          observedAt: 1_234,
        }),
      ]);
    } finally {
      await manager.stop();
    }
  });

  it("rejects registrations that would route a model ingress back into a manager listener", async () => {
    const manager = new EgressManager({
      mode: "auto",
      env: {},
      resolveSystemProxy: async () => "DIRECT",
      frontendsEnabled: true,
    });
    try {
      await manager.start();
      const forward = manager.registerForwardProxy({
        clientId: "provider-test-loop",
        runtime: "provider_test",
      });
      const listener = new URL(forward.proxyUrl);
      listener.username = "";
      listener.password = "";

      expect(() =>
        manager.registerModelIngress({
          clientId: "codex-loop",
          runtime: "codex",
          upstreamBaseUrl: listener.href,
          supportsWebSocket: true,
        }),
      ).toThrow("model_ingress_proxy_loop_detected");
    } finally {
      await manager.stop();
    }
  });
});
