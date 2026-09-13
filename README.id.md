# ASP PWPanel

[English](README.md)

ASP PWPanel adalah panel administrasi dan portal pemain yang ringan untuk
server Perfect World 1.5.5 milik sendiri. Panel memakai pustaka standar Python
dan MariaDB, serta dirancang berjalan berdampingan dengan layanan PW155 di
Ubuntu.

## Fitur

- Pendaftaran, login, panel akun, ranking, berita, dan unduhan pemain
- Endpoint berita `/launcher-news` untuk browser lama di Launcher Perfect World
- Permintaan pembelian coin dengan antrean verifikasi administrator
- Pemulihan karakter tersangkut ke titik aman tetap (wajib offline, kepemilikan
  diperiksa, memakai cooldown, dan tercatat di audit)
- Pengelolaan akun dan GM oleh admin
- Dashboard admin responsif dengan sidebar dan kartu status bernuansa PW
- Antrean cash Boutique dan sinkronisasi karakter
- Monitoring layanan inti dan kontrol map
- Tautan opsional ke layanan ASP CPW yang dipasang secara terpisah
- Backup database manual oleh admin dengan akses unduhan yang dilindungi

## Yang tidak disertakan

Repositori ini hanya berisi source code asli panel. Repositori **tidak** berisi
client/server Perfect World, binary game, data game, payload patch, kredensial,
backup, atau schema konfigurasi pihak ketiga. Baca [NOTICE.md](NOTICE.md).

## Kebutuhan

- Ubuntu Server 20.04 atau Linux kompatibel yang memakai `systemd`
- Python 3.8 atau lebih baru
- MariaDB client/server dan database PW155 yang kompatibel
- Layanan PW155 yang sudah tersedia serta data game yang diperoleh secara sah
- Akun database khusus dengan hak akses seminimal mungkin
- Opsional: ASP CPW untuk menerbitkan patch client

Tidak ada paket Python pihak ketiga yang perlu dipasang.

Pesanan coin tidak menarik pembayaran secara otomatis. Pemain memasukkan
referensi pembayaran, lalu administrator memverifikasi dan menyetujui
pengiriman. Coin yang disetujui ditambahkan melalui operasi PW GameDB
`DBModifyRoleData`, bukan dengan mengubah cache karakter. Fitur Unstuck membaca
status karakter lengkap, hanya mengganti world dan koordinat yang sudah
dikonfigurasi, lalu menyimpannya kembali. Pemain tidak dapat memasukkan
koordinat bebas.

## Menjalankan untuk pengembangan

1. Clone repositori.
2. Salin nilai dari `.env.example` ke environment shell atau service.
3. Buat `/etc/pw155-web/db.cnf` berisi akun MariaDB dengan hak akses terbatas.
4. Buat CSRF secret unik minimal 32 karakter.
5. Terapkan `database/player-services.sql` sebagai root MariaDB saat
   memperbarui instalasi lama.
6. Jalankan panel:

```bash
export PW155_WEB_CSRF_SECRET="ganti-dengan-secret-acak-yang-unik"
export PW155_WEB_DB_CONFIG="/etc/pw155-web/db.cnf"
python3 app.py
```

Buka `http://127.0.0.1:8080`. Halaman akun membutuhkan database yang sudah
dikonfigurasi.

Pengeditan data game sengaja tidak disertakan dalam ASP PWPanel. Gunakan editor
desktop khusus untuk item, NPC, monster, spawn, merchant, dan recipe.

Untuk produksi, jalankan panel sebagai service `systemd` tanpa hak root dan
letakkan di belakang reverse proxy HTTPS. Jangan membuka HTTP server bawaan
Python langsung ke internet. Batasi izin file database dan simpan semua folder
worker/control di luar web root.

## Instalasi dan update

1. Hidupkan VM/VPS Ubuntu dan pastikan SSH dapat diakses.
2. Klik dua kali [`ASP-PWPANEL-DESKTOP.cmd`](ASP-PWPANEL-DESKTOP.cmd).
3. Isi alamat Ubuntu, port SSH, username SSH, dan URL panel, lalu simpan.
4. Pilih **Test SSH**, kemudian **Install / Update**.
5. Buka panel dan periksa kartu status layanan.

Jalankan **Install / Update** satu kali untuk panel baru dan ulangi setelah
menarik versi ASP PWPanel yang lebih baru. Aplikasi tidak menyimpan password
SSH. ASP CPW adalah produk opsional yang terpisah; installer ini tidak pernah
memasang atau memperbarui ASP CPW.

## Backup database dari Admin Panel

1. Login memakai akun administrator.
2. Buka **Database Backup**.
3. Pilih **Create Backup** dan tunggu worker selesai.
4. Unduh arsip yang dihasilkan dari daftar backup yang terlindungi.
5. Simpan satu salinan tambahan di luar VM/VPS.

Proses web tidak menjalankan dump database sebagai root. Web hanya membuat
permintaan yang sudah dibatasi, lalu worker sistem terpisah membuat dan
mengemas backup. Retensi default adalah 14 hari dan dapat diubah melalui
`PW155_BACKUP_RETENTION_DAYS`.

## Unduhan dan payload CPW

Salin `downloads/manifest.example.json` menjadi `downloads/manifest.json`, lalu
masukkan hanya paket yang memang boleh Anda distribusikan. Folder
`downloads/CPW`, ZIP, data game, dan manifest hasil generate diabaikan Git.

## Pengujian

```bash
python3 -m unittest discover -s tests -v
```

Rangkaian test mencakup validasi input, otorisasi, CSRF/session, antrean kontrol
layanan, akses patch, monitoring, dan worker backup.

## Lisensi

Source code dan dokumentasi ASP PWPanel dirilis memakai [Lisensi MIT](LICENSE).
Lisensi tersebut tidak memberikan hak atas Perfect World maupun aset pihak
ketiga.
