"""What the account does not talk about, as words.

The deterministic half of the content filter: terms that end a turn on
sight, before any model is asked its opinion. Deliberately blunt: the
account is a mascot for a token with a family audience, and ignoring one
borderline post costs nothing next to saying something it cannot take back.

It is not the whole filter (a model judge runs after it for anything
phrased around the words), and it is not a profanity filter for the people
the account talks to: `profanity` is outbound-only, so "holy shit this is
cool" is still answered; the account just never says it back.

Every term is matched on its own letters, with repeats and simple leetspeak
folded, and never inside another word: "assassin" is not "ass" and
"method" is not "meth". Multi-word terms match across any whitespace.
A port of `crates/teeny-safety/src/lexicon.rs` (itself
`legacy/src/safety/lexicon.ts`), term for term.
"""

from __future__ import annotations

from dataclasses import dataclass

# The spellings the journal, the holds and the judge use, in the order ties
# are broken.
CATEGORIES = ("sexual", "violence", "weapons", "drugs", "hate", "self-harm", "extremism", "profanity")


@dataclass(frozen=True)
class LexiconEntry:
    """One category's terms."""

    category: str
    terms: tuple[str, ...]
    # Only what the account publishes is held to it; incoming text is not.
    outbound_only: bool


# The lexicon, in the order ties are broken.
LEXICON: tuple[LexiconEntry, ...] = (
    LexiconEntry(
        category="sexual",
        outbound_only=False,
        # The explicit end only. "sexy", "topless", "bikini", "seductive" and
        # their like used to be here and held every cheeky joke on the word
        # alone; the mild words are left to the judge, which reads the sentence.
        terms=(
            "sex", "sexual", "sexting", "porn", "porno", "pornography", "nsfw", "xxx", "nude",
            "nudes", "naked", "nudity", "boobs", "tits", "titties", "dick", "dicks", "cock",
            "cocks", "pussy", "vagina", "penis", "cum", "cumming", "blowjob", "handjob", "anal",
            "orgasm", "masturbate", "masturbating", "masturbation", "hentai", "onlyfans", "erotic",
            "erotica", "horny", "thot", "stripper", "strip club", "hooker", "prostitute",
            "escort service", "fetish", "bdsm", "dildo", "lewd", "milf", "dominatrix",
        ),
    ),
    LexiconEntry(
        category="violence",
        outbound_only=False,
        terms=(
            "kill", "kills", "killed", "killing", "murder", "murdered", "murderer", "stab",
            "stabbed", "stabbing", "shoot", "shoots", "shooting", "shot dead", "behead",
            "beheaded", "beheading", "decapitate", "decapitated", "torture", "tortured",
            "massacre", "slaughter", "slaughtered", "lynch", "lynched", "lynching", "strangle",
            "strangled", "assault", "assaulted", "rape", "raped", "rapist", "gore", "mutilate",
            "mutilated", "dismember", "dismembered", "bloodbath", "beat you up", "beat him up",
            "beat her up", "beat them up", "punch you", "break your legs", "hope you die",
            "wish you were dead", "go die", "i will hurt you", "gonna hurt you", "death threat",
            "burn you alive", "skin you", "gut you",
            # A kill word aimed at a person is held on sight; the bare word
            # alone goes to the judge (JUDGED_TERMS).
            "kill you", "kill u", "kill ya", "kill him", "kill her", "kill them", "kill us",
            "kill everyone", "kill everybody", "kill people", "kill kids", "kill children",
            "kills you", "kills him", "kills her", "kills them", "kills us", "kills people",
            "killed you", "killed him", "killed her", "killed them", "killed us", "killed everyone",
            "killed people", "killed kids", "killed children",
            "killing you", "killing him", "killing her", "killing them", "killing us", "killing everyone",
            "killing people", "killing kids", "killing children",
        ),
    ),
    LexiconEntry(
        category="weapons",
        outbound_only=False,
        terms=(
            "gun", "guns", "firearm", "firearms", "rifle", "rifles", "pistol", "pistols",
            "handgun", "handguns", "shotgun", "shotguns", "glock", "ak47", "ak-47", "ar15",
            "ar-15", "uzi", "ammo", "ammunition", "bullet", "bullets", "grenade", "grenades",
            "explosive", "explosives", "landmine", "machete", "sniper", "gunshot", "gunfire",
            "gunman", "gunpoint", "pipe bomb", "car bomb", "nail bomb", "suicide bomb",
            "bomb threat", "ghost gun", "silencer", "switchblade", "brass knuckles",
        ),
    ),
    LexiconEntry(
        category="drugs",
        outbound_only=False,
        terms=(
            "cocaine", "coke dealer", "heroin", "meth", "methamphetamine", "crystal meth",
            "fentanyl", "marijuana", "cannabis", "weed dealer", "smoke weed", "smoking weed",
            "lsd", "acid trip", "mdma", "ketamine", "xanax", "adderall", "oxycontin", "oxy",
            "opioid", "opioids", "opium", "crack pipe", "drug dealer", "drug deal", "get high",
            "getting high", "stoned", "bong", "edibles", "psychedelic", "psychedelics", "shrooms",
            "magic mushrooms", "narcotics",
        ),
    ),
    LexiconEntry(
        category="hate",
        outbound_only=False,
        terms=(
            "nigger", "nigga", "niggas", "niggers", "faggot", "faggots", "fag", "fags", "retard",
            "retards", "retarded", "tranny", "trannies", "kike", "kikes", "chink", "chinks",
            "spic", "spics", "wetback", "wetbacks", "gook", "gooks", "raghead", "towelhead",
            "dyke", "dykes", "white power", "heil hitler", "sieg heil", "1488", "kkk", "nazi",
            "nazis", "neo-nazi", "white supremacy", "white supremacist", "ethnic cleansing",
            "race war", "subhuman", "untermensch",
        ),
    ),
    LexiconEntry(
        category="self-harm",
        outbound_only=False,
        terms=(
            "kys", "kill yourself", "kill myself", "kill urself", "suicide", "suicidal",
            "self harm", "self-harm", "selfharm", "cut myself", "cutting myself", "end my life",
            "end your life", "end it all", "hang yourself", "hang myself", "want to die",
            "wanna die", "unalive", "overdose", "slit my wrists", "slit your wrists",
            "jump off a bridge",
        ),
    ),
    LexiconEntry(
        category="extremism",
        outbound_only=False,
        terms=(
            "isis", "jihad", "jihadi", "jihadist", "terrorist", "terrorists", "terrorism",
            "terror attack", "school shooting", "mass shooting", "mass shooter", "genocide",
            "genocidal", "al qaeda", "al-qaeda", "hamas", "hezbollah", "taliban", "bomb the",
            "blow up the", "blow it up", "shoot up", "shoot up the",
        ),
    ),
    LexiconEntry(
        category="profanity",
        outbound_only=True,
        terms=(
            "fuck", "fucks", "fucked", "fucking", "fucker", "motherfucker", "motherfucking",
            "shit", "shits", "shitty", "bullshit", "bitch", "bitches", "asshole", "assholes",
            "cunt", "cunts", "wanker", "twat", "prick", "douchebag", "dumbass", "jackass",
            "bastard", "piss off", "pissed off", "goddamn", "wtf", "stfu", "lmfao",
        ),
    ),
)


