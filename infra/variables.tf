variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "project_name" {
  description = "Base name for all resources"
  type        = string
  default     = "azure-devops-pipelines-mcp"
}

variable "location" {
  description = "Azure region for all resources"
  type        = string
  default     = "uksouth"
}

variable "oauth2_scope_name" {
  description = "Name of the delegated OAuth2 permission scope"
  type        = string
  default     = "access_as_user"
}

variable "azure_devops_org" {
  description = "Azure DevOps organization name (the part after dev.azure.com/)"
  type        = string
}

variable "azure_devops_projects" {
  description = "List of Azure DevOps project names the MI can access"
  type        = list(string)
}

variable "maximum_instance_count" {
  description = "Maximum number of instances for Flex Consumption scale-out"
  type        = number
  default     = 40
}

variable "instance_memory_mb" {
  description = "Memory allocated per instance in MB (2048 or 4096)"
  type        = number
  default     = 2048
}

variable "admin_user_object_ids" {
  description = "Entra ID object IDs of users to grant direct access to the MCP server"
  type        = list(string)
  default     = []
}
