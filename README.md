# Matan NTFY - ML Run Notification System

A notification and monitoring system for long-running ML experiments across multiple machines.

## Components

### notify.py / notify.sh
Wraps any command and monitors it for:
- Trigger strings (e.g., "Ray Debugger is waiting", "CUDA out of memory")
- Crashes (non-zero exit codes)
- Completion

Sends notifications via ntfy to `mshtepel-ml-runs` and `mshtepel-start-ml-runs` topics.

**Usage:**
```bash
notify python train.py
# or
NTFY ./my_script.sh
```

### notify-dashboard.py / notify-dashboard.sh
Real-time dashboard that tracks all notify runs across machines.

Shows runs categorized as:
- **ONGOING** - Currently running
- **HANGING** - Hit a trigger string but still running
- **FAILED** - Exited with non-zero code
- **COMPLETED** - Finished successfully

**Usage:**
```bash
ntfy-dash
```

## Setup

1. Copy scripts to `~/bin/`
2. Make executable: `chmod +x ~/bin/notify*.{py,sh}`
3. Add aliases to `.zshrc`:
   ```bash
   alias NTFY=~/bin/notify.sh
   alias ntfy-dash=~/bin/notify-dashboard.sh
   ```
4. Subscribe to ntfy topics on your devices:
   - https://ntfy.sh/mshtepel-ml-runs
   - https://ntfy.sh/mshtepel-start-ml-runs

## How It Works

1. `notify` starts your command and sends a "start" notification
2. Monitors output for trigger strings
3. Sends notifications on triggers or crashes
4. Sends completion notification when done
5. Dashboard listens to all notifications and maintains state
6. State persists to `~/.notify_dashboard_state.json`

## Features

- ✅ Works across shared filesystems
- ✅ Tracks machine name, tmux session, working directory
- ✅ Colored terminal dashboard
- ✅ Push notifications to any device
- ✅ Persistent state across dashboard restarts
