# Batur Teacher Talking — one-command installer for Windows
#   irm https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.ps1 | iex
# Optional: preload the Gemini key so the student never sees a prompt:
#   $env:BTT_KEY="your-key"; irm https://raw.githubusercontent.com/baturdenada/batur-teacher-talking/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
$ProgressPreference = "SilentlyContinue"            # makes the download much faster
$repo    = "baturdenada/batur-teacher-talking"
$desktop = [Environment]::GetFolderPath("Desktop")
$dir     = Join-Path $desktop "Batur Teacher Talking"
# never touch a developer/source copy that lives under the same name
if (Test-Path (Join-Path $dir "app.py")) { $dir = Join-Path $desktop "Batur Teacher Talking App" }
$exe     = Join-Path $dir "Batur Teacher Talking.exe"
$url     = "https://github.com/$repo/releases/latest/download/BaturTeacherTalking-windows.zip"
$zip     = Join-Path $env:TEMP "BaturTeacherTalking-windows.zip"

Write-Host "Installing Batur Teacher Talking..." -ForegroundColor Cyan
Get-Process "Batur Teacher Talking" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
$tmp = Join-Path $env:TEMP ("btt_" + [guid]::NewGuid().ToString("N"))
Expand-Archive -Path $zip -DestinationPath $tmp -Force
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Remove-Item (Join-Path $dir "_internal") -Recurse -Force -ErrorAction SilentlyContinue   # old version's files only
Copy-Item -Path (Join-Path $tmp "Batur Teacher Talking\*") -Destination $dir -Recurse -Force
Remove-Item $tmp, $zip -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Installed to $dir"

if ($env:BTT_KEY) {
    $data = Join-Path $env:APPDATA "BaturTeacherTalking"
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    Set-Content -Path (Join-Path $data "api_key.txt") -Value $env:BTT_KEY.Trim() -NoNewline
    Remove-Item Env:\BTT_KEY
    Write-Host "Gemini key saved."
}

$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $desktop "Batur Teacher Talking.lnk"))
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $dir
$lnk.IconLocation = "$exe,0"
$lnk.Description = "Batur Teacher Talking - live captions"
$lnk.Save()

Write-Host "Done! Starting the app..." -ForegroundColor Green
Start-Process $exe
