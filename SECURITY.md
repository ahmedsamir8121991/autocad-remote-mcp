# Security policy

AutoCAD Remote MCP gives an AI model access to AutoCAD on another PC, so security reports are taken seriously.

## Supported versions

| Version | Supported |
|---|---|
| 6.2.x | ✅ |
| 6.1.x | ⚠️ Please update |
| ≤ 6.0 | ❌ Unencrypted protocol: update both PCs |

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting instead:
[Report a vulnerability](https://github.com/ahmedsamir8121991/autocad-remote-mcp/security/advisories/new).

Please include the version, what an attacker could do, and steps to reproduce. **Never include your real password or pairing code.**

In scope, for example:
- Connecting, or reading or replaying traffic, without the pairing code and password
- Escaping safe mode: running commands, LISP or scripts, or reading or writing files outside the drawings folder
- Changing protected system variables (e.g. `SECURELOAD`, `TRUSTEDPATHS`)
- Recovering the password or key from stored settings

## Design summary

scrypt key derivation from password + pairing code, AES-256-GCM encryption of every request and reply, HMAC-SHA256 signing with replay protection, LAN-only networking, and brute-force lockout. See the README's **Security model** section for details.
