variable "subscription_id" {
  description = "Subscription that holds the Terraform state storage."
  type        = string
}

variable "location" {
  description = "Region for the state storage."
  type        = string
  default     = "westus3"
}

variable "resource_group_name" {
  description = "Resource group that holds only the Terraform state storage."
  type        = string
  default     = "rg-equipment-ai-poc-tfstate"
}

variable "storage_account_name" {
  description = "Globally unique name of the state storage account."
  type        = string
  default     = "stequipaipoctfstate"
}

variable "state_admin_principal_ids" {
  description = "Object IDs allowed to read and write state. Defaults to the identity running Terraform."
  type        = set(string)
  default     = []
}

variable "github_oidc_subject_prefix" {
  description = "Subject prefix GitHub puts in OIDC tokens for the repository allowed to run Terraform. It carries the owner and repository IDs, so a renamed or recreated repository cannot reuse the trust. Read it with: gh api repos/<owner>/<repo>/actions/oidc/customization/sub"
  type        = string
  default     = "repo:ryanecarr@23034943/ai-equipment-service-assistant@1443015281"
}

variable "github_environment" {
  description = "GitHub environment that gates terraform apply."
  type        = string
  default     = "poc"
}

variable "poc_resource_group_name" {
  description = "Resource group the pipeline manages."
  type        = string
  default     = "rg-equipment-ai-poc"
}

variable "poc_foundry_account_name" {
  description = "Foundry account that holds the agent the release pipeline publishes."
  type        = string
  default     = "foundry-equipment-ai-poc"
}

variable "poc_foundry_project_name" {
  description = "Foundry project that holds the agent the release pipeline publishes."
  type        = string
  default     = "equipment-service-poc"
}
