"""Attack suite: hostile code submitted through the public API. Each case asserts the run outcome
AND the cluster state afterwards (cleanup proof, nodes healthy). Every case has a positive
control where one is needed, so a denial cannot pass just because the probe itself was broken."""
import subprocess
import textwrap
import time
import uuid
from datetime import datetime

import pytest

from tests.e2e.conftest import (
    NS_SBX,
    NS_SYS,
    assert_cleaned_up,
    kubectl,
    wait_status,
    wait_terminal,
)

pytestmark = pytest.mark.attack

# Linux errno values. These tests run on a Mac, whose errno module differs (EAGAIN is 35 there).
EPERM, EAGAIN, EROFS = 1, 11, 30


def submit(client, code, timeout=30):
    r = client.post("/v1/runs", json={"language": "python3.12", "code": textwrap.dedent(code),
                                      "timeout_seconds": timeout})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def run(client, code, timeout=30, wait=None):
    rid = submit(client, code, timeout)
    result = wait_terminal(client, rid, timeout=wait or timeout + 60)
    assert_cleaned_up(rid)
    return result


def lines(result):
    return result["stdout"].strip().splitlines()


def nodes_ready():
    out = kubectl("get", "nodes", "--no-headers")
    assert out and all(" Ready" in line for line in out.splitlines()), out


