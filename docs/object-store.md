# Object store: MinIO replaced by SeaweedFS (decision record, 2026-10-06)

Status: **SeaweedFS 4.48 is wired in** (compose, pod bootstrap, tests) and was exercised on a local
PC against the product's own S3 code. It has **not** been run on the pod, on Linux, or on the
pod's MooseFS `/workspace` (see "NOT verified"). Every number is labelled SOURCE (read on the page
named) / MEASURED (run here, local Windows PC, indicative) / UNVERIFIED.

## Why MinIO had to go

- AGPL-3.0 (SOURCE: the vonng.com and blocksandfiles.com pages below), and the product is sold
  hosted **and** installed, which is what makes AGPL a problem.
- The community repository was marked "no longer maintained" and archived on 2026-02-12, and the
  community edition ships as source only: no pre-built binaries, RPM/DEB or Docker images.
  SOURCE: vonng.com "MinIO is dead", blocksandfiles.com 2025-06-19 (admin features removed).

## What the product needs from a store (the whole list)

`head_bucket`, `create_bucket`, `put_object`, `get_object`, a presigned GET (SigV4), path-style
addressing, via boto3 (`src/cdi_adapter/storage.py`). No multipart, versioning, tagging, lifecycle,
ACL or SSE calls are made today (MEASURED: `git grep`). Any S3 server that honours these works;
changing store is `CDI_S3_ENDPOINT_URL` + keys.

## The two candidates

| | RustFS | SeaweedFS |
|---|---|---|
| Licence | Apache-2.0 (SOURCE: README) | Apache-2.0 (SOURCE: project site, comparisons) |
| Maturity | 1.0.0 GA **2026-09-16**, 1.0.1 2026-10-03; about 3 weeks of GA history (SOURCE: GitHub releases) | releases for over ten years; 4.48 (SOURCE: GitHub releases) |
| S3 calls we use | claimed; not tested by me | **MEASURED: all of them work** (below) |
| Release integrity | SHA-256 + provenance published (SOURCE) | MD5 files only; GitHub's release-asset API publishes a sha256 per asset (MEASURED: it equals the sha256 I computed for the Windows zip) |
| Deployment | single process, API :9000 + console :9001 (SOURCE: README) | `weed server -s3`: master + volume + filer + S3 gateway in one process, 8 listeners (MEASURED) |
| Security advisories (page 1 of the GitHub list, read 2026-10-06) | 10 shown: 1 critical (console stored XSS, 2026-06), 5 high (IAM/policy, Object Lock treated as absent when bucket metadata unreadable; 2026-08) | 10 shown, 6 critical, 2026-09 (details under "Residual risks"). Fixed versions are not stated on the advisory pages I read ("patched: none listed") |
| Known limits | single-node single-drive deployment cannot be expanded in place (SOURCE: README) | more moving parts |
| Small objects | vendor claims 2.3x MinIO at 4 KB (UNVERIFIED) | see MEASURED numbers below |

Counting advisories does not rank the projects: one page of each was read, severity is
self-reported, and the products expose different surfaces.

## Decision: SeaweedFS

A decade of production history for a store that holds patients' original documents outweighs
RustFS's simpler single binary; RustFS is three weeks past GA with a run of high-severity IAM /
policy advisories in the two months before it. This is a judgement, not a measurement. Because the
application only speaks S3, switching to RustFS (or anything else) is configuration, not code.

## What was changed

| File | Change |
|---|---|
| `src/cdi_adapter/storage.py` | `ping()` asks `head_bucket` for our bucket (a bucket-scoped key cannot `ListBuckets`); `ensure_bucket` tolerates the creation race; `ensure_bucket_when_ready` + `python -m cdi_adapter.storage` retry while the store starts |
| `infra/runpod/start_objectstore.sh` (new) | pinned binary (version + sha256), identities from the environment, hardened start, bucket created with the admin identity; idempotent. Called by `bootstrap_pod.sh` step 4 |
| `infra/compose/docker-compose.yml`, `seaweedfs/s3.json` | `objectstore` (`chrislusf/seaweedfs:4.48`) + `createbuckets` job; only S3 published, on host loopback |
| docs, Makefile, READMEs, tests | MinIO wording removed; `tests/test_storage_unit.py` (stubbed client), `tests/test_storage_integration.py` (live store, marker `objectstore`) |

### Hardening flags (each exists in `weed server -h`, 4.48; each is a default that is wrong for us)

| Flag | Default | Why it is set |
|---|---|---|
| `-master.telemetry=false` | **true**: reports "anonymous cluster statistics" to `telemetry.seaweedfs.com` (SOURCE: `weed server -h`) | no outbound data from a clinical system. MEASURED: no non-loopback connection while running with it off |
| `-ip.bind=127.0.0.1` | all interfaces | MEASURED: all 8 listening sockets are on 127.0.0.1 (S3 `0.0.0.0` only inside the compose container, published on host loopback) |
| `-s3.port.iceberg=0`, `-s3.port.lance=0` | on (8181, 9101) | gateways we do not use; the Lance gateway had a path-traversal advisory |
| `-s3.iam=false` | on | embedded IAM API not needed; identities come from the file |
| `-s3.autoCreateBucket=false` | true | a typo in a bucket name must fail, not create a bucket |
| `-s3.allowedOrigins=<webapp origin>` | `*` | MEASURED: with the default any origin was reflected in the CORS reply; now a foreign origin's preflight is 403 |
| `-volume.max=0`, `-master.volumeSizeLimitMB=1024` | 8 volumes x 30 GB | volume count follows free disk, in 1 GB volumes |

