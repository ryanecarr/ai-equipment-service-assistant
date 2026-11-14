terraform {
  backend "azurerm" {
    resource_group_name  = "rg-equipment-ai-poc-tfstate"
    storage_account_name = "stequipaipoctfstate"
    container_name       = "tfstate"
    key                  = "poc.tfstate"
    use_azuread_auth     = true
    use_cli              = true
  }
}
