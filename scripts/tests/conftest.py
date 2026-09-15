"""路径引导：把 scripts/ 加入 sys.path，任意 cwd 下可跑（import export_precise / fakes）。"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
