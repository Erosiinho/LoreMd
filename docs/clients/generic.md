# Generic client

LoreMd exposes a standard MCP server over Streamable HTTP. Any compliant client
works with three pieces of information:

| Setting | Value |
|---|---|
| Transport | `streamable-http` |
| URL | `https://<your-domain>/mcp` |
| Header | `Authorization: Bearer lmd_…` |

Example with the Python MCP SDK:

```python
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "https://memory.acme.com/mcp"
HEADERS = {"Authorization": "Bearer lmd_xxxxxxxx_…"}


async def main() -> None:
    async with streamablehttp_client(URL, headers=HEADERS) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "memory_search", {"query": "database choice", "limit": 5}
            )
            print(result.content)


asyncio.run(main())
```

Responses are JSON: `{"matches": [...], "status": "ok"}` or
`{"matches": [], "status": "no_match"}`.
