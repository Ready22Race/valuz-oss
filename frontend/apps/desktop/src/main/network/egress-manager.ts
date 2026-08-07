import { randomUUID } from "node:crypto";
import {
  EgressControlServer,
  type EgressBootstrap,
  type RuntimePhaseRecord,
} from "./control-server";
import { EgressDiagnostics, redactProxyUrl } from "./diagnostics";
import {
  ForwardProxy,
  type ForwardProxyDescriptor,
  type ForwardProxyRegistration,
  type ForwardProxyConnectionEvent,
} from "./forward-proxy";
import { applyConnectionOutcome } from "./health";
import {
  ModelIngress,
  type ModelIngressConnectionEvent,
  type ModelIngressDescriptor,
  type ModelIngressRegistration,
} from "./model-ingress";
import {
  OutboundResolver,
  type OutboundResolverOptions,
} from "./outbound-resolver";
import type {
  EgressDiagnosticEvent,
  EgressMode,
  EgressManagerStatus,
  EgressResolution,
  EgressRuntime,
  EgressSnapshot,
} from "./types";
import { UpstreamConnector } from "./upstream-connector";

const PROXY_ENV_KEYS = [
  "http_proxy",
  "HTTP_PROXY",
  "https_proxy",
  "HTTPS_PROXY",
  "all_proxy",
  "ALL_PROXY",
  "no_proxy",
  "NO_PROXY",
] as const;

export const captureProxyEnvironment = (
  env: NodeJS.ProcessEnv,
): Record<string, string> => {
  const snapshot: Record<string, string> = {};
  for (const key of PROXY_ENV_KEYS) {
    const value = env[key];
    if (value !== undefined) snapshot[key] = value;
  }
  return snapshot;
};

export const resolveInitialEgressMode = (
  env: NodeJS.ProcessEnv,
  persistedMode: EgressMode = "auto",
): EgressMode =>
  env.VALUZ_EGRESS_MODE?.trim().toLowerCase() === "off" ||
  persistedMode === "off"
    ? "off"
    : "auto";

export interface EgressManagerOptions {
  mode: EgressMode;
  env: NodeJS.ProcessEnv;
  resolveSystemProxy: OutboundResolverOptions["resolveSystemProxy"];
  diagnostics?: EgressDiagnostics;
  now?: () => number;
  frontendsEnabled?: boolean;
  emergencyOverride?: boolean;
}

export interface ShadowResolveRequest {
  targetUrl: string;
  clientId: string;
  runtime: EgressRuntime;
}

/**
 * Electron-owned canary egress manager. It owns route resolution, diagnostics,
 * the loopback control plane and the two narrowly scoped traffic frontends.
 */
export class EgressManager {
  private mode: EgressMode;
  private readonly now: () => number;
  private readonly diagnostics: EgressDiagnostics;
  private readonly resolver: OutboundResolver;
  private readonly connector: UpstreamConnector;
  private readonly frontendsEnabled: boolean;
  private readonly emergencyOverride: boolean;
  private readonly modelIngress: ModelIngress;
  private readonly forwardProxy: ForwardProxy;
  private readonly controlServer: EgressControlServer;
  private readonly runtimePhases: RuntimePhaseRecord[] = [];
  private readonly snapshots = new Map<string, EgressSnapshot>();
  private started = false;
  private lastErrorCode: string | undefined;

