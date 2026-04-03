PLATFORM := $(shell uname -s)-$(shell uname -m)

help:
	@echo "Available targets:"
	@echo "  run    - Bootstrap the full environment (install tools, provision cluster)"
	@echo "  down   - Destroy the cluster and all resources"
	@echo "  push   - Bump patch version, tag, and push to trigger CI"
	@echo "  tools  - Install necessary tools only"
	@echo "  tofu   - Initialize OpenTofu"
	@echo "  apply  - Apply OpenTofu configuration"

run:
	@echo "This will provision a new Kubernetes cluster and deploy all components."
ifeq ($(PLATFORM),Darwin-arm64)
	@bash scripts/setup-darwin.sh
else
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || { echo "Aborted."; exit 1; }
	@bash scripts/setup.sh
endif

tools:
ifeq ($(PLATFORM),Darwin-arm64)
	@echo "darwin: using pre-installed tools (k3d, kubectl, helm, k9s)"
else
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || { echo "Aborted."; exit 1; }
	@curl -fsSL https://get.opentofu.org/install-opentofu.sh | sh -s -- --install-method standalone
	@curl -sS https://webi.sh/k9s | bash
endif

tofu:
	@cd bootstrap && tofu init

apply:
	@cd bootstrap && tofu apply -auto-approve

down:
ifeq ($(PLATFORM),Darwin-arm64)
	@cd bootstrap-darwin && tofu destroy -auto-approve
else
	@cd bootstrap && tofu destroy -auto-approve
endif

push:
	@git fetch origin --tags --force
	$(eval TAG=$(shell git tag --list 'v*' | sort -V | tail -1 | sed 's/^v//'))
	$(eval TAG=$(if $(TAG),$(TAG),0.0.0))
	$(eval MAJOR=$(shell echo $(TAG) | cut -d. -f1))
	$(eval MINOR=$(shell echo $(TAG) | cut -d. -f2))
	$(eval PATCH=$(shell echo $(TAG) | cut -d. -f3))
	$(eval NEW_TAG=$(shell \
		PATCH=$$(($(PATCH)+1)); \
		if [ $$PATCH -gt 9 ]; then \
			echo v$(MAJOR).$$(($(MINOR)+1)).0; \
		else \
			echo v$(MAJOR).$(MINOR).$$PATCH; \
		fi))
	@git tag $(NEW_TAG)
	@git push origin main $(NEW_TAG)
	@echo "Tagged and pushed $(NEW_TAG)"
