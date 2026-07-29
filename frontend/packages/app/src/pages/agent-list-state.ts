import type { Agent } from "@valuz/core";

interface AgentSyncInfo {
  status?: string;
}

/** Cloud-only organization resources are catalog entries, not local targets. */
export function isCloudOnlyResource(resource: unknown): boolean {
  if (!resource || typeof resource !== "object") return false;
  const outer = resource as Record<string, unknown>;
  const target =
    outer.kind === "installed" && outer.item && typeof outer.item === "object"
      ? (outer.item as Record<string, unknown>)
      : outer;
  const sync = target._sync as AgentSyncInfo | undefined;
  return sync?.status === "cloud_only";
}

/** Cloud-only organization Agents are catalog entries, not local edit targets. */
export function isCloudOnlyAgent(agent: Agent): boolean {
  return isCloudOnlyResource(agent);
}

/** Built-in runtime agent whose managed identity and resources are immutable. */
export function isSystemAgent(agent: Agent): boolean {
  return agent.kind === "system";
}

/** Keep the canonical system Agent ahead of every portable Agent. */
export function compareAgentsWithValurionFirst(a: Agent, b: Agent): number {
  const valurionPriority =
    Number(b.slug === "valurion") - Number(a.slug === "valurion");
  return valurionPriority || a.name.localeCompare(b.name);
}
