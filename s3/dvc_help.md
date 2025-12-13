# DVC (Data Version Control) Quick Guide

## What is DVC?

DVC tracks large files (datasets, models, logs) alongside your git repo without bloating git history. It stores:
- **Small `.dvc` files** in git (pointers/hashes)
- **Actual data** in remote storage (S3)

## Core Workflow

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  dvc add    │  →   │  git commit │  →   │  dvc push   │
│  (hash data)│      │  (.dvc file)│      │  (upload)   │
└─────────────┘      └─────────────┘      └─────────────┘
```

---

## Common Commands

### Track new data
```bash
dvc add data/                  # Creates data.dvc, adds data/ to .gitignore
git add data.dvc .gitignore    # Stage the tracking file
git commit -m "Track data"     # Commit to git
dvc push                       # Upload actual data to S3
```

### Pull data (clone repo on new machine)
```bash
git clone <repo-url>
cd <repo>
dvc pull                       # Downloads all tracked data from S3
```

### Update existing data
```bash
# After modifying files in data/
dvc add data/                  # Re-hash (updates data.dvc)
git add data.dvc
git commit -m "Update data"
dvc push                       # Upload changes
```

### Check status
```bash
dvc status                     # Local changes
dvc status --cloud             # What needs to be pushed/pulled
```

### Go back to old data version
```bash
git checkout <old-commit>      # Checkout old code
dvc checkout                   # Restore data to match that commit
```

---

## Setup (One-Time)

### Per-machine setup
```bash
# Install
conda install -c conda-forge dvc dvc-s3 -y

# Configure remote globally (all repos use this)
dvc remote add -d s3remote s3://matan-ml-exp-bucket/dvc-storage --global
dvc remote modify s3remote region us-east-1 --global
```

### Per-repo setup (if not using global config)
```bash
cd <repo>
dvc init                       # Creates .dvc/ directory
dvc remote add -d s3remote s3://matan-ml-exp-bucket/dvc-storage
```

---

## .dvcignore File

Like `.gitignore` but for DVC. Controls what DVC scans/tracks.

```bash
# .dvcignore example
*.tmp
__pycache__/
```

**Important:** If you use `*` (ignore everything), remember to whitelist `.dvc` files:
```bash
# Ignore everything except what we want
*
!data/
!data/**
!*.dvc          # Don't ignore tracking files!
```

---

## Troubleshooting

### "Everything is up to date" but nothing uploaded
```bash
# Check if .dvcignore is blocking your .dvc files
cat .dvcignore

# Check cloud status
dvc status --cloud
```

### "No data or pipelines tracked"
```bash
# Make sure .dvc files aren't being ignored
cat .dvcignore | grep -i dvc

# Re-add the data
dvc add <path>
```

### Large upload interrupted
```bash
# Just re-run - DVC resumes from where it left off
dvc push
```

### Check what remote is configured
```bash
dvc remote list -v
cat .dvc/config
```

---

## DVC vs s3push

| Feature | DVC | s3push |
|---------|-----|--------|
| Version control | ✓ Tied to git commits | ✗ Just uploads |
| Storage | Content-addressed (by hash) | Path-based (human-readable) |
| Deduplication | ✓ Same file stored once | ✗ Duplicates possible |
| Use case | Reproducible experiments | Quick file sharing |

**Rule of thumb:**
- Use **DVC** for data that should be versioned with code (datasets, model outputs)
- Use **s3push** for ad-hoc uploads you want to browse in S3

---

## Useful Flags

```bash
dvc add --no-commit data/      # Add without auto-staging to git
dvc push -j 4                  # Parallel upload (4 jobs)
dvc pull -j 4                  # Parallel download
dvc gc --cloud                 # Clean up unused files from remote
dvc config core.autostage true # Auto git-add .dvc files
```

---

## File Structure

After `dvc add data/`:
```
repo/
├── .dvc/
│   ├── config                 # Remote configuration
│   └── cache/                 # Local cache of data (by hash)
├── .gitignore                 # Updated to ignore data/
├── data/                      # Your actual data (not in git)
└── data.dvc                   # Tracking file (in git)
```

The `data.dvc` file looks like:
```yaml
outs:
- md5: abc123def456...         # Hash of the data
  size: 1234567890
  nfiles: 100
  path: data
```
