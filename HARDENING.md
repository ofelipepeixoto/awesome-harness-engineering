# Local hardening proposal

No workflow has been enabled or dispatched. No real GitHub search was run by
the tests. Existing catalog data remains unchanged in this patch.

The updater limits each request to three attempts, with at most two waits of
10 seconds and a 15-second requests timeout. A plain permission 403 is final;
rate-limited 403, 429 and selected 5xx may retry. Server waits beyond the budget
return immediately rather than retrying early. Redirects are not followed.
This is a request/attempt budget, not a hard wall-clock deadline for the entire
catalog (requests read timeout does not cap a slowly streaming response).

HTTPS GitHub owner/repository URLs are normalized case-insensitively and clone
suffix/trailing slash removed. Credentials, ports, queries and fragments are
rejected. This does not resolve repository renames or aliases using GitHub IDs.
Discovery still returns the first 100 results per query, not exhaustive search.

Refresh/search failures abort before writing. Both outputs are serialized first;
each is replaced with a same-directory temporary file, flush/fsync/os.replace,
with cleanup on failure. This prevents torn individual files, but is NOT a
transaction across JSON and README: a crash between replacements can leave
different generations. Inspect the diff and regenerate before publication.

Run offline tests: `python -m unittest discover -s tests -v`. Tests stub the
requests module before import; no requests installation/network is needed.
Real requests/TLS behavior and remote workflow execution remain unverified.

License remains unresolved: README declares CC0 but LICENSE is absent at base
54ab2ea57882f77808f0e8494a08e4d418a1133b. This fork patch retains existing authorship
and does not invent a license, relicense upstream code or authorize independent
redistribution. Resolve that question before reuse outside this fork. Review
dependency pinning and editorial approval before any future automation.
