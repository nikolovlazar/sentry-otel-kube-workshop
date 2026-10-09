PYTHON ?= python3
.PHONY: bootstrap doctor cluster-up configure validate-config build deploy up forward forward-stop down cluster-delete reset-demo verify verify-network migrate deps test verify-sentry verify-recovery source-map-upload
bootstrap:
	$(PYTHON) scripts/bootstrap.py
doctor cluster-up configure validate-config build deploy up forward forward-stop down cluster-delete reset-demo verify verify-network migrate:
	$(PYTHON) scripts/workshop.py $@
deps:
	PATH="$(CURDIR)/.local/bin:$$PATH" npm --prefix apps/web ci
	PATH="$(CURDIR)/.local/bin:$$PATH" npm --prefix services/notification-worker ci
	cd services/catalog && uv sync --frozen
test: deps
	$(PYTHON) -m compileall -q scripts services/catalog
	cd services/catalog && uv run --frozen pytest
	cd apps/web && PATH="$(CURDIR)/.local/bin:$$PATH" npm test
	cd services/notification-worker && PATH="$(CURDIR)/.local/bin:$$PATH" npm test
	cd services/fulfillment && go test -mod=readonly ./...
verify-recovery:
	PATH="$(CURDIR)/.local/bin:$$PATH" npm --prefix services/notification-worker run test:recovery
verify-sentry:
	$(PYTHON) scripts/verify-sentry.py
source-map-upload:
	$(PYTHON) scripts/source-map-upload.py
