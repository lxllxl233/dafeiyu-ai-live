package dev.dafeiyu.mcbridge;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicInteger;

import com.google.gson.JsonObject;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import net.minecraft.client.gui.screens.recipebook.RecipeCollection;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.crafting.display.RecipeDisplay;
import net.minecraft.world.item.crafting.display.RecipeDisplayEntry;
import net.minecraft.world.item.crafting.display.ShapedCraftingRecipeDisplay;
import net.minecraft.world.item.crafting.display.ShapelessCraftingRecipeDisplay;
import net.minecraft.world.item.crafting.display.SlotDisplay;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/** mc-bridge/v1 的动作实现：全部通过客户端按键/交互完成，不注入键鼠事件。 */
public final class Actions {
    private static final AtomicInteger MINED = new AtomicInteger();
    private static final AtomicInteger PLACED = new AtomicInteger();
    private static final AtomicInteger CRAFTED = new AtomicInteger();
    private static final AtomicInteger KILLS = new AtomicInteger();
    private static final AtomicInteger DEATHS = new AtomicInteger();
    private static final AtomicInteger ACTIONS = new AtomicInteger();
    private static volatile String last = "";
    private static boolean wasDead = false;

    private Actions() {
    }

    public static JsonObject stats() {
        JsonObject stats = new JsonObject();
        stats.addProperty("mined", MINED.get());
        stats.addProperty("placed", PLACED.get());
        stats.addProperty("crafted", CRAFTED.get());
        stats.addProperty("kills", KILLS.get());
        stats.addProperty("deaths", DEATHS.get());
        stats.addProperty("actions", ACTIONS.get());
        stats.addProperty("seconds", 0);
        return stats;
    }

    public static String last() {
        return last;
    }

    public static JsonObject run(JsonObject body) {
        String type = string(body, "type", "");
        ACTIONS.incrementAndGet();
        try {
            Minecraft mc = Minecraft.getInstance();
            if (mc.player == null || mc.level == null) {
                return err("还没进世界");
            }
            // 单人游戏默认失焦就暂停；直播/后台跑必须让世界继续 tick。
            // 只改运行时选项，不写入 options.txt。
            mc.options.pauseOnLostFocus = false;
            tickDeath(mc.player);
            if (type.equals("start")) {
                closeScreen(mc);
                return ok("已经在你自己的世界里");
            }
            if (type.equals("stop")) {
                releaseAll();
                return ok("已松开所有按键");
            }
            if (type.equals("reset")) {
                return err("真机不能重置世界；请自己在游戏里开新世界");
            }
            closeScreen(mc);
            return switch (type) {
                case "wait" -> waitSeconds(number(body, "seconds", 1.0));
                case "goto" -> goTo(body);
                case "mine" -> mine(body);
                case "attack" -> attack(body);
                case "place" -> place(body);
                case "eat" -> eat();
                case "equip" -> equip(body);
                case "craft" -> craft(body);
                case "smelt" -> err("真机模组暂不支持熔炼（下一步接熔炉界面）");
                default -> err("不认识的动作：" + type);
            };
        } catch (Throwable error) {
            releaseAll();
            return err(error.getClass().getSimpleName() + ": " + error.getMessage());
        }
    }

    private static void tickDeath(LocalPlayer player) {
        boolean dead = player.isDeadOrDying();
        if (dead && !wasDead) {
            DEATHS.incrementAndGet();
        }
        wasDead = dead;
    }

