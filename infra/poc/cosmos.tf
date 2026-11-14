resource "azurerm_cosmosdb_account" "main" {
  name                = "cosmos-equipment-service-poc"
  resource_group_name = azurerm_resource_group.main.name
  location            = local.location
  tags                = local.tags

  kind       = "GlobalDocumentDB"
  offer_type = "Standard"

  # One free-tier account is allowed per subscription: the first 1000 RU/s and 25 GB are free.
  free_tier_enabled = true

  # Data access is Entra ID only; the account keys are disabled.
  local_authentication_enabled  = false
  public_network_access_enabled = true
  minimal_tls_version           = "Tls12"

  automatic_failover_enabled = true

  consistency_policy {
    consistency_level       = "Session"
    max_interval_in_seconds = 5
    max_staleness_prefix    = 100
  }

  geo_location {
    location          = local.location
    failover_priority = 0
  }

  backup {
    type                = "Periodic"
    interval_in_minutes = 240
    retention_in_hours  = 8
    storage_redundancy  = "Geo"
  }

  lifecycle {
    # Deleting this would lose the data and the subscription's one free-tier slot.
    prevent_destroy = true
  }
}

# All three containers share this throughput. Do not set throughput on a container.
resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "equipment-service-db"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  throughput          = 400

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  cosmos_containers = {
    equipment   = { excluded_paths = [] }
    work-orders = { excluded_paths = [] }
    # Nothing queries inside a saved assessment, so it stays out of the index.
    saved-assessments = { excluded_paths = ["/assessment/*"] }
  }
}

resource "azurerm_cosmosdb_sql_container" "main" {
  for_each = local.cosmos_containers

  name                = each.key
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name

  partition_key_kind  = "Hash"
  partition_key_paths = ["/equipment_id"]

  indexing_policy {
    indexing_mode = "consistent"

    included_path {
      path = "/*"
    }

    dynamic "excluded_path" {
      for_each = each.value.excluded_paths

      content {
        path = excluded_path.value
      }
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}
