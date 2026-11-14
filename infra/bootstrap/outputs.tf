output "resource_group_name" {
  value = azurerm_resource_group.tfstate.name
}

output "storage_account_name" {
  value = azurerm_storage_account.tfstate.name
}

output "container_name" {
  value = azurerm_storage_container.tfstate.name
}

output "ci_plan_client_id" {
  description = "GitHub secret AZURE_PLAN_CLIENT_ID."
  value       = azurerm_user_assigned_identity.ci_plan.client_id
}

output "ci_apply_client_id" {
  description = "GitHub environment secret AZURE_APPLY_CLIENT_ID."
  value       = azurerm_user_assigned_identity.ci_apply.client_id
}
