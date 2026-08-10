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
    const runningService = {
      name: 'agent-server',
      status: 'running' as const,
      port: 19100,
      pid: 123,
      detail: 'Ready',
    }
    const restartService = vi.fn(async () => [runningService])
    const emitEvent = vi.fn()
    const status = () => ({
      mode,
      enabled: true,
      started: mode !== 'off',
      emergencyOverride: false,
      snapshotCount: 0,
      diagnosticEventCount: 0,
    })
    const runtime = createDesktopRuntime(
      {
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
        getAllStatus: () => [runningService],
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
      },
      emitEvent,
      async () => [],
    )

    await runtime.setEgressMode('auto')
    await runtime.setEgressMode('direct')
    await runtime.setEgressMode('off')

    expect(restartService).toHaveBeenCalledTimes(2)
    expect(restartService).toHaveBeenNthCalledWith(1, 'agent-server')
    expect(restartService).toHaveBeenNthCalledWith(2, 'agent-server')
    expect(emitEvent.mock.calls).toEqual([
      [
        'service-status-changed',
        [
          expect.objectContaining({
            name: 'agent-server',
            status: 'starting',
            pid: null,
          }),
        ],
      ],
      ['service-status-changed', [runningService]],
      ['egress-status-changed', expect.objectContaining({ mode: 'auto' })],
      ['egress-status-changed', expect.objectContaining({ mode: 'direct' })],
      [
        'service-status-changed',
        [
          expect.objectContaining({
            name: 'agent-server',
            status: 'starting',
            pid: null,
          }),
        ],
      ],
      ['service-status-changed', [runningService]],
      ['egress-status-changed', expect.objectContaining({ mode: 'off' })],
    ])
  })

  it('does not change network ownership while a model task is running', async () => {
    const runningService = {
      name: 'agent-server',
      status: 'running' as const,
      port: 19100,
      pid: 123,
      detail: 'Ready',
    }
    const restartService = vi.fn(async () => [runningService])
    const setEgressMode = vi.fn(async () => ({
      mode: 'off' as const,
      enabled: false,
      started: false,
      emergencyOverride: false,
      snapshotCount: 0,
      diagnosticEventCount: 0,
    }))
    const runtime = createDesktopRuntime(
      {
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
        getAllStatus: () => [runningService],
        registerDescriptor: (descriptor) => descriptor,
        unregisterDescriptor: () => true,
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => 'auto',
        getEgressStatus: () => ({
          mode: 'auto',
          enabled: true,
          started: true,
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        }),
        getEgressRuntimePhases: () => [],
        setEgressMode,
      },
      undefined,
      async () => ['active-session'],
    )

    await expect(runtime.setEgressMode('off')).rejects.toThrow(
      'egress_mode_change_blocked_by_active_runs',
    )
    expect(setEgressMode).not.toHaveBeenCalled()
    expect(restartService).not.toHaveBeenCalled()
  })

  it('interrupts active tasks before changing network ownership when confirmed', async () => {
    let mode: 'auto' | 'direct' | 'off' = 'auto'
    const runningService = {
      name: 'agent-server',
      status: 'running' as const,
      port: 19100,
      pid: 123,
      detail: 'Ready',
    }
    const restartService = vi.fn(async () => [runningService])
    const interrupt = vi.fn(async () => undefined)
    const setEgressMode = vi.fn(async (nextMode: typeof mode) => {
      mode = nextMode
      return {
        mode,
        enabled: mode !== 'off',
        started: mode !== 'off',
        emergencyOverride: false,
        snapshotCount: 0,
        diagnosticEventCount: 0,
      }
    })
    const runtime = createDesktopRuntime(
      {
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
        getAllStatus: () => [runningService],
        registerDescriptor: (descriptor) => descriptor,
        unregisterDescriptor: () => true,
        getEgressDiagnostics: () => [],
        getEgressSnapshots: () => [],
        getEgressMode: () => mode,
        getEgressStatus: () => ({
          mode,
          enabled: mode !== 'off',
          started: mode !== 'off',
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        }),
        getEgressRuntimePhases: () => [],
        setEgressMode,
      },
      undefined,
      async () => ['session-a', 'session-b'],
      interrupt,
    )

    await expect(
      runtime.setEgressMode('off', { interruptActiveRuns: true }),
    ).resolves.toMatchObject({ mode: 'off' })
    expect(interrupt).toHaveBeenCalledWith(19100, ['session-a', 'session-b'])
    expect(interrupt.mock.invocationCallOrder[0]).toBeLessThan(
      setEgressMode.mock.invocationCallOrder[0],
    )
    expect(restartService).toHaveBeenCalledWith('agent-server')
  })
})
