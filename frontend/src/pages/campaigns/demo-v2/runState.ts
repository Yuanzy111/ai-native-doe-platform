// Pure state logic for the design-space page. Kept free of React so it can be
// unit-tested directly and reused by the header and the workspace.

import type { RunStatus } from '../../../api/types'

// The status shown in the header. The server status is authoritative, but an
// unsaved edit against a validated run is surfaced as Draft immediately, since
// any change drops the run back to Draft on the next save (§3.6).
export function displayStatus(
  serverStatus: RunStatus | null,
  dirty: boolean,
): string {
  if (serverStatus === null) return 'Draft'
  if (dirty && serverStatus === 'DesignSpaceValidated') return 'Draft'
  return serverStatus
}

// Generate Initial Design is only reachable once the server has validated the
// current design space, there is no unsaved edit pending, and no recommendation
// batch has been generated yet. The `hasBatch` guard is derived from the server
// aggregate, not a local flag, so the gate stays consistent with backend state.
export function canGenerateInitialDesign(
  serverStatus: RunStatus | null,
  dirty: boolean,
  hasBatch = false,
): boolean {
  return serverStatus === 'DesignSpaceValidated' && !dirty && !hasBatch
}

// The design space freezes once the run leaves DesignSpaceValidated — i.e. the
// moment an initial design exists. Derived from the authoritative server status
// so the lock never disagrees with the backend after a reload.
export function isLifecycleLocked(serverStatus: RunStatus | null): boolean {
  return (
    serverStatus === 'RecommendationsPending' ||
    serverStatus === 'AwaitingMeasurements' ||
    serverStatus === 'RoundClosed' ||
    serverStatus === 'Completed' ||
    serverStatus === 'Archived'
  )
}
