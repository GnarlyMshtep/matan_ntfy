# S3 & DVC Storage Tools

## Quick Reference

### S3 Helper Scripts

```bash
# List bucket contents
s3ls                           # List data/
s3ls experiments -l            # Long format with sizes
s3ls --tree                    # Tree view

# Push files (path-based storage)
s3push ./myfile.txt --to experiments/run1
s3push ./outputs/              # Auto-detects repo if in git

# Pull files
s3pull experiments/run1/myfile.txt ./
```

### S3 Mount (goofys)

```bash
# Browse directly like a local filesystem
cd ~/s3/data
ls
cp ./localfile.txt experiments/
```

Mount should auto-start on shell login. If not:
```bash
~/bin/goofys matan-ml-exp-bucket ~/s3
```

---

## DVC Setup (New Machine / New Repo)

### 1. Install DVC (one-time per machine)
```bash
conda install -c conda-forge dvc dvc-s3 -y
```

### 2. Configure remote (one-time per machine)
```bash
dvc remote add -d s3remote s3://matan-ml-exp-bucket/dvc-storage
dvc remote modify s3remote region us-east-1
```

Or configure globally (applies to all repos):
```bash
dvc config --global remote.s3remote.url s3://matan-ml-exp-bucket/dvc-storage
dvc config --global remote.s3remote.region us-east-1
```

### 3. Clone a repo with DVC data
```bash
git clone <repo-url>
cd <repo>
dvc pull                       # Downloads all tracked data from S3
```

### 4. Track new data with DVC
```bash
dvc add data/                  # Creates data.dvc, adds data/ to .gitignore
git add data.dvc .gitignore
git commit -m "Track data with DVC"
dvc push                       # Uploads to S3
git push
```

### 5. Update existing tracked data
```bash
# After modifying files in data/
dvc add data/                  # Updates the hash
git add data.dvc
git commit -m "Update data"
dvc push
git push
```

---

## Storage Structure

```
s3://matan-ml-exp-bucket/
├── dvc-storage/              # DVC content-addressed (automatic)
│   └── ab/cdef1234...        # Files stored by hash
└── data/                     # Path-based (manual via s3push)
    └── {any/path/you/want}/
```

**DVC** = Version-controlled, tied to git commits, content-addressed
**s3push** = Simple uploads, human-readable paths, not version-controlled

---

## Troubleshooting

### "Everything is up to date" but nothing uploaded
Check `.dvcignore` - make sure it's not ignoring your `.dvc` files:
```bash
cat .dvcignore
# Should include: !*.dvc
```

### Mount not working
```bash
# Check if mounted
mountpoint ~/s3

# Remount
fusermount -u ~/s3 2>/dev/null
~/bin/goofys matan-ml-exp-bucket ~/s3
```

### AWS credentials
```bash
# Check credentials exist
cat ~/.aws/credentials

# Reconfigure
aws configure
```

---

## New Machine Setup

Run the setup script:
```bash
~/bin/setup-s3.sh
```

Or manually:
1. Install AWS CLI + DVC: `conda install -c conda-forge awscli dvc dvc-s3`
2. Configure AWS: `aws configure`
3. Download goofys: `curl -L -o ~/bin/goofys https://github.com/kahing/goofys/releases/latest/download/goofys && chmod +x ~/bin/goofys`
4. Mount bucket: `mkdir -p ~/s3 && ~/bin/goofys matan-ml-exp-bucket ~/s3`
5. Add to PATH: `export PATH="$HOME/bin/s3:$HOME/bin:$PATH"`
