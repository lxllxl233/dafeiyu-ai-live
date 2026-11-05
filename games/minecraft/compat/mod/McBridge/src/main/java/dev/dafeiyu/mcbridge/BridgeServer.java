package dev.dafeiyu.mcbridge;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import net.minecraft.client.Minecraft;

/** 最小 HTTP 服务：GET /state，POST /act，只绑定 127.0.0.1。 */
public final class BridgeServer {
    private static final ExecutorService WORKERS = Executors.newFixedThreadPool(2, runnable -> {
        Thread thread = new Thread(runnable, "mcbridge-http");
        thread.setDaemon(true);
        return thread;
    });
    private static HttpServer server;

    private BridgeServer() {
    }

    public static synchronized void start(int port) {
        if (server != null) {
            return;
        }
        try {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
            server.createContext("/state", BridgeServer::handleState);
            server.createContext("/act", BridgeServer::handleAct);
            server.setExecutor(WORKERS);
            server.start();
            System.out.println("[McBridge] http://127.0.0.1:" + port + " ready");
        } catch (IOException error) {
            System.err.println("[McBridge] 端口 " + port + " 起不来：" + error);
        }
    }

    private static void handleState(HttpExchange exchange) throws IOException {
        try {
            JsonObject state = ClientThread.call(GameSnapshot::capture, 4000L);
            send(exchange, 200, state);
        } catch (Throwable error) {
            send(exchange, 500, failure(error));
        }
    }

    private static void handleAct(HttpExchange exchange) throws IOException {
        JsonObject body;
        try (InputStream input = exchange.getRequestBody()) {
            String text = new String(input.readAllBytes(), StandardCharsets.UTF_8);
            body = JsonParser.parseString(text).getAsJsonObject();
        } catch (RuntimeException error) {
            send(exchange, 400, failure(new IllegalArgumentException("请求体不是 JSON 对象")));
            return;
        }
        try {
            JsonObject result = Actions.run(body);
            boolean ok = result.has("ok") && result.get("ok").getAsBoolean();
            send(exchange, ok ? 200 : 400, result);
        } catch (Throwable error) {
            send(exchange, 500, failure(error));
        }
    }

    private static JsonObject failure(Throwable error) {
        JsonObject out = new JsonObject();
        out.addProperty("ok", false);
        out.addProperty("error", error.getClass().getSimpleName() + ": " + error.getMessage());
        return out;
    }

    private static void send(HttpExchange exchange, int status, JsonObject body) throws IOException {
        byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Access-Control-Allow-Origin", "*");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }

    /** 防止客户端在未初始化时就被调用。 */
    @SuppressWarnings("unused")
    private static boolean ready() {
        Minecraft mc = Minecraft.getInstance();
        return mc.player != null && mc.level != null;
    }
}
