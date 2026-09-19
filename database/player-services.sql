-- Re-runnable schema upgrade for Player Panel coin orders and safe teleport.
CREATE DATABASE IF NOT EXISTS pw_portal CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS pw_portal.realm_settings (
  id TINYINT UNSIGNED NOT NULL,
  dummy_online INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (id)
) ENGINE=InnoDB;
INSERT IGNORE INTO pw_portal.realm_settings(id,dummy_online) VALUES (1,0);
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
ALTER TABLE pw_portal.coin_orders
  MODIFY status ENUM('pending','processing','completed','rejected','failed')
  NOT NULL DEFAULT 'pending';
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
GRANT SELECT, INSERT, UPDATE ON pw_portal.coin_orders TO 'pw_web'@'localhost';
GRANT SELECT, INSERT ON pw_portal.unstuck_log TO 'pw_web'@'localhost';
GRANT INSERT ON pw_portal.audit_log TO 'pw_web'@'localhost';
GRANT SELECT (zoneid) ON pw.point TO 'pw_web'@'localhost';
GRANT SELECT ON pw_portal.realm_settings TO 'pw_web'@'localhost';
GRANT UPDATE (dummy_online) ON pw_portal.realm_settings TO 'pw_web'@'localhost';
FLUSH PRIVILEGES;
