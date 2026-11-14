variable "subscription_id" {
  description = "Subscription that holds the POC."
  type        = string
}

variable "developer_principal_ids" {
  description = "Object IDs of developers who run the app locally against the POC resources."
  type        = set(string)
  default     = []
}
