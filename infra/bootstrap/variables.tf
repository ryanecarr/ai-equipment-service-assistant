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

variable "github_repository" {
  description = "GitHub repository (owner/name) whose workflows may run Terraform."
  type        = string
  default     = "ryanecarr/ai-equipment-service-assistant"
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
