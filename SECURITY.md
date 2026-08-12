# Security policy

## Supported version

Security fixes are applied to the current `0.3.x` line. This project is a
local, single-user application; the API is not designed to be exposed to an
untrusted network.

## Reporting a vulnerability

Do not open a public issue containing exploit details or private inventory.
Use [GitHub's private vulnerability reporting
flow](https://github.com/metemorris/loadout/security/advisories/new). Include a
minimal synthetic reproduction, the affected version, and the expected impact.

## Deployment boundary

The API binds to `127.0.0.1`, has no authentication layer, and accepts writes
that mutate local YAML state after application-level confirmation. Do not bind
it to a public interface or put it behind a public reverse proxy without adding
authentication, authorization, request-size limits, and deployment-specific
CSRF protections.

## Dependency audit notes

As of 2026-08-11, the newest published compatible Python dependencies still
receive advisory matches whose patched versions are not available from the
configured package index. The affected code paths are outside LoadOut's use:

- LoadOut does not call `click.edit`.
- It does not parse HTML forms or trust `request.url` for authorization.
- It does not mount Starlette `StaticFiles` or `HTTPEndpoint` routes.
- Findings in pytest and setuptools affect development/build tooling, not the
  running local service.

These are mitigations, not blanket dismissals. Re-run `pip-audit` when upstream
releases become available and upgrade promptly. The frontend lockfile should be
checked with `npm audit` before each release.