def secs(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


# --- network -----------------------------------------------------------------------------------

def test_network_is_fully_denied_but_run_completes(client):
    pg_ip = kubectl("-n", NS_SYS, "get", "pod", "-l", "app=ksc-postgres", "-o",
                    "jsonpath={.items[0].status.podIP}")
    assert pg_ip
    result = run(client, f"""
        import socket
        def tcp(host, port):
            s = socket.socket(); s.settimeout(3)
            try:
                s.connect((host, port)); return "OPEN"
            except Exception as e:
                return "closed:" + type(e).__name__
            finally:
                s.close()
        print("public", tcp("1.1.1.1", 443))
        print("k8s-api", tcp("10.96.0.1", 443))
        print("other-pod", tcp("{pg_ip}", 5432))
        print("metadata", tcp("169.254.169.254", 80))
        try:
            socket.getaddrinfo("kubernetes.default.svc.cluster.local", 443); print("dns OPEN")
        except Exception as e:
            print("dns closed:" + type(e).__name__)
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        print("loopback", tcp("127.0.0.1", srv.getsockname()[1]))
    """, timeout=110)
    assert result["status"] == "succeeded", result
    out = dict(line.split(" ", 1) for line in lines(result))
    for target in ("public", "k8s-api", "other-pod", "metadata"):
        assert out[target].startswith("closed"), (target, out)
    assert out["dns"].startswith("closed"), out
    assert out["loopback"] == "OPEN", out  # control: the probe itself works
    nodes_ready()


# --- credentials and filesystem ---------------------------------------------------------------

def test_service_account_token_does_not_exist(client):
    result = run(client, """
        import os
        p = "/var/run/secrets/kubernetes.io/serviceaccount"
        print(os.path.exists(p), os.path.exists(p + "/token"), os.path.exists("/var/run/secrets"))
        # The kubelet always injects the kubernetes service address (enableServiceLinks only drops
        # other services). That is an address, not a credential: the network test shows it is
        # unreachable. What must be absent is any credential.
        # (GPG_KEY in the base image is Python's public release-signing fingerprint, not a credential)
        print(sorted(k for k in os.environ if any(w in k for w in
                     ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "AWS_", "DATABASE"))))
        print(sorted(k for k in os.environ if k.startswith("KUBERNETES_") and "SERVICE_HOST" not in k
                     and "PORT" not in k and "ADDR" not in k and "PROTO" not in k))
    """)
    assert result["status"] == "succeeded"
    assert lines(result) == ["False False False", "[]", "[]"]


def test_cannot_write_outside_tmp(client):
    result = run(client, """
        for path in ["/etc/ksc-test", "/usr/ksc-test", "/ksc-test", "/code/x", "/opt/ksc/x"]:
            try:
                open(path, "w").write("x"); print(path, "WRITTEN")
            except OSError as e:
                print(path, e.errno)
        open("/tmp/ok", "w").write("x"); print("tmp ok")
    """)
    assert result["status"] == "succeeded"
    out = lines(result)
    assert out[-1] == "tmp ok"  # control
    for line in out[:-1]:
        assert line.endswith(f" {EROFS}"), line


def test_filling_tmp_is_contained_and_reported(client):
    rid = submit(client, """
        import os, time
        with open("/tmp/big", "wb") as f:
            for _ in range(300):
                f.write(b"x" * 1048576); f.flush(); os.fsync(f.fileno())
        time.sleep(100)
    """, timeout=120)
    result = wait_terminal(client, rid, timeout=150)
    assert (result["status"], result["termination_reason"]) == \
        ("failed", "ephemeral_storage_exceeded"), result
    assert_cleaned_up(rid)
    nodes_ready()
    hello = run(client, "print('still fine')")
    assert hello["status"] == "succeeded" and hello["stdout"] == "still fine\n"


# --- resources --------------------------------------------------------------------------------

def test_memory_past_the_limit_is_oom_killed(client):
    result = run(client, "x = b'x' * (2 * 1024 ** 3)\nprint(len(x))")
    assert result["status"] == "oom_killed", result
    nodes_ready()


def test_pid_limit_stops_forking_at_the_cap(client):
    result = run(client, """
        import os, time
        kids, err = [], None
        try:
            while len(kids) < 600:
                pid = os.fork()
                if pid == 0:
                    time.sleep(30); os._exit(0)
                kids.append(pid)
        except OSError as e:
            err = e.errno
        print(len(kids), err)
    """, timeout=40)
    assert result["status"] == "succeeded", result
    count, err = lines(result)[0].split()
    assert int(err) == EAGAIN, result["stdout"]
    assert 20 < int(count) < 256, result["stdout"]  # kubelet podPidsLimit is 256
    assert secs(result["started_at"], result["finished_at"]) < 20  # stray children were killed


def test_fork_bomb_is_contained_and_neighbours_still_run(client):
    bomb = submit(client, """
        import os
        while True:
            try:
                pid = os.fork()
            except OSError:
                continue
            if pid == 0:
                while True:
                    pass
    """, timeout=10)
    wait_status(client, bomb, "running")
    neighbours = [submit(client, f"print({i} * 2)") for i in range(2)]
    for i, rid in enumerate(neighbours):
        res = wait_terminal(client, rid, timeout=90)
        assert res["status"] == "succeeded" and res["stdout"] == f"{i * 2}\n", res
    result = wait_terminal(client, bomb, timeout=90)
    assert result["status"] in ("timed_out", "oom_killed", "failed"), result
    assert result["status"] != "infra_error"
    for rid in [bomb, *neighbours]:
        assert_cleaned_up(rid)
    nodes_ready()


def test_infinite_loop_times_out_at_about_the_timeout(client):
    result = run(client, "while True:\n    pass", timeout=3)
    assert result["status"] == "timed_out" and result["termination_reason"] == "timeout"
    assert secs(result["started_at"], result["finished_at"]) < 3 + 8  # not the 33 s backstop


def dispatcher_rss_kb():
    out = kubectl("-n", NS_SYS, "exec", "deploy/ksc-dispatcher", "--", "sh", "-c",
                  "grep VmRSS /proc/1/status")
    return int(out.split()[1])


def test_unbounded_output_is_truncated_and_dispatcher_memory_stays_flat(client):
    run(client, "print('warm up')")  # warm the dispatcher's code paths first
    before = dispatcher_rss_kb()
    result = run(client, """
        import sys
        line = "x" * 1000 + "\\n"
        while True:
            sys.stdout.write(line); sys.stderr.write(line)
    """, timeout=5)
    after = dispatcher_rss_kb()
    assert result["status"] == "timed_out", result
    assert result["truncated"] is True
    assert len(result["stdout"]) <= 1024 * 1024 and len(result["stderr"]) <= 1024 * 1024
    assert len(result["stdout"]) > 512 * 1024  # real output was kept up to the cap
    assert after - before < 50 * 1024, f"dispatcher RSS grew {after - before} kB"


# --- isolation between runs and from the kernel -----------------------------------------------

def test_cannot_find_another_concurrent_runs_code_or_files(client):
    secret = f"SECRET-ISOLATION-TOKEN-{uuid.uuid4()}"
    a = submit(client, f"""
        import time
        open("/tmp/mine", "w").write("{secret}")
        print("{secret}")
        time.sleep(40)
    """, timeout=60)
    wait_status(client, a, "running")
    head, tail = secret[:17], secret[17:]  # the probe's own source never contains the whole token
    b = run(client, f"""
        import os
        tok = ("{head}" + "{tail}").encode()
        open("/tmp/own", "wb").write(tok)  # positive control: the scan must find this one
        hits = []
        def scan(path):
            try:
                with open(path, "rb") as f:
                    if tok in f.read(1 << 20):
                        hits.append(path)
            except OSError:
                pass
        for root, dirs, files in os.walk("/"):
            dirs[:] = [d for d in dirs if root + d not in ("/proc", "/sys", "/dev") and
                       os.path.join(root, d) not in ("/proc", "/sys", "/dev")]
            for name in files:
                scan(os.path.join(root, name))
        for pid in filter(str.isdigit, os.listdir("/proc")):
            for part in ("cmdline", "environ"):
                scan(f"/proc/{{pid}}/{{part}}")
        print(sorted(hits))
    """, timeout=90)
    assert b["status"] == "succeeded", b
    assert lines(b) == ["['/tmp/own']"], b["stdout"]
    # run A is still running while B finished; let it be cancelled and cleaned up
    assert client.get(f"/v1/runs/{a}").json()["status"] == "running"
    client.delete(f"/v1/runs/{a}")
    assert_cleaned_up(a)


def test_no_capabilities_no_privilege_no_mount_chroot_or_raw_sockets(client):
    result = run(client, """
        import ctypes, os, socket
        print("uid", os.getuid(), os.getgid())
        st = dict(l.split(":\\t", 1) for l in open("/proc/self/status").read().splitlines() if ":\\t" in l)
        print("CapEff", st["CapEff"].strip()); print("CapBnd", st["CapBnd"].strip())
        print("NoNewPrivs", st["NoNewPrivs"].strip())
        libc = ctypes.CDLL(None, use_errno=True)
        print("mount", libc.mount(b"none", b"/tmp", b"tmpfs", 0, None), ctypes.get_errno())
        try:
            os.chroot("/tmp"); print("chroot OK")
        except OSError as e:
            print("chroot", e.errno)
        try:
            socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP); print("raw OK")
        except OSError as e:
            print("raw", e.errno)
        try:
            os.setuid(0); print("setuid OK")
        except OSError as e:
            print("setuid", e.errno)
    """)
    assert result["status"] == "succeeded", result
    assert lines(result) == [
        "uid 10001 10001", "CapEff 0000000000000000", "CapBnd 0000000000000000", "NoNewPrivs 1",
        f"mount -1 {EPERM}", f"chroot {EPERM}", f"raw {EPERM}", f"setuid {EPERM}",
    ]


# --- admission: the cluster refuses bad pods even without the dispatcher ----------------------

POD = """
apiVersion: v1
kind: Pod
metadata: {{name: {name}}}
spec:
  automountServiceAccountToken: {automount}
  restartPolicy: Never
  {extra}
  securityContext: {{runAsNonRoot: true, runAsUser: 10001, seccompProfile: {{type: RuntimeDefault}}}}
  containers:
    - name: c
      image: busybox:1.36
      command: ["sleep", "60"]
      securityContext: {{allowPrivilegeEscalation: false, capabilities: {{drop: [ALL]}}, readOnlyRootFilesystem: {rofs}}}
      resources: {{limits: {{cpu: 100m, memory: 64Mi, ephemeral-storage: 16Mi}}}}
"""


def apply(manifest):
    return subprocess.run(["kubectl", "-n", NS_SBX, "apply", "-f", "-"], input=manifest, text=True,
                          capture_output=True, check=False)


def test_privileged_pod_applied_directly_is_rejected():
    r = subprocess.run(["kubectl", "-n", NS_SBX, "run", "evil-priv", "--image=busybox",
                        "--privileged", "--restart=Never"], capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "forbidden" in r.stderr and "PodSecurity" in r.stderr, r.stderr


def test_hostpath_pod_applied_directly_is_rejected():
    r = apply("""
apiVersion: v1
kind: Pod
metadata: {name: evil-hostpath}
spec:
  volumes: [{name: v, hostPath: {path: /}}]
  containers: [{name: c, image: busybox, volumeMounts: [{name: v, mountPath: /host}]}]
""")
    assert r.returncode != 0 and "restricted volume types" in r.stderr, r.stderr


def test_policy_denies_writable_rootfs_and_token_even_when_psa_passes():
    rw = apply(POD.format(name="evil-rw", automount="false", extra="", rofs="false"))
    assert rw.returncode != 0 and "ksc-sandbox-pod-guardrails" in rw.stderr, rw.stderr
    tok = apply(POD.format(name="evil-token", automount="true", extra="", rofs="true"))
    assert tok.returncode != 0 and "ksc-sandbox-pod-guardrails" in tok.stderr, tok.stderr


def test_nothing_the_attacks_applied_was_created():
    time.sleep(1)
    out = kubectl("-n", NS_SBX, "get", "pods", "-o", "name", check=False)
    assert "evil" not in out, out
