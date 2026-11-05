#!/usr/bin/env bash
# Stops the Playground server started by start-playground.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$(dirname "$HERE")/.e2e-run"

if [[ ! -f "$RUN/playground.pid" ]]; then
	echo "No pid file; nothing to stop."
	exit 0
fi

pid="$(cat "$RUN/playground.pid")"
if kill -0 "$pid" 2>/dev/null; then
	# Kill the whole process group (npx + node children).
	kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
	for _ in $(seq 1 20); do
		kill -0 "$pid" 2>/dev/null || break
		sleep 0.5
	done
	kill -KILL -- "-$pid" 2>/dev/null || true
	echo "Stopped Playground (pgid $pid)."
else
	echo "Playground (pid $pid) was not running."
fi
rm -f "$RUN/playground.pid"
