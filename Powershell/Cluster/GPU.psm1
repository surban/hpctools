$ErrorActionPreference = 'Stop'

$lockdir = Join-Path $env:ProgramData "GPULock"
$RefreshInterval = 5

$LockFile = $null
$LockJob = $null

$NvidiaSMI = 'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe'

function Get-GPU
{
    if (-not (Test-Path $NvidiaSMI))  { return $null }

    try
    {
        $out = [xml]$(& $NvidiaSMI -q -x)
        $gpu = $out.nvidia_smi_log.gpu
        $object = New-Object –TypeName PSObject
        $object | Add-Member –MemberType NoteProperty –Name Name –Value $gpu.product_name 
        $str = $gpu.fb_memory_usage.total 
        $object | Add-Member –MemberType NoteProperty –Name TotalMemory –Value ($str.Substring(0,$str.Length-4) -as [int])
        $str = $gpu.fb_memory_usage.free
        $object | Add-Member –MemberType NoteProperty –Name FreeMemory –Value ($str.Substring(0,$str.Length-4) -as [int])   
        return $object
    }
    catch
    {
        return $null
    }
}


function Prepare-LockDir
{
    if (-not (Test-Path $lockdir)) { New-Item -ItemType Directory $lockdir | Out-Null }
}

function Get-GPULocks
{
    Prepare-LockDir
    $locks = @{}
    foreach ($file in (Get-ChildItem -File $lockdir))
    {
        $interval = $(Get-Date).ToUniversalTime() - $file.LastWriteTimeUtc
        if ($interval.TotalSeconds -lt ($RefreshInterval + 1))
        {
            $lockname = (Get-Content $file.FullName | Out-String).Trim()
            if ($locks.ContainsKey($lockname)) 
                { $locks[$lockname] += 1 }
            else
                { $locks[$lockname] = 1 }
        }
        else
        {
            Remove-Item -Force $file.FullName -ErrorAction Ignore
        }
    }
    $locks
}

function Lock-GPU($lockname)
{
    Prepare-LockDir
    if ($global:LockFile -ne $null) { throw "GPU already locked in this session" }

    $rndname = [System.IO.Path]::GetRandomFileName()
    $filename = [Environment]::UserName + "_" + $rndname
    $filepath = Join-Path $lockdir $filename

    $global:LockFile = New-Item -ItemType File -Path $filepath -Value $lockname

    $Acl = Get-Acl $global:LockFile
    $Ar = New-Object  System.Security.AccessControl.FileSystemAccessRule("Users", 
                                                                         [System.Security.AccessControl.FileSystemRights]::Delete, 
                                                                         [System.Security.AccessControl.AccessControlType]::Allow)
    $Acl.SetAccessRule($Ar)
    Set-Acl -Path $global:LockFile $Acl

    $global:LockJob = Start-Job -ArgumentList $filepath, $RefreshInterval -ScriptBlock `
    {
        $filepath = $args[0]
        $RefreshInterval = $args[1]

        while ($true)
        {
            $LockFile = Get-Item $filepath
            # echo "Setting LastWriteTime on $($LockFile.Name)"
            $LockFile.LastWriteTime = Get-Date
            Start-Sleep -Seconds $RefreshInterval
        }
    }
}

function Unlock-GPU
{
    Prepare-LockDir
    if ($global:LockFile -eq $null) { throw "GPU not locked in this session" }

    Stop-Job $global:LockJob
    Remove-Job $global:LockJob
    Remove-Item $global:LockFile
    $global:LockJob = $null
    $global:LockFile = $null
}


function TryLock-GPU
{
    [CmdletBinding()]
    Param ([Parameter(Mandatory=$False)] [string] $ConcurrentName = $null,
           [Parameter(Mandatory=$False)] [int] $MaxTasks = 1)

    if ($ConcurrentName -eq $null) 
    { 
        $ConcurrentName = "none"
        #if (Test-Path Env:\CCP_JOBID) 
        #{ 
        #    $ConcurrentName = $env:CCP_JOBID 
        #}
        #else
        #{
        #    $ConcurrentName = "none"
        #}
    }

    $locks = Get-GPULocks
    if (($locks.Count -eq 0) -or
        ($locks.Count -eq 1 -and $locks.Keys[0] -eq $ConcurrentName -and $locks[$ConcurrentName] -lt $MaxTasks))
    {
        Lock-GPU $ConcurrentName
        return $True
    }
    else
    {
        return $False
    }
}


function Is-GPUUseful
{
    $gpu = Get-GPU
    if ($gpu -eq $null) { return $False }
    if ($gpu.Name -match ' K600') { return $False }
    if ($gpu.FreeMemory -lt 1024) { return $False }
    return $True
}

function Start-OnBestDevice
{
    if ((Is-GPUUseful) -and (TryLock-GPU))
    {
        $env:COMPUTE_DEVICE = "gpu"
        $env:THEANO_FLAGS = "device=gpu,force_device=True"
        #echo "Using GPU"
    }
    else
    {
        $env:COMPUTE_DEVICE = "cpu"
        $env:THEANO_FLAGS = "device=cpu,force_device=True"
        #echo "Using CPU"
    }

    $exec = $args[0]
    $params = $args[1..999]
    $ErrorActionPreference = 'Continue'
    & $exec @params 
    $ErrorActionPreference = 'Stop'
    $exitcode = $LASTEXITCODE

    if ($env:COMPUTE_DEVICE -eq "gpu")
    {
        Unlock-GPU
    }

    #return $exitcode
}


