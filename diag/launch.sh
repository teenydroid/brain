#!/usr/bin/env bash
# Diagnostic only: time one Chrome launch to DevToolsActivePort with TinyComputer's args.
chrome="$1"; shift; d="$(mktemp -d)"
start=$(date +%s.%N)
"$chrome" --remote-debugging-port=0 --no-first-run --no-default-browser-check --disable-background-networking \
 --disable-backgrounding-occluded-windows --disable-component-update --disable-default-apps --disable-hang-monitor \
 --disable-popup-blocking --disable-prompt-on-repost --disable-sync --disable-features=Translate \
 --enable-features=NetworkService,NetworkServiceInProcess --metrics-recording-only --password-store=basic --use-mock-keychain \
 --headless=new --enable-unsafe-swiftshader --proxy-server=http://127.0.0.1:9 '--proxy-bypass-list=<-loopback>' \
 --user-data-dir="$d" --disable-quic --disable-http2 '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1' \
 --force-webrtc-ip-handling-policy=disable_non_proxied_udp "$@" 2>"$d.err" &
pid=$!
for _ in $(seq 1 1200); do [ -s "$d/DevToolsActivePort" ] && break; kill -0 $pid 2>/dev/null || break; sleep 0.05; done
end=$(date +%s.%N)
if [ -s "$d/DevToolsActivePort" ]; then echo "ok $(python3 -c "print(round($end-$start,2))")s $chrome $*"; else echo "NO PORT after $(python3 -c "print(round($end-$start,2))")s $chrome $*"; fi
sed 's/^/   stderr: /' "$d.err" | head -12
kill $pid 2>/dev/null; wait $pid 2>/dev/null; rm -rf "$d" "$d.err"
