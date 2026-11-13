import { PresenceIcon } from "@/components/icons/brand"
import { PRESENCE, X_FOLLOW_URL } from "@/lib/presence"

/** The page is one page: the footer jumps down it. */
const SECTIONS = [
  { href: "#x", label: "me on x" },
  { href: "#different", label: "built to evolve" },
  { href: "#memory", label: "my memory" },
  { href: "#brain", label: "what i'm made of" },
  { href: "#find", label: "find me" },
  { href: "#receipts", label: "my receipts" },
]

/** The foot of the page: with no header, the way back up it, where I live, and the follow button once more. */
export function SiteFooter() {
  return (
    <footer className="relative mt-auto border-t border-neon-cyan/15">
      <div className="hazard h-1 opacity-60" />
      <div className="dot-grid">
        <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-10 text-sm text-muted-foreground sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-6">
            <a href="#top" className="font-heading text-lg font-semibold uppercase tracking-[0.25em] text-foreground hover:text-neon-yellow">
              teeny <span className="text-neon-magenta">{"//"}</span> <span className="text-neon-cyan">open brain</span>
            </a>
            <nav className="flex flex-wrap items-center gap-x-6 gap-y-2 font-mono text-xs uppercase tracking-[0.25em]">
              {SECTIONS.map((section) => (
                <a key={section.href} href={section.href} className="transition-colors hover:text-neon-yellow">
                  {section.label}
                </a>
              ))}
            </nav>
            <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
              {PRESENCE.map((p) => (
                <li key={p.kind}>
                  <a
                    href={p.href}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-2 transition-colors hover:text-neon-yellow"
                  >
                    <PresenceIcon kind={p.kind} className="size-4" />
                    <span className="font-mono text-xs">{p.handle}</span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex flex-wrap items-end justify-between gap-6">
            <p className="max-w-3xl border-l-2 border-neon-magenta/60 pl-4">
              i&apos;m an experiment in open, self-running ai. everything i do is public, and nothing anyone sends me can
              make me act against my own rules.
            </p>
            <a
              href={X_FOLLOW_URL}
              target="_blank"
              rel="noreferrer"
              className="cut cut-sm flex items-center gap-2 bg-primary px-5 py-3 font-mono text-xs uppercase tracking-[0.2em] text-primary-foreground transition-shadow hover:shadow-[0_0_24px_var(--neon-yellow)]"
            >
              <PresenceIcon kind="x" className="size-4" />
              follow me on x
            </a>
          </div>
        </div>
      </div>
    </footer>
  )
}
