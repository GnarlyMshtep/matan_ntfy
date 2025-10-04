#!/bin/bash

# Simple wrapper for notify-dashboard.py
exec "$(dirname "$0")/notify-dashboard.py" "$@"
