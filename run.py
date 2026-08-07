"""NovelForge 启动入口"""

import sys
from pathlib import Path

# 确保src在路径中
sys.path.insert(0, str(Path(__file__).parent))

from src.cli.main import main

if __name__ == "__main__":
    main()
