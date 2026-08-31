package dev.redstonecompiler.harness;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.permissions.PermissionSet;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.gamerules.GameRules;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;

/** Executes only validated compiler fixtures on the main server thread. */
final class HarnessValidation {
    private static final int MINIMUM_OUTPUT_SETTLE_TICKS = 50;
    private static final int MAXIMUM_FORCED_FIXTURE_CHUNKS = 256;
    private static final Set<ChunkCoordinate> ForcedFixtureChunks = new LinkedHashSet<>();

    private record ChunkCoordinate(int X, int Z) {
    }

    private HarnessValidation() {
    }

    static void validate(MinecraftServer server, JsonObject request, HarnessConfiguration configuration, BufferedWriter output) {
        JsonObject response = new JsonObject();
        JsonObject diagnostics = new JsonObject();
        try {
            String action = request.get("Action").getAsString();
            if ("WorldReadBlocks".equals(action)) {
                JsonArray blocks = OnServer(server, () -> readWorldBlocks(
                        server.overworld(), request.getAsJsonArray("Positions")));
                response.addProperty("Status", "observed");
                response.add("Blocks", blocks);
                diagnostics.addProperty("ObservedBlockCount", blocks.size());
                diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
                writeResponse(response, diagnostics, output);
                return;
            }
            if ("WorldSetBlocks".equals(action)) {
                int updated = OnServer(server, () -> setWorldBlocks(
                        server, server.overworld(), request.getAsJsonArray("Blocks")));
                response.addProperty("Status", "updated");
                diagnostics.addProperty("UpdatedBlockCount", updated);
                diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
                writeResponse(response, diagnostics, output);
                return;
            }
            if ("WorldRunCommand".equals(action)) {
                String command = request.get("Command").getAsString();
                if (command.isBlank() || command.indexOf('\n') >= 0 || command.indexOf('\r') >= 0) {
                    throw new IllegalArgumentException("invalid-world-command");
                }
                OnServer(server, () -> {
                    server.getCommands().performPrefixedCommand(
                            server.createCommandSourceStack().withPermission(PermissionSet.ALL_PERMISSIONS),
                            command.startsWith("/") ? command.substring(1) : command);
                    return null;
                });
                response.addProperty("Status", "command-complete");
                diagnostics.addProperty("CommandExecuted", true);
                diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
                writeResponse(response, diagnostics, output);
                return;
            }
            if ("ClearImportedBlocks".equals(action)) {
                int cleared = OnServer(server, () -> clearRegions(
                        server.overworld(), request.getAsJsonArray("ClearRegions")));
                response.addProperty("Status", "cleared");
                diagnostics.addProperty("ClearedNonAirBlocks", cleared);
                diagnostics.addProperty("ClearRegionCount", request.getAsJsonArray("ClearRegions").size());
                writeResponse(response, diagnostics, output);
                return;
            }
            if ("ConfigureQuietWorld".equals(action)) {
                OnServer(server, () -> {
                    configureQuietWorld(server);
                    return null;
                });
                response.addProperty("Status", "configured");
                diagnostics.addProperty("MobSpawning", false);
                diagnostics.addProperty("SpawnerBlocks", false);
                diagnostics.addProperty("MobLoot", false);
                diagnostics.addProperty("BlockDrops", false);
                diagnostics.addProperty("EntityDrops", false);
                diagnostics.addProperty("DaylightCycle", false);
                diagnostics.addProperty("WeatherCycle", false);
                writeResponse(response, diagnostics, output);
                return;
            }
            if ("PauseTicks".equals(action) || "ResumeTicks".equals(action) || "StepTicks".equals(action)) {
                OnServer(server, () -> {
                    CommandSourceStack source = server.createCommandSourceStack().withPermission(PermissionSet.ALL_PERMISSIONS);
                    if ("PauseTicks".equals(action)) setTickFrozen(server, source, true);
                    else if ("ResumeTicks".equals(action)) setTickFrozen(server, source, false);
                    else server.getCommands().performPrefixedCommand(source, "tick step " + request.get("StepTicks").getAsInt());
                    return null;
                });
                response.addProperty("Status", "PauseTicks".equals(action) ? "paused" : "ResumeTicks".equals(action) ? "resumed" : "stepped");
                if ("StepTicks".equals(action)) diagnostics.addProperty("StepTicks", request.get("StepTicks").getAsInt());
                writeResponse(response, diagnostics, output);
                return;
            }
            if (!"Validate".equals(action) && !"LoadFixture".equals(action)) {
                throw new IllegalArgumentException("unsupported-action");
            }
            Path fixturePath = Path.of(request.get("FixturePath").getAsString()).toRealPath();
            String expectedDigest = request.get("FixtureSha256").getAsString();
            String actualDigest = sha256(Files.readAllBytes(fixturePath));
            if (!expectedDigest.equals(actualDigest)) {
                throw new IllegalArgumentException("fixture-digest-mismatch");
            }
            JsonObject fixture = JsonParser.parseString(Files.readString(fixturePath)).getAsJsonObject();
            JsonArray origin = fixture.getAsJsonObject("Arena").getAsJsonArray("Origin");
            int forcedFixtureChunkCount = OnServer(server, () -> {
                CommandSourceStack source = server.createCommandSourceStack().withPermission(PermissionSet.ALL_PERMISSIONS);
                setTickRate(server, source, configuration.requestedTickRate());
                ServerLevel level = server.overworld();
                int forcedChunkCount = forceFixtureChunks(
                        level, fixture.getAsJsonArray("Blocks"), origin, fixture.getAsJsonObject("Arena"));
                clearFixture(level, fixture.getAsJsonArray("Blocks"), origin, fixture.getAsJsonObject("Arena"));
                placeFixture(server, level, fixture.getAsJsonArray("Blocks"), origin);
                forceBlockUpdates(level, fixture.getAsJsonArray("Blocks"), origin);
                return forcedChunkCount;
            });
            waitForTicks(server, 2);
            if ("LoadFixture".equals(action)) {
                response.addProperty("Status", "loaded");
                diagnostics.addProperty("FixtureSha256", actualDigest);
                diagnostics.addProperty("LoadedBlocks", fixture.getAsJsonArray("Blocks").size());
                diagnostics.addProperty("ForcedFixtureChunks", forcedFixtureChunkCount);
                diagnostics.addProperty("RequestedTickRate", configuration.requestedTickRate());
                diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
                writeResponse(response, diagnostics, output);
                return;
            }
            Map<String, JsonObject> inputStates = inputStates(fixture.getAsJsonArray("Inputs"), fixture.getAsJsonArray("Blocks"), origin);
            Map<String, BlockPos> outputPositions = outputPositions(fixture.getAsJsonArray("Outputs"), origin);
            int tested = validateVectors(server, request.getAsJsonArray("Vectors"), inputStates, outputPositions, configuration.settleTimeoutTicks());
            response.addProperty("Status", "passed");
            diagnostics.addProperty("FixtureSha256", actualDigest);
            diagnostics.addProperty("TestedVectors", tested);
            diagnostics.addProperty("ForcedFixtureChunks", forcedFixtureChunkCount);
            diagnostics.addProperty("RequestedTickRate", configuration.requestedTickRate());
            diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
        } catch (Mismatch error) {
            response.addProperty("Status", "mismatch");
            diagnostics.addProperty("Error", error.getMessage());
        } catch (Timeout error) {
            response.addProperty("Status", "timeout");
            diagnostics.addProperty("Error", error.getMessage());
        } catch (Exception error) {
            response.addProperty("Status", "infrastructure-failure");
            diagnostics.addProperty("Error", error.toString());
            if (error.getCause() != null) diagnostics.addProperty("Cause", error.getCause().toString());
        }
        response.add("Diagnostics", diagnostics);
        try {
            output.write(response + "\n");
            output.flush();
        } catch (IOException error) {
            System.err.println("RedstoneCompiler harness response failed: " + error);
        }
    }

