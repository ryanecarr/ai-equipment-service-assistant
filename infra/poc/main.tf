resource "azurerm_resource_group" "main" {
  name     = "rg-equipment-ai-poc"
  location = "eastus"
  tags     = local.tags
}
