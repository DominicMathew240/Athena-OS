// Forwards every page request on port 3000 to the Athena Django console on port 8001, headers untouched.
const http = require("http");

const TARGET_HOST = "127.0.0.1";
const TARGET_PORT = 8001;
const PORT = Number(process.env.PORT || 3000);

const server = http.createServer((req, res) => {
  const upstream = http.request(
    { host: TARGET_HOST, port: TARGET_PORT, method: req.method, path: req.url, headers: req.headers },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers);
      up.pipe(res);
    }
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502, { "Content-Type": "text/html; charset=utf-8", "Retry-After": "3" });
    res.end("<!doctype html><title>Athena is starting</title><p style=\"font-family:sans-serif;padding:2rem\">Athena is starting up. Refresh in a few seconds.</p>");
  });
  req.pipe(upstream);
});

server.listen(PORT, "0.0.0.0", () => console.log(`Athena preview proxy on ${PORT} -> ${TARGET_PORT}`));