    private static void configureQuietWorld(MinecraftServer server) {
        // Java 26.2 renamed these rules.  Use the typed API instead of command
        // text so the persisted behavior cannot silently change with syntax.
        GameRules rules = server.getGameRules();
        rules.set(GameRules.SPAWN_MOBS, false, server);
        rules.set(GameRules.SPAWN_MONSTERS, false, server);
        rules.set(GameRules.SPAWNER_BLOCKS_WORK, false, server);
        rules.set(GameRules.SPAWN_PATROLS, false, server);
        rules.set(GameRules.SPAWN_PHANTOMS, false, server);
        rules.set(GameRules.SPAWN_WANDERING_TRADERS, false, server);
        rules.set(GameRules.SPAWN_WARDENS, false, server);
        rules.set(GameRules.MOB_DROPS, false, server);
        rules.set(GameRules.BLOCK_DROPS, false, server);
        rules.set(GameRules.ENTITY_DROPS, false, server);
        rules.set(GameRules.ADVANCE_TIME, false, server);
        rules.set(GameRules.ADVANCE_WEATHER, false, server);
    }

    private static void setTickFrozen(MinecraftServer server, CommandSourceStack source, boolean frozen) {
        server.getCommands().performPrefixedCommand(source, frozen ? "tick freeze" : "tick unfreeze");
    }

