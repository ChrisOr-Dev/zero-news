#!/bin/sh
# Build the AppStore package. Needs dpkg-deb and python3-pil, so run it on the
# CardputerZero (or any Debian box):  sh build-deb.sh "Name <email>"
# Output: zero-news_<version>_all.deb in this folder.
set -eu
cd "$(dirname "$0")"
VERSION=$(python3 -c "import json; print(json.load(open('app-builder.json'))['version'])")
PKG=zero-news
MAINTAINER=${1:?usage: build-deb.sh "Name <email>"}

umask 022
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"
APP=$ROOT/usr/share/$PKG
L=$ROOT/usr/share/APPLaunch
mkdir -p "$APP" "$L/bin" "$L/applications" "$L/share/images" "$ROOT/DEBIAN"

install -m 644 zero_news.py feeds.py text.py settings_ui.py "$APP/"
printf '#!/bin/sh\nexec /usr/bin/python3 /usr/share/%s/zero_news.py "$@"\n' "$PKG" > "$L/bin/$PKG"
chmod 755 "$L/bin/$PKG"
install -m 644 zero-news.desktop "$L/applications/$PKG.desktop"
python3 zero_news.py --make-icon "$L/share/images/$PKG.png"
chmod 644 "$L/share/images/$PKG.png"

cat > "$ROOT/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: all
Maintainer: $MAINTAINER
Depends: python3, python3-pil, python3-numpy
Section: utils
Priority: optional
Installed-Size: $(du -sk "$ROOT/usr" | cut -f1)
Description: Rotating news headlines for CardputerZero
 Shows one headline at a time in large type with a ticker of the next ones.
 Sources are RSS feeds chosen by language (English / Chinese) and category
 (tech / life / general) in the in-app settings (S key).
EOF

dpkg-deb --root-owner-group --build "$ROOT" "${PKG}_${VERSION}_all.deb"
