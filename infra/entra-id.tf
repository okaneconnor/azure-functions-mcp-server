resource "random_id" "suffix" {
  byte_length = 4
}

resource "random_uuid" "scope_id" {}

resource "azuread_application" "mcp_server" {
  display_name     = "${var.project_name}-${random_id.suffix.hex}"
  sign_in_audience = "AzureADMyOrg"

  lifecycle {
    ignore_changes = [identifier_uris]
  }

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      id                         = random_uuid.scope_id.result
      value                      = var.oauth2_scope_name
      type                       = "User"
      admin_consent_display_name = "Access Azure DevOps Pipelines MCP"
      admin_consent_description  = "Allow the MCP client to access Azure DevOps on your behalf"
      user_consent_display_name  = "Access Azure DevOps Pipelines MCP"
      user_consent_description   = "Allow the MCP client to access Azure DevOps on your behalf"
      enabled                    = true
    }
  }

  web {
    redirect_uris = [
      "http://127.0.0.1:33418/",
      "https://vscode.dev/redirect",
    ]
  }

  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read
      type = "Scope"
    }
    resource_access {
      id   = "37f7f235-527c-4136-accd-4a02d197296e" # openid
      type = "Scope"
    }
    resource_access {
      id   = "14dad69e-099b-42c9-810b-d002981feec1" # profile
      type = "Scope"
    }
    resource_access {
      id   = "64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0" # email
      type = "Scope"
    }
  }

}

# ── Identifier URI (must be separate to avoid self-reference) ────────────────
resource "azuread_application_identifier_uri" "mcp_server" {
  application_id = azuread_application.mcp_server.id
  identifier_uri = "api://${azuread_application.mcp_server.client_id}"
}

# ── Pre-authorized clients (VS Code + Azure CLI) ────────────────────────────
resource "azuread_application_pre_authorized" "vscode" {
  application_id       = azuread_application.mcp_server.id
  authorized_client_id = "aebc6443-996d-45c2-90f0-388ff96faa56" # VS Code
  permission_ids       = [random_uuid.scope_id.result]
}

resource "azuread_application_pre_authorized" "azure_cli" {
  application_id       = azuread_application.mcp_server.id
  authorized_client_id = "04b07795-8ddb-461a-bbee-02f9e1bf7b46" # Azure CLI
  permission_ids       = [random_uuid.scope_id.result]
}

# ── Service Principal ────────────────────────────────────────────────────────
resource "azuread_service_principal" "mcp_server" {
  client_id                    = azuread_application.mcp_server.client_id
  app_role_assignment_required = true
}

# ── Security group for access control ────────────────────────────────────────
resource "azuread_group" "mcp_users" {
  display_name     = "${var.project_name}-users-${random_id.suffix.hex}"
  security_enabled = true
  description      = "Users allowed to access the ${var.project_name} MCP server"
}

# ── Group membership — add users to the MCP access group ───────────────────
resource "azuread_group_member" "mcp_users" {
  for_each = toset(var.admin_user_object_ids)

  group_object_id  = azuread_group.mcp_users.object_id
  member_object_id = each.value
}

# ── Grant the group access to the MCP server app ──────────────────────────
# With app_role_assignment_required = true on the service principal,
# only members of this group can obtain tokens for the app.
resource "azuread_app_role_assignment" "mcp_users_group" {
  app_role_id         = "00000000-0000-0000-0000-000000000000" # Default Access
  principal_object_id = azuread_group.mcp_users.object_id
  resource_object_id  = azuread_service_principal.mcp_server.object_id
}

