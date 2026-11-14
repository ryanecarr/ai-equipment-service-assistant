# Microsoft Foundry account. The agent itself (equipment-service-assistant) is created inside the
# project through the Foundry data plane and is not an Azure resource, so it is not managed here.
resource "azurerm_cognitive_account" "foundry" {
  name                = "foundry-equipment-ai-poc"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags

  kind                       = "AIServices"
  sku_name                   = "S0"
  custom_subdomain_name      = "foundry-equipment-ai-poc"
  project_management_enabled = true

  # Entra ID only: with API keys off, a leaked key cannot be used to run up model charges.
  local_auth_enabled            = false
  public_network_access_enabled = true

  identity {
    type = "SystemAssigned"
  }

  network_acls {
    default_action = "Allow"
  }

  lifecycle {
    # Deleting the account deletes the project and the agent inside it.
    prevent_destroy = true
  }
}

resource "azurerm_cognitive_account_project" "main" {
  name                 = "equipment-service-poc"
  cognitive_account_id = azurerm_cognitive_account.foundry.id
  location             = azurerm_cognitive_account.foundry.location
  display_name         = "equipment-service-poc"
  description          = "Default project created with the resource"
  tags                 = local.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_deployment" "gpt_5_mini" {
  name                 = "gpt-5-mini"
  cognitive_account_id = azurerm_cognitive_account.foundry.id

  rai_policy_name        = "Microsoft.DefaultV2"
  version_upgrade_option = "OnceNewDefaultVersionAvailable"

  model {
    format  = "OpenAI"
    name    = "gpt-5-mini"
    version = "2025-08-07"
  }

  sku {
    name = "GlobalStandard"

    # Thousands of tokens per minute. Kept low as a cost guard; requests beyond it get a 429.
    capacity = 5
  }

  lifecycle {
    # Azure moves the deployment to each new default model version on its own.
    ignore_changes = [model[0].version]
  }
}
