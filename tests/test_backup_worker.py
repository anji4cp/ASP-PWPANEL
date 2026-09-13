import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


WORKER_PATH = Path(__file__).parents[1] / "backup_control_worker.py"
SPEC = importlib.util.spec_from_file_location("pw155_backup_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class BackupWorkerTests(unittest.TestCase):
    def test_validated_backup_is_packaged_and_listed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_root = root / "database"
            source = source_root / "20260913T060000Z"
            source.mkdir(parents=True)
            for name in ("pw.sql.gz", "pw_portal.sql.gz", "metadata.txt", "SHA256SUMS"):
                (source / name).write_bytes(name.encode("ascii"))
            control = root / "control"
            files = control / "files"
            files.mkdir(parents=True)
            with patch.object(worker, "BACKUP_ROOT", source_root), \
                    patch.object(worker, "CONTROL_DIR", control), \
                    patch.object(worker, "REQUEST_DIR", control / "requests"), \
                    patch.object(worker, "FILE_DIR", files):
                self.assertEqual(source, worker.validate_backup_directory(source))
                archive = worker.create_download_archive(source)
                self.assertTrue(archive.is_file())
                self.assertEqual(archive.name, worker.backup_files()[0]["filename"])

    def test_backup_directory_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "expected"
            outside = root / "outside" / "20260913T060000Z"
            outside.mkdir(parents=True)
            with patch.object(worker, "BACKUP_ROOT", expected):
                with self.assertRaises(RuntimeError):
                    worker.validate_backup_directory(outside)

    def test_worker_rejects_non_create_request(self):
        with tempfile.TemporaryDirectory() as folder:
            request = Path(folder) / "bad.json"
            request.write_text('{"action":"delete"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                worker.process_request(request)


if __name__ == "__main__":
    unittest.main()
