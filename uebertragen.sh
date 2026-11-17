#!/usr/bin/env bash
# Überträgt den Ordner pipeline/ (Quelle im privaten App-Repository) ins öffentliche Daten-Repository,
# wo der Job stündlich läuft. Jeder Push dorthin startet sofort einen Lauf.
# Aufruf vom Projektordner aus:  bash pipeline/uebertragen.sh
set -euo pipefail

ZIEL_REPO="https://github.com/luigibaumigi/schoenes-wetter-daten.git"
quelle="$(cd "$(dirname "$0")" && pwd)"
version="$(git -C "$quelle" rev-parse --short HEAD)"
if [ -n "$(git -C "$quelle" status --porcelain -- .)" ]; then
  echo "pipeline/ hat nicht eingecheckte Änderungen. Erst committen." >&2
  exit 1
fi

arbeit="$(mktemp -d)"
git clone --quiet "$ZIEL_REPO" "$arbeit/daten"
# Alles außer .git ersetzen, damit gelöschte Dateien auch dort verschwinden
git -C "$arbeit/daten" ls-files -z | xargs -0 -r git -C "$arbeit/daten" rm --quiet
# vom Projektordner aus packen: aus einem Unterordner heraus beschränkt git archive zusätzlich auf diesen Pfad
oben="$(git -C "$quelle" rev-parse --show-toplevel)"
git -C "$oben" archive "HEAD:$(git -C "$quelle" rev-parse --show-prefix)" | tar -x -f - -C "$arbeit/daten"
git -C "$arbeit/daten" add -A
if git -C "$arbeit/daten" diff --cached --quiet; then
  echo "Nichts zu übertragen, das Daten-Repository ist auf dem Stand $version."
else
  git -C "$arbeit/daten" commit --quiet -m "Abgleich mit schoenes-wetter $version"
  git -C "$arbeit/daten" push --quiet origin HEAD:main
  echo "Übertragen: Stand $version. Der Job läuft jetzt einmal los."
fi
