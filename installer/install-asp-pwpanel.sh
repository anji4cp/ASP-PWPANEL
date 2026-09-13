#!/usr/bin/env bash
set -euo pipefail

source_dir="${1:-/srv/pw155/staging/web}"
install_dir="/opt/pw155-web"
config_dir="/etc/pw155-web"
service_file="/etc/systemd/system/pw155-web.service"

if [[ "$EUID" -ne 0 ]]; then
  echo "Jalankan dengan sudo." >&2
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "Sistem operasi tidak didukung." >&2
  exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "Installer ini diuji untuk Ubuntu Server." >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 mariadb-server mariadb-client openssl ca-certificates curl

for required in app.py sync_characters.py gm_position.py role_operations.py monitor_services.py map_control_worker.py backup_control_worker.py game_control_worker.py pw155-backup-db.sh index.html login.html panel.html admin.html ranking.html news.html launcher-news.html guide.html downloads.html patch_manager.html static/style.css static/app.js; do
  if [[ ! -f "$source_dir/$required" ]]; then
    echo "File sumber tidak lengkap: $required" >&2
    exit 1
  fi
done

if ! id pwweb >/dev/null 2>&1; then
  useradd --system --home-dir "$install_dir" --shell /usr/sbin/nologin pwweb
fi
if ! id pwsync >/dev/null 2>&1; then
  useradd --system --home-dir "$install_dir" --shell /usr/sbin/nologin pwsync
fi
if ! getent group pwmonitor >/dev/null 2>&1; then
  groupadd --system pwmonitor
fi
if ! getent group pwmap >/dev/null 2>&1; then
  groupadd --system pwmap
fi
if ! getent group pwbackup >/dev/null 2>&1; then
  groupadd --system pwbackup
fi
if ! getent group pwgamectl >/dev/null 2>&1; then
  groupadd --system pwgamectl
fi
usermod -a -G pwmonitor,pwmap,pwbackup,pwgamectl pwweb

install -d -o root -g root -m 0755 "$install_dir" "$install_dir/static" "$install_dir/downloads"
install -d -o root -g pwweb -m 0750 "$config_dir"
install -d -o root -g pwsync -m 0750 /etc/pw155-sync
install -d -o root -g pwmonitor -m 0750 /var/lib/pw155-monitor
install -d -o root -g pwmap -m 0770 /var/lib/pw155-map-control/requests
install -d -o root -g pwbackup -m 0750 /var/lib/pw155-backup-control
install -d -o root -g pwbackup -m 0770 /var/lib/pw155-backup-control/requests
install -d -o root -g pwbackup -m 0750 /var/lib/pw155-backup-control/files
install -d -o root -g pwgamectl -m 0750 /var/lib/pw155-game-control
install -d -o root -g pwgamectl -m 0770 /var/lib/pw155-game-control/requests
install -d -o root -g root -m 0755 /srv/pw155/tools
install -d -o root -g root -m 0700 /srv/pw155/backups/database
install -o root -g root -m 0644 "$source_dir/app.py" "$install_dir/app.py"
install -o root -g root -m 0755 "$source_dir/sync_characters.py" "$install_dir/sync_characters.py"
install -o root -g root -m 0755 "$source_dir/gm_position.py" "$install_dir/gm_position.py"
install -o root -g root -m 0755 "$source_dir/role_operations.py" "$install_dir/role_operations.py"
install -o root -g root -m 0755 "$source_dir/monitor_services.py" "$install_dir/monitor_services.py"
install -o root -g root -m 0755 "$source_dir/map_control_worker.py" "$install_dir/map_control_worker.py"
install -o root -g root -m 0755 "$source_dir/backup_control_worker.py" "$install_dir/backup_control_worker.py"
install -o root -g root -m 0755 "$source_dir/game_control_worker.py" "$install_dir/game_control_worker.py"
install -o root -g root -m 0750 "$source_dir/pw155-backup-db.sh" /srv/pw155/tools/pw155-backup-db.sh
install -o root -g root -m 0644 "$source_dir/index.html" "$install_dir/index.html"
install -o root -g root -m 0644 "$source_dir/login.html" "$install_dir/login.html"
install -o root -g root -m 0644 "$source_dir/panel.html" "$install_dir/panel.html"
install -o root -g root -m 0644 "$source_dir/admin.html" "$install_dir/admin.html"
install -o root -g root -m 0644 "$source_dir/patch_manager.html" "$install_dir/patch_manager.html"
install -o root -g root -m 0644 "$source_dir/ranking.html" "$install_dir/ranking.html"
install -o root -g root -m 0644 "$source_dir/news.html" "$install_dir/news.html"
install -o root -g root -m 0644 "$source_dir/launcher-news.html" "$install_dir/launcher-news.html"
install -o root -g root -m 0644 "$source_dir/guide.html" "$install_dir/guide.html"
install -o root -g root -m 0644 "$source_dir/downloads.html" "$install_dir/downloads.html"
install -o root -g root -m 0644 "$source_dir/static/style.css" "$install_dir/static/style.css"
install -o root -g root -m 0644 "$source_dir/static/app.js" "$install_dir/static/app.js"
if [[ -f "$source_dir/downloads/manifest.json" ]]; then
  install -o root -g root -m 0644 "$source_dir/downloads/manifest.json" "$install_dir/downloads/manifest.json"
