# HomeLabCMDB Roadmap

Shipped features are recorded in [`CHANGELOG.md`](../CHANGELOG.md). What remains:

1. **Package & version inventory (CVE-aware)** Track installed packages per host; highlight
   hosts needing security updates.
2. **Scheduled collection** `cmdb collect` runs on demand only. Remaining: scheduled/background
   runs and concurrent-run locking.
