# AI-assisted implementation record

## Request and scope

On 2026-11-14 the user requested implementation of the FastZip README in the local ECE779 directory. After reviewing the proposal, Codex described the architecture and unresolved choices. The user accepted those defaults and explicitly requested creation of the GitHub `develop` branch and upload of the code there. Repository team ownership tables were treated as design context, not as a restriction to one contributor's portion.

## Decisions implemented

- Full application rather than only Xi Chen's infrastructure portion; preserve the original proposal as the design record.
- React/TypeScript frontend, synchronous FastAPI/SQLAlchemy HTTP handlers, PostgreSQL coordination, and S3-compatible media storage. No Redis/RabbitMQ.
- Atomic per-account upload admission, SKIP LOCKED claims, renewable leases, bounded retries, and attempt-specific output fencing. Processing is at least once with a single accepted publication.
- Configurable quality presets rather than target-size guarantees; show real workflow states without fabricated encoding percentages. English UI by default.
- Separate local Compose and production Swarm configurations, file-mounted production secrets, public-key-only encrypted backups, and fresh-database restore reconciliation.
- DigitalOcean's root-disk alert does not satisfy an attached-Volume alert; add a host Volume timer with separately configured SMTP.
- Deployment scripts/configuration are delivered without claiming that paid cloud resources were created or live infrastructure was verified.

## Tools and checks

Codex read the repository via the connected GitHub API, created `develop`, cloned it into the requested folder, and authored the implementation. It used isolated local toolchains, real PostgreSQL tests, Pillow/FFmpeg, an HTTP S3 development server, age encryption and PostgreSQL recovery tools. Browser verification used Microsoft Playwright MCP as requested by the user. Failed migration/validation cases found during implementation were corrected and tests rerun; record the final results in docs/validation.md.

See repository commits and CI runs for concrete changes/checks. These notes describe this implementation session, not a claim of team review or completion of the proposal's later cloud experiments. Review generated code, run the provided checks, and collect deployment evidence before presenting cloud requirements as achieved.

## Local-first release and frontend review, 2026-11-20

**Request.** “阅读proposal，优化当前的前端并检查这个项目还有什么没做的。我现在需要：一键部署到云端，需要填信息我自己填，先跑本地。”

**AI contribution.** Codex compared the proposal, course project requirements, and current implementation. It updated the frontend and wrote a Windows entry point that packages a published commit and uses the existing Swarm release script. The deployment workflow separates one-time infrastructure preparation from an application release.

**Issue found and verification.** The original upload handler awaited a job-history refresh after a successful upload, so a failed refresh could surface as an upload error. It also calculated account quota from the current page of history. The revised frontend reports upload and refresh separately and uses the server's account-wide count. Verification covered a PostgreSQL pagination regression check, the live Compose image and video workflows, and an Edge browser test at 390px. The Windows release entry point was exercised with simulated SSH and Docker. See [validation evidence](docs/validation.md) for observed results and the remaining team review and cloud checks.
