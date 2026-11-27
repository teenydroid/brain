#!/bin/sh
# hand-shim.sh NAME [--check]
#
# Switches hands/NAME from its Python wrapper to the TypeScript build of the
# same hand (core/dist/hands/NAME/cli.js), keeping the path every caller uses.
# The wrapper it writes is the whole of the convention:
#
#   #!/bin/sh
#   exec node --disable-warning=ExperimentalWarning "$(dirname "$(realpath "$0")")/../core/dist/hands/NAME/cli.js" "$@"
#
# - realpath "$0" resolves a symlinked or PATH-found hands/NAME to the real
#   checkout, as the Python wrappers' os.path.realpath did, so it works from any
#   cwd; nothing in it reads $PWD.
# - exec replaces the shell, so stdin, stdout, stderr and the exit status are the
#   node process's own, with no extra process between a caller and the hand.
# - the warning flag keeps node:sqlite's "experimental" notice off stderr, which
#   is part of the contract.
# - it needs `node` (>= 24.16) on PATH and core/dist built (`npm run build` in
#   core/); the image the stack runs in has both.
#
# With --check it only prints the wrapper it would write.
set -eu

name="${1:?usage: hand-shim.sh NAME [--check]}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
target="$repo/hands/$name"

wrapper() {
  printf '#!/bin/sh\n'
  printf '# hands/%s: a TypeScript hand; its source is core/src/hands/%s (core/src/hands/README.md).\n' "$name" "$name"
  printf 'exec node --disable-warning=ExperimentalWarning "$(dirname "$(realpath "$0")")/../core/dist/hands/%s/cli.js" "$@"\n' "$name"
}

if [ "${2:-}" = "--check" ]; then
  wrapper
  exit 0
fi

[ -f "$target" ] || { echo "hand-shim: no hands/$name" >&2; exit 1; }
[ -f "$repo/core/src/hands/$name/cli.ts" ] || { echo "hand-shim: core/src/hands/$name/cli.ts does not exist yet" >&2; exit 1; }
wrapper >"$target.new"
chmod 755 "$target.new"
mv "$target.new" "$target"
echo "hands/$name now runs core/dist/hands/$name/cli.js"
