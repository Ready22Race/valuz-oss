import { describe, expect, it, vi } from 'vitest'
import { createDesktopRuntime } from './services'
import { DescriptorRegistry } from '../services/descriptors'

describe('createDesktopRuntimeForTest', () => {
  it('starts required services and returns the updated snapshot', async () => {
    const runtime = createDesktopRuntime({
      descriptors: new DescriptorRegistry([]),
      startAllServices: async () => [
        {
          name: 'agent-server',
          status: 'running',
          port: 19100,
          pid: 123,
          detail: 'Ready',
        },
      ],
      stopAllServices: async () => [],
      restartService: async () => [],
      getLogs: () => [],
      getAgentServerInfo: () => ({
        port: 19100,
        status: 'running',
        token: 'test-token',
      }),
      getShellStatus: () => ({ ready: true }),
      getAllStatus: () => [],
      registerDescriptor: (descriptor) => descriptor,
      unregisterDescriptor: () => true,
      getEgressDiagnostics: () => [],
      getEgressSnapshots: () => [],
      getEgressMode: () => 'off',
      getEgressStatus: () => ({
        mode: 'off',
        enabled: false,
        started: false,
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      }),
      getEgressRuntimePhases: () => [],
      setEgressMode: async () => ({
        mode: 'off',
        enabled: false,
        started: false,
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      }),
    })

    const snapshot = await runtime.startAllServices()

    expect(snapshot[0]).toEqual(
      expect.objectContaining({
        name: 'agent-server',
        status: 'running',
      }),
    )
  })

  it('restarts the sidecar only when crossing the compatibility-mode boundary', async () => {
    let mode: 'auto' | 'direct' | 'off' = 'off'
    const restartService = vi.fn(async () => [])
    const status = () => ({
      mode,
      enabled: true,
      started: mode !== 'off',
      emergencyOverride: false,
      snapshotCount: 0,
      diagnosticEventCount: 0,
    })
    const runtime = createDesktopRuntime({
      descriptors: new DescriptorRegistry([]),
      startAllServices: async () => [],
      stopAllServices: async () => [],
      restartService,
      getLogs: () => [],
      getAgentServerInfo: () => ({
        port: 19100,
        status: 'running',
        token: 'test-token',
      }),
      getShellStatus: () => ({ ready: true }),
      getAllStatus: () => [],
      registerDescriptor: (descriptor) => descriptor,
      unregisterDescriptor: () => true,
      getEgressDiagnostics: () => [],
      getEgressSnapshots: () => [],
      getEgressMode: () => mode,
      getEgressStatus: status,
      getEgressRuntimePhases: () => [],
      setEgressMode: async (nextMode) => {
        mode = nextMode
        return status()
      },
    })

    await runtime.setEgressMode('auto')
    await runtime.setEgressMode('direct')
    await runtime.setEgressMode('off')

    expect(restartService).toHaveBeenCalledTimes(2)
    expect(restartService).toHaveBeenNthCalledWith(1, 'agent-server')
    expect(restartService).toHaveBeenNthCalledWith(2, 'agent-server')
  })
})
