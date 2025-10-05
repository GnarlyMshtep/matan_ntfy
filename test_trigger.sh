#!/bin/bash
# Test script that triggers a notification and exits

echo "Starting test script..."
sleep 5
echo "CUDA out of memory - this should trigger a notification"
sleep 5
echo "Script continuing..."
sleep 5
echo "Done!"
exit 0
