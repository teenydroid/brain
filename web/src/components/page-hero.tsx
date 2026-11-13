import Link from 'next/link'
import { SceneCanvas } from '@/components/scene'
import type { PageMeta } from '@/lib/pages'
import { PAGES } from '@/lib/pages'

export function PageHero({ page }: { page: PageMeta }) {
  const index = PAGES.findIndex((p) => p.slug === page.slug)
  return (
    <section className="relative px-2 pt-2 sm:px-4 sm:pt-4">
      <div
        className="cut dot-grid relative overflow-hidden border border-neon-cyan/20 bg-[#06060b]/80"
        style={{ '--cut': '22px' } as React.CSSProperties}
      >
        <SceneCanvas name={page.scene} className="absolute inset-y-0 right-0 h-full w-full lg:w-3/5" />
        {/* Keeps the text legible over whatever the scene is doing. */}
        <div className="pointer-events-none absolute inset-0 bg-linear-to-r from-[#06060b] via-[#06060b]/70 to-transparent lg:via-[#06060b]/30" />
        <div className="pointer-events-none absolute inset-0 bg-linear-to-t from-[#06060b] via-[#06060b]/50 to-transparent lg:hidden" />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-linear-to-r from-neon-yellow via-neon-magenta to-transparent" />
        <div className="relative mx-auto flex min-h-[52vh] max-w-7xl flex-col justify-end px-5 pt-20 pb-14 sm:px-8 sm:pb-20">
          <Link
            href="/"
            className="absolute top-5 left-5 font-mono text-[0.65rem] uppercase tracking-[0.3em] text-neon-cyan/80 transition-colors hover:text-neon-yellow sm:top-7 sm:left-8"
          >
            &larr; teeny <span className="text-neon-magenta">{'//'}</span> mind
          </Link>
          <p className="mb-6 font-mono text-[0.65rem] uppercase tracking-[0.3em] text-muted-foreground">
            <span className="text-neon-magenta">{String(index + 1).padStart(2, '0')}</span>
            <span className="mx-2 text-neon-cyan/50">/</span>
            {String(PAGES.length).padStart(2, '0')}
          </p>
          <p className="eyebrow">{page.eyebrow}</p>
          <h1 data-text={page.title} className="glitch max-w-4xl text-4xl leading-[1.05] font-semibold uppercase sm:text-6xl">
            {page.title}
          </h1>
          <p className="mt-5 max-w-2xl text-lg text-muted-foreground">{page.lede}</p>
        </div>
      </div>
    </section>
  )
}
