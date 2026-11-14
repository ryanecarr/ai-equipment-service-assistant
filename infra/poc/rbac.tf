locals {
  function_principal_id = azurerm_function_app_flex_consumption.main.identity[0].principal_id

  cosmos_database_scope          = "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.main.name}"
  cosmos_saved_assessments_scope = "${local.cosmos_database_scope}/colls/${azurerm_cosmosdb_sql_container.main["saved-assessments"].name}"
  cosmos_data_reader_role_id     = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001"
}

# --- Foundry -------------------------------------------------------------------------------

resource "azurerm_role_definition" "foundry_agent_invoker" {
  role_definition_id = "2563f132-4d1d-4e9f-841c-33d2523a73cc"
  name               = "Foundry Agent Invoker (POC)"
  description        = "Least-privilege role for a service identity that only needs to call an existing Foundry agent via Conversations/Responses (agent_reference) — no agent creation/modification, no broader Cognitive Services data access."
  scope              = azurerm_resource_group.main.id
  assignable_scopes  = [azurerm_resource_group.main.id]

  permissions {
    actions      = ["Microsoft.CognitiveServices/*/read"]
    data_actions = ["Microsoft.CognitiveServices/accounts/AIServices/agents/*"]
  }
}

resource "azurerm_role_assignment" "function_agent_invoker" {
  scope              = azurerm_cognitive_account_project.main.id
  role_definition_id = azurerm_role_definition.foundry_agent_invoker.role_definition_resource_id
  principal_id       = local.function_principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "developer_foundry_account" {
  for_each = var.developer_principal_ids

  scope                = azurerm_cognitive_account.foundry.id
  role_definition_name = "Foundry User"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "developer_foundry_project" {
  for_each = var.developer_principal_ids

  scope                = azurerm_cognitive_account_project.main.id
  role_definition_name = "Foundry User"
  principal_id         = each.value
}

# --- Storage -------------------------------------------------------------------------------

# The Function host reaches its storage account as its own identity, with no account key.
# Blob Data Owner is the minimum the host needs; Table Data Contributor lets it record
# diagnostic events when the app can't start.
resource "azurerm_role_assignment" "function_storage" {
  for_each = toset(["Storage Blob Data Owner", "Storage Table Data Contributor"])

  scope                = azurerm_storage_account.function.id
  role_definition_name = each.value
  principal_id         = local.function_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "developer_storage_blob_reader" {
  for_each = var.developer_principal_ids

  scope                = azurerm_storage_account.function.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = each.value
}

# --- Cosmos DB (data plane) ----------------------------------------------------------------
# The Function needs both roles: with only Data Reader, every save returns 503.

resource "azurerm_cosmosdb_sql_role_definition" "saved_assessments_writer" {
  role_definition_id  = "2e77647c-ab24-48b6-b46a-ce370d1d8cf4"
  name                = "Saved Assessments Writer (POC)"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  type                = "CustomRole"
  assignable_scopes   = [local.cosmos_saved_assessments_scope]

  permissions {
    data_actions = [
      "Microsoft.DocumentDB/databaseAccounts/readMetadata",
      "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create",
    ]
  }
}

resource "azurerm_cosmosdb_sql_role_assignment" "function_reader" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = local.cosmos_data_reader_role_id
  principal_id        = local.function_principal_id
  scope               = local.cosmos_database_scope
}

resource "azurerm_cosmosdb_sql_role_assignment" "function_saved_assessments_writer" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = azurerm_cosmosdb_sql_role_definition.saved_assessments_writer.id
  principal_id        = local.function_principal_id
  scope               = local.cosmos_saved_assessments_scope
}

resource "azurerm_cosmosdb_sql_role_assignment" "developer_reader" {
  for_each = var.developer_principal_ids

  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = local.cosmos_data_reader_role_id
  principal_id        = each.value
  scope               = local.cosmos_database_scope
}
