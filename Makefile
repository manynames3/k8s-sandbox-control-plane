.PHONY: doctor disk clean nuke up down test e2e lint verify-cluster
WORKERS ?= 2
CLUSTER ?= ksc
IMAGE_API ?= ksc/api:dev
IMAGE_RUNNER_PY ?= ksc/runner-python:dev

up:
	@bash scripts/doctor.sh >/dev/null || { echo "make doctor failed"; exit 1; }
	WORKERS=$(WORKERS) CLUSTER_NAME=$(CLUSTER) deploy/kind/up.sh
	docker build -q -f Dockerfile.api -t $(IMAGE_API) .
	docker build -q -f runner/Dockerfile.python -t $(IMAGE_RUNNER_PY) .
	kind load docker-image $(IMAGE_API) $(IMAGE_RUNNER_PY) --name $(CLUSTER)
	helm upgrade --install ksc charts/ksc -n ksc-system --create-namespace \
	  -f charts/ksc/values-kind.yaml --set image.api=$(IMAGE_API) \
	  --set image.runnerPython=$(IMAGE_RUNNER_PY) --wait --timeout 5m
	kubectl -n ksc-system rollout restart deploy/ksc-api deploy/ksc-dispatcher >/dev/null
	kubectl -n ksc-system rollout status deploy/ksc-api deploy/ksc-dispatcher --timeout=120s
	@echo "cluster up: $(WORKERS) workers. API: kubectl -n ksc-system port-forward svc/ksc-api 8080:8080"

down:
	-kind delete cluster --name $(CLUSTER)
	@docker ps -a --filter name=$(CLUSTER)- -q | grep -q . && { echo "leftover containers"; exit 1; } || echo "down: nothing left"

test:
	@docker rm -f ksc-test-pg >/dev/null 2>&1; docker run -d --name ksc-test-pg -e POSTGRES_PASSWORD=x -p 55432:5432 postgres:16-alpine >/dev/null
	@until docker exec ksc-test-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done; sleep 1
	@TEST_DATABASE_URL=postgresql+psycopg://postgres:x@localhost:55432/postgres uv run pytest -q; rc=$$?; docker rm -f ksc-test-pg >/dev/null; exit $$rc

e2e:
	uv run pytest -q -m e2e tests/e2e

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