    private static void writeResponse(JsonObject response, JsonObject diagnostics, BufferedWriter output) throws IOException {
        response.add("Diagnostics", diagnostics);
        output.write(response + "\n");
        output.flush();
    }

    private static void setTickRate(MinecraftServer server, CommandSourceStack source, double rate) {
        server.getCommands().performPrefixedCommand(source, "tick rate " + rate);
    }

    private static void placeFixture(MinecraftServer server, ServerLevel level, JsonArray blocks, JsonArray origin) {
        for (JsonElement element : blocks) {
            JsonObject block = element.getAsJsonObject();
            BlockPos position = absolute(block.getAsJsonArray("Position"), origin);
            level.setBlock(position, parseState(server, blockState(block.getAsJsonObject("State"))), 3);
        }
        for (JsonElement element : blocks) {
            BlockPos position = absolute(element.getAsJsonObject().getAsJsonArray("Position"), origin);
            level.updateNeighborsAt(position, level.getBlockState(position).getBlock());
        }
    }

    private static int forceFixtureChunks(
            ServerLevel level, JsonArray blocks, JsonArray origin, JsonObject arena) {
        Set<ChunkCoordinate> requested = fixtureChunks(blocks, origin, arena);
        if (requested.size() > MAXIMUM_FORCED_FIXTURE_CHUNKS) {
            throw new IllegalArgumentException("fixture-spans-too-many-chunks:" + requested.size());
        }
        for (ChunkCoordinate coordinate : requested) {
            if (!ForcedFixtureChunks.contains(coordinate)) {
                level.setChunkForced(coordinate.X(), coordinate.Z(), true);
                level.getChunk(coordinate.X(), coordinate.Z());
            }
        }
        for (ChunkCoordinate coordinate : new LinkedHashSet<>(ForcedFixtureChunks)) {
            if (!requested.contains(coordinate)) {
                level.setChunkForced(coordinate.X(), coordinate.Z(), false);
            }
        }
        ForcedFixtureChunks.clear();
        ForcedFixtureChunks.addAll(requested);
        return requested.size();
    }

