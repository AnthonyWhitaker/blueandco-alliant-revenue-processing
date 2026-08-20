#Requires -Version 5.1
<#
.SYNOPSIS
    Deploy the Alliant Revenue Processing Engine to Azure.

.DESCRIPTION
    Steps:
      1. Creates (or reuses) resource group
      2. Creates (or reuses) Azure Container Registry
      3. Builds and pushes the Docker image
      4. Provisions infrastructure via Bicep (SQL Database + Container App)
      5. Bootstraps the database schema + seeds supplier profiles

.NOTES
    Prerequisites:
      - Azure CLI installed and logged in  (`az login`)
      - Docker Desktop installed and running
      - Run from revenue_engine/ directory
      - Set $env:SQL_ADMIN_PASSWORD before running

    To share infrastructure with alliant-purchasing-solutions, set:
      $EXISTING_SQL_SERVER    — name of the existing SQL Server
      $EXISTING_ENVIRONMENT   — name of the existing Container Apps environment
#>

$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------
$RESOURCE_GROUP       = 'alliant-rg'
$LOCATION             = 'eastus'
$NAME_PREFIX          = 'rev'
$EXISTING_SQL_SERVER  = ''   # e.g. 'pdn-sql-abc123' to share purchasing-solutions SQL server
$EXISTING_ENVIRONMENT = ''   # e.g. 'pdn-env' to share purchasing-solutions Container Apps env

$ACR_SUFFIX = -join ((1..6) | ForEach-Object { Get-Random -Minimum 0 -Maximum 10 })
$ACR_NAME   = "revreg$ACR_SUFFIX"

function Write-Step {
    param([int]$n, [int]$total, [string]$msg)
    Write-Host ''
    Write-Host "[$n/$total] $msg" -ForegroundColor Cyan
}

# -----------------------------------------------------------------------
# PRE-FLIGHT
# -----------------------------------------------------------------------
Write-Host ''
Write-Host '=========================================================' -ForegroundColor Green
Write-Host ' Alliant Revenue Engine - Azure Deployment'               -ForegroundColor Green
Write-Host '=========================================================' -ForegroundColor Green
Write-Host " Resource group : $RESOURCE_GROUP"
Write-Host " Location       : $LOCATION"
Write-Host " Name prefix    : $NAME_PREFIX"
Write-Host ''

if (-not (Test-Path 'infra/main.bicep')) {
    throw 'Run this script from the revenue_engine/ directory.'
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI not found. Install from https://aka.ms/installazurecliwindows then run: az login'
}

$AZ_ACCOUNT = az account show 2>$null | ConvertFrom-Json
if (-not $AZ_ACCOUNT) {
    throw 'Not logged in to Azure. Run: az login'
}
Write-Host "Azure account  : $($AZ_ACCOUNT.user.name)"

$SQL_LOGIN = 'pdnadmin'
if (-not $env:SQL_ADMIN_PASSWORD) {
    throw "Set the SQL_ADMIN_PASSWORD environment variable first. `$env:SQL_ADMIN_PASSWORD='<strong-password>'"
}
$SQL_PASSWORD = $env:SQL_ADMIN_PASSWORD

# -----------------------------------------------------------------------
# STEP 1 - Resource group
# -----------------------------------------------------------------------
Write-Step 1 6 'Resource group'
$rgExists = az group exists --name $RESOURCE_GROUP
if ($rgExists -eq 'false') {
    Write-Host "Creating resource group '$RESOURCE_GROUP'..."
    az group create --name $RESOURCE_GROUP --location $LOCATION | Out-Null
} else {
    Write-Host "Resource group '$RESOURCE_GROUP' already exists."
}

# -----------------------------------------------------------------------
# STEP 2 - Azure Container Registry
# -----------------------------------------------------------------------
Write-Step 2 6 'Azure Container Registry'

$EXISTING_ACR = az acr list --resource-group $RESOURCE_GROUP --query '[0].name' --output tsv 2>$null
if ($EXISTING_ACR -and $EXISTING_ACR -ne '') {
    $ACR_NAME = $EXISTING_ACR
    Write-Host "Reusing existing ACR: $ACR_NAME"
} else {
    Write-Host "Creating ACR '$ACR_NAME'..."
    az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic --admin-enabled true | Out-Null
}

$ACR_LOGIN_SERVER = az acr show --name $ACR_NAME --query loginServer --output tsv
$ACR_USERNAME     = az acr credential show --name $ACR_NAME --query username --output tsv
$ACR_PASSWORD     = az acr credential show --name $ACR_NAME --query 'passwords[0].value' --output tsv
Write-Host "Login server: $ACR_LOGIN_SERVER"

# -----------------------------------------------------------------------
# STEP 3 - Build and push Docker image
# -----------------------------------------------------------------------
Write-Step 3 6 'Build and push Docker image'

$APP_IMAGE = "${ACR_LOGIN_SERVER}/revenue-engine:latest"
Write-Host "Building image $APP_IMAGE in Azure..."
az acr build --registry $ACR_NAME --image 'revenue-engine:latest' --file Dockerfile .

