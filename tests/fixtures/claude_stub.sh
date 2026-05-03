#!/usr/bin/env bash
# A stub `claude` for e2e tests. Records invocations to $CLIO_TEST_CLAUDE_LOG
# and emits the response in $CLIO_TEST_CLAUDE_RESPONSE on stdout. If unset,
# emits an empty response.

if [ -n "${CLIO_TEST_CLAUDE_LOG:-}" ]; then
    echo "$@" >> "$CLIO_TEST_CLAUDE_LOG"
fi
if [ -n "${CLIO_TEST_CLAUDE_RESPONSE:-}" ]; then
    cat "$CLIO_TEST_CLAUDE_RESPONSE"
fi
