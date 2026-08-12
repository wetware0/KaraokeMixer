# Security policy

## Supported version

Security fixes are applied to the latest commit and latest tagged release on
`main`. Older source snapshots are not maintained.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** action on the repository's
Security tab. Do not open a public issue for an undisclosed vulnerability and
do not include private media, credentials, cookies, or library paths in a
report.

Include the affected version, Windows/Python versions, reproduction steps,
impact, and the smallest safe proof of concept. You should receive an initial
response within seven days.

## Local-only security boundary

Karaoke Media Manager is an unauthenticated, single-user desktop service. It
can download media, write tags and lyrics, move files, and send files or
folders to the Recycle Bin. The bundled launcher binds only to
`127.0.0.1`, and the application rejects non-loopback clients, untrusted Host
headers, and cross-origin browser requests by default.

Do not expose port 8000 directly to a LAN or the internet. The
`KARAOKE_ALLOW_REMOTE=1` escape hatch is for an operator who has deliberately
placed authentication, TLS, request-size limits, and trusted proxy handling in
front of the service. The project does not provide or support that deployment.

YouTube browser cookies and cookie files remain local settings. Never attach
them to an issue or commit them to the repository.
