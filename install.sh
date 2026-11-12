#!/bin/sh
# Register News with APPLaunch. Copy this folder to ~/zero-news on the
# CardputerZero, then run:  sudo sh ~/zero-news/install.sh
# The launcher entry is a small wrapper owned by pi; the app itself stays in
# ~/zero-news, so later updates need no sudo.
set -eu
cd "$(dirname "$0")"
L=/usr/share/APPLaunch
APP_DIR=$(pwd)
printf '#!/bin/sh\nexec /usr/bin/python3 %s/zero_news.py "$@"\n' "$APP_DIR" > "$L/bin/zero-news"
chown pi:pi "$L/bin/zero-news"
chmod 755 "$L/bin/zero-news"
install -m 644 zero-news.desktop "$L/applications/zero-news.desktop"
python3 zero_news.py --make-icon "$L/share/images/zero-news.png"
chmod 644 "$L/share/images/zero-news.png"
echo "Installed. Restart the launcher as pi: systemctl --user restart APPLaunch"
