@{
    Severity     = @('Error', 'Warning')
    ExcludeRules = @(
        'PSAvoidUsingWriteHost',    # output is read over WinRM/SSH by ev2ahv; Write-Host is intended
        'PSReviewUnusedParameter'   # VirtioPath is read from script scope inside functions
    )
}
