# Security Policy

## Supported versions

This maintained fork is in active development. Security fixes target the latest fork `main`; **0.3.0-dev.0** is an unreleased development snapshot. Inherited upstream tags and installers do not contain the fork's changes and are not maintained releases of this fork.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Private vulnerability reporting is enabled for [OpenFabric-Studio/openfabric-studio](https://github.com/OpenFabric-Studio/openfabric-studio/security). Use [Report a vulnerability](https://github.com/OpenFabric-Studio/openfabric-studio/security/advisories/new). Include the fork commit/version, sanitized reproduction steps, affected component and impact. Do not include personal recordings or credentials unless essential and explicitly requested.

There is no guaranteed response time or security-support SLA.

## Scope and deployment notes

- OpenFabric Studio is designed to run **locally**. The launch scripts (`dev.*`, `prod_run.*`) bind the backend to `127.0.0.1`, and the app has **no authentication**. Do not expose its ports to a network or the internet.
- Vulnerabilities in underlying engines should be reported to their maintainers. If the vulnerable behavior is in this fork's integration, report it here. Include exact engine/model revisions; their licenses and security policies are separate from this repository's MIT license.
