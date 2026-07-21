import { useEffect, useState } from 'react'

import { agentsApi, type Agent } from '../api/agents-api'
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
 * Load the temporary-conversation agent library from the execution target
 * selected in the composer. Target changes synchronously hide the previous
 * roster, and cleanup prevents a slower obsolete response from replacing the
 * active target's agents.
 */
export function useComposerAgentLibrary(
  baseUrl?: string,
  refreshKey?: string | number | null,
): ComposerAgentLibrary {
  const requestKey = `${baseUrl ?? ''}\u0000${refreshKey ?? ''}`
  const [state, setState] = useState<ComposerAgentLibraryState>({
    requestKey,
    agents: [],
    loaded: false,
  })

  useEffect(() => {
    let active = true

    void agentsApi
      .listAgents(undefined, { baseUrl, fresh: true })
      .then(({ agents }) => {
        if (active) setState({ requestKey, agents, loaded: true })
      })
      .catch(() => {
        if (active) setState({ requestKey, agents: [], loaded: true })
      })

    return () => {
      active = false
    }
  }, [baseUrl, requestKey])

  if (state.requestKey !== requestKey) {
    return { agents: [], loaded: false }
  }
  return { agents: state.agents, loaded: state.loaded }
}
