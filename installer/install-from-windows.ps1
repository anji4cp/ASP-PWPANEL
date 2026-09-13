param(
  [Parameter(Mandatory=$true)][string]$Server,
  [Parameter(Mandatory=$true)][ValidateRange(1,65535)][int]$Port,
  [Parameter(Mandatory=$true)][string]$User
)

$ErrorActionPreference = "Stop"
$Package = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Archive = Join-Path $env:TEMP ("asp-pwpanel-" + [guid]::NewGuid().ToString("N") + ".tar.gz")
$RemoteArchive = "/tmp/asp-pwpanel-upload.tar.gz"
$RemoteRoot = "/tmp/asp-pwpanel-install"

if ($User -notmatch '^[a-z_][a-z0-9_-]{0,31}$') {
  throw "Invalid SSH username. Enter the Ubuntu login name, not its password."
}
try {
  Write-Host "ASP PWPanel Desktop Installer" -ForegroundColor Cyan
  Write-Host "Target: $User@$Server`:$Port"
  Write-Host "Creating a clean installation package..."
  & tar.exe -czf $Archive --exclude=.git --exclude=desktop/bin --exclude=__pycache__ --exclude='*/__pycache__' -C $Package .
  if ($LASTEXITCODE -ne 0) { throw "Unable to create the installation archive." }

  Write-Host "Uploading package. Enter the Ubuntu SSH password when requested." -ForegroundColor Yellow
  & scp.exe -P $Port $Archive "$User@$Server`:$RemoteArchive"
  if ($LASTEXITCODE -ne 0) { throw "Unable to upload the PWPanel package." }

  $Command = "rm -rf '$RemoteRoot' && mkdir -p '$RemoteRoot' && tar -xzf '$RemoteArchive' -C '$RemoteRoot' && chmod +x '$RemoteRoot/installer/install-asp-pwpanel.sh' && sudo '$RemoteRoot/installer/install-asp-pwpanel.sh' '$RemoteRoot'"
  & ssh.exe -t -p $Port "$User@$Server" $Command
  if ($LASTEXITCODE -ne 0) { throw "Ubuntu rejected or failed the PWPanel installation." }
  Write-Host "ASP PWPanel installed/updated successfully." -ForegroundColor Green
}
finally {
  if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
}
