package dev.dafeiyu.mcbridge;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.Callable;

import net.minecraft.client.Minecraft;

/** 把读写游戏状态的任务丢回客户端主线程执行（HTTP 线程不能直接碰世界）。 */
public final class ClientThread {
    private ClientThread() {
    }

    public static <T> T call(Callable<T> task, long timeoutMs) {
        Minecraft mc = Minecraft.getInstance();
        CompletableFuture<T> future = new CompletableFuture<>();
        mc.execute(() -> {
            try {
                future.complete(task.call());
            } catch (Throwable error) {
                future.completeExceptionally(error);
            }
        });
        try {
            return future.get(Math.max(200L, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (Exception error) {
            throw new IllegalStateException("客户端主线程超时：" + error, error);
        }
    }

    public static void run(Runnable task, long timeoutMs) {
        call(() -> {
            task.run();
            return Boolean.TRUE;
        }, timeoutMs);
    }
}
