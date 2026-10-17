# Client générique

LoreMd expose un serveur MCP standard en Streamable HTTP. Tout client conforme
fonctionne avec trois informations :

| Paramètre | Valeur |
|---|---|
| Transport | `streamable-http` |
| URL | `https://<votre-domaine>/mcp` |
| En-tête | `Authorization: Bearer lmd_…` |

Exemple avec le SDK MCP Python :

```python
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "https://memoire.acme.com/mcp"
HEADERS = {"Authorization": "Bearer lmd_xxxxxxxx_…"}


async def main() -> None:
    async with streamablehttp_client(URL, headers=HEADERS) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "memory_search", {"query": "choix de base de données", "limit": 5}
            )
            print(result.content)


asyncio.run(main())
```

Les réponses sont du JSON : `{"matches": [...], "status": "ok"}` ou
`{"matches": [], "status": "no_match"}`.
