from pathlib import Path
p=Path("bin/test/studio_test.sh"); s=p.read_text()
old='''  check)
    if [ -n "${FAKE_SCENE_HELD:-}" ]; then echo '{"ok": false, "refused": "held", "category": "unknown"}'; exit 2; fi
    jq -c '{ok: true, text: .text}' <<<"$in" ;;
'''
new='''  check)
    # The words about to go to X (surface x, the studio's final gate):
    # $FAKE_WORDS_HELD holds any text containing it, $FAKE_WORDS_FAILED is a
    # judge that could not judge. Every other check (a scene, a meme's lines):
    # $FAKE_SCENE_HELD.
    if [ "$(jq -r '.surface // ""' <<<"$in")" = x ]; then
      if [ -n "${FAKE_WORDS_FAILED:-}" ]; then echo '{"ok": false, "refused": "held", "category": "unknown", "hold_id": 12, "judge_failed": true}'; exit 2; fi
      if [ -n "${FAKE_WORDS_HELD:-}" ] && jq -e --arg w "$FAKE_WORDS_HELD" '.text | contains($w)' <<<"$in" >/dev/null; then
        echo '{"ok": false, "refused": "held", "category": "sexual", "hold_id": 11}'; exit 2
      fi
    elif [ -n "${FAKE_SCENE_HELD:-}" ]; then echo '{"ok": false, "refused": "held", "category": "unknown"}'; exit 2; fi
    jq -c '{ok: true, text: .text}' <<<"$in" ;;
'''
assert old in s; s=s.replace(old,new)
old='''stdin_of() { grep -E "^$1" "$FAKE_HANDS" | head -1 | sed 's/^[^{]*stdin=//'; }
'''
new='''stdin_of() { grep -E "^$1" "$FAKE_HANDS" | head -1 | sed 's/^[^{]*stdin=//'; }
# The first guard check that is not the final gate on the words for X.
media_check() { grep -E '^guard check ' "$FAKE_HANDS" | sed 's/^[^{]*stdin=//' | jq -c 'select(.surface != "x")' | head -1; }
'''
assert old in s; s=s.replace(old,new)
old='''  jqe '.surface == "task" and .text == "teeny in scene 1"' <<<"$(stdin_of 'guard check ')"'''
assert old in s; s=s.replace(old,'''  jqe '.surface == "task" and .text == "teeny in scene 1"' <<<"$(media_check)"''')
old='''check "the guard sees the meme's lines as written" jqe '.text == "asking first 1 / asking after"' <<<"$(stdin_of 'guard check ')"'''
assert old in s; s=s.replace(old,'''check "the guard sees the meme's lines as written" jqe '.text == "asking first 1 / asking after"' <<<"$(media_check)"''')
old='''check "--desk with --no-outside and --no-slot reads no hand but a dry hands/x post" \\
  bash -c "[ \\"\\$(jq -r .outcome <<<\\"\\$1\\")\\" = picked ] && ! grep -qvE '^(story record-run|x (post|post-thread) DRY_RUN=1 )' '$FAKE_HANDS'" _ "$out"'''
assert old in s, "desk"
s=s.replace(old,'''check "--desk with --no-outside and --no-slot reads no hand but the guard and a dry hands/x post" \\
  bash -c "[ \\"\\$(jq -r .outcome <<<\\"\\$1\\")\\" = picked ] && ! grep -qvE '^(story record-run|guard check DRY_RUN=1 |x (post|post-thread) DRY_RUN=1 )' '$FAKE_HANDS'" _ "$out"''')
p.write_text(s)
