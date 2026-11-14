resource "azurerm_service_plan" "main" {
  name                = "ASP-rgequipmentaipoc-9c98"
  resource_group_name = azurerm_resource_group.main.name
  location            = local.location
  tags                = local.tags

  os_type  = "Linux"
  sku_name = "FC1" # Flex Consumption
}

resource "azurerm_function_app_flex_consumption" "main" {
  name                = "func-equipment-service-poc"
  resource_group_name = azurerm_resource_group.main.name
  location            = local.location
  service_plan_id     = azurerm_service_plan.main.id

  runtime_name           = "python"
  runtime_version        = "3.14"
  instance_memory_in_mb  = 2048
  maximum_instance_count = 100

  https_only                    = true
  public_network_access_enabled = true

  # Client certificates are off; the mode only applies if they are turned on.
  client_certificate_enabled = false
  client_certificate_mode    = "Required"

  webdeploy_publish_basic_authentication_enabled = false

  # Deployment package storage, reached as the app's own identity (roles are in rbac.tf).
  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.function.primary_blob_endpoint}${azurerm_storage_container.app_package.name}"
  storage_authentication_type = "SystemAssignedIdentity"

  app_settings = {
    # Host storage by identity. The empty AzureWebJobsStorage is Microsoft's documented workaround:
    # without it the provider writes a keyless connection string that overrides the identity.
    # Known side effect: Azure does not return the empty value, so every plan shows this one
    # setting as an in-place change. Do not "fix" that with ignore_changes or by deleting the line.
    AzureWebJobsStorage              = ""
    AzureWebJobsStorage__accountName = azurerm_storage_account.function.name

    PROJECT_ENDPOINT                   = "https://${azurerm_cognitive_account.foundry.custom_subdomain_name}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.main.name}"
    AGENT_NAME                         = "equipment-service-assistant"
    COSMOS_ENDPOINT                    = azurerm_cosmosdb_account.main.endpoint
    COSMOS_DATABASE_NAME               = azurerm_cosmosdb_sql_database.main.name
    COSMOS_EQUIPMENT_CONTAINER         = azurerm_cosmosdb_sql_container.main["equipment"].name
    COSMOS_WORK_ORDERS_CONTAINER       = azurerm_cosmosdb_sql_container.main["work-orders"].name
    COSMOS_SAVED_ASSESSMENTS_CONTAINER = azurerm_cosmosdb_sql_container.main["saved-assessments"].name
  }

  # The app reaches Foundry and Cosmos DB as this identity; see rbac.tf.
  identity {
    type = "SystemAssigned"
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    minimum_tls_version                    = "1.2"

    cors {
      allowed_origins = [
        "http://localhost:4200", # local Angular UI
        "https://portal.azure.com",
      ]
    }
  }

  tags = merge(local.tags, {
    # The portal uses this tag to link the app to Application Insights.
    "hidden-link: /app-insights-resource-id" = "${azurerm_resource_group.main.id}/providers/microsoft.insights/components/${azurerm_application_insights.main.name}"
  })

}
