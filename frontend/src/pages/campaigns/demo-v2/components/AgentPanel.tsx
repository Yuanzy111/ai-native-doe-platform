import { useEffect, useRef } from 'react'
import type { AgentMessageDto, AgentProposalDto } from '../../../../api/types'
import { canApproveProposal, canSendMessage, describeProposal, isProposalStale } from '../agentState'

interface Props {
  experimentSummary: string
  messages: AgentMessageDto[]
  pendingProposals: AgentProposalDto[]
  draft: string
  sending: boolean
  actioningProposalId: string | null
  // True once the run's lifecycle has advanced past validation. Modification
  // proposals cannot be approved while frozen (the agent stays read/explain).
  frozen: boolean
  // Unsaved local design-space edits: approving would clobber them, so Approve
  // is disabled with a save-first hint until they are persisted.
  dirty: boolean
  // The run's current revision id (null before first save). A proposal pinned to
  // a different revision is stale and cannot be approved.
  currentRevisionId: string | null
  // The run's current version token (its updatedAt, null before first save). A
  // proposal pinned to a different token is stale even if the revision matches.
  currentRunUpdatedAt: string | null
  errorMessage: string | null
  onDraftChange: (value: string) => void
  onSend: () => void
  onApprove: (proposalId: string) => void
  onReject: (proposalId: string) => void
}

function ProposalCard({
  proposal,
  frozen,
  dirty,
  currentRevisionId,
  currentRunUpdatedAt,
  actioning,
  onApprove,
  onReject,
}: {
  proposal: AgentProposalDto
  frozen: boolean
  dirty: boolean
  currentRevisionId: string | null
  currentRunUpdatedAt: string | null
  actioning: boolean
  onApprove: (proposalId: string) => void
  onReject: (proposalId: string) => void
}) {
  const summary = describeProposal(proposal)
  // The disable decision is the single source of truth in `canApproveProposal`;
  // the individual predicates below only pick which hint to show.
  const stale = isProposalStale(proposal, currentRevisionId, currentRunUpdatedAt)
  const approveDisabled =
    actioning ||
    !canApproveProposal({
      proposal,
      frozen,
      dirty,
      currentRevisionId,
      currentRunUpdatedAt,
    })
  return (
    <div className="rounded border border-indigo-200 bg-indigo-50 px-3 py-2.5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-indigo-600">
        Proposed action
      </p>
      <p className="mt-1 text-sm font-medium text-indigo-900">
        {summary.title}
        {stale && (
          <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
            Stale
          </span>
        )}
      </p>
      {summary.lines.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 text-xs text-indigo-800">
          {summary.lines.map((line, index) => (
            <li key={`${proposal.id}-line-${index}`} className="font-mono">
              {line}
            </li>
          ))}
        </ul>
      )}
      {frozen && (
        <p className="mt-2 text-xs text-amber-700">
          The run is frozen; this proposal can no longer be approved.
        </p>
      )}
      {stale && !frozen && (
        <p className="mt-2 text-xs text-amber-700">
          The design space changed after this proposal; reject it and ask again.
        </p>
      )}
      {dirty && !stale && !frozen && (
        <p className="mt-2 text-xs text-amber-700">
          Save your design-space changes before approving this proposal.
        </p>
      )}
      <div className="mt-2.5 flex items-center gap-2">
        <button
          type="button"
          disabled={approveDisabled}
          onClick={() => onApprove(proposal.id)}
          className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Run
        </button>
        <button
          type="button"
          disabled={actioning}
          onClick={() => onReject(proposal.id)}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

export default function AgentPanel({
  experimentSummary,
  messages,
  pendingProposals,
  draft,
  sending,
  actioningProposalId,
  frozen,
  dirty,
  currentRevisionId,
  currentRunUpdatedAt,
  errorMessage,
  onDraftChange,
  onSend,
  onApprove,
  onReject,
}: Props) {
  const canSend = canSendMessage(draft, sending)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages, pendingProposals])

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (canSend) onSend()
    }
  }

  return (
    <aside className="flex w-[360px] shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="flex h-12 shrink-0 items-center border-b border-slate-200 px-4">
        <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
        <h2 className="ml-2 text-sm font-semibold text-slate-800">AI Agent</h2>
      </div>

      <div className="border-b border-slate-100 px-4 py-3">
        <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          Experiment Summary
        </h3>
        <p className="text-sm leading-relaxed text-slate-700">{experimentSummary}</p>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3">
        {messages.length === 0 ? (
          <p className="text-sm text-slate-400">
            Ask the agent to help configure the design space, then approve the changes it proposes.
          </p>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {messages.map((message) => (
              <li
                key={message.id}
                className={message.role === 'user' ? 'flex justify-end' : 'flex justify-start'}
              >
                <div
                  className={`max-w-[85%] whitespace-pre-wrap rounded px-3 py-2 text-sm ${
                    message.role === 'user'
                      ? 'bg-indigo-600 text-white'
                      : 'bg-slate-100 text-slate-800'
                  }`}
                >
                  {message.content}
                </div>
              </li>
            ))}
          </ul>
        )}

        {pendingProposals.length > 0 && (
          <div className="mt-3 flex flex-col gap-2">
            {pendingProposals.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                frozen={frozen}
                dirty={dirty}
                currentRevisionId={currentRevisionId}
                currentRunUpdatedAt={currentRunUpdatedAt}
                actioning={actioningProposalId === proposal.id}
                onApprove={onApprove}
                onReject={onReject}
              />
            ))}
          </div>
        )}
      </div>

      {errorMessage !== null && (
        <div className="border-t border-red-200 bg-red-50 px-4 py-2.5">
          <p className="text-xs text-red-700">{errorMessage}</p>
        </div>
      )}

      <div className="border-t border-slate-200 px-4 py-3">
        <textarea
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={handleKeyDown}
          rows={3}
          placeholder="Describe what you want to change…"
          className="w-full resize-none rounded border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none"
        />
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            disabled={!canSend}
            onClick={onSend}
            className="rounded bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </aside>
  )
}
