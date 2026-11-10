#!/bin/sh
# Chat by hand: starts the server and opens two Terminal windows, Alice (Python) and Bob (TypeScript).
# Hand-written (not generated). macOS Terminal. Usage: sh chat.sh [--fresh]   (--fresh forgets earlier logins and messages)
# In each window type `login <name>` the first time, then `send <name> <message>`, `inbox`, `outbox`, `quit`.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
port=7070
[ "$1" = "--fresh" ] && rm -rf /tmp/friend-chat
mkdir -p /tmp/friend-chat

if ! nc -z 127.0.0.1 $port 2>/dev/null; then
  python3 "$here/server/server.py" --port $port > /tmp/friend-chat/server.log 2>&1 &
  echo "server started on port $port (pid $!; stop it with: kill $!)"
else
  echo "server already running on port $port"
fi

open_window() {  # title, command
  osascript -e "tell application \"Terminal\" to do script \"clear; echo '== $1 =='; cd '$here' && $2\"" > /dev/null
}
open_window "Alice (Python)" "python3 clients/python/main.py --server 127.0.0.1:$port --data-dir /tmp/friend-chat/alice"
open_window "Bob (TypeScript)" "node clients/typescript/dist/main.js --server 127.0.0.1:$port --data-dir /tmp/friend-chat/bob"
echo "opened Alice and Bob. In each window: login Alice / login Bob, then e.g. send Bob hi"
