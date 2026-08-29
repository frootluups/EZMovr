# Sign the EZMovr executable with a self-signed code-signing certificate and
# make that certificate trusted on THIS machine so Windows Smart App Control
# lets the app run.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\sign-dev.ps1
#
# Note: The certificate is stored in your user store. Run this once per build.
#       Not for public distribution, only makes the app trusted locally.

param(
    [string]$ExePath = (Join-Path $PSScriptRoot '..\dist\EZMovr.exe')
)

$ErrorActionPreference = 'Stop'
$certName = 'EZMovr Dev Cert'

# ---- 1. Create (or reuse) a code-signing cert in the current user's store ----
$cert = Get-ChildItem 'Cert:\CurrentUser\My' |
    Where-Object { $_.Subject -like "*$certName*" -and $_.HasPrivateKey } |
    Select-Object -First 1

if (-not $cert) {
    Write-Host 'Creating self-signed code-signing certificate...'
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject "CN=$certName" `
        -CertStoreLocation 'Cert:\CurrentUser\My' `
        -KeyUsage DigitalSignature `
        -KeyExportPolicy Exportable `
        -NotAfter (Get-Date).AddYears(3)
}

# ---- 2. Export + import as trusted (user scope, no admin needed) ----
$cerPath = Join-Path $env:TEMP 'ezmovr-dev-cert.cer'
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null

Import-Certificate -FilePath $cerPath -CertStoreLocation 'Cert:\CurrentUser\Root' | Out-Null
Import-Certificate -FilePath $cerPath -CertStoreLocation 'Cert:\CurrentUser\TrustedPublisher' | Out-Null
Remove-Item $cerPath -ErrorAction SilentlyContinue

# ---- 3. Sign the executable ----
if (-not (Test-Path $ExePath)) {
    throw "Executable not found: $ExePath. Run build.bat first."
}

$sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -HashAlgorithm SHA256
if ($sig.Status -eq 'Valid') {
    Write-Host "Signed OK: $ExePath"
    Write-Host 'Windows Smart App Control should now allow EZMovr on this machine.'
} else {
    Write-Host "Signing status: $($sig.Status)" -ForegroundColor Yellow
}