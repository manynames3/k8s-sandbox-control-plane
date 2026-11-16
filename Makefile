.PHONY: doctor disk clean nuke
WORKERS ?= 3

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
