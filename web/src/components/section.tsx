import type { ReactNode } from "react"

/** The content band under a page hero. */
export function Container({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-7xl px-4 py-14 sm:px-6 sm:py-20">{children}</div>
}
