/**
 * Gate for the project-detail send handoff.
 *
 * ``/conversation/new?project=A`` carries the project in the URL, but
 * ``ensureSession`` mints from ``selectedProjectId`` — state that bootstrap
 * only fills after it has fetched and validated the project list. Sending
 * before that lands means ``sessionProjectId`` falls back to
 * ``"chat-default"``, which mints a QUICK CHAT: not bound to the project, and
 * routed by the chat target picker instead of the project's execution origin.
 * The user sees a session that jumped correctly and then behaves as 本地 even
 * for a 云端服务 project.
 *
 * Extracted so the condition is testable — the page itself has no harness.
 */
export function canSendProjectHandoff(params: {
  /** ``?project=`` on the URL, or null for a non-project entry. */
  projectParam: string | null;
  /** Bootstrap's validated binding, null until it resolves. */
  selectedProjectId: string | null;
}): boolean {
  const { projectParam, selectedProjectId } = params;
  // No project in the URL: nothing to wait for (temp / quick chat).
  if (!projectParam) return true;
  return selectedProjectId === projectParam;
}
