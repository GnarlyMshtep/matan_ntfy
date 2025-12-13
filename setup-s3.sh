#!/bin/bash
# setup-s3.sh - Set up S3 storage access on a new machine
# Assumes: conda is available
#
# Usage: ./setup-s3.sh
#
# This script will:
# 1. Install AWS CLI (if missing)
# 2. Download goofys binary
# 3. Configure AWS credentials (if missing)
# 4. Install DVC with S3 support
# 5. Set up S3 mount at ~/s3
# 6. Copy helper scripts to ~/bin
# 7. Add ~/bin to PATH

set -e  # Exit on error

BUCKET="matan-ml-exp-bucket"
MOUNT_POINT="$HOME/s3"
BIN_DIR="$HOME/bin"
REGION="us-east-1"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo "========================================"
echo "     S3 Storage Setup Script"
echo "========================================"
echo ""

# 1. Check for conda
info "Checking conda..."
if ! command -v conda &> /dev/null; then
    error "conda not found. Please install conda first."
fi
success "conda found"

# 2. Install AWS CLI if missing
info "Checking AWS CLI..."
if ! command -v aws &> /dev/null; then
    info "Installing AWS CLI..."
    conda install -c conda-forge awscli -y
fi
success "AWS CLI installed: $(aws --version 2>&1 | head -1)"

# 3. Download goofys if missing
info "Checking goofys..."
mkdir -p "$BIN_DIR"
if [ ! -x "$BIN_DIR/goofys" ]; then
    info "Downloading goofys..."
    curl -L -o "$BIN_DIR/goofys" https://github.com/kahing/goofys/releases/latest/download/goofys
    chmod +x "$BIN_DIR/goofys"
fi
success "goofys installed: $("$BIN_DIR/goofys" --version 2>&1)"

# 4. Configure AWS credentials if missing
info "Checking AWS credentials..."
if [ ! -f ~/.aws/credentials ]; then
    warn "AWS credentials not found."
    echo ""
    echo "Please enter your AWS credentials for the dvc-user account:"
    echo "(You can find these in your AWS IAM console)"
    echo ""
    aws configure
fi

# Test AWS access
info "Testing AWS access..."
if aws s3 ls "s3://$BUCKET/" &> /dev/null; then
    success "AWS credentials working"
else
    error "Cannot access S3 bucket. Check your credentials."
fi

# 5. Install DVC with S3 support
info "Checking DVC..."
if ! command -v dvc &> /dev/null; then
    info "Installing DVC..."
    conda install -c conda-forge dvc dvc-s3 -y
fi
success "DVC installed: $(dvc --version 2>&1)"

# 6. Configure DVC remote globally
info "Configuring DVC remote..."
dvc config --global remote.s3.url "s3://$BUCKET/dvc-storage"
dvc config --global remote.s3.region "$REGION"
success "DVC remote configured"

# 7. Set up mount point
info "Setting up S3 mount point..."
mkdir -p "$MOUNT_POINT"

# Unmount if already mounted
if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    warn "Mount point already mounted, unmounting..."
    fusermount -u "$MOUNT_POINT" || true
fi

# Mount the bucket
info "Mounting S3 bucket..."
"$BIN_DIR/goofys" "$BUCKET" "$MOUNT_POINT"

if mountpoint -q "$MOUNT_POINT"; then
    success "S3 bucket mounted at $MOUNT_POINT"
else
    error "Failed to mount S3 bucket"
fi

# 8. Add auto-mount to .zshrc
info "Configuring shell..."
ZSHRC="$HOME/.zshrc"

# Add ~/bin and ~/bin/s3 to PATH if not present
if ! grep -q 'export PATH="$HOME/bin' "$ZSHRC" 2>/dev/null; then
    echo '' >> "$ZSHRC"
    echo '# Add ~/bin and ~/bin/s3 to PATH' >> "$ZSHRC"
    echo 'export PATH="$HOME/bin/s3:$HOME/bin:$PATH"' >> "$ZSHRC"
    success "Added ~/bin and ~/bin/s3 to PATH"
fi

# Add auto-mount if not present
if ! grep -q "goofys $BUCKET" "$ZSHRC" 2>/dev/null; then
    echo '' >> "$ZSHRC"
    echo '# Auto-mount S3 bucket' >> "$ZSHRC"
    echo "[ -d $MOUNT_POINT ] && ! mountpoint -q $MOUNT_POINT 2>/dev/null && $BIN_DIR/goofys $BUCKET $MOUNT_POINT" >> "$ZSHRC"
    success "Added auto-mount to .zshrc"
fi

# 9. Final test
echo ""
echo "========================================"
echo "            Setup Complete!"
echo "========================================"
echo ""
echo "Your S3 bucket is mounted at: $MOUNT_POINT"
echo ""
echo "Available commands:"
echo "  s3ls              - List bucket contents"
echo "  s3push <file>     - Push file to S3"
echo "  s3pull <path>     - Pull file from S3"
echo ""
echo "Or just use the mount directly:"
echo "  cd ~/s3/data"
echo "  ls"
echo "  cp ./myfile.txt ."
echo ""
echo "DVC is configured globally. To pull data in a cloned repo:"
echo "  git clone <repo-url> && cd <repo> && dvc pull"
echo ""
echo "For more info: cat ~/bin/s3/README.md"
echo ""
echo "Restart your shell or run: source ~/.zshrc"
