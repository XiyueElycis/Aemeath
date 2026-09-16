"""备份与回滚测试。"""

from gamedoctor.fixer.backup.manager import BackupManager


def test_backup_and_rollback(tmp_path):
    """验证文件被修改后能依据备份记录回滚到原始内容。"""
    src = tmp_path / "target" / "config.ini"
    src.parent.mkdir()
    src.write_text("original", encoding="utf-8")

    manager = BackupManager(backup_root=tmp_path / "backups")
    record = manager.backup("diag-1", "a1", [src])

    # 修改文件后回滚
    src.write_text("changed", encoding="utf-8")
    manager.rollback(record)

    # 回滚后内容应恢复为备份时的原始值
    assert src.read_text(encoding="utf-8") == "original"


def test_list_records(tmp_path):
    """验证按诊断 ID 能列出备份记录，且记录关联正确的动作 ID。"""
    src = tmp_path / "f.txt"
    src.write_text("x", encoding="utf-8")
    manager = BackupManager(backup_root=tmp_path / "backups")
    manager.backup("diag-2", "a1", [src])

    records = manager.list_records("diag-2")
    # 应恰好一条记录
    assert len(records) == 1
    # 记录的动作 ID 应与备份时传入的一致
    assert records[0].action_id == "a1"
