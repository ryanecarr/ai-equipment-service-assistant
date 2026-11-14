provider "azurerm" {
  subscription_id = var.subscription_id

  # No storage account here allows account keys, so storage data-plane calls use Entra ID.
  storage_use_azuread = true

  features {}
}
