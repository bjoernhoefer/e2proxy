#!/usr/bin/env bash
# Host diagnostics for e2proxy outages (run by the self-hosted GitHub runner).
#
# Writes a report directory with raw sections + SUMMARY.md (automatic findings).
# The repo is PUBLIC: workflow logs/artifacts are world-readable, so every
# output is passed through redact() and config.json is never dumped.
#
#   DIAG_OUT       output directory (default: ./diag-report)
#   DIAG_SINCE     journal window (default: "7 days ago")
#   E2PROXY_DIR    deployment directory (default: $HOME/e2proxy)
#   E2PROXY_URL    base URL (default: http://localhost:8888)
#   CONTAINER      container name (default: e2proxy)
set -uo pipefail

OUT="${DIAG_OUT:-$PWD/diag-report}"
SINCE="${DIAG_SINCE:-7 days ago}"
E2PROXY_DIR="${E2PROXY_DIR:-$HOME/e2proxy}"
E2PROXY_URL="${E2PROXY_URL:-http://localhost:8888}"
CONTAINER="${CONTAINER:-e2proxy}"
REC_MOUNT="${REC_MOUNT:-/mnt/nvme}"
DOCKER_SINCE="${DOCKER_SINCE:-168h}"

mkdir -p "$OUT"
SUMMARY="$OUT/SUMMARY.md"
FINDINGS=()

redact() {
  sed -E \
    -e 's/([?&](X-Plex-Token|token|api_key|apikey|key|password|pass|auth)=)[^&[:space:]"]+/\1***/Ig' \
    -e 's/("?(plex_token|tmdb_api_key|tvdb_api_key|token|password|api_key|secret)"?[[:space:]]*[:=][[:space:]]*"?)[^",[:space:]]+/\1***/Ig' \
    -e 's/(Authorization:[[:space:]]*)[^[:space:]]+([[:space:]]+[^[:space:]]+)?/\1***/Ig' \
    -e 's/(Bearer[[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1***/g' \
    -e 's#(https?://)[^/:@[:space:]]+:[^/@[:space:]]+@#\1***:***@#g'
}

