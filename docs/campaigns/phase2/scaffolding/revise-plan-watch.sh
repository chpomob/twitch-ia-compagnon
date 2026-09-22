#!/bin/bash
# Phase 2 — reprend la révision du plan APRÈS le reset de la fenêtre 5h de Claude (04:50),
# puis committe le plan amendé. Aucun changement de provider : on attend le quota (règle utilisateur).
set -u
R=/media/chpo/HDD-papa/twitch-ia-compagnon
S=$R/docs/campaigns/phase2/scaffolding
LOG=$S/revise-plan-watch.log

TARGET=$(date -d "today 04:55" +%s)
NOW=$(date +%s)
[ "$TARGET" -le "$NOW" ] && TARGET=$((NOW + 60))
echo "[$(date '+%H:%M')] watcher armé — reprise prévue à $(date -d @"$TARGET" '+%H:%M')" | tee -a "$LOG"

while [ "$(date +%s)" -lt "$TARGET" ]; do sleep 60; done

cd "$R" || exit 1
echo "[$(date '+%H:%M')] quota attendu rétabli — révision du plan (branche $(git branch --show-current))" >> "$LOG"
bash /home/chpo/.config/adversarial/claude-dev.sh < "$S/revise-plan-prompt.md" > "$S/revise-plan-out.txt" 2>&1
echo "[$(date '+%H:%M')] DEV terminé (exit $?)" >> "$LOG"

if ! git diff --quiet -- plan.md; then
  cat > "$S/commit-msg-plan-revise.txt" <<'MSG'
docs(phase2): plan revision — transcription budget boundary, registry guard, service enumeration
MSG
  git add plan.md && git commit -F "$S/commit-msg-plan-revise.txt" >> "$LOG" 2>&1
  echo "[$(date '+%H:%M')] plan.md commité" >> "$LOG"
else
  echo "[$(date '+%H:%M')] plan.md inchangé — rien à committer" >> "$LOG"
fi
echo "[$(date '+%H:%M')] watcher terminé" >> "$LOG"
