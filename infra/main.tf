resource "azurerm_resource_group" "mcp" {
  name     = "rg-${var.project_name}-${random_id.suffix.hex}"
  location = var.location
}
