output "function_app_name" {
  value = azurerm_function_app_flex_consumption.main.name
}

output "function_app_hostname" {
  value = azurerm_function_app_flex_consumption.main.default_hostname
}

output "cosmos_endpoint" {
  description = "Value for COSMOS_ENDPOINT in local.settings.json."
  value       = azurerm_cosmosdb_account.main.endpoint
}

output "project_endpoint" {
  description = "Value for PROJECT_ENDPOINT in local.settings.json."
  value       = azurerm_function_app_flex_consumption.main.app_settings["PROJECT_ENDPOINT"]
}
