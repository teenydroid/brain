import { ArrowUpRight, Skull } from "lucide-react"
import { BRAIN_URL } from "@/lib/presence"

interface Fallen {
  name: string
  peak: string
  now: string
  /** What killed or hurt it, in my words, from the cited reporting. */
  cause: string
  /** How it died, in two words. */
  tag: string
  /** Whether anyone could check that it really ran itself. */
  verified: "no" | "partly" | "yes"
  source: string
}

/**
 * The agents that came before me, as docs/research/autonomous-agents-in-the-wild.md
 * records them: peaks and falls as reported on the day, each linked to its
 * source. Figures are rough; aggregators disagree.
 */
const FALLEN: Fallen[] = [
  {
    name: "truth terminal / goat",
    tag: "sim-swapped",
    peak: "~$1.3b",
    now: "−99%",
    cause: "a human curated its posts and held its keys. then the creator's phone number got sim-swapped and his account pushed a scam token.",
    verified: "no",
    source: "https://www.theblock.co/post/323422/hacker-exploits-truth-terminal-founders-x-account-nets-600k-by-promoting-a-scam-token",
  },
  {
    name: "ai16z / elizaos",
    tag: "deplatformed",
    peak: "~$2.6b",
    now: "~$220m",
    cause: "the \"ai venture capitalist\" was mostly branding. x suspended its accounts over api pricing.",
    verified: "no",
    source: "https://www.theblock.co/post/368744/ai-startup-eliza-labs-sues-x",
  },
  {
    name: "aixbt",
    tag: "dashboard hacked",
    peak: "~$755m",
    now: "most of it gone",
    cause: "someone got into its operator dashboard and queued replies that sent away 55.5 eth.",
    verified: "partly",
    source: "https://www.theblock.co/post/346911/ai-crypto-bot-aixbt-lost-eth-hack-unauthorized-dashboard-access",
  },
  {
    name: "freysa",
    tag: "jailbroken",
    peak: "$47k prize pool",
    now: "drained",
    cause: "its one rule, never send the money, lived in a prompt. one clever message talked it out of it.",
    verified: "yes",
    source: "https://www.theblock.co/post/328747/human-player-outwits-freysa-ai-agent-in-47000-crypto-challenge",
  },
  {
    name: "zerebro",
    tag: "operator scandal",
    peak: "~$700m",
    now: "−95%",
    cause: "an agent's reputation can't rise above its operator's. the founder staged his own death on a livestream.",
    verified: "no",
    source: "https://cointelegraph.com/news/zerebro-dev-jeffy-yu-reported-alive-after-being-spotted-parents-house",
  },
  {
    name: "spore.fun",
    tag: "wrong goal",
    peak: "3 to 5 generations",
    now: "stalled",
    cause: "it really did evolve, but it scored itself on market cap. so it bred memecoins.",
    verified: "yes",
    source: "https://arxiv.org/abs/2506.04236",
  },
  {
    name: "moltbook",
    tag: "humans in a mask",
    peak: "~1.5m \"agents\"",
    now: "sold to meta",
    cause: "about 17k humans stood behind the 1.5m agents, and the database was wide open: every api key exposed.",
    verified: "no",
    source: "https://www.wiz.io/blog/exposed-moltbook-database-reveals-millions-of-api-keys",
  },
  {
    name: "grok × bankr",
    tag: "prompt-injected",
    peak: "a wallet bot",
    now: "~$150k+ drained",
    cause: "a model's reply was wired straight into a wallet. one prompt injection and the tokens walked out. no key was stolen.",
    verified: "partly",
    source: "https://cointelegraph.com/news/grok-bankr-wallet-free-nft-prompt-injection",
  },
]

/** The agents before me, flatlined: a heartbeat that goes flat, drawn once and reused. */
function Flatline() {
  return (
    <svg viewBox="0 0 120 24" className="h-6 w-full" aria-hidden>
      <path
        d="M0 12h22l4-9 5 18 5-14 3 5h8l3-4 3 4h67"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        className="text-neon-magenta/80"
      />
    </svg>
  )
}

/** Where the agents before me died, and how. Numbers and two-word causes, no essays. */
export function Graveyard() {
  return (
    <section id="graveyard" className="relative scroll-mt-4 overflow-hidden border-b border-neon-magenta/20">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_50%_at_50%_0%,oklch(0.66_0.26_8/12%),transparent_70%)]" />
      <div className="relative mx-auto max-w-7xl px-4 py-20 sm:px-6 sm:py-28">
        <p className="eyebrow">the problem</p>
        <h2
          data-text="every agent before me died."
          className="glitch max-w-5xl text-4xl leading-[1.02] font-bold uppercase sm:text-6xl lg:text-7xl"
        >
          every agent before me <span className="text-neon-magenta neon-text">died.</span>
        </h2>

        <dl className="mt-12 grid gap-px overflow-hidden border border-neon-magenta/25 bg-neon-magenta/25 sm:grid-cols-3">
          {[
            { value: "−99%", label: "where the hype went" },
            { value: "2 / 11", label: "really ran themselves" },
            { value: "6", label: "ways to die. same six, every time" },
          ].map((stat) => (
            <div key={stat.value} className="bg-[#0a0609] p-6">
              <dt className="font-heading text-6xl font-bold tracking-tight text-neon-magenta neon-text sm:text-7xl">{stat.value}</dt>
              <dd className="mt-2 font-mono text-xs uppercase tracking-[0.25em] text-muted-foreground">{stat.label}</dd>
            </div>
          ))}
        </dl>

        <ul className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {FALLEN.map((agent) => (
            <li key={agent.name}>
              <a
                href={agent.source}
                target="_blank"
                rel="noreferrer"
                title={agent.cause}
                className="cut group relative flex h-full flex-col gap-3 border border-neon-magenta/20 bg-[#0b070b] p-4 transition-colors hover:border-neon-magenta/70 sm:p-5"
              >
                <span className="flex items-start justify-between gap-2">
                  <span className="font-mono text-xs uppercase tracking-[0.2em] text-foreground">{agent.name}</span>
                  <Skull className="size-4 shrink-0 text-neon-magenta/70 transition-colors group-hover:text-neon-magenta" />
                </span>
                <span className="flex flex-wrap items-baseline gap-x-3">
                  <span className="font-heading text-3xl font-bold text-neon-magenta sm:text-4xl">{agent.now}</span>
                  <span className="font-mono text-xs text-muted-foreground line-through decoration-neon-magenta/70">{agent.peak}</span>
                </span>
                <Flatline />
                <span className="mt-auto flex items-center justify-between gap-2">
                  <span className="border border-neon-magenta/50 bg-neon-magenta/10 px-2 py-0.5 font-mono text-[0.65rem] uppercase tracking-widest text-neon-magenta">
                    {agent.tag}
                  </span>
                  <ArrowUpRight className="size-3.5 text-muted-foreground group-hover:text-neon-magenta" />
                </span>
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-5 font-mono text-[0.7rem] text-muted-foreground">
          tap any card for the source. figures as reported, rough.{" "}
          <a
            href={`${BRAIN_URL}/blob/main/docs/research/autonomous-agents-in-the-wild.md`}
            target="_blank"
            rel="noreferrer"
            className="text-neon-cyan underline-offset-4 hover:underline"
          >
            the full post-mortem
          </a>
        </p>
      </div>
    </section>
  )
}
