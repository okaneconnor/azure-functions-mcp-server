terraform {
  required_version = ">= 1.5"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
    azuredevops = {
      source  = "microsoft/azuredevops"
      version = ">= 1.13.0"
    }
  }

  backend "local" {}
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

provider "azuread" {}

provider "azuredevops" {
  org_service_url = "https://dev.azure.com/${var.azure_devops_org}"
}

data "azuread_client_config" "current" {}
