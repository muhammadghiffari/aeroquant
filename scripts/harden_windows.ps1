[CmdletBinding()]
param(
    [switch]$ApplyRdpFirewall
)

$ErrorActionPreference = "Stop"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window. No settings were changed."
}

# Keep RDP authentication on the Windows NLA path.
$rdpKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
Set-ItemProperty -LiteralPath $rdpKey -Name UserAuthentication -Type DWord -Value 1

# Make repeated password guessing expensive without changing any password.
& net.exe accounts /lockoutthreshold:5 /lockoutduration:15 /lockoutwindow:15 | Out-Null

# The primary worker must stay alive while the laptop is on AC power.
& powercfg.exe /change standby-timeout-ac 0

if ($ApplyRdpFirewall) {
    $tailscaleAddress = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "100.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        ForEach-Object { $_.IPAddress; break }
    if ([string]::IsNullOrWhiteSpace($tailscaleAddress)) {
        throw "No Tailscale IPv4 address found; firewall was not changed. Install and authenticate Tailscale first."
    }
    Get-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction Stop |
        Set-NetFirewallRule -Enabled True -Profile Domain,Private -RemoteAddress "100.64.0.0/10"
}

$nla = (Get-ItemProperty -LiteralPath $rdpKey -Name UserAuthentication).UserAuthentication
Write-Output "NLA=$nla"
Write-Output "AC_sleep_timeout=0"
Write-Output "RDP_firewall_restricted=$ApplyRdpFirewall"
