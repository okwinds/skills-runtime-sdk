## 1. Runtime Socket Liveness

- [x] 1.1 Add a regression test that proves a half-open or non-EOF client cannot block a later `runtime.status` request.
- [x] 1.2 Implement connection-level request handling so accepted sockets do not monopolize the main accept loop.
- [x] 1.3 Add a bounded request-read liveness timeout and ensure stalled connections are abandoned without leaving the server unhealthy.

## 2. Collab Wait Interactivity

- [x] 2.1 Add a regression test that runs `collab.wait` and `collab.send_input` from separate clients against the same runtime server.
- [x] 2.2 Refactor `collab.wait` handling so a pending wait no longer prevents other RPCs from being processed.
- [x] 2.3 Verify that a wait call pending on child input eventually returns after another client delivers input.

## 3. Verification

- [x] 3.1 Run the targeted runtime liveness regression tests with resource constraints and record the command/results in `docs/worklog.md`.
- [x] 3.2 Re-run existing runtime security/crash semantics tests that are directly adjacent to the changed code paths to confirm no regression.
