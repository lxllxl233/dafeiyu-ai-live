package dev.dafeiyu.mcbridge;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;

/** 把客户端的角色/世界读成 mc-bridge/v1 的 state。 */
public final class GameSnapshot {
    private static final int RADIUS = 8;

    private GameSnapshot() {
    }

    public static JsonObject capture() {
        Minecraft mc = Minecraft.getInstance();
        JsonObject out = new JsonObject();
        out.addProperty("ok", true);
        if (mc.player == null || mc.level == null) {
            out.addProperty("ready", false);
            out.addProperty("screen", "menu");
            out.addProperty("last", "还没进世界");
            return out;
        }
        LocalPlayer player = mc.player;
        out.addProperty("ready", true);
        out.addProperty("screen", screen(mc, player));

        JsonObject world = new JsonObject();
        long dayTime = mc.level.getLevelData().getGameTime();
        long ticks = Math.floorMod(dayTime, 24000L);
        world.addProperty("time", dayTime / 20.0);
        world.addProperty("day", (int) (dayTime / 24000L) + 1);
        world.addProperty("phase", ticks < 12000 ? "day" : (ticks < 14000 ? "dusk" : "night"));
        world.addProperty("weather", mc.level.isThundering() ? "thunder" : (mc.level.isRaining() ? "rain" : "clear"));
        world.addProperty("dimension", mc.level.dimension().identifier().getPath());
        world.addProperty("difficulty", mc.level.getDifficulty().name().toLowerCase(Locale.ROOT));
        out.add("world", world);

        JsonObject p = new JsonObject();
        p.addProperty("name", player.getGameProfile().name());
        p.addProperty("x", round(player.getX()));
        p.addProperty("y", round(player.getY()));
        p.addProperty("z", round(player.getZ()));
        p.addProperty("yaw", round(player.getYRot()));
        p.addProperty("hp", (int) Math.ceil(player.getHealth()));
        p.addProperty("max_hp", (int) Math.ceil(player.getMaxHealth()));
        p.addProperty("food", player.getFoodData().getFoodLevel());
        p.addProperty("level", player.experienceLevel);
        p.addProperty("held", canonItem(path(BuiltInRegistries.ITEM.getKey(player.getInventory().getSelectedItem().getItem()))));
        p.addProperty("dead", player.isDeadOrDying());
        p.addProperty("shelter", round(shelter(mc.level, player)));

        JsonObject inventory = new JsonObject();
        Inventory inv = player.getInventory();
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack stack = inv.getItem(i);
            if (stack.isEmpty()) {
                continue;
            }
            String id = canonItem(path(BuiltInRegistries.ITEM.getKey(stack.getItem())));
            int count = stack.getCount();
            if (inventory.has(id)) {
                count += inventory.get(id).getAsInt();
            }
            inventory.addProperty(id, count);
        }
        p.add("inventory", inventory);
        out.add("player", p);

        Scan scan = scan(mc.level, player);
        out.add("blocks", scan.blocks);
        out.add("mobs", mobs(mc.level, player));

        JsonObject flags = new JsonObject();
        flags.addProperty("crafting_table", scan.craftingTable);
        flags.addProperty("furnace", scan.furnace);
        out.add("flags", flags);

