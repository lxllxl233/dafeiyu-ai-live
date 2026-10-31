using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Newtonsoft.Json.Linq;

namespace VsBridge
{
    /// <summary>
    /// Minimal HTTP/1.1 server on loopback (no HttpListener URL ACL).
    /// GET /state returns the latest snapshot, POST /act queues an action.
    /// </summary>
    internal sealed class HttpApi
    {
        private readonly Plugin _plugin;
        private readonly int _port;
        private TcpListener _listener;
        private Thread _thread;
        private volatile bool _running;

        public HttpApi(Plugin plugin)
        {
            _plugin = plugin;
            _port = 8091;
            var raw = Environment.GetEnvironmentVariable("VS_BRIDGE_PORT");
            if (int.TryParse(raw, out var custom) && custom > 0 && custom < 65536)
                _port = custom;
        }

        public string Url => $"http://127.0.0.1:{_port}";

        public void Start()
        {
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, _port);
                _listener.Start();
                _running = true;
                _thread = new Thread(Loop) { IsBackground = true, Name = "VsBridge.Http" };
                _thread.Start();
                Plugin.LogSrc?.LogInfo($"VS bridge API listening on {Url}");
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogError($"VS bridge could not listen on {Url}: {ex.Message}");
                _running = false;
            }
        }

        public void Stop()
        {
            _running = false;
            try { _listener?.Stop(); } catch { }
            _listener = null;
        }

        private void Loop()
        {
            while (_running)
            {
                TcpClient client = null;
                try
                {
                    client = _listener.AcceptTcpClient();
                    client.NoDelay = true;
                    var c = client;
                    var worker = new Thread(() => Handle(c))
                    {
                        IsBackground = true,
                        Name = "VsBridge.HttpClient",
                    };
                    worker.Start();
                }
                catch (Exception)
                {
                    if (!_running) return;
                    try { client?.Close(); } catch { }
                    Thread.Sleep(50);
                }
            }
        }

        private sealed class Request
        {
            public string Method;
            public string Path;
            public string Body;
        }

        private void Handle(TcpClient client)
        {
            try
            {
                using (client)
                using (var stream = client.GetStream())
                {
                    stream.ReadTimeout = 5000;
                    stream.WriteTimeout = 5000;
                    var request = ReadRequest(stream);
                    if (request == null)
                    {
                        Write(stream, 400, "{\"ok\":false,\"error\":\"bad request\"}");
                        return;
                    }
                    if (request.Method == "GET" && request.Path == "/state")
                    {
                        Write(stream, 200, _plugin.StateJson);
                    }
                    else if (request.Method == "GET" && request.Path == "/health")
                    {
                        Write(stream, 200, "{\"ok\":true,\"api\":1}");
                    }
                    else if (request.Method == "POST" && request.Path == "/act")
                    {
                        JObject msg;
                        try { msg = JObject.Parse(request.Body.Length == 0 ? "{}" : request.Body); }
                        catch (Exception)
                        {
                            Write(stream, 400, "{\"ok\":false,\"error\":\"invalid json\"}");
                            return;
                        }
                        Plugin.Enqueue(msg);
                        Write(stream, 200, "{\"ok\":true}");
                    }
                    else
                    {
                        Write(stream, 404, "{\"ok\":false,\"error\":\"not found\"}");
                    }
                }
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"VS bridge request failed: {ex.Message}");
                try { client.Close(); } catch { }
            }
        }

        private static Request ReadRequest(NetworkStream stream)
        {
            var buffer = new byte[8192];
            using var ms = new MemoryStream();
            int headerEnd = -1;
            while (ms.Length < 65536)
            {
                int read = stream.Read(buffer, 0, buffer.Length);
                if (read <= 0) return null;
                ms.Write(buffer, 0, read);
                var data = ms.GetBuffer();
                for (int i = 0; i <= ms.Length - 4; i++)
                {
                    if (data[i] == 13 && data[i + 1] == 10
                        && data[i + 2] == 13 && data[i + 3] == 10)
                    {
                        headerEnd = i + 4;
                        break;
                    }
                }
                if (headerEnd >= 0) break;
            }
            if (headerEnd < 0) return null;

            var all = ms.ToArray();
            var headerText = Encoding.UTF8.GetString(all, 0, headerEnd);
            var lines = headerText.Split(new[] { "\r\n" }, StringSplitOptions.None);
            var parts = lines[0].Split(' ');
            if (parts.Length < 3) return null;
            var method = parts[0].ToUpperInvariant();
            var path = parts[1];
            int query = path.IndexOf('?');
            if (query >= 0) path = path.Substring(0, query);

            int contentLength = 0;
            var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 1; i < lines.Length; i++)
            {
                int colon = lines[i].IndexOf(':');
                if (colon <= 0) continue;
                headers[lines[i].Substring(0, colon).Trim()] =
                    lines[i].Substring(colon + 1).Trim();
            }
            if (headers.TryGetValue("Content-Length", out var len))
                int.TryParse(len, out contentLength);

            int already = all.Length - headerEnd;
            var bodyBytes = new byte[Math.Max(0, contentLength)];
            if (already > 0)
                Array.Copy(all, headerEnd, bodyBytes, 0, Math.Min(already, bodyBytes.Length));
            int offset = Math.Min(already, bodyBytes.Length);
            while (offset < bodyBytes.Length)
            {
                int read = stream.Read(bodyBytes, offset, bodyBytes.Length - offset);
                if (read <= 0) break;
                offset += read;
            }
            var body = Encoding.UTF8.GetString(bodyBytes, 0, offset);
            return new Request { Method = method, Path = path, Body = body };
        }

        private static void Write(NetworkStream stream, int status, string json)
        {
            var body = Encoding.UTF8.GetBytes(json);
            var reason = status == 200 ? "OK" : status == 404 ? "Not Found" : "Bad Request";
            var head = $"HTTP/1.1 {status} {reason}\r\n"
                       + "Content-Type: application/json; charset=utf-8\r\n"
                       + $"Content-Length: {body.Length}\r\n"
                       + "Connection: close\r\n\r\n";
            var headBytes = Encoding.ASCII.GetBytes(head);
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Write(body, 0, body.Length);
            stream.Flush();
        }
    }
}
