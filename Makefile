.PHONY: doctor disk clean nuke up down test lint verify-cluster
WORKERS ?= 2
CLUSTER ?= ksc
IMAGE_API ?= ksc/api:dev

up:
	@bash scripts/doctor.sh >/dev/null || { echo "make doctor failed"; exit 1; }
	WORKERS=$(WORKERS) CLUSTER_NAME=$(CLUSTER) deploy/kind/up.sh
	docker build -q -f Dockerfile.api -t $(IMAGE_API) .
	kind load docker-image $(IMAGE_API) --name $(CLUSTER)
	helm upgrade --install ksc charts/ksc -n ksc-system --create-namespace \
	  -f charts/ksc/values-kind.yaml --set image.api=$(IMAGE_API) --wait --timeout 5m
	@echo "cluster up: $(WORKERS) workers. API: kubectl -n ksc-system port-forward svc/ksc-api 8080:8080"

down:
	-kind delete cluster --name $(CLUSTER)
	@docker ps -a --filter name=$(CLUSTER)- -q | grep -q . && { echo "leftover containers"; exit 1; } || echo "down: nothing left"

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy api dispatcher

verify-cluster:
	@bash scripts/verify-cluster.sh

doctor:
	@bash scripts/doctor.sh

disk:
	-docker system df
	-docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'
	-du -sh "$$CX"/cache/* 2>/dev/null

clean:
	docker builder prune -f
	docker image prune -f
	rm -f bench/*.log

nuke:
	@printf 'Type NUKE to delete the kind cluster and prune everything: '; read a; [ "$$a" = NUKE ] || { echo aborted; exit 1; }
	-kind delete cluster --name ksc
	docker system prune -af
# up, down, test, e2e, attack, chaos: added in later phases
