#!/usr/bin/env python3
"""
Backup system for Fighter Jet Game leaderboard.
- Local backups every minute (keeps last 60)
- Backblaze B2 offload every 6 hours
"""

import os
import sys
import json
import shutil
import hashlib
import tarfile
from datetime import datetime
from pathlib import Path

# Configuration
DATA_DIR = Path(os.environ.get('DATA_DIR', '/app/data'))
BACKUP_DIR = DATA_DIR / 'backups'
LEADERBOARD_FILE = DATA_DIR / 'leaderboard.json'

# Keep last 60 local backups (1 hour of minute-by-minute backups)
MAX_LOCAL_BACKUPS = 60

# Backblaze B2 settings (set via environment variables)
B2_BUCKET = os.environ.get('B2_BUCKET', '')
B2_KEY_ID = os.environ.get('B2_KEY_ID', '')
B2_APP_KEY = os.environ.get('B2_APP_KEY', '')


def get_file_hash(filepath):
    """Get MD5 hash of file content."""
    if not filepath.exists():
        return None
    with open(filepath, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def local_backup():
    """Create a local backup if data has changed."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if not LEADERBOARD_FILE.exists():
        print("[Backup] No leaderboard file to backup")
        return False

    # Check if content changed since last backup
    current_hash = get_file_hash(LEADERBOARD_FILE)
    hash_file = BACKUP_DIR / '.last_hash'

    if hash_file.exists():
        last_hash = hash_file.read_text().strip()
        if current_hash == last_hash:
            print("[Backup] No changes detected, skipping")
            return False

    # Create timestamped backup
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = BACKUP_DIR / f'leaderboard_{timestamp}.json'

    shutil.copy2(LEADERBOARD_FILE, backup_file)
    hash_file.write_text(current_hash)

    print(f"[Backup] Created: {backup_file.name}")

    # Rotate old backups
    rotate_local_backups()

    return True


def rotate_local_backups():
    """Keep only the last MAX_LOCAL_BACKUPS backups."""
    backups = sorted(BACKUP_DIR.glob('leaderboard_*.json'), reverse=True)

    for old_backup in backups[MAX_LOCAL_BACKUPS:]:
        old_backup.unlink()
        print(f"[Backup] Rotated out: {old_backup.name}")


def offload_to_backblaze():
    """Upload backups to Backblaze B2."""
    if not all([B2_BUCKET, B2_KEY_ID, B2_APP_KEY]):
        print("[B2] Backblaze credentials not configured, skipping offload")
        return False

    backups = list(BACKUP_DIR.glob('leaderboard_*.json'))
    if not backups:
        print("[B2] No backups to offload")
        return False

    # Create compressed archive
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f'fighter-jet-backups_{timestamp}.tar.gz'
    archive_path = DATA_DIR / archive_name

    try:
        # Create tar.gz of all backups
        with tarfile.open(archive_path, 'w:gz') as tar:
            for backup in backups:
                tar.add(backup, arcname=backup.name)

        archive_size = archive_path.stat().st_size
        print(f"[B2] Created archive: {archive_name} ({archive_size} bytes)")

        # Upload using b2sdk
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        info = InMemoryAccountInfo()
        b2_api = B2Api(info)
        b2_api.authorize_account('production', B2_KEY_ID, B2_APP_KEY)

        bucket = b2_api.get_bucket_by_name(B2_BUCKET)

        # Upload the archive
        bucket.upload_local_file(
            local_file=str(archive_path),
            file_name=f'fighter-jet-game/backups/{archive_name}'
        )

        print(f"[B2] Uploaded: {archive_name}")

        # Clean up local archive
        archive_path.unlink()

        # Keep only last 5 backups locally after offload
        backups = sorted(BACKUP_DIR.glob('leaderboard_*.json'), reverse=True)
        for old_backup in backups[5:]:
            old_backup.unlink()
            print(f"[B2] Cleaned up local: {old_backup.name}")

        return True

    except ImportError:
        print("[B2] b2sdk not installed")
        if archive_path.exists():
            archive_path.unlink()
        return False
    except Exception as e:
        print(f"[B2] Upload failed: {e}")
        if archive_path.exists():
            archive_path.unlink()
        return False


def restore_latest():
    """Restore from the most recent backup."""
    backups = sorted(BACKUP_DIR.glob('leaderboard_*.json'), reverse=True)

    if not backups:
        print("[Restore] No backups available")
        return False

    latest = backups[0]
    shutil.copy2(latest, LEADERBOARD_FILE)
    print(f"[Restore] Restored from: {latest.name}")
    return True


def list_backups():
    """List all available backups."""
    backups = sorted(BACKUP_DIR.glob('leaderboard_*.json'), reverse=True)

    if not backups:
        print("No backups available")
        return []

    print(f"Available backups ({len(backups)}):")
    result = []
    for backup in backups:
        size = backup.stat().st_size
        mtime = datetime.fromtimestamp(backup.stat().st_mtime)
        info = {'name': backup.name, 'size': size, 'mtime': mtime.isoformat()}
        result.append(info)
        print(f"  {backup.name} - {size} bytes - {mtime}")

    return result


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: backup.py [backup|offload|restore|list]")
        sys.exit(1)

    command = sys.argv[1]

    if command == 'backup':
        local_backup()
    elif command == 'offload':
        offload_to_backblaze()
    elif command == 'restore':
        restore_latest()
    elif command == 'list':
        list_backups()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
