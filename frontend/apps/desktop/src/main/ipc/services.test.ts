import { describe, expect, it } from 'vitest'
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
    })

    const snapshot = await runtime.startAllServices()

    expect(snapshot[0]).toEqual(
      expect.objectContaining({
        name: 'agent-server',
        status: 'running',
      }),
    )
  })
})
