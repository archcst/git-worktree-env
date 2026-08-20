# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's security advisory
feature. Do not open a public issue for an unpatched vulnerability.

## Trust model

Project profiles are trusted local configuration. In particular, initializer
commands are executed by Bash. Do not install profiles from an untrusted source.
Secret contents are not copied into the registry, but profile files may reveal
local paths and should be treated as private configuration.

wte restricts generated-file and secret-link targets to the matched worktree.
Please report any path traversal, hook execution, registry corruption, or secret
exposure issue as a security vulnerability.
