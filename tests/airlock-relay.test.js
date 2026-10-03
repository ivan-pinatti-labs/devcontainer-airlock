'use strict';
// Tests for images/workbench/bin/airlock-relay, run by `make coverage` with
// node's own test runner, which fails below 100% of its lines, branches and
// functions.
//
// The relay runs as its own node process, the way bin/claude starts it, in
// front of a fake egress proxy that each test scripts. The TLS origin is a
// local server with a certificate made by openssl for this run only and
// trusted through NODE_EXTRA_CA_CERTS; nothing leaves 127.0.0.1.
// cspell:words newkey nodes keyout addext subj

const { test, before, after } = require('node:test');
const assert = require('node:assert');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const tls = require('node:tls');

const RELAY = path.join(__dirname, '..', 'images', 'workbench', 'bin', 'airlock-relay');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let scratch;
let proxy;
let proxyPort;
let origin;
let originPort;
let relay;
let relayPort;
let deadRelay;
let deadRelayPort;
// What the fake egress proxy does with the next connection.
let onProxy = () => {};
// The last request the TLS origin received.
let originGot = '';

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer().listen(0, '127.0.0.1', () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

// Starts the relay and waits until it accepts connections. The preload
// turns SIGTERM into a normal exit, so node writes the coverage of the run.
async function startRelay(upstream) {
  const port = await freePort();
  const child = spawn(process.execPath, ['-r', path.join(scratch, 'exit-on-term.js'), RELAY], {
    env: { ...process.env, AIRLOCK_RELAY_PORT: String(port), AIRLOCK_RELAY_UPSTREAM: upstream },
    stdio: 'inherit',
  });
  for (let i = 0; i < 100; i++) {
    const up = await new Promise((resolve) => {
      const s = net.connect(port, '127.0.0.1', () => { s.destroy(); resolve(true); });
      s.on('error', () => resolve(false));
    });
    if (up) return { child, port };
    await sleep(50);
  }
  throw new Error('the relay did not start');
}

function stop(child) {
  return new Promise((resolve) => {
    child.on('exit', resolve);
    child.kill('SIGTERM');
  });
}

// Sends `chunks` to the relay (a pause between each) and resolves with
// everything it answers once it closes the connection.
function exchange(port, chunks, { gap = 50 } = {}) {
  return new Promise((resolve) => {
    const s = net.connect(port, '127.0.0.1');
    let got = Buffer.alloc(0);
    s.on('data', (d) => { got = Buffer.concat([got, d]); });
    s.on('error', () => {});
    s.on('close', () => resolve(got.toString('latin1')));
    (async () => {
      for (const c of chunks) {
        s.write(c);
        await sleep(gap);
      }
    })();
  });
}

before(async () => {
  scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'airlock-relay-'));
  fs.writeFileSync(path.join(scratch, 'exit-on-term.js'), "process.on('SIGTERM', () => process.exit(0));\n");
  execFileSync('openssl', [
    'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=DNS:localhost',
    '-keyout', path.join(scratch, 'key.pem'), '-out', path.join(scratch, 'cert.pem'),
  ], { stdio: 'ignore' });
  process.env.NODE_EXTRA_CA_CERTS = path.join(scratch, 'cert.pem');

  origin = tls.createServer({
    key: fs.readFileSync(path.join(scratch, 'key.pem')),
    cert: fs.readFileSync(path.join(scratch, 'cert.pem')),
  }, (s) => {
    let buf = '';
    s.on('data', (d) => {
      buf += d.toString('latin1');
      if (buf.includes('\r\n\r\n') && !buf.startsWith('GET /hold ')) {
        originGot = buf;
        // Give the rest of a body a moment to arrive.
        setTimeout(() => {
          originGot = buf;
          s.end('HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\norigin');
        }, 100);
      }
    });
    s.on('error', () => {});
  });
  await new Promise((r) => origin.listen(0, '127.0.0.1', r));
  originPort = origin.address().port;

  proxy = net.createServer((s) => {
    s.on('error', () => {});
    onProxy(s);
  });
  await new Promise((r) => proxy.listen(0, '127.0.0.1', r));
  proxyPort = proxy.address().port;

  ({ child: relay, port: relayPort } = await startRelay(`http://127.0.0.1:${proxyPort}`));
  // Nothing listens where this one sends everything.
  const dead = await freePort();
  ({ child: deadRelay, port: deadRelayPort } = await startRelay(`http://127.0.0.1:${dead}`));
});

