import { useEffect, useState } from 'react'

import type { Agent } from '../api/agents-api'
import { getComposerCatalogAdapter } from '../edition/composer-catalog'
import { useAgentStore } from '../store/agent-store'

export const useAgent = () => useAgentStore()

export interface ComposerAgentLibrary {
  agents: Agent[]
  loaded: boolean
}

interface ComposerAgentLibraryState extends ComposerAgentLibrary {
  requestKey: string
}

/**
 * Load the temporary-conversation agent library through the active edition's
 * catalog adapter. OSS treats ``targetId`` as opaque; only an installed edition
 * adapter may interpret it. Scope changes synchronously hide the previous
 * roster, and cleanup prevents an obsolete response from replacing the active
 * scope's agents.
 */
export function useComposerAgentLibrary(
  targetId?: string | null,
  refreshKey?: string | number | null,
): ComposerAgentLibrary {
  const adapter = getComposerCatalogAdapter()
  const scopeKey = adapter.getScopeKey({ targetId })
  const requestKey = `${scopeKey}\u0000${refreshKey ?? ''}`
  const [state, setState] = useState<ComposerAgentLibraryState>({
    requestKey,
    agents: [],
    loaded: false,
  })

  useEffect(() => {
    let active = true

    void adapter
      .listAgents({ targetId })
      .then(({ agents }) => {
        if (active) setState({ requestKey, agents, loaded: true })
      })
      .catch(() => {
        if (active) setState({ requestKey, agents: [], loaded: true })
      })

    return () => {
      active = false
    }
  }, [adapter, requestKey, targetId])

  if (state.requestKey !== requestKey) {
    return { agents: [], loaded: false }
  }
  return { agents: state.agents, loaded: state.loaded }
}
