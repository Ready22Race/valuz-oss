import type { ServiceDescriptor } from '@valuz/core'
import type {
  EgressDiagnosticEvent,
  EgressManagerStatus,
  EgressMode,
  EgressSnapshot,
} from '../network/types'
import type { RuntimePhaseRecord } from '../network/control-server'
import { createServiceManager, type DesktopServiceManager } from '../services/mod'
import { cleanStaleUpdateCache } from '../update-cache'

// Once-per-session guard: purge a previous version's leftover update package the
// first time the backend reports healthy, not before. Keeping it until the new
// build proves it actually runs means a start-up failure (e.g. a bad update)
// still leaves the old package around to fall back to.
let updateCachePurged = false

export interface DesktopRuntime {
  startAllServices(): Promise<ReturnType<DesktopServiceManager['getAllStatus']>>
  stopAllServices(): ReturnType<DesktopServiceManager['stopAllServices']>
  getServicesStatus(): ReturnType<DesktopServiceManager['getAllStatus']>
  restartService(serviceName: string): Promise<ReturnType<DesktopServiceManager['getAllStatus']>>
  getServiceLogs(serviceName: string): string[]
  getAgentServerInfo(): ReturnType<DesktopServiceManager['getAgentServerInfo']>
  getShellStatus(): { ready: boolean }
  listServiceDescriptors(): ServiceDescriptor[]
  registerServiceDescriptor(descriptor: ServiceDescriptor): ServiceDescriptor
  unregisterServiceDescriptor(name: string): boolean
  getEgressDiagnostics(): EgressDiagnosticEvent[]
  getEgressSnapshots(): EgressSnapshot[]
  getEgressMode(): EgressMode
  getEgressStatus(): EgressManagerStatus
  getEgressRuntimePhases(): RuntimePhaseRecord[]
  setEgressMode(
    mode: EgressMode,
    options?: { interruptActiveRuns?: boolean },
  ): Promise<EgressManagerStatus>
}

type DesktopEventEmitter = (eventName: string, payload: unknown) => void

type ActiveRunsProbe = (port: number) => Promise<string[]>
type ActiveRunsInterrupt = (port: number, sessionIds: string[]) => Promise<void>

const probeActiveRuns: ActiveRunsProbe = async (port) => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 1_500)
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/runs?status=running`, {
      signal: controller.signal,
    })
    if (!response.ok) return []
    const payload = (await response.json()) as {
      runs?: Array<{ session_id?: unknown }>
    }
    if (!Array.isArray(payload.runs)) return []
    return payload.runs.flatMap((run) =>
      typeof run.session_id === 'string' ? [run.session_id] : [],
    )
  } catch {
    // If the backend is already unavailable, switching network ownership may
    // be the recovery action that brings it back. Do not strand the user by
    // treating an unreachable activity probe as an active task.
    return []
  } finally {
    clearTimeout(timeout)
  }
}

const interruptActiveRuns: ActiveRunsInterrupt = async (port, sessionIds) => {
  await Promise.all(
    sessionIds.map(async (sessionId) => {
      const controller = new AbortController()
      // Runtime interruption can legitimately take close to a minute while a
      // model client unwinds. Give the backend enough time to finalize the
      // session as idle before rebuilding it under the new network owner.
      const timeout = setTimeout(() => controller.abort(), 70_000)
      try {
        const response = await fetch(
          `http://127.0.0.1:${port}/v1/sessions/${encodeURIComponent(sessionId)}/interrupt`,
          { method: 'POST', signal: controller.signal },
        )
        if (!response.ok) {
          throw new Error(`interrupt_failed_${response.status}`)
        }
      } finally {
        clearTimeout(timeout)
      }
    }),
  )
}

