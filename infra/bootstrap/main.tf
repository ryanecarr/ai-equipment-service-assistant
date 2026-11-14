data "azurerm_client_config" "current" {}

locals {
  state_admins = length(var.state_admin_principal_ids) > 0 ? var.state_admin_principal_ids : toset([data.azurerm_client_config.current.object_id])

  tags = {
    project     = "equipment-ai-poc"
    environment = "poc"
    managed_by  = "terraform"
    purpose     = "terraform-state"
  }
}

resource "azurerm_resource_group" "tfstate" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "tfstate" {
  name                = var.storage_account_name
  resource_group_name = azurerm_resource_group.tfstate.name
  location            = azurerm_resource_group.tfstate.location
  tags                = local.tags

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "GRS"

  min_tls_version                  = "TLS1_2"
  https_traffic_only_enabled       = true
  allow_nested_items_to_be_public  = false
  cross_tenant_replication_enabled = false

  # State is reached with Entra ID only; there are no account keys to leak.
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

# Owner on the subscription does not grant blob data access, so state needs its own assignment.
resource "azurerm_role_assignment" "state_admin" {
  for_each = local.state_admins

  scope                = azurerm_storage_container.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value
}

resource "azurerm_management_lock" "tfstate" {
  name       = "tfstate-cannot-delete"
  scope      = azurerm_storage_account.tfstate.id
  lock_level = "CanNotDelete"
  notes      = "Holds Terraform state for the equipment AI POC."
}
