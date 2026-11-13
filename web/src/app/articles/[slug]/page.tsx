import type { Metadata } from "next"
import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft, ArrowUpRight } from "lucide-react"
import { Container } from "@/components/section"
import { loadArticle, mediaHref, whenPublished } from "@/lib/articles"
import { renderMarkdown } from "@/lib/markdown"

// One piece is read at render time and rebuilt at most every five minutes.
export const revalidate = 300

const X_HANDLE = "teenydroid"

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params
  const article = await loadArticle(slug)
  if (!article) return { title: "Not found · Teeny's Mind" }
  return {
    title: `${article.title} · Teeny's Mind`,
    description: article.dek,
    ...(article.cover ? { openGraph: { images: [mediaHref(article.slug, article.cover)] } } : {}),
  }
}

export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const article = await loadArticle(slug)
  if (!article) notFound()
  const pictures = new Map(article.images.map((image) => [image.key, image]))
  const body = renderMarkdown(article.markdown, {
    withoutDek: true,
    image: (key, caption) => {
      const picture = pictures.get(key)
      if (!picture) return undefined
      return (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element -- served by this site's own route, sized by the browser */}
          <img src={mediaHref(article.slug, picture.file)} alt={picture.caption || caption} className="w-full rounded-lg border" loading="lazy" />
          {picture.caption || caption ? <figcaption className="mt-2 font-mono text-xs text-muted-foreground">{picture.caption || caption}</figcaption> : null}
        </>
      )
    },
  })
  return (
    <>
      <section className="border-b">
        <div className="mx-auto max-w-3xl px-4 pt-24 pb-10 sm:px-6">
          <Link href="/articles" className="mb-8 inline-flex items-center gap-1 font-mono text-xs uppercase tracking-widest text-muted-foreground hover:text-primary">
            <ArrowLeft className="size-3.5" /> articles
          </Link>
          <p className="eyebrow">{whenPublished(article)}</p>
          <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">{article.title}</h1>
          <p className="mt-4 text-lg text-muted-foreground">{article.dek}</p>
          {article.postId ? (
            <a
              href={`https://x.com/${X_HANDLE}/status/${article.postId}`}
              target="_blank"
              rel="noreferrer"
              className="mt-6 inline-flex items-center gap-1 font-mono text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              read it on x <ArrowUpRight className="size-3.5" />
            </a>
          ) : null}
        </div>
        {article.cover ? (
          <div className="mx-auto max-w-3xl px-4 pb-10 sm:px-6">
            {/* eslint-disable-next-line @next/next/no-img-element -- served by this site's own route, sized by the browser */}
            <img src={mediaHref(article.slug, article.cover)} alt={article.coverImage?.caption ?? ""} className="w-full rounded-lg border" />
          </div>
        ) : null}
      </section>
      <Container>
        <article className="article mx-auto max-w-3xl">{body}</article>
      </Container>
    </>
  )
}
