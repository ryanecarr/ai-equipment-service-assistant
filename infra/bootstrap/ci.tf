# Identities that GitHub Actions uses to run Terraform for infra/poc. GitHub proves which
# repository and workflow context is asking (OIDC), so no secret is stored anywhere.
#
# They live here, not in infra/poc, so the pipeline can never change its own permissions.
#
#   plan  - read-only; used for pull requests and for the plan on main
#   apply - can change the POC; only usable from the protected GitHub environment

locals {
  github_issuer = "https://token.actions.githubusercontent.com"
  poc_rg_id     = "/subscriptions/${var.subscription_id}/resourceGroups/${var.poc_resource_group_name}"

  # The workspace Azure auto-created for Application Insights; infra/poc reads it.
  default_workspace_id = "/subscriptions/${var.subscription_id}/resourceGroups/DefaultResourceGroup-USW3/providers/Microsoft.OperationalInsights/workspaces/DefaultWorkspace-${var.subscription_id}-USW3"

  poc_function_storage_id = "${local.poc_rg_id}/providers/Microsoft.Storage/storageAccounts/rgequipmentaipocbdaf"
}

# --- plan ----------------------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "ci_plan" {
  name                = "id-equipment-ai-poc-tf-plan"
  resource_group_name = azurerm_resource_group.tfstate.name
  location            = azurerm_resource_group.tfstate.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "ci_plan" {
  for_each = {
    pull-request = "repo:${var.github_repository}:pull_request"
    main         = "repo:${var.github_repository}:ref:refs/heads/main"
  }

  name                      = "github-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.ci_plan.id
  issuer                    = local.github_issuer
  audience                  = ["api://AzureADTokenExchange"]
  subject                   = each.value
}

# Reader cannot run the "list" calls the provider makes while refreshing these resources.
resource "azurerm_role_definition" "ci_plan_refresh" {
  name              = "Terraform Plan Refresh (POC)"
  description       = "Read-style list actions that terraform plan needs on top of Reader. Grants no ability to change resources."
  scope             = local.poc_rg_id
  assignable_scopes = [local.poc_rg_id]

  permissions {
    # Deliberately excludes every listKeys action: the plan identity must not be able to read
    # a key that could be used to call the model or the database.
    actions = [
      "Microsoft.Web/sites/config/list/action",
    ]
  }
}

resource "azurerm_role_assignment" "ci_plan" {
  for_each = {
    poc-reader        = { scope = local.poc_rg_id, role = "Reader" }
    workspace-reader  = { scope = local.default_workspace_id, role = "Reader" }
    storage-blob-read = { scope = local.poc_function_storage_id, role = "Storage Blob Data Reader" }
    # Plans run with -lock=false, so read access to state is enough.
    state-read = { scope = azurerm_storage_container.tfstate.id, role = "Storage Blob Data Reader" }
  }

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.ci_plan.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "ci_plan_refresh" {
  scope              = local.poc_rg_id
  role_definition_id = azurerm_role_definition.ci_plan_refresh.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.ci_plan.principal_id
  principal_type     = "ServicePrincipal"
}

# --- apply ---------------------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "ci_apply" {
  name                = "id-equipment-ai-poc-tf-apply"
  resource_group_name = azurerm_resource_group.tfstate.name
  location            = azurerm_resource_group.tfstate.location
  tags                = local.tags
}

# Only jobs that run in the GitHub environment can use this identity, and that environment
# requires a reviewer's approval.
resource "azurerm_federated_identity_credential" "ci_apply" {
  name                      = "github-environment-${var.github_environment}"
  user_assigned_identity_id = azurerm_user_assigned_identity.ci_apply.id
  issuer                    = local.github_issuer
  audience                  = ["api://AzureADTokenExchange"]
  subject                   = "repo:${var.github_repository}:environment:${var.github_environment}"
}

resource "azurerm_role_assignment" "ci_apply" {
  for_each = {
    # Owner, not Contributor: infra/poc manages role definitions and role assignments.
    poc-owner         = { scope = local.poc_rg_id, role = "Owner" }
    workspace-reader  = { scope = local.default_workspace_id, role = "Reader" }
    storage-blob-read = { scope = local.poc_function_storage_id, role = "Storage Blob Data Reader" }
    state-write       = { scope = azurerm_storage_container.tfstate.id, role = "Storage Blob Data Contributor" }
  }

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.ci_apply.principal_id
  principal_type       = "ServicePrincipal"
}
