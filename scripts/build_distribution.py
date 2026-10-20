from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist"
ZIP_EXCLUDES = {
    ".git", ".agentmem", ".agentmem-data", ".venv", "venv", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "node_modules", "AGENTS.md", "install-codex.ps1",
}
SECRET_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".log", ".token", ".secret", ".pem", ".key", ".p12", ".pfx"}
PRIVATE_PATH = re.compile(r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+|[A-Z]:\\(?:programming|stuff|ethan)\\[^\\\s]+|/Users/[^/\s]+/|/home/[^/\s]+/)")


def source_files() -> list[Path]:
    selected = [ROOT / name for name in (
        ".gitignore", "pyproject.toml", "MANIFEST.in", "README.md", "LICENSE", "DISTRIBUTION.md",
        "ARCHITECTURE.md", "INFORMATION-POLICY-IMPLEMENTATION.md", "PROJECT-AGENTS.template.md",
        "agentmem.ps1",
    )]
    selected += list((ROOT / "src" / "agentmem").rglob("*.py"))
    selected += list((ROOT / "tests").rglob("*.py"))
    selected += [ROOT / "scripts" / "setup.ps1", ROOT / "scripts" / "build_distribution.py"]
    result = []
    for path in sorted(set(selected)):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if any(part in ZIP_EXCLUDES for part in relative.parts) or path.suffix.lower() in SECRET_SUFFIXES:
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        data = path.read_bytes()
        try:
            contents = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if path.name != "build_distribution.py" and PRIVATE_PATH.search(contents):
            raise SystemExit(f"Refusing source ZIP: personal workstation path found in {relative.as_posix()}")
        result.append(path)
    return result


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if not version:
        raise SystemExit("Project version is missing")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(OUTPUT)], cwd=ROOT, check=True)

    archive = OUTPUT / f"{project['name'].replace('-', '_')}-{version}-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in source_files():
            bundle.write(path, f"agentmem-{version}/{path.relative_to(ROOT).as_posix()}")

    artifacts = [OUTPUT / f"{project['name'].replace('-', '_')}-{version}-py3-none-any.whl", OUTPUT / f"{project['name'].replace('-', '_')}-{version}.tar.gz", archive]
    missing = [path.name for path in artifacts if not path.is_file()]
    if missing:
        raise SystemExit(f"Expected build artifacts are missing: {missing}")
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in artifacts]
    (OUTPUT / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for path in [*artifacts, OUTPUT / "SHA256SUMS.txt"]:
        print(f"{path.name}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
