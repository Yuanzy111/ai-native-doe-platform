// Endpoint functions for the conversational agent API. Components call these;
// they never build URLs or touch `fetch` themselves.
//
// Sending a message never mutates the campaign — it may stage a Pending
// proposal, which is only applied by a later approve call.

import { httpGet, httpPost } from './client'
import type { AgentThreadDto, ApproveProposalResponseDto } from './types'

export function getAgentThread(runId: string): Promise<AgentThreadDto> {
  return httpGet<AgentThreadDto>(
    `/campaign-runs/${encodeURIComponent(runId)}/agent/thread`,
  )
}

export function postAgentMessage(
  runId: string,
  message: string,
): Promise<AgentThreadDto> {
  return httpPost<AgentThreadDto>(
    `/campaign-runs/${encodeURIComponent(runId)}/agent/messages`,
    { message },
  )
}

export function approveProposal(
  runId: string,
  proposalId: string,
): Promise<ApproveProposalResponseDto> {
  return httpPost<ApproveProposalResponseDto>(
    `/campaign-runs/${encodeURIComponent(runId)}/agent/proposals/${encodeURIComponent(proposalId)}/approve`,
  )
}

export function rejectProposal(
  runId: string,
  proposalId: string,
): Promise<AgentThreadDto> {
  return httpPost<AgentThreadDto>(
    `/campaign-runs/${encodeURIComponent(runId)}/agent/proposals/${encodeURIComponent(proposalId)}/reject`,
  )
}
