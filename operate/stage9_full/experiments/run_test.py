"""正式 Test 入口（占位，遵循开封纪律）。"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段9 正式 Test（封存纪律）")
    parser.add_argument("--confirm-test", action="store_true")
    parser.add_argument("--test-manifest", default=None)
    args = parser.parse_args(argv)

    if not args.confirm_test or not args.test_manifest:
        print(
            "正式 Test 未启动：需要 --test-manifest <封存Test清单> 且 --confirm-test。\n"
            "说明：Test 数据封存于受限目录，不在 Git 仓库。正式 Test 必须：\n"
            "  1) 使用冻结的 Test 样本与 Ground Truth；\n"
            "  2) 运行前登记实验版本、代码 commit、配置与数据 SHA-256；\n"
            "  3) 只运行一次；禁止根据 Test 结果回改模型或评价标准。\n"
            "满足条件后在服务器执行正式 Test。"
        )
        return 2

    print("正式 Test 流程待接入封存 Test 数据后启用（此处不做伪造）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
