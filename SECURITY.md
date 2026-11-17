# Security Policy

## Supported Versions

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |

## Threat Model & Guarantees

`weavegraph` runs **100% locally** on static Python/TypeScript ASTs and file dependency graphs without executing untrusted repository code (`ast.parse` only, never `eval` or `exec`).

To report a security issue, please email `sendemailtonagarjuna@gmail.com` with reproduction steps before public disclosure.
