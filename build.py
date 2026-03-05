import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "EBH-SQL-Formatter"


def main():
    for d in ["build", "dist"]:
        if Path(d).exists():
            shutil.rmtree(d)

    # PyInstaller needs a script path, not -m
    entry = str(Path("src") / "app.py")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", APP_NAME,
        "--paths", "src",
        entry,
    ]

    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd)


if __name__ == "__main__":
    main()
