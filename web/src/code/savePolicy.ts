/**
 * External-change policy for the editor (PURE, tested — handoff §6.5, trap
 * T4): the subtle part of the code pane. `plot.py` can change on disk while
 * the editor holds a buffer (agent wrote it, user's external editor, or the
 * echo of our own PUT through the watcher).
 *
 * - disk === buffer → no-op (own-write echo: PUT → watcher fires → GET
 *   returns what we just wrote; never setValue on identical content).
 * - buffer clean (== last saved), disk differs → reload silently.
 * - buffer dirty AND differs → banner [Reload] [Overwrite]; agent output is
 *   never silently clobbered, and neither is the user's edit (UX §5).
 */

export type ExternalPolicy = "noop" | "reload" | "banner";

export function externalChangePolicy(
  buffer: string,
  disk: string,
  dirty: boolean,
): ExternalPolicy {
  if (disk === buffer) return "noop";
  return dirty ? "banner" : "reload";
}
