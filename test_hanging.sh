#!/bin/bash
# Test script that triggers and then hangs

echo "Starting test script..."
sleep 2
echo "CUDA out of memory - this should trigger a notification"
sleep 1
echo "Now hanging forever..."
sleep infinity
