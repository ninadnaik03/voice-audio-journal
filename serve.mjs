import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';

const port = Number(process.env.PORT || 8000);

createServer(async (request, response) => {
  try {
    const path = request.url === '/' ? 'index.html' : request.url.slice(1);
    const content = await readFile(new URL(path, import.meta.url));
    const type = path.endsWith('.html')
      ? 'text/html; charset=utf-8'
      : path.endsWith('.jpg') || path.endsWith('.jpeg')
        ? 'image/jpeg'
        : 'application/octet-stream';
    response.writeHead(200, { 'Content-Type': type });
    response.end(content);
  } catch {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Not found');
  }
}).listen(port, () => console.log(`Voice Audio Journal: http://localhost:${port}`));