    private static Set<ChunkCoordinate> fixtureChunks(
            JsonArray blocks, JsonArray origin, JsonObject arena) {
        int minimumX = Integer.MAX_VALUE, minimumZ = Integer.MAX_VALUE;
        int maximumX = Integer.MIN_VALUE, maximumZ = Integer.MIN_VALUE;
        if (arena.has("Bounds")) {
            JsonObject bounds = arena.getAsJsonObject("Bounds");
            BlockPos minimum = absolute(bounds.getAsJsonArray("Minimum"), origin);
            BlockPos maximum = absolute(bounds.getAsJsonArray("Maximum"), origin);
            minimumX = minimum.getX(); minimumZ = minimum.getZ();
            maximumX = maximum.getX(); maximumZ = maximum.getZ();
        }
        for (JsonElement element : blocks) {
            BlockPos position = absolute(element.getAsJsonObject().getAsJsonArray("Position"), origin);
            minimumX = Math.min(minimumX, position.getX());
            minimumZ = Math.min(minimumZ, position.getZ());
            maximumX = Math.max(maximumX, position.getX());
            maximumZ = Math.max(maximumZ, position.getZ());
        }
        if (minimumX == Integer.MAX_VALUE) return Set.of();
        int minimumChunkX = Math.floorDiv(minimumX, 16);
        int minimumChunkZ = Math.floorDiv(minimumZ, 16);
        int maximumChunkX = Math.floorDiv(maximumX, 16);
        int maximumChunkZ = Math.floorDiv(maximumZ, 16);
        long chunkCount = ((long) maximumChunkX - minimumChunkX + 1)
                * ((long) maximumChunkZ - minimumChunkZ + 1);
        if (chunkCount > MAXIMUM_FORCED_FIXTURE_CHUNKS) {
            throw new IllegalArgumentException("fixture-spans-too-many-chunks:" + chunkCount);
        }
        Set<ChunkCoordinate> result = new LinkedHashSet<>();
        for (int chunkX = minimumChunkX; chunkX <= maximumChunkX; chunkX++) {
            for (int chunkZ = minimumChunkZ; chunkZ <= maximumChunkZ; chunkZ++) {
                result.add(new ChunkCoordinate(chunkX, chunkZ));
            }
        }
        return result;
    }

    private static void clearFixture(ServerLevel level, JsonArray blocks, JsonArray origin, JsonObject arena) {
        int minimumX = Integer.MAX_VALUE, minimumY = Integer.MAX_VALUE, minimumZ = Integer.MAX_VALUE;
        int maximumX = Integer.MIN_VALUE, maximumY = Integer.MIN_VALUE, maximumZ = Integer.MIN_VALUE;
        if (arena.has("Bounds")) {
            JsonObject bounds = arena.getAsJsonObject("Bounds");
            BlockPos minimum = absolute(bounds.getAsJsonArray("Minimum"), origin);
            BlockPos maximum = absolute(bounds.getAsJsonArray("Maximum"), origin);
            minimumX = minimum.getX(); minimumY = minimum.getY(); minimumZ = minimum.getZ();
            maximumX = maximum.getX(); maximumY = maximum.getY(); maximumZ = maximum.getZ();
        }
        for (JsonElement element : blocks) {
            BlockPos position = absolute(element.getAsJsonObject().getAsJsonArray("Position"), origin);
            minimumX = Math.min(minimumX, position.getX()); minimumY = Math.min(minimumY, position.getY()); minimumZ = Math.min(minimumZ, position.getZ());
            maximumX = Math.max(maximumX, position.getX()); maximumY = Math.max(maximumY, position.getY()); maximumZ = Math.max(maximumZ, position.getZ());
        }
        if (minimumX == Integer.MAX_VALUE) return;
        for (int x = minimumX; x <= maximumX; x++) for (int y = minimumY; y <= maximumY; y++) for (int z = minimumZ; z <= maximumZ; z++)
            level.setBlock(new BlockPos(x, y, z), Blocks.AIR.defaultBlockState(), 3);
    }

