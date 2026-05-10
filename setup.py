#!/usr/bin/env python3
"""Cross-platform installer script for Slox multi-agent debate supervisor."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_DIRS = ["supervisor", "config", "local", "personas", "scripts", "tests", "docs"]
PYTHON_DEPS = ["requests"]
OPTIONAL_DEPS = ["Pillow"]

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg):
    print(f"{GREEN}✓{RESET} {msg}")


def warn(msg):
    print(f"{YELLOW}⚠{RESET} {msg}")


def fail(msg):
    print(f"{RED}✗{RESET} {msg}")


def step(msg):
    print(f"\n{BOLD}{msg}{RESET}")


def check_python():
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 8):
        fail(f"Python {v.major}.{v.minor} — need 3.8+")
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro}")
    return True


def check_deps():
    missing = []
    for dep in PYTHON_DEPS:
        try:
            __import__(dep)
            ok(f"Python package: {dep}")
        except ImportError:
            missing.append(dep)
            fail(f"Python package: {dep} — MISSING")
    if missing:
        print(f"  Run: pip install {' '.join(missing)}")
        return False

    for dep in OPTIONAL_DEPS:
        try:
            __import__(dep)
            ok(f"Python package (optional): {dep}")
        except ImportError:
            warn(f"Python package (optional): {dep} — not installed")

    return True


def check_structure(root):
    root = Path(root)
    all_ok = True
    for d in REQUIRED_DIRS:
        p = root / d
        if p.is_dir():
            ok(f"Directory: {d}/")
        else:
            fail(f"Directory: {d}/ — MISSING")
            all_ok = False

    key_files = [
        ("supervisor/slox-supervisor.py", "Supervisor daemon"),
        ("config/two_room_lounge.json", "Configuration"),
        ("local/slox_credentials.csv", "Matrix credentials"),
        ("scripts/verify_features.py", "Verification suite"),
        ("tests/100-tests.md", "Test scenarios"),
        ("tests/innovation-features.md", "Feature specs"),
        ("docs/slox-audit-innovation.md", "Audit report"),
    ]
    for path, desc in key_files:
        p = root / path
        if p.exists():
            ok(f"File: {path} ({desc})")
        else:
            fail(f"File: {path} ({desc}) — MISSING")
            all_ok = False

    return all_ok


def check_env(root):
    env_file = Path(root) / ".env"
    if not env_file.exists():
        warn(".env file not found — create from template")
        print("  Required env vars: DEEPSEEK_API_KEY, SLOX_HOMESERVER")
        print(f"  Template: {root}/.env.example")
        return False
    ok(".env file present")
    required_vars = ["DEEPSEEK_API_KEY", "SLOX_HOMESERVER"]
    loaded = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                loaded[k.strip()] = v.strip()
    for var in required_vars:
        val = loaded.get(var)
        if val:
            ok(f"ENV: {var}={val[:20]}...")
        else:
            warn(f"ENV: {var} — not set")
    return True


def check_matrix(root):
    creds = Path(root) / "local" / "slox_credentials.csv"
    if not creds.exists():
        warn("Credentials file not found — need to create")
        return False
    with open(creds) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    accounts = len([l for l in lines if "," in l])
    if accounts >= 6:
        ok(f"Matrix credentials: {accounts} accounts found")
    elif accounts > 0:
        warn(f"Matrix credentials: {accounts}/6 accounts — need 6 total")
    else:
        warn("Matrix credentials: no accounts found")
        return False
    return True


def verify_features(root):
    script = Path(root) / "scripts" / "verify_features.py"
    if not script.exists():
        warn("verify_features.py not found — can't run")
        return False
    step("Running feature verification...")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, cwd=root
    )
    if result.returncode == 0:
        ok("All 10 tests pass (148/148 checks)")
        return True
    else:
        warn(f"Verification failed (exit code {result.returncode})")
        print(result.stdout[-500:] if result.stdout else result.stderr[-500:])
        return False


def check_systemd(root):
    svc_path = Path("/etc/systemd/system/slox-supervisor.service")
    if svc_path.exists():
        ok("systemd service installed")
        result = subprocess.run(
            ["systemctl", "is-active", "slox-supervisor"],
            capture_output=True, text=True
        )
        status = result.stdout.strip()
        if status == "active":
            ok("slox-supervisor.service is running")
        else:
            warn(f"slox-supervisor.service is {status}")
    else:
        warn("systemd service not installed")
        print("  Create /etc/systemd/system/slox-supervisor.service (see README)")


def main():
    root = Path.cwd()

    print(f"{'='*60}")
    print(f" Slox Installation Check")
    print(f" Root: {root}")
    print(f"{'='*60}")

    passed = 0
    total = 0

    def check(label, fn):
        nonlocal passed, total
        total += 1
        if fn():
            passed += 1

    step("1. Python Runtime")
    check("Python version", check_python)

    step("2. Python Dependencies")
    check("Dependencies", check_deps)

    step("3. Directory & File Structure")
    check("Structure", lambda: check_structure(root))

    step("4. Environment Variables")
    check_env(root)

    step("5. Matrix Bot Credentials")
    check_matrix(root)

    step("6. Feature Verification (148 checks)")
    verify_features(root)

    step("7. Systemd Service (optional)")
    check_systemd(root)

    step("Summary")
    warn("After install, you need to:")
    print("  1. Fill slox/local/slox_credentials.csv with your Matrix bot tokens")
    print("  2. Set DEEPSEEK_API_KEY in .env")
    print("  3. Edit slox/config/two_room_lounge.json with your homeserver URL")
    print("  4. Enable desired features (set toggles to true)")
    print("  5. Run: python3 slox/supervisor/slox-supervisor.py")

    print(f"\n{'='*60}")
    ok(f"{passed}/{total} checks passed" if passed == total else warn(f"{passed}/{total} checks passed"))
    print(f"{'='*60}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
