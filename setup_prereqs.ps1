#Requires -Version 5.1
<#
Installs the developer toolchain Remiqora's other scripts need, via
winget (built into Windows 10/11; if missing, install "App Installer" from
the Microsoft Store first).

NOT covered here, on purpose:
  - The NVIDIA GPU driver. Get it from https://www.nvidia.com/drivers for
    your exact card - an unattended driver swap can break your display and
    winget's driver packages are less reliable than NVIDIA's own installer.
  - CUDA Toolkit and Visual Studio Build Tools are included below because
    they ARE reliably winget-installable, but they are multi-GB downloads
    and need Administrator rights. Run with -SkipHeavy to install only the
    small, fast tools (git/python/uv/node/cmake/ffmpeg) and do those two by
    hand instead.

Re-run any time - winget skips packages that are already installed.
#>

param(
    [switch]$SkipHeavy
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "winget was not found. Install 'App Installer' from the Microsoft Store, then re-run this script." -ForegroundColor Red
    Write-Host "https://apps.microsoft.com/detail/9nblggh4nns1"
    exit 1
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not running as Administrator - some packages (Build Tools, CUDA) may fail to install machine-wide." -ForegroundColor Yellow
    Write-Host "Re-run this script from an elevated PowerShell if that happens." -ForegroundColor Yellow
}

function Install-Winget($id, $overrideArgs) {
    Write-Host ""
    Write-Host "== $id ==" -ForegroundColor Cyan
    $installed = winget list --id $id --accept-source-agreements 2>$null | Select-String -SimpleMatch $id
    if ($installed) {
        Write-Host "Already installed, skipping."
        return
    }
    $wingetArgs = @("install", "--id", $id, "--silent", "--accept-package-agreements", "--accept-source-agreements")
    if ($overrideArgs) { $wingetArgs += @("--override", $overrideArgs) }
    winget @wingetArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "winget could not install $id automatically - install it by hand from its official site." -ForegroundColor Yellow
    }
}

Write-Host "=== Small, fast tools ===" -ForegroundColor Green
Install-Winget "Git.Git"
Install-Winget "Python.Python.3.12"
Install-Winget "astral-sh.uv"
Install-Winget "OpenJS.NodeJS.LTS"
Install-Winget "Kitware.CMake"
Install-Winget "Gyan.FFmpeg"

if ($SkipHeavy) {
    Write-Host ""
    Write-Host "Skipping Visual Studio Build Tools and CUDA Toolkit (-SkipHeavy passed)." -ForegroundColor Yellow
    Write-Host "Install them by hand:"
    Write-Host "  https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022 (C++ workload)"
    Write-Host "  https://developer.nvidia.com/cuda-downloads"
} else {
    Write-Host ""
    Write-Host "=== Heavy tools (multi-GB, needs Administrator) ===" -ForegroundColor Green
    Install-Winget "Microsoft.VisualStudio.2022.BuildTools" "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
    Install-Winget "Nvidia.CUDA"
    Write-Host "If either of these failed, install manually (exact winget package IDs can drift):" -ForegroundColor Yellow
    Write-Host "  https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022 (C++ workload)"
    Write-Host "  https://developer.nvidia.com/cuda-downloads"
}

Write-Host ""
Write-Host "=== Not automated - do this yourself ===" -ForegroundColor Green
Write-Host "  NVIDIA GPU driver: https://www.nvidia.com/drivers"

Write-Host ""
Write-Host "Done. Close this terminal and open a NEW one (so PATH picks up what just" -ForegroundColor Cyan
Write-Host "installed), then run setup_models.bat." -ForegroundColor Cyan