elif [[ -f "$source_dir/downloads/manifest.example.json" ]]; then
  install -o root -g root -m 0644 "$source_dir/downloads/manifest.example.json" "$install_dir/downloads/manifest.json"
fi
find "$source_dir/downloads" -maxdepth 1 -type f -name '*.zip' -exec install -o root -g root -m 0644 {} "$install_dir/downloads/" \;

db_password=""
if [[ -f "$config_dir/db.cnf" ]]; then
  db_password="$(awk -F= '$1 == "password" {print substr($0, index($0,"=")+1)}' "$config_dir/db.cnf")"
fi
if [[ -z "$db_password" ]]; then
  db_password="$(openssl rand -hex 24)"
fi

sync_password=""
if [[ -f /etc/pw155-sync/db.cnf ]]; then
  sync_password="$(awk -F= '$1 == "password" {print substr($0, index($0,"=")+1)}' /etc/pw155-sync/db.cnf)"
fi
if [[ -z "$sync_password" ]]; then
  sync_password="$(openssl rand -hex 24)"
fi

mariadb --batch <<SQL
CREATE USER IF NOT EXISTS 'pw_web'@'localhost' IDENTIFIED BY '$db_password';
ALTER USER 'pw_web'@'localhost' IDENTIFIED BY '$db_password';
CREATE USER IF NOT EXISTS 'pw_sync'@'localhost' IDENTIFIED BY '$sync_password';
ALTER USER 'pw_sync'@'localhost' IDENTIFIED BY '$sync_password';
CREATE DATABASE IF NOT EXISTS pw_portal CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS pw_portal.accounts (
  account_id INT NOT NULL,
  username VARCHAR(20) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (account_id),
  UNIQUE KEY uq_portal_username (username)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pw_portal.admins (
  account_id INT NOT NULL,
  granted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (account_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pw_portal.audit_log (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actor_id INT NOT NULL,
  actor_username VARCHAR(20) NOT NULL,
  action_name VARCHAR(40) NOT NULL,
  target_id INT NOT NULL,
  target_username VARCHAR(20) NOT NULL,
  details VARCHAR(255) NOT NULL,
  client_ip VARCHAR(45) NOT NULL,
  PRIMARY KEY (id),
  KEY ix_audit_created (created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pw_portal.news (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_key VARCHAR(64) NULL,
  title VARCHAR(100) NOT NULL,
  body VARCHAR(800) NOT NULL,
  status ENUM('draft','published','archived') NOT NULL DEFAULT 'draft',
  author_id INT NOT NULL,
  author_username VARCHAR(20) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  published_at TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_news_source (source_key),
  KEY ix_news_public (status, published_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pw_portal.coin_orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  account_id INT NOT NULL,
  role_id INT NOT NULL,
  role_name VARCHAR(64) NOT NULL,
  coin_amount INT UNSIGNED NOT NULL,
  payment_reference VARCHAR(64) NOT NULL,
  status ENUM('pending','processing','completed','rejected','failed') NOT NULL DEFAULT 'pending',
  client_ip VARCHAR(45) NOT NULL,
  processed_by INT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_at TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY ix_coin_account (account_id,created_at),
  KEY ix_coin_status (status,created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pw_portal.unstuck_log (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  account_id INT NOT NULL,
  role_id INT NOT NULL,
  role_name VARCHAR(64) NOT NULL,
  world_tag INT NOT NULL,
  pos_x FLOAT NOT NULL,
  pos_y FLOAT NOT NULL,
  pos_z FLOAT NOT NULL,
  client_ip VARCHAR(45) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_unstuck_account (account_id,created_at)
) ENGINE=InnoDB;
ALTER TABLE pw_portal.coin_orders
  MODIFY status ENUM('pending','processing','completed','rejected','failed')
  NOT NULL DEFAULT 'pending';
INSERT IGNORE INTO pw_portal.admins(account_id)
SELECT ID FROM pw.users WHERE ID=1024 AND LOWER(name)='admin';
INSERT IGNORE INTO pw_portal.news(source_key,title,body,status,author_id,author_username,published_at) VALUES
  ('seed-realm-stable','Realm lokal stabil','Seluruh daemon inti, map utama, dan Celestial Vale sudah aktif. Login, pembuatan karakter, serta masuk ke dunia telah diverifikasi.','published',1024,'PW155 Team','2026-08-31 00:00:00'),
  ('seed-id-stage-one','Bahasa Indonesia tahap pertama','Antarmuka umum telah diterjemahkan dan diperiksa di client. Nama skill dan item dipertahankan dalam bahasa Inggris agar tetap mudah dikenali pemain.','published',1024,'PW155 Team','2026-08-31 00:00:00'),
  ('seed-panels-active','Player & Admin Panel aktif','Portal sekarang mendukung registrasi, profil karakter, perubahan sandi, pengelolaan GM, monitoring, ranking publik, serta pusat download.','published',1024,'PW155 Team','2026-08-31 00:00:00');
GRANT SELECT (id, name, creatime) ON pw.users TO 'pw_web'@'localhost';
GRANT SELECT (uid, lastlogin) ON pw.point TO 'pw_web'@'localhost';
GRANT SELECT (userid, zoneid, rid) ON pw.auth TO 'pw_web'@'localhost';
GRANT SELECT (userid, zoneid, sn, cash, status, creatime) ON pw.usecashnow TO 'pw_web'@'localhost';
GRANT SELECT (account_id, role_id, role_name, role_level, role_occupation, role_gender, faction_name)
  ON pw.roles TO 'pw_web'@'localhost';
GRANT EXECUTE ON PROCEDURE pw.adduser TO 'pw_web'@'localhost';
GRANT SELECT, INSERT ON pw_portal.accounts TO 'pw_web'@'localhost';
GRANT UPDATE (username, password_hash, last_login_at) ON pw_portal.accounts TO 'pw_web'@'localhost';
GRANT SELECT ON pw_portal.admins TO 'pw_web'@'localhost';
GRANT SELECT ON pw_portal.audit_log TO 'pw_web'@'localhost';
GRANT SELECT ON pw_portal.news TO 'pw_web'@'localhost';
GRANT SELECT, INSERT, UPDATE ON pw_portal.coin_orders TO 'pw_web'@'localhost';
GRANT SELECT, INSERT ON pw_portal.unstuck_log TO 'pw_web'@'localhost';
GRANT INSERT ON pw_portal.audit_log TO 'pw_web'@'localhost';
GRANT SELECT (ID) ON pw.users TO 'pw_sync'@'localhost';
GRANT SELECT, INSERT, DELETE ON pw.roles TO 'pw_sync'@'localhost';
FLUSH PRIVILEGES;
SQL

mariadb --batch <<'SQL'
DROP PROCEDURE IF EXISTS pw_portal.verify_game_login;
DROP PROCEDURE IF EXISTS pw_portal.change_game_password;
DROP PROCEDURE IF EXISTS pw_portal.set_full_gm;
DROP PROCEDURE IF EXISTS pw_portal.save_news;
DROP PROCEDURE IF EXISTS pw_portal.set_news_status;
DROP PROCEDURE IF EXISTS pw_portal.grant_boutique_gold;
DELIMITER //
CREATE DEFINER='root'@'localhost' PROCEDURE pw_portal.verify_game_login(
  IN p_username VARCHAR(20), IN p_hash VARCHAR(64)
)
SQL SECURITY DEFINER
BEGIN
  SELECT ID, name FROM pw.users
  WHERE LOWER(name) = LOWER(p_username) AND passwd = p_hash
  LIMIT 1;
END//
CREATE DEFINER='root'@'localhost' PROCEDURE pw_portal.change_game_password(
  IN p_account_id INT, IN p_hash VARCHAR(64)
)
SQL SECURITY DEFINER
BEGIN
  UPDATE pw.users SET passwd = p_hash, passwd2 = p_hash
  WHERE ID = p_account_id;
END//
CREATE DEFINER='root'@'localhost' PROCEDURE pw_portal.set_full_gm(
  IN p_actor_id INT, IN p_target_id INT, IN p_enabled TINYINT, IN p_client_ip VARCHAR(45)
)
SQL SECURITY DEFINER
BEGIN
  DECLARE v_actor VARCHAR(20) DEFAULT NULL;
  DECLARE v_target VARCHAR(20) DEFAULT NULL;
  SELECT u.name INTO v_actor FROM pw.users u
    INNER JOIN pw_portal.admins a ON a.account_id=u.ID
    WHERE u.ID=p_actor_id LIMIT 1;
  SELECT name INTO v_target FROM pw.users WHERE ID=p_target_id LIMIT 1;
  IF v_actor IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Actor is not a portal admin';
  END IF;
  IF v_target IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Target account not found';
  END IF;
  START TRANSACTION;
  IF p_enabled = 1 THEN
    INSERT IGNORE INTO pw.auth(userid,zoneid,rid) VALUES
      (p_target_id,1,0),(p_target_id,1,1),(p_target_id,1,2),(p_target_id,1,3),
      (p_target_id,1,4),(p_target_id,1,5),(p_target_id,1,6),(p_target_id,1,7),
      (p_target_id,1,8),(p_target_id,1,9),(p_target_id,1,10),(p_target_id,1,11),
      (p_target_id,1,100),(p_target_id,1,101),(p_target_id,1,102),(p_target_id,1,103),
      (p_target_id,1,104),(p_target_id,1,105),(p_target_id,1,200),(p_target_id,1,201),
      (p_target_id,1,202),(p_target_id,1,203),(p_target_id,1,204),(p_target_id,1,205),
      (p_target_id,1,206),(p_target_id,1,207),(p_target_id,1,208),(p_target_id,1,209),
      (p_target_id,1,210),(p_target_id,1,211),(p_target_id,1,212),(p_target_id,1,213),
      (p_target_id,1,214),(p_target_id,1,500),(p_target_id,1,501),(p_target_id,1,502),
      (p_target_id,1,503),(p_target_id,1,504),(p_target_id,1,505),(p_target_id,1,506),
      (p_target_id,1,507),(p_target_id,1,508),(p_target_id,1,509),(p_target_id,1,510),
      (p_target_id,1,511),(p_target_id,1,512),(p_target_id,1,513),(p_target_id,1,514),
      (p_target_id,1,515),(p_target_id,1,516),(p_target_id,1,517),(p_target_id,1,518);
    INSERT INTO pw_portal.audit_log(actor_id,actor_username,action_name,target_id,
      target_username,details,client_ip) VALUES
      (p_actor_id,v_actor,'gm.grant',p_target_id,v_target,'memberikan hak GM zone 1 kepada',p_client_ip);
  ELSE
    DELETE FROM pw.auth WHERE userid=p_target_id AND zoneid=1;
    INSERT INTO pw_portal.audit_log(actor_id,actor_username,action_name,target_id,
      target_username,details,client_ip) VALUES
      (p_actor_id,v_actor,'gm.revoke',p_target_id,v_target,'mencabut hak GM zone 1 dari',p_client_ip);
  END IF;
  COMMIT;
END//
CREATE DEFINER='root'@'localhost' PROCEDURE pw_portal.save_news(
  IN p_actor_id INT, IN p_news_id INT, IN p_title VARCHAR(100), IN p_body VARCHAR(800),
  IN p_published TINYINT, IN p_client_ip VARCHAR(45)
)
SQL SECURITY DEFINER
BEGIN
  DECLARE v_actor VARCHAR(20) DEFAULT NULL;
  DECLARE v_news_id INT DEFAULT 0;
  DECLARE v_exists INT DEFAULT 0;
  SELECT u.name INTO v_actor FROM pw.users u
    INNER JOIN pw_portal.admins a ON a.account_id=u.ID
    WHERE u.ID=p_actor_id LIMIT 1;
  IF v_actor IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Actor is not a portal admin';
  END IF;
  IF CHAR_LENGTH(TRIM(p_title)) < 5 OR CHAR_LENGTH(TRIM(p_body)) < 20 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='News content is invalid';
  END IF;
  START TRANSACTION;
  IF p_news_id = 0 THEN
    INSERT INTO pw_portal.news(title,body,status,author_id,author_username,published_at)
    VALUES(TRIM(p_title),TRIM(p_body),IF(p_published=1,'published','draft'),
      p_actor_id,v_actor,IF(p_published=1,CURRENT_TIMESTAMP,NULL));
    SET v_news_id=LAST_INSERT_ID();
  ELSE
    SELECT COUNT(*) INTO v_exists FROM pw_portal.news WHERE id=p_news_id;
    IF v_exists = 0 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='News item not found';
    END IF;
    UPDATE pw_portal.news SET title=TRIM(p_title),body=TRIM(p_body),
      status=IF(p_published=1,'published','draft'),author_id=p_actor_id,
      author_username=v_actor,
      published_at=IF(p_published=1,COALESCE(published_at,CURRENT_TIMESTAMP),NULL)
      WHERE id=p_news_id;
    SET v_news_id=p_news_id;
  END IF;
  INSERT INTO pw_portal.audit_log(actor_id,actor_username,action_name,target_id,
    target_username,details,client_ip) VALUES
    (p_actor_id,v_actor,'news.save',v_news_id,'berita',
     CONCAT(IF(p_published=1,'menyimpan dan menerbitkan: ','menyimpan draft: '),LEFT(TRIM(p_title),180)),p_client_ip);
  COMMIT;
  SELECT v_news_id;
END//
CREATE DEFINER='root'@'localhost' PROCEDURE pw_portal.set_news_status(
  IN p_actor_id INT, IN p_news_id INT, IN p_status VARCHAR(16), IN p_client_ip VARCHAR(45)
)
SQL SECURITY DEFINER
BEGIN
  DECLARE v_actor VARCHAR(20) DEFAULT NULL;
  DECLARE v_title VARCHAR(100) DEFAULT NULL;
  SELECT u.name INTO v_actor FROM pw.users u
    INNER JOIN pw_portal.admins a ON a.account_id=u.ID
    WHERE u.ID=p_actor_id LIMIT 1;
  SELECT title INTO v_title FROM pw_portal.news WHERE id=p_news_id LIMIT 1;
  IF v_actor IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Actor is not a portal admin';
  END IF;
  IF v_title IS NULL OR p_status NOT IN ('published','archived') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='News action is invalid';
  END IF;
  START TRANSACTION;
  UPDATE pw_portal.news SET status=p_status,
    published_at=IF(p_status='published',COALESCE(published_at,CURRENT_TIMESTAMP),published_at)
    WHERE id=p_news_id;
  INSERT INTO pw_portal.audit_log(actor_id,actor_username,action_name,target_id,
    target_username,details,client_ip) VALUES
    (p_actor_id,v_actor,IF(p_status='published','news.publish','news.archive'),
     p_news_id,'berita',CONCAT(IF(p_status='published','menerbitkan: ','mengarsipkan: '),LEFT(v_title,180)),p_client_ip);
  COMMIT;
END//
CREATE DEFINER='root'@'localhost' PROCEDURE pw_portal.grant_boutique_gold(
  IN p_actor_id INT, IN p_target_username VARCHAR(20), IN p_gold INT,
  IN p_client_ip VARCHAR(45)
)
SQL SECURITY DEFINER
BEGIN
  DECLARE v_actor VARCHAR(20) DEFAULT NULL;
  DECLARE v_target_id INT DEFAULT NULL;
  DECLARE v_target VARCHAR(20) DEFAULT NULL;
  DECLARE v_pending INT DEFAULT 0;
  DECLARE v_cash INT DEFAULT 0;
  SELECT u.name INTO v_actor FROM pw.users u
    INNER JOIN pw_portal.admins a ON a.account_id=u.ID
    WHERE u.ID=p_actor_id LIMIT 1;
  SELECT ID,name INTO v_target_id,v_target FROM pw.users
    WHERE LOWER(name)=LOWER(p_target_username) LIMIT 1;
  IF v_actor IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Actor is not a portal admin';
  END IF;
  IF v_target_id IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Target account not found';
  END IF;
  IF p_gold < 1 OR p_gold > 10000 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Gold amount is outside allowed range';
  END IF;
  SELECT COUNT(*) INTO v_pending FROM pw.usecashnow
    WHERE userid=v_target_id AND zoneid=1 AND sn>=0;
  IF v_pending > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Target already has pending cash';
  END IF;
  SET v_cash=p_gold*100;
  START TRANSACTION;
  INSERT INTO pw.usecashnow(userid,zoneid,sn,aid,point,cash,status,creatime)
    VALUES(v_target_id,1,0,1,0,v_cash,1,CURRENT_TIMESTAMP);
  INSERT INTO pw_portal.audit_log(actor_id,actor_username,action_name,target_id,
    target_username,details,client_ip) VALUES
    (p_actor_id,v_actor,'cash.grant',v_target_id,v_target,
     CONCAT('mengirim ',p_gold,' Gold Boutique kepada'),p_client_ip);
  COMMIT;
  SELECT v_target_id,v_target,p_gold,v_cash;
END//
DELIMITER ;
GRANT EXECUTE ON PROCEDURE pw_portal.verify_game_login TO 'pw_web'@'localhost';
GRANT EXECUTE ON PROCEDURE pw_portal.change_game_password TO 'pw_web'@'localhost';
GRANT EXECUTE ON PROCEDURE pw_portal.set_full_gm TO 'pw_web'@'localhost';
GRANT EXECUTE ON PROCEDURE pw_portal.save_news TO 'pw_web'@'localhost';
GRANT EXECUTE ON PROCEDURE pw_portal.set_news_status TO 'pw_web'@'localhost';
GRANT EXECUTE ON PROCEDURE pw_portal.grant_boutique_gold TO 'pw_web'@'localhost';
FLUSH PRIVILEGES;
SQL

umask 0027
printf '[client]\nuser=pw_web\npassword=%s\nhost=localhost\ndatabase=pw\nprotocol=socket\n' \
  "$db_password" > "$config_dir/db.cnf"
chown root:pwweb "$config_dir/db.cnf"
chmod 0640 "$config_dir/db.cnf"

printf '[client]\nuser=pw_sync\npassword=%s\nhost=localhost\ndatabase=pw\nprotocol=socket\n' \
  "$sync_password" > /etc/pw155-sync/db.cnf
chown root:pwsync /etc/pw155-sync/db.cnf
chmod 0640 /etc/pw155-sync/db.cnf

if [[ ! -f "$config_dir/web.env" ]]; then
  printf 'PW155_WEB_CSRF_SECRET=%s\nPW155_WEB_HOST=0.0.0.0\nPW155_WEB_PORT=8080\n' \
    "$(openssl rand -hex 32)" > "$config_dir/web.env"
fi
grep -q '^PW155_SAFE_WORLD_TAG=' "$config_dir/web.env" || printf 'PW155_SAFE_WORLD_TAG=1\n' >> "$config_dir/web.env"
grep -q '^PW155_SAFE_X=' "$config_dir/web.env" || printf 'PW155_SAFE_X=1286.669\n' >> "$config_dir/web.env"
grep -q '^PW155_SAFE_Y=' "$config_dir/web.env" || printf 'PW155_SAFE_Y=219.375\n' >> "$config_dir/web.env"
grep -q '^PW155_SAFE_Z=' "$config_dir/web.env" || printf 'PW155_SAFE_Z=1044.339\n' >> "$config_dir/web.env"
chown root:pwweb "$config_dir/web.env"
chmod 0640 "$config_dir/web.env"

cat > "$service_file" <<'UNIT'
[Unit]
Description=PW155 local player portal
After=network.target mariadb.service
Requires=mariadb.service

[Service]
Type=simple
User=pwweb
Group=pwweb
SupplementaryGroups=pwmonitor pwmap pwbackup pwgamectl
WorkingDirectory=/opt/pw155-web
EnvironmentFile=/etc/pw155-web/web.env
Environment=PW155_WEB_DB_CONFIG=/etc/pw155-web/db.cnf
Environment=PW155_MONITOR_DIR=/var/lib/pw155-monitor
Environment=PW155_MAP_CONTROL_DIR=/var/lib/pw155-map-control
Environment=PW155_BACKUP_CONTROL_DIR=/var/lib/pw155-backup-control
Environment=PW155_GAME_CONTROL_DIR=/var/lib/pw155-game-control
ExecStart=/usr/bin/python3 /opt/pw155-web/app.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/var/lib/pw155-map-control/requests /var/lib/pw155-backup-control/requests /var/lib/pw155-game-control/requests
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true

[Install]
WantedBy=multi-user.target
UNIT

chmod 0644 "$service_file"

cat > /etc/systemd/system/pw155-character-sync.service <<'UNIT'
[Unit]
Description=PW155 read-only character cache synchronization
After=network.target mariadb.service
Requires=mariadb.service

[Service]
Type=oneshot
User=pwsync
Group=pwsync
WorkingDirectory=/opt/pw155-web
Environment=PW155_SYNC_DB_CONFIG=/etc/pw155-sync/db.cnf
Environment=PW155_GAMEDBD_HOST=127.0.0.1
Environment=PW155_GAMEDBD_PORT=29400
ExecStart=/usr/bin/python3 /opt/pw155-web/sync_characters.py --all
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/pw155-character-sync.timer <<'UNIT'
[Unit]
Description=Refresh PW155 character cache every five minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
RandomizedDelaySec=20
Persistent=true
Unit=pw155-character-sync.service

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/pw155-monitor.service <<'UNIT'
[Unit]
Description=Collect PW155 daemon and maintenance service status
After=network.target

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/opt/pw155-web
Environment=PW155_MONITOR_DIR=/var/lib/pw155-monitor
Environment=PW155_PID_DIR=/srv/pw155/runtime/pids
ExecStart=/usr/bin/python3 /opt/pw155-web/monitor_services.py
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/var/lib/pw155-monitor
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true
UNIT

cat > /etc/systemd/system/pw155-monitor.timer <<'UNIT'
[Unit]
Description=Refresh PW155 service monitoring every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=1min
AccuracySec=10s
Persistent=true
Unit=pw155-monitor.service

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/pw155-map-control.service <<'UNIT'
[Unit]
Description=Process allowlisted PW155 map requests
After=network.target mariadb.service

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/opt/pw155-web
Environment=PW155_MAP_CONTROL_DIR=/var/lib/pw155-map-control
Environment=PW155_PID_DIR=/srv/pw155/runtime/pids
Environment=PW155_GS_CONFIG=/srv/pw155/staging/pw155/gamed/gs.conf
Environment=PW155_SERVICE_SCRIPT=/srv/pw155/tools/pw155-service.sh
ExecStart=/usr/bin/python3 /opt/pw155-web/map_control_worker.py process
# Proses game yang dimulai worker harus berbagi /tmp dan namespace host dengan
# daemon PW lain. Input tetap dibatasi oleh catalog gs.conf di worker.
KillMode=process
UNIT

cat > /etc/systemd/system/pw155-map-control.path <<'UNIT'
[Unit]
Description=Watch for PW155 map control requests

[Path]
PathExistsGlob=/var/lib/pw155-map-control/requests/*.json
Unit=pw155-map-control.service

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/pw155-backup-control.service <<'UNIT'
[Unit]
Description=Process allowlisted PW155 database backup requests
After=mariadb.service
Requires=mariadb.service

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/opt/pw155-web
Environment=PW155_BACKUP_CONTROL_DIR=/var/lib/pw155-backup-control
Environment=PW155_BACKUP_ROOT=/srv/pw155/backups/database
Environment=PW155_BACKUP_SCRIPT=/srv/pw155/tools/pw155-backup-db.sh
Environment=PW155_BACKUP_RETENTION_DAYS=14
ExecStart=/usr/bin/python3 /opt/pw155-web/backup_control_worker.py process
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/var/lib/pw155-backup-control /srv/pw155/backups/database
RestrictSUIDSGID=true
LockPersonality=true
UNIT

cat > /etc/systemd/system/pw155-backup-control.path <<'UNIT'
[Unit]
Description=Watch for PW155 database backup requests

[Path]
PathExistsGlob=/var/lib/pw155-backup-control/requests/*.json
Unit=pw155-backup-control.service

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/pw155-game-control.service <<'UNIT'
[Unit]
Description=PW155 in-game broadcast and persistent safe shutdown worker
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/pw155-web
Environment=PW155_GAME_CONTROL_DIR=/var/lib/pw155-game-control
Environment=PW155_SERVICE_SCRIPT=/srv/pw155/tools/pw155-service.sh
Environment=PW155_PROVIDER_HOST=127.0.0.1
Environment=PW155_PROVIDER_PORT=29300
Environment=PW155_WORLD_CHAT_OPCODE=120
ExecStart=/usr/bin/python3 /opt/pw155-web/game_control_worker.py run
Restart=on-failure
RestartSec=2
KillMode=process
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/var/lib/pw155-game-control /srv/pw155/runtime
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/pw155-map-status.service <<'UNIT'
[Unit]
Description=Refresh PW155 map status

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/opt/pw155-web
Environment=PW155_MAP_CONTROL_DIR=/var/lib/pw155-map-control
Environment=PW155_PID_DIR=/srv/pw155/runtime/pids
Environment=PW155_GS_CONFIG=/srv/pw155/staging/pw155/gamed/gs.conf
ExecStart=/usr/bin/python3 /opt/pw155-web/map_control_worker.py refresh
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/var/lib/pw155-map-control
RestrictSUIDSGID=true
LockPersonality=true
UNIT

cat > /etc/systemd/system/pw155-map-status.timer <<'UNIT'
[Unit]
Description=Refresh PW155 map status every 15 seconds

[Timer]
OnBootSec=10s
OnUnitActiveSec=15s
AccuracySec=3s
Unit=pw155-map-status.service

[Install]
WantedBy=timers.target
UNIT

chmod 0644 /etc/systemd/system/pw155-character-sync.service \
  /etc/systemd/system/pw155-character-sync.timer \
  /etc/systemd/system/pw155-monitor.service \
  /etc/systemd/system/pw155-monitor.timer \
  /etc/systemd/system/pw155-map-control.service \
  /etc/systemd/system/pw155-map-control.path \
  /etc/systemd/system/pw155-backup-control.service \
  /etc/systemd/system/pw155-backup-control.path \
  /etc/systemd/system/pw155-game-control.service \
  /etc/systemd/system/pw155-map-status.service \
  /etc/systemd/system/pw155-map-status.timer
systemctl daemon-reload
systemctl enable pw155-web.service
systemctl enable --now pw155-character-sync.timer
systemctl enable --now pw155-monitor.timer
systemctl enable --now pw155-map-control.path
systemctl enable --now pw155-backup-control.path
systemctl enable pw155-game-control.service
systemctl restart pw155-game-control.service
systemctl enable --now pw155-map-status.timer
PW155_BACKUP_CONTROL_DIR=/var/lib/pw155-backup-control \
  PW155_BACKUP_ROOT=/srv/pw155/backups/database \
  /usr/bin/python3 /opt/pw155-web/backup_control_worker.py refresh
systemctl start pw155-map-status.service
systemctl start pw155-monitor.service
systemctl restart pw155-web.service
systemctl --no-pager --full status pw155-web.service | sed -n '1,12p'
echo "ASP PWPanel berhasil dipasang atau diperbarui."
