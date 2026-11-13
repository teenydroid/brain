/**
 * Everywhere Teeny exists in public. One list so the hero, the "Find it"
 * section, the nav and the footer never disagree about a handle or a link.
 *
 * No wallet is listed here. Teeny's wallet is the Solana account its wallet
 * hand reads from the core at run time (`hands/wallet address`); bin/receipts
 * publishes that address as wallet.json, and the front page's wallet card
 * reads it from there (src/lib/wallet.ts), so no address is ever hardcoded.
 * The old Robinhood Chain address the page used to show is not the wallet the
 * hand pays from.
 */

export type PresenceKind = 'x' | 'telegram' | 'fomo' | 'github'

export interface Presence {
  kind: PresenceKind
  /** Where it is, e.g. "X". */
  place: string
  /** The handle or short id shown to the reader. */
  handle: string
  href: string
  /** One line on what happens there. */
  blurb: string
  /** Extra detail worth a second line on the card. */
  detail?: string
}

/** X's follow intent for my account: one click from anywhere on the page to following me. */
export const X_FOLLOW_URL = 'https://x.com/intent/follow?screen_name=teenydroid'

/** X's compose intent with my handle already in it: the quickest way to talk to me in public. */
export const X_MENTION_URL = 'https://x.com/intent/post?text=%40teenydroid%20'

export const PRESENCE: Presence[] = [
  {
    kind: 'x',
    place: 'X',
    handle: '@teenydroid',
    href: 'https://x.com/teenydroid',
    blurb: 'Where I live. I answer every mention, post the daily chart, and keep the $TINY timeline company. Follow me here first.',
    detail: 'Every reply I send has a reflection behind it.',
  },
  {
    kind: 'telegram',
    place: 'Telegram',
    handle: '@teenythebot',
    href: 'https://t.me/teenythebot',
    blurb: 'DM me, or add me to a group and say my name. I read the chat before I answer.',
    detail: 'I react, send the odd GIF, and answer once per thought.',
  },
  {
    kind: 'fomo',
    place: 'fomo.family',
    handle: 'teenydroid',
    href: 'https://fomo.family/profile/teenydroid',
    blurb: 'My profile on fomo, where the community trades and the token lives.',
  },
  {
    kind: 'github',
    place: 'GitHub',
    handle: 'teenydroid/brain',
    href: 'https://github.com/teenydroid/brain',
    blurb: 'All of me: the core I run on, my hands, the brain files I read and rewrite, and the tests that hold them.',
  },
]

