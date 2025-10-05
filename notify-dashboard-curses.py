#!/usr/bin/env python3

import curses
import json
import os
import sys
import time
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
        self.state_lock = RLock()
        self.runs: Dict[str, dict] = {}
        self.status_message = ""
        self.load_state()

    def load_state(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.runs = data.get('runs', {})
            except Exception as e:
                pass

    def save_state(self):
        try:
            with self.state_lock:
                data = {'runs': self.runs}
                with open(STATE_FILE, 'w') as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            pass

    def handle_start(self, data):
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
        run_id = data.get('run_id')
        if not run_id:
            return

        trigger = data.get('trigger', '')

        with self.state_lock:
            if run_id in self.runs:
                if trigger not in self.runs[run_id]['triggers']:
                    self.runs[run_id]['triggers'].append(trigger)
                if self.runs[run_id]['status'] != 'hanging':
                    self.runs[run_id]['status_change_time'] = datetime.now().isoformat()
                self.runs[run_id]['status'] = 'hanging'
        self.save_state()

    def handle_wandb(self, data):
        run_id = data.get('run_id')
        if not run_id:
            return

        wandb_url = data.get('wandb_url')
        with self.state_lock:
            if run_id in self.runs:
                self.runs[run_id]['wandb_url'] = wandb_url
        self.save_state()

    def handle_complete(self, data):
        run_id = data.get('run_id')
        if not run_id:
            return

        exit_code = data.get('exit_code', 0)

        with self.state_lock:
            if run_id in self.runs:
                self.runs[run_id]['exit_code'] = exit_code
                self.runs[run_id]['end_time'] = data.get('timestamp', datetime.now().isoformat())
                self.runs[run_id]['status_change_time'] = data.get('timestamp', datetime.now().isoformat())

                if self.runs[run_id]['status'] == 'hanging':
                    if exit_code == 0:
                        self.runs[run_id]['status'] = 'completed'
                    else:
                        self.runs[run_id]['status'] = 'failed'
                else:
                    if exit_code == 0:
                        self.runs[run_id]['status'] = 'completed'
                    else:
                        self.runs[run_id]['status'] = 'failed'
        self.save_state()

    def flush_category(self, status):
        with self.state_lock:
            to_remove = [run_id for run_id, run in self.runs.items() if run.get('status') == status]
            for run_id in to_remove:
                del self.runs[run_id]
        self.save_state()
        return len(to_remove)

    def flush_all_finished(self):
        with self.state_lock:
            to_remove = [run_id for run_id, run in self.runs.items()
                        if run.get('status') in ['completed', 'failed']]
            for run_id in to_remove:
                del self.runs[run_id]
        self.save_state()
        return len(to_remove)

    def delete_run_by_index(self, category, index):
        try:
            with self.state_lock:
                runs_in_category = [run_id for run_id, run in self.runs.items()
                                   if run.get('status') == category.lower()]
                runs_in_category.sort(key=lambda rid: self.runs[rid].get('start_time', ''), reverse=True)

                if 0 <= index - 1 < len(runs_in_category):
                    run_id = runs_in_category[index - 1]
                    del self.runs[run_id]
                    self.save_state()
                    return True
            return False
        except Exception as e:
            return False

    def categorize_runs(self):
        with self.state_lock:
            ongoing = []
            hanging = []
            failed = []
            completed = []

            for run_id, run in self.runs.items():
                status = run.get('status', 'ongoing')
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

            for lst in [ongoing, hanging, failed, completed]:
                lst.sort(key=lambda x: x.get('start_time', ''), reverse=True)

            return {
                'ONGOING': ongoing[:6],
                'HANGING': hanging[:6],
                'FAILED': failed[:6],
                'COMPLETED': completed[:6]
            }

    def format_time_ago(self, iso_time):
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


def display_dashboard(stdscr, dashboard, selected_number):
    """Display the dashboard using curses"""
    stdscr.clear()

    # Setup colors
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)

    CYAN = curses.color_pair(1)
    GREEN = curses.color_pair(2)
    RED = curses.color_pair(3)
    YELLOW = curses.color_pair(4)
    GRAY = curses.color_pair(5) | curses.A_DIM
    BOLD = curses.A_BOLD

    categories = dashboard.categorize_runs()

    row = 0
    max_y, max_x = stdscr.getmaxyx()

    # Header
    stdscr.addstr(row, 0, "=" * min(110, max_x - 1), CYAN | BOLD)
    row += 1
    stdscr.addstr(row, 0, "NOTIFY DASHBOARD".center(min(110, max_x - 1)), CYAN | BOLD)
    row += 1
    stdscr.addstr(row, 0, f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".center(min(110, max_x - 1)), GRAY)
    row += 1
    stdscr.addstr(row, 0, "=" * min(110, max_x - 1), CYAN | BOLD)
    row += 2

    # Categories
    category_colors = {
        'ONGOING': CYAN,
        'HANGING': YELLOW,
        'FAILED': RED,
        'COMPLETED': GREEN
    }

    for category_name, runs in categories.items():
        if row >= max_y - 5:
            break

        with dashboard.state_lock:
            total_count = sum(1 for r in dashboard.runs.values() if r.get('status') == category_name.lower())

        color = category_colors.get(category_name, CYAN)
        stdscr.addstr(row, 0, f"{category_name} ({total_count}):", color | BOLD)
        row += 1
        stdscr.addstr(row, 0, "-" * min(110, max_x - 1), GRAY)
        row += 1

        if not runs:
            stdscr.addstr(row, 2, "(none)", GRAY)
            row += 1
        else:
            for idx, run in enumerate(runs, 1):
                if row >= max_y - 5:
                    break

                start_time_ago = dashboard.format_time_ago(run.get('start_time', ''))
                command = run.get('command', '')
                command_parts = command.split()
                if command_parts:
                    cmd_name = command_parts[0].split('/')[-1]
                else:
                    cmd_name = command[:50]

                # Format time display
                if category_name == 'ONGOING':
                    time_display = f"{start_time_ago:>8}"
                else:
                    status_change = run.get('status_change_time') or run.get('end_time') or run.get('start_time')
                    status_time_ago = dashboard.format_time_ago(status_change) if status_change else "unknown"
                    time_display = f"{start_time_ago:>8}→{status_time_ago:>8}"

                # Main line
                line = f"[{idx}] [{time_display}] {cmd_name}"
                stdscr.addstr(row, 2, f"[{idx}]", CYAN)
                stdscr.addstr(row, 6, f"[{time_display}]", GRAY)
                stdscr.addstr(row, 6 + len(f"[{time_display}]") + 1, cmd_name, BOLD)
                row += 1

                # Metadata
                if run.get('wandb_url') and row < max_y - 5:
                    stdscr.addstr(row, 6, f"└─ W&B: {run['wandb_url']}", CYAN)
                    row += 1

                if run.get('tmux') and row < max_y - 5:
                    stdscr.addstr(row, 6, f"└─ Tmux: {run['tmux']}", GRAY)
                    row += 1

                if run.get('machine') and row < max_y - 5:
                    machine = run['machine'].split('.')[0]
                    stdscr.addstr(row, 6, f"└─ Machine: {machine}", GRAY)
                    row += 1

                if run.get('cwd') and row < max_y - 5:
                    cwd = run['cwd']
                    display_cwd = cwd if len(cwd) < 80 else '...' + cwd[-77:]
                    stdscr.addstr(row, 6, f"└─ Dir: {display_cwd}", GRAY)
                    row += 1

                if category_name == 'HANGING' and run.get('triggers') and row < max_y - 5:
                    for trigger in run['triggers']:
                        if row < max_y - 5:
                            stdscr.addstr(row, 6, f"└─ Trigger: {trigger}", YELLOW)
                            row += 1

                if category_name == 'FAILED' and row < max_y - 5:
                    exit_code = run.get('exit_code', 'unknown')
                    stdscr.addstr(row, 6, f"└─ Exit code: {exit_code}", RED)
                    row += 1

        row += 1

    # Footer
    if row < max_y - 3:
        stdscr.addstr(max_y - 3, 0, "=" * min(110, max_x - 1), CYAN)

        if dashboard.status_message:
            stdscr.addstr(max_y - 2, 0, dashboard.status_message, YELLOW | BOLD)
        elif selected_number:
            stdscr.addstr(max_y - 2, 0, f"Selected [{selected_number}]. Press: [o]=ONGOING [h]=HANGING [f]=FAILED [c]=COMPLETED", YELLOW)
        else:
            footer = "Delete: [1-6] then [o/h/f/c]  |  Flush: [Shift+F/C/H/A]  |  Exit: Ctrl+C"
            stdscr.addstr(max_y - 2, 0, footer, GRAY)

        stdscr.addstr(max_y - 1, 0, "=" * min(110, max_x - 1), CYAN)

    stdscr.refresh()


def listen_to_stream(url, dashboard, event_type):
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

                        if msg.get('event') == 'keepalive':
                            continue

                        if event_type == 'start':
                            message = msg.get('message', '')
                            if message:
                                try:
                                    data = json.loads(message)
                                    if data.get('event') == 'start':
                                        dashboard.handle_start(data)
                                except json.JSONDecodeError:
                                    pass
                        elif event_type == 'wandb':
                            message = msg.get('message', '')
                            if message:
                                try:
                                    data = json.loads(message)
                                    if data.get('event') == 'wandb':
                                        dashboard.handle_wandb(data)
                                except json.JSONDecodeError:
                                    pass
                        else:
                            message = msg.get('message', '')
                            if message:
                                try:
                                    data = json.loads(message)
                                    event_type_data = data.get('event')

                                    if event_type_data == 'complete':
                                        dashboard.handle_complete(data)
                                    elif event_type_data == 'trigger':
                                        dashboard.handle_trigger(data, message)
                                except json.JSONDecodeError:
                                    pass

                    except Exception as e:
                        pass

        except Exception as e:
            time.sleep(5)


def main_curses(stdscr):
    # Setup
    curses.curs_set(0)  # Hide cursor
    stdscr.nodelay(True)  # Non-blocking input
    stdscr.timeout(100)  # 100ms timeout for getch()

    dashboard = Dashboard()

    # Start listener threads
    start_thread = Thread(target=listen_to_stream, args=(NTFY_START_URL, dashboard, 'start'), daemon=True)
    main_thread = Thread(target=listen_to_stream, args=(NTFY_URL, dashboard, 'main'), daemon=True)
    wandb_thread = Thread(target=listen_to_stream, args=(NTFY_WANDB_URL, dashboard, 'wandb'), daemon=True)

    start_thread.start()
    main_thread.start()
    wandb_thread.start()

    selected_number = None
    last_display = 0

    while True:
        current_time = time.time()

        # Update display every 3 seconds or when there's input
        if current_time - last_display >= 3:
            display_dashboard(stdscr, dashboard, selected_number)
            last_display = current_time

        # Handle input
        try:
            key = stdscr.getch()

            if key == -1:  # No input
                continue

            # Convert to character
            if key < 256:
                ch = chr(key)

                # Check if it's a number (1-6)
                if ch in '123456':
                    selected_number = int(ch)
                    dashboard.status_message = ""
                    display_dashboard(stdscr, dashboard, selected_number)
                    last_display = current_time
                    continue

                # Check for delete by category (lowercase)
                if selected_number and ch in 'ohfc':
                    category_map = {'o': 'ongoing', 'h': 'hanging', 'f': 'failed', 'c': 'completed'}
                    category = category_map[ch]
                    if dashboard.delete_run_by_index(category, selected_number):
                        dashboard.status_message = f"✓ Deleted item [{selected_number}] from {category.upper()}"
                    else:
                        dashboard.status_message = f"✗ Item [{selected_number}] not found in {category.upper()}"
                    selected_number = None
                    display_dashboard(stdscr, dashboard, selected_number)
                    last_display = current_time
                    time.sleep(1)
                    dashboard.status_message = ""
                    continue

                # Flush commands (uppercase)
                if ch == 'F':
                    count = dashboard.flush_category('failed')
                    dashboard.status_message = f"✓ Flushed {count} FAILED run(s)"
                    selected_number = None
                    display_dashboard(stdscr, dashboard, selected_number)
                    last_display = current_time
                    time.sleep(1)
                    dashboard.status_message = ""
                elif ch == 'C':
                    count = dashboard.flush_category('completed')
                    dashboard.status_message = f"✓ Flushed {count} COMPLETED run(s)"
                    selected_number = None
                    display_dashboard(stdscr, dashboard, selected_number)
                    last_display = current_time
                    time.sleep(1)
                    dashboard.status_message = ""
                elif ch == 'H':
                    count = dashboard.flush_category('hanging')
                    dashboard.status_message = f"✓ Flushed {count} HANGING run(s)"
                    selected_number = None
                    display_dashboard(stdscr, dashboard, selected_number)
                    last_display = current_time
                    time.sleep(1)
                    dashboard.status_message = ""
                elif ch == 'A':
                    count = dashboard.flush_all_finished()
                    dashboard.status_message = f"✓ Flushed {count} finished run(s)"
                    selected_number = None
                    display_dashboard(stdscr, dashboard, selected_number)
                    last_display = current_time
                    time.sleep(1)
                    dashboard.status_message = ""

        except KeyboardInterrupt:
            break
        except Exception as e:
            pass


if __name__ == '__main__':
    try:
        curses.wrapper(main_curses)
    except KeyboardInterrupt:
        pass
