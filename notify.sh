#!/bin/bash

# Simple wrapper for notify.py
exec "$(dirname "$0")/notify.py" "$@"
