#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$systemDrive = (Get-CimInstance Win32_OperatingSystem).SystemDrive.TrimEnd(':')
$partition = Get-Partition -DriveLetter $systemDrive
$supported = Get-PartitionSupportedSize `
    -DiskNumber $partition.DiskNumber `
    -PartitionNumber $partition.PartitionNumber

# Avoid needless writes and tiny alignment-only changes.
if ($supported.SizeMax -gt ($partition.Size + 1GB)) {
    Resize-Partition `
        -DiskNumber $partition.DiskNumber `
        -PartitionNumber $partition.PartitionNumber `
        -Size $supported.SizeMax
}

