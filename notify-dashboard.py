#!/usr/bin/env python3

import json
import os
import select
import sys
import termios
import time
import tty
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from threading import Thread, RLock
from typing import Dict, List
import urllib.request
import urllib.error

NTFY_TOPIC = "mshtepel-ml-runs"
NTFY_START_TOPIC = "mshtepel-start-ml-runs"
NTFY_WANDB_TOPIC = "mshtepel-wandburl-ml-runs"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}/json"
NTFY_START_URL = f"https://ntfy.sh/{NTFY_START_TOPIC}/json"
NTFY_WANDB_URL = f"https://ntfy.sh/{NTFY_WANDB_TOPIC}/json"

STATE_FILE = Path.home() / '.notify_dashboard_state.json'
DEBUG_LOG = Path.home() / '.notify_dashboard_debug.log'

class Dashboard:
    def __init__(self):
        self.state_lock = RLock()  # Reentrant lock to prevent deadlock
        self.runs: Dict[str, dict] = {}  # run_id -> run info
        self.status_message = ""  # Status message to display
        self.load_state()

    def load_state(self):
        """Load state from disk"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.runs = data.get('runs', {})
                    print(f"[dashboard] Loaded {len(self.runs)} runs from state file", file=sys.stderr)
            except Exception as e:
                print(f"[dashboard] Failed to load state: {e}", file=sys.stderr)

    def save_state(self):
        """Save state to disk"""
        try:
            with self.state_lock:
                data = {'runs': self.runs}
                with open(STATE_FILE, 'w') as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[dashboard] Failed to save state: {e}", file=sys.stderr)

    def handle_start(self, data):
        """Handle start notification"""
        run_id = data.get('run_id')
        if not run_id:
            return

        with self.state_lock:
            self.runs[run_id] = {
                'run_id': run_id,
                'command': data.get('command', ''),
                'machine': data.get('machine', ''),
                'tmux': data.get('tmux'),
                'cwd': data.get('cwd', ''),
                'start_time': data.get('timestamp', datetime.now().isoformat()),
                'status': 'ongoing',
                'triggers': [],
                'exit_code': None,
                'wandb_url': None
            }
        self.save_state()

    def handle_trigger(self, data, body):
        """Handle trigger notification"""
        run_id = data.get('run_id')
        if not run_id:
            return

        trigger = data.get('trigger', '')

        with self.state_lock:
            if run_id in self.runs:
                if trigger not in self.runs[run_id]['triggers']:
                    self.runs[run_id]['triggers'].append(trigger)
                # Only update status change time if status is actually changing
                if self.runs[run_id]['status'] != 'hanging':
                    self.runs[run_id]['status_change_time'] = datetime.now().isoformat()
                self.runs[run_id]['status'] = 'hanging'
        self.save_state()

    def handle_wandb(self, data):
        """Handle wandb URL notification"""
        run_id = data.get('run_id')
        if not run_id:
            with open(DEBUG_LOG, 'a') as f:
                f.write(f"[{datetime.now().isoformat()}] handle_wandb: No run_id in data\n")
                f.flush()
            return

        wandb_url = data.get('wandb_url')
        with open(DEBUG_LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] handle_wandb: run_id={run_id}, wandb_url={wandb_url}\n")
            f.write(f"[{datetime.now().isoformat()}] handle_wandb: run_id in runs? {run_id in self.runs}\n")
            f.flush()

        with self.state_lock:
            if run_id in self.runs:
                self.runs[run_id]['wandb_url'] = wandb_url
                with open(DEBUG_LOG, 'a') as f:
                    f.write(f"[{datetime.now().isoformat()}] handle_wandb: Updated run with wandb_url\n")
                    f.flush()
        self.save_state()

    def handle_complete(self, data):
        """Handle completion notification"""
        run_id = data.get('run_id')
        if not run_id:
            return

        exit_code = data.get('exit_code', 0)

        with self.state_lock:
            if run_id in self.runs:
                self.runs[run_id]['exit_code'] = exit_code
                self.runs[run_id]['end_time'] = data.get('timestamp', datetime.now().isoformat())
                self.runs[run_id]['status_change_time'] = data.get('timestamp', datetime.now().isoformat())

                # Determine final status
                if self.runs[run_id]['status'] == 'hanging':
                    # Was hanging, now finished
                    if exit_code == 0:
                        self.runs[run_id]['status'] = 'completed'
                    else:
                        self.runs[run_id]['status'] = 'failed'
                else:
                    # Was ongoing, now finished
                    if exit_code == 0:
                        self.runs[run_id]['status'] = 'completed'
                    else:
                        self.runs[run_id]['status'] = 'failed'
        self.save_state()

    def flush_category(self, status):
        """Remove all runs with the given status"""
        with self.state_lock:
            to_remove = [run_id for run_id, run in self.runs.items() if run.get('status') == status]
            for run_id in to_remove:
                del self.runs[run_id]
        self.save_state()
        return len(to_remove)

    def flush_all_finished(self):
        """Remove all completed and failed runs"""
        with self.state_lock:
            to_remove = [run_id for run_id, run in self.runs.items()
                        if run.get('status') in ['completed', 'failed']]
            for run_id in to_remove:
                del self.runs[run_id]
        self.save_state()
        return len(to_remove)

    def categorize_runs(self):
        """Categorize runs into ONGOING, HANGING, FAILED, COMPLETED"""
        with self.state_lock:
            ongoing = []
            hanging = []
            failed = []
            completed = []

            for run_id, run in self.runs.items():
                status = run.get('status', 'ongoing')
                # Create a copy and add run_id (don't mutate original)
                run_copy = run.copy()
                run_copy['run_id'] = run_id
                if status == 'ongoing':
                    ongoing.append(run_copy)
                elif status == 'hanging':
                    hanging.append(run_copy)
                elif status == 'failed':
                    failed.append(run_copy)
                elif status == 'completed':
                    completed.append(run_copy)

            # Sort by start time (most recent first)
            for lst in [ongoing, hanging, failed, completed]:
                lst.sort(key=lambda x: x.get('start_time', ''), reverse=True)

            return {
                'ONGOING': ongoing[:6],
                'HANGING': hanging[:6],
                'FAILED': failed[:6],
                'COMPLETED': completed[:6]
            }

    def format_time_ago(self, iso_time):
        """Format time as 'Xm ago' or 'Xh ago'"""
        try:
            start = datetime.fromisoformat(iso_time)
            now = datetime.now()
            delta = now - start

            total_seconds = delta.total_seconds()
            if total_seconds < 60:
                return f"{int(total_seconds)}s ago"
            elif total_seconds < 3600:
                return f"{int(total_seconds / 60)}m ago"
            elif total_seconds < 86400:
                return f"{int(total_seconds / 3600)}h ago"
            else:
                return f"{int(total_seconds / 86400)}d ago"
        except:
            return "unknown"

    def delete_run_by_index(self, category, index):
        """Delete a run by its index (1-6) within a category"""
        try:
            with self.state_lock:
                # Get all runs for this category
                runs_in_category = [run_id for run_id, run in self.runs.items()
                                   if run.get('status') == category.lower()]
                runs_in_category.sort(key=lambda rid: self.runs[rid].get('start_time', ''), reverse=True)

                with open(DEBUG_LOG, 'a') as f:
                    f.write(f"[{datetime.now().isoformat()}] delete_run_by_index: category={category}, index={index}\n")
                    f.write(f"[{datetime.now().isoformat()}] delete_run_by_index: found {len(runs_in_category)} runs in {category}\n")
                    f.write(f"[{datetime.now().isoformat()}] delete_run_by_index: runs_in_category={runs_in_category[:6]}\n")
                    f.flush()

                # Get the run at the specified index
                if 0 <= index - 1 < len(runs_in_category):
                    run_id = runs_in_category[index - 1]
                    with open(DEBUG_LOG, 'a') as f:
                        f.write(f"[{datetime.now().isoformat()}] delete_run_by_index: deleting run_id={run_id}\n")
                        f.flush()
                    del self.runs[run_id]
                    self.save_state()
                    with open(DEBUG_LOG, 'a') as f:
                        f.write(f"[{datetime.now().isoformat()}] delete_run_by_index: SUCCESS\n")
                        f.flush()
                    return True
                else:
                    with open(DEBUG_LOG, 'a') as f:
                        f.write(f"[{datetime.now().isoformat()}] delete_run_by_index: FAILED - index out of range\n")
                        f.flush()
            return False
        except Exception as e:
            with open(DEBUG_LOG, 'a') as f:
                f.write(f"[{datetime.now().isoformat()}] Error in delete_run_by_index: {e}\n")
                import traceback
                f.write(traceback.format_exc())
                f.flush()
            return False

    def display(self):
        """Display the dashboard"""
        categories = self.categorize_runs()

        # ANSI color codes
        RESET = '\033[0m'
        BOLD = '\033[1m'
        GREEN = '\033[92m'
        RED = '\033[91m'
        CYAN = '\033[96m'
        YELLOW = '\033[93m'
        ORANGE = '\033[38;5;208m'
        GRAY = '\033[90m'

        # Move cursor to home (don't clear yet - we'll overwrite)
        print('\033[H', end='')
        sys.stdout.flush()

        print(BOLD + CYAN + "=" * 110 + RESET)
        print(BOLD + CYAN + "NOTIFY DASHBOARD".center(110) + RESET)
        print(GRAY + f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".center(110) + RESET)
        print(BOLD + CYAN + "=" * 110 + RESET)
        print()

        # Color map for categories
        category_colors = {
            'ONGOING': CYAN,
            'HANGING': ORANGE,
            'FAILED': RED,
            'COMPLETED': GREEN
        }

        for category_name, runs in categories.items():
            count = len(runs)
            # Get total count (not just last 6)
            with self.state_lock:
                total_count = sum(1 for r in self.runs.values() if r.get('status') == category_name.lower())

            color = category_colors.get(category_name, RESET)
            print(f"\n{BOLD}{color}{category_name} ({total_count}):{RESET}")
            print(GRAY + "-" * 110 + RESET)

            if not runs:
                print(GRAY + "  (none)" + RESET)
            else:
                for idx, run in enumerate(runs, 1):
                    start_time_ago = self.format_time_ago(run.get('start_time', ''))
                    machine = run.get('machine', 'unknown').split('.')[0]  # Shorten hostname
                    tmux = run.get('tmux')
                    cwd = run.get('cwd', '')
                    command = run.get('command', '')

                    # Extract just the script/command name (first word, basename only)
                    command_parts = command.split()
                    if command_parts:
                        cmd_name = command_parts[0].split('/')[-1]  # Get basename
                    else:
                        cmd_name = command[:50]

                    # Format time display
                    if category_name == 'ONGOING':
                        time_display = f"{start_time_ago:>8}"
                    else:
                        # Show both start and status change time
                        status_change = run.get('status_change_time') or run.get('end_time') or run.get('start_time')
                        status_time_ago = self.format_time_ago(status_change) if status_change else "unknown"
                        time_display = f"{start_time_ago:>8}→{status_time_ago:>8}"

                    # Main line: number, time, command
                    print(f"{CYAN}[{idx}]{RESET} {GRAY}[{time_display}]{RESET} {BOLD}{cmd_name}{RESET}")

                    # Metadata lines with └─
                    # Show wandb URL if available
                    wandb_url = run.get('wandb_url')
                    if wandb_url:
                        print(f"    {CYAN}└─ W&B: {wandb_url}{RESET}")

                    # Show tmux if available
                    if tmux:
                        print(f"    {GRAY}└─ Tmux: {tmux}{RESET}")

                    # Show machine
                    print(f"    {GRAY}└─ Machine: {machine}{RESET}")

                    # Show directory
                    if cwd:
                        # Shorten path if too long
                        display_cwd = cwd if len(cwd) < 80 else '...' + cwd[-77:]
                        print(f"    {GRAY}└─ Dir: {display_cwd}{RESET}")

                    # Show triggers if hanging
                    if category_name == 'HANGING' and run.get('triggers'):
                        for trigger in run.get('triggers'):
                            print(f"    {ORANGE}└─ Trigger: {trigger}{RESET}")

                    # Show exit code if failed
                    if category_name == 'FAILED':
                        exit_code = run.get('exit_code', 'unknown')
                        print(f"    {RED}└─ Exit code: {exit_code}{RESET}")

        print("\n" + CYAN + "=" * 110 + RESET)

        # Show status message if present
        if self.status_message:
            print(BOLD + YELLOW + self.status_message + RESET)
        else:
            print(GRAY + "Delete item: " + RESET +
                  CYAN + "[1-6]" + RESET + " then " +
                  CYAN + "[o/h/f/c]" + RESET + " (ongoing/hanging/failed/completed)  |  " +
                  GRAY + "Flush: " + RESET +
                  CYAN + "[Shift+F/C/H/A]" + RESET + "  |  " +
                  GRAY + "Exit: " + RESET + "Ctrl+C")

        print(CYAN + "=" * 110 + RESET)

        # NOW clear from cursor to end of screen (removes any leftover lines from previous display)
        print('\033[0J', end='')
        sys.stdout.flush()

def listen_to_stream(url, dashboard, event_type):
    """Listen to ntfy SSE stream"""
    while True:
        try:
            req = urllib.request.Request(url)
            req.add_header('Accept', 'application/x-ndjson')

            with urllib.request.urlopen(req, timeout=None) as response:
                for line in response:
                    if not line:
                        continue

                    try:
                        msg = json.loads(line.decode('utf-8'))

                        # Skip keepalive messages
                        if msg.get('event') == 'keepalive':
                            continue

                        # Parse message
                        if event_type == 'start':
                            # Start messages have JSON in the message field
                            message = msg.get('message', '')
                            if message:
                                try:
                                    data = json.loads(message)
                                    if data.get('event') == 'start':
                                        dashboard.handle_start(data)
                                except json.JSONDecodeError:
                                    pass
                        elif event_type == 'wandb':
                            # Wandb messages have JSON in the message field
                            message = msg.get('message', '')
                            if message:
                                try:
                                    data = json.loads(message)
                                    with open(DEBUG_LOG, 'a') as f:
                                        f.write(f"[{datetime.now().isoformat()}] Received wandb message\n")
                                        f.write(f"  data = {data}\n")
                                        f.flush()
                                    if data.get('event') == 'wandb':
                                        dashboard.handle_wandb(data)
                                        with open(DEBUG_LOG, 'a') as f:
                                            f.write(f"[{datetime.now().isoformat()}] Called handle_wandb\n")
                                            f.flush()
                                except json.JSONDecodeError:
                                    pass
                        else:
                            # Main topic - check for event type in message
                            message = msg.get('message', '')

                            # Parse message as JSON
                            if message:
                                try:
                                    data = json.loads(message)
                                    event_type = data.get('event')

                                    # DEBUG: Write what we received to log file
                                    with open(DEBUG_LOG, 'a') as f:
                                        f.write(f"[{datetime.now().isoformat()}] Received message on main topic\n")
                                        f.write(f"  event = {event_type}\n")
                                        f.write(f"  data = {data}\n")
                                        f.flush()

                                    if event_type == 'complete':
                                        dashboard.handle_complete(data)
                                    elif event_type == 'trigger':
                                        with open(DEBUG_LOG, 'a') as f:
                                            f.write(f"[{datetime.now().isoformat()}] Detected trigger event! Run ID: {data.get('run_id')}\n")
                                            f.flush()
                                        dashboard.handle_trigger(data, message)
                                except json.JSONDecodeError:
                                    pass

                    except Exception as e:
                        print(f"[dashboard] Error processing message: {e}", file=sys.stderr)

        except Exception as e:
            print(f"[dashboard] Connection error on {event_type}: {e}", file=sys.stderr)
            time.sleep(5)  # Wait before reconnecting

def main():
    dashboard = Dashboard()

    # Start listener threads
    start_thread = Thread(target=listen_to_stream, args=(NTFY_START_URL, dashboard, 'start'), daemon=True)
    main_thread = Thread(target=listen_to_stream, args=(NTFY_URL, dashboard, 'main'), daemon=True)
    wandb_thread = Thread(target=listen_to_stream, args=(NTFY_WANDB_URL, dashboard, 'wandb'), daemon=True)

    start_thread.start()
    main_thread.start()
    wandb_thread.start()

    print("[dashboard] Connecting to ntfy streams...", file=sys.stderr)
    time.sleep(2)  # Give threads time to connect

    # Set terminal to non-blocking mode for keyboard input
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        # Enter alternate screen buffer (prevents flicker)
        print('\033[?1049h', end='')
        sys.stdout.flush()

        tty.setcbreak(sys.stdin.fileno())

        # Display loop
        selected_number = None
        while True:
            dashboard.display()

            # Check for keyboard input with timeout
            start_time = time.time()
            while time.time() - start_time < 3:  # Refresh every 3 seconds
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    key = sys.stdin.read(1)

                    # Debug: log every key press
                    with open(DEBUG_LOG, 'a') as f:
                        f.write(f"[{datetime.now().isoformat()}] Key pressed: '{key}' (ord={ord(key)}), selected_number={selected_number}\n")
                        f.flush()

                    # Check if it's a number (1-6)
                    if key in '123456':
                        selected_number = int(key)
                        dashboard.status_message = f"Selected [{selected_number}]. Now press: [o]=ONGOING  [h]=HANGING  [f]=FAILED  [c]=COMPLETED"
                        with open(DEBUG_LOG, 'a') as f:
                            f.write(f"[{datetime.now().isoformat()}] User selected number: {selected_number}\n")
                            f.flush()
                        break  # Redisplay to show the selection message

                    # Check for delete by category (lowercase)
                    if selected_number and key in 'ohfc':
                        category_map = {'o': 'ongoing', 'h': 'hanging', 'f': 'failed', 'c': 'completed'}
                        category = category_map[key]
                        with open(DEBUG_LOG, 'a') as f:
                            f.write(f"[{datetime.now().isoformat()}] DELETE: User pressed category: {key} ({category})\n")
                            f.write(f"[{datetime.now().isoformat()}] DELETE: Attempting to delete item {selected_number} from {category}\n")
                            f.flush()
                        if dashboard.delete_run_by_index(category, selected_number):
                            dashboard.status_message = f"✓ Deleted item [{selected_number}] from {category.upper()}"
                        else:
                            dashboard.status_message = f"✗ Item [{selected_number}] not found in {category.upper()}"
                        selected_number = None
                        time.sleep(1)
                        break
                    elif selected_number and key not in '123456':
                        # User has selected a number but pressed an invalid key
                        with open(DEBUG_LOG, 'a') as f:
                            f.write(f"[{datetime.now().isoformat()}] SKIP: selected_number={selected_number} but key '{key}' not in 'ohfc'\n")
                            f.flush()

                    # Flush commands (uppercase)
                    if key == 'F':
                        count = dashboard.flush_category('failed')
                        dashboard.status_message = f"✓ Flushed {count} FAILED run(s)"
                        selected_number = None
                        time.sleep(1)
                        break
                    elif key == 'C':
                        count = dashboard.flush_category('completed')
                        dashboard.status_message = f"✓ Flushed {count} COMPLETED run(s)"
                        selected_number = None
                        time.sleep(1)
                        break
                    elif key == 'H':
                        count = dashboard.flush_category('hanging')
                        dashboard.status_message = f"✓ Flushed {count} HANGING run(s)"
                        selected_number = None
                        time.sleep(1)
                        break
                    elif key == 'A':
                        count = dashboard.flush_all_finished()
                        dashboard.status_message = f"✓ Flushed {count} finished run(s)"
                        selected_number = None
                        time.sleep(1)
                        break

            # Clear status message after it's been displayed for one cycle
            if dashboard.status_message and not selected_number:
                dashboard.status_message = ""

    except KeyboardInterrupt:
        pass
    finally:
        # Exit alternate screen buffer
        print('\033[?1049l', end='')
        sys.stdout.flush()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        print("\n[dashboard] Exiting...", file=sys.stderr)
        sys.exit(0)

if __name__ == '__main__':
    main()
