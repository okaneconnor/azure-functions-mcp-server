output "client_id" {
  description = "Application (client) ID for the MCP server"
  value       = azuread_application.mcp_server.client_id
}

output "tenant_id" {
  description = "Azure AD tenant ID"
  value       = data.azuread_client_config.current.tenant_id
}

output "identifier_uri" {
  description = "Application identifier URI (audience for tokens)"
  value       = "api://${azuread_application.mcp_server.client_id}"
}

output "scope_name" {
  description = "Full scope name for access_as_user"
  value       = "api://${azuread_application.mcp_server.client_id}/${var.oauth2_scope_name}"
}

output "security_group_name" {
  description = "Name of the security group for access control"
  value       = azuread_group.mcp_users.display_name
}

output "security_group_id" {
  description = "Object ID of the security group"
  value       = azuread_group.mcp_users.object_id
}

output "mi_client_id" {
  description = "Client ID of the User-Assigned Managed Identity"
  value       = azurerm_user_assigned_identity.mcp.client_id
}

output "mi_principal_id" {
  description = "Principal ID of the User-Assigned Managed Identity"
  value       = azurerm_user_assigned_identity.mcp.principal_id
}

output "function_app_name" {
  description = "Name of the Function App"
  value       = azurerm_function_app_flex_consumption.mcp.name
}

output "function_app_url" {
  description = "Default hostname of the Function App"
  value       = "https://${azurerm_function_app_flex_consumption.mcp.name}.azurewebsites.net"
}

output "mcp_endpoint" {
  description = "MCP Streamable HTTP endpoint for client configuration"
  value       = "https://${azurerm_function_app_flex_consumption.mcp.name}.azurewebsites.net/runtime/webhooks/mcp"
}

output "mcp_sse_endpoint" {
  description = "MCP SSE endpoint (legacy transport)"
  value       = "https://${azurerm_function_app_flex_consumption.mcp.name}.azurewebsites.net/runtime/webhooks/mcp/sse"
}

output "storage_account_name" {
  description = "Name of the storage account"
  value       = azurerm_storage_account.func.name
}

output "application_insights_name" {
  description = "Name of the Application Insights instance"
  value       = azurerm_application_insights.mcp.name
}

output "resource_group_name" {
  description = "Name of the resource group"
  value       = azurerm_resource_group.mcp.name
}
