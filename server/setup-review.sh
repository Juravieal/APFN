#!/bin/bash
# One-time setup for build review at https://allprofantasy.us/buildreview/. Run as juravieal:
#   ssh -t juravieal@167.172.255.195 sudo bash /home/apfn/review/server/setup-review.sh
# It adds the review comment service (apfn-huddle-review on 127.0.0.1:8082, running as apfn),
# has Caddy serve /buildreview/ from /var/www/apfn-review, and makes the notification key pair
# if there isn't one. Safe to run again.
set -euo pipefail

install -d -o apfn -g apfn -m 755 /var/www/apfn-review
sudo -u apfn env HUDDLE_DB=/home/apfn/huddle/huddle.db \
  /home/apfn/venv/bin/python /home/apfn/review/scripts/huddle_api.py pushkey

cat > /etc/systemd/system/apfn-huddle-review.service <<'EOF'
[Unit]
Description=APFN Huddle, build review copy
After=network.target

[Service]
User=apfn
Group=apfn
WorkingDirectory=/home/apfn/review
Environment=HUDDLE_DB=/home/apfn/huddle/review.db
ExecStart=/home/apfn/venv/bin/python scripts/huddle_api.py serve --port 8082
# apfn-review stops it after publishing a branch; systemd starts the new code.
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

cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-review
cat > /etc/caddy/Caddyfile <<'EOF'
# APFN League Hub. Served on this machine only (127.0.0.1); the Cloudflare Tunnel connects here.
:8080 {
	bind 127.0.0.1
	encode zstd gzip
	# The Huddle's comments and votes
	handle /api/* {
		reverse_proxy 127.0.0.1:8081
	}
	# Build review: a branch's copy of the site (server/apfn-review), with its own comment service
	redir /buildreview /buildreview/
	handle /buildreview/api/* {
		uri strip_prefix /buildreview
		reverse_proxy 127.0.0.1:8082
	}
	handle_path /buildreview/* {
		header X-Robots-Tag "noindex, nofollow"
		root * /var/www/apfn-review
		file_server
	}
	handle {
		root * /var/www/apfn
		file_server
	}
}
EOF
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

systemctl daemon-reload
systemctl enable --now apfn-huddle-review
systemctl reload caddy
echo "Build review is set up. Publish a branch with: ~/review/server/apfn-review BRANCH (as apfn)"
