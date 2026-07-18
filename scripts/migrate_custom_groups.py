#!/usr/bin/env python3
"""将旧版 ``custom_groups.json`` 迁移到 v2 SQLite 目录。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


# 直接运行脚本时不能导入 ``src.infrastructure`` 聚合入口：该入口会初始化
# AstrBot 运行环境并可能在插件目录创建 data/。这里把 storage 作为独立包加载，
# 仍复用完全相同的生产迁移代码，但不触碰插件生命周期。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_PACKAGE = PROJECT_ROOT / "src" / "infrastructure" / "storage"


def _load_storage_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_helpinfo_storage",
        STORAGE_PACKAGE / "__init__.py",
        submodule_search_locations=[str(STORAGE_PACKAGE)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载迁移模块：{STORAGE_PACKAGE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_storage = _load_storage_module()
CommandCatalog = _storage.CommandCatalog


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="迁移旧版自定义命令组到 SQLite")
    parser.add_argument("--database", required=True, type=Path, help="目标数据库路径")
    parser.add_argument("--source", required=True, type=Path, help="旧 JSON 路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅严格验证并输出计划，不创建数据库或备份",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行迁移；stdout 固定为 JSON，面向人的摘要写入 stderr。"""
    args = _parse_args(argv)
    catalog = CommandCatalog(args.database)
    try:
        report = catalog.import_legacy_custom_groups(args.source, dry_run=args.dry_run)
    except Exception as error:
        # CLI 是进程边界：迁移校验、文件系统及 SQLite 错误都必须转成同一
        # 报告契约；这里不会伪装成功，错误类型和原始原因都会显式返回。
        payload = {
            "status": "error",
            "source_path": str(args.source),
            "database_path": str(args.database),
            "dry_run": args.dry_run,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        print(f"迁移失败：{error}", file=sys.stderr)
        return 2

    payload = report.to_dict()
    payload["database_path"] = str(args.database)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if report.dry_run:
        print(
            f"验证通过：{report.group_count} 个分组，"
            f"{report.command_count} 条命令；未写入数据库或备份。",
            file=sys.stderr,
        )
    elif report.status == "already_migrated":
        print("数据库已完成旧目录迁移，本次未读取或覆盖权威数据。", file=sys.stderr)
    else:
        print(
            f"迁移完成：{report.group_count} 个分组，"
            f"{report.command_count} 条命令；备份：{report.backup_path}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
