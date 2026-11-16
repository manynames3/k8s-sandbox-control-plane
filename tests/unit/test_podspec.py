"""Every hardening line in the sandbox pod template is asserted here (and attacked in tests/attack)."""
from dispatcher.podspec import SandboxSettings, build_configmap, build_job

RID = "11111111-2222-3333-4444-555555555555"


def spec(timeout=30):
    job = build_job(RID, "python3.12", timeout, SandboxSettings())
    return job, job["spec"]["template"]["spec"], job["spec"]["template"]["spec"]["containers"][0]


def test_pod_security_context():
    _, pod, c = spec()
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] == 10001
    assert pod["securityContext"]["runAsGroup"] == 10001
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    sc = c["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert "privileged" not in sc


def test_no_token_no_host_access():
    _, pod, _ = spec()
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["serviceAccountName"] == "sandbox"
    for k in ("hostNetwork", "hostPID", "hostIPC"):
        assert pod[k] is False
    assert not any("hostPath" in v for v in pod["volumes"])
    assert all("ports" not in c for c in pod["containers"])


def test_only_tmp_is_writable_and_bounded():
    _, pod, c = spec()
    mounts = {m["mountPath"]: m for m in c["volumeMounts"]}
    assert set(mounts) == {"/tmp", "/code"}
    assert mounts["/code"]["readOnly"] is True and not mounts["/tmp"].get("readOnly")
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["tmp"]["emptyDir"]["sizeLimit"]
    assert set(vols) == {"tmp", "code"} and "configMap" in vols["code"]


def test_guaranteed_qos_and_ephemeral_limits():
    _, _, c = spec()
    r = c["resources"]
    assert r["requests"] == r["limits"]
    assert r["limits"]["cpu"] == "500m" and r["limits"]["memory"] == "512Mi"
    assert "ephemeral-storage" in r["limits"]


def test_job_lifecycle_fields():
    job, pod, _ = spec(timeout=45)
    js = job["spec"]
    assert js["backoffLimit"] == 0 and pod["restartPolicy"] == "Never"
    assert js["ttlSecondsAfterFinished"] == 120
    assert js["activeDeadlineSeconds"] == 45 + 30
    assert pod["terminationGracePeriodSeconds"] == 5


def test_labels_name_and_spread():
    job, pod, _ = spec()
    assert job["metadata"]["name"] == f"run-{RID}"
    lab = job["metadata"]["labels"]
    assert lab["app.kubernetes.io/managed-by"] == "ksc" and lab["ksc.dev/run-id"] == RID
    t = pod["topologySpreadConstraints"][0]
    assert t["maxSkew"] == 1 and t["topologyKey"] == "kubernetes.io/hostname"
    assert t["whenUnsatisfiable"] == "ScheduleAnyway"
    assert "affinity" not in pod  # hard anti-affinity would cap concurrency at node count


def test_user_code_only_enters_via_configmap_owned_by_job():
    cm = build_configmap(RID, "print('x')", "in", "sandboxes", "uid-1")
    assert cm["data"] == {"main.py": "print('x')", "stdin": "in"}
    assert cm["metadata"]["ownerReferences"][0]["uid"] == "uid-1"
    job, _, c = spec()
    assert "print" not in str(job)  # code is not embedded in the Job
    assert [e["name"] for e in c["env"]] == ["KSC_TIMEOUT_SECONDS"]


def test_node_code_goes_in_main_js_with_the_same_hardening():
    from dispatcher.podspec import SandboxSettings, build_job
    cm = build_configmap(RID, "console.log(1)", None, "sandboxes", "uid", "node20")
    assert list(cm["data"]) == ["main.js"]
    job = build_job(RID, "node20", 30, SandboxSettings())
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert c["image"].startswith("ksc/runner-node")
    assert c["securityContext"]["readOnlyRootFilesystem"] is True


def test_runtime_class_and_placement_are_only_set_when_configured():
    from dispatcher.podspec import SandboxSettings, build_job
    plain = build_job(RID, "python3.12", 30, SandboxSettings())["spec"]["template"]["spec"]
    assert "runtimeClassName" not in plain and "nodeSelector" not in plain and "tolerations" not in plain
    s = SandboxSettings(runtime_class="gvisor", node_selector={"ksc.dev/pool": "sandbox"},
                        tolerations=[{"key": "ksc.dev/sandbox", "operator": "Equal", "value": "true",
                                      "effect": "NoSchedule"}])
    pod = build_job(RID, "python3.12", 30, s)["spec"]["template"]["spec"]
    assert pod["runtimeClassName"] == "gvisor" and pod["nodeSelector"] == {"ksc.dev/pool": "sandbox"}
    assert pod["tolerations"][0]["key"] == "ksc.dev/sandbox"
    assert pod["securityContext"]["runAsNonRoot"] is True  # placement never relaxes the hardening
