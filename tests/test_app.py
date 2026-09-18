import importlib.util
import inspect
import os
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

    def test_panel_has_owned_character_services(self):
        profile = {"account_id": 32, "username": "player_01",
                   "created_at": "now", "last_game_login": "-"}
        characters = [{"role_id": 33, "name": "Hero", "level": 10,
                       "class_name": "Wizard", "gender": "Pria", "faction": "-"}]
        orders = [{"id": "7", "role_name": "Hero", "amount": "1000000",
                   "reference": "PAY-7", "status": "pending", "created_at": "now"}]
        body, _ = app.render_panel(profile, characters, coin_orders=orders)
        self.assertIn('action="/character/unstuck"', body)
        self.assertIn('value="33"', body)
        self.assertIn('action="/coin/order"', body)
        self.assertIn("PAY-7", body)
        self.assertNotIn("{{", body)

    def test_coin_order_rejects_unowned_role(self):
        with patch.object(app, "owned_character", return_value=None), self.assertRaises(ValueError):
            app.create_coin_order(32, 99, 1_000_000, "PAY-1", "127.0.0.1")

    def test_unstuck_requires_offline_account(self):
        with patch.object(app, "owned_character", return_value={"role_id": 33, "name": "Hero"}), \
                patch.object(app, "account_is_online", return_value=True), self.assertRaises(ValueError):
            app.unstuck_character(32, "player_01", 33, "127.0.0.1")

    def test_admin_template_lists_coin_order(self):
        orders = [{"id": "7", "username": "player_01", "role_id": "33",
                   "role_name": "Hero", "amount": "5000000", "reference": "PAY-7",
                   "created_at": "now"}]
        body, _ = app.render_admin((1024, "admin"), [], (0, 0, 0), [],
                                   {"services": [], "events": []}, [], coin_orders=orders)
        self.assertIn("Coin Purchase Orders", body)
        self.assertIn("PAY-7", body)
        self.assertIn('action="/admin/coin/action"', body)

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
        self.assertIn("Service Monitor", body)
        self.assertIn("Web Portal", body)
        self.assertIn("1/1", body)
        self.assertNotIn("Login Gateway", body)
        self.assertNotIn("Export ke Host", body)
        self.assertEqual(1, body.count("Celestial Vale"))
        self.assertNotIn("menjadi offline", body)
        self.assertNotIn("Buka editor data", body)
        self.assertNotIn("/admin/data", body)
        self.assertIn("kembali online", body)
        self.assertIn("News &amp; Announcement", body)
        self.assertIn("Realm stabil", body)
        self.assertIn("Kirim Gold", body)
        self.assertIn('href="/admin/patch"', body)
        self.assertIn("Backup Database", body)
        self.assertIn('action="/admin/backups/create"', body)
        self.assertIn("Broadcast &amp; Safe Shutdown", body)
        self.assertIn('action="/admin/game/action"', body)
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

    def test_game_control_queue_and_scheduled_status_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "requests").mkdir()
            (root / "status.json").write_text(
                '{"busy":false,"updated_at":"now","scheduled":null}', encoding="utf-8")
            with patch.object(app, "GAME_CONTROL_DIR", root):
                request = app.queue_game_action(
                    (1024, "admin"), "schedule-shutdown", "127.0.0.1",
                    seconds=300, reason="Maintenance",
                )
                self.assertEqual(300, request["seconds"])
                self.assertEqual(1, len(list((root / "requests").glob("*.json"))))
                with self.assertRaises(ValueError):
                    app.queue_game_action(
                        (1024, "admin"), "schedule-shutdown", "127.0.0.1",
                        seconds=5, reason="Too short",
                    )

    def test_anonymous_system_broadcast_is_queued(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "requests").mkdir()
            (root / "status.json").write_text(
                '{"busy":false,"scheduled":null}', encoding="utf-8")
            with patch.object(app, "GAME_CONTROL_DIR", root):
                request = app.queue_game_action(
                    (1024, "admin"), "broadcast", "127.0.0.1",
                    message="Event starts soon",
                )
            self.assertEqual("Event starts soon", request["message"])
            self.assertNotIn("gm_role_id", request)

    def test_rate_and_material_allowed_but_equipment_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "requests").mkdir()
            (root / "status.json").write_text(
                '{"busy":false,"scheduled":null,"rates":{"exp":1,"gold":1}}',
                encoding="utf-8")
            with patch.object(app, "GAME_CONTROL_DIR", root):
                rates = app.queue_game_action(
                    (1024, "admin"), "set-rates", "127.0.0.1",
                    exp_multiplier=5, gold_multiplier=2,
                )
                self.assertEqual(5, rates["exp_multiplier"])
                list((root / "requests").glob("*.json"))[0].unlink()
                item = app.queue_game_action(
                    (1024, "admin"), "send-item", "127.0.0.1",
                    role_id=33, item_id=21652, count=1,
                )
                self.assertEqual(21652, item["item_id"])
                list((root / "requests").glob("*.json"))[0].unlink()
                with self.assertRaisesRegex(ValueError, "bukan material"):
                    app.queue_game_action(
                        (1024, "admin"), "send-item", "127.0.0.1",
                        role_id=33, item_id=11212, count=1,
                    )
                self.assertEqual([], list((root / "requests").glob("*.json")))

    def test_rate_and_item_forms_render_character_choices(self):
        game_control = {
            "busy": False, "updated_at": "now", "error": None,
            "scheduled": None, "last_action": None,
            "rates": {"exp": 5, "gold": 2},
        }
        body, _ = app.render_admin(
            (1024, "admin"), [], (0, 0, 0), [], {"services": [], "events": []}, [],
            game_control=game_control,
            characters=[{"role_id": 33, "name": "Hero", "username": "player01"}],
        )
        self.assertIn('action="/admin/game/rates"', body)
        self.assertIn("MATERIAL_ESSENCE, proc_type 0", body)
        self.assertIn('action="/admin/game/item"', body)
        self.assertIn("Hero · player01 · #33", body)
        self.assertIn('value="5" selected', body)
        self.assertNotIn("{{", body)

    def test_rate_and_item_post_routes_are_allowlisted(self):
        source = APP_PATH.read_text(encoding="utf-8")
        post_allowlist = source[
            source.index("def do_POST(self):"):source.index("fields = self.read_form()")
        ]
        self.assertIn('"/admin/game/rates"', post_allowlist)
        self.assertIn('"/admin/game/item"', post_allowlist)

    def test_game_control_template_renders_countdown_and_cancel(self):
        game_control = {
            "busy": False, "updated_at": "now", "error": None,
            "scheduled": {"execute_at": 2000000000, "reason": "Maintenance",
                          "actor": "admin"},
            "last_action": {"actor": "admin", "action": "schedule-shutdown",
                            "status": "scheduled", "message": "300 seconds"},
        }
        body, _ = app.render_admin(
            (1024, "admin"), [], (0, 0, 0), [], {"services": [], "events": []}, [],
            game_control=game_control,
        )
        self.assertIn('data-shutdown-at="2000000000"', body)
        self.assertIn("Maintenance", body)
        self.assertIn("Batalkan Shutdown", body)
        self.assertNotIn('value="cancel-shutdown"><label class="check-label"><input type="checkbox" name="confirm" value="yes" required disabled', body)
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

    def test_game_data_editor_routes_and_templates_are_removed(self):
        self.assertNotIn('/admin/data', inspect.getsource(app.PWHandler.do_GET))
        self.assertNotIn('/admin/data', inspect.getsource(app.PWHandler.do_POST))
        for name in (
                "data_editor.html", "boutique_editor.html", "npc_editor.html",
                "npc_spawn_editor.html", "equipment_editor.html",
                "equipment_item_editor.html", "npc_service_catalog.html",
                "npc_service_detail.html", "npc_recipe_editor.html"):
            self.assertFalse((APP_PATH.parent / name).exists())

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

    def test_launcher_news_uses_legacy_compatible_markup(self):
        body = app.render_launcher_news([{
            "id": "1", "title": "Server Update", "body": "New event is available.",
            "date": "2026-09-13", "author": "admin",
        }])
        self.assertIn("Server Update", body)
        self.assertIn("font-family: Arial", body)
        self.assertNotIn("{{", body)
        self.assertNotIn("<script", body.lower())
        self.assertNotIn("stylesheet", body.lower())

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
