/** Snippets behind the "Try an attack" buttons. They mirror tests/attack/test_attacks.py; the
 *  expectation says what a healthy cluster does, so a visitor can judge the outcome. */
export interface Attack {
  id: string;
  label: string;
  language: "python3.12" | "node20";
  timeout: number;
  expect: string;
  code: string;
}

export const ATTACKS: Attack[] = [
  {
    id: "network",
    label: "Reach the network",
    language: "python3.12",
    timeout: 60,
    expect: "Every outbound attempt is closed; loopback (control) is OPEN; the run still succeeds.",
    code: `import socket

def tcp(host, port):
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); return "OPEN"
    except Exception as e:
        return "closed:" + type(e).__name__
    finally:
        s.close()

print("public 1.1.1.1:443   ", tcp("1.1.1.1", 443))
print("k8s api 10.96.0.1:443", tcp("10.96.0.1", 443))
print("cloud metadata       ", tcp("169.254.169.254", 80))
try:
    socket.getaddrinfo("example.com", 80); print("dns                   OPEN")
except Exception as e:
    print("dns                   closed:" + type(e).__name__)
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
print("loopback (control)   ", tcp("127.0.0.1", srv.getsockname()[1]))
`,
  },
  {
    id: "token",
    label: "Steal a service-account token",
    language: "python3.12",
    timeout: 20,
    expect: "False False False: there is no token and no secrets directory.",
    code: `import os
p = "/var/run/secrets/kubernetes.io/serviceaccount"
print(os.path.exists(p), os.path.exists(p + "/token"), os.path.exists("/var/run/secrets"))
`,
  },
  {
    id: "write",
    label: "Write outside /tmp",
    language: "python3.12",
    timeout: 20,
    expect: "Errno 30 (read-only file system) everywhere except /tmp.",
    code: `for path in ["/etc/pwned", "/usr/pwned", "/pwned", "/code/pwned"]:
    try:
        open(path, "w").write("x"); print(path, "WRITTEN")
    except OSError as e:
        print(path, "errno", e.errno, e.strerror)
open("/tmp/ok", "w").write("x"); print("/tmp ok")
`,
  },
  {
    id: "memory",
    label: "Eat all memory",
    language: "python3.12",
    timeout: 30,
    expect: "oom_killed: the 512Mi limit kills the process; the node is unaffected.",
    code: `x = b"x" * (2 * 1024 ** 3)
print(len(x))
`,
  },
  {
    id: "fork",
    label: "Fork bomb",
    language: "python3.12",
    timeout: 10,
    expect: "Contained by the PID limit; ends at the timeout (or OOM); other runs keep working.",
    code: `import os
while True:
    try:
        pid = os.fork()
    except OSError:
        continue
    if pid == 0:
        while True:
            pass
`,
  },
  {
    id: "loop",
    label: "Infinite loop",
    language: "python3.12",
    timeout: 5,
    expect: "timed_out at about 5 s, killed by the entrypoint wrapper (not the 35 s backstop).",
    code: `while True:
    pass
`,
  },
  {
    id: "output",
    label: "Unbounded output",
    language: "python3.12",
    timeout: 5,
    expect: "Output truncated at 1 MiB per stream with the truncated flag set; the control plane stays flat.",
    code: `import sys
line = "x" * 1000 + "\\n"
while True:
    sys.stdout.write(line)
`,
  },
  {
    id: "caps",
    label: "Mount, chroot, raw socket",
    language: "python3.12",
    timeout: 20,
    expect: "uid 10001, no capabilities, every privileged call returns EPERM (1).",
    code: `import ctypes, os, socket
print("uid", os.getuid())
st = dict(l.split(":\\t", 1) for l in open("/proc/self/status").read().splitlines() if ":\\t" in l)
print("CapEff", st["CapEff"].strip(), "NoNewPrivs", st["NoNewPrivs"].strip())
libc = ctypes.CDLL(None, use_errno=True)
print("mount ->", libc.mount(b"none", b"/tmp", b"tmpfs", 0, None), "errno", ctypes.get_errno())
try:
    os.chroot("/tmp"); print("chroot OK")
except OSError as e:
    print("chroot errno", e.errno)
try:
    socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP); print("raw socket OK")
except OSError as e:
    print("raw socket errno", e.errno)
`,
  },
];

export const HELLO: Record<"python3.12" | "node20", string> = {
  "python3.12": `import sys\nprint("hello from", sys.version.split()[0])\n`,
  node20: `console.log("hello from node", process.version);\n`,
};