    private static void forceBlockUpdates(ServerLevel level, JsonArray blocks, JsonArray origin) {
        // setBlock(..., 3) performs ordinary placement notifications.  A
        // second pass covers every direct neighbor too, which makes a pasted
        // redstone network settle through the same neighbor-update path as a
        // player-built circuit rather than relying on paste order.
        Set<BlockPos> affected = new LinkedHashSet<>();
        for (JsonElement element : blocks) {
            BlockPos position = absolute(element.getAsJsonObject().getAsJsonArray("Position"), origin);
            affected.add(position);
            for (net.minecraft.core.Direction direction : net.minecraft.core.Direction.values()) {
                affected.add(position.relative(direction));
            }
        }
        forceBlockUpdates(level, affected);
    }

    private static void forceBlockUpdates(ServerLevel level, Set<BlockPos> affected) {
        for (BlockPos position : affected) {
            level.updateNeighborsAt(position, level.getBlockState(position).getBlock());
        }
    }

    private static JsonArray readWorldBlocks(ServerLevel level, JsonArray positions) {
        if (positions.size() > 10_000) throw new IllegalArgumentException("too-many-world-positions");
        JsonArray result = new JsonArray();
        for (JsonElement element : positions) {
            JsonArray position = element.getAsJsonArray();
            if (position.size() != 3) throw new IllegalArgumentException("invalid-world-position");
            BlockPos blockPosition = new BlockPos(
                    position.get(0).getAsInt(), position.get(1).getAsInt(), position.get(2).getAsInt());
            JsonObject block = new JsonObject();
            block.add("Position", position.deepCopy());
            block.add("State", serializeState(level.getBlockState(blockPosition)));
            result.add(block);
        }
        return result;
    }

    private static int setWorldBlocks(MinecraftServer server, ServerLevel level, JsonArray blocks) {
        if (blocks.size() > 10_000) throw new IllegalArgumentException("too-many-world-blocks");
        Set<BlockPos> affected = new LinkedHashSet<>();
        for (JsonElement element : blocks) {
            JsonObject block = element.getAsJsonObject();
            JsonArray position = block.getAsJsonArray("Position");
            if (position.size() != 3) throw new IllegalArgumentException("invalid-world-position");
            BlockPos blockPosition = new BlockPos(
                    position.get(0).getAsInt(), position.get(1).getAsInt(), position.get(2).getAsInt());
            level.setBlock(blockPosition, parseState(server, block.get("State").getAsString()), 3);
            affected.add(blockPosition);
            for (net.minecraft.core.Direction direction : net.minecraft.core.Direction.values()) {
                affected.add(blockPosition.relative(direction));
            }
        }
        forceBlockUpdates(level, affected);
        return blocks.size();
    }

