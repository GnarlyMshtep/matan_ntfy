#!/usr/bin/env python3

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from threading import Thread, Lock
from typing import Dict, List
import urllib.request
import urllib.error

NTFY_TOPIC = "mshtepel-ml-runs"
NTFY_START_TOPIC = "mshtepel-start-ml-runs"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}/json"
NTFY_START_URL = f"https://ntfy.sh/{NTFY_START_TOPIC}/json"

STATE_FILE = Path.home() / '.notify_dashboard_state.json'

class Dashboard:
    def __init__(self):
        self.state_lock = Lock()
        self.runs: Dict[str, dict] = {}  # run_id -> run info
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
                'exit_code': None
            }
        self.save_state()

    def handle_trigger(self, headers, body):
        """Handle trigger notification"""
        run_id = headers.get('X-Run-ID')
        if not run_id:
            return

        # Extract trigger from title or body
        title = headers.get('Title', '')
        trigger = title.replace('🔔 Trigger: ', '').strip()

        with self.state_lock:
            if run_id in self.runs:
                if trigger not in self.runs[run_id]['triggers']:
                    self.runs[run_id]['triggers'].append(trigger)
                self.runs[run_id]['status'] = 'hanging'
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

    def categorize_runs(self):
        """Categorize runs into ONGOING, HANGING, FAILED, COMPLETED"""
        with self.state_lock:
            ongoing = []
            hanging = []
            failed = []
            completed = []

            for run_id, run in self.runs.items():
                status = run.get('status', 'ongoing')
                if status == 'ongoing':
                    ongoing.append(run)
                elif status == 'hanging':
                    hanging.append(run)
                elif status == 'failed':
                    failed.append(run)
                elif status == 'completed':
                    completed.append(run)

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

    def display(self):
        """Display the dashboard"""
        categories = self.categorize_runs()

        # ANSI color codes
        RESET = '\033[0m'
        BOLD = '\033[1m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        RED = '\033[91m'
        CYAN = '\033[96m'
        ORANGE = '\033[38;5;208m'
        GRAY = '\033[90m'

        # Clear screen
        os.system('clear')

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
                for run in runs:
                    time_ago = self.format_time_ago(run.get('start_time', ''))
                    machine = run.get('machine', 'unknown').split('.')[0]  # Shorten hostname
                    tmux = run.get('tmux')
                    command = run.get('command', '')[:75]  # Show more of command

                    # Format the line
                    location = f"{machine}"
                    if tmux:
                        location += f" | tmux:{tmux}"

                    print(f"  {GRAY}[{time_ago:>8}]{RESET} {location:35} | {BOLD}{command}{RESET}")

                    # Show triggers if hanging
                    if category_name == 'HANGING' and run.get('triggers'):
                        for trigger in run.get('triggers'):
                            print(f"    {ORANGE}└─ Trigger: {trigger}{RESET}")

                    # Show exit code if failed
                    if category_name == 'FAILED':
                        exit_code = run.get('exit_code', 'unknown')
                        print(f"    {RED}└─ Exit code: {exit_code}{RESET}")

        print("\n" + CYAN + "=" * 110 + RESET)
        print(GRAY + "Press Ctrl+C to exit" + RESET)
        print(CYAN + "=" * 110 + RESET)

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
                        else:
                            # Main topic - check for event type in message
                            message = msg.get('message', '')
                            headers = msg.get('headers', {})

                            # Check if it's a completion event
                            if message:
                                try:
                                    data = json.loads(message)
                                    if data.get('event') == 'complete':
                                        dashboard.handle_complete(data)
                                except json.JSONDecodeError:
                                    pass

                            # Check if it's a trigger event
                            if headers.get('X-Event-Type') == 'trigger':
                                dashboard.handle_trigger(headers, message)

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

    start_thread.start()
    main_thread.start()

    print("[dashboard] Connecting to ntfy streams...", file=sys.stderr)
    time.sleep(2)  # Give threads time to connect

    # Display loop
    try:
        while True:
            dashboard.display()
            time.sleep(3)  # Refresh every 3 seconds
    except KeyboardInterrupt:
        print("\n[dashboard] Exiting...", file=sys.stderr)
        sys.exit(0)

if __name__ == '__main__':
    main()
