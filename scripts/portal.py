#!/usr/bin/env python3
"""
Portal backup/restore CLI

Usage:
  python scripts/portal.py backup [--output DIR]
  python scripts/portal.py restore <archive.tar.gz> [--yes]

Backup creates a timestamped .tar.gz containing:
  db.dump          PostgreSQL custom-format dump (pg_dump -Fc)
  uploads.tar.gz   Contents of the /data/uploads Docker volume
  credentials.json Google OAuth credentials (if present)
  .env             Environment configuration
  manifest.json    Metadata (date, DB name, version)

Typical migration workflow on a new host:
  git clone <repo>
  cp portal_backup_*.tar.gz <repo>/
  cd <repo>
  python scripts/portal.py restore portal_backup_YYYYMMDD_HHMMSS.tar.gz
  docker compose up -d
"""

import argparse
import datetime
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_env(path: Path) -> dict:
    """Minimal .env parser — no variable substitution, strips inline comments."""
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.split("#")[0].strip().strip('"').strip("'")
        result[key.strip()] = val
    return result


def _run(cmd: list, *, check=True, capture=False, stdin_data=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        input=stdin_data,
        cwd=ROOT,
    )


def _dc(*args, **kwargs):
    """Shorthand for docker compose <args>."""
    return _run(["docker", "compose"] + list(args), **kwargs)


def _container_running(service: str) -> bool:
    r = _dc("ps", "--status=running", "--services", capture=True, check=False)
    return service in r.stdout.decode().splitlines()


def _wait_for_db(timeout: int = 60) -> bool:
    print("  Waiting for database", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _dc("exec", "-T", "db", "pg_isready", capture=True, check=False)
        if r.returncode == 0:
            print(" ready.")
            return True
        print(".", end="", flush=True)
        time.sleep(2)
    print(" timed out!")
    return False


def _app_exec_or_run(dc_args: list, capture=False, check=False) -> subprocess.CompletedProcess:
    """Run a command in the app container, using exec if running, run otherwise."""
    if _container_running("app"):
        return _dc("exec", "-T", "app", *dc_args, capture=capture, check=check)
    return _dc(
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        dc_args[0],
        "app",
        *dc_args[1:],
        capture=capture,
        check=check,
    )


# ── BACKUP ─────────────────────────────────────────────────────────────────


def cmd_backup(args):
    env = _load_env(ROOT / ".env")
    pg_user = env.get("POSTGRES_USER", "assoportail")
    pg_db = env.get("POSTGRES_DB", "assoportail_dev")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output).resolve() if args.output else ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"portal_backup_{ts}.tar.gz"

    print(f"\nCreating backup → {archive}\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 1. PostgreSQL dump
        print("[1/4] Dumping PostgreSQL database...")
        r = _dc(
            "exec",
            "-T",
            "db",
            "pg_dump",
            "-U",
            pg_user,
            "-d",
            pg_db,
            "-Fc",
            capture=True,
        )
        dump_path = tmp / "db.dump"
        dump_path.write_bytes(r.stdout)
        print(f"      {len(r.stdout) // 1024} KB")

        # 2. Uploads volume
        print("[2/4] Archiving uploads volume...")
        r = _app_exec_or_run(
            ["tar", "czf", "-", "-C", "/data", "uploads"],
            capture=True,
        )
        (tmp / "uploads.tar.gz").write_bytes(r.stdout)
        print(f"      {len(r.stdout) // 1024} KB")

        # 3. credentials.json (Google OAuth — uploaded via portal UI)
        print("[3/4] Checking credentials.json...")
        r = _app_exec_or_run(["test", "-f", "/data/credentials.json"])
        if r.returncode == 0:
            r2 = _app_exec_or_run(["cat", "/data/credentials.json"], capture=True, check=True)
            (tmp / "credentials.json").write_bytes(r2.stdout)
            print(f"      {len(r2.stdout)} bytes")
        else:
            print("      (not found — skipping)")

        # 4. .env
        print("[4/4] Saving .env...")
        env_src = ROOT / ".env"
        if env_src.exists():
            shutil.copy(env_src, tmp / ".env")
            print("      OK")
        else:
            print("      WARNING: .env not found in project root")

        # Manifest
        (tmp / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                    "postgres_db": pg_db,
                    "postgres_user": pg_user,
                },
                indent=2,
            )
        )

        # Bundle everything into a single archive
        with tarfile.open(archive, "w:gz") as tar:
            for f in sorted(tmp.iterdir()):
                tar.add(f, arcname=f.name)

    size_mb = archive.stat().st_size / 1_048_576
    print(f"\nDone. {archive.name} ({size_mb:.1f} MB)")
    print("Keep this file safe — it contains secrets from .env and credentials.json.")


# ── RESTORE ────────────────────────────────────────────────────────────────


