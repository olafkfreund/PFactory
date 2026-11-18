#!/usr/bin/env bash
set -uo pipefail
export LC_ALL=C
exec 2>/dev/null

range=HEAD^!
if [[ -n ${1:-} ]] && git rev-parse --verify --quiet "${1}^{commit}" >/dev/null; then
    range="${1}..HEAD"
fi

declare -A seen=()
count=0
while IFS= read -r c; do
    while IFS= read -r slug; do
        [[ $slug =~ ^[A-Za-z0-9._/#:-]{1,128}$ ]] || continue
        [[ -z ${seen[$slug]+x} ]] || continue
        seen[$slug]=1
        printf 'Plan-Slug: %s\n' "$slug"
        count=$((count + 1))
        (( count < 10 )) || exit 0
    done < <(git log -1 --format=%B "$c" | git interpret-trailers --parse | sed -n '/^Plan-Slug: /{s/^Plan-Slug: //;p;}')
done < <(git log --format=%H "$range")
exit 0
