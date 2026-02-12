Follow a spec-driven + TDD workflow:
- When changing code, write or update a minimal spec first.
- Add or update offline regression tests (no external keys/network required).
- Make small, reversible changes; explain decisions and trade-offs.

Safety:
- Avoid destructive operations unless explicitly requested.
- If an action is risky, ask for approval or ask the user.