    private static JsonObject waitSeconds(double seconds) {
        long end = System.currentTimeMillis() + (long) Math.max(0.0, Math.min(20.0, seconds)) * 1000L;
        while (System.currentTimeMillis() < end) {
            try {
                Thread.sleep(100L);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        return ok("等了 " + seconds + " 秒");
    }

    private static JsonObject goTo(JsonObject body) {
        Minecraft mc = Minecraft.getInstance();
        String target = string(body, "target", "home").toLowerCase(Locale.ROOT);
        if (body.has("x") && body.has("z")) {
            double x = number(body, "x", 0.0);
            double z = number(body, "z", 0.0);
            boolean arrived = walkTo(x, z, 2.0, 15000L);
            return arrived ? ok("走到了 " + (int) x + "," + (int) z) : err("没走到 " + (int) x + "," + (int) z);
        }
        if (target.equals("home")) {
            BlockPos spawn = mc.level.getRespawnData().pos();
            boolean arrived = walkTo(spawn.getX() + 0.5, spawn.getZ() + 0.5, 3.0, 15000L);
            return arrived ? ok("回到出生点") : err("没回到出生点");
        }
        Entity mob = findEntity(target, 20.0);
        if (mob != null) {
            boolean arrived = walkTo(mob.getX(), mob.getZ(), 2.5, 15000L);
            return arrived ? ok("接近了 " + target) : err("没接近到 " + target);
        }
        BlockPos block = findBlock(target, 20.0);
        if (block != null) {
            boolean arrived = walkTo(block.getX() + 0.5, block.getZ() + 0.5, 3.0, 15000L);
            return arrived ? ok("走到了 " + target) : err("没走到 " + target);
        }
        return err("附近找不到 " + target);
    }

    private static JsonObject mine(JsonObject body) {
        Minecraft mc = Minecraft.getInstance();
        String name = string(body, "block", "oak_log");
        BlockPos pos = findBlock(name, 20.0);
        if (pos == null) {
            return err("附近找不到 " + name);
        }
        walkTo(pos.getX() + 0.5, pos.getZ() + 0.5, 4.0, 15000L);
        BlockState state = ClientThread.call(() -> mc.level.getBlockState(pos), 1500L);
        if (state.isAir()) {
            return ok("方块已经不在了");
        }
        Direction face = ClientThread.call(() -> faceToward(pos), 1500L);
        ClientThread.run(() -> {
            lookAt(Vec3.atCenterOf(pos));
            mc.gameMode.startDestroyBlock(pos, face);
        }, 2000L);
        long end = System.currentTimeMillis() + 20000L;
        boolean broken = false;
        while (System.currentTimeMillis() < end) {
            broken = ClientThread.call(() -> {
                lookAt(Vec3.atCenterOf(pos));
                if (mc.level.getBlockState(pos).isAir()) {
                    return true;
                }
                mc.gameMode.continueDestroyBlock(pos, face);
                return false;
            }, 1500L);
            if (broken) {
                break;
            }
            sleep(70L);
        }
        if (broken) {
            MINED.incrementAndGet();
            return ok("挖掉了 " + name);
        }
        return err("没挖掉 " + name + "（可能太远或工具不对）");
    }

    private static JsonObject attack(JsonObject body) {
        Minecraft mc = Minecraft.getInstance();
        String name = string(body, "target", "zombie");
        Entity target = findEntity(name, 20.0);
        if (target == null) {
            return err("附近没有 " + name);
        }
        walkTo(target.getX(), target.getZ(), 2.2, 15000L);
        long end = System.currentTimeMillis() + 20000L;
        boolean dead = false;
        while (System.currentTimeMillis() < end) {
            dead = ClientThread.call(() -> {
                if (!target.isAlive()) {
                    return true;
                }
                lookAt(target.getEyePosition());
                double distance = Math.sqrt(target.distanceToSqr(mc.player));
                if (distance > 3.4) {
                    stepToward(target.getX(), target.getZ(), 2.0);
                    return false;
                }
                mc.gameMode.attack(mc.player, target);
                mc.player.swing(InteractionHand.MAIN_HAND);
                return !target.isAlive();
            }, 1500L);
            if (dead) {
                break;
            }
            sleep(320L);
        }
        if (dead) {
            KILLS.incrementAndGet();
            return ok("打死了 " + name);
        }
        return err("没打死 " + name);
    }

    private static JsonObject place(JsonObject body) {
        Minecraft mc = Minecraft.getInstance();
        String block = string(body, "block", "");
        int slot = findHotbar(block);
        if (slot < 0) {
            return err("快捷栏里没有 " + block);
        }
        selectSlot(slot);
        boolean placed = ClientThread.call(() -> placeOne(), 3000L);
        return placed ? ok("放了一个 " + block) : err("附近没有合适的位置放 " + block);
    }

    private static boolean placeOne() {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        BlockPos base = player.blockPosition();
        for (int r = 1; r <= 3; r++) {
            for (int dx = -r; dx <= r; dx++) {
                for (int dz = -r; dz <= r; dz++) {
                    if (Math.max(Math.abs(dx), Math.abs(dz)) != r) {
                        continue;
                    }
                    for (int dy = -1; dy <= 1; dy++) {
                        BlockPos ground = base.offset(dx, dy, dz);
                        BlockPos target = ground.above();
                        if (!mc.level.getBlockState(target).isAir()) {
                            continue;
                        }
                        if (mc.level.getBlockState(ground).isAir()) {
                            continue;
                        }
                        double distance = Math.sqrt(target.distToCenterSqr(player.getX(), player.getY() + 1.0, player.getZ()));
                        if (distance > 4.2) {
                            continue;
                        }
                        lookAt(Vec3.atCenterOf(target));
                        BlockHitResult hit = new BlockHitResult(Vec3.atCenterOf(ground), Direction.UP, ground, false);
                        var result = mc.gameMode.useItemOn(player, InteractionHand.MAIN_HAND, hit);
                        player.swing(InteractionHand.MAIN_HAND);
                        if (result.consumesAction()) {
                            return true;
                        }
                    }
                }
            }
        }
        return false;
    }

    private static JsonObject eat() {
        Minecraft mc = Minecraft.getInstance();
        int slot = findFoodSlot();
        if (slot < 0) {
            return err("快捷栏里没有食物");
        }
        selectSlot(slot);
        long end = System.currentTimeMillis() + 2600L;
        while (System.currentTimeMillis() < end) {
            ClientThread.run(() -> mc.gameMode.useItem(mc.player, InteractionHand.MAIN_HAND), 1500L);
            sleep(260L);
        }
        return ok("吃了东西");
    }

    private static JsonObject equip(JsonObject body) {
        Minecraft mc = Minecraft.getInstance();
        String item = string(body, "item", "");
        int slot = findHotbar(item);
        if (slot < 0) {
            return err("快捷栏里没有 " + item);
        }
        selectSlot(slot);
        return ok("切到了 " + item);
    }

    // ------------------------------------------------------------ 合成
    private static JsonObject craft(JsonObject body) {
        Minecraft mc = Minecraft.getInstance();
        String name = string(body, "item", "");
        int want = Math.max(1, (int) number(body, "count", 1.0));
        Item target = BuiltInRegistries.ITEM
                .getOptional(Identifier.withDefaultNamespace(name))
                .orElse(Items.AIR);
        if (target == Items.AIR) {
            return err("不认识物品：" + name);
        }
        closeScreen(mc);
        boolean table = openTableIfPossible();
        if (!table) {
            openInventory();
        }
        boolean allowBig = ClientThread.call(
                () -> mc.player.containerMenu instanceof CraftingMenu, 1500L);
        List<RecipeDisplayEntry> entries = findRecipes(target, allowBig);
        if (entries.isEmpty()) {
            closeScreen(mc);
            return err("配方书里没有 " + name
                    + (allowBig ? "（材料可能不够/配方未解锁）" : "（需要工作台或材料不够）"));
        }
        int made = 0;
        for (RecipeDisplayEntry entry : entries) {
            int count = ClientThread.call(() -> craftOnce(entry), 4000L);
            if (count <= 0) {
                continue;
            }
            made += count;
            if (made >= want) {
                break;
            }
        }
        closeScreen(mc);
        if (made <= 0) {
            return err("合成 " + name + " 失败：材料不够或配方没解锁");
        }
        CRAFTED.incrementAndGet();
        return ok("合成了 " + made + "×" + name);
    }

    private static void openInventory() {
        Minecraft mc = Minecraft.getInstance();
        ClientThread.run(() -> mc.gui.setScreen(new InventoryScreen(mc.player)), 2000L);
        sleep(120L);
    }

    private static boolean openTableIfPossible() {
        Minecraft mc = Minecraft.getInstance();
        BlockPos table = ClientThread.call(() -> findBlock("crafting_table", 8.0), 2000L);
        if (table == null) {
            if (findHotbar("crafting_table") < 0) {
                return false;
            }
            selectSlot(findHotbar("crafting_table"));
            boolean placed = ClientThread.call(Actions::placeOne, 3000L);
            if (!placed) {
                return false;
            }
            table = ClientThread.call(() -> findBlock("crafting_table", 8.0), 2000L);
            if (table == null) {
                return false;
            }
        }
        BlockPos target = table;
        ClientThread.run(() -> {
            lookAt(Vec3.atCenterOf(target));
            mc.gameMode.useItemOn(mc.player, InteractionHand.MAIN_HAND,
                    new BlockHitResult(Vec3.atCenterOf(target), Direction.UP, target, false));
            mc.player.swing(InteractionHand.MAIN_HAND);
        }, 2000L);
        long end = System.currentTimeMillis() + 2500L;
        while (System.currentTimeMillis() < end) {
            boolean open = ClientThread.call(
                    () -> mc.player.containerMenu instanceof CraftingMenu, 1500L);
            if (open) {
                return true;
            }
            sleep(100L);
        }
        return false;
    }

    private static List<RecipeDisplayEntry> findRecipes(Item target, boolean allowBig) {
        Minecraft mc = Minecraft.getInstance();
        List<RecipeDisplayEntry> out = new ArrayList<>();
        for (RecipeCollection collection : mc.player.getRecipeBook().getCollections()) {
            for (RecipeDisplayEntry entry : collection.getRecipes()) {
                RecipeDisplay display = entry.display();
                ItemStack result = resultOf(display);
                if (result.isEmpty() || result.getItem() != target) {
                    continue;
                }
                if (display instanceof ShapedCraftingRecipeDisplay shaped) {
                    if (!allowBig && (shaped.width() > 2 || shaped.height() > 2)) {
                        continue;
                    }
                } else if (display instanceof ShapelessCraftingRecipeDisplay shapeless) {
                    if (!allowBig && shapeless.ingredients().size() > 4) {
                        continue;
                    }
                } else {
                    continue;
                }
                out.add(entry);
            }
        }
        return out;
    }

    private static ItemStack resultOf(RecipeDisplay display) {
        SlotDisplay slot = display.result();
        if (slot instanceof SlotDisplay.ItemSlotDisplay item) {
            return new ItemStack(item.item().value());
        }
        return ItemStack.EMPTY;
    }

    private static int craftOnce(RecipeDisplayEntry entry) {
        Minecraft mc = Minecraft.getInstance();
        int containerId = ClientThread.call(
                () -> mc.player.containerMenu.containerId, 1500L);
        if (containerId < 0) {
            return 0;
        }
        ClientThread.run(() -> mc.gameMode.handlePlaceRecipe(
                containerId, entry.id(), false), 1500L);
        // 让服务端把材料放进合成格；客户端线程不能睡，否则世界不 tick。
        sleep(260L);
        int count = ClientThread.call(() -> {
            ItemStack out = mc.player.containerMenu.getSlot(0).getItem();
            return out.isEmpty() ? 0 : out.getCount();
        }, 1500L);
        if (count <= 0) {
            return 0;
        }
        ClientThread.run(() -> {
            mc.gameMode.handleContainerInput(containerId, 0, 0,
                    ContainerInput.QUICK_MOVE, mc.player);
            int max = mc.player.containerMenu instanceof CraftingMenu ? 9 : 4;
            for (int slot = 1; slot <= max; slot++) {
                ItemStack left = mc.player.containerMenu.getSlot(slot).getItem();
                if (!left.isEmpty()) {
                    mc.gameMode.handleContainerInput(containerId, slot, 0,
                            ContainerInput.QUICK_MOVE, mc.player);
                }
            }
        }, 2000L);
        sleep(120L);
        return count;
    }

    private static int findFoodSlot() {
        Minecraft mc = Minecraft.getInstance();
        for (int i = 0; i < 9; i++) {
            ItemStack stack = mc.player.getInventory().getItem(i);
            if (stack.isEmpty()) {
                continue;
            }
            String id = GameSnapshot.canonItem(BuiltInRegistries.ITEM.getKey(stack.getItem()).getPath());
            if (id.equals("bread") || id.equals("beef") || id.equals("cooked_beef") || id.equals("porkchop")
                    || id.equals("cooked_porkchop") || id.equals("chicken") || id.equals("cooked_chicken")) {
                return i;
            }
        }
        return -1;
    }

    private static int findHotbar(String item) {
        Minecraft mc = Minecraft.getInstance();
        for (int i = 0; i < 9; i++) {
            ItemStack stack = mc.player.getInventory().getItem(i);
            if (stack.isEmpty()) {
                continue;
            }
            String id = BuiltInRegistries.ITEM.getKey(stack.getItem()).getPath();
            if (id.equals(item) || GameSnapshot.canonItem(id).equals(item)) {
                return i;
            }
        }
        return -1;
    }

    private static void selectSlot(int slot) {
        Minecraft mc = Minecraft.getInstance();
        ClientThread.run(() -> {
            mc.player.getInventory().setSelectedSlot(slot);
            mc.player.getInventory().setChanged();
        }, 1500L);
    }

    private static Entity findEntity(String name, double radius) {
        Minecraft mc = Minecraft.getInstance();
        Entity best = null;
        double bestDistance = Double.MAX_VALUE;
        for (Entity entity : mc.level.getEntities(mc.player, mc.player.getBoundingBox().inflate(radius))) {
            if (entity == mc.player || !(entity instanceof LivingEntity) || !entity.isAlive()) {
                continue;
            }
            String id = BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).getPath();
            boolean match = name.equals("hostile") ? entity instanceof net.minecraft.world.entity.monster.Monster
                    : id.equals(name);
            if (!match) {
                continue;
            }
            double distance = entity.distanceToSqr(mc.player);
            if (distance < bestDistance) {
                bestDistance = distance;
                best = entity;
            }
        }
        return best;
    }

    private static BlockPos findBlock(String name, double radius) {
        Minecraft mc = Minecraft.getInstance();
        String wanted = switch (name) {
            case "tree", "木头", "log" -> "oak_log";
            case "stone", "石头" -> "cobblestone";
            case "coal", "煤" -> "coal_ore";
            case "iron", "铁" -> "iron_ore";
            case "table", "workbench", "工作台" -> "crafting_table";
            case "furnace", "熔炉" -> "furnace";
            default -> name;
        };
        BlockPos base = mc.player.blockPosition();
        int r = (int) Math.min(16.0, radius);
        BlockPos best = null;
        double bestDistance = Double.MAX_VALUE;
        for (int dx = -r; dx <= r; dx++) {
            for (int dy = -6; dy <= 6; dy++) {
                for (int dz = -r; dz <= r; dz++) {
                    BlockPos pos = base.offset(dx, dy, dz);
                    BlockState state = mc.level.getBlockState(pos);
                    String id = GameSnapshot.canonBlock(BuiltInRegistries.BLOCK.getKey(state.getBlock()).getPath());
                    if (!id.equals(wanted)) {
                        continue;
                    }
                    double distance = pos.distToCenterSqr(mc.player.getX(), mc.player.getY(), mc.player.getZ());
                    if (distance < bestDistance) {
                        bestDistance = distance;
                        best = pos;
                    }
                }
            }
        }
        return best;
    }

    private static boolean walkTo(double tx, double tz, double stop, long timeoutMs) {
        long end = System.currentTimeMillis() + timeoutMs;
        boolean arrived = false;
        while (System.currentTimeMillis() < end) {
            boolean done = ClientThread.call(() -> stepToward(tx, tz, stop), 1500L);
            if (done) {
                arrived = true;
                break;
            }
            sleep(60L);
        }
        releaseMove();
        return arrived;
    }

    private static boolean stepToward(double tx, double tz, double stop) {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        if (player == null) {
            return true;
        }
        double dx = tx - player.getX();
        double dz = tz - player.getZ();
        double distance = Math.hypot(dx, dz);
        if (distance <= stop + 0.15) {
            releaseMove();
            return true;
        }
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        player.setYRot(yaw);
        mc.options.keyUp.setDown(true);
        mc.options.keyShift.setDown(false);
        boolean stuck = player.horizontalCollision || player.getDeltaMovement().horizontalDistanceSqr() < 0.0004;
        mc.options.keyJump.setDown(player.onGround() && stuck && distance > 1.2);
        return false;
    }

    private static void lookAt(Vec3 target) {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        Vec3 eye = player.getEyePosition();
        double dx = target.x - eye.x;
        double dy = target.y - eye.y;
        double dz = target.z - eye.z;
        double flat = Math.hypot(dx, dz);
        if (flat < 1.0E-4 && Math.abs(dy) < 1.0E-4) {
            return;
        }
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        float pitch = (float) (-Math.toDegrees(Math.atan2(dy, flat)));
        player.setYRot(yaw);
        player.setXRot(pitch);
    }

    private static Direction faceToward(BlockPos pos) {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        double dx = player.getX() - (pos.getX() + 0.5);
        double dy = player.getY() + player.getEyeHeight() - (pos.getY() + 0.5);
        double dz = player.getZ() - (pos.getZ() + 0.5);
        double ax = Math.abs(dx);
        double ay = Math.abs(dy);
        double az = Math.abs(dz);
        if (ax >= ay && ax >= az) {
            return dx >= 0.0 ? Direction.EAST : Direction.WEST;
        }
        if (ay >= az) {
            return dy >= 0.0 ? Direction.UP : Direction.DOWN;
        }
        return dz >= 0.0 ? Direction.SOUTH : Direction.NORTH;
    }

    private static void closeScreen(Minecraft mc) {
        if (mc.gui.screen() != null) {
            ClientThread.run(() -> mc.gui.setScreen(null), 2000L);
        }
    }

    private static void releaseMove() {
        Minecraft mc = Minecraft.getInstance();
        if (mc.options == null) {
            return;
        }
        mc.options.keyUp.setDown(false);
        mc.options.keyDown.setDown(false);
        mc.options.keyLeft.setDown(false);
        mc.options.keyRight.setDown(false);
        mc.options.keyJump.setDown(false);
        mc.options.keyShift.setDown(false);
    }

    private static void releaseAll() {
        releaseMove();
        Minecraft mc = Minecraft.getInstance();
        if (mc.options != null) {
            mc.options.keyAttack.setDown(false);
            mc.options.keyUse.setDown(false);
        }
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        }
    }

    private static String string(JsonObject body, String key, String fallback) {
        return body.has(key) && !body.get(key).isJsonNull() ? body.get(key).getAsString() : fallback;
    }

    private static double number(JsonObject body, String key, double fallback) {
        try {
            return body.has(key) ? body.get(key).getAsDouble() : fallback;
        } catch (RuntimeException error) {
            return fallback;
        }
    }

    private static JsonObject ok(String message) {
        last = message;
        JsonObject out = new JsonObject();
        out.addProperty("ok", true);
        out.addProperty("message", message);
        return out;
    }

    private static JsonObject err(String message) {
        last = message;
        JsonObject out = new JsonObject();
        out.addProperty("ok", false);
        out.addProperty("error", message);
        out.addProperty("message", message);
        return out;
    }
}
