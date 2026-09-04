import toast from 'react-hot-toast'

/**
 * Feedback after sending leads to the Research Agent. On a real queue,
 * offers a one-click jump to that session's live view.
 *   res  — the handoff response: { session_id, queued, skipped }
 *   navigate — react-router useNavigate()
 */
export function researchSentToast(res, navigate) {
  const skipped = (res?.skipped || []).length
  if (!res?.queued) {
    toast(skipped ? `Nothing queued — ${skipped} already queued, done, or excluded` : 'Nothing to send')
    return
  }
  toast.success((t) => (
    <span className="flex items-center gap-3">
      <span>Sent {res.queued} lead{res.queued === 1 ? '' : 's'} to the Research Agent</span>
      {res.session_id && (
        <button
          className="text-xs font-semibold text-brand-300 hover:text-brand-200 underline underline-offset-2"
          onClick={() => { toast.dismiss(t.id); navigate(`/research-agent?session=${res.session_id}`) }}
        >
          View in Research Agent →
        </button>
      )}
    </span>
  ), { duration: 6000 })
}
