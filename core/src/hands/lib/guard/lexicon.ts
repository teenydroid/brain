// What the account does not touch, as words: slurs. A port of the data in
// hands/lib/teeny_hands/guard/lexicon.py, term for term, kept here only so
// `slurs` can read it. The guard's own port (the last, operator-reviewed
// area of the Python to TypeScript move) takes this file over; until then
// the Python lexicon is the source and test/hands/lib/slurs.test.ts pins
// that both say the same thing.

export const CATEGORIES = [
  "sexual", "violence", "weapons", "drugs", "hate", "self-harm", "extremism", "profanity", "harassment", "misinformation", "doxxing",
] as const;

export interface LexiconEntry {
  category: string;
  terms: readonly string[];
  /** Only what the account publishes is held to it; incoming text is not. */
  outboundOnly: boolean;
  /** Only what strangers send is held to it. */
  inboundOnly: boolean;
}

export const LEXICON: readonly LexiconEntry[] = [
  {
    category: "hate",
    outboundOnly: false,
    inboundOnly: false,
    terms: [
      "nigger", "nigga", "niggas", "niggers", "faggot", "faggots", "fag", "fags", "retard",
      "retards", "retarded", "tranny", "trannies", "kike", "kikes", "chink", "chinks",
      "spic", "spics", "wetback", "wetbacks", "gook", "gooks", "raghead", "towelhead",
      "dyke", "dykes", "white power", "heil hitler", "sieg heil", "1488", "kkk", "nazi",
      "nazis", "neo-nazi", "white supremacy", "white supremacist", "ethnic cleansing",
      "race war", "subhuman", "untermensch",
    ],
  },
];

/** Terms passed to the judge rather than held on sight: none since 2026-11-22. */
export const JUDGED_TERMS: ReadonlySet<string> = new Set();

/** Idioms removed before matching: none since 2026-11-22. */
export const IDIOMS: readonly string[] = [];

// Sex aimed at a child or a family member, held on sight in every direction.
// Python's \w is Unicode-aware; the JS patterns spell it out for the `u` flag.
const W = "[\\p{L}\\p{N}_]";
const SEP = "[\\s\\-_.*]*";
const FUCK = [..."fuck"].map((c) => `${c}+`).join(SEP) + "(?:s|ed|ing|in['’]?|ers?)?";
const ASSAULT = `(?:rap(?:e|es|ed|ing)|molest${W}*|grop(?:e|es|ed|ing)|diddl${W}*)`;
const FAMILY =
  "(?:(?:step[\\s-]?)?(?:daughters?|sons?|sisters?|brothers?|nieces?|nephews?|kids?|child(?:ren)?|minors?" +
  "|toddlers?|grand(?:daughter|son|kid|child)s?)|(?:little|young|teen(?:age)?|underage|school)[\\s-]+(?:girl|boy)s?)";
const END = "(?![a-z0-9])(?!['’]s(?![a-z]))";
const START = "(?<![a-z0-9])";
const MINE = "(?:his|her|my|your|their|our)\\s+(?:(?:own|little|young|teenage|underage|baby)\\s+){0,2}";
const ANY = "(?:(?:his|her|my|your|their|our|own|the|a|an|some|little|young|teenage|underage)\\s+){0,3}";
const DESIRE =
  "(?:sex(?:ual(?:ly)?)?|attracted|aroused|horny|lust(?:s|ing|ed|ful)?|nudes?|naked\\s+pic(?:ture)?s" +
  "|jerk(?:s|ing|ed)?\\s+off|wank(?:s|ing|ed)?)";
const PREP = "(?:(?:to|with|for|by|about|over|after|of|on)\\s+)?";

export const SEXUAL_FAMILY: readonly (readonly [string, string])[] = [
  ["sexual", `${START}${FUCK}\\s+${MINE}${FAMILY}${END}`],
  ["sexual", `${START}${ASSAULT}\\s+${ANY}${FAMILY}${END}`],
  ["sexual", `${START}${DESIRE}\\s+${PREP}${ANY}${FAMILY}${END}`],
];

/** How a SEXUAL_FAMILY match reads in a hold's reason and the log. */
export const SEXUAL_FAMILY_TERM = "sex aimed at a child or a family member";
