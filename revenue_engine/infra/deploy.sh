#!/usr/bin/env bash
#
# Deploy the Alliant Revenue Processing Engine to Azure.
#
# Steps:
#   1. Creates (or reuses) resource group
#   2. Creates (or reuses) Azure Container Registry
#   3. Builds and pushes the Docker image
#   4. Provisions infrastructure via Bicep (SQL Database + Container App)
#   5. Bootstraps the database schema + seeds supplier profiles
#
# Prerequisites:
#   - Azure CLI installed and logged in  (az login)
#   - Docker Desktop installed and running
#   - Run from revenue_engine/ directory
#   - Set SQL_ADMIN_PASSWORD before running
#
# To share infrastructure with alliant-purchasing-solutions, set:
#   EXISTING_SQL_SERVER    - name of the existing SQL Server
#   EXISTING_ENVIRONMENT   - name of the existing Container Apps environment

set -euo pipefail

# -----------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------
RESOURCE_GROUP="${RESOURCE_GROUP:-alliant-rg}"
LOCATION="${LOCATION:-eastus}"
NAME_PREFIX="${NAME_PREFIX:-rev}"
EXISTING_SQL_SERVER="${EXISTING_SQL_SERVER:-}"    # e.g. "pdn-sql-abc123" to share purchasing-solutions SQL server
EXISTING_ENVIRONMENT="${EXISTING_ENVIRONMENT:-}"  # e.g. "pdn-env" to share purchasing-solutions Container Apps env

ACR_SUFFIX=$(printf "%06d" $((RANDOM % 1000000)))
ACR_NAME="${ACR_NAME:-revreg${ACR_SUFFIX}}"

step() {
    echo ""
    echo "[$1/6] $2"
}

# -----------------------------------------------------------------------
# PRE-FLIGHT
# -----------------------------------------------------------------------
echo ""
echo "========================================================="
echo " Alliant Revenue Engine - Azure Deployment"
echo "========================================================="
echo " Resource group : $RESOURCE_GROUP"
echo " Location       : $LOCATION"
echo " Name prefix    : $NAME_PREFIX"
echo ""

if [ ! -f "infra/main.bicep" ]; then
    echo "ERROR: Run this script from the revenue_engine/ directory." >&2
    exit 1
fi

if ! command -v az >/dev/null 2>&1; then
    echo "ERROR: Azure CLI not found. Install it, then run: az login" >&2
    exit 1
fi

if ! AZ_ACCOUNT_NAME=$(az account show --query user.name -o tsv 2>/dev/null); then
    echo "ERROR: Not logged in to Azure. Run: az login" >&2
    exit 1
fi
echo "Azure account  : $AZ_ACCOUNT_NAME"

SQL_LOGIN="pdnadmin"
if [ -z "${SQL_ADMIN_PASSWORD:-}" ]; then
    echo "ERROR: Set the SQL_ADMIN_PASSWORD environment variable first: export SQL_ADMIN_PASSWORD='<strong-password>'" >&2
    exit 1
fi
SQL_PASSWORD="$SQL_ADMIN_PASSWORD"

# -----------------------------------------------------------------------
# STEP 1 - Resource group
# -----------------------------------------------------------------------
step 1 "Resource group"
RG_EXISTS=$(az group exists --name "$RESOURCE_GROUP")
if [ "$RG_EXISTS" = "false" ]; then
    echo "Creating resource group '$RESOURCE_GROUP'..."
    az group create --name "$RESOURCE_GROUP" --location "$LOCATION" >/dev/null
else
    echo "Resource group '$RESOURCE_GROUP' already exists."
fi

# -----------------------------------------------------------------------
# STEP 2 - Azure Container Registry
# -----------------------------------------------------------------------
step 2 "Azure Container Registry"

EXISTING_ACR=$(az acr list --resource-group "$RESOURCE_GROUP" --query '[0].name' -o tsv 2>/dev/null || true)
if [ -n "$EXISTING_ACR" ]; then
    ACR_NAME="$EXISTING_ACR"
    echo "Reusing existing ACR: $ACR_NAME"
else
    echo "Creating ACR '$ACR_NAME'..."
    az acr create --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --sku Basic --admin-enabled true >/dev/null
fi

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)
echo "Login server: $ACR_LOGIN_SERVER"

echo "Logging local Docker into ACR..."
echo "$ACR_PASSWORD" | docker login "$ACR_LOGIN_SERVER" -u "$ACR_USERNAME" --password-stdin >/dev/null 2>&1 || echo "WARNING: docker login failed — bootstrap step may not be able to pull the image"

# -----------------------------------------------------------------------
# STEP 3 - Build and push Docker image
# -----------------------------------------------------------------------
step 3 "Build and push Docker image"

