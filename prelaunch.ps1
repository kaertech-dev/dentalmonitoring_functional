Add-Type -AssemblyName System.Runtime.WindowsRuntime

# Helper to await WinRT async operations  
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {  
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and  
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'  
})[0]  

Function Await($WinRtTask, $ResultType) {  
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)  
    $netTask = $asTask.Invoke($null, @($WinRtTask))  
    $netTask.Wait(-1) | Out-Null  
    $netTask.Result  
}

# # Get the tethering manager for the current internet connection  
# $connectionProfile = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]::GetInternetConnectionProfile()
# $tetheringManager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]::CreateFromConnectionProfile($connectionProfile)
# $hotspotConfig = $tetheringManager.GetCurrentAccessPointConfiguration()
# $hotspotConfig.Band = 1 # set to 2.4GHz
# $tetheringManager.ConfigureAccessPointAsync($hotspotConfig)

# # Write the Bench SSID and Password as Env Variables
# $def_ssid = $hotspotConfig.Ssid
# $def_pw = $hotspotConfig.Passphrase
# if (Test-Path -Path ".venv\Scripts\activate.ps1") {
#     $env:BENCH_SSID = $def_ssid
#     $env:BENCH_PW = $def_pw
# } else {
#     [System.Environment]::SetEnvironmentVariable("BENCH_SSID", $def_ssid, "User")
#     [System.Environment]::SetEnvironmentVariable("BENCH_PW", $def_pw, "User")
# }

# # Enable Hotspot
# if ($tetheringManager.TetheringOperationalState -ne 1) {
#     $tetheringManager.DisableNoConnectionsTimeout()
#     Await ($tetheringManager.StopTetheringAsync())
#     Await ($tetheringManager.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
# }

# Enable Bluetooth
$radio  = [Windows.Devices.Radios.Radio]
$rList  = [System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]
$status = [Windows.Devices.Radios.RadioAccessStatus]
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]
Await ($radio::RequestAccessAsync()) ($status)
$radios = Await ($radio::GetRadiosAsync()) ($rList)

$bt   = $radios | Where-Object {$_.Kind -eq 'Bluetooth'}
Await ($bt.SetStateAsync('On')) ($status)

netsh wlan add profile filename=".\wifi-profile.xml"