after(async () => {
  await stop(relay);
  await stop(deadRelay);
  proxy.close();
  origin.close();
  fs.rmSync(scratch, { recursive: true, force: true });
});

// Reads the head the relay sends the proxy, then hands it to `then`.
function proxyReadsHead(then) {
  onProxy = (s) => {
    let buf = Buffer.alloc(0);
    const onData = (d) => {
      buf = Buffer.concat([buf, d]);
      if (buf.includes('\r\n\r\n')) {
        s.removeListener('data', onData);
        then(s, buf.toString('latin1'));
      }
    };
    s.on('data', onData);
  };
}

test('an unset or unparsable upstream exits 2 with a message', async () => {
  for (const value of ['', 'not a url']) {
    const child = spawn(process.execPath, [RELAY], {
      env: { ...process.env, AIRLOCK_RELAY_UPSTREAM: value },
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    let err = '';
    child.stderr.on('data', (d) => { err += d; });
    const code = await new Promise((r) => child.on('exit', r));
    assert.strictEqual(code, 2);
    assert.match(err, /AIRLOCK_RELAY_UPSTREAM is not set/);
  }
});

test('CONNECT passes through to the egress proxy untouched', async () => {
  let seen = '';
  proxyReadsHead((s, head) => {
    seen = head;
    s.write('HTTP/1.1 200 Connection established\r\n\r\n');
    s.on('data', (d) => s.end(`echo:${d}`));
  });
  const got = await exchange(relayPort, ['CONNECT example.com:443 HTTP/1.1\r\n', 'Host: example.com:443\r\n\r\n', 'hello']);
  assert.strictEqual(seen, 'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n');
  assert.match(got, /200 Connection established/);
  assert.match(got, /echo:hello/);
});

test('plain http and a request line without a target pass through too', async () => {
  for (const head of ['GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n', 'BOGUS\r\n\r\n']) {
    let seen = '';
    proxyReadsHead((s, h) => { seen = h; s.end('HTTP/1.1 403 Forbidden\r\n\r\n'); });
    const got = await exchange(relayPort, [head]);
    assert.strictEqual(seen, head);
    assert.match(got, /403 Forbidden/);
  }
});

test('a head larger than 64 KiB is refused with 431', async () => {
  const got = await exchange(relayPort, ['GET / HTTP/1.1\r\n', `X-Big: ${'a'.repeat(70 * 1024)}`, 'more']);
  assert.match(got, /^HTTP\/1.1 431 /);
});

test('an https target that is not a URL is refused with 400', async () => {
  const got = await exchange(relayPort, ['GET https:// HTTP/1.1\r\n\r\n']);
  assert.match(got, /^HTTP\/1.1 400 Bad Request/);
});

test('an unreachable egress proxy is a 502, for both kinds of request', async () => {
  for (const head of ['GET http://example.com/ HTTP/1.1\r\n\r\n', 'GET https://example.com/ HTTP/1.1\r\n\r\n']) {
    const got = await exchange(deadRelayPort, [head]);
    assert.match(got, /^HTTP\/1.1 502 Bad Gateway/);
  }
});

test("the egress proxy's refusal of the tunnel is passed on as it gave it", async () => {
  let seen = '';
  proxyReadsHead((s, head) => {
    seen = head;
    s.write('HTTP/1.1 403 Forbidden\r\n');
    setTimeout(() => s.end('Content-Length: 0\r\n\r\n'), 50);
  });
  const got = await exchange(relayPort, ['GET https://blocked.example:8443/x HTTP/1.1\r\nHost: blocked.example\r\n\r\n']);
  assert.strictEqual(seen, 'CONNECT blocked.example:8443 HTTP/1.1\r\nHost: blocked.example:8443\r\n\r\n');
  assert.match(got, /^HTTP\/1.1 403 Forbidden/);
});

test('the egress proxy closing before it answers is a 502', async () => {
  proxyReadsHead((s) => s.end());
  const got = await exchange(relayPort, ['GET https://example.com/ HTTP/1.1\r\n\r\n']);
  assert.match(got, /^HTTP\/1.1 502 Bad Gateway/);
});

test('an answer to CONNECT larger than 64 KiB is a 502', async () => {
  proxyReadsHead((s) => s.write('x'.repeat(70 * 1024)));
  const got = await exchange(relayPort, ['GET https://example.com/ HTTP/1.1\r\n\r\n']);
  assert.match(got, /^HTTP\/1.1 502 Bad Gateway/);
});

test('an https request goes through a tunnel, with TLS opened by the relay', async () => {
  let seen = '';
  proxyReadsHead((s, head) => {
    seen = head;
    const o = net.connect(originPort, '127.0.0.1', () => {
      s.write('HTTP/1.1 200 Connection established\r\n\r\n');
      s.pipe(o).pipe(s);
    });
  });
  const got = await exchange(relayPort, [
    'POST https://localhost/v1/register?a=1 HTTP/1.1\r\nHost: localhost\r\n'
      + 'Proxy-Connection: keep-alive\r\nProxy-Authorization: x\r\nContent-Length: 4\r\n\r\nbody',
  ]);
  assert.strictEqual(seen, 'CONNECT localhost:443 HTTP/1.1\r\nHost: localhost:443\r\n\r\n');
  assert.match(got, /200 OK[\s\S]*origin$/);
  assert.match(originGot, /^POST \/v1\/register\?a=1 HTTP\/1.1\r\nHost: localhost\r\nContent-Length: 4\r\nConnection: close\r\n\r\nbody$/);
  assert.doesNotMatch(originGot, /Proxy-/);
});

test('a request with no body is sent on as it is', async () => {
  proxyReadsHead((s) => {
    const o = net.connect(originPort, '127.0.0.1', () => {
      s.write('HTTP/1.1 200 Connection established\r\n\r\n');
      s.pipe(o).pipe(s);
    });
  });
  const got = await exchange(relayPort, ['GET https://localhost:9443/ HTTP/1.1\r\nHost: localhost\r\n\r\n']);
  assert.match(got, /origin$/);
  assert.match(originGot, /^GET \/ HTTP\/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n$/);
});

test('a TLS failure on the tunnel closes the client', async () => {
  proxyReadsHead((s) => {
    s.write('HTTP/1.1 200 Connection established\r\n\r\nthis is not TLS\r\n');
    setTimeout(() => s.resetAndDestroy(), 50);
  });
  const got = await exchange(relayPort, ['GET https://localhost/ HTTP/1.1\r\n\r\n']);
  assert.strictEqual(got, '');
});

test('a client that resets a pass through tears the upstream down', async () => {
  let upstreamClosed;
  const closed = new Promise((r) => { upstreamClosed = r; });
  proxyReadsHead((s) => { s.on('close', upstreamClosed); });
  const c = net.connect(relayPort, '127.0.0.1');
  c.on('error', () => {});
  c.write('CONNECT example.com:443 HTTP/1.1\r\n\r\n');
  await sleep(150);
  c.resetAndDestroy();
  await closed;
});

test('a client that resets a tunnel tears the upstream down', async () => {
  let upstreamClosed;
  const closed = new Promise((r) => { upstreamClosed = r; });
  proxyReadsHead((s) => {
    s.on('close', upstreamClosed);
    const o = net.connect(originPort, '127.0.0.1', () => {
      s.write('HTTP/1.1 200 Connection established\r\n\r\n');
      s.pipe(o).pipe(s);
    });
    o.on('error', () => {});
  });
  const c = net.connect(relayPort, '127.0.0.1');
  c.on('error', () => {});
  c.write('GET https://localhost/hold HTTP/1.1\r\n\r\n');
  await sleep(300);
  c.resetAndDestroy();
  await closed;
});

test('the egress proxy closing after the client left answers nobody', async () => {
  let release;
  const proxied = new Promise((r) => { release = r; });
  proxyReadsHead((s) => release(s));
  const c = net.connect(relayPort, '127.0.0.1');
  c.on('error', () => {});
  c.write('GET https://example.com/ HTTP/1.1\r\n\r\n');
  const s = await proxied;
  c.destroy();
  await sleep(150);
  s.end();
  await sleep(100);
});

test('an upstream without a port is on port 80', async () => {
  // Nothing listens on port 80 in the test container, so this is a 502;
  // what matters is that the relay starts and tries.
  const { child, port } = await startRelay('http://127.0.0.1');
  try {
    const got = await exchange(port, ['GET http://example.com/ HTTP/1.1\r\n\r\n']);
    assert.match(got, /^HTTP\/1.1 502 Bad Gateway/);
  } finally {
    await stop(child);
  }
});
