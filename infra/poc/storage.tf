# Host storage for the Function App: runtime state and the deployment package.
resource "azurerm_storage_account" "function" {
  name                = "rgequipmentaipocbdaf"
  resource_group_name = azurerm_resource_group.main.name
  location            = local.location
  tags                = local.tags

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  min_tls_version                  = "TLS1_2"
  https_traffic_only_enabled       = true
  allow_nested_items_to_be_public  = false
  cross_tenant_replication_enabled = false
  default_to_oauth_authentication  = true

  # The Function App reaches this account as its managed identity, so account keys are off.
  shared_access_key_enabled = false
}

# `func azure functionapp publish` uploads the code package here; Terraform owns only the container.
resource "azurerm_storage_container" "app_package" {
  name                  = "app-package-func-equipment-service-poc-9089a1b"
  storage_account_id    = azurerm_storage_account.function.id
  container_access_type = "private"
}
