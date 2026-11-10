"""hands/mail: the only holder of Teeny's AgentMail key (docs/hands.md, "mail").

  client   the AgentMail API base, the inbox, and ids that may reach a path
  codes    verification codes and allowlisted links, read out of mail
  reads    the read verbs, through the read cache (`mail_cache`)
  inbox    intake: new mail screened, recorded in `mail_messages` and offered to the core
  actions  the only path to a write: guard, rules, DRY_RUN, AgentMail, the ledger
  cli      the verbs
"""
