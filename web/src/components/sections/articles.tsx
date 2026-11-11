import { ArrowUpRight } from "lucide-react"
import Link from "next/link"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Container } from "@/components/section"
import { mediaHref, whenPublished, type ArticleList } from "@/lib/articles"

/** The bot could not be reached, so the articles could not be read. Said, not papered over. */
function Unavailable() {
  return (
    <Card>
      <CardContent className="py-8 text-center text-sm text-muted-foreground">
        The bot is not reachable right now, so the articles cannot be read.
      </CardContent>
    </Card>
  )
}

function Nothing() {
  return (
    <Card>
      <CardContent className="py-8 text-center text-sm text-muted-foreground">
        Nothing published yet. The first one is on its way.
      </CardContent>
    </Card>
  )
}

export function ArticlesSection({ list }: { list: ArticleList }) {
  return (
    <Container>
      {list.source === "unavailable" ? (
        <Unavailable />
      ) : list.articles.length === 0 ? (
        <Nothing />
      ) : (
        <ul className="grid gap-6 sm:grid-cols-2">
          {list.articles.map((article) => (
            <li key={article.slug}>
              <Link href={`/articles/${article.slug}`} className="group block h-full">
                <Card className="h-full gap-4 overflow-hidden transition-colors group-hover:border-primary/60">
                  {article.cover ? (
                    // eslint-disable-next-line @next/next/no-img-element -- served by this site's own route, sized by the browser
                    <img
                      src={mediaHref(article.slug, article.cover)}
                      alt=""
                      className="aspect-[3/2] w-full object-cover"
                      loading="lazy"
                    />
                  ) : null}
                  <CardHeader>
                    <p className="font-mono text-xs uppercase tracking-widest text-primary">{whenPublished(article)}</p>
                    <CardTitle className="flex items-start gap-2 text-lg leading-snug">
                      <span>{article.title}</span>
                      <ArrowUpRight className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                    </CardTitle>
                    <CardDescription className="leading-relaxed">{article.dek}</CardDescription>
                  </CardHeader>
                  <CardContent className="mt-auto font-mono text-xs text-muted-foreground">
                    {[typeof article.wordCount === "number" ? `${article.wordCount} words` : undefined, article.postId ? "on x" : undefined]
                      .filter(Boolean)
                      .join(" · ")}
                  </CardContent>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Container>
  )
}
