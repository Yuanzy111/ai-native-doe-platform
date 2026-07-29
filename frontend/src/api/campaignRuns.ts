// Endpoint functions for the campaign-run API. Components call these; they
// never build URLs or touch `fetch` themselves.

import { httpGet, httpPost, httpPut } from './client'
import type {
  CreateCampaignRunBody,
  DesignSpaceBody,
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
