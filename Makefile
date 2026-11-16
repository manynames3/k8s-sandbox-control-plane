.PHONY: doctor disk clean nuke up down test e2e attack chaos lint verify-cluster monitoring
WORKERS ?= 2
CLUSTER ?= ksc
IMAGE_API ?= ksc/api:dev
IMAGE_RUNNER_PY ?= ksc/runner-python:dev
IMAGE_RUNNER_NODE ?= ksc/runner-node:dev
KEDA_VERSION ?= 2.21.0
PROM_STACK_VERSION ?= 93.3.0
# monitoring objects are installed only when the Prometheus stack is present (see `make monitoring`)
MONITORING ?= $(shell kubectl get ns monitoring >/dev/null 2>&1 && echo true || echo false)

up:
	@[ -n "$$CI" ] || bash scripts/doctor.sh >/dev/null || { echo "make doctor failed"; exit 1; }
	WORKERS=$(WORKERS) CLUSTER_NAME=$(CLUSTER) deploy/kind/up.sh
	docker build -q -f Dockerfile.api -t $(IMAGE_API) .
	docker build -q -f runner/Dockerfile.python -t $(IMAGE_RUNNER_PY) .
	docker build -q -f runner/Dockerfile.node -t $(IMAGE_RUNNER_NODE) .
	kind load docker-image $(IMAGE_API) $(IMAGE_RUNNER_PY) $(IMAGE_RUNNER_NODE) --name $(CLUSTER)
	helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
	helm upgrade --install keda kedacore/keda --version $(KEDA_VERSION) -n keda --create-namespace \
	  --set webhooks.enabled=false --wait --timeout 5m
	helm upgrade --install ksc charts/ksc -n ksc-system --create-namespace --set monitoring.enabled=$(MONITORING) \
	  -f charts/ksc/values-kind.yaml --set image.api=$(IMAGE_API) \
	  --set image.runnerPython=$(IMAGE_RUNNER_PY) --set image.runnerNode=$(IMAGE_RUNNER_NODE) \
	  --set-json knownNodes="$$(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o json | jq -c '[.items[].metadata.name]')" \
	  --wait --timeout 5m
	kubectl -n ksc-system rollout restart deploy/ksc-api deploy/ksc-dispatcher >/dev/null
	kubectl -n ksc-system rollout status deploy/ksc-api deploy/ksc-dispatcher --timeout=120s
	@echo "cluster up: $(WORKERS) workers. API: kubectl -n ksc-system port-forward svc/ksc-api 8080:8080"

monitoring:
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack --version $(PROM_STACK_VERSION) \
	  -n monitoring --create-namespace -f deploy/monitoring/values.yaml --wait --timeout 10m
	$(MAKE) up
	@echo "Grafana: kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80  (admin / admin)"

down:
	-kind delete cluster --name $(CLUSTER)
	@docker ps -a --filter name=$(CLUSTER)- -q | grep -q . && { echo "leftover containers"; exit 1; } || echo "down: nothing left"

test:
	@docker rm -f ksc-test-pg >/dev/null 2>&1; docker run -d --name ksc-test-pg -e POSTGRES_PASSWORD=x -p 55432:5432 postgres:16-alpine >/dev/null
	@until docker exec ksc-test-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done; sleep 1
	@TEST_DATABASE_URL=postgresql+psycopg://postgres:x@localhost:55432/postgres uv run pytest -q; rc=$$?; docker rm -f ksc-test-pg >/dev/null; exit $$rc

e2e:
	uv run pytest -q -m e2e tests/e2e

attack:
	uv run pytest -v -m attack tests/attack

chaos:
	uv run pytest -v -m chaos tests/chaos

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
