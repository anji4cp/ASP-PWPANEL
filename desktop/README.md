# ASP PWPanel Desktop

[Bahasa Indonesia](README.id.md) | **English**

This lightweight Windows application installs or updates **ASP PWPanel only**.
Keep the executable inside the repository because it calls the installer in
`installer/install-from-windows.ps1`.

## First installation

1. Start the Ubuntu VM/VPS and confirm SSH is reachable.
2. Run `ASP-PWPANEL-DESKTOP.cmd` from the repository root.
3. Enter the Ubuntu address, SSH port, SSH username, and panel URL.
4. Select **Save Settings**, then **Test SSH**.
5. Select **Install / Update** and enter the hidden SSH/sudo password when asked.
6. Select **Open Panel** and verify the service status.

## Updating

Pull or download the newest ASP PWPanel repository, then run **Install / Update**
again. Connection settings are reused; passwords are never saved. This process
does not install, update, stop, or remove ASP CPW.
