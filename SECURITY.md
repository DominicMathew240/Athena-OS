# Security policy

**Pre-release status:** public release and the reporting channel below must be verified before announcement. The listed mailbox is existing project configuration; delivery and staffing have not been verified by this planning update. Do not send sensitive reports until the published release policy confirms an operational private channel. Maintainers must test a benign private report before release.

## Supported versions

No public release support commitment is established by this pre-release policy. Before release, maintainers must publish the exact supported SDK and CLI release lines and end-of-support policy. Package version declarations alone do not establish a released or supported version.

## Reporting a vulnerability

Please do **not** open a public issue for security vulnerabilities.

After the released policy confirms this mailbox is operational, email `security@athena-forge.dev` with:

- A description of the vulnerability
- Steps to reproduce, or a minimal proof of concept
- The affected package and version
- Any suggested remediation

The proposed response target is acknowledgment within 72 hours, subject to confirmed staffing before release. We intend to acknowledge reports and will work with you to validate
and remediate the issue before any public disclosure.

## Scope boundary

This security policy covers the open-source packages in this repository. It does
**not** cover the closed-source Athena Forge control plane, Athena Lab, or
proprietary model weights. After mailbox verification, reports about closed systems may use the
same private address for internal routing.

## Disclosure policy

We follow coordinated disclosure. Once a fix is available, we will publish a
security advisory and release a patched version. We ask that reporters not
disclose the issue publicly until we have done so.
