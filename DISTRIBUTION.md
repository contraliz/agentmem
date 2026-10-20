# Local distribution artifacts

Build reviewable artifacts from the current source tree with Python 3.11 or newer and the `build` package installed:

```console
python -m pip install build
python scripts/build_distribution.py
```

The command writes a wheel, source distribution, repository ZIP, and `SHA256SUMS.txt` under `dist/`. It uses the current working tree, including uncommitted and untracked source files. It excludes `.git`, local AgentMem data, virtual environments, generated caches/build outputs, local project instructions, and the Codex installer script. It does not publish artifacts or modify Git history.

AgentMem is licensed under MIT, as selected by the project owner. The license text is included in each source and package artifact. A PyPI or other registry upload still requires a separate release decision and a registry availability check for both the package name and version; local builds do not prove either name or version is available.

The repository ZIP is intended for source review or local installation. It does not contain Git history or user-specific project state. The public history may contain previously disclosed personal commit metadata or path references; a clean artifact does not remove those historical copies. Review and authorize any history remediation separately.
