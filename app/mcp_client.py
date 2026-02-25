"""
Cliente HTTP asíncrono para el MCP Server REST API.
Conecta FastAPI con las 11 herramientas del MCP Server via HTTP.
"""

import httpx
import structlog
from typing import Any, Dict, List, Optional

logger = structlog.get_logger()


class MCPClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def initialize(self):
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        logger.info("mcp_client_initialized", base_url=self.base_url)

    async def list_tools(self) -> List[Dict[str, Any]]:
        resp = await self._client.get("/tools")
        resp.raise_for_status()
        data = resp.json()
        return data.get("tools", [])

    async def call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        resp = await self._client.post(f"/tools/{name}", json=args)
        resp.raise_for_status()
        return resp.json()

    async def health_check(self) -> Dict[str, Any]:
        resp = await self._client.get("/health")
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("mcp_client_closed")
