#!/bin/bash
# Test script with both wandb URL and trigger

echo "Starting ML training..."
sleep 2
echo "wandb: 🚀 View run at https://wandb.ai/test-user/test-project/runs/abc123"
sleep 3
echo "Training epoch 1..."
sleep 3
echo "CUDA out of memory - but continuing with smaller batch"
sleep 3
echo "Training epoch 2..."
sleep 3
echo "Training complete!"
exit 0
