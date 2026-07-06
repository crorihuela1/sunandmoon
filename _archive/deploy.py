#!/usr/bin/env python3
"""
deploy.py — Publish the Sun & Moon at 30A site to Cloudflare Pages.

Pure-Python deployer (no Node, no Wrangler). It uploads the contents of this
folder to a Cloudflare Pages project using the same Direct Upload flow that
Wrangler uses internally:

    1. Get a short-lived upload JWT for the project
    2. Hash every file and ask Cloudflare which ones are missing
    3. Upload only the missing files (so re-deploys are fast)
    4. Create a new deployment from the full file manifest

Run it from this folder, on your own computer (it needs internet access to
api.cloudflare.com, which this site's sandbox does not have):

    python3 deploy.py                 # deploy to production
    python3 deploy.py --dry-run       # show what would upload, change nothing
    python3 deploy.py --branch staging  # deploy to a preview branch

------------------------------------------------------------------------------
ONE-TIME SETUP
------------------------------------------------------------------------------
1. Install the two dependencies:
       pip3 install requests blake3

2. Provide your Cloudflare credentials. Either set environment variables:
       export CLOUDFLARE_API_TOKEN="..."     # token with Account > Cloudflare Pages > Edit
       export CLOUDFLARE_ACCOUNT_ID="..."     # 32-char hex from the dashboard
   ...or copy deploy.config.example.json to deploy.config.json and fill it in
   (that file is git-ignored so your token never gets committed or deployed).

   Create the API token at: https://dash.cloudflare.com/?to=/:account/api-tokens
   -> Create Token -> Custom Token -> Permissions: Account · Cloudflare Pages · Edit
------------------------------------------------------------------------------
"""

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
from pathlib import Path

try:
    import requests
    from blake3 import blake3
except ImportError:
    sys.exit("Missing dependencies. Run:  pip3 install requests blake3")

API_BASE = "https://api.cloudflare.com/client/v4"

# Files/folders that should never be published to the live site.
IGNORE_DIRS = {".git", ".github", "node_modules", "__pycache__"}
IGNORE_NAMES = {
    ".DS_Store", ".gitignore", "README.md", "_missing_files.txt",
    "deploy.py", "deploy.config.json", "deploy.config.example.json",
    ".deploy-state.json", "install-autodeploy.sh", "deploy.log",
}
IGNORE_SUFFIXES_EXTRA = {".sh"}
STATE_FILE = ".deploy-state.json"
IGNORE_SUFFIXES = {".py", ".pyc"}

# Cloudflare upload batching limits (kept conservative).
MAX_BATCH_FILES = 200
MAX_BATCH_BYTES = 40 * 1024 * 1024  # 40 MB of base64 payload per request


def load_config():
    """Read credentials from env vars first, then deploy.config.json."""
    cfg = {
        "api_token": os.environ.get("CLOUDFLARE_API_TOKEN", ""),
        "account_id": os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
        "project_name": os.environ.get("CLOUDFLARE_PROJECT_NAME", "sun-and-moon-30a"),
    }
    cfg_file = Path(__file__).parent / "deploy.config.json"
    if cfg_file.exists():
        data = json.loads(cfg_file.read_text())
        for key in ("api_token", "account_id", "project_name"):
            if data.get(key):
                cfg[key] = data[key]
    missing = [k for k in ("api_token", "account_id") if not cfg[k]]
    if missing:
        sys.exit(
            "Missing credentials: " + ", ".join(missing) +
            "\nSet CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID, or fill in "
            "deploy.config.json (see deploy.config.example.json)."
        )
    return cfg


