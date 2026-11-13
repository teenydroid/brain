import { ArrowUpRight } from "lucide-react"
import { PresenceIcon } from "@/components/icons/brand"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Container } from "@/components/section"
import { PRESENCE } from "@/lib/presence"

/** Where Teeny can be found, one card per place. */
export function PresenceSection() {
  return (
    <section className="border-b border-neon-cyan/10">
      <Container>
        <p className="eyebrow">find me</p>
        <h2 className="mb-8 text-2xl font-semibold uppercase tracking-wide sm:text-4xl">
          one mind, a few places.
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {PRESENCE.map((p) => (
            <a
              key={p.kind}
              href={p.href}
              target="_blank"
              rel="noreferrer"
              className="group rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Card className="h-full gap-3 transition-colors group-hover:border-primary/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-3 text-base">
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted/40 text-foreground transition-colors group-hover:border-primary/50 group-hover:text-primary">
                      <PresenceIcon kind={p.kind} className="size-4" />
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="text-xs font-normal text-muted-foreground">{p.place}</span>
                      <span className="truncate font-mono">{p.handle}</span>
                    </span>
                    <ArrowUpRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary" />
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <CardDescription className="leading-relaxed">{p.blurb}</CardDescription>
                  {p.detail && (
                    <p className="mt-2 text-xs text-muted-foreground/80">{p.detail}</p>
                  )}
                </CardContent>
              </Card>
            </a>
          ))}
        </div>
      </Container>
    </section>
  )
}
