import Link from "next/link"
import { PresenceIcon } from "@/components/icons/brand"
import { PAGES } from "@/lib/pages"
import { PRESENCE } from "@/lib/presence"

/** The foot of every page, and with no header, its only way around: home, the pages, and where Teeny lives. */
export function SiteFooter() {
  return (
    <footer className="relative mt-auto border-t border-neon-cyan/15">
      <div className="hazard h-1 opacity-60" />
      <div className="dot-grid">
        <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-10 text-sm text-muted-foreground sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-6">
            <Link href="/" className="font-heading text-lg font-semibold uppercase tracking-[0.25em] text-foreground hover:text-neon-yellow">
              teeny <span className="text-neon-magenta">{"//"}</span> <span className="text-neon-cyan">open brain</span>
            </Link>
            <nav className="flex flex-wrap items-center gap-x-6 gap-y-2 font-mono text-xs uppercase tracking-[0.25em]">
              {PAGES.map((page) => (
                <Link key={page.slug} href={page.slug} className="transition-colors hover:text-neon-yellow">
                  {page.label}
                </Link>
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
          <p className="max-w-3xl border-l-2 border-neon-magenta/60 pl-4">
            Teeny is a community mascot. It never promotes another token, never takes a wallet
            action, and never predicts a price. Nothing here is financial advice.
          </p>
        </div>
      </div>
    </footer>
  )
}
