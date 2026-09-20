#!/usr/bin/env bash
#
# Stop every running Yaffo process: the dev server, packaged/waitress roles,
# taskq hosts, and the isolated test environments (their tsx supervisor and the
# Flask app it spawns).
#
# Matching is on WHAT a process is, never on its path. The repo lives under
# .../projects/yaffo, so a naive `pkill -f yaffo` also matches PyCharm's
# TypeScript language server, tsserver's typings installer, and any editor or
# shell whose cwd happens to sit in the tree. Those are explicitly excluded.
#
# Each match is resolved to its process GROUP and the group is signalled, so
# child processes (multiprocessing resource trackers, the node/tsx wrappers,
# Flask reloader children) go with their parent instead of being orphaned.
#
# Usage:
#   scripts/kill_yaffo.sh              # list, then stop everything
#   scripts/kill_yaffo.sh -n           # dry run: list only, kill nothing
#   scripts/kill_yaffo.sh --keep 5001  # spare whatever is serving this port
#   scripts/kill_yaffo.sh --keep 5001 --keep 5010
#
set -uo pipefail

# Some processes on this box (the terminal plugin's PTY proxy) carry an entire
# Python source file in argv. BSD grep reports "Invalid argument" on those bytes
# under a UTF-8 locale and returns non-zero, which silently turns every match
# below into a miss. C locale treats them as bytes and keeps matching.
export LC_ALL=C

DRY_RUN=0
KEEP_PORTS=()

usage() {
    sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -n|--dry-run) DRY_RUN=1; shift ;;
        --keep) [ $# -ge 2 ] || { echo "--keep needs a port" >&2; exit 2; }
                KEEP_PORTS+=("$2"); shift 2 ;;
        --keep=*) KEEP_PORTS+=("${1#--keep=}"); shift ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown argument: $1" >&2; usage 2 ;;
    esac
done

# What counts as Yaffo. Anchored to the module or script being executed, so a
# path that merely contains "yaffo" never matches on its own.
#
# `-m flask run` is the one loose pattern: it is how the isolated test
# environment starts its app, and a crashed runner can orphan one. It would also
# match a Flask app from an unrelated project. That is why nothing is killed
# before the full list is printed, and why -n exists.
PATTERNS=(
    '-m[[:space:]]+yaffo(\.|[[:space:]]|$)'   # yaffo.app, yaffo.taskq.host, yaffo
    'isolated_runner\.ts'                     # the test environment supervisor
    '-m[[:space:]]+flask[[:space:]]+run'      # the app that supervisor spawns
    'YAFFO_ROLE'                              # packaged build's waitress roles
)

# Never touch these, however they match above.
EXCLUDE='jsLanguageServicesImpl|typingsInstaller|tsserver|language-service|/Applications/|Visual Studio Code|[Cc]ode Helper|kill_yaffo|/bin/zsh|/bin/bash|pty.*proxy|grep|ps -eo'

SELF=$$
declare -a PIDS=() ROWS=()

# Ports to spare, resolved to the pids listening on them (plus their groups).
declare -a SPARED_PGIDS=()
for port in ${KEEP_PORTS+"${KEEP_PORTS[@]}"}; do
    while read -r pid; do
        [ -n "$pid" ] || continue
        pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
        [ -n "$pgid" ] && SPARED_PGIDS+=("$pgid")
    done < <(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null)
done

is_spared() {
    local pgid="$1"
    for spared in ${SPARED_PGIDS+"${SPARED_PGIDS[@]}"}; do
        [ "$pgid" = "$spared" ] && return 0
    done
    return 1
}

# `read` splits on runs of IFS, which absorbs the padding `ps` uses to right-
# align pid and pgid; cutting on a single space does not.
while read -r pid pgid cmd; do
    [ -n "${cmd:-}" ] || continue

    [ "$pid" = "$SELF" ] && continue
    [ "$pid" = "$PPID" ] && continue

    # Bash's own =~ rather than grep: argv here can hold an entire embedded
    # source file, which BSD grep rejects with "Invalid argument" and a
    # non-zero status — indistinguishable from "no match", so real processes
    # were silently skipped. This also avoids forking grep per pattern.
    [[ $cmd =~ $EXCLUDE ]] && continue

    matched=0
    for pattern in "${PATTERNS[@]}"; do
        if [[ $cmd =~ $pattern ]]; then matched=1; break; fi
    done
    [ "$matched" = 1 ] || continue

    if is_spared "$pgid"; then
        ROWS+=("  SPARED  pid=$pid pgid=$pgid  ${cmd:0:96}")
        continue
    fi

    PIDS+=("$pid:$pgid")
    ROWS+=("  kill    pid=$pid pgid=$pgid  ${cmd:0:96}")
done < <(ps -eo pid=,pgid=,command=)

if [ "${#ROWS[@]}" -eq 0 ]; then
    echo "No Yaffo processes running."
    exit 0
fi

printf '%s\n' "${ROWS[@]}"

if [ "${#PIDS[@]}" -eq 0 ]; then
    echo
    echo "Everything found is spared; nothing to stop."
    exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
    echo
    echo "Dry run: ${#PIDS[@]} process(es) would be stopped."
    exit 0
fi

# Signal the group so children go with the parent. Fall back to the bare pid
# when the group is gone (or is our own, which -PGID would take down too).
signal_all() {
    local sig="$1" entry pid pgid
    for entry in "${PIDS[@]}"; do
        pid=${entry%%:*}
        pgid=${entry##*:}
        if [ -n "$pgid" ] && [ "$pgid" != "$(ps -o pgid= -p $SELF 2>/dev/null | tr -d ' ')" ]; then
            kill "-$sig" "-$pgid" 2>/dev/null || kill "-$sig" "$pid" 2>/dev/null
        else
            kill "-$sig" "$pid" 2>/dev/null
        fi
    done
}

echo
echo "Stopping ${#PIDS[@]} process(es)..."
signal_all TERM

for _ in $(seq 1 20); do
    alive=0
    for entry in "${PIDS[@]}"; do
        kill -0 "${entry%%:*}" 2>/dev/null && alive=1
    done
    [ "$alive" = 0 ] && break
    sleep 0.5
done

STRAGGLERS=()
for entry in "${PIDS[@]}"; do
    kill -0 "${entry%%:*}" 2>/dev/null && STRAGGLERS+=("$entry")
done

if [ "${#STRAGGLERS[@]}" -gt 0 ]; then
    echo "Force-killing ${#STRAGGLERS[@]} that ignored SIGTERM..."
    PIDS=("${STRAGGLERS[@]}")
    signal_all KILL
    sleep 1
fi

REMAINING=0
for entry in "${PIDS[@]}"; do
    kill -0 "${entry%%:*}" 2>/dev/null && REMAINING=$((REMAINING + 1))
done

if [ "$REMAINING" -gt 0 ]; then
    echo "$REMAINING process(es) survived; inspect with: ps -eo pid,pgid,command | grep yaffo"
    exit 1
fi

echo "Done."