def cmd_restore(args):
    archive = Path(args.archive).resolve()
    if not archive.exists():
        sys.exit(f"ERROR: archive not found: {archive}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        print(f"\nExtracting {archive.name}...")
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp)

        manifest_path = tmp / "manifest.json"
        if not manifest_path.exists():
            sys.exit("ERROR: Invalid archive — manifest.json missing.")

        m = json.loads(manifest_path.read_text())
        print("\nBackup info:")
        print(f"  Created : {m.get('created_at', '?')}")
        print(f"  Database: {m.get('postgres_db', '?')}")

        if not args.yes:
            ans = input("\nThis will OVERWRITE the current database and uploads. Continue? [y/N] ")
            if ans.strip().lower() != "y":
                sys.exit("Aborted.")

        # ── Step 1: .env ──────────────────────────────────────────────────
        env_bak = tmp / ".env"
        env_dest = ROOT / ".env"
        if env_bak.exists():
            print("\n[1/5] Restoring .env...")
            if env_dest.exists() and not args.yes:
                ans = input("  .env already exists — overwrite? [y/N] ")
                if ans.strip().lower() == "y":
                    shutil.copy(env_bak, env_dest)
                    print("  Overwritten.")
                else:
                    print("  Kept existing .env.")
            else:
                shutil.copy(env_bak, env_dest)
                print("  Restored.")
        else:
            print("\n[1/5] No .env in archive — skipping.")

        env = _load_env(ROOT / ".env")
        pg_user = env.get("POSTGRES_USER", m.get("postgres_user", "assoportail"))
        pg_db = env.get("POSTGRES_DB", m.get("postgres_db", "assoportail_dev"))

        # ── Step 2: Start DB ──────────────────────────────────────────────
        print("\n[2/5] Starting database container...")
        _dc("up", "-d", "db")
        if not _wait_for_db():
            sys.exit("ERROR: Database did not become ready in time.")

        # ── Step 3: Restore DB ────────────────────────────────────────────
        db_dump = tmp / "db.dump"
        if db_dump.exists():
            print("\n[3/5] Restoring database...")
            # Wipe public schema cleanly before restore
            _dc(
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                pg_user,
                "-d",
                pg_db,
                "-c",
                "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
            )
            with open(db_dump, "rb") as f:
                r = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "db",
                        "pg_restore",
                        "-U",
                        pg_user,
                        "-d",
                        pg_db,
                        "--no-owner",
                    ],
                    input=f.read(),
                    cwd=ROOT,
                    capture_output=True,
                )
            # pg_restore exits 1 for non-fatal warnings — treat as success
            if r.returncode not in (0, 1):
                print(f"  WARNING: pg_restore exited with code {r.returncode}")
                if r.stderr:
                    print(r.stderr.decode(errors="replace")[:1000])
            else:
                print("  Database restored.")
        else:
            print("\n[3/5] No db.dump in archive — skipping.")

        # ── Step 4: Run migrations (catches any new ones added after the backup) ──
        print("\n[4/5] Running database migrations...")
        _dc("run", "--rm", "--no-deps", "app", "flask", "db", "upgrade")

        # ── Step 5: Uploads + credentials ─────────────────────────────────
        uploads_tar = tmp / "uploads.tar.gz"
        if uploads_tar.exists() and uploads_tar.stat().st_size > 20:
            print("\n[5/5] Restoring uploads volume...")
            with open(uploads_tar, "rb") as f:
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--entrypoint",
                        "tar",
                        "app",
                        "xzf",
                        "-",
                        "-C",
                        "/data",
                    ],
                    input=f.read(),
                    cwd=ROOT,
                    check=True,
                )
            print("  Uploads restored.")
        else:
            print("\n[5/5] No uploads in archive — skipping.")

        creds = tmp / "credentials.json"
        if creds.exists():
            print("  Restoring credentials.json...")
            with open(creds, "rb") as f:
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--entrypoint",
                        "sh",
                        "app",
                        "-c",
                        "cat > /data/credentials.json",
                    ],
                    input=f.read(),
                    cwd=ROOT,
                    check=True,
                )
            print("  credentials.json restored.")

    print("\nRestore complete!")
    print("\nStart the portal:")
    print("  docker compose up -d")


# ── MAIN ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Portal backup/restore tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pb = sub.add_parser("backup", help="Create a backup archive")
    pb.add_argument(
        "--output",
        "-o",
        metavar="DIR",
        help="Directory where the archive is written (default: project root)",
    )

    pr = sub.add_parser("restore", help="Restore from a backup archive")
    pr.add_argument("archive", help="Path to portal_backup_YYYYMMDD_HHMMSS.tar.gz")
    pr.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip all confirmation prompts (for scripted use)",
    )

    args = parser.parse_args()
    {"backup": cmd_backup, "restore": cmd_restore}[args.command](args)


if __name__ == "__main__":
    main()
