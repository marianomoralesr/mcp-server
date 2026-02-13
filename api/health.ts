import type { VercelRequest, VercelResponse } from '@vercel/node';

export default function handler(_req: VercelRequest, res: VercelResponse) {
  res.setHeader('Access-Control-Allow-Origin', '*');

  return res.status(200).json({
    status: 'ok',
    server: 'trefa-mcp-server',
    version: '1.0.0',
    timestamp: new Date().toISOString(),
    endpoints: {
      mcp: '/api/mcp',
      health: '/api/health',
    },
    documentation: 'https://modelcontextprotocol.io/docs',
  });
}
