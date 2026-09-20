$ErrorActionPreference = 'Stop'

Write-Host 'Configure Tencent ASR variables for the current Windows user.'
$secretId = Read-Host 'SecretId'
$secureSecretKey = Read-Host 'SecretKey (hidden input)' -AsSecureString
$bucket = Read-Host 'Full COS bucket name (example: lingshi-asr-1250000000)'
$region = Read-Host 'COS region code (example: ap-shanghai)'

if ([string]::IsNullOrWhiteSpace($secretId) -or
    [string]::IsNullOrWhiteSpace($bucket) -or
    [string]::IsNullOrWhiteSpace($region)) {
    throw 'No input can be empty.'
}

$secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecretKey)
try {
    $secretKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    if ([string]::IsNullOrWhiteSpace($secretKey)) {
        throw 'SecretKey cannot be empty.'
    }
    [Environment]::SetEnvironmentVariable('TENCENTCLOUD_SECRET_ID', $secretId.Trim(), 'User')
    [Environment]::SetEnvironmentVariable('TENCENTCLOUD_SECRET_KEY', $secretKey, 'User')
    [Environment]::SetEnvironmentVariable('TENCENT_COS_BUCKET', $bucket.Trim(), 'User')
    [Environment]::SetEnvironmentVariable('TENCENT_COS_REGION', $region.Trim(), 'User')
}
finally {
    if ($null -ne $secretPointer) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    }
    $secretKey = $null
}

Write-Host 'Configuration saved. Close and reopen PowerShell, then run:'
Write-Host 'python tools/tencent_asr_smoke.py check'
