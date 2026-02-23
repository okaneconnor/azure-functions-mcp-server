resource "azurerm_storage_account" "func" {
  name                     = "stmcpfunc${random_id.suffix.hex}"
  resource_group_name      = azurerm_resource_group.mcp.name
  location                 = azurerm_resource_group.mcp.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_container" "deployments" {
  name                  = "function-deployments"
  storage_account_id    = azurerm_storage_account.func.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "mi_storage_blob" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.mcp.principal_id
}

resource "azurerm_role_assignment" "mi_storage_queue" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.mcp.principal_id
}

resource "azurerm_role_assignment" "mi_storage_table" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.mcp.principal_id
}
