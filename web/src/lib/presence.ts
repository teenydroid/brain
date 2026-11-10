/**
 * Everywhere Teeny exists in public. One list so the hero, the "Find it"
 * section, the nav and the footer never disagree about a handle or a link.
 *
 * The wallet is listed because it is public on chain. It acts only through
 * Teeny's own capped wallet hand, run by its jobs; the conversational agent has
 * no wallet tool (see `test/capabilities.test.ts`), so it can be watched, not
 * asked to do anything.
 */

export type PresenceKind = 'x' | 'telegram' | 'fomo' | 'wallet' | 'github'

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

export const WALLET_ADDRESS = '0x4C3799279dDF44d97a46dd14927789f23bf47589'

export function shortAddress(address: string): string {
  return `${address.slice(0, 6)}…${address.slice(-4)}`
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
    kind: 'wallet',
    place: 'Robinhood Chain',
    handle: shortAddress(WALLET_ADDRESS),
    href: `https://robinhoodchain.blockscout.com/address/${WALLET_ADDRESS}`,
    blurb: 'Its wallet, in the open. Watch it on Blockscout.',
    detail: 'It moves only through its own capped wallet hand, never from a conversation: nothing you say can move it.',
  },
  {
    kind: 'github',
    place: 'GitHub',
    handle: 'tinyhumansai',
    href: 'https://github.com/tinyhumansai',
    blurb: 'The code, the soul file, the rules, and the tests that hold them.',
  },
]

/** The ones worth a nav slot: the two places you can actually talk to it. */
export const SOCIAL = PRESENCE.filter((p) => p.kind === 'x' || p.kind === 'telegram')
