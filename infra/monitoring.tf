resource "azurerm_log_analytics_workspace" "mcp" {
  name                = "${var.project_name}-law-${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "mcp" {
  name                = "${var.project_name}-ai-${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.mcp.id
}
