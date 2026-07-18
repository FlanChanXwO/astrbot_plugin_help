"""旧自定义分组迁移 CLI 的行为测试。"""

import json
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "migrate_custom_groups.py"
)


def test_cli_dry_run_emits_json_and_human_report_without_side_effects(tmp_path):
    """CLI dry-run 的 stdout 可机器解析，stderr 可供人阅读，且完全不写磁盘。"""
    source_path = tmp_path / "custom_groups.json"
    database_path = tmp_path / "command_catalog.db"
    source_path.write_text('[{"group_name":"常用"}]', encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--database",
            str(database_path),
            "--source",
            str(source_path),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["status"] == "validated"
    assert "验证通过" in completed.stderr
    assert not database_path.exists()
    assert list(tmp_path.glob("custom_groups.json.backup.*")) == []
    assert not (tmp_path / "data").exists()


def test_cli_import_reuses_migration_service_and_reports_backup(tmp_path):
    """正式 CLI 迁移创建数据库与备份，并输出可核对的双格式报告。"""
    source_path = tmp_path / "custom_groups.json"
    database_path = tmp_path / "command_catalog.db"
    source_path.write_text(
        '[{"group_name":"常用","commands":[{"command":"帮助"}]}]',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--database",
            str(database_path),
            "--source",
            str(source_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert payload["status"] == "imported"
    assert payload["group_count"] == 1
    assert payload["command_count"] == 1
    assert Path(payload["backup_path"]).read_bytes() == source_path.read_bytes()
    assert database_path.is_file()
    assert "迁移完成" in completed.stderr
    assert not (tmp_path / "data").exists()


def test_cli_database_failure_is_reported_as_json_without_traceback(tmp_path):
    """SQLite 打开失败也必须保持 stdout JSON、stderr 摘要的 CLI 契约。"""
    source_path = tmp_path / "custom_groups.json"
    invalid_database_path = tmp_path / "database_is_a_directory"
    source_path.write_text('[{"group_name":"常用"}]', encoding="utf-8")
    invalid_database_path.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--database",
            str(invalid_database_path),
            "--source",
            str(source_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert payload["status"] == "error"
    assert payload["error_type"] == "OperationalError"
    assert payload["database_path"] == str(invalid_database_path)
    assert payload["error"]
    assert "迁移失败" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_two_cli_processes_serialize_same_content_import(tmp_path):
    """两个独立进程并发首次初始化时，仍只导入并备份一次。"""
    database_path = tmp_path / "command_catalog.db"
    sources = [tmp_path / "first.json", tmp_path / "second.json"]
    payload = '[{"group_name":"concurrent-process"}]'
    for source in sources:
        source.write_text(payload, encoding="utf-8")

    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--database",
                str(database_path),
                "--source",
                str(source),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
        )
        for source in sources
    ]
    completed = [process.communicate() for process in processes]

    assert [process.returncode for process in processes] == [0, 0]
    reports = [json.loads(stdout) for stdout, _stderr in completed]
    assert sorted(report["status"] for report in reports) == [
        "already_migrated",
        "imported",
    ]
    assert len(list(tmp_path.glob("*.backup.*"))) == 1
    assert all("Traceback" not in stderr for _stdout, stderr in completed)
