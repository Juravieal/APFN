#!/bin/bash
# One-time setup for The Huddle's comment service on the APFN server. Run as juravieal:
#   ssh -t juravieal@167.172.255.195 sudo bash /home/apfn/APFN/server/setup-huddle.sh
# It installs the apfn-huddle service (scripts/huddle_api.py on 127.0.0.1:8081, running as apfn)
# and has Caddy send /api/* to it. Safe to run again.
set -euo pipefail

install -d -o apfn -g apfn -m 700 /home/apfn/huddle

cat > /etc/systemd/system/apfn-huddle.service <<'EOF'
[Unit]
Description=APFN Huddle comments and votes
After=network.target

[Service]
User=apfn
Group=apfn
WorkingDirectory=/home/apfn/APFN
Environment=HUDDLE_DB=/home/apfn/huddle/huddle.db
ExecStart=/home/apfn/venv/bin/python scripts/huddle_api.py serve --port 8081
# apfn-build stops it after pulling a new huddle_api.py; systemd starts the new one.
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/apfn/huddle

[Install]
WantedBy=multi-user.target
EOF

cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-huddle
cat > /etc/caddy/Caddyfile <<'EOF'
# APFN League Hub. Served on this machine only (127.0.0.1); the Cloudflare Tunnel connects here.
:8080 {
	bind 127.0.0.1
	encode zstd gzip
	# The Huddle's comments and votes
	handle /api/* {
		reverse_proxy 127.0.0.1:8081
	}
	handle {
		root * /var/www/apfn
		file_server
	}
}
EOF
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

systemctl daemon-reload
systemctl enable --now apfn-huddle
systemctl reload caddy
sleep 2
curl -fsS http://127.0.0.1:8080/api/huddle >/dev/null && echo "The Huddle is up."
