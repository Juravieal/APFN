"""Weekly refresh: pull everything from Sleeper and rebuild every page.

Usage: python scripts/update.py

The public pages are normally built and published by GitHub Actions; this is for working
locally, and it is the only thing that builds the private advisor page.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [["pull_sleeper.py", "--nfl", "--players"], ["build_dataset.py"], ["build_site.py"],
         ["build_advisor.py"], ["build_live.py"]]

for step in STEPS:
    print(">>", " ".join(step), flush=True)
    quiet = step[0] == "build_advisor.py"  # its console dump is for debugging
    subprocess.run([sys.executable, str(HERE / step[0]), *step[1:]], check=True,
                   stdout=subprocess.DEVNULL if quiet else None)
subprocess.run(["node", str(HERE / "check_pages.js")], check=False)
