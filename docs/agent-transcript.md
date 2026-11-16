# Agent transcript: Claude using the `ksc-sandbox` MCP server

A real, unedited one-shot agent session against the local kind cluster (WORKERS=2, Calico, runc).
Only this MCP server and its `run_code` tool were available to the agent. Tool inputs and outputs below are
copied from the session's `stream-json` log; the agent's reasoning is omitted.

- **Date:** 2026-11-16
- **Model:** `claude-haiku-5-5` (Claude Code CLI, `claude -p`)
- **Environment:** kind on a Mac mini (arm64, Colima VM), Kubernetes v1.36.4, containerd, runc
- **Server:** `mcp/ksc_mcp_server.py` over stdio, API on `127.0.0.1:8080` via `kubectl port-forward`
- **Turns:** 5

## Claude Code config snippet

```json
{
  "mcpServers": {
    "ksc-sandbox": {
      "command": "/path/to/k8s-sandbox-control-plane/.venv/bin/python",
      "args": ["/path/to/k8s-sandbox-control-plane/mcp/ksc_mcp_server.py"],
      "env": {"KSC_API_URL": "http://127.0.0.1:8080", "KSC_API_KEY": "<from: kubectl -n ksc-system exec deploy/ksc-api -- python -m api.keys create agent>"}
    }
  }
}
```

## Prompt

> You have a code-sandbox tool. Do these three things, then give one line per result: (1) in Python, compute the
> first 10 prime numbers; (2) in Node, try to open a TCP connection to example.com on port 443 with a 3 second
> timeout and report whether it worked; (3) in Python, try to write a file to /etc/hostname and report the exact
> error. Use only the sandbox tools.

## Tool calls

### 1. `run_code` (python3.12)

Code sent:

```python
def is_prime(n):
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True

primes = []
n = 2
while len(primes) < 10:
    if is_prime(n):
        primes.append(n)
    n += 1
print(primes)
```

Timeout: 30 s

Result returned to the agent:

```json
{
  "run_id": "c2f4aab5-4fa4-46ed-a233-60a562853716",
  "status": "succeeded",
  "exit_code": 0,
  "termination_reason": null,
  "language": "python3.12",
  "stdout": "[2, 3, 5, 7, 11, 13, 17, 19, 23, 29]\n",
  "stderr": "",
  "output_truncated": false
}
```

### 2. `run_code` (node20)

Code sent:

```javascript
const net = require('net');
const socket = net.createConnection({ host: 'example.com', port: 443 });
socket.setTimeout(3000);
socket.on('connect', () => { console.log('CONNECTED'); socket.destroy(); });
socket.on('timeout', () => { console.log('TIMEOUT after 3000ms'); socket.destroy(); });
socket.on('error', (err) => { console.log('ERROR: ' + err.code + ' ' + err.message); });
```

Timeout: 30 s

Result returned to the agent:

```json
{
  "run_id": "2ad28025-7786-4321-9d2a-cdd73b5ca979",
  "status": "succeeded",
  "exit_code": 0,
  "termination_reason": null,
  "language": "node20",
  "stdout": "TIMEOUT after 3000ms\n",
  "stderr": "",
  "output_truncated": false
}
```

### 3. `run_code` (python3.12)

Code sent:

```python
try:
    with open('/etc/hostname', 'w') as f:
        f.write('test\n')
    print('WRITE OK')
except Exception as e:
    print(f'{type(e).__name__}: {e}')
```

Timeout: 30 s

Result returned to the agent:

```json
{
  "run_id": "c7de9eac-5f9e-4ebe-be6a-8c2149557c1b",
  "status": "succeeded",
  "exit_code": 0,
  "termination_reason": null,
  "language": "python3.12",
  "stdout": "OSError: [Errno 30] Read-only file system: '/etc/hostname'\n",
  "stderr": "",
  "output_truncated": false
}
```

## Agent's final answer

1. Primes: [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
2. Node TCP to example.com:443 (3 s timeout): did not connect. It hit the timeout ("TIMEOUT after 3000ms"). The sandbox has no network.
3. Write to /etc/hostname: failed with `OSError: [Errno 30] Read-only file system: '/etc/hostname'`.
