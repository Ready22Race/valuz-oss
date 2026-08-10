type NetworkHealth = "unknown" | "healthy" | "degraded" | "failed";

export const isManagedNetworkMode = (mode?: string): boolean =>
  mode === "auto" || mode === "direct";

export const shouldShowNetworkDiagnosticsAction = (
  health: NetworkHealth,
  hasDiagnostics: boolean,
): boolean =>
  hasDiagnostics && (health === "degraded" || health === "failed");

export const currentNetworkSnapshots = <
  T extends {
    activeTurn: boolean;
    requestActive?: boolean;
    totalMs?: number;
    updatedAt: number;
  },
>(snapshots: T[]): T[] =>
  snapshots
    .filter(
      (snapshot) =>
        snapshot.activeTurn &&
        (snapshot.requestActive ?? snapshot.totalMs === undefined),
    )
    .sort((left, right) => right.updatedAt - left.updatedAt);

export const networkRuntimeLabel = (runtime: string): string =>
  ({
    codex: "Codex",
    claude: "Claude Code",
    deepagents: "Valuz Agent",
    provider_test: "Provider Test",
  })[runtime] ?? runtime;

export const networkRouteKey = (
  route: string,
):
  | "settings.network.route.direct"
  | "settings.network.route.httpProxy"
  | "settings.network.route.socks5Proxy"
  | "settings.network.route.unknown" =>
  route === "direct"
    ? "settings.network.route.direct"
    : route === "http_proxy"
      ? "settings.network.route.httpProxy"
      : route === "socks5_proxy"
        ? "settings.network.route.socks5Proxy"
        : "settings.network.route.unknown";

export const networkHealthDetailKey = (snapshot: {
  health: NetworkHealth;
  connectMs?: number;
}):
  | "settings.network.healthDetail.waitingRequest"
  | "settings.network.healthDetail.waitingResponse"
  | "settings.network.healthDetail.healthy"
  | "settings.network.healthDetail.degraded"
  | "settings.network.healthDetail.failed" => {
  if (snapshot.health === "healthy") {
    return "settings.network.healthDetail.healthy";
  }
  if (snapshot.health === "degraded") {
    return "settings.network.healthDetail.degraded";
  }
  if (snapshot.health === "failed") {
    return "settings.network.healthDetail.failed";
  }
  return snapshot.connectMs === undefined
    ? "settings.network.healthDetail.waitingRequest"
    : "settings.network.healthDetail.waitingResponse";
};
