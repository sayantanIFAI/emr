# Object store: replacing MinIO (decision record, 2026-10-06)

Status: **decided: SeaweedFS.** The product code is store-neutral (below); the infrastructure
swap (compose, pod bootstrap) is **pending verification** against a real SeaweedFS binary. Every
number here is labelled: SOURCE (read on the page named) / MEASURED / UNVERIFIED.

## Why MinIO has to go

- AGPL-3.0 (SOURCE: the vonng.com and blocksandfiles.com pages cited below), and the product is
  sold hosted **and** installed, which is what makes AGPL a problem.
- The community repository was marked "no longer maintained" and archived on 2026-02-12, and the
  community edition ships as source only: no pre-built binaries, RPM/DEB or Docker images.
  SOURCE: vonng.com "MinIO is dead", blocksandfiles.com 2025-06-19 (admin features removed).
  `infra/runpod/bootstrap_pod.sh` still downloads a MinIO binary from `dl.min.io`: that path is
  not something to rely on.

## What the product needs from a store (the whole list)

`head_bucket`, `create_bucket`, `put_object`, `get_object`, a presigned GET (SigV4), path-style
addressing, via boto3 (`src/cdi_adapter/storage.py`). No multipart, versioning, tagging, lifecycle,
ACL or SSE calls are made today (MEASURED: `git grep` of the code). Any S3 server that honours
these works; changing store is `CDI_S3_ENDPOINT_URL` + keys.

## The two candidates

| | RustFS | SeaweedFS |
|---|---|---|
| Licence | Apache-2.0 (SOURCE: github.com/rustfs/rustfs README) | Apache-2.0 (SOURCE: search results, project site) |
| Maturity | 1.0.0 GA **2026-09-16**, 1.0.1 2026-10-03 (SOURCE: GitHub releases). About 3 weeks of GA history. | Over ten years of releases; latest 4.48 (SOURCE: GitHub releases, opentechhub / elest.io comparisons) |
| S3 calls we use | claimed (SOURCE: docs, GA article); UNVERIFIED by me | documented: Create/Head/ListBuckets, Put/GetObject, presigned URLs via SigV4 (SOURCE: SeaweedFS wiki "Amazon S3 API"); UNVERIFIED by me |
| Release integrity | SHA-256 checksums + provenance files published (SOURCE: releases page) | MD5 checksums only on the releases page (SOURCE) |
| Deployment | single process; API :9000, console :9001 (SOURCE: README) | `weed server -s3` runs master + volume + filer + S3 gateway together (SOURCE: wiki); several internal ports |
| Security advisories (GitHub page 1 of 4, read 2026-10-06) | 10 shown: 1 critical (console stored XSS, 2026-06), 5 high (IAM / policy conditions, Object Lock treated as absent when bucket metadata unreadable; 2026-08), rest moderate. Fixed versions not shown on the page. | 10 shown, 6 critical, all 2026-09: unauthenticated S3-gateway gRPC PutIdentity/RemoveIdentity, missing authorisation in the volume server, SFTP, SSRF, and filer-backend injection. Fixed versions not shown on the page. |
| Known limits | single-node single-drive deployment cannot be expanded in place (SOURCE: README) | operationally heavier: more moving parts than a single binary |
| Small objects | claims 2.3x MinIO at 4 KB (vendor claim, UNVERIFIED) | built for many small files (SOURCE: comparisons); UNVERIFIED by me |

Counting advisories does not rank the projects: one page of each was read, severity is
self-reported, and the two products expose different surfaces. The point is what each demands
of the operator: RustFS needs its IAM / Object Lock features treated carefully and kept current;
SeaweedFS needs every non-S3 port closed.

## Decision: SeaweedFS, with conditions

Reason: a decade of production history for a store that will hold patients' original documents
outweighs RustFS's simpler single binary. RustFS is three weeks past GA with a run of high-severity
IAM / policy advisories in the two months before GA. This is a judgement, not a measurement.

Conditions (each is a requirement for the infra swap):

1. **Pin a version at or after the fixes** for the 2026-09 advisories (confirm on the advisory
   pages, which do not list fixed versions in what I read) and pin the sha256 **we** compute, since
   upstream publishes MD5 only.
2. **Only the S3 port is reachable by clients.** Master, volume, filer and their gRPC ports bind to
   loopback or the private network; SFTP, WebDAV, the Lance gateway and remote-storage mounts stay
   off. TLS terminates in front of the S3 port.
3. **Least-privilege keys.** An identity scoped to the one bucket (`Read:cdi-documents`,
   `Write:cdi-documents`, `List:cdi-documents`, SOURCE: wiki "S3 Credentials"); no `Admin` key in
   the application's environment. `storage.ping()` was changed to `head_bucket` for this reason
   (a scoped key cannot call `ListBuckets`).
4. **Run the integration tests and a migration rehearsal against the real binary** before it ships
   (below). Until then this is a recommendation, not a verified swap.

If SeaweedFS fails condition 4, RustFS is the fallback, and because the application only speaks S3
the switch is configuration, not code.

## Migrating existing data (the pilot pod's `/workspace/minio-data`)

Keys are `documents/<sha[:2]>/<sha>/...` and the database stores `s3://<bucket>/<key>`, so a
bucket-to-bucket copy preserving keys is lossless. Copy with any S3 client (`aws s3 sync`,
`rclone sync`, `mc mirror`) from the running MinIO to the new store, then compare object counts and
sizes, then spot-check SHA-256 of a sample against `source_document.sha256` (the database holds it).
Do not delete the MinIO volume until that comparison is clean.

## NOT verified yet

- That SeaweedFS 4.48 (or RustFS 1.0.1) accepts every call above from boto3, including a SigV4
  presigned GET and path-style addressing. The product-side calls are unit-tested with a stubbed
  client (`tests/test_storage_unit.py`); the server side needs the integration suite.
- Exact `weed server` flags for this layout (`-s3`, `-s3.config`, `-s3.port`, `-ip.bind`, `-dir`) and
  its health endpoint: the wiki confirms `weed server -s3` and the credentials file format; the
  rest is to be read from `weed server -h` on the binary, not recalled.
- Behaviour on the pod's `/workspace` (MooseFS, FUSE): MinIO needed care there; neither candidate
  has been tried on it.
- Latency, throughput and memory against MinIO on the pilot workload: no number exists.
- The advisories' fixed versions.