Identities (`s3.json`, bucket-scoped actions, SOURCE: SeaweedFS wiki "S3 Credentials"):
`cdi-app` = `Read/Write/List:cdi-documents` only (this is the application's key); `cdi-bucket-admin`
= `Admin`, used only to create the bucket. The pod script generates the admin secret on first run
(`$WS/seaweedfs/admin.env`, mode 600 where the filesystem honours it) and writes `s3.json` from
the environment each start, so no secret is on a command line.

## Verified (MEASURED, Windows PC, SeaweedFS 4.48 `weed.exe`, loopback, product's own `storage.py`)

- `ping`, `ensure_bucket`, `put_bytes` / `get_bytes` byte-exact (300 KB PNG-like and 40 MB objects),
  content type kept, overwrite, `NoSuchKey` on a missing key: pass.
- Presigned GET (SigV4, path-style) fetched by a plain HTTP client without credentials: pass; a
  tampered signature: 403; a presigned URL for a missing key: 404; an unsigned request: 403.
- Least privilege: the app key sees only its own bucket in `ListBuckets`, cannot create another
  bucket (403), cannot write to another bucket (403); a wrong secret: 403.
- Restart durability: 26 objects and both identities survived a restart; the start script is
  idempotent (second run on existing data, "already exists" treated as success).
- Latency, 300 KB objects, 200 sequential: put p50 8.9 ms / p95 11.6 ms; get p50 4.6 ms /
  p95 6.3 ms. 8 threads x 25 puts: 223 puts/s, 0 corrupt. 40 MB single PUT 0.58 s, GET 0.21 s.
  No MinIO number exists to compare with (MinIO was not run).
- The pinned sha256 of `linux_amd64.tar.gz` (`4a7d1083...1124`, 46,067,365 bytes) is GitHub's own
  digest for the release asset; the same method reproduced my hash for the Windows asset.
- A bug the first script run found and is fixed: right after start, `weed shell` is not ready, so
  a listing came back empty and bucket creation then failed on "already exists"; creation now
  retries and treats "already exists" as success.

## Residual risks (read before shipping)

1. **S3 gateway gRPC identity API.** Advisory GHSA-5fx4-c9qp-36jc (critical, SOURCE: GitHub): the
   gateway's gRPC `PutIdentity` / `RemoveIdentity` need no authentication, so a peer that can reach
   the gRPC port can create an `Admin` identity. Listed as affecting 4.45 and earlier, **no patched
   version listed**; whether 4.48 is fixed is UNVERIFIED. The port cannot be switched off
   (`-s3.port.grpc=0` still listens on 19000: MEASURED). Mitigation in place: bound to 127.0.0.1, so
   exploiting it needs code running on the same host; **do not run other tenants' or untrusted code
   next to the store**, and do not publish the port. Stronger: mTLS through `security.toml`
   (`[grpc.s3]`, mentioned in the advisory) or a test of 4.48 with a gRPC client.
2. **Volume-server / SFTP / SSRF advisories** (2026-09): affect components that are either not
   started (SFTP, WebDAV, MQ, Lance, Iceberg) or bound to loopback; GHSA-39jr-32mg-hjwj concerns the
   optional *Rust* volume server (4.35-4.41); I did not check which volume-server implementation
   `weed server` starts in 4.48. Re-read the advisory list when upgrading.
3. **POST-policy uploads skip bucket policies** (GHSA-qmx6-fpcw-q3xg, moderate, affects <=4.45): the
   product uses neither POST uploads nor bucket policies.
4. No TLS between the app and the store: both are on one host / one compose network. Put TLS in
   front of the S3 port before it is reachable from anywhere else.

## Migrating existing data (the pilot pod's `/workspace/minio-data`)

Keys are `documents/<sha[:2]>/<sha>/...` and the database stores `s3://<bucket>/<key>`, so a
bucket-to-bucket copy preserving keys is lossless. Copy with any S3 client (`aws s3 sync`,
`rclone sync`, `mc mirror`) from the running MinIO to the new store, then compare object counts and
sizes, then spot-check SHA-256 of a sample against `source_document.sha256`. Do not delete the
MinIO volume until that comparison is clean. (Not rehearsed: no MinIO data was available here.)

## NOT verified

- **Linux and the pod.** Everything above ran on Windows. `start_objectstore.sh` was run with
  `weed.exe` under Git Bash (with `setsid` absent, which the script tolerates); the Linux tarball
  download + sha256 check path, `setsid`, and the Linux binary are untested.
- **MooseFS `/workspace`.** Not tried; MinIO needed care there. If SeaweedFS misbehaves on it, set
  `CDI_OBJECTSTORE_DIR` to a local disk and sync, or fall back to RustFS.
- **Docker compose.** Not run (no Docker here). The healthcheck uses `curl` because the image's
  Dockerfile installs it (SOURCE: `docker/Dockerfile.go_build` at 4.48; I did not confirm that file
  is the one CI uses for the published tag). The tag `chrislusf/seaweedfs:4.48` exists (SOURCE:
  Docker Hub API); named-volume ownership for the image's non-root user is unchecked.
- **DB-backed integration tests** (`-m integration`: ingest, pipeline) were not run: they need
  PostgreSQL, and the one listening on this PC is not mine to write to. The S3 layer they use is
  covered by `tests/test_storage_integration.py` (6 passed live).
- Throughput against MinIO, memory use, behaviour at millions of objects, and the advisories'
  fixed versions.

## Re-running the checks

```bash
# any store; add the admin key to include the least-privilege test
CDI_S3_ENDPOINT_URL=http://127.0.0.1:9000 CDI_S3_ACCESS_KEY=... CDI_S3_SECRET_KEY=... \
CDI_S3_ADMIN_ACCESS_KEY=... CDI_S3_ADMIN_SECRET_KEY=... pytest tests/test_storage_integration.py
```
