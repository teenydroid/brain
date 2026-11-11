import { apiUrl, readApi } from './api'

/**
 * Teeny's articles, read from the bot over the compose network. The bot
 * serves the published ones at /articles and the whole piece at
 * /articles/<slug>; the pictures are files beside the record and come
 * through this site's own route so a browser never needs the bot's
 * address.
 */

export interface ArticleSummary {
  slug: string
  title: string
  dek: string
  status: string
  createdAt: string
  publishedAt?: string
  postId?: string
  cover?: string
  wordCount: number
}

export interface ArticleImage {
  key: string
  file: string
  caption: string
}

export interface Article extends ArticleSummary {
  engine?: string
  markdown: string
  coverImage?: ArticleImage
  images: ArticleImage[]
}

export interface ArticleList {
  source: 'live' | 'unavailable'
  articles: ArticleSummary[]
}

export const SLUG = /^[a-z0-9-]+$/
export const IMAGE_FILE = /^[a-z0-9-]+\.(png|jpg|webp)$/

export async function loadArticles(): Promise<ArticleList> {
  const body = await readApi<{ articles: ArticleSummary[] }>('/articles', {
    revalidate: 300,
    check: (value) => Array.isArray((value as { articles?: unknown }).articles),
  })
  return body ? { source: 'live', articles: body.articles } : { source: 'unavailable', articles: [] }
}

export async function loadArticle(slug: string): Promise<Article | undefined> {
  if (!SLUG.test(slug)) return undefined
  const body = await readApi<Record<string, unknown>>(`/articles/${slug}`, {
    revalidate: 300,
    check: (value) => typeof (value as { markdown?: unknown }).markdown === 'string',
  })
  return body ? toArticle(body) : undefined
}

/** One article as the bot serves it at /articles/<slug>. */
export function toArticle(body: Record<string, unknown>): Article {
  const cover = body.cover as ArticleImage | undefined
  return {
    slug: String(body.slug),
    title: String(body.title),
    dek: String(body.dek),
    status: String(body.status),
    createdAt: String(body.createdAt),
    ...(typeof body.publishedAt === 'string' ? { publishedAt: body.publishedAt } : {}),
    ...(typeof body.postId === 'string' ? { postId: body.postId } : {}),
    ...(cover ? { cover: cover.file, coverImage: cover } : {}),
    wordCount: Number(body.wordCount) || 0,
    ...(typeof body.engine === 'string' ? { engine: body.engine } : {}),
    markdown: String(body.markdown),
    images: Array.isArray(body.images) ? (body.images as ArticleImage[]) : [],
  }
}

/** Where a picture is served from on this site. */
export function mediaHref(slug: string, file: string): string {
  return `/articles/${slug}/media/${file}`
}

/** Where the bot serves it from, for the route that proxies it. */
export function mediaSource(slug: string, file: string): string | undefined {
  if (!SLUG.test(slug) || !IMAGE_FILE.test(file)) return undefined
  return apiUrl(`/articles/${slug}/media/${file}`)
}

export function whenPublished(article: Pick<ArticleSummary, 'publishedAt' | 'createdAt'>): string {
  const date = new Date(article.publishedAt ?? article.createdAt)
  return Number.isNaN(date.getTime()) ? '' : date.toISOString().slice(0, 10)
}
