#!/usr/bin/env python3

import argparse
import json
import os
import random
import re
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Set

DEFAULT_TRIGGERS = [
    "Ray debugger is listening",
    # "ERROR",
    "CUDA out of memory"
]

NTFY_TOPIC = "mshtepel-ml-runs"
NTFY_START_TOPIC = "mshtepel-start-ml-runs"
NTFY_WANDB_TOPIC = "mshtepel-wandburl-ml-runs"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
NTFY_START_URL = f"https://ntfy.sh/{NTFY_START_TOPIC}"
NTFY_WANDB_URL = f"https://ntfy.sh/{NTFY_WANDB_TOPIC}"

def get_machine_name():
    return socket.gethostname()

def get_tmux_session():
    """Get current tmux session name if running in tmux"""
    tmux_var = os.environ.get('TMUX')
    if not tmux_var:
        return None

    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-p', '#S'],
            capture_output=True,
            text=True,
            timeout=2
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except:
        return None

def generate_run_id():
    """Generate unique run ID"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    random_suffix = random.randint(1000, 9999)
    return f"{timestamp}_{random_suffix}"

def send_notification(title, message, tags="warning", url=None, extra_headers=None):
    """Send notification via ntfy"""
    if url is None:
        url = NTFY_URL

    headers = ['-H', f'Title: {title}', '-H', f'Tags: {tags}']
    if extra_headers:
        for key, value in extra_headers.items():
            headers.extend(['-H', f'{key}: {value}'])

    try:
        subprocess.run(
            ['curl', '-s', '-d', message] + headers + [url],
            timeout=10,
            capture_output=True
        )
    except Exception as e:
        print(f"[notify] Failed to send notification: {e}", file=sys.stderr)

def send_json_notification(topic_url, data, title=""):
    """Send JSON notification via ntfy"""
    try:
        headers = ['-H', 'Content-Type: application/json']
        if title:
            headers.extend(['-H', f'Title: {title}'])

        subprocess.run(
            ['curl', '-s'] + headers + ['-d', json.dumps(data), topic_url],
            timeout=10,
            capture_output=True
        )
    except Exception as e:
        print(f"[notify] Failed to send JSON notification: {e}", file=sys.stderr)

def get_context_lines(file_path, current_pos, context_size=5):
    """Get last N lines from file"""
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()

        start = max(0, len(lines) - context_size)
        context = ''.join(lines[start:])
        return context.strip()
    except:
        return ""

def monitor_output_and_process(output_file, proc, triggers, command_str, machine, tmux_session, cwd, run_id):
    """Monitor output file for triggers and process for crashes"""
    seen_triggers: Set[str] = set()
    wandb_url = None
    wandb_pattern = re.compile(r'wandb:.*?(https://wandb\.ai/\S+)')
    file_pos = 0

    # Wait for output file to exist
    timeout = 10  # seconds
    elapsed = 0
    while not output_file.exists() and proc.poll() is None:
        time.sleep(0.1)
        elapsed += 0.1
        if elapsed > timeout:
            print("[notify] Warning: Output file not created after 10s", file=sys.stderr)
            break

    # If file still doesn't exist, process probably ended immediately
    if not output_file.exists():
        returncode = proc.wait()
        return returncode

    with open(output_file, 'r') as f:
        while True:
            # Check if process is still running
            returncode = proc.poll()

            # Read new lines
            line = f.readline()
            if line:
                file_pos = f.tell()

                # Check for triggers
                for trigger in triggers:
                    if trigger in line and trigger not in seen_triggers:
                        seen_triggers.add(trigger)

                        # Get context
                        context = get_context_lines(output_file, file_pos, context_size=5)

                        # Build notification data as JSON (like start/complete events)
                        trigger_data = {
                            "event": "trigger",
                            "run_id": run_id,
                            "trigger": trigger,
                            "context": context,
                            "command": command_str,
                            "machine": machine,
                            "tmux": tmux_session,
                            "cwd": cwd,
                            "timestamp": datetime.now().isoformat()
                        }

                        send_json_notification(NTFY_URL, trigger_data,
                                             title=f"🔔 Trigger: {trigger}")
                        print(f"\n[notify] ⚠️  Detected trigger: {trigger}", file=sys.stderr)

                # Check for wandb URL
                if not wandb_url:
                    match = wandb_pattern.search(line)
                    if match:
                        wandb_url = match.group(1)
                        print(f"\n[notify] DEBUG: Matched wandb URL in line: {line.strip()}", file=sys.stderr)
                        print(f"[notify] DEBUG: Extracted URL: {wandb_url}", file=sys.stderr)
                        wandb_data = {
                            "event": "wandb",
                            "run_id": run_id,
                            "wandb_url": wandb_url,
                            "timestamp": datetime.now().isoformat()
                        }
                        print(f"[notify] DEBUG: Sending wandb notification to {NTFY_WANDB_URL}", file=sys.stderr)
                        print(f"[notify] DEBUG: Data: {wandb_data}", file=sys.stderr)
                        send_json_notification(NTFY_WANDB_URL, wandb_data,
                                             title=f"🚀 W&B Run: {run_id[:20]}")
                        print(f"\n[notify] 🚀 Detected W&B URL: {wandb_url}", file=sys.stderr)
            else:
                # No new data
                if returncode is not None:
                    # Process ended
                    if returncode != 0:
                        # Get last lines of output
                        context = get_context_lines(output_file, file_pos, context_size=10)

                        location = f"Machine: {machine}"
                        if tmux_session:
                            location += f"\nTmux: {tmux_session}"
                        location += f"\nDir: {cwd}"

                        title = f"💥 Script crashed (exit {returncode})"
                        message = f"{location}\nCommand: {command_str}\n\nLast output:\n{context}"

                        send_notification(title, message, "skull,warning",
                                        extra_headers={"X-Run-ID": run_id, "X-Event-Type": "failed"})
                        print(f"\n[notify] 💥 Script crashed with exit code {returncode}", file=sys.stderr)

                    return returncode

                # Still running, wait a bit
                time.sleep(0.1)

def main():
    parser = argparse.ArgumentParser(
        description='Run a command with monitoring and notifications',
        usage='%(prog)s [--triggers TRIGGER ...] command [args ...]'
    )
    parser.add_argument('--triggers', nargs='+', help='Additional trigger strings to monitor')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='Command to run')

    args = parser.parse_args()

    if not args.command:
        print("Error: No command specified", file=sys.stderr)
        print("Usage: notify [--triggers TRIGGER ...] command [args ...]", file=sys.stderr)
        sys.exit(1)

    # Setup
    run_id = generate_run_id()
    machine = get_machine_name()
    tmux_session = get_tmux_session()
    cwd = os.getcwd()
    triggers = DEFAULT_TRIGGERS + (args.triggers or [])

    # Create output file
    output_dir = Path.home() / '.notify_logs'
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = output_dir / f"notify_{timestamp}_{os.getpid()}.log"

    command_str = ' '.join(shlex.quote(arg) for arg in args.command)
    command_display = ' '.join(args.command)
    print(f"[notify] 🚀 Starting: {command_display}", file=sys.stderr)
    print(f"[notify] 🆔 Run ID: {run_id}", file=sys.stderr)
    print(f"[notify] 📝 Log: {output_file}", file=sys.stderr)
    print(f"[notify] 📁 Working dir: {cwd}", file=sys.stderr)
    print(f"[notify] 👀 Monitoring for: {', '.join(triggers)}", file=sys.stderr)
    if tmux_session:
        print(f"[notify] 🖥️  Tmux session: {tmux_session}", file=sys.stderr)
    print(f"[notify] 🌐 Machine: {machine}", file=sys.stderr)
    print("", file=sys.stderr)

    # Send start notification
    start_data = {
        "event": "start",
        "run_id": run_id,
        "command": command_display,
        "machine": machine,
        "tmux": tmux_session,
        "cwd": cwd,
        "timestamp": datetime.now().isoformat()
    }
    send_json_notification(NTFY_START_URL, start_data, title=f"🚀 Started: {command_display[:50]}")

    # Start command with output redirected via tee
    # This runs independently - we just monitor its output file and PID
    # Use bash -c with proper escaping to handle complex commands
    # Build the command that will be tee'd
    # We need to handle the command as-is and pipe through tee
    bash_script = f"set -o pipefail; {command_str} 2>&1 | tee {shlex.quote(str(output_file))}"

    proc = subprocess.Popen(
        ['bash', '-c', bash_script],
        preexec_fn=os.setsid  # Create new process group
    )

    # Monitor output and process
    try:
        returncode = monitor_output_and_process(
            output_file, proc, triggers, command_str, machine, tmux_session, cwd, run_id
        )
    except KeyboardInterrupt:
        print("\n[notify] ⚠️  Interrupted by user", file=sys.stderr)
        # Try to terminate the process group
        try:
            os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
            time.sleep(1)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), 9)  # SIGKILL
        except:
            pass
        returncode = 130

    print(f"\n[notify] ✅ Finished with exit code {returncode}", file=sys.stderr)
    print(f"[notify] 📄 Full log: {output_file}", file=sys.stderr)

    # Send completion notification
    completion_data = {
        "event": "complete",
        "run_id": run_id,
        "exit_code": returncode,
        "timestamp": datetime.now().isoformat()
    }
    send_json_notification(NTFY_URL, completion_data,
                          title=f"✅ Completed (exit {returncode}): {command_display[:40]}")

    sys.exit(returncode)

if __name__ == '__main__':
    main()
