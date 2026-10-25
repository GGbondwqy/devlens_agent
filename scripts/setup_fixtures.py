"""初始化靶场仓库（fixtures/repo）为 git 仓库。

流程：
    1. main 分支提交基础版（coupon_service 为安全版本）；
    2. 创建并切换到 pr-42 分支，提交待评审的风险变更
       （except: pass 吞异常 + 超时 30s→3s），保持该分支为检出状态；
    3. main 分支上保留 discount.py 的单位混用缺陷（ISSUE-101 / CI-JOB-88 的根因）。

幂等：已初始化（存在 .git）时跳过，--force 可重置重建。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO = PROJECT_ROOT / "fixtures" / "repo"
COUPON_FILE = REPO / "app" / "services" / "coupon_service.py"

# main 分支上的基础版 coupon_service（PR-42 变更前的样子）
BASE_COUPON = '''"""优惠券服务：券的查询与校验。"""

import httpx

from app.utils.discount import Coupon
from app.utils.logger import get_logger

logger = get_logger(__name__)

TIMEOUT = 30
COUPON_API = "https://coupon.internal.example/api/v1"


def get_coupon(code: str) -> Coupon:
    """查询优惠券；查询失败时抛出异常，由调用方处理。"""
    resp = httpx.get(f"{COUPON_API}/{code}", timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return Coupon(code=code, kind=data["kind"], value=float(data["value"]))
'''

GIT = shutil.which("git")


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [GIT, *args]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr}")
    return proc


def ensure_fixture_repo(force: bool = False) -> bool:
    if not GIT:
        print("[setup] 未找到 git 命令，跳过靶场仓库初始化")
        return False
    if (REPO / ".git").exists():
        if not force:
            print(f"[setup] 靶场仓库已初始化，跳过（{REPO}）")
            return True
        shutil.rmtree(REPO / ".git")

    pr_content = COUPON_FILE.read_text(encoding="utf-8")
    commit_env_note = 'git -c user.name=DevLens -c user.email=devlens@example.com'

    _git("init", "-b", "main")
    COUPON_FILE.write_text(BASE_COUPON, encoding="utf-8")
    _git("add", "-A")
    _git("-c", "user.name=DevLens", "-c", "user.email=devlens@example.com", "commit", "-m", "init: order-service 基础版（main）")

    _git("checkout", "-b", "pr-42")
    COUPON_FILE.write_text(pr_content, encoding="utf-8")
    _git("add", "-A")
    _git("-c", "user.name=dev-zhang", "-c", "user.email=zhang@example.com", "commit", "-m", "fix(coupon): 异常兜底 + 超时调整（PR-42）")

    print(f"[setup] 靶场仓库就绪：main（基础版） + pr-42（待评审变更，当前检出）")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="删除已有 .git 并重建")
    args = parser.parse_args()
    ok = ensure_fixture_repo(force=args.force)
    sys.exit(0 if ok else 1)
