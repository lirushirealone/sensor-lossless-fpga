param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\SensorLosslessFPGA'),
    [switch]$NoShortcuts,
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# PowerShell 7 or another parent process can pass a different module search path.
# Always resolve the Windows PowerShell standard modules from this executable.
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$SourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$utf8 = New-Object Text.UTF8Encoding($false)
$installLock = $null
function Inside([string]$Root, [string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative)) { throw "Expected relative path: $Relative" }
    $full = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if (-not $full.StartsWith($Root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Path escapes its root: $Relative" }
    return $full
}
function Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Run([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}
try {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw 'This installer supports Windows 10/11 x64 (Intel/AMD). ARM64 and 32-bit Windows are not yet qualified.' }
    if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'Windows 10 or Windows 11 is required.' }
    if ($InstallRoot -eq $SourceRoot -or $InstallRoot.StartsWith($SourceRoot.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Choose an installation directory outside the extracted source package.' }
    Write-Host '[1/6] Checking the complete source package...'
    $manifestPath = Join-Path $SourceRoot 'manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $local = Inside $SourceRoot $entry.Name
        if ((Hash $local) -ne $entry.Value) { throw "Source checksum mismatch: $($entry.Name). Extract a complete fresh package." }
    }
    $identity = (Hash $manifestPath).Substring(0,16)
    $AppRoot = Inside $InstallRoot ('app\'+$identity)
    $EngineRoot = Inside $InstallRoot 'engine'
    New-Item -ItemType Directory -Path $AppRoot,$EngineRoot -Force | Out-Null
    # A lock prevents two installers modifying the same environment concurrently.
    $lockPath = Inside $InstallRoot 'install.lock'
    $installLock = [IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    $config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'downloads.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $env:UV_CACHE_DIR = Inside $EngineRoot 'cache'
    $env:UV_PYTHON_INSTALL_DIR = Inside $EngineRoot 'python'
    $env:UV_PYTHON_BIN_DIR = Inside $EngineRoot 'bin'
    $env:UV_PYTHON_NO_REGISTRY = 'true'
    $env:UV_PYTHON_INSTALL_REGISTRY = '0'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:UV_NO_CONFIG = 'true'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:PYTHONUTF8 = '1'
    Write-Host '[2/6] Preparing a checksum-verified installation tool...'
    $uvDirectory = Inside $EngineRoot ('uv-'+$config.uv_version)
    $uvZip = Inside $EngineRoot ('uv-'+$config.uv_version+'.zip')
    if (-not (Test-Path -LiteralPath $uvZip) -or (Hash $uvZip) -ne $config.uv_zip_sha256) {
        $pending = $uvZip+'.pending'
        Invoke-WebRequest -UseBasicParsing -Uri $config.uv_url -OutFile $pending
        if ((Hash $pending) -ne $config.uv_zip_sha256) { throw 'Installer download checksum mismatch; no downloaded program was executed.' }
        Move-Item -LiteralPath $pending -Destination $uvZip -Force
    }
    # Extract afresh into a unique directory; never trust a stale executable.
    $uvExtract = Inside $EngineRoot ('uv-extract-'+[Guid]::NewGuid().ToString('N'))
    Expand-Archive -LiteralPath $uvZip -DestinationPath $uvExtract
    $uv = @(Get-ChildItem -LiteralPath $uvExtract -Recurse -File -Filter 'uv.exe')
    if ($uv.Count -ne 1) { throw 'Unexpected installation tool archive.' }
    $uvExe = $uv[0].FullName
    Write-Host '[3/6] Copying the current software into its own directory...'
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $target = Inside $AppRoot $entry.Name
        if (Test-Path -LiteralPath $target) {
            if ((Hash $target) -ne $entry.Value) { throw "Existing installation has been modified: $($entry.Name)" }
        } else {
            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
            Copy-Item -LiteralPath (Inside $SourceRoot $entry.Name) -Destination $target
        }
    }
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $AppRoot 'manifest.json') -Force
    Write-Host '[4/6] Downloading independent Python and locked desktop dependencies...'
    $venv = Inside $AppRoot '.venv'
    $python = Inside $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        Run $uvExe @('venv','--python',$config.python,$venv)
    }
    Run $uvExe @('pip','sync','--python',$python,'--require-hashes','--only-binary',':all:',(Join-Path $AppRoot 'installer\requirements-lock.txt'))
    $env:SENSOR_WORKBENCH_HOME = $InstallRoot
    foreach ($name in @('导入数据','历史结果汇总','工作缓存','运行日志')) {
        New-Item -ItemType Directory -Path (Inside $InstallRoot $name) -Force | Out-Null
    }
    foreach ($example in Get-ChildItem -LiteralPath (Join-Path $AppRoot 'data\examples') -File) {
        $destination = Inside (Join-Path $InstallRoot '导入数据') $example.Name
        if (-not (Test-Path -LiteralPath $destination)) { Copy-Item -LiteralPath $example.FullName -Destination $destination }
    }
    Write-Host '[5/6] Verifying all examples and the desktop window (no board shutdown)...'
    Run $python @('-I','-X','utf8',(Join-Path $AppRoot 'tools\installation_check.py'))
    Write-Host '[6/6] Creating launch files and shortcuts...'
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launch.pyw') -Destination (Join-Path $InstallRoot 'launch.pyw') -Force
    $pythonw = Inside $venv 'Scripts\pythonw.exe'
    $receipt = [ordered]@{schema='sensor-workbench-install-v2';app_relative_path=('app\'+$identity);source_manifest_sha256=(Hash $manifestPath);python=$config.python;installed_at=(Get-Date).ToString('o');data_root=$InstallRoot}
    $receiptTemp = Inside $InstallRoot 'installation.json.pending'
    [IO.File]::WriteAllText($receiptTemp,($receipt | ConvertTo-Json -Depth 5),$utf8)
    Move-Item -LiteralPath $receiptTemp -Destination (Join-Path $InstallRoot 'installation.json') -Force
    $command = '@echo off'+"`r`n"+'"%~dp0app\'+$identity+'\.venv\Scripts\pythonw.exe" -I -X utf8 "%~dp0launch.pyw"'+"`r`n"
    [IO.File]::WriteAllText((Join-Path $InstallRoot 'start.cmd'),$command,$utf8)
    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        foreach ($directory in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))) {
            if ($directory) {
                $shortcut = $shell.CreateShortcut((Join-Path $directory '多通道传感数据无损压缩系统.lnk'))
                $shortcut.TargetPath = $pythonw
                $shortcut.Arguments = '-I -X utf8 "'+(Join-Path $InstallRoot 'launch.pyw')+'"'
                $shortcut.WorkingDirectory = $InstallRoot
                $shortcut.Description = '多通道传感数据无损压缩系统'
                $shortcut.Save()
            }
        }
    }
    Write-Host ('Installation complete: '+$InstallRoot) -ForegroundColor Green
    if (-not $NoLaunch) { Start-Process -FilePath $pythonw -ArgumentList @('-I','-X','utf8',('"'+(Join-Path $InstallRoot 'launch.pyw')+'"')) -WorkingDirectory $InstallRoot -WindowStyle Hidden }
    exit 0
} catch {
    Write-Host ('Installation failed: '+$_.Exception.Message) -ForegroundColor Red
    Write-Host 'Existing verified history is preserved. Check your network, extract the complete package, and rerun install.cmd.'
    exit 1
} finally {
    if ($null -ne $installLock) { $installLock.Dispose() }
}