  constructor(options: EgressManagerOptions) {
    this.mode = options.mode;
    this.now = options.now ?? Date.now;
    this.diagnostics = options.diagnostics ?? new EgressDiagnostics({ now: this.now });
    this.resolver = new OutboundResolver({
      env: captureProxyEnvironment(options.env),
      resolveSystemProxy: options.resolveSystemProxy,
      now: this.now,
    });
    this.frontendsEnabled = options.frontendsEnabled ?? false;
    this.emergencyOverride = options.emergencyOverride ?? false;
    const connector = new UpstreamConnector({ now: this.now });
    this.connector = connector;
    this.modelIngress = new ModelIngress({
      resolver: this.resolver,
      connector,
      mode: this.mode === "direct" ? "direct" : "auto",
      now: this.now,
      onConnection: (event) =>
        this.recordFrontendConnection("model_ingress", event),
    });
    this.forwardProxy = new ForwardProxy({
      resolver: this.resolver,
      connector,
      mode: this.mode === "direct" ? "direct" : "auto",
      now: this.now,
      onConnection: (event) =>
        this.recordFrontendConnection("forward_proxy", event),
    });
    this.controlServer = new EgressControlServer({
      mode: this.mode === "direct" ? "direct" : "auto",
      registerModelIngress: (registration) =>
        this.registerModelIngress(registration),
      registerForwardProxy: (registration) =>
        this.registerForwardProxy(registration),
      revokeClient: (clientId) => this.revokeClient(clientId),
      renewClients: (clientIds, expiresAt) => {
        for (const clientId of clientIds) {
          this.modelIngress.renew(clientId, expiresAt);
          this.forwardProxy.renew(clientId, expiresAt);
        }
      },
      recordRuntimePhase: (payload) => {
        this.runtimePhases.push({ ...payload, observedAt: this.now() });
        this.runtimePhases.splice(0, Math.max(0, this.runtimePhases.length - 500));
      },
      now: this.now,
    });
  }

