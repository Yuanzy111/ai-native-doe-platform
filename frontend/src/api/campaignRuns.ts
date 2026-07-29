// Endpoint functions for the campaign-run API. Components call these; they
// never build URLs or touch `fetch` themselves.

import { httpGet, httpPost, httpPut } from './client'
import type {
  CreateCampaignRunBody,
  DesignSpaceBody,
  InitialDesignResponseDto,
  RunViewDto,
  SaveDesignSpaceResponseDto,
  ValidateResponseDto,
} from './types'

export function createCampaignRun(body: CreateCampaignRunBody): Promise<RunViewDto> {
  return httpPost<RunViewDto>('/campaign-runs', body)
}

export function getCampaignRun(runId: string): Promise<RunViewDto> {
  return httpGet<RunViewDto>(`/campaign-runs/${encodeURIComponent(runId)}`)
}

export function saveDesignSpace(
  runId: string,
  body: DesignSpaceBody,
): Promise<SaveDesignSpaceResponseDto> {
  return httpPut<SaveDesignSpaceResponseDto>(
    `/campaign-runs/${encodeURIComponent(runId)}/design-space`,
    body,
  )
}

export function validateDesignSpace(runId: string): Promise<ValidateResponseDto> {
  return httpPost<ValidateResponseDto>(
    `/campaign-runs/${encodeURIComponent(runId)}/validate`,
  )
}

// Generate the model-free first-round design. The backend runs the optimizer;
// the frontend never touches BayBE directly.
export function generateInitialDesign(runId: string): Promise<InitialDesignResponseDto> {
  return httpPost<InitialDesignResponseDto>(
    `/campaign-runs/${encodeURIComponent(runId)}/initial-design`,
  )
}
