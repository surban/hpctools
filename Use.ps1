$mydir = Split-Path -parent $PSCommandPath
$env:PYTHONPATH = $env:PYTHONPATH + ";$mydir\Python"
$env:PSModulePath = $env:PSModulePath + ";$mydir\Powershell"


