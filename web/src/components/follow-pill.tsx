import { PresenceIcon } from "@/components/icons/brand"
import { PRESENCE, X_FOLLOW_URL } from "@/lib/presence"

const X = PRESENCE.find((p) => p.kind === "x")!

/** With no header, this is what stays on screen while you scroll: a way to follow me on X. */
export function FollowPill() {
  return (
    <a
      href={X_FOLLOW_URL}
      target="_blank"
      rel="noreferrer"
      className="cut cut-sm group fixed right-4 bottom-4 z-50 flex items-center gap-2.5 border border-neon-yellow/60 bg-background/80 px-4 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-neon-yellow backdrop-blur-md transition-all hover:bg-primary hover:text-primary-foreground hover:shadow-[0_0_28px_var(--neon-yellow)] sm:right-6 sm:bottom-6"
    >
      <span className="relative flex size-2">
        <span className="hotspot-ping absolute inline-flex size-full rounded-full bg-neon-yellow" />
        <span className="relative inline-flex size-2 rounded-full bg-neon-yellow group-hover:bg-primary-foreground" />
      </span>
      <PresenceIcon kind="x" className="size-3.5" />
      <span>
        Follow <span className="hidden sm:inline">{X.handle}</span>
      </span>
    </a>
  )
}
