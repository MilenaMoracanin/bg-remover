#!/bin/bash
cd "$(dirname "$0")"
URL="http://127.0.0.1:8765"

echo "pokretanje $(date '+%H:%M:%S')" >> pozadina.log
if curl -sf "$URL/api/status" >/dev/null; then
  echo "vec radi" >> pozadina.log
  open "$URL"
  exit 0
fi

if ! .venv/bin/python -c "import fastapi, onnxruntime, PIL" >/dev/null 2>&1; then
  PY="/usr/local/bin/python3.12"
  if [ ! -x "$PY" ]; then
    PY="$(command -v python3.12 || true)"
  fi
  if [ -z "$PY" ]; then
    echo "Potreban je Python 3.12."
    read -r -p "Pritisni Enter za izlaz..."
    exit 1
  fi
  "$PY" -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

NO_BROWSER=1 nohup .venv/bin/python app.py >>pozadina.log 2>&1 </dev/null &
echo $! > pozadina.pid
disown
echo "pid $(cat pozadina.pid)" >> pozadina.log

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if curl -sf "$URL/api/status" >/dev/null; then
    open "$URL"
    echo "Pozadina radi. Ovaj prozor možeš zatvoriti."
    exit 0
  fi
  sleep 0.4
done

echo "Program se nije pokrenuo. Poslednje linije iz pozadina.log:"
tail -n 30 pozadina.log
read -r -p "Pritisni Enter za izlaz..."
exit 1
