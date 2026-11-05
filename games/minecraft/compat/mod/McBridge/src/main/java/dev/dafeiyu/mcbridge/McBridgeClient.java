package dev.dafeiyu.mcbridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.fabricmc.api.ClientModInitializer;

/** Fabric 客户端入口：只在本机 127.0.0.1 暴露 mc-bridge/v1。 */
public final class McBridgeClient implements ClientModInitializer {
    public static final int PORT = 8092;

    @Override
    public void onInitializeClient() {
        BridgeServer.start(PORT);
    }
}
