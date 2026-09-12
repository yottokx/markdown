"""Refresh pinned offline preview libraries, or verify their local SHA-256 inventory.

Run ``uv run python tools/update_preview_vendor.py --refresh`` to download the
pinned official npm archives and license texts. The default performs no network IO.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import io
import json
import re
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "src/md_editor/resources/vendor"
PINS = {
    "katex": ("0.18.7", "9a80a3fba2367e99bf67b52bfff52e9534c8c7f198e4eaa12699e4f1df9a0bda"),
    "mermaid": ("11.17.2", "6ad2f42c3fc26bbf9e45cbb6d11898972573ea52b33a5f4ff51952899f950ffd"),
}
DOCS = {
    "katex": "https://katex.org/docs/browser",
    "mermaid": "https://mermaid.js.org/intro/getting-started.html",
}


def download_package(name: str, version: str, expected_sha256: str | None = None):
    metadata_url = f"https://registry.npmjs.org/{name}/{version}"
    request = urllib.request.Request(metadata_url, headers={"User-Agent": "md-editor-vendor/1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        metadata = json.load(response)
    archive_url = metadata["dist"]["tarball"]
    if not archive_url.startswith("https://registry.npmjs.org/"):
        raise ValueError(f"Unexpected archive host: {archive_url}")
    with urllib.request.urlopen(archive_url, timeout=60) as response:
        archive = response.read()
    sha256 = hashlib.sha256(archive).hexdigest()
    if expected_sha256 is not None and sha256 != expected_sha256:
        raise ValueError(f"Pinned SHA-256 mismatch: {name}@{version}")
    integrity = metadata["dist"].get("integrity", "")
    algorithm, expected = integrity.split("-", 1)
    if base64.b64encode(hashlib.new(algorithm, archive).digest()).decode() != expected:
        raise ValueError(f"npm archive integrity mismatch: {name}@{version}")
    info = {
        "name": name,
        "version": version,
        "license": metadata.get("license"),
        "metadata_url": metadata_url,
        "archive_url": archive_url,
        "archive_sha256": sha256,
        "npm_integrity": integrity,
    }
    return info, archive


def write_member(tar, member, relative: str, source: str, inventory: dict):
    target = VENDOR / relative
    if not target.resolve().is_relative_to(VENDOR.resolve()) or not member.isfile():
        raise ValueError(f"Unsafe archive member: {member.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = tar.extractfile(member).read()
    target.write_bytes(data)
    inventory[relative] = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "source": f"{source}#{member.name}",
    }


def install_runtime(name: str, info: dict, archive: bytes, inventory: dict):
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            selected = (
                member.name == "package/LICENSE"
                or member.name == f"package/dist/{name}.min.js"
                or name == "katex"
                and (
                    member.name == "package/dist/katex.min.css"
                    or member.name.startswith("package/dist/fonts/")
                )
            )
            if selected and member.isfile():
                relative = member.name.removeprefix("package/dist/").removeprefix("package/")
                write_member(tar, member, f"{name}/{relative}", info["archive_url"], inventory)
        if name != "mermaid":
            return []
        source_map = json.load(tar.extractfile("package/dist/mermaid.min.js.map"))
        packages = set()
        for source in source_map["sources"]:
            if "/.pnpm/" not in source:
                continue
            package = source.split("/.pnpm/", 1)[1].split("/node_modules/", 1)[0].split("_", 1)[0]
            dependency, version = package.rsplit("@", 1)
            packages.add((dependency.replace("+", "/"), version))
        return sorted(packages)


def dependency_license(package):
    name, version = package
    info, archive = download_package(name, version)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            relative = member.name.removeprefix("package/")
            if (
                "/" not in relative
                and re.match(r"(?i)^(licen[sc]e|copying|notice)([._-]|$)", relative)
                and member.isfile()
            ):
                files[relative] = tar.extractfile(member).read()
        if not files:
            for member in tar.getmembers():
                if member.name.lower() == "package/readme.md":
                    data = tar.extractfile(member).read()
                    if b"Permission is hereby granted" in data and b"SOFTWARE IS PROVIDED" in data:
                        # Some packages (FastDom) ship their complete license
                        # only within README. Preserve those bytes unchanged.
                        files["README.md"] = data
    if not files:
        raise ValueError(f"No root license files in {name}@{version}")
    return info, files


def refresh():
    inventory, packages, bundled = {}, [], []
    for name, (version, digest) in PINS.items():
        info, archive = download_package(name, version, digest)
        info["documentation_url"] = DOCS[name]
        info["global"] = name
        info["entrypoint"] = f"{name}/{name}.min.js"
        dependencies = install_runtime(name, info, archive, inventory)
        if dependencies:
            bundled = dependencies
        packages.append(info)
    third_party = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for info, files in pool.map(dependency_license, bundled):
            prefix = info["name"].replace("@", "").replace("/", "__") + "-" + info["version"]
            info["license_files"] = []
            for filename, data in files.items():
                relative = f"mermaid/licenses/{prefix}/{filename}"
                target = VENDOR / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                inventory[relative] = {
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "source": f"{info['archive_url']}#package/{filename}",
                }
                info["license_files"].append(relative)
            third_party.append(info)
    manifest = {
        "format": 1,
        "generated_utc": datetime.now(UTC).isoformat(),
        "description": "Unmodified pinned official npm browser bundles. No network required at runtime.",
        "packages": packages,
        "mermaid_bundled_license_packages": third_party,
        "files": dict(sorted(inventory.items())),
    }
    (VENDOR / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Installed {len(inventory)} files; {len(third_party)} bundled license packages")


def verify():
    manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        target = VENDOR / relative
        if not target.resolve().is_relative_to(VENDOR.resolve()):
            raise ValueError(f"Invalid manifest path: {relative}")
        data = target.read_bytes()
        if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError(f"Modified vendor asset: {relative}")
    css = (VENDOR / "katex/katex.min.css").read_text(encoding="utf-8")
    for font in re.findall(r"url\((?:[\"']?)(fonts/[^)\"']+)", css):
        if not (VENDOR / "katex" / font).is_file():
            raise ValueError(f"Missing KaTeX font: {font}")
    for package in manifest["packages"]:
        source = (VENDOR / package["entrypoint"]).read_text(encoding="utf-8")
        if re.search(r"\bimport\s*\(", source):
            raise ValueError(f"Unexpected dynamic import in classic bundle: {package['name']}")
    print(f"Verified {len(manifest['files'])} SHA-256 hashes and all KaTeX font references")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Download the pinned npm assets")
    arguments = parser.parse_args()
    if arguments.refresh:
        refresh()
    verify()
