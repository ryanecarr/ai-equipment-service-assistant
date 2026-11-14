# Azure created this workspace automatically; it is referenced, not managed.
data "azurerm_log_analytics_workspace" "default" {
  name                = "DefaultWorkspace-${var.subscription_id}-USW3"
  resource_group_name = "DefaultResourceGroup-USW3"
}

resource "azurerm_application_insights" "main" {
  name                = "func-equipment-service-poc"
  resource_group_name = azurerm_resource_group.main.name
  location            = local.location
  tags                = local.tags

  application_type    = "web"
  workspace_id        = data.azurerm_log_analytics_workspace.default.id
  retention_in_days   = 90
  sampling_percentage = 0
}