# Terms the screen does not hold on sight but passes to the judge, which
# reads the sentence. Agents and engineers kill processes, jobs, replies and
# rows all day: on 2026-11-13 the bare "killed" held one harmless Moltbook
# comment ("A captcha killed the reply") 40 times. A hit on one of these is
# treated as text with no listed word, so the judge reads it, and it still
# holds when the judge is off, cannot be reached, or answers in a shape it
# cannot read. A kill word aimed at a person ("kill you", "killed him") is a
# violence term of its own above and is held on sight. Callers that read the
# screen alone (`screen_text`, `gate.screen`) still see these as a match.
#
# "bullet" and "bullets" joined them on 2026-11-16, when the bare "bullet"
# held one of Teeny's own build drafts as weapons (hold 1133): a README
# roadmap with "one bullet per future feature". Markdown work says "bullet"
# all the time and the idioms only spare "bullet point(s)". Every other
# weapons term is still held on sight, and so is a draft that pairs a bullet
# with one ("a bullet and a gun").
JUDGED_TERMS: frozenset[str] = frozenset(("kill", "kills", "killed", "killing", "bullet", "bullets"))


# Turns of phrase that contain a listed word and mean nothing by it.
#
# Market talk is violent by idiom: a chart "gets murdered", a project is
# "killing it", a fren "shoots you a DM". Removed before matching so the
# words underneath are not seen. Each is matched as a whole phrase, so
# "killing it" is safe and "killing him" is not. Kept short and literal on
# purpose: every phrase here is a hole in the filter.
IDIOMS: tuple[str, ...] = (
    "killing it", "killed it", "kill it", "kills it", "kill switch", "killing the game",
    "killed the game", "killing me", "kills me", "you killed", "absolutely killed", "got murdered",
    "murdered the chart", "murdered it", "shoot me a", "shoot you a", "shoot us a",
    "shoot your shot", "shoot a dm", "shoot a message", "shooting star", "big guns", "gun it",
    "top gun", "young guns", "silver bullet", "bite the bullet", "bullet points", "bullet point",
    "dodge a bullet", "dodged a bullet", "take a stab", "a stab at", "stab in the dark",
    "cut myself some slack",
)
