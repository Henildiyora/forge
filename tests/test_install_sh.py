from __future__ import annotations

import subprocess
from pathlib import Path


def test_install_sh_passes_bash_syntax_check() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "install.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
