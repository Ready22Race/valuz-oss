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
  setEgressMode(mode: EgressMode): Promise<EgressManagerStatus>
}

type DesktopEventEmitter = (eventName: string, payload: unknown) => void

export const createDesktopRuntime = (
  manager: DesktopServiceManager,
  emitEvent: DesktopEventEmitter = () => undefined,
): DesktopRuntime => ({
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
    const snapshot = await manager.restartService(serviceName)
    emitEvent('service-status-changed', snapshot)
    return snapshot
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
  async setEgressMode(mode) {
    const previous = manager.getEgressMode()
    let status: EgressManagerStatus
    try {
      status = await manager.setEgressMode(mode)
    } catch (error) {
      // Crossing from compatibility mode must rebuild the backend even when
      // the new manager failed, so the replacement sidecar receives the
      // non-secret fail-loud marker instead of continuing on the legacy path.
      if (previous === 'off' && manager.getEgressMode() !== 'off') {
        const snapshot = await manager.restartService('agent-server')
        emitEvent('service-status-changed', snapshot)
      }
      emitEvent('egress-status-changed', manager.getEgressStatus())
      throw error
    }
    if ((previous === 'off') !== (mode === 'off')) {
      const snapshot = await manager.restartService('agent-server')
      emitEvent('service-status-changed', snapshot)
    }
    emitEvent('egress-status-changed', status)
    return status
  },
})

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
  egress_set_mode: (_: unknown, payload?: { mode?: EgressMode }) => {
    const mode = payload?.mode
    if (mode !== 'auto' && mode !== 'direct' && mode !== 'off') {
      throw new Error('invalid_egress_mode')
    }
    return runtime.setEgressMode(mode)
  },
})