# Run with root rights when possible (non-interactive), otherwise as-is.
S() {
  if [ "$(id -u)" = 0 ]; then "$@"
  elif command -v sudo >/dev/null && sudo -n true 2>/dev/null; then sudo -n "$@"
  else "$@"; fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# section <file> <title> <command...>
section() {
  local file="$OUT/$1"; shift
  local title="$1"; shift
  {
    echo "### $title"
    echo "\$ $*"
    timeout 120 bash -c "$*" 2>&1
    echo "[exit=$?]"
    echo
  } | redact >>"$file"
}

finding() { FINDINGS+=("$1"); echo "FINDING: $1"; }

DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo"
dk() { if [ "$DOCKER" = docker ]; then docker "$@"; else S docker "$@"; fi; }
export -f S dk have
export DOCKER

echo "== Collecting diagnostics into $OUT (journal since: $SINCE)"

# ── System / Boots ───────────────────────────────────────────
section 01-system.txt "Identity" "date; hostname; uptime; uname -a; cat /etc/os-release; id; nproc"
section 01-system.txt "Virtualisation / board" "systemd-detect-virt 2>/dev/null; cat /proc/device-tree/model 2>/dev/null; echo; cat /sys/class/dmi/id/product_name 2>/dev/null"
section 01-system.txt "Boots" "S journalctl --list-boots --no-pager | tail -30"
section 01-system.txt "last reboot/shutdown" "last -x -F reboot shutdown 2>/dev/null | head -40"
section 01-system.txt "End of previous boot (crash context)" "S journalctl -b -1 -n 150 --no-pager -o short-iso"
section 01-system.txt "Failed units" "systemctl --failed --no-pager"
section 01-system.txt "Errors since $SINCE" "S journalctl -p err --since '$SINCE' --no-pager -o short-iso | tail -300"

# ── Resources ────────────────────────────────────────────────
section 02-resources.txt "Memory" "free -m; echo; swapon --show; echo; cat /proc/pressure/memory /proc/pressure/cpu /proc/pressure/io 2>/dev/null"
section 02-resources.txt "Load" "cat /proc/loadavg; top -b -n1 -o %CPU | head -30"
section 02-resources.txt "Top memory" "ps -eo pid,ppid,user,rss,vsz,etime,cmd --sort=-rss | head -25"
section 02-resources.txt "ffmpeg processes" "ps -eo pid,ppid,etime,rss,pcpu,cmd | grep -i [f]fmpeg"
section 02-resources.txt "Zombie processes" "ps -eo pid,ppid,stat,cmd | awk '\$3 ~ /Z/'"
section 02-resources.txt "Disk usage" "df -hT -x tmpfs -x devtmpfs -x overlay -x squashfs; echo; df -i -x tmpfs -x devtmpfs -x overlay -x squashfs"
section 02-resources.txt "Block devices" "lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,MODEL"
section 02-resources.txt "Recording mount" "findmnt $REC_MOUNT; grep -E '$REC_MOUNT|nvme' /etc/fstab"
section 02-resources.txt "Docker disk" "S du -sh /var/lib/docker 2>/dev/null; S sh -c 'du -sh /var/lib/docker/containers/*/*-json.log 2>/dev/null | sort -h | tail -10'; dk system df"
section 02-resources.txt "Deployment dir" "du -sh '$E2PROXY_DIR' '$E2PROXY_DIR/data' 2>/dev/null; ls -la '$E2PROXY_DIR' '$E2PROXY_DIR/data' 2>/dev/null"
section 02-resources.txt "Open files limit" "ulimit -n; cat /proc/sys/fs/file-nr"

# ── Kernel / Hardware ────────────────────────────────────────
section 03-kernel.txt "Kernel warnings since $SINCE" "S journalctl -k -p warning --since '$SINCE' --no-pager -o short-iso | tail -300"
section 03-kernel.txt "Kernel critical patterns" "S journalctl -k --since '$SINCE' --no-pager -o short-iso | grep -iE 'oom|killed process|out of memory|hung task|blocked for more than|i/o error|nvme|ext4-fs error|xfs.*error|watchdog|soft lockup|hard lockup|thermal|throttl|under-voltage|voltage|segfault|mce|panic|reset' | tail -200"
section 03-kernel.txt "dmesg tail" "S dmesg -T 2>/dev/null | tail -100"
section 03-kernel.txt "Temperatures" "sensors 2>/dev/null; for z in /sys/class/thermal/thermal_zone*/temp; do echo \$z \$(cat \$z); done 2>/dev/null"
section 03-kernel.txt "Raspberry Pi throttling" "vcgencmd get_throttled 2>/dev/null; vcgencmd measure_temp 2>/dev/null; vcgencmd measure_volts 2>/dev/null"
section 03-kernel.txt "SMART / NVMe health" "for d in \$(lsblk -dno NAME | grep -vE '^(loop|ram|zram)'); do echo \"-- \$d\"; S smartctl -H -A /dev/\$d 2>/dev/null | head -40; done; S nvme smart-log /dev/nvme0 2>/dev/null"

# ── Docker / e2proxy ─────────────────────────────────────────
section 04-docker.txt "docker ps -a" "dk ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.RunningFor}}'"
section 04-docker.txt "Container state" "dk inspect $CONTAINER --format 'Status={{.State.Status}} Restarts={{.RestartCount}} OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}} Error={{.State.Error}} StartedAt={{.State.StartedAt}} FinishedAt={{.State.FinishedAt}} Health={{if .State.Health}}{{.State.Health.Status}} FailingStreak={{.State.Health.FailingStreak}}{{end}}'"
section 04-docker.txt "Container limits/logging" "dk inspect $CONTAINER --format 'Memory={{.HostConfig.Memory}} NanoCpus={{.HostConfig.NanoCpus}} PidsLimit={{.HostConfig.PidsLimit}} Restart={{.HostConfig.RestartPolicy.Name}} LogDriver={{.HostConfig.LogConfig.Type}} LogOpts={{json .HostConfig.LogConfig.Config}} LogPath={{.LogPath}}'"
section 04-docker.txt "Health log" "dk inspect $CONTAINER --format '{{if .State.Health}}{{range .State.Health.Log}}{{.Start}} exit={{.ExitCode}} {{.Output}}{{println}}{{end}}{{end}}'"
section 04-docker.txt "Container stats" "dk stats --no-stream"
section 04-docker.txt "Docker events (container die/oom/restart/health)" "dk events --since $DOCKER_SINCE --until 0s --filter type=container --filter event=die --filter event=oom --filter event=restart --filter event=kill --filter event=health_status --format '{{.Time}} {{.Actor.Attributes.name}} {{.Action}} exit={{.Actor.Attributes.exitCode}}' | tail -200"
section 04-docker.txt "Docker daemon journal" "S journalctl -u docker -u containerd --since '$SINCE' --no-pager -o short-iso | grep -viE 'level=info' | tail -150"
section 04-docker.txt "Process tree in container" "dk top $CONTAINER -eo pid,ppid,etime,rss,pcpu,args"

section 05-e2proxy.txt "Health / version" "curl -sS -m 10 -w '\nHTTP %{http_code} in %{time_total}s\n' $E2PROXY_URL/api/health; curl -sS -m 10 $E2PROXY_URL/api/version"
section 05-e2proxy.txt "Status (receiver probe)" "curl -sS -m 30 -w '\nHTTP %{http_code} in %{time_total}s\n' $E2PROXY_URL/api/status | head -c 4000"
section 05-e2proxy.txt "Container log: startup/crash markers" "dk logs --since $DOCKER_SINCE $CONTAINER 2>&1 | grep -nE 'e2proxy v[0-9]|Proxy stopping|Traceback|Error:|Exception|MemoryError|Too many open files|No space left|Temporary failure|Could not resolve|E: |apt|Killed|Segmentation' | tail -200"
section 05-e2proxy.txt "Container log: WARNING/ERROR (last 300)" "dk logs --since $DOCKER_SINCE $CONTAINER 2>&1 | grep -E '\[(WARNING|ERROR|CRITICAL)\]' | tail -300"
section 05-e2proxy.txt "Container log: level histogram" "dk logs --since $DOCKER_SINCE $CONTAINER 2>&1 | grep -oE '\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]' | sort | uniq -c"
section 05-e2proxy.txt "Container log: tail" "dk logs --tail 200 $CONTAINER 2>&1"
section 05-e2proxy.txt "e2proxy.log errors (file logs)" "ls -la '$E2PROXY_DIR/data'/e2proxy.log* 2>/dev/null; cat '$E2PROXY_DIR/data'/e2proxy.log* 2>/dev/null | grep -E '\[(ERROR|CRITICAL)\]|Traceback' | tail -200"
section 05-e2proxy.txt "Deployed compose" "cat '$E2PROXY_DIR/docker-compose.e2proxy.yml'"

# ── Automatic findings ───────────────────────────────────────
pct() { df --output=pcent "$1" 2>/dev/null | tail -1 | tr -dc '0-9'; }
for m in / /var/lib/docker "$E2PROXY_DIR" "$REC_MOUNT"; do
  p="$(pct "$m")"; [ -n "$p" ] && [ "$p" -ge 90 ] && finding "Disk $m is ${p}% full"
done
findmnt "$REC_MOUNT" >/dev/null 2>&1 || finding "$REC_MOUNT is NOT a mount point — recordings may fill the system disk"

oom=$(S journalctl -k --since "$SINCE" --no-pager 2>/dev/null | grep -ciE 'out of memory|oom-kill|killed process')
[ "${oom:-0}" -gt 0 ] && finding "Kernel OOM killer fired $oom time(s) since $SINCE"
io=$(S journalctl -k --since "$SINCE" --no-pager 2>/dev/null | grep -ciE 'i/o error|nvme.*(timeout|reset)|ext4-fs error')
[ "${io:-0}" -gt 0 ] && finding "$io storage error line(s) in kernel log (I/O / NVMe / ext4)"
hung=$(S journalctl -k --since "$SINCE" --no-pager 2>/dev/null | grep -ciE 'hung task|blocked for more than|soft lockup')
[ "${hung:-0}" -gt 0 ] && finding "$hung hung-task/lockup message(s) in kernel log"
uv=$(S journalctl -k --since "$SINCE" --no-pager 2>/dev/null | grep -ciE 'under-voltage|voltage normalised')
[ "${uv:-0}" -gt 0 ] && finding "Under-voltage reported $uv time(s) — check power supply"
if have vcgencmd; then
  th=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
  [ -n "$th" ] && [ "$th" != "0x0" ] && finding "Raspberry Pi get_throttled=$th (under-voltage/throttling occurred)"
fi
boots=$(S journalctl --list-boots --no-pager 2>/dev/null | awk -v s="$(date -d "$SINCE" +%s 2>/dev/null || echo 0)" '{cmd="date -d \""$4" "$5"\" +%s"; cmd | getline t; close(cmd); if (t>=s) n++} END{print n+0}')
[ "${boots:-0}" -gt 1 ] && finding "$boots boots since $SINCE — host rebooted $((boots-1)) time(s)"
if S journalctl -b -1 -n 1 --no-pager >/dev/null 2>&1; then
  S journalctl -b -1 -n 40 --no-pager 2>/dev/null | grep -qiE 'reached target.*(shutdown|reboot|power-off)|systemd-shutdown|Journal stopped' \
    || finding "Previous boot ended WITHOUT a clean shutdown (crash, power loss or hard reset)"
fi

if dk inspect "$CONTAINER" >/dev/null 2>&1; then
  st=$(dk inspect "$CONTAINER" --format '{{.State.Status}}|{{.RestartCount}}|{{.State.OOMKilled}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.HostConfig.LogConfig.Config}}|{{.HostConfig.Memory}}|{{.State.StartedAt}}')
  IFS='|' read -r cst crs coom chealth clog cmem cstart <<<"$st"
  [ "$cst" != running ] && finding "Container $CONTAINER is $cst"
  [ "${crs:-0}" -gt 0 ] && finding "Container restart count: $crs"
  [ "$coom" = true ] && finding "Container was OOM-killed"
  [ -n "$chealth" ] && [ "$chealth" != healthy ] && finding "Container health: $chealth"
  [[ "$clog" != *max-size* ]] && finding "Docker log of $CONTAINER is unbounded (no max-size)"
  [ "${cmem:-0}" = 0 ] && finding "No memory limit on $CONTAINER"
  echo "Container started at: $cstart" >>"$OUT/04-docker.txt"
  lp=$(dk inspect "$CONTAINER" --format '{{.LogPath}}')
  ls=$(S stat -c %s "$lp" 2>/dev/null)
  [ -n "$ls" ] && [ "$ls" -gt $((500*1024*1024)) ] && finding "Docker json log is $((ls/1024/1024)) MB"
  dies=$(dk events --since "$DOCKER_SINCE" --until 0s --filter "container=$CONTAINER" --filter event=die --format x 2>/dev/null | wc -l)
  [ "${dies:-0}" -gt 0 ] && finding "Container died $dies time(s) in last $DOCKER_SINCE (docker events)"
  aptfail=$(dk logs --since "$DOCKER_SINCE" "$CONTAINER" 2>&1 | grep -ciE 'Temporary failure resolving|Unable to fetch|E: Unable to locate|Could not resolve')
  [ "${aptfail:-0}" -gt 0 ] && finding "apt-get failures at container start ($aptfail lines) — startup depends on network"
  starts=$(dk logs --since "$DOCKER_SINCE" "$CONTAINER" 2>&1 | grep -cE 'e2proxy v[0-9.]+ started')
  finding "e2proxy process started $starts time(s) in last $DOCKER_SINCE"
else
  finding "Container $CONTAINER not found / docker not accessible for runner user ($(id -un))"
fi
curl -fsS -m 10 "$E2PROXY_URL/api/health" >/dev/null 2>&1 || finding "Health endpoint $E2PROXY_URL/api/health NOT reachable"

{
  echo "# e2proxy host diagnose — $(hostname) — $(date -Is)"
  echo
  echo "Window: journal since \`$SINCE\`, docker since \`$DOCKER_SINCE\`. Runner user: \`$(id -un)\` (sudo -n: $(sudo -n true 2>/dev/null && echo yes || echo no))"
  echo
  echo "## Findings"
  if [ ${#FINDINGS[@]} -eq 0 ]; then echo "- none detected automatically"; fi
  for f in "${FINDINGS[@]}"; do echo "- $f"; done
  echo
  echo "## Quick facts"
  echo '```'
  uptime; free -m | head -3; df -h / "$REC_MOUNT" 2>/dev/null
  echo '```'
  echo
  echo "Raw sections: $(cd "$OUT" && ls *.txt | tr '\n' ' ')"
} | redact >"$SUMMARY"

echo "== Done: $OUT"
exit 0
