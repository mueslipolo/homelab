# shellcheck shell=bash
# What counts as a stack's code: everything in stacks/<name>/ except Markdown.
# A README edit then neither rebuilds an image nor marks a stack for redeploy.
# Sourced by build-images.sh and dashboard-status.sh (lib/stacks.sh).

# A hash of the stack's files without its .md files (changes when code does).
code_hash() { # <repo> <stack dir, e.g. stacks/edge>
	git -C "$1" ls-tree -r HEAD -- "$2" 2>/dev/null | grep -v '\.md$' | sha1sum | cut -c1-40
}

# The last commit that changed the stack's code, as "<format>" (default
# "%H %ct"); empty if none.
code_commit() { # <repo> <stack dir> [git log format]
	git -C "$1" log -1 --format="${3:-%H %ct}" -- "$2" ":(exclude,glob)$2/**/*.md" 2>/dev/null
}