export const createDesktopRuntime = (
  manager: DesktopServiceManager,
  emitEvent: DesktopEventEmitter = () => undefined,
  activeRunsProbe: ActiveRunsProbe = probeActiveRuns,
  activeRunsInterrupt: ActiveRunsInterrupt = interruptActiveRuns,
): DesktopRuntime => {
  const restartService = async (serviceName: string) => {
    // A network-mode boundary rebuilds the backend process so the replacement
    // receives (or deliberately does not receive) the one-shot egress
    // bootstrap. Tell the renderer *before* stopping it: otherwise the routed
    // app remains interactive during the short restart window and a send can
    // race the closed loopback port, surfacing a misleading permanent
    // "backend unavailable" turn even though the replacement is healthy a
    // moment later.
    const restarting = manager.getAllStatus().map((service) =>
      service.name === serviceName
        ? { ...service, status: 'starting' as const, pid: null }
        : service,
    )
    emitEvent('service-status-changed', restarting)

    const snapshot = await manager.restartService(serviceName)
    emitEvent('service-status-changed', snapshot)
    return snapshot
  }

  return {
    async startAllServices() {
      const snapshot = await manager.startAllServices()
      emitEvent('service-status-changed', snapshot)
      // Backend came up healthy → the app has truly started. Only now purge a
      // previous version's leftover update package (see ``cleanStaleUpdateCache``).
      if (
        !updateCachePurged &&
        snapshot.some((s) => s.name === 'agent-server' && s.status === 'running')
      ) {
        updateCachePurged = true
        cleanStaleUpdateCache()
      }
      return snapshot
    },
    async stopAllServices() {
      const snapshot = await manager.stopAllServices()
      emitEvent('service-status-changed', snapshot)
      return snapshot
    },
    getServicesStatus() {
      return manager.getAllStatus()
    },
    async restartService(serviceName: string) {
      return restartService(serviceName)
    },
    getServiceLogs(serviceName: string) {
      return manager.getLogs(serviceName)
    },
    getAgentServerInfo() {
      return manager.getAgentServerInfo()
    },
    getShellStatus() {
      return manager.getShellStatus()
    },
    listServiceDescriptors() {
      return manager.descriptors.snapshot()
    },
    registerServiceDescriptor(descriptor: ServiceDescriptor) {
      const registered = manager.registerDescriptor(descriptor)
      emitEvent('service-descriptors-changed', manager.descriptors.snapshot())
      return registered
    },
    unregisterServiceDescriptor(name: string) {
      const removed = manager.unregisterDescriptor(name)
      if (removed) {
        emitEvent('service-descriptors-changed', manager.descriptors.snapshot())
      }
      return removed
    },
    getEgressDiagnostics() {
      return manager.getEgressDiagnostics()
    },
    getEgressSnapshots() {
      return manager.getEgressSnapshots()
    },
    getEgressMode() {
      return manager.getEgressMode()
    },
    getEgressStatus() {
      return manager.getEgressStatus()
    },
    getEgressRuntimePhases() {
      return manager.getEgressRuntimePhases()
    },
    async setEgressMode(mode, options) {
      const previous = manager.getEgressMode()
      const crossesOwnershipBoundary = (previous === 'off') !== (mode === 'off')
      if (crossesOwnershipBoundary) {
        const server = manager.getAgentServerInfo()
        if (server.status === 'running') {
          const activeSessionIds = await activeRunsProbe(server.port)
          if (activeSessionIds.length > 0) {
            if (!options?.interruptActiveRuns) {
              throw new Error('egress_mode_change_blocked_by_active_runs')
            }
            try {
              await activeRunsInterrupt(server.port, activeSessionIds)
            } catch {
              throw new Error('egress_mode_change_interrupt_failed')
            }
          }
        }
      }
      let status: EgressManagerStatus
      try {
        status = await manager.setEgressMode(mode)
      } catch (error) {
        // Crossing from model-client-managed mode must rebuild the backend even when
        // the new manager failed, so the replacement sidecar receives the
        // non-secret fail-loud marker instead of continuing on the legacy path.
        if (previous === 'off' && manager.getEgressMode() !== 'off') {
          await restartService('agent-server')
        }
        emitEvent('egress-status-changed', manager.getEgressStatus())
        throw error
      }
      if (crossesOwnershipBoundary) {
        await restartService('agent-server')
      }
      emitEvent('egress-status-changed', status)
      return status
    },
  }
}

export const createDesktopRuntimeForTest = () => createDesktopRuntime(createServiceManager())

export const serviceHandlers = (runtime: DesktopRuntime) => ({
  get_services_status: () => runtime.getServicesStatus(),
  start_all_services: () => runtime.startAllServices(),
  stop_all_services: () => runtime.stopAllServices(),
  restart_service: (_: unknown, payload?: { serviceName?: string }) =>
    runtime.restartService(payload?.serviceName ?? ''),
  get_service_logs: (_: unknown, payload?: { serviceName?: string }) =>
    runtime.getServiceLogs(payload?.serviceName ?? ''),
  get_agent_server_info: () => runtime.getAgentServerInfo(),
  desktop_shell_status: () => runtime.getShellStatus(),
  list_service_descriptors: () => runtime.listServiceDescriptors(),
  register_service_descriptor: (_: unknown, payload?: { descriptor?: ServiceDescriptor }) =>
    runtime.registerServiceDescriptor(payload?.descriptor as ServiceDescriptor),
  unregister_service_descriptor: (_: unknown, payload?: { name?: string }) =>
    runtime.unregisterServiceDescriptor(payload?.name ?? ''),
  egress_get_diagnostics: () => runtime.getEgressDiagnostics(),
  egress_get_snapshots: () => runtime.getEgressSnapshots(),
  egress_get_mode: () => runtime.getEgressMode(),
  egress_get_status: () => runtime.getEgressStatus(),
  egress_get_runtime_phases: () => runtime.getEgressRuntimePhases(),
  egress_set_mode: (
    _: unknown,
    payload?: { mode?: EgressMode; interruptActiveRuns?: boolean },
  ) => {
    const mode = payload?.mode
    if (mode !== 'auto' && mode !== 'direct' && mode !== 'off') {
      throw new Error('invalid_egress_mode')
    }
    return runtime.setEgressMode(mode, {
      interruptActiveRuns: payload?.interruptActiveRuns === true,
    })
  },
})
