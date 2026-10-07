# Queue server licence (Redis / Valkey)

The job queue speaks the Redis protocol. What we may run, and why:

| Server | Licence | Allowed |
|---|---|---|
| Redis 6.x, 7.0, 7.2.x | BSD-3-Clause | **Yes.** Free for commercial use. The copyright and licence notice must be kept when Redis is redistributed (for the Debian / Ubuntu package: `/usr/share/doc/redis-server/copyright`). "Free" is not "no obligations". |
| Valkey (any version) | BSD-3-Clause | **Yes.** The Linux Foundation fork of Redis 7.2.4. |
| Redis 7.3 | unstable pre-release line | No (never for production). |
| Redis 7.4 and later | RSALv2 / SSPLv1 (8.0 adds AGPLv3 as a third choice) | **No.** |

So the baseline is **Redis 7.2.x or Valkey**. A floating image tag such as `redis:7-alpine` is not acceptable because it moves to
7.4+; pin the exact tag (for example `redis:7.2.x`) or use the OS package (Ubuntu 24.04 ships 7.0.15).

How it is enforced:

* `compliance/servers.py` asks the running server who it is (`INFO server`) at start-up. Outside `CDI_ENV=dev` a version that is
  not allowed stops the service; in dev it is a warning. `CDI_SERVER_LICENCE_ENFORCE=false` switches the check off (not advised).
* `infra/runpod/bootstrap_pod.sh` refuses to continue when the installed `redis-server` is newer than 7.2.
* The generated NOTICE file lists the Redis / Valkey notice duty.

This is the engineering reading, not legal advice; counsel confirms.
