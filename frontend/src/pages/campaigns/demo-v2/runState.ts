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
// current design space and there is no unsaved edit pending.
export function canGenerateInitialDesign(
  serverStatus: RunStatus | null,
  dirty: boolean,
): boolean {
  return serverStatus === 'DesignSpaceValidated' && !dirty
}
