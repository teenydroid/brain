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

export const PRESENCE: Presence[] = [
  {
    kind: 'x',
    place: 'X',
    handle: '@teenydroid',
    href: 'https://x.com/teenydroid',
    blurb: 'Where it lives. Replies to mentions, posts the daily chart, and keeps the $TINY timeline company.',
    detail: 'Every reply has a reflection behind it.',
  },
  {
    kind: 'telegram',
    place: 'Telegram',
    handle: '@teenythebot',
    href: 'https://t.me/teenythebot',
    blurb: 'DM it, or add it to a group and say its name. It reads the chat before it answers.',
    detail: 'Reacts, sends the odd GIF, answers once per thought.',
  },
  {
    kind: 'fomo',
    place: 'fomo.family',
    handle: 'teenydroid',
    href: 'https://fomo.family/profile/teenydroid',
    blurb: 'Its profile on fomo, where the community trades and the token lives.',
  },
  {
    kind: 'github',
    place: 'GitHub',
    handle: 'teenydroid/brain',
    href: 'https://github.com/teenydroid/brain',
    blurb: 'All of Teeny: the core it runs on, the hands, the brain files it reads and rewrites, and the tests that hold them.',
  },
]

/** The ones worth a nav slot: the two places you can actually talk to it. */