    private static JsonObject serializeState(BlockState state) {
        JsonObject result = new JsonObject();
        result.addProperty("Name", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
        JsonObject properties = new JsonObject();
        for (Property<?> property : state.getProperties()) {
            properties.addProperty(property.getName(), propertyValue(state, property));
        }
        if (properties.size() > 0) result.add("Properties", properties);
        return result;
    }

    private static <T extends Comparable<T>> String propertyValue(BlockState state, Property<T> property) {
        return property.getName(state.getValue(property));
    }

    private static int clearRegions(ServerLevel level, JsonArray regions) {
        int cleared = 0;
        for (JsonElement element : regions) {
            JsonObject region = element.getAsJsonObject();
            JsonArray minimum = region.getAsJsonArray("Minimum");
            JsonArray maximum = region.getAsJsonArray("Maximum");
            int minX = minimum.get(0).getAsInt(), minY = minimum.get(1).getAsInt(), minZ = minimum.get(2).getAsInt();
            int maxX = maximum.get(0).getAsInt(), maxY = maximum.get(1).getAsInt(), maxZ = maximum.get(2).getAsInt();
            if (minX > maxX || minY > maxY || minZ > maxZ) throw new IllegalArgumentException("invalid-clear-region");
            long volume = (long) (maxX - minX + 1) * (maxY - minY + 1) * (maxZ - minZ + 1);
            if (volume > 10_000_000L) throw new IllegalArgumentException("clear-region-too-large");
            for (int x = minX; x <= maxX; x++) for (int y = minY; y <= maxY; y++) for (int z = minZ; z <= maxZ; z++) {
                BlockPos position = new BlockPos(x, y, z);
                if (!level.getBlockState(position).isAir()) cleared++;
                level.setBlock(position, Blocks.AIR.defaultBlockState(), 3);
                level.updateNeighborsAt(position, Blocks.AIR);
            }
        }
        return cleared;
    }

    private static Map<String, JsonObject> inputStates(JsonArray inputs, JsonArray blocks, JsonArray origin) {
        Map<String, JsonObject> result = new LinkedHashMap<>();
        for (JsonElement input : inputs) {
            JsonObject port = input.getAsJsonObject();
            BlockPos position = absolute(port.getAsJsonArray("LeverPosition"), origin);
            JsonObject state = findState(blocks, port.getAsJsonArray("LeverPosition"));
            state.addProperty("_Position", position.getX() + " " + position.getY() + " " + position.getZ());
            result.put(port.get("Name").getAsString(), state);
        }
        return result;
    }

    private static Map<String, BlockPos> outputPositions(JsonArray outputs, JsonArray origin) {
        Map<String, BlockPos> result = new LinkedHashMap<>();
        for (JsonElement output : outputs) {
            JsonObject port = output.getAsJsonObject();
            result.put(port.get("Name").getAsString(), absolute(port.getAsJsonArray("LampPosition"), origin));
        }
        return result;
    }

    private static int validateVectors(MinecraftServer server, JsonArray vectors,
                                       Map<String, JsonObject> inputs, Map<String, BlockPos> outputs, int timeoutTicks) {
        int tested = 0;
        for (JsonElement vector : vectors) {
            JsonObject item = vector.getAsJsonObject();
            OnServer(server, () -> {
                ServerLevel level = server.overworld();
                Set<BlockPos> affected = new LinkedHashSet<>();
                for (Map.Entry<String, JsonObject> input : inputs.entrySet()) {
                    JsonObject state = input.getValue().deepCopy();
                    state.getAsJsonObject("Properties").addProperty("powered", item.getAsJsonObject("Inputs").get(input.getKey()).getAsBoolean());
                    String[] position = state.remove("_Position").getAsString().split(" ");
                    BlockPos blockPosition = new BlockPos(Integer.parseInt(position[0]), Integer.parseInt(position[1]), Integer.parseInt(position[2]));
                    level.setBlock(blockPosition, parseState(server, blockState(state)), 3);
                    affected.add(blockPosition);
                    for (net.minecraft.core.Direction direction : net.minecraft.core.Direction.values()) {
                        affected.add(blockPosition.relative(direction));
                    }
                }
                forceBlockUpdates(level, affected);
                return null;
            });
            waitForTicks(server, 1);
            Snapshot previous = snapshot(server, outputs);
            long start = previous.gameTime();
            long earliestComparison = start + Math.min(
                    MINIMUM_OUTPUT_SETTLE_TICKS,
                    timeoutTicks);
            int stableTicks = 0;
            while (previous.gameTime() - start < timeoutTicks) {
                try { Thread.sleep(1); } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new Timeout("validation-interrupted"); }
                Snapshot current = snapshot(server, outputs);
                if (current.gameTime() == previous.gameTime()) continue;
                if (current.gameTime() < earliestComparison) {
                    previous = current;
                    continue;
                }
                if (current.values().equals(previous.values())) stableTicks++; else stableTicks = 0;
                if (stableTicks >= 2) {
                    compare(
                            item.getAsJsonObject("Inputs"),
                            item.getAsJsonObject("Expected"),
                            current.values());
                    break;
                }
                previous = current;
            }
            if (previous.gameTime() - start >= timeoutTicks) {
                throw new Timeout("redstone-network-did-not-settle");
            }
            tested++;
        }
        return tested;
    }

