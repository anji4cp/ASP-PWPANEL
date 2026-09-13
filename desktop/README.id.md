# ASP PWPanel Desktop

**Bahasa Indonesia** | [English](README.md)

Aplikasi Windows ringan ini hanya memasang atau memperbarui **ASP PWPanel**.
Pertahankan executable di dalam repositori karena aplikasi memanggil installer
`installer/install-from-windows.ps1`.

## Instalasi pertama

1. Hidupkan VM/VPS Ubuntu dan pastikan SSH dapat diakses.
2. Jalankan `ASP-PWPANEL-DESKTOP.cmd` dari root repositori.
3. Isi alamat Ubuntu, port SSH, username SSH, dan URL panel.
4. Pilih **Save Settings**, kemudian **Test SSH**.
5. Pilih **Install / Update**, lalu masukkan password SSH/sudo saat diminta.
   Karakter password memang tidak terlihat.
6. Pilih **Open Panel** dan periksa status layanan.

## Memperbarui panel

Pull atau unduh repositori ASP PWPanel terbaru, lalu jalankan **Install / Update**
lagi. Pengaturan koneksi dipakai kembali; password tidak pernah disimpan. Proses
ini tidak memasang, memperbarui, menghentikan, atau menghapus ASP CPW.
