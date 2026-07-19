#!/bin/sh
set -eu

if [ -n "${SECRET_KEY_FILE:-}" ]; then
    if [ ! -r "$SECRET_KEY_FILE" ]; then
        echo "SECRET_KEY_FILE is not readable" >&2
        exit 1
    fi
    IFS= read -r SECRET_KEY < "$SECRET_KEY_FILE"
    if [ -z "$SECRET_KEY" ]; then
        echo "SECRET_KEY_FILE is empty" >&2
        exit 1
    fi
    export SECRET_KEY
fi

exec "$@"