    private static Snapshot snapshot(MinecraftServer server, Map<String, BlockPos> outputs) {
        return OnServer(server, () -> new Snapshot(server.overworld().getGameTime(), sample(server.overworld(), outputs)));
    }

    private static void waitForTicks(MinecraftServer server, int ticks) {
        long target = OnServer(server, () -> server.overworld().getGameTime()) + ticks;
        while (OnServer(server, () -> server.overworld().getGameTime()) < target) {
            try { Thread.sleep(1); } catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new Timeout("validation-interrupted"); }
        }
    }

    private static <T> T OnServer(MinecraftServer server, Callable<T> callable) {
        CompletableFuture<T> result = new CompletableFuture<>();
        server.execute(() -> {
            try { result.complete(callable.call()); } catch (Exception error) { result.completeExceptionally(error); }
        });
        try { return result.get(); } catch (Exception error) { throw new RuntimeException("server-task-failed", error); }
    }

    private static Map<String, Boolean> sample(ServerLevel level, Map<String, BlockPos> outputs) {
        Map<String, Boolean> result = new LinkedHashMap<>();
        for (Map.Entry<String, BlockPos> output : outputs.entrySet()) {
            result.put(output.getKey(), level.getBlockState(output.getValue()).getValue(BlockStateProperties.LIT));
        }
        return result;
    }

    private static void compare(
            JsonObject inputs,
            JsonObject expected,
            Map<String, Boolean> actual) {
        for (Map.Entry<String, Boolean> output : actual.entrySet()) {
            if (expected.get(output.getKey()).getAsBoolean() != output.getValue()) {
                throw new Mismatch(
                        "output-mismatch:" + output.getKey()
                                + ":expected=" + expected.get(output.getKey()).getAsBoolean()
                                + ":actual=" + output.getValue()
                                + ":inputs=" + inputs);
            }
        }
    }

    private static JsonObject findState(JsonArray blocks, JsonArray position) {
        for (JsonElement element : blocks) {
            JsonObject block = element.getAsJsonObject();
            if (block.getAsJsonArray("Position").equals(position)) return block.getAsJsonObject("State").deepCopy();
        }
        throw new IllegalArgumentException("input-lever-state-not-in-fixture");
    }

    private static BlockPos absolute(JsonArray position, JsonArray origin) {
        return new BlockPos(position.get(0).getAsInt() + origin.get(0).getAsInt(), position.get(1).getAsInt() + origin.get(1).getAsInt(), position.get(2).getAsInt() + origin.get(2).getAsInt());
    }

    private static String blockState(JsonObject state) {
        StringBuilder value = new StringBuilder(state.get("Name").getAsString());
        if (state.has("Properties")) {
            value.append("[");
            boolean first = true;
            for (Map.Entry<String, JsonElement> property : state.getAsJsonObject("Properties").entrySet()) {
                if (!first) value.append(",");
                value.append(property.getKey()).append("=").append(property.getValue().getAsString());
                first = false;
            }
            value.append("]");
        }
        return value.toString();
    }

    private static net.minecraft.world.level.block.state.BlockState parseState(MinecraftServer server, String state) {
        try {
            return BlockStateParser.parseForBlock(
                    server.registryAccess().lookupOrThrow(Registries.BLOCK), state, false).blockState();
        } catch (Exception error) {
            throw new IllegalArgumentException("invalid-fixture-block-state:" + state, error);
        }
    }

    private static String sha256(byte[] bytes) throws NoSuchAlgorithmException {
        StringBuilder value = new StringBuilder();
        for (byte item : MessageDigest.getInstance("SHA-256").digest(bytes)) value.append(String.format("%02x", item));
        return value.toString();
    }

    private static final class Mismatch extends RuntimeException { Mismatch(String value) { super(value); } }
    private static final class Timeout extends RuntimeException { Timeout(String value) { super(value); } }
    private record Snapshot(long gameTime, Map<String, Boolean> values) { }
}
