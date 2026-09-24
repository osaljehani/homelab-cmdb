"""Application-side authentication for the web UI.

Four modes, selected by CMDB_AUTH_MODE (see cmdb/config.py):

* ``none``   -- no gate at all; byte-identical to the app before this existed.
* ``local``  -- username + password against the ``users`` table (the default).
* ``oidc``   -- authorization code + PKCE against any OIDC provider.
* ``proxy``  -- identity read from a reverse proxy's headers (this homelab's
  deployment, where an Authentik outpost has already authenticated the request).

Submodules: :mod:`passwords` (scrypt), :mod:`session` (signed-cookie principal),
:mod:`proxy` (header principal), :mod:`oidc` (federated login), and
:mod:`middleware` (the gate itself).
"""
