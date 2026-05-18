"""Pure Pydantic models shared by core (server) and the magpie CLI.

Zero Django / DRF imports - this is the single source of truth for the
cross-boundary shapes (config + wire), consumed by both sides via an
editable path dependency (NOT a uv workspace; see project memory
project_schema_authority_northstar).
"""
