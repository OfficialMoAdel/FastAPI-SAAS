"""The tenant/tier/account census probe, as it was RUN on 2026-10-20.

It produced the environment facts recorded in `FINDINGS.md` §3: the only tenant
is `test-tenant-e2e` (the `system` slug the Oct-15 attempts used is gone), the
tier rows are `Free` and `Pro` (the earlier row was `pro`), and there are zero
`ab%` accounts.

The committed copy differs from the copy that ran ONLY in formatting -- the
imports are split and the one over-long SQL line is wrapped. No expression
changed. The copy as it ran was 1,152 B, sha256
`fe571fbcf4d36c25e524666e521382e39216241c7bd1eaa83f688a01cd14a4bb`.
"""

import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv("/home/ansible/.l8_env", override=True)


async def main() -> None:
    conn = await asyncpg.connect(
        host=os.environ["POSTGRES_SERVER"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
    )
    try:
        rows = await conn.fetch("SELECT id, slug, name, is_active FROM tenants ORDER BY slug")
        print("TENANTS:")
        for r in rows:
            print("   ", dict(r))
        tiers = await conn.fetch("SELECT id, name FROM tier ORDER BY name")
        print("TIERS:")
        for r in tiers:
            print("   ", dict(r))
        n = await conn.fetchval("SELECT count(*) FROM users WHERE username LIKE 'ab%'")
        print("AB USERS:", n)
        sample = await conn.fetch(
            "SELECT username, email, tenant_id FROM users WHERE username LIKE 'ab%' ORDER BY username LIMIT 5"
        )
        for r in sample:
            print("   ab:", dict(r))
    finally:
        await conn.close()


asyncio.run(main())
