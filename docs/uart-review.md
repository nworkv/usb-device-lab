# Offline UART evidence review

After stopping capture and creating a successful evidence export:

```sh
python3 -m usb_device_lab.uart_review --evidence var/results-uart/<UUID>/evidence-<export-UUID>
python3 -m unittest discover -s tests -p 'test_uart_review.py' -v
```

Creates review.html in that evidence directory. Open the local file in a browser
for readable attempts, candidate kernel reports with context/stack, byte ranges,
connection epochs, ambiguous associations, truncation and separate diagnostics.
The report is observational: no confirmed bug, exploitability, causation, healthy
host or complete UART evidence is inferred. Absence of recognized messages is
explicitly not a clean verdict. Categories may overlap.

Input: completed export marker plus matching capture IDs in summary/analysis;
summary schema 1 and observational semantics are required. Original JSON/raw
files are never modified. No UART/USB/SSH/processes or network resources are used.
All source text is HTML-escaped; output contains no JS, forms or external assets.
CSP blocks active/network content and allows only the built-in inline styling.
This is not secret redaction: exported messages may contain sensitive information.

Linux/POSIX filesystem operations: bounded regular inputs, leaf symlink rejection,
8 MiB per JSON input, 1 Mi characters per display field, 16 MiB rendered HTML.
Limits fail rather than silently truncate. Stop capture first; there is no atomic
multi-file input snapshot. Trusted directories are required.

Output uses a 0600 temporary file, fsync, and hard-link publication with an
exclusive final name. Existing review.html is never overwritten, including a
symlink. Filesystems without hard-link support fail explicitly. Failed publication
cleans the temporary file. Failure during final directory fsync can leave a
complete visible HTML file with unconfirmed power-loss durability; inspect before
using it. This is not an atomic transaction with the original evidence export.

The web panel allowlist is unchanged: it does not serve/download review.html.
This commit adds offline generation only, not a new web route or launcher.
Seven tests passed locally with actual filesystem/HTML parser and reconstructed
existing bounded JSON/file reader helpers. Full checkout/CI and real-browser
rendering were not tested. Kernel messages in tests are synthetic.
