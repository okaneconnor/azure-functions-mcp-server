resource "azurerm_user_assigned_identity" "mcp" {
  name                = "${var.project_name}-mi-${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
}

resource "azuredevops_service_principal_entitlement" "mcp_mi" {
  origin               = "aad"
  origin_id            = azurerm_user_assigned_identity.mcp.principal_id
  account_license_type = "stakeholder"
}

data "azuredevops_project" "target" {
  for_each = toset(var.azure_devops_projects)
  name     = each.value
}

data "azuredevops_group" "contributors" {
  for_each   = toset(var.azure_devops_projects)
  project_id = data.azuredevops_project.target[each.key].id
  name       = "Contributors"
}

resource "azuredevops_group_membership" "mcp_mi_contributors" {
  for_each = toset(var.azure_devops_projects)
  group    = data.azuredevops_group.contributors[each.key].descriptor
  mode     = "add"
  members  = [azuredevops_service_principal_entitlement.mcp_mi.descriptor]
}
