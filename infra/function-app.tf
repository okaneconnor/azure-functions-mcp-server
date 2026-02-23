resource "azurerm_service_plan" "mcp" {
  name                = "${var.project_name}-plan-${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
  os_type             = "Linux"
  sku_name            = "FC1"
}

resource "azurerm_function_app_flex_consumption" "mcp" {
  name                = "${var.project_name}-func-${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
  service_plan_id     = azurerm_service_plan.mcp.id

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.func.primary_blob_endpoint}${azurerm_storage_container.deployments.name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.mcp.id

  runtime_name    = "python"
  runtime_version = "3.12"

  maximum_instance_count = var.maximum_instance_count
  instance_memory_in_mb  = var.instance_memory_mb

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.mcp.id]
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.mcp.connection_string
  }

  app_settings = {
    "AzureWebJobsStorage"              = ""
    "AzureWebJobsStorage__accountName" = azurerm_storage_account.func.name
    "AzureWebJobsStorage__credential"  = "managedidentity"
    "AzureWebJobsStorage__clientId"    = azurerm_user_assigned_identity.mcp.client_id

    "FUNCTIONS_EXTENSION_VERSION" = "~4"
    "AzureWebJobsFeatureFlags"    = "EnableWorkerIndexing"

    "AZURE_DEVOPS_ORG"      = var.azure_devops_org
    "AZURE_DEVOPS_PROJECT"  = var.azure_devops_projects[0]
    "AZURE_DEVOPS_PROJECTS" = join(",", var.azure_devops_projects)

    "AZURE_MI_CLIENT_ID" = azurerm_user_assigned_identity.mcp.client_id

    # Protected Resource Metadata — MCP clients discover auth requirements here
    "WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES" = "api://${azuread_application.mcp_server.client_id}/${var.oauth2_scope_name}"
  }

  # EasyAuth — Entra ID authentication for the Function App.
  auth_settings_v2 {
    auth_enabled           = true
    require_authentication = true
    unauthenticated_action = "Return401"
    require_https          = true
    excluded_paths         = ["/admin/host/status", "/api/health"]

    active_directory_v2 {
      client_id            = azuread_application.mcp_server.client_id
      tenant_auth_endpoint = "https://login.microsoftonline.com/${data.azuread_client_config.current.tenant_id}/v2.0"
      allowed_audiences = [
        "api://${azuread_application.mcp_server.client_id}",
        azuread_application.mcp_server.client_id,
      ]
    }

    login {
      token_store_enabled = true
    }
  }

  depends_on = [
    azurerm_role_assignment.mi_storage_blob,
    azurerm_role_assignment.mi_storage_queue,
    azurerm_role_assignment.mi_storage_table,
  ]
}
