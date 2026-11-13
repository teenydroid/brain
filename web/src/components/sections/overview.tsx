import Link from "next/link"
import { ArrowRight } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Container } from "@/components/section"
import { PAGES } from "@/lib/pages"

export function Overview() {
  return (
    <Container>
      <p className="eyebrow">Inside</p>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {PAGES.map((page) => (
          <Link key={page.slug} href={page.slug} className="group rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Card className="h-full gap-3 transition-colors group-hover:border-primary/50">
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-base">
                  {page.label}
                  <ArrowRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
                </CardTitle>
              </CardHeader>
              <CardContent>
                <CardDescription className="leading-relaxed">{page.title}. {page.lede.split(". ")[0]}.</CardDescription>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </Container>
  )
}
