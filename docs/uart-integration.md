# UART integration CI: stage 8

Adds two Linux integration tests using actual UARTConfig, UARTReceiver, campaign,
Mutator/DeviceConfig, checkpoint, evidence exporter and web supervisor/server.
The only executor substitution is a local Python child creating a readiness file
and sleeping; this avoids Raw Gadget hardware access. The lock root is a temporary
directory and UART is a real Linux PTY. HTTP uses actual loopback sockets.

Cases:
1. HTTP start -> real capture -> saved attempt -> offline report export ->
   authenticated evidence download, with byte/text/context checks and child reap.
2. Cancel a mutated attempt -> compatible resume -> identical generated input in
   a new capture UUID -> separate exports -> completed progress and child reap.
Messages are synthetic; these tests do not reproduce a real kernel failure.

Run on a complete checkout:

```sh
python3 -m unittest discover -s tests -p 'test_uart_integration.py' -v
python3 -m unittest discover -s tests -p 'test_uart*.py' -v
```

New uart-integration.yml runs on UART-branch pushes, relevant PRs and manual
workflow dispatch. Ubuntu/Python 3.11 and 3.12, read-only repository permissions,
15-minute job timeout. It installs the project, compiles all Python modules/tests,
runs all UART tests, and checks browser JS syntax using Node.js. Existing workflows
are unchanged. It never requests /dev/raw-gadget, KCOV or physical UART hardware.

Preparation verified Python test syntax and parsed the workflow YAML. The two
integration tests and whole-checkout UART suite were NOT executed in the assistant
environment because the complete repository checkout was unavailable there.
No CI success is claimed; GitHub Actions must confirm the first run after push.
This is intentionally not another mock-only green test claim. Browser rendering,
real USB enumeration, electrical UART settings and physical failure/recovery
remain stand acceptance work. No product behavior changes in this commit.
