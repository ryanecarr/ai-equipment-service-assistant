locals {
  # The resource group sits in eastus with Foundry; everything else runs in westus3.
  location = "westus3"

  tags = {
    project     = "equipment-ai-poc"
    environment = "poc"
    managed_by  = "terraform"
  }
}
