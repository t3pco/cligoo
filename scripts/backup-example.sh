#!/bin/bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Example backup script for cligoo
# Mount your data to /data, and run this script via:
#   docker exec cligoo /scripts/backup-example.sh
# Or call it from a cron job / periodic loop.
# ─────────────────────────────────────────────────────────────────────────────

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_ARCHIVE="/tmp/backup_${TIMESTAMP}.tar.gz"
REMOTE_BACKUP_DIR="/Web/Backups"

echo "[$TIMESTAMP] Starting backup process..."

# 1. Compress /data folder
echo "[$TIMESTAMP] Creating compressed archive of /data..."
tar -czf "$BACKUP_ARCHIVE" -C /data .

# 2. Ensure remote backup folder exists
echo "[$TIMESTAMP] Ensuring remote destination $REMOTE_BACKUP_DIR exists..."
cligoo mkdir "$REMOTE_BACKUP_DIR" || true

# 3. Upload backup archive to Degoo
echo "[$TIMESTAMP] Uploading $BACKUP_ARCHIVE to Degoo ($REMOTE_BACKUP_DIR)..."
cligoo upload "$BACKUP_ARCHIVE" -t "$REMOTE_BACKUP_DIR"

# 4. Clean up local temporary file
rm -f "$BACKUP_ARCHIVE"

echo "[$TIMESTAMP] Backup completed successfully!"

