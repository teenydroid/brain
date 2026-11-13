'use client'

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Menu } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { PRESENCE, SOCIAL } from "@/lib/presence"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { PAGES } from "@/lib/pages"
import { cn } from "@/lib/utils"

const SOURCE = PRESENCE.find((p) => p.kind === "github")!

/** The mark: a small neon core with a split cyan/magenta ghost. */
function Mark() {
  return (
    <span className="relative flex size-6 items-center justify-center">
      <span className="absolute inset-0 rotate-45 border border-neon-cyan/70" />
      <span className="absolute inset-1 rotate-45 border border-neon-magenta/60 translate-x-px" />
      <span className="size-2 rotate-45 bg-neon-yellow shadow-[0_0_12px_var(--neon-yellow)]" />
    </span>
  )
}

export function SiteNav() {
  const pathname = usePathname()
  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-neon-cyan/15 bg-background/70 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="group flex items-center gap-3 font-heading text-base font-semibold uppercase tracking-[0.2em]">
          <Mark />
          <span className="glitch" data-text="teeny">teeny</span>
          <span className="font-mono text-xs font-normal tracking-widest text-neon-cyan/70">/ mind</span>
        </Link>

        <nav className="hidden items-center gap-0.5 lg:flex">
          {PAGES.map((page, i) => {
            const active = pathname === page.slug
            return (
              <Link
                key={page.slug}
                href={page.slug}
                className={cn(
                  "group relative px-3 py-2 font-mono text-[0.7rem] uppercase tracking-[0.2em] text-muted-foreground transition-colors hover:text-neon-yellow",
                  active && "text-neon-yellow",
                )}
              >
                <span className="mr-1 text-neon-magenta/70">{String(i + 1).padStart(2, "0")}</span>
                {page.label}
                <span
                  className={cn(
                    "absolute inset-x-3 -bottom-px h-px origin-left scale-x-0 bg-neon-yellow shadow-[0_0_8px_var(--neon-yellow)] transition-transform group-hover:scale-x-100",
                    active && "scale-x-100",
                  )}
                />
              </Link>
            )
          })}
          <span className="mx-2 h-5 w-px bg-neon-cyan/20" />
          {SOCIAL.map((p) => (
            <Button
              key={p.kind}
              variant="ghost"
              size="icon"
              title={`${p.place} · ${p.handle}`}
              render={<a href={p.href} target="_blank" rel="noreferrer" />}
            >
              <PresenceIcon kind={p.kind} className="size-4" />
              <span className="sr-only">{p.place}</span>
            </Button>
          ))}
          <Button variant="outline" size="sm" className="ml-1" render={<a href={SOURCE.href} target="_blank" rel="noreferrer" />}>
            <PresenceIcon kind="github" className="size-4" />
            Source
          </Button>
        </nav>

        <Sheet>
          <SheetTrigger render={<Button variant="ghost" size="icon" className="lg:hidden" />}>
            <Menu className="size-5" />
            <span className="sr-only">Open menu</span>
          </SheetTrigger>
          <SheetContent side="right" className="w-72 border-neon-cyan/20 bg-background/95">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <nav className="mt-8 flex flex-col gap-1 px-4">
              {PAGES.map((page, i) => (
                <Link
                  key={page.slug}
                  href={page.slug}
                  className={cn(
                    "flex items-center gap-3 border-l-2 border-transparent px-3 py-2 font-mono text-sm uppercase tracking-widest text-muted-foreground hover:border-neon-yellow hover:text-neon-yellow",
                    pathname === page.slug && "border-neon-yellow text-neon-yellow",
                  )}
                >
                  <span className="text-xs text-neon-magenta/70">{String(i + 1).padStart(2, "0")}</span>
                  {page.label}
                </Link>
              ))}
              <p className="eyebrow mt-6 px-3">Find it</p>
              {PRESENCE.map((p) => (
                <Button
                  key={p.kind}
                  variant="ghost"
                  className="justify-start"
                  render={<a href={p.href} target="_blank" rel="noreferrer" />}
                >
                  <PresenceIcon kind={p.kind} className="size-4" />
                  <span>{p.place}</span>
                  <span className="ml-auto font-mono text-xs text-muted-foreground">{p.handle}</span>
                </Button>
              ))}
            </nav>
          </SheetContent>
        </Sheet>
      </div>
    </header>
  )
}