def hash_file(path: Path) -> str:
    """Reproduce Wrangler's content hash: blake3(base64(content)+ext)[:32]."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    ext = path.suffix[1:]  # extension without the leading dot
    return blake3((b64 + ext).encode()).hexdigest()[:32]


def gather_files(root: Path):
    """Return {url_path: (Path, hash)} for every publishable file under root."""
    files = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        if p.name in IGNORE_NAMES or p.suffix.lower() in (IGNORE_SUFFIXES | IGNORE_SUFFIXES_EXTRA):
            continue
        url = "/" + str(p.relative_to(root)).replace(os.sep, "/")
        files[url] = (p, hash_file(p))
    return files


def cf_request(method, url, token, **kwargs):
    """Call the Cloudflare API and unwrap the standard envelope."""
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, url, headers=headers, timeout=60, **kwargs)
    try:
        body = resp.json()
    except ValueError:
        sys.exit(f"Cloudflare returned non-JSON ({resp.status_code}):\n{resp.text[:400]}")
    if not body.get("success", False):
        errs = body.get("errors", [])
        sys.exit(f"Cloudflare API error on {url}\n  HTTP {resp.status_code}: {errs}")
    return body["result"]


def get_upload_jwt(cfg):
    url = f"{API_BASE}/accounts/{cfg['account_id']}/pages/projects/{cfg['project_name']}/upload-token"
    return cf_request("GET", url, cfg["api_token"])["jwt"]


def check_missing(cfg, jwt, hashes):
    url = f"{API_BASE}/accounts/{cfg['account_id']}/pages/projects/{cfg['project_name']}/assets/check-missing"
    return cf_request("POST", url, jwt, json={"hashes": hashes})


def upload_assets(cfg, jwt, files, missing):
    """Upload only the files whose hash is in `missing`, in size-bounded batches."""
    by_hash = {}
    for url, (path, h) in files.items():
        if h in missing and h not in by_hash:
            by_hash[h] = path
    payloads = []
    for h, path in by_hash.items():
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        payloads.append({
            "key": h,
            "value": base64.b64encode(path.read_bytes()).decode(),
            "metadata": {"contentType": ctype},
            "base64": True,
        })

    url = f"{API_BASE}/accounts/{cfg['account_id']}/pages/projects/{cfg['project_name']}/assets/upload"
    batch, batch_bytes, sent = [], 0, 0
    def flush():
        nonlocal batch, batch_bytes, sent
        if not batch:
            return
        cf_request("POST", url, jwt, json=batch)
        sent += len(batch)
        print(f"   uploaded {sent}/{len(payloads)} new files")
        batch, batch_bytes = [], 0
    for item in payloads:
        size = len(item["value"])
        if batch and (len(batch) >= MAX_BATCH_FILES or batch_bytes + size > MAX_BATCH_BYTES):
            flush()
        batch.append(item)
        batch_bytes += size
    flush()
    return len(payloads)


def upsert_hashes(cfg, jwt, hashes):
    """Tell the project the full set of asset hashes (best-effort)."""
    url = f"{API_BASE}/accounts/{cfg['account_id']}/pages/projects/{cfg['project_name']}/assets/upsert-hashes"
    try:
        cf_request("POST", url, jwt, json={"hashes": hashes})
    except SystemExit:
        pass  # non-fatal; deployment manifest is the source of truth


def create_deployment(cfg, files, branch):
    manifest = {url: h for url, (_, h) in files.items()}
    url = f"{API_BASE}/accounts/{cfg['account_id']}/pages/projects/{cfg['project_name']}/deployments"
    form = {"manifest": (None, json.dumps(manifest))}
    if branch:
        form["branch"] = (None, branch)
    return cf_request("POST", url, cfg["api_token"], files=form)


def main():
    ap = argparse.ArgumentParser(description="Deploy this folder to Cloudflare Pages.")
    ap.add_argument("--dry-run", action="store_true", help="List files and hashes; do not upload.")
    ap.add_argument("--branch", default=None, help="Deploy to a preview branch instead of production.")
    ap.add_argument("--dir", default=str(Path(__file__).parent), help="Folder to deploy (default: this folder).")
    ap.add_argument("--skip-unchanged", action="store_true",
                    help="Exit without deploying if nothing changed since the last deploy (for scheduled runs).")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    files = gather_files(root)
    if not files:
        sys.exit(f"No publishable files found in {root}")

    print(f"Sun & Moon at 30A — Cloudflare Pages deploy")
    print(f"  source : {root}")
    print(f"  files  : {len(files)}")

    if args.dry_run:
        for url, (_, h) in files.items():
            print(f"   {h}  {url}")
        print("\nDry run only — nothing uploaded.")
        return

    # Fingerprint the full manifest so scheduled runs can skip no-op deploys.
    manifest = {url: h for url, (_, h) in files.items()}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    state_path = root / STATE_FILE
    if args.skip_unchanged and state_path.exists():
        try:
            if json.loads(state_path.read_text()).get("digest") == digest:
                print("No changes since last deploy — skipping.")
                return
        except (ValueError, OSError):
            pass

    cfg = load_config()
    print(f"  project: {cfg['project_name']}")

    all_hashes = sorted({h for _, h in files.values()})
    print("→ requesting upload token...")
    jwt = get_upload_jwt(cfg)
    print("→ checking which files Cloudflare already has...")
    missing = check_missing(cfg, jwt, all_hashes)
    print(f"   {len(missing)} of {len(all_hashes)} files need uploading")
    if missing:
        upload_assets(cfg, jwt, files, set(missing))
    upsert_hashes(cfg, jwt, all_hashes)
    print("→ creating deployment...")
    result = create_deployment(cfg, files, args.branch)

    try:
        state_path.write_text(json.dumps({"digest": digest, "url": result.get("url", "")}))
    except OSError:
        pass

    url = result.get("url") or "(check the dashboard)"
    print("\n✓ Deployed.")
    print(f"  Live at: {url}")
    print(f"  Custom domain (if configured) updates within ~30s.")


if __name__ == "__main__":
    main()
