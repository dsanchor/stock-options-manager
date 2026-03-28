#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Provision Azure CosmosDB for Stock Options Manager
#
# Usage:
#   bash scripts/provision_cosmosdb.sh
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - Sufficient permissions to create resources in the target subscription
#
# What this script does:
#   1. Creates a resource group (if it doesn't exist)
#   2. Creates a CosmosDB account (serverless by default)
#   3. Creates the "stock-options-manager" database
#   4. Creates the "symbols" container with partition key /symbol
#   5. Applies custom indexing policy (index query fields, exclude large blobs)
#   6. Retrieves and prints the connection endpoint and primary key
#
# The script is idempotent — safe to re-run. Existing resources are not modified.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Variables (customize these) ──────────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-stock-options-manager}"
LOCATION="${LOCATION:-eastus}"
COSMOSDB_ACCOUNT="${COSMOSDB_ACCOUNT:-cosmos-stock-options}"
DATABASE_NAME="${DATABASE_NAME:-stock-options-manager}"
CONTAINER_NAME="${CONTAINER_NAME:-symbols}"

echo "═══════════════════════════════════════════════════════════════"
echo "  CosmosDB Provisioning — Stock Options Manager"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Resource Group:   $RESOURCE_GROUP"
echo "  Location:         $LOCATION"
echo "  CosmosDB Account: $COSMOSDB_ACCOUNT"
echo "  Database:         $DATABASE_NAME"
echo "  Container:        $CONTAINER_NAME"
echo ""

# ── 1. Create Resource Group ─────────────────────────────────────────────────
echo "▶ Creating resource group '$RESOURCE_GROUP'..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --only-show-errors \
  -o none
echo "  ✓ Resource group ready"

# ── 2. Create CosmosDB Account ───────────────────────────────────────────────
# Option A (default): Serverless — pay-per-request, best for dev/low-traffic
echo "▶ Creating CosmosDB account '$COSMOSDB_ACCOUNT' (serverless)..."
az cosmosdb create \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --kind GlobalDocumentDB \
  --capabilities EnableServerless \
  --default-consistency-level Session \
  --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
  --only-show-errors \
  -o none

# Option B: Provisioned throughput (uncomment below, comment out Option A above)
# echo "▶ Creating CosmosDB account '$COSMOSDB_ACCOUNT' (provisioned)..."
# az cosmosdb create \
#   --name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --kind GlobalDocumentDB \
#   --default-consistency-level Session \
#   --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
#   --enable-automatic-failover false \
#   --only-show-errors \
#   -o none

echo "  ✓ CosmosDB account ready"

# ── 3. Create Database ───────────────────────────────────────────────────────
echo "▶ Creating database '$DATABASE_NAME'..."
az cosmosdb sql database create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DATABASE_NAME" \
  --only-show-errors \
  -o none
echo "  ✓ Database ready"

# ── 4. Create Container ──────────────────────────────────────────────────────
echo "▶ Creating container '$CONTAINER_NAME' (partition key: /symbol)..."

# Serverless container (no throughput setting)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  --only-show-errors \
  -o none

# Provisioned container with autoscale (uncomment if using Option B above)
# az cosmosdb sql container create \
#   --account-name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --database-name "$DATABASE_NAME" \
#   --name "$CONTAINER_NAME" \
#   --partition-key-path "/symbol" \
#   --partition-key-version 2 \
#   --max-throughput 4000 \
#   --only-show-errors \
#   -o none

echo "  ✓ Container ready"

# ── 5. Apply Custom Indexing Policy ──────────────────────────────────────────
echo "▶ Applying custom indexing policy..."
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --idx '{
    "indexingMode": "consistent",
    "automatic": true,
    "includedPaths": [
      {"path": "/symbol/?"},
      {"path": "/doc_type/?"},
      {"path": "/timestamp/?"},
      {"path": "/watchlist/covered_call/?"},
      {"path": "/watchlist/cash_secured_put/?"},
      {"path": "/agent_type/?"},
      {"path": "/decision/?"}
    ],
    "excludedPaths": [
      {"path": "/reason/*"},
      {"path": "/raw_response/*"},
      {"path": "/analysis_context/*"},
      {"path": "/*"}
    ]
  }' \
  --only-show-errors \
  -o none
echo "  ✓ Indexing policy applied"

# ── 6. Retrieve Connection Details ───────────────────────────────────────────
echo "▶ Retrieving connection details..."

COSMOSDB_ENDPOINT=$(az cosmosdb show \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query documentEndpoint \
  --output tsv)

COSMOSDB_KEY=$(az cosmosdb keys list \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query primaryMasterKey \
  --output tsv)

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Provisioning complete!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Set these environment variables:"
echo ""
echo "    export COSMOSDB_ENDPOINT=\"$COSMOSDB_ENDPOINT\""
echo "    export COSMOSDB_KEY=\"$COSMOSDB_KEY\""
echo ""
echo "  Or add them to your .env file:"
echo ""
echo "    COSMOSDB_ENDPOINT=$COSMOSDB_ENDPOINT"
echo "    COSMOSDB_KEY=$COSMOSDB_KEY"
echo ""
