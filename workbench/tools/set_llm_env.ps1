$ErrorActionPreference = 'Stop'

Write-Host 'Configure the DeepSeek key for the current Windows user.'
$secureKey = Read-Host 'DeepSeek API Key (hidden input)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw 'API Key cannot be empty.'
    }
    [Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', $plainKey, 'User')
    Write-Host 'DEEPSEEK_API_KEY saved. Restart the Demo if it is already running.'
}
finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $plainKey = $null
}