# -----------------------------------------------------------------------
# STEP 4 - Bicep deployment
# -----------------------------------------------------------------------
Write-Step 4 6 'Azure infrastructure via Bicep'

$PARAMS_OBJ = @{
    '$schema'      = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters     = @{
        namePrefix              = @{ value = $NAME_PREFIX }
        sqlAdminLogin           = @{ value = $SQL_LOGIN }
        sqlAdminPassword        = @{ value = $SQL_PASSWORD }
        appImage                = @{ value = $APP_IMAGE }
        registryServer          = @{ value = $ACR_LOGIN_SERVER }
        registryUsername         = @{ value = $ACR_USERNAME }
        registryPassword        = @{ value = $ACR_PASSWORD }
        existingSqlServerName   = @{ value = $EXISTING_SQL_SERVER }
        existingEnvironmentName = @{ value = $EXISTING_ENVIRONMENT }
    }
}

$TEMP_PARAMS = [System.IO.Path]::Combine(
    [System.IO.Path]::GetTempPath(),
    "rev-params-$([System.Guid]::NewGuid()).json"
)
$PARAMS_OBJ | ConvertTo-Json -Depth 5 | Out-File -FilePath $TEMP_PARAMS -Encoding utf8

try {
    $BICEP_OUT = az deployment group create `
        --resource-group $RESOURCE_GROUP `
        --template-file infra/main.bicep `
        --parameters "@$TEMP_PARAMS" `
        --output json | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) { throw "Bicep deployment failed." }
} finally {
    Remove-Item $TEMP_PARAMS -Force -ErrorAction SilentlyContinue
}

$APP_URL   = $BICEP_OUT.properties.outputs.appUrl.value
$SQL_FQDN  = $BICEP_OUT.properties.outputs.sqlServerFqdn.value
$DB_NAME   = $BICEP_OUT.properties.outputs.databaseName.value
Write-Host "Infrastructure provisioned."
Write-Host "App URL: $APP_URL"

# -----------------------------------------------------------------------
# STEP 5 - Bootstrap database
# -----------------------------------------------------------------------
Write-Step 5 6 'Database bootstrap + seed'

$SQL_CONN = "Driver={ODBC Driver 18 for SQL Server};Server=tcp:${SQL_FQDN},1433;Database=${DB_NAME};Uid=${SQL_LOGIN};Pwd=${SQL_PASSWORD};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30"

Write-Host 'Adding temporary SQL firewall rule...'
$LOCAL_IP = (Invoke-WebRequest -Uri 'https://api.ipify.org' -UseBasicParsing).Content.Trim()
$SQL_SERVER_NAME = az sql server list --resource-group $RESOURCE_GROUP --query '[0].name' --output tsv

az sql server firewall-rule create `
    --resource-group $RESOURCE_GROUP `
    --server $SQL_SERVER_NAME `
    --name 'deploy-bootstrap-temp' `
    --start-ip-address $LOCAL_IP `
    --end-ip-address $LOCAL_IP | Out-Null

$env:AZURE_SQL_CONNECTION_STRING = $SQL_CONN
try {
    Write-Host 'Running schema init + seed...'
    docker run --rm `
        -e AZURE_SQL_CONNECTION_STRING `
        $APP_IMAGE `
        python -c "from engine.db import init_db; init_db(); print('Schema OK')"

    docker run --rm `
        -e AZURE_SQL_CONNECTION_STRING `
        $APP_IMAGE `
        python scripts/seed_guerbet.py

    docker run --rm `
        -e AZURE_SQL_CONNECTION_STRING `
        $APP_IMAGE `
        python scripts/seed_tier_ab_suppliers.py

    Write-Host 'Database bootstrapped.'
} finally {
    $env:AZURE_SQL_CONNECTION_STRING = $null
    az sql server firewall-rule delete `
        --resource-group $RESOURCE_GROUP `
        --server $SQL_SERVER_NAME `
        --name 'deploy-bootstrap-temp' `
        --yes 2>$null | Out-Null
}

# -----------------------------------------------------------------------
# STEP 6 - Summary
# -----------------------------------------------------------------------
Write-Step 6 6 'Deployment complete'

Write-Host ''
Write-Host '=========================================================' -ForegroundColor Green
Write-Host ' DEPLOYMENT COMPLETE'                                       -ForegroundColor Green
Write-Host '=========================================================' -ForegroundColor Green
Write-Host ''
Write-Host "  App URL        : $APP_URL"
Write-Host "  SQL Server     : $SQL_FQDN"
Write-Host "  Database       : $DB_NAME"
Write-Host "  ACR            : $ACR_LOGIN_SERVER"
Write-Host ''
Write-Host 'SAVE THESE CREDENTIALS:' -ForegroundColor Yellow
Write-Host "  SQL login      : $SQL_LOGIN"
Write-Host "  SQL password   : (from `$env:SQL_ADMIN_PASSWORD)"
Write-Host "  ACR username   : $ACR_USERNAME"
Write-Host ''
Write-Host 'NOTE: Restrict access via IP allowlist or Easy Auth before sharing.' -ForegroundColor Yellow
Write-Host ''
