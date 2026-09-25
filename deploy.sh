#!/usr/bin/env bash
# Publish the current app.html to https://lgmax.arcane-collectibles.com/
# (GitHub Pages, gh-pages branch). Run ./build.sh first.
#
# The branch is kept at a single amended commit so the 10 MB page doesn't
# pile up in history with every deploy.
set -euo pipefail
cd "$(dirname "$0")"
[ -f app.html ] || { echo "no app.html — run ./build.sh first" >&2; exit 1; }

W=$(mktemp -d)
git worktree add -q "$W" gh-pages
cp app.html "$W/index.html"
# PWA shell: manifest, service worker, and icons ship beside index.html so the
# app is installable and offline-capable. app_template.html references them by
# relative path, so they must sit at the site root next to index.html.
cp pwa/manifest.webmanifest pwa/sw.js pwa/icon-192.png pwa/icon-512.png pwa/icon-180.png "$W/"
(
  cd "$W"
  git add index.html manifest.webmanifest sw.js icon-192.png icon-512.png icon-180.png
  git commit -q --amend -m "Deploy LeafGreen Maximizer ($(date +%F))"
  git push -f origin gh-pages
)
git worktree remove -f "$W"
echo "live at https://lgmax.arcane-collectibles.com/ (allow a minute for the CDN)"
