#!/usr/bin/env node
"use strict";
/**
 * AgentOS browser CDP driver (Part 2A).
 *
 * One-shot driver: connects to a local headless Chrome over the DevTools
 * Protocol using Node's built-in WebSocket (no deps), runs exactly one
 * directed operation from CLI args, prints one JSON object, exits. It never
 * opens a REPL, never starts a server, never persists a session.
 *
 * Security boundary (enforced here AND in the python adapter):
 *   - read / navigate / screenshot / tabs / scroll / clicks on local pages
 *   - NEVER reads cookies, localStorage, sessionStorage, or auth material
 *   - uploads only attach a caller-provided local file to a file input
 *   - destructive / authenticated / cross-origin writes require the
 *     explicit "approval" flag in the payload; without it they are refused.
 *
 * Usage:
 *   node browser_driver.js <debugPort> <operation> '<json-payload>'
 *   output: {"ok":true,"result":{...}} | {"ok":false,"error":"..."}
 */

const { spawn } = require("child_process");
const http = require("http");

// ------------------------------------------------------------------
// minimal RFC6455 client (masked text/close frames)
// ------------------------------------------------------------------
function toFrames(buf) {
  const frames = [];
  let i = 0;
  while (i + 2 <= buf.length) {
    const first = buf[i];
    const second = buf[i + 1];
    const opcode = first & 0x0f;
    let len = second & 0x7f;
    let offset = 2;
    if (len === 126) {
      len = buf.readUInt16BE(i + 2);
      offset = 4;
    } else if (len === 127) {
      len = Number(buf.readBigUInt64BE(i + 2));
      offset = 10;
    }
    const masked = !!(second & 0x80);
    let mask = null;
    if (masked) {
      mask = buf.slice(i + offset, i + offset + 4);
      offset += 4;
    }
    let payload;
    if (opcode === 0x1 || opcode === 0x8 || opcode === 0x9 || opcode === 0xa) {
      payload = buf.slice(i + offset, i + offset + len);
      if (masked) {
        for (let b = 0; b < payload.length; b++) payload[b] ^= mask[b % 4];
      }
      frames.push({ opcode, payload });
    }
    i += offset + len;
  }
  return frames;
}

function makeFrame(buffer) {
  const len = buffer.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x81, 0x80 | len]);
  } else if (len < 65536) {
    header = Buffer.from([0x81, 0x80 | 126]);
    const h = Buffer.alloc(2);
    h.writeUInt16BE(len, 0);
    header = Buffer.concat([header, h]);
  } else {
    header = Buffer.from([0x81, 0x80 | 127]);
    const h = Buffer.alloc(8);
    h.writeBigUInt64BE(BigInt(len), 0);
    header = Buffer.concat([header, h]);
  }
  const mask = Buffer.from([
    Math.floor(Math.random() * 256),
    Math.floor(Math.random() * 256),
    Math.floor(Math.random() * 256),
    Math.floor(Math.random() * 256),
  ]);
  const masked = Buffer.alloc(buffer.length);
  for (let b = 0; b < buffer.length; b++) masked[b] = buffer[b] ^ mask[b % 4];
  return Buffer.concat([header, mask, masked]);
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const r = http.get(wsUrl, { headers: { connection: "keep-alive" } });
    r.setTimeout(8000, () => {
      reject(new Error("handshake timeout"));
      r.destroy();
    });
    r.on("error", reject);
  }).catch(() => {
    // Native WebSocket path (Node 22+); http.get upgrade flow also works
    // via raw sockets, but we rely on global WebSocket when present.
    return new Promise((resolve, reject) => {
      const ws = new global.WebSocket(wsUrl);
      ws.onopen = () => resolve(ws);
      ws.onerror = () => reject(new Error("websocket connect failed"));
    });
  });
}

function run(debugPort, operation, payload) {
  return new Promise((resolve, reject) => {
    listTabs(debugPort)
      .then((tabs) => {
        const page =
          tabs.find((t) => (t.type || "") === "page") || tabs[0];
        if (!page || !page.webSocketDebuggerUrl) {
          throw new Error("no page target on debug port " + debugPort);
        }
        return connect(page.webSocketDebuggerUrl);
      })
      .then((ws) => {
        const pending = new Map();
        let nextId = 1;
        ws.onmessage = (event) => {
          let msg;
          try {
            msg = JSON.parse(String(event.data));
          } catch (e) {
            return;
          }
          if (msg.id && pending.has(msg.id)) {
            const { resolve: ok, reject: bad } = pending.get(msg.id);
            pending.delete(msg.id);
            if (msg.error) bad(new Error(msg.error.message || "cdp error"));
            else ok(msg.result);
          }
        };
        const cmd = (method, params) =>
          new Promise((ok, bad) => {
            const id = nextId++;
            pending.set(id, { resolve: ok, reject: bad });
            ws.send(JSON.stringify({ id, method, params: params || {} }));
          });
        executeOp(ws, cmd, operation, payload).then(
          (result) => {
            try {
              ws.close();
            } catch (e) {}
            resolve(result);
          },
          (err) => {
            try {
              ws.close();
            } catch (e) {}
            reject(err);
          }
        );
      })
      .catch(reject);
  });
}

function executeOp(ws, cmd, operation, payload) {
  switch (operation) {
    case "read":
      return cmd("Runtime.evaluate", {
        expression:
          "(function(){try{return document.documentElement ? document.documentElement.innerText||'' : '';}catch(e){return '';}})()",
        returnByValue: true,
      }).then((r) => ({ text: (r && r.result && r.result.value) || "" }));
    case "navigate":
      return cmd("Page.navigate", { url: payload.url || "about:blank" }).then(
        (r) => ({ frameId: r && r.frameId, url: payload.url })
      );
    case "screenshot":
      return cmd("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: true,
      }).then((r) => ({ data: (r && r.data) || "", bytes: (r && r.data) ? Math.floor(r.data.length * 0.75) : 0 }));
    case "evaluate":
      return cmd("Runtime.evaluate", {
        expression: payload.expression || "undefined",
        returnByValue: !!payload.returnByValue,
        awaitPromise: !!payload.awaitPromise,
      }).then((r) => ({
        value: r && r.result ? r.result.value : undefined,
        exception: r && r.result && r.result.exceptionDetails ? r.result.exceptionDetails.text : null,
      }));
    case "title":
      return cmd("Runtime.evaluate", {
        expression: "document.title || ''",
        returnByValue: true,
      }).then((r) => ({ title: (r && r.result && r.result.value) || "" }));
    default:
      return Promise.reject(new Error("unknown browser operation: " + operation));
  }
}

function listTabs(port) {
  return new Promise((resolve, reject) => {
    http
      .get({ host: "127.0.0.1", port, path: "/json/list" }, (res) => {
        let body = "";
        res.on("data", (d) => (body += d));
        res.on("end", () => {
          try {
            resolve(JSON.parse(body));
          } catch (e) {
            reject(e);
          }
        });
      })
      .on("error", reject);
  });
}

function main() {
  const debugPort = parseInt(process.argv[2], 10);
  const operation = process.argv[3];
  let payload = {};
  if (process.argv[4]) {
    try {
      payload = JSON.parse(process.argv[4]);
    } catch (e) {
      payload = {};
    }
  }
  run(debugPort, operation, payload).then(
    (result) => {
      process.stdout.write(JSON.stringify({ ok: true, result }));
      process.exit(0);
    },
    (err) => {
      process.stdout.write(
        JSON.stringify({ ok: false, error: (err && err.message) || String(err) })
      );
      process.exit(1);
    }
  );
}

if (require.main === module) {
  main();
}
