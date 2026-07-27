# guard_windows.ps1 - Ensure WireGuard network + firewall + SSH are always up
$ErrorActionPreference = "SilentlyContinue"

# 1. Set wg0 to Private network
$wg = Get-NetConnectionProfile -InterfaceAlias "wg0"
if ($wg.NetworkCategory -ne "Private") {
    Set-NetConnectionProfile -InterfaceAlias "wg0" -NetworkCategory Private
    Write-Output "$(Get-Date): Set wg0 to Private"
}

# 2. Ensure firewall rules exist
$rules = @(
    @{Name="SSH (WireGuard)"; Port=22; Proto="TCP"},
    @{Name="TTS Worker (WireGuard)"; Port=8001; Proto="TCP"},
    @{Name="RDP (WireGuard)"; Port=3389; Proto="TCP"}
)

foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -LocalPort $r.Port -Protocol $r.Proto -Action Allow -Profile Any
        Write-Output "$(Get-Date): Created firewall rule $($r.Name)"
    }
}

# 3. Ensure SSH service is running
$sshd = Get-Service sshd -ErrorAction SilentlyContinue
if ($sshd.Status -ne "Running") {
    Start-Service sshd
    Set-Service -Name sshd -StartupType Automatic
    Write-Output "$(Get-Date): Started sshd"
}

# 4. Check WireGuard tunnel
$wgAdapter = Get-NetAdapter -Name "wg0" -ErrorAction SilentlyContinue
if ($wgAdapter.Status -ne "Up") {
    Write-Output "$(Get-Date): wg0 is DOWN"
    $wgProcess = Get-Process "wireguard" -ErrorAction SilentlyContinue
    if (-not $wgProcess) {
        Start-Process "C:\Program Files\WireGuard\wireguard.exe"
        Write-Output "$(Get-Date): Started WireGuard process"
    }
}