  async start(): Promise<void> {
    if (this.mode === "off") return;
    this.lastErrorCode = undefined;
    this.started = true;
    if (!this.frontendsEnabled) return;
    try {
      await this.modelIngress.start();
      await this.forwardProxy.start();
      await this.controlServer.start();
      this.connector.setProtectedLoopbackPorts(
        [
          this.modelIngress.getListeningPort(),
          this.forwardProxy.getListeningPort(),
          this.controlServer.getListeningPort(),
        ].filter((port): port is number => port !== null),
      );
    } catch (error) {
      await Promise.allSettled([
        this.modelIngress.stop(),
        this.forwardProxy.stop(),
        this.controlServer.stop(),
      ]);
      this.started = false;
      this.lastErrorCode = "egress_frontend_start_failed";
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.started = false;
    await Promise.allSettled([
      this.controlServer.stop(),
      this.modelIngress.stop(),
      this.forwardProxy.stop(),
    ]);
    this.resolver.invalidate();
    this.connector.setProtectedLoopbackPorts([]);
    this.snapshots.clear();
    this.diagnostics.clear();
    this.runtimePhases.splice(0);
  }

  /** Stop registrations first while allowing already-established streams to drain. */
  async quiesce(): Promise<void> {
    this.started = false;
    await this.controlServer.stop();
    this.modelIngress.revokeAll();
    this.forwardProxy.revokeAll();
  }

  async setMode(mode: EgressMode): Promise<void> {
    if (this.emergencyOverride && mode !== "off") {
      throw new Error("egress_mode_locked_by_environment");
    }
    this.mode = mode;
    this.resolver.invalidate();
    if (mode === "off") {
      this.lastErrorCode = undefined;
      await this.stop();
      return;
    }
    const activeMode = mode === "direct" ? "direct" : "auto";
    this.modelIngress.setMode(activeMode);
    this.forwardProxy.setMode(activeMode);
    this.controlServer.setMode(activeMode);
    if (!this.started) await this.start();
  }

  getMode(): EgressMode {
    return this.mode;
  }

  isStarted(): boolean {
    return this.started;
  }

  isFrontendsEnabled(): boolean {
    return this.frontendsEnabled;
  }

  getStatus(): EgressManagerStatus {
    return {
      mode: this.mode,
      enabled: this.frontendsEnabled,
      started: this.started,
      emergencyOverride: this.emergencyOverride,
      snapshotCount: this.snapshots.size,
      diagnosticEventCount: this.diagnostics.snapshot().length,
      lastErrorCode: this.lastErrorCode,
    };
  }

  getDiagnostics(): EgressDiagnosticEvent[] {
    return this.diagnostics.snapshot();
  }

  getSnapshots(): EgressSnapshot[] {
    return [...this.snapshots.values()].map((snapshot) => ({ ...snapshot }));
  }

  getRuntimePhases(): RuntimePhaseRecord[] {
    return this.runtimePhases.map((phase) => ({ ...phase }));
  }

  getBootstrap(): EgressBootstrap | null {
    if (!this.frontendsEnabled || !this.started || this.mode === "off") {
      return null;
    }
    return this.controlServer.bootstrap();
  }

  registerModelIngress(
    registration: ModelIngressRegistration,
  ): ModelIngressDescriptor {
    if (!this.frontendsEnabled || !this.started || this.mode === "off") {
      throw new Error("egress_frontends_unavailable");
    }
    return this.modelIngress.register(registration);
  }

  registerForwardProxy(
    registration: ForwardProxyRegistration,
  ): ForwardProxyDescriptor {
    if (!this.frontendsEnabled || !this.started || this.mode === "off") {
      throw new Error("egress_frontends_unavailable");
    }
    return this.forwardProxy.register(registration);
  }

  revokeClient(clientId: string): void {
    this.modelIngress.revoke(clientId);
    this.forwardProxy.revoke(clientId);
  }

  async resolveShadow(request: ShadowResolveRequest): Promise<EgressResolution> {
    if (!this.started || this.mode === "off") {
      return {
        targetOrigin: this.targetOrigin(request.targetUrl),
        candidates: [],
        resolvedAt: this.now(),
        ttlMs: 0,
        status: "unknown",
        reason: "egress_manager_off",
      };
    }

    const connectionAttemptId = randomUUID();
    const startedAt = this.now();
    const targetOrigin = this.targetOrigin(request.targetUrl);
    const activeMode = this.mode === "direct" ? "direct" : "auto";
    this.diagnostics.record({
      event: "egress.attempt.started",
      connectionAttemptId,
      clientId: request.clientId,
      runtime: request.runtime,
      frontend: "shadow",
      targetOrigin,
      mode: activeMode,
      timestamp: startedAt,
    });

    const result = await this.resolver.resolve(request.targetUrl, activeMode);
    const finishedAt = this.now();
    const resolveMs = Math.max(0, finishedAt - startedAt);
    const first = result.candidates[0];
    const key = `${request.clientId}:${result.targetOrigin}`;

    if (result.status === "resolved" && first) {
      const redactedProxy =
        "url" in first ? redactProxyUrl(first.url) : undefined;
      this.diagnostics.record({
        event: "egress.route.resolved",
        connectionAttemptId,
        clientId: request.clientId,
        runtime: request.runtime,
        frontend: "shadow",
        targetOrigin: result.targetOrigin,
        mode: activeMode,
        timestamp: finishedAt,
        resolveMs,
        route: first.kind,
        source: first.source,
        redactedProxy,
        candidateCount: result.candidates.length,
      });
      this.snapshots.set(key, {
        clientId: request.clientId,
        runtime: request.runtime,
        frontend: "shadow",
        targetOrigin: result.targetOrigin,
        mode: this.mode,
        route: first.kind,
        health: "unknown",
        source: first.source,
        redactedProxy,
        resolveMs,
        reconnectCount: 0,
        fallbackCount: 0,
        correlationConfidence: "exact_runtime",
        updatedAt: finishedAt,
      });
      return result;
    }

    const errorCode = result.reason ?? "egress_resolve_unknown";
    this.diagnostics.record({
      event: "egress.resolve.failed",
      connectionAttemptId,
      clientId: request.clientId,
      runtime: request.runtime,
      frontend: "shadow",
      targetOrigin: result.targetOrigin,
      mode: activeMode,
      timestamp: finishedAt,
      resolveMs,
      candidateCount: 0,
      errorCode,
    });
    this.snapshots.set(key, {
      clientId: request.clientId,
      runtime: request.runtime,
      frontend: "shadow",
      targetOrigin: result.targetOrigin,
      mode: this.mode,
      route: "unknown",
      health: "unknown",
      resolveMs,
      reconnectCount: 0,
      fallbackCount: 0,
      lastErrorCode: errorCode,
      correlationConfidence: "exact_runtime",
      updatedAt: finishedAt,
    });
    return result;
  }

  private targetOrigin(raw: string): string {
    try {
      return new URL(raw).origin;
    } catch {
      return "invalid";
    }
  }

  private recordFrontendConnection(
    frontend: "model_ingress" | "forward_proxy",
    event: ModelIngressConnectionEvent | ForwardProxyConnectionEvent,
  ): void {
    const activeMode = this.mode === "direct" ? "direct" : "auto";
    const finishedAt = this.now();
    this.diagnostics.record({
      event: "egress.attempt.started",
      connectionAttemptId: event.connectionAttemptId,
      clientId: event.registration.clientId,
      runtime: event.registration.runtime,
      frontend,
      targetOrigin: event.targetOrigin,
      mode: activeMode,
      timestamp: event.startedAt,
    });
    const key = `${event.registration.clientId}:${event.targetOrigin}`;

    if (event.connection) {
      const redactedProxy =
        "url" in event.connection.route
          ? redactProxyUrl(event.connection.route.url)
          : undefined;
      this.diagnostics.record({
        event: "egress.route.resolved",
        connectionAttemptId: event.connectionAttemptId,
        clientId: event.registration.clientId,
        runtime: event.registration.runtime,
        frontend,
        targetOrigin: event.targetOrigin,
        mode: activeMode,
        timestamp: event.startedAt + event.resolutionMs,
        resolveMs: event.resolutionMs,
        route: event.connection.route.kind,
        source: event.connection.route.source,
        redactedProxy,
        candidateIndex: event.connection.candidateIndex,
      });
      this.diagnostics.record({
        event: "egress.stream.established",
        connectionAttemptId: event.connectionAttemptId,
        clientId: event.registration.clientId,
        runtime: event.registration.runtime,
        frontend,
        targetOrigin: event.targetOrigin,
        mode: activeMode,
        timestamp: finishedAt,
        route: event.connection.route.kind,
        source: event.connection.route.source,
        redactedProxy,
        candidateIndex: event.connection.candidateIndex,
        connectMs: event.connection.connectMs,
        fallbackCount: event.connection.fallbackCount,
      });
      const base: EgressSnapshot = {
        clientId: event.registration.clientId,
        runtime: event.registration.runtime,
        frontend,
        targetOrigin: event.targetOrigin,
        mode: this.mode,
        route: event.connection.route.kind,
        health: "unknown",
        source: event.connection.route.source,
        redactedProxy,
        resolveMs: event.resolutionMs,
        reconnectCount: 0,
        fallbackCount: 0,
        correlationConfidence: "exact_runtime",
        updatedAt: finishedAt,
      };
      this.snapshots.set(
        key,
        applyConnectionOutcome(
          this.snapshots.get(key) ?? base,
          {
            success: true,
            connectMs: event.connection.connectMs,
            fallbackCount: event.connection.fallbackCount,
          },
          { now: () => finishedAt },
        ),
      );
      return;
    }

    const errorCode = event.errorCode ?? "egress_connect_failed";
    this.diagnostics.record({
      event: "egress.connect.failed",
      connectionAttemptId: event.connectionAttemptId,
      clientId: event.registration.clientId,
      runtime: event.registration.runtime,
      frontend,
      targetOrigin: event.targetOrigin,
      mode: activeMode,
      timestamp: finishedAt,
      resolveMs: event.resolutionMs,
      errorCode,
    });
    const base: EgressSnapshot = {
      clientId: event.registration.clientId,
      runtime: event.registration.runtime,
      frontend,
      targetOrigin: event.targetOrigin,
      mode: this.mode,
      route: "unknown",
      health: "unknown",
      resolveMs: event.resolutionMs,
      reconnectCount: 0,
      fallbackCount: 0,
      correlationConfidence: "exact_runtime",
      updatedAt: finishedAt,
    };
    this.snapshots.set(
      key,
      applyConnectionOutcome(
        this.snapshots.get(key) ?? base,
        { success: false, errorCode },
        { now: () => finishedAt },
      ),
    );
  }
}