        out.add("stats", Actions.stats());
        out.addProperty("last", Actions.last());
        return out;
    }

    private static String screen(Minecraft mc, LocalPlayer player) {
        if (player.isDeadOrDying()) {
            return "dead";
        }
        if (mc.gui.screen() != null) {
            return "menu";
        }
        return "playing";
    }

    private static JsonArray mobs(Level level, LocalPlayer player) {
        List<JsonObject> found = new ArrayList<>();
        for (Entity entity : level.getEntities(player, player.getBoundingBox().inflate(16.0))) {
            if (entity == player || !(entity instanceof LivingEntity living) || !entity.isAlive()) {
                continue;
            }
            JsonObject mob = new JsonObject();
            mob.addProperty("name", path(BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType())));
            mob.addProperty("hostile", entity instanceof Monster);
            mob.addProperty("hp", (int) Math.ceil(living.getHealth()));
            mob.addProperty("max_hp", (int) Math.ceil(living.getMaxHealth()));
            mob.addProperty("attack", 0);
            mob.addProperty("x", round(entity.getX()));
            mob.addProperty("y", round(entity.getY()));
            mob.addProperty("z", round(entity.getZ()));
            mob.addProperty("distance", round(Math.sqrt(entity.distanceToSqr(player))));
            found.add(mob);
        }
        found.sort(Comparator.comparingDouble(mob -> mob.get("distance").getAsDouble()));
        JsonArray out = new JsonArray();
        for (int i = 0; i < Math.min(12, found.size()); i++) {
            out.add(found.get(i));
        }
        return out;
    }

    private record Scan(JsonArray blocks, boolean craftingTable, boolean furnace) {
    }

    private static Scan scan(Level level, LocalPlayer player) {
        BlockPos base = player.blockPosition();
        List<JsonObject> found = new ArrayList<>();
        boolean table = false;
        boolean furnace = false;
        for (int dx = -RADIUS; dx <= RADIUS; dx++) {
            for (int dy = -4; dy <= 4; dy++) {
                for (int dz = -RADIUS; dz <= RADIUS; dz++) {
                    BlockPos pos = base.offset(dx, dy, dz);
                    BlockState state = level.getBlockState(pos);
                    String id = canonBlock(path(BuiltInRegistries.BLOCK.getKey(state.getBlock())));
                    if (id.isEmpty()) {
                        continue;
                    }
                    if (id.equals("crafting_table")) {
                        table = true;
                    }
                    if (id.equals("furnace")) {
                        furnace = true;
                    }
                    double distance = Math.sqrt(pos.distToCenterSqr(player.getX(), player.getY(), player.getZ()));
                    JsonObject block = new JsonObject();
                    block.addProperty("name", id);
                    block.addProperty("kind", id);
                    block.addProperty("x", pos.getX());
                    block.addProperty("y", pos.getY());
                    block.addProperty("z", pos.getZ());
                    block.addProperty("distance", round(distance));
                    found.add(block);
                }
            }
        }
        found.sort(Comparator.comparingDouble(block -> block.get("distance").getAsDouble()));
        JsonArray blocks = new JsonArray();
        for (int i = 0; i < Math.min(48, found.size()); i++) {
            blocks.add(found.get(i));
        }
        return new Scan(blocks, table, furnace);
    }

    static double shelter(Level level, LocalPlayer player) {
        BlockPos base = player.blockPosition();
        int walls = 0;
        for (Direction direction : Direction.Plane.HORIZONTAL) {
            for (int y = 0; y <= 2; y++) {
                BlockPos pos = base.offset(direction.getStepX() * 2, y, direction.getStepZ() * 2);
                if (!level.getBlockState(pos).isAir()) {
                    walls++;
                }
            }
        }
        boolean roof = !level.getBlockState(base.above(3)).isAir();
        boolean floor = !level.getBlockState(base.below()).isAir();
        if (roof && walls >= 6 && floor) {
            return 0.9;
        }
        if (roof && floor) {
            return 0.45;
        }
        return Math.min(0.4, walls * 0.05);
    }

    static String canonBlock(String id) {
        if (id.endsWith("_log") || id.endsWith("_wood")) {
            return "oak_log";
        }
        if (id.endsWith("_planks")) {
            return "oak_planks";
        }
        return switch (id) {
            case "cobblestone", "stone" -> "cobblestone";
            case "coal_ore", "deepslate_coal_ore" -> "coal_ore";
            case "iron_ore", "deepslate_iron_ore" -> "iron_ore";
            case "crafting_table" -> "crafting_table";
            case "furnace" -> "furnace";
            default -> "";
        };
    }

    static String canonItem(String id) {
        if (id.endsWith("_log") || id.endsWith("_wood")) {
            return "oak_log";
        }
        if (id.endsWith("_planks")) {
            return "oak_planks";
        }
        return switch (id) {
            case "charcoal" -> "coal";
            case "stone" -> "cobblestone";
            default -> id;
        };
    }

    private static String path(Identifier location) {
        return location == null ? "air" : location.getPath();
    }

    private static double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }
}
