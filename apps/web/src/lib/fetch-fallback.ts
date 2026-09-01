import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

/**
 * Network-resilient fetch for Telegraph engine calls.
 *
 * Some networks (including this dev machine's) drop TLS handshakes whose
 * ClientHello fingerprint isn't a known browser/curl shape — undici dies with
 * `fetch failed / Connect Timeout` while curl negotiates fine. Rather than
 * debug the middlebox, every engine request tries undici first and silently
 * retries through curl on any network-level throw. On clean networks (e.g. the
 * Railway deployment) the fallback never triggers.
 */

const execFileAsync = promisify(execFile);

const execOptions = { timeout: 30_000, maxBuffer: 10 * 1024 * 1024 };

function flattenHeaders(init?: RequestInit): Record<string, string> {
  const out: Record<string, string> = {};
  const h = init?.headers as HeadersInit | undefined;
  if (!h) return out;
  if (h instanceof Headers) {
    h.forEach((v, k) => {
      out[k] = v;
    });
    return out;
  }
  if (Array.isArray(h)) {
    for (const pair of h) {
      if (Array.isArray(pair)) out[pair[0]] = String(pair[1]);
    }
    return out;
  }
  for (const [k, v] of Object.entries(h)) out[k] = String(v);
  return out;
}

function parseCurlOutput(stdout: string): Response {
  // `curl -D -` writes the header block, a CRLF blank line, then the body.
  const sep = stdout.indexOf('\r\n\r\n');
  if (sep < 0) return new Response(stdout, { status: 200 });
  const headerBlock = stdout.slice(0, sep);
  const body = stdout.slice(sep + 4);
  const lines = headerBlock.split('\r\n');
  const statusMatch = /^HTTP\/[\d.]+\s+(\d+)/.exec(lines[0] ?? '');
  const status = statusMatch ? Number(statusMatch[1]) : 200;
  const headers: Record<string, string> = {};
  for (const line of lines.slice(1)) {
    const idx = line.indexOf(':');
    if (idx > 0) {
      const key = line.slice(0, idx).trim().toLowerCase();
      if (key !== 'content-length') headers[key] = line.slice(idx + 1).trim();
    }
  }
  return new Response(body, { status, headers });
}

async function curlFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  const method = (init?.method ?? 'GET').toUpperCase();
  const args = ['-sS', '-D', '-', '-X', method, '--max-time', '25', url];
  for (const [k, v] of Object.entries(flattenHeaders(init))) {
    if (k.toLowerCase() === 'content-length') continue;
    args.push('-H', `${k}: ${v}`);
  }
  if (init?.body != null && method !== 'GET' && method !== 'HEAD') {
    const body = typeof init.body === 'string' ? init.body : String(init.body);
    args.push('--data-binary', body);
  }
  try {
    const { stdout } = await execFileAsync('curl', args, execOptions);
    return parseCurlOutput(stdout);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`curl fetch failed for ${method} ${url}: ${detail}`);
  }
}

export async function resilientFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  if (process.env.TELEGRAPH_NO_CURL_FALLBACK === '1') {
    return fetch(input, init);
  }
  try {
    return await fetch(input, init);
  } catch (err) {
    // fetch only throws for network-level failures — never for HTTP status.
    return curlFetch(input, init);
  }
}