APP_IMAGE="${ACR_LOGIN_SERVER}/revenue-engine:latest"
echo "Building image $APP_IMAGE in Azure..."
az acr build --registry "$ACR_NAME" --image "revenue-engine:latest" --file Dockerfile .

# -----------------------------------------------------------------------
# STEP 4 - Bicep deployment
# -----------------------------------------------------------------------
step 4 "Azure infrastructure via Bicep"

TEMP_PARAMS=$(mktemp /tmp/rev-params-XXXXXX.json)
cat > "$TEMP_PARAMS" <<EOF
{
  "\$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "namePrefix": { "value": "$NAME_PREFIX" },
    "sqlAdminLogin": { "value": "$SQL_LOGIN" },
    "sqlAdminPassword": { "value": "$SQL_PASSWORD" },
    "appImage": { "value": "$APP_IMAGE" },
    "registryServer": { "value": "$ACR_LOGIN_SERVER" },
    "registryUsername": { "value": "$ACR_USERNAME" },
    "registryPassword": { "value": "$ACR_PASSWORD" },
    "existingSqlServerName": { "value": "$EXISTING_SQL_SERVER" },
    "existingEnvironmentName": { "value": "$EXISTING_ENVIRONMENT" }
  }
}
EOF

cleanup_params() { rm -f "$TEMP_PARAMS"; }
trap cleanup_params EXIT

BICEP_OUT=$(az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file infra/main.bicep \
    --parameters "@$TEMP_PARAMS" \
    --output json)

cleanup_params
trap - EXIT

APP_URL=$(echo "$BICEP_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['properties']['outputs']['appUrl']['value'])")
SQL_FQDN=$(echo "$BICEP_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['properties']['outputs']['sqlServerFqdn']['value'])")
DB_NAME=$(echo "$BICEP_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['properties']['outputs']['databaseName']['value'])")

echo "Infrastructure provisioned."
echo "App URL: $APP_URL"

# -----------------------------------------------------------------------
# STEP 5 - Bootstrap database
# -----------------------------------------------------------------------
step 5 "Database bootstrap + seed"

SQL_CONN="Driver={ODBC Driver 18 for SQL Server};Server=tcp:${SQL_FQDN},1433;Database=${DB_NAME};Uid=${SQL_LOGIN};Pwd=${SQL_PASSWORD};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30"

echo "Adding temporary SQL firewall rule..."
LOCAL_IP=$(curl -s https://api.ipify.org)
SQL_SERVER_NAME=$(az sql server list --resource-group "$RESOURCE_GROUP" --query '[0].name' -o tsv)

az sql server firewall-rule create \
    --resource-group "$RESOURCE_GROUP" \
    --server "$SQL_SERVER_NAME" \
    --name "deploy-bootstrap-temp" \
    --start-ip-address "$LOCAL_IP" \
    --end-ip-address "$LOCAL_IP" >/dev/null

cleanup_firewall() {
    az sql server firewall-rule delete \
        --resource-group "$RESOURCE_GROUP" \
        --server "$SQL_SERVER_NAME" \
        --name "deploy-bootstrap-temp" \
        --yes >/dev/null 2>&1 || true
}
trap cleanup_firewall EXIT

echo "Running schema init + seed..."
docker run --rm \
    --entrypoint python \
    -e AZURE_SQL_CONNECTION_STRING="$SQL_CONN" \
    "$APP_IMAGE" \
    -c "from engine.db import init_db; init_db(); print('Schema OK')"

docker run --rm \
    --entrypoint python \
    -e AZURE_SQL_CONNECTION_STRING="$SQL_CONN" \
    "$APP_IMAGE" \
    scripts/seed_guerbet.py

docker run --rm \
    --entrypoint python \
    -e AZURE_SQL_CONNECTION_STRING="$SQL_CONN" \
    "$APP_IMAGE" \
    scripts/seed_tier_ab_suppliers.py

echo "Database bootstrapped."

cleanup_firewall
trap - EXIT

# -----------------------------------------------------------------------
# STEP 6 - Summary
# -----------------------------------------------------------------------
step 6 "Deployment complete"

echo ""
echo "========================================================="
echo " DEPLOYMENT COMPLETE"
echo "========================================================="
echo ""
echo "  App URL        : $APP_URL"
echo "  SQL Server     : $SQL_FQDN"
echo "  Database       : $DB_NAME"
echo "  ACR            : $ACR_LOGIN_SERVER"
echo ""
echo "SAVE THESE CREDENTIALS:"
echo "  SQL login      : $SQL_LOGIN"
echo "  SQL password   : (from \$SQL_ADMIN_PASSWORD)"
echo "  ACR username   : $ACR_USERNAME"
echo ""
echo "NOTE: Restrict access via IP allowlist or Easy Auth before sharing."
echo ""
