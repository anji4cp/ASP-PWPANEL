import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("PW155_WEB_CSRF_SECRET", "t" * 32)
APP_PATH = Path(__file__).parents[1] / "app.py"
sys.path.insert(0, str(APP_PATH.parent))
SPEC = importlib.util.spec_from_file_location("pw155_web", APP_PATH)
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class ValidationTests(unittest.TestCase):
    def test_valid_registration(self):
        self.assertIsNone(app.validate_registration("player_01", "secret-123", "secret-123"))

    def test_rejects_unsafe_username(self):
        self.assertIsNotNone(app.validate_registration("x'; DROP", "secret123", "secret123"))

    def test_rejects_mismatched_password(self):
        self.assertIsNotNone(app.validate_registration("player", "secret123", "different"))

    def test_csrf_signature(self):
        token = app.new_csrf_token()
        self.assertTrue(app.valid_csrf_token(token))
        self.assertFalse(app.valid_csrf_token(token + "x"))

    def test_modern_password_hash(self):
        encoded = app.hash_web_password("secret-123")
        self.assertTrue(app.verify_web_password("secret-123", encoded))
        self.assertFalse(app.verify_web_password("wrong-pass", encoded))

    def test_session_signature(self):
        session = app.make_session(32, "player_01")
        self.assertEqual(app.parse_session(session), (32, "player_01"))
        self.assertIsNone(app.parse_session(session + "x"))

    def test_panel_template(self):
        profile = {
            "account_id": 32, "username": "player_01",
            "created_at": "2026-08-31 12:00", "last_game_login": "-",
        }
        body, token = app.render_panel(profile, [])
        self.assertIn("Halo, <em>player_01</em>", body)
        self.assertIn("Cache karakter belum tersedia", body)
        self.assertNotIn("{{", body)
        self.assertTrue(app.valid_csrf_token(token))

    def test_admin_authorization_uses_explicit_allowlist(self):
        with patch.object(app, "run_db", return_value=["1"]) as query:
            self.assertTrue(app.is_panel_admin(1024))
            self.assertIn("account_id=1024", query.call_args.args[0])
        with patch.object(app, "run_db", return_value=["0"]):
            self.assertFalse(app.is_panel_admin(1040))

    def test_admin_template(self):
        accounts = [{
            "account_id": 1040, "username": "tuyul1",
            "created_at": "2026-08-31 12:00", "character_count": 1,
            "max_level": 4, "gm_permissions": 0,
        }]
        audit = [{
            "created_at": "2026-08-31 12:00:00", "actor": "admin",
            "action": "gm.grant", "target": "tuyul1",
            "details": "memberikan hak GM zone 1 kepada",
        }]
        monitor = {
            "checked_at": "2026-08-31T12:00:00Z", "online": 2, "total": 3,
            "services": [
                {"name": "glinkd", "label": "Login Gateway", "kind": "daemon",
                 "running": True, "detail": "aktif"},
                {"name": "is61", "label": "Celestial Vale", "kind": "daemon",
                 "running": False, "detail": "proses tidak aktif"},
                {"name": "pw155-web.service", "label": "Web Portal", "kind": "systemd",
                 "running": True, "detail": "active"},
                {"name": "pw155-host-export.timer", "label": "Export ke Host", "kind": "systemd",
                 "running": False, "detail": "inactive"},
            ],
            "events": [
                {"timestamp": "2026-08-31T12:00:00Z", "service": "is61",
                 "label": "Celestial Vale", "status": "offline",
                 "detail": "proses tidak aktif"},
                {"timestamp": "2026-08-31T12:00:00Z", "service": "pw155-web.service",
                 "label": "Web Portal", "status": "online", "detail": "active"},
            ],
        }
        news = [{"id": 1, "title": "Realm stabil", "status": "published",
                 "updated_at": "2026-08-31 12:00", "published_at": "2026-08-31 12:00"}]
        body, token = app.render_admin((1024, "admin"), accounts, (2, 2, 1), audit,
                                       monitor, news)
        self.assertIn("Jadikan GM", body)
        self.assertIn("tuyul1", body)
        self.assertIn("Monitoring layanan pendukung", body)
        self.assertIn("Web Portal", body)
        self.assertIn("1/1", body)
        self.assertNotIn("Login Gateway", body)
        self.assertNotIn("Export ke Host", body)
        self.assertEqual(1, body.count("Celestial Vale"))
        self.assertNotIn("menjadi offline", body)
        self.assertNotIn("Buka editor data", body)
        self.assertNotIn("/admin/data", body)
        self.assertIn("kembali online", body)
        self.assertIn("Editor berita", body)
        self.assertIn("Realm stabil", body)
        self.assertIn("Kirim Gold Boutique", body)
        self.assertIn('href="/admin/patch"', body)
        self.assertIn("Backup &amp; download database", body)
        self.assertIn('action="/admin/backups/create"', body)
        self.assertNotIn("{{", body)
        self.assertTrue(app.valid_csrf_token(token))

    def test_backup_control_queue_and_download_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "requests").mkdir()
            (root / "files").mkdir()
            filename = "PW155-database-20260913T060000Z.tar.gz"
            archive = root / "files" / filename
            archive.write_bytes(b"backup")
            (root / "status.json").write_text(
                '{"busy":false,"updated_at":"now","backups":['
                '{"filename":"PW155-database-20260913T060000Z.tar.gz",'
                '"created_at":"20260913T060000Z","bytes":6},'
                '{"filename":"../app.py","created_at":"bad","bytes":1}]}',
                encoding="utf-8",
            )
            with patch.object(app, "BACKUP_CONTROL_DIR", root):
                state = app.backup_control_state()
                self.assertEqual([filename], [item["filename"] for item in state["backups"]])
                self.assertEqual(archive, app.resolve_backup_download(filename))
                self.assertIsNone(app.resolve_backup_download("../app.py"))
                request = app.queue_backup_action((1024, "admin"), "create", "127.0.0.1")
                self.assertEqual("create", request["action"])
                self.assertEqual(1, len(list((root / "requests").glob("*.json"))))
                with self.assertRaises(ValueError):
                    app.queue_backup_action((1024, "admin"), "delete", "127.0.0.1")

    def test_backup_template_lists_downloadable_archive(self):
        backups = {
            "busy": False, "updated_at": "now", "error": None,
            "last_action": {"actor": "admin", "status": "completed",
                            "message": "Backup siap"},
            "backups": [{
                "filename": "PW155-database-20260913T060000Z.tar.gz",
                "created_at": "20260913T060000Z", "bytes": 1048576,
            }],
        }
        body, _ = app.render_admin((1024, "admin"), [], (0, 0, 0), [],
                                   {"services": [], "events": []}, [], backups=backups)
        self.assertIn("PW155-database-20260913T060000Z.tar.gz", body)
        self.assertIn("1.05 MB", body)
        self.assertIn("Download", body)
        self.assertNotIn("{{", body)

    def test_map_control_queue_uses_catalog_allowlist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "catalog.json").write_text(
                '{"max_selection":6,"maps":[{"alias":"gs01","label":"World Utama"},'
                '{"alias":"is61","label":"Celestial Vale"}]}', encoding="utf-8")
            (root / "status.json").write_text(
                '{"checked_at":"now","busy":false,"active":[],"maps":{}}',
                encoding="utf-8")
            with patch.object(app, "MAP_CONTROL_DIR", root):
                request = app.queue_map_action((1024, "admin"), "start",
                                               ["gs01", "is61"], "127.0.0.1")
                queued = list((root / "requests").glob("*.json"))
                self.assertEqual(["gs01", "is61"], request["maps"])
                self.assertEqual(1, len(queued))
                with self.assertRaises(ValueError):
                    app.queue_map_action((1024, "admin"), "start",
                                         ["is99"], "127.0.0.1")

    def test_map_control_rejects_request_while_worker_busy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "catalog.json").write_text(
                '{"max_selection":6,"maps":[{"alias":"gs01"}]}', encoding="utf-8")
            (root / "status.json").write_text(
                '{"checked_at":"now","busy":true,"active":[],"maps":{}}',
                encoding="utf-8")
            with patch.object(app, "MAP_CONTROL_DIR", root), self.assertRaises(ValueError):
                app.queue_map_action((1024, "admin"), "start", ["gs01"], "127.0.0.1")

    def test_core_daemon_action_accepts_no_map_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "catalog.json").write_text(
                '{"max_selection":6,"maps":[{"alias":"gs01"}]}', encoding="utf-8")
            (root / "status.json").write_text(
                '{"checked_at":"now","busy":false,"active":[],"maps":{},'
                '"core":{},"core_active":[],"core_online":false,"core_total":8}',
                encoding="utf-8")
            with patch.object(app, "MAP_CONTROL_DIR", root):
                request = app.queue_map_action((1024, "admin"), "stop-core", [],
                                               "127.0.0.1")
                self.assertEqual("stop-core", request["action"])
                self.assertEqual([], request["maps"])

    def test_core_daemon_action_rejects_map_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "catalog.json").write_text(
                '{"max_selection":6,"maps":[{"alias":"gs01"}]}', encoding="utf-8")
            (root / "status.json").write_text(
                '{"checked_at":"now","busy":false,"active":[],"maps":{}}',
                encoding="utf-8")
            with patch.object(app, "MAP_CONTROL_DIR", root), self.assertRaises(ValueError):
                app.queue_map_action((1024, "admin"), "start-core", ["gs01"],
                                     "127.0.0.1")

    def test_data_editor_template(self):
        catalog = {
            "total": 2004,
            "category_count": 8,
            "rows": [{
                "index": 3, "place": 3, "item_id": 15038,
                "name": "Dragon Orb (1 Star)", "amount": 1,
                "category": "Utility", "subcategory": "Refining",
                "price_cash": 100, "sale_count": 1,
            }],
        }
        body, token = app.render_data_editor((1024, "admin"), catalog, "dragon")
        self.assertIn("Game Data Workshop", body)
        self.assertIn("Dragon Orb (1 Star)", body)
        self.assertIn("2004", body)
        self.assertNotIn("{{", body)
        self.assertTrue(app.valid_csrf_token(token))

    def test_boutique_draft_edit_and_clone_preserve_active_files(self):
        project = APP_PATH.parents[1]
        client_source = project / "vendor/Perfect_World_Server_1.5.5/gamed/config/gshop.data"
        server_source = project / "vendor/Perfect_World_Server_1.5.5/gamed/config/gshopsev.data"
        if not client_source.is_file() or not server_source.is_file():
            self.skipTest("optional proprietary PW gshop fixtures are not installed")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client_dir = root / "client"
            editor_dir = root / "editor"
            client_dir.mkdir()
            shutil.copy2(client_source, client_dir / "gshop.data")
            shutil.copy2(server_source, client_dir / "gshopsev.data")
            active_client = (client_dir / "gshop.data").read_bytes()
            active_server = (client_dir / "gshopsev.data").read_bytes()
            fields = {
                "item_id": ["42203"], "amount": ["2"],
                "category": ["0"], "subcategory": ["0"],
                "price_cash": ["123"], "name": ["Test Boutique Item"],
                "description": ["Draft test"], "icon": ["Surfaces\\test.dds"],
            }
            with patch.object(app, "CLIENT_DATA_DIR", client_dir), \
                    patch.object(app, "DATA_EDITOR_DIR", editor_dir):
                edited = app.build_boutique_draft((1024, "admin"), 0, fields)
                self.assertEqual("edit", edited["operation"])
                cloned = app.build_boutique_draft((1024, "admin"), 0, fields, clone=True)
                self.assertEqual("clone", cloned["operation"])
                self.assertEqual(2005, cloned["record_count"])
            self.assertEqual(active_client, (client_dir / "gshop.data").read_bytes())
            self.assertEqual(active_server, (client_dir / "gshopsev.data").read_bytes())

    def test_npc_draft_edit_and_clone_preserve_active_file(self):
        project = APP_PATH.parents[1]
        source = project / "runtime/fortune-treasure-v3/a61/npcgen.data"
        if not source.is_file():
            self.skipTest("optional proprietary PW npcgen fixture is not installed")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            active = root / "npcgen.data"
            editor_dir = root / "editor"
            shutil.copy2(source, active)
            original = active.read_bytes()
            fields = {
                "npc_id": ["48055"], "amount": ["2"], "respawn": ["30"],
                "trigger_id": ["0"], "x": ["100.5"], "y": ["20"], "z": ["300.25"],
            }
            with patch.object(app, "NPCGEN_PATH", active), \
                    patch.object(app, "DATA_EDITOR_DIR", editor_dir):
                manifest = app.build_npc_draft((1024, "admin"), 295, 0, fields)
                self.assertEqual("edit", manifest["operation"])
                cloned = app.build_npc_draft((1024, "admin"), 295, 0, fields, clone=True)
                self.assertEqual("clone", cloned["operation"])
                self.assertEqual(manifest["group_count"] + 1, cloned["group_count"])
            self.assertEqual(original, active.read_bytes())

    def test_equipment_draft_edit_preserves_active_file(self):
        project = APP_PATH.parents[1]
        source = project / "runtime/fortune-treasure-v2/elements.data"
        config = project / "tools/1.5.x/sELedit++/configs/PW_1.5.5_v156.cfg"
        if not source.is_file() or not config.is_file():
            self.skipTest("optional proprietary PW elements fixtures are not installed")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            active = root / "elements.data"
            editor_dir = root / "editor"
            shutil.copy2(source, active)
            original_hash = app._sha256_file(active)
            with patch.object(app, "ELEMENTS_PATH", active), \
                    patch.object(app, "ELEMENTS_CONFIG_PATH", config), \
                    patch.object(app, "DATA_EDITOR_DIR", editor_dir):
                _, item_list, row = app.equipment_item("weapon", 0)
                fields = {field: [app._equipment_value(item_list, row, field)]
                          for field in app.EQUIPMENT_COMMON_FIELDS}
                for field, _ in app.EQUIPMENT_TYPES["weapon"]["stats"]:
                    fields[field] = [app._equipment_value(item_list, row, field)]
                fields["Name"] = ["Test Weapon Draft"]
                manifest = app.build_equipment_draft((1024, "admin"), "weapon", 0, fields)
                self.assertEqual("edit", manifest["operation"])
                self.assertTrue((editor_dir / "elements/draft/elements.data").is_file())
            self.assertEqual(original_hash, app._sha256_file(active))

    def test_merchant_service_draft_chains_without_touching_active(self):
        project = APP_PATH.parents[1]
        source = project / "runtime/fortune-treasure-v2/elements.data"
        config = project / "tools/1.5.x/sELedit++/configs/PW_1.5.5_v156.cfg"
        if not source.is_file() or not config.is_file():
            self.skipTest("optional proprietary PW elements fixtures are not installed")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            active = root / "elements.data"
            editor_dir = root / "editor"
            shutil.copy2(source, active)
            original_hash = app._sha256_file(active)
            with patch.object(app, "ELEMENTS_PATH", active), \
                    patch.object(app, "ELEMENTS_CONFIG_PATH", config), \
                    patch.object(app, "DATA_EDITOR_DIR", editor_dir):
                detail = app.npc_service_detail(2165)
                first = detail["merchant"][0]
                app.build_merchant_service_draft((1024, "admin"), 2165,
                                                 first["page"], first["slot"],
                                                 first["id"], first["price"] + 1)
                changed = app.npc_service_detail(2165)["merchant"][0]
                self.assertEqual(first["price"] + 1, changed["price"])
                self.assertTrue(app.service_draft_manifest())
            self.assertEqual(original_hash, app._sha256_file(active))

    def test_admin_search_rejects_sql_metacharacters(self):
        with patch.object(app, "run_db") as query:
            self.assertEqual(app.admin_accounts("x%' OR 1=1"), [])
            query.assert_not_called()

    def test_ranking_template_escapes_character_data(self):
        ranking = [{
            "position": 1, "name": "<Hero>", "level": 105,
            "class_name": "Wizard", "faction": "A&B",
        }]
        body = app.render_ranking(ranking)
        self.assertIn("&lt;Hero&gt;", body)
        self.assertIn("A&amp;B", body)
        self.assertNotIn("<Hero>", body)
        self.assertNotIn("{{", body)

    def test_ranking_reads_only_character_cache(self):
        with patch.object(app, "run_db", return_value=["Hero\t105\t1\tFaction"]) as query:
            ranking = app.ranking_characters()
        self.assertEqual(ranking[0]["class_name"], "Wizard")
        self.assertIn("FROM pw.roles", query.call_args.args[0])
        self.assertNotIn("gamedbd", query.call_args.args[0])

    def test_download_page_escapes_manifest_data(self):
        manifest = {"downloads": [{
            "title": "<Unsafe>", "version": "1.0", "filename": None,
            "bytes": 1500000, "sha256": "A&B", "available": False,
            "description": "Test <script>",
        }]}
        body = app.render_downloads(manifest)
        self.assertIn("&lt;Unsafe&gt;", body)
        self.assertIn("1.50 MB", body)
        self.assertIn("A&amp;B", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("{{", body)

    def test_download_resolution_uses_manifest_allowlist(self):
        manifest = {"downloads": [{
            "filename": "PW155-ID-Connection-Pack-20260831.zip",
            "available": True,
        }]}
        allowed = app.resolve_download("PW155-ID-Connection-Pack-20260831.zip", manifest)
        self.assertEqual(allowed, app.DOWNLOAD_DIR / "PW155-ID-Connection-Pack-20260831.zip")
        self.assertIsNone(app.resolve_download("../app.py", manifest))
        self.assertIsNone(app.resolve_download("unlisted.zip", manifest))

    def test_available_download_matches_manifest_integrity(self):
        manifest = app.load_download_manifest()
        package = next(item for item in manifest["downloads"]
                       if item["id"] == "connection-pack")
        source = app.resolve_download(package["filename"], manifest)
        self.assertIsNotNone(source)
        self.assertEqual(source.stat().st_size, package["bytes"])
        digest = app.hashlib.sha256(source.read_bytes()).hexdigest().upper()
        self.assertEqual(digest, package["sha256"])

    def test_cpw_patch_resolution_allows_protocol_files_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "info").mkdir()
            (root / "element" / "element" / "ZGF0YQ==").mkdir(parents=True)
            (root / "info" / "pid").write_text("101", encoding="ascii")
            packed = root / "element" / "element" / "ZGF0YQ==" / "dGFza3MuZGF0YQ=="
            packed.write_bytes(b"packed")
            with patch.object(app, "PATCH_DIR", root):
                self.assertEqual(app.resolve_patch_file("info/pid"), root / "info" / "pid")
                self.assertEqual(
                    app.resolve_patch_file("element/element/ZGF0YQ==/dGFza3MuZGF0YQ=="), packed)
                self.assertIsNone(app.resolve_patch_file("../app.py"))
                self.assertIsNone(app.resolve_patch_file("element/unknown.txt"))
                self.assertIsNone(app.resolve_patch_file("element\\version"))

    def test_cpw_control_queue_is_allowlisted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "status.json").write_text('{"busy":false}', encoding="utf-8")
            (root / "snapshot.json").write_text(
                '{"releases":["r-2","r-1"],"current":"r-2","counts":{},"total":0}',
                encoding="utf-8")
            with patch.object(app, "CPW_CONTROL_DIR", root):
                request = app.queue_cpw_action((1024, "admin"), "rollback", "r-1", "127.0.0.1")
                self.assertEqual(request["action"], "rollback")
                self.assertEqual(request["release"], "r-1")
                self.assertEqual(len(list((root / "requests").glob("*.json"))), 1)
                with self.assertRaises(ValueError):
                    app.queue_cpw_action((1024, "admin"), "shell", "", "127.0.0.1")

    def test_cpw_control_rejects_unsafe_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "status.json").write_text('{"busy":false}', encoding="utf-8")
            (root / "snapshot.json").write_text('{"releases":[]}', encoding="utf-8")
            with patch.object(app, "CPW_CONTROL_DIR", root):
                with self.assertRaises(ValueError):
                    app.queue_cpw_action((1024, "admin"), "rollback", "../escape", "127.0.0.1")

    def test_patch_manager_template_is_fully_rendered(self):
        state = {"busy": False, "updated_at": "now", "current": "r-2", "total": 3,
                 "counts": {"element": 2, "launcher": 1, "patcher": 0},
                 "verification": {"element": {"version": 2, "files": 4}},
                 "releases": ["r-2", "r-1"], "error": None}
        body, token = app.render_patch_manager((1024, "admin"), state)
        self.assertIn("ASP Patch Manager", body)
        self.assertIn("r-2 · CURRENT", body)
        self.assertIn('value="r-1"', body)
        self.assertNotIn("{{", body)
        self.assertTrue(app.valid_csrf_token(token))

    def test_public_templates_have_no_unresolved_placeholders(self):
        for name in ("guide.html",):
            body = (app.BASE_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("{{", body)

    def test_news_template_escapes_database_content(self):
        body = app.render_news([{
            "id": "1", "title": "<Realm>", "body": "Baris <script>\nKedua",
            "date": "2026-08-31", "author": "A&B",
        }])
        self.assertIn("&lt;Realm&gt;", body)
        self.assertIn("Baris &lt;script&gt;<br>Kedua", body)
        self.assertIn("31 Agustus 2026", body)
        self.assertIn("A&amp;B", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("{{", body)

    def test_news_status_rejects_invalid_value_before_database(self):
        with patch.object(app, "run_db") as query:
            with self.assertRaises(ValueError):
                app.set_news_status(1024, 1, "deleted", "127.0.0.1")
            query.assert_not_called()

    def test_boutique_gold_uses_stored_procedure(self):
        with patch.object(app, "run_db", return_value=["1040\ttuyul1\t25\t2500"]) as query:
            result = app.grant_boutique_gold(1024, "Tuyul1", 25, "127.0.0.1")
        self.assertEqual(result["gold"], 25)
        self.assertEqual(result["cash"], 2500)
        self.assertIn("CALL pw_portal.grant_boutique_gold", query.call_args.args[0])
        self.assertIn("'tuyul1'", query.call_args.args[0])

    def test_boutique_pending_parses_queue(self):
        with patch.object(app, "run_db", return_value=["tuyul1\t100\t1\t2026-08-31 21:00"]):
            queue = app.boutique_pending()
        self.assertEqual(queue[0]["username"], "tuyul1")
        self.assertEqual(queue[0]["gold"], "100")


if __name__ == "__main__":
    unittest.main()
