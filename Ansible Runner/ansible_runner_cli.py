#!/usr/bin/env python3
"""
ansible_runner_cli.py
A small CLI wrapper to list and run Ansible playbooks with nice logging and
a fallback from ansible-runner (Python API) to calling ansible-playbook.

Usage examples:
  # list playbooks
  ./ansible_runner_cli.py list

  # run playbook with default inventory
  ./ansible_runner_cli.py run site-backup.yml --inventory inventories/siteA --limit "host01"

  # run with extra-vars and tags, dry run
  ./ansible_runner_cli.py run site-update.yml -i inventories/siteB -e '{"reboot":true}' -t "install,configure" --check

Dependencies:
  - Python 3.8+
  - pip install ansible-runner pyyaml  (ansible-runner optional; fallback uses ansible-playbook binary)
  - ansible installed (for subprocess fallback)
"""

import argparse
import json
import os
import subprocess
import sys
import shutil
import datetime
import logging
from pathlib import Path

try:
    import yaml
except Exception:
    print("Please install pyyaml: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# Try to import ansible-runner (optional)
try:
    import ansible_runner  # type: ignore
    HAS_ANSIBLE_RUNNER = True
except Exception:
    HAS_ANSIBLE_RUNNER = False

# Configuration defaults
BASE_DIR = Path.home() / ".local" / "ansible-runner-cli"
PLAYBOOKS_DIR = Path.cwd() / "playbooks"             # recommended place for playbooks
INVENTORIES_DIR = Path.cwd() / "inventories"         # recommended place for inventories
LOG_DIR = BASE_DIR / "logs"
RUNNER_DIR = BASE_DIR / "runs"

for d in (BASE_DIR, LOG_DIR, RUNNER_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Logging
logging.basicConfig(
    filename=str(LOG_DIR / "ansible_runner_cli.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def list_playbooks(playbooks_dir: Path):
    """Return list of playbook files (yml/yaml) in playbooks_dir."""
    if not playbooks_dir.exists():
        print(f"No playbooks directory found at {playbooks_dir!s}")
        return []
    files = sorted([p for p in playbooks_dir.glob("*.y*ml")])
    for p in files:
        print(p.name)
    if not files:
        print("(no playbooks found)")
    return files

def _timestamped_name(prefix="run"):
    return prefix + "-" + datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

def run_with_ansible_runner(playbook_path: Path, inventory: str, extra_vars: dict,
                            tags: str, limit: str, check: bool, become: bool,
                            vault_password_file: str = None):
    """Run using ansible-runner Python API."""
    private_data_dir = str(RUNNER_DIR / _timestamped_name("run"))
    os.makedirs(private_data_dir, exist_ok=True)

    # Prepare env & extravars
    runner_config = {
        "playbook": str(playbook_path),
        "inventory": inventory if inventory else None,
        "extravars": extra_vars or None,
        "verbosity": 0,
    }

    # Build runner kwargs
    runner_kwargs = {
        "private_data_dir": private_data_dir,
        "playbook": str(playbook_path),
        "inventory": inventory or None,
        "extravars": extra_vars or None,
    }

    if vault_password_file:
        # place vault password into env so ansible-runner picks it up
        os.environ['ANSIBLE_VAULT_PASSWORD_FILE'] = str(vault_password_file)

    if tags:
        runner_kwargs["cmdline"] = f"--tags {tags}"
    if limit:
        runner_kwargs["cmdline"] = (runner_kwargs.get("cmdline", "") + f" --limit {limit}").strip()
    if check:
        runner_kwargs["cmdline"] = (runner_kwargs.get("cmdline", "") + " --check").strip()
    if become:
        runner_kwargs["cmdline"] = (runner_kwargs.get("cmdline", "") + " --become").strip()

    logging.info("Starting ansible-runner run: %s", runner_kwargs)
    print(f"[runner] running playbook {playbook_path.name} with ansible-runner (dir: {private_data_dir})")
    r = ansible_runner.run(**runner_kwargs)
    status = r.status  # 'successful', 'failed', 'timeout', etc.
    rc = r.rc
    stdout_path = Path(private_data_dir) / "artifacts" / "ansible.log"
    # ansible-runner stores event data, but there's no single stdout file by default.
    # We'll record run summary in our logs.
    summary = {
        "status": status,
        "rc": rc,
        "private_data_dir": private_data_dir
    }
    logging.info("ansible-runner result: %s", summary)
    print("RESULT:", status, "(rc=" + str(rc) + ")")
    return summary

def run_with_subprocess(playbook_path: Path, inventory: str, extra_vars: dict,
                        tags: str, limit: str, check: bool, become: bool,
                        vault_password_file: str = None):
    """Run using ansible-playbook CLI via subprocess and capture output to timestamped log."""
    ansible_playbook = shutil.which("ansible-playbook")
    if not ansible_playbook:
        raise RuntimeError("ansible-playbook binary not found in PATH. Install Ansible or set PATH.")

    cmd = [ansible_playbook, str(playbook_path)]

    if inventory:
        cmd.extend(["-i", inventory])
    if tags:
        cmd.extend(["--tags", tags])
    if limit:
        cmd.extend(["--limit", limit])
    if check:
        cmd.append("--check")
    if become:
        cmd.append("--become")
    if extra_vars:
        # pass as JSON to be safe
        cmd.extend(["--extra-vars", json.dumps(extra_vars)])

    env = os.environ.copy()
    if vault_password_file:
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_password_file)

    run_name = _timestamped_name("run")
    logfile = LOG_DIR / (run_name + ".log")
    print(f"[subprocess] running: {' '.join(cmd)}")
    print(f"Logging to: {logfile}")
    logging.info("Running subprocess ansible-playbook: %s", " ".join(cmd))
    with open(logfile, "wb") as fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        # stream output to both console and file
        for line in proc.stdout:
            fh.write(line)
            fh.flush()
            try:
                print(line.decode(errors="ignore"), end="")
            except Exception:
                # if decode fails, ignore
                pass
        proc.wait()
        rc = proc.returncode

    summary = {
        "rc": rc,
        "logfile": str(logfile)
    }
    logging.info("subprocess result: %s", summary)
    print("RESULT: rc =", rc)
    return summary

def find_playbook(playbooks_dir: Path, name: str) -> Path:
    p = playbooks_dir / name
    if p.exists():
        return p
    # try with .yml/.yaml
    if not (name.endswith(".yml") or name.endswith(".yaml")):
        for ext in (".yml", ".yaml"):
            candidate = playbooks_dir / (name + ext)
            if candidate.exists():
                return candidate
    raise FileNotFoundError(f"Playbook {name} not found in {playbooks_dir}")

def main():
    parser = argparse.ArgumentParser(prog="ansible_runner_cli", description="Run ansible playbooks easily.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    parser_list = sub.add_parser("list", help="List playbooks in playbooks/")

    parser_run = sub.add_parser("run", help="Run a playbook")
    parser_run.add_argument("playbook", help="Playbook filename (from playbooks/ or path)")
    parser_run.add_argument("-i", "--inventory", help="Inventory file or directory (default: inventories/)", default=str(INVENTORIES_DIR))
    parser_run.add_argument("-e", "--extra-vars", help="Extra vars as JSON or key=value pairs", default=None)
    parser_run.add_argument("-t", "--tags", help="Comma-separated tags", default=None)
    parser_run.add_argument("-l", "--limit", help="Limit to hosts pattern", default=None)
    parser_run.add_argument("--check", help="Do a dry-run (ansible --check)", action="store_true")
    parser_run.add_argument("--become", help="Use become (sudo)", action="store_true")
    parser_run.add_argument("--vault-password-file", help="Path to vault password file", default=None)
    parser_run.add_argument("--playbooks-dir", help="Playbooks directory", default=str(PLAYBOOKS_DIR))
    parser_run.add_argument("--no-runner", help="Do not attempt to use ansible-runner Python API even if installed", action="store_true")

    args = parser.parse_args()

    if args.cmd == "list":
        list_playbooks(PLAYBOOKS_DIR)
        return 0

    if args.cmd == "run":
        # resolve playbooks dir
        pb_dir = Path(args.playbooks_dir)
        if not pb_dir.exists():
            print(f"Playbooks directory {pb_dir} does not exist. Creating it for you.")
            pb_dir.mkdir(parents=True, exist_ok=True)

        # resolve playbook path
        try:
            playbook_path = find_playbook(pb_dir, args.playbook)
        except FileNotFoundError as e:
            # allow absolute path too
            alt = Path(args.playbook)
            if alt.exists():
                playbook_path = alt
            else:
                print(str(e), file=sys.stderr)
                sys.exit(2)

        # parse extra vars
        extra_vars = None
        if args.extra_vars:
            s = args.extra_vars.strip()
            if s.startswith("{") or s.startswith("["):
                try:
                    extra_vars = json.loads(s)
                except Exception as ex:
                    print("Failed to parse extra-vars JSON:", ex, file=sys.stderr)
                    sys.exit(3)
            else:
                # key=value pairs separated by commas or spaces
                extra_vars = {}
                items = [x.strip() for x in s.replace(",", " ").split() if x.strip()]
                for it in items:
                    if "=" in it:
                        k, v = it.split("=", 1)
                        extra_vars[k] = _coerce_value(v)
                    else:
                        extra_vars[it] = True

        # choose run method
        use_runner = HAS_ANSIBLE_RUNNER and (not args.no_runner)
        try:
            if use_runner:
                result = run_with_ansible_runner(
                    playbook_path=playbook_path,
                    inventory=args.inventory,
                    extra_vars=extra_vars,
                    tags=args.tags,
                    limit=args.limit,
                    check=args.check,
                    become=args.become,
                    vault_password_file=args.vault_password_file
                )
            else:
                result = run_with_subprocess(
                    playbook_path=playbook_path,
                    inventory=args.inventory,
                    extra_vars=extra_vars,
                    tags=args.tags,
                    limit=args.limit,
                    check=args.check,
                    become=args.become,
                    vault_password_file=args.vault_password_file
                )
        except Exception as exc:
            logging.exception("Run failed")
            print("ERROR:", exc, file=sys.stderr)
            sys.exit(4)

        # final summary
        print("\n--- run summary ---")
        for k, v in result.items():
            print(f"{k}: {v}")
        return 0

def _coerce_value(v: str):
    # try to coerce common types
    if v.lower() in ("true", "yes", "on"):
        return True
    if v.lower() in ("false", "no", "off"):
        return False
    try:
        if "." in v:
            return float(v)
        return int(v)
    except Exception:
        return v

if __name__ == "__main__":
    sys.exit(main())
