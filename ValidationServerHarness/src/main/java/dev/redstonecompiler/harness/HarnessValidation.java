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
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.permissions.PermissionSet;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.entity.SignText;
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
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;

/** Executes only validated compiler fixtures on the main server thread. */
final class HarnessValidation {
    static final int REQUIRED_UNCHANGED_TICKS = 40;
    private static final int MAXIMUM_FORCED_FIXTURE_CHUNKS = 1_024;
    private static final int MAXIMUM_TRACE_PROBES = 10_000;
    private static final Set<String> FALLBACK_TRACE_BLOCKS = Set.of(
            "minecraft:comparator",
            "minecraft:lever",
            "minecraft:redstone_lamp",
            "minecraft:redstone_torch",
            "minecraft:redstone_wall_torch",
            "minecraft:redstone_wire",
            "minecraft:repeater");
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
            if (!"Validate".equals(action)
                    && !"ValidateExisting".equals(action)
                    && !"LoadFixture".equals(action)) {
                throw new IllegalArgumentException("unsupported-action");
            }
            boolean validateExisting = "ValidateExisting".equals(action);
            Path fixturePath = Path.of(request.get("FixturePath").getAsString()).toRealPath();
            String expectedDigest = request.get("FixtureSha256").getAsString();
            String actualDigest = sha256(Files.readAllBytes(fixturePath));
            if (!expectedDigest.equals(actualDigest)) {
                throw new IllegalArgumentException("fixture-digest-mismatch");
            }
            JsonObject fixture = JsonParser.parseString(Files.readString(fixturePath)).getAsJsonObject();
            JsonArray origin = fixture.getAsJsonObject("Arena").getAsJsonArray("Origin");
            int initialForcedFixtureChunkCount = OnServer(server, () -> {
                CommandSourceStack source = server.createCommandSourceStack().withPermission(PermissionSet.ALL_PERMISSIONS);
                setTickRate(server, source, configuration.requestedTickRate());
                ServerLevel level = server.overworld();
                int forcedChunkCount = forceFixtureChunks(
                        level,
                        fixture.getAsJsonArray("Blocks"),
                        List.of(origin),
                        fixture.getAsJsonObject("Arena"));
                if (!validateExisting) {
                    clearFixture(
                            level,
                            fixture.getAsJsonArray("Blocks"),
                            origin,
                            fixture.getAsJsonObject("Arena"));
                    placeFixture(
                            server,
                            level,
                            fixture.getAsJsonArray("Blocks"),
                            fixture.has("Signs")
                                    ? fixture.getAsJsonArray("Signs")
                                    : new JsonArray(),
                            origin);
                    forceBlockUpdates(
                            level,
                            fixture.getAsJsonArray("Blocks"),
                            origin);
                }
                return forcedChunkCount;
            });
            diagnostics.addProperty("WorldStateMode", validateExisting ? "existing" : "fixture-paste");
            diagnostics.addProperty("FixtureArenaCleared", !validateExisting);
            diagnostics.addProperty("FixturePasted", !validateExisting);
            diagnostics.addProperty("InputStatesRestored", validateExisting);
            waitForTicks(server, 2);
            if ("LoadFixture".equals(action)) {
                response.addProperty("Status", "loaded");
                diagnostics.addProperty("FixtureSha256", actualDigest);
                diagnostics.addProperty("LoadedBlocks", fixture.getAsJsonArray("Blocks").size());
                diagnostics.addProperty(
                        "LoadedSigns",
                        fixture.has("Signs") ? fixture.getAsJsonArray("Signs").size() : 0);
                diagnostics.addProperty("ForcedFixtureChunks", initialForcedFixtureChunkCount);
                diagnostics.addProperty("RequestedTickRate", configuration.requestedTickRate());
                diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
                writeResponse(response, diagnostics, output);
                return;
            }
            Map<String, JsonObject> inputStateMap = validateExisting
                    ? OnServer(server, () -> inputStatesFromWorld(
                            server.overworld(),
                            fixture.getAsJsonArray("Inputs"),
                            origin))
                    : inputStates(
                            fixture.getAsJsonArray("Inputs"),
                            fixture.getAsJsonArray("Blocks"),
                            origin);
            ValidationFixture validationFixture = new ValidationFixture(
                    inputStateMap,
                    outputPositions(fixture.getAsJsonArray("Outputs"), origin),
                    BuildTraceProbes(fixture, origin));
            ValidationSummary summary;
            try {
                summary = validateVectors(
                        server,
                        request.getAsJsonArray("Vectors"),
                        validationFixture,
                        configuration.settleTimeoutTicks(),
                        output);
            } finally {
                if (validateExisting) {
                    OnServer(server, () -> {
                        restoreInputStates(
                                server,
                                server.overworld(),
                                validationFixture.InputStates());
                        return null;
                    });
                    waitForTicks(server, 2);
                }
            }
            response.addProperty("Status", "passed");
            diagnostics.addProperty("FixtureSha256", actualDigest);
            diagnostics.addProperty("TestedVectors", summary.TestedVectors());
            diagnostics.addProperty("RequiredUnchangedTicks", REQUIRED_UNCHANGED_TICKS);
            diagnostics.addProperty("TraceProbeCount", summary.TraceProbeCount());
            diagnostics.addProperty("MaximumSettleTicks", summary.MaximumSettleTicks());
            diagnostics.addProperty("UnobservedTickGapCount", summary.UnobservedTickGapCount());
            diagnostics.addProperty("ValidationMode", "single-fixture-canary");
            diagnostics.addProperty(
                    "ForcedFixtureChunks",
                    initialForcedFixtureChunkCount);
            diagnostics.addProperty("RequestedTickRate", configuration.requestedTickRate());
            diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
        } catch (Mismatch error) {
            response.addProperty("Status", "mismatch");
            diagnostics.addProperty("Error", error.getMessage());
            diagnostics.add("Mismatch", error.Details());
            diagnostics.add(
                    "TestedVectors",
                    error.Details().get("TestedVectorsBeforeFailure"));
            diagnostics.add("TraceBlocks", error.TraceBlocks());
        } catch (Timeout error) {
            response.addProperty("Status", "timeout");
            diagnostics.addProperty("Error", error.getMessage());
            if (error.Details() != null) {
                diagnostics.add("Timeout", error.Details());
                diagnostics.add(
                        "TestedVectors",
                        error.Details().get("TestedVectorsBeforeFailure"));
            }
            if (error.TraceBlocks() != null) diagnostics.add("TraceBlocks", error.TraceBlocks());
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

    static JsonObject BuildValidationProgress(
            int completed,
            int total) {
        JsonObject Progress = new JsonObject();
        Progress.addProperty("Status", "progress");
        Progress.addProperty("Completed", completed);
        Progress.addProperty("Total", total);
        Progress.addProperty(
                "Stage",
                "required single-fixture Fabric canary validation");
        return Progress;
    }

    private static void WriteValidationProgress(
            int completed,
            int total,
            BufferedWriter output) throws IOException {
        output.write(BuildValidationProgress(completed, total) + "\n");
        output.flush();
    }

    private static void setTickRate(MinecraftServer server, CommandSourceStack source, double rate) {
        server.getCommands().performPrefixedCommand(source, "tick rate " + rate);
    }

    private static void placeFixture(
            MinecraftServer server,
            ServerLevel level,
            JsonArray blocks,
            JsonArray signs,
            JsonArray origin) {
        for (JsonElement element : blocks) {
            JsonObject block = element.getAsJsonObject();
            BlockPos position = absolute(block.getAsJsonArray("Position"), origin);
            level.setBlock(position, parseState(server, blockState(block.getAsJsonObject("State"))), 3);
        }
        for (JsonElement element : signs) {
            JsonObject sign = element.getAsJsonObject();
            BlockPos position = absolute(sign.getAsJsonArray("Position"), origin);
            if (!(level.getBlockEntity(position) instanceof SignBlockEntity blockEntity)) {
                throw new IllegalArgumentException("fixture-sign-position-is-not-a-sign:" + position.toShortString());
            }
            blockEntity.setText(signText(sign.getAsJsonArray("FrontText")), true);
            blockEntity.setText(signText(sign.getAsJsonArray("BackText")), false);
        }
        for (JsonElement element : blocks) {
            BlockPos position = absolute(element.getAsJsonObject().getAsJsonArray("Position"), origin);
            level.updateNeighborsAt(position, level.getBlockState(position).getBlock());
        }
    }

    private static SignText signText(JsonArray lines) {
        if (lines.size() != 4) throw new IllegalArgumentException("fixture-sign-text-must-have-four-lines");
        SignText result = new SignText();
        for (int index = 0; index < lines.size(); index++) {
            if (!lines.get(index).isJsonPrimitive() || !lines.get(index).getAsJsonPrimitive().isString()) {
                throw new IllegalArgumentException("fixture-sign-line-must-be-a-string");
            }
            result = result.setMessage(index, Component.literal(lines.get(index).getAsString()));
        }
        return result;
    }

    private static int forceFixtureChunks(
            ServerLevel level,
            JsonArray blocks,
            List<JsonArray> origins,
            JsonObject arena) {
        Set<ChunkCoordinate> requested = new LinkedHashSet<>();
        for (JsonArray origin : origins) {
            requested.addAll(fixtureChunks(blocks, origin, arena));
        }
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

    private static Map<String, JsonObject> inputStatesFromWorld(
            ServerLevel level, JsonArray inputs, JsonArray origin) {
        Map<String, JsonObject> result = new LinkedHashMap<>();
        for (JsonElement input : inputs) {
            JsonObject port = input.getAsJsonObject();
            BlockPos position = absolute(port.getAsJsonArray("LeverPosition"), origin);
            JsonObject state = serializeState(level.getBlockState(position));
            if (!"minecraft:lever".equals(state.get("Name").getAsString())
                    || !state.has("Properties")
                    || !state.getAsJsonObject("Properties").has("powered")) {
                throw new IllegalArgumentException(
                        "existing-fixture-input-is-not-a-lever:" + position.toShortString());
            }
            state.addProperty("_Position", position.getX() + " " + position.getY() + " " + position.getZ());
            result.put(port.get("Name").getAsString(), state);
        }
        return result;
    }

    private static void restoreInputStates(
            MinecraftServer server, ServerLevel level, Map<String, JsonObject> inputStates) {
        Set<BlockPos> affected = new LinkedHashSet<>();
        for (JsonObject original : inputStates.values()) {
            JsonObject state = original.deepCopy();
            String[] position = state.remove("_Position").getAsString().split(" ");
            BlockPos blockPosition = new BlockPos(
                    Integer.parseInt(position[0]),
                    Integer.parseInt(position[1]),
                    Integer.parseInt(position[2]));
            level.setBlock(blockPosition, parseState(server, blockState(state)), 3);
            affected.add(blockPosition);
            for (net.minecraft.core.Direction direction : net.minecraft.core.Direction.values()) {
                affected.add(blockPosition.relative(direction));
            }
        }
        forceBlockUpdates(level, affected);
    }

    private static Map<String, BlockPos> outputPositions(JsonArray outputs, JsonArray origin) {
        Map<String, BlockPos> result = new LinkedHashMap<>();
        for (JsonElement output : outputs) {
            JsonObject port = output.getAsJsonObject();
            result.put(port.get("Name").getAsString(), absolute(port.getAsJsonArray("LampPosition"), origin));
        }
        return result;
    }

    private static ValidationSummary validateVectors(
            MinecraftServer server,
            JsonArray vectors,
            ValidationFixture fixture,
            int timeoutTicks,
            BufferedWriter progressOutput) throws IOException {
        if (fixture.TraceProbes().isEmpty()) {
            throw new IllegalArgumentException("fixture-has-no-trace-probes");
        }
        int traceProbeCount = fixture.TraceProbes().size();
        if (traceProbeCount > MAXIMUM_TRACE_PROBES) {
            throw new IllegalArgumentException(
                    "too-many-trace-probes:" + traceProbeCount);
        }
        int tested = 0;
        int MaximumSettleTicks = 0;
        int TotalUnobservedTickGaps = 0;
        WriteValidationProgress(
                tested,
                vectors.size(),
                progressOutput);
        for (JsonElement element : vectors) {
            JsonObject vector = element.getAsJsonObject();
            OnServer(server, () -> {
                ApplyValidationVector(
                        server,
                        server.overworld(),
                        fixture.InputStates(),
                        vector);
                return null;
            });
            waitForTicks(server, 1);
            TickFixtureObserver observer = OnServer(server, () -> {
                TickFixtureObserver installed = new TickFixtureObserver(
                        server,
                        fixture,
                        timeoutTicks);
                RedstoneCompilerHarness.InstallServerTickObserver(installed);
                return installed;
            });
            try {
                observer.AwaitCompletion();
            } finally {
                RedstoneCompilerHarness.RemoveServerTickObserver(observer);
            }
            TraceQuiescenceTracker.TraceQuiescenceStatus status =
                    observer.Quiescence().Status();
            SettlementEvidence evidence = new SettlementEvidence(
                    status.ElapsedTicks(),
                    status.LastObservedChangeTick(),
                    status.ObservedUnchangedTicks(),
                    traceProbeCount,
                    status.UnobservedTickGapCount());
            Snapshot settled = observer.SettledSnapshot();
            if (settled == null) {
                Snapshot lastObserved = observer.PreviousSnapshot();
                throw BuildTimeout(
                        vector,
                        lastObserved.OutputValues(),
                        tested,
                        evidence)
                        .WithTraceBlocks(lastObserved.TraceBlocks());
            }
            try {
                compare(
                        vector,
                        settled.OutputValues(),
                        tested,
                        evidence);
            } catch (Mismatch error) {
                throw error.WithTraceBlocks(settled.TraceBlocks());
            }
            MaximumSettleTicks = Math.max(
                    MaximumSettleTicks,
                    evidence.ElapsedTicks());
            TotalUnobservedTickGaps += status.UnobservedTickGapCount();
            tested++;
            WriteValidationProgress(
                    tested,
                    vectors.size(),
                    progressOutput);
        }
        return new ValidationSummary(
                tested,
                traceProbeCount,
                MaximumSettleTicks,
                TotalUnobservedTickGaps);
    }

    private static void ApplyValidationVector(
            MinecraftServer server,
            ServerLevel level,
            Map<String, JsonObject> inputs,
            JsonObject vector) {
        Set<BlockPos> affected = new LinkedHashSet<>();
        for (Map.Entry<String, JsonObject> input : inputs.entrySet()) {
            JsonObject state = input.getValue().deepCopy();
            state.getAsJsonObject("Properties").addProperty(
                    "powered",
                    vector.getAsJsonObject("Inputs")
                            .get(input.getKey()).getAsBoolean());
            String[] position = state.remove("_Position").getAsString().split(" ");
            BlockPos blockPosition = new BlockPos(
                    Integer.parseInt(position[0]),
                    Integer.parseInt(position[1]),
                    Integer.parseInt(position[2]));
            level.setBlock(
                    blockPosition,
                    parseState(server, blockState(state)),
                    3);
            affected.add(blockPosition);
            for (net.minecraft.core.Direction direction
                    : net.minecraft.core.Direction.values()) {
                affected.add(blockPosition.relative(direction));
            }
        }
        forceBlockUpdates(level, affected);
    }

    private static Snapshot CaptureSnapshot(
            ServerLevel level,
            ValidationFixture fixture) {
        return new Snapshot(
                level.getGameTime(),
                sample(level, fixture.OutputPositions()),
                ReadTraceStates(level, fixture.TraceProbes()),
                null);
    }

    private static Snapshot CaptureDetailedSnapshot(
            ServerLevel level,
            ValidationFixture fixture,
            long gameTime,
            List<BlockState> traceStates) {
        return new Snapshot(
                gameTime,
                sample(level, fixture.OutputPositions()),
                traceStates,
                ReadTraceBlocks(level, fixture.TraceProbes()));
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
            JsonObject vector,
            Map<String, Boolean> actual,
            int testedVectorsBeforeFailure,
            SettlementEvidence Evidence) {
        JsonObject inputs = vector.getAsJsonObject("Inputs");
        JsonObject expected = vector.getAsJsonObject("Expected");
        for (Map.Entry<String, Boolean> output : actual.entrySet()) {
            if (expected.get(output.getKey()).getAsBoolean() != output.getValue()) {
                boolean expectedValue = expected.get(output.getKey()).getAsBoolean();
                JsonObject details = new JsonObject();
                details.addProperty("Output", output.getKey());
                details.addProperty("Expected", expectedValue);
                details.addProperty("Actual", output.getValue());
                details.add("Inputs", inputs.deepCopy());
                details.addProperty("TestedVectorsBeforeFailure", testedVectorsBeforeFailure);
                AddSettlementEvidence(details, Evidence);
                details.addProperty(
                        "GlobalVectorIndex",
                        testedVectorsBeforeFailure);
                if (vector.has("ExpectedSignals")) {
                    details.add("ExpectedSignals", vector.getAsJsonObject("ExpectedSignals").deepCopy());
                }
                throw new Mismatch(
                        "output-mismatch:" + output.getKey()
                                + ":expected=" + expectedValue
                                + ":actual=" + output.getValue()
                                + ":inputs=" + inputs,
                        details,
                        new JsonArray());
            }
        }
    }

    private static Timeout BuildTimeout(
            JsonObject vector,
            Map<String, Boolean> actual,
            int testedVectorsBeforeFailure,
            SettlementEvidence Evidence) {
        JsonObject expected = vector.getAsJsonObject("Expected");
        String selectedOutput = null;
        for (Map.Entry<String, Boolean> output : actual.entrySet()) {
            selectedOutput = output.getKey();
            if (expected.get(output.getKey()).getAsBoolean() != output.getValue()) break;
        }
        JsonObject details = new JsonObject();
        details.addProperty("Reason", "redstone-network-did-not-settle");
        details.add("Inputs", vector.getAsJsonObject("Inputs").deepCopy());
        details.addProperty("TestedVectorsBeforeFailure", testedVectorsBeforeFailure);
        AddSettlementEvidence(details, Evidence);
        details.addProperty(
                "GlobalVectorIndex",
                testedVectorsBeforeFailure);
        if (selectedOutput != null) {
            details.addProperty("Output", selectedOutput);
            details.addProperty("Expected", expected.get(selectedOutput).getAsBoolean());
            details.addProperty("Actual", actual.get(selectedOutput));
        }
        if (vector.has("ExpectedSignals")) {
            details.add("ExpectedSignals", vector.getAsJsonObject("ExpectedSignals").deepCopy());
        }
        return new Timeout("redstone-network-did-not-settle", details, new JsonArray());
    }

    private static void AddSettlementEvidence(
            JsonObject Details,
            SettlementEvidence Evidence) {
        Details.addProperty("RequiredUnchangedTicks", REQUIRED_UNCHANGED_TICKS);
        Details.addProperty("ElapsedTicks", Evidence.ElapsedTicks());
        Details.addProperty("LastObservedChangeTick", Evidence.LastObservedChangeTick());
        Details.addProperty("ObservedUnchangedTicks", Evidence.ObservedUnchangedTicks());
        Details.addProperty("TraceProbeCount", Evidence.TraceProbeCount());
        Details.addProperty("UnobservedTickGapCount", Evidence.UnobservedTickGapCount());
    }

    private static List<TraceProbe> BuildTraceProbes(
            JsonObject Fixture,
            JsonArray Origin) {
        List<TraceProbe> Result = new ArrayList<>();
        JsonArray Positions = new JsonArray();
        if (Fixture.has("Trace")
                && Fixture.getAsJsonObject("Trace").has("ProbePositions")) {
            Positions = Fixture.getAsJsonObject("Trace").getAsJsonArray("ProbePositions");
        } else {
            for (JsonElement Element : Fixture.getAsJsonArray("Blocks")) {
                JsonObject Block = Element.getAsJsonObject();
                String Name = Block.getAsJsonObject("State").get("Name").getAsString();
                if (FALLBACK_TRACE_BLOCKS.contains(Name)) {
                    Positions.add(Block.getAsJsonArray("Position").deepCopy());
                }
            }
        }
        if (Positions.size() > MAXIMUM_TRACE_PROBES) {
            throw new IllegalArgumentException("too-many-trace-probes:" + Positions.size());
        }
        Set<BlockPos> Seen = new LinkedHashSet<>();
        for (JsonElement Element : Positions) {
            JsonArray RelativePosition = Element.getAsJsonArray();
            BlockPos WorldPosition = absolute(RelativePosition, Origin);
            if (Seen.add(WorldPosition)) {
                Result.add(new TraceProbe(RelativePosition.deepCopy(), WorldPosition));
            }
        }
        return Result;
    }

    private static JsonArray ReadTraceBlocks(
            ServerLevel level,
            List<TraceProbe> TraceProbes) {
        JsonArray result = new JsonArray();
        for (TraceProbe Probe : TraceProbes) {
            JsonObject block = new JsonObject();
            block.add("Position", Probe.RelativePosition().deepCopy());
            JsonArray serializedWorldPosition = new JsonArray();
            serializedWorldPosition.add(Probe.WorldPosition().getX());
            serializedWorldPosition.add(Probe.WorldPosition().getY());
            serializedWorldPosition.add(Probe.WorldPosition().getZ());
            block.add("WorldPosition", serializedWorldPosition);
            block.add("State", serializeState(level.getBlockState(Probe.WorldPosition())));
            result.add(block);
        }
        return result;
    }

    private static List<BlockState> ReadTraceStates(
            ServerLevel level,
            List<TraceProbe> TraceProbes) {
        List<BlockState> states = new ArrayList<>(TraceProbes.size());
        for (TraceProbe probe : TraceProbes) {
            states.add(level.getBlockState(probe.WorldPosition()));
        }
        return states;
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

    private static final class TickFixtureObserver
            implements RedstoneCompilerHarness.ServerTickObserver {
        private final ValidationFixture Fixture;
        private Snapshot PreviousSnapshot;
        private final TraceQuiescenceTracker Quiescence;
        private Snapshot SettledSnapshot;
        private final CountDownLatch Finished = new CountDownLatch(1);
        private volatile RuntimeException Failure;

        TickFixtureObserver(
                MinecraftServer server,
                ValidationFixture fixture,
                int timeoutTicks) {
            Fixture = fixture;
            PreviousSnapshot = CaptureSnapshot(server.overworld(), Fixture);
            Quiescence = new TraceQuiescenceTracker(
                    REQUIRED_UNCHANGED_TICKS,
                    timeoutTicks,
                    PreviousSnapshot.GameTime(),
                    PreviousSnapshot.TraceStates());
        }

        @Override
        public void OnServerTick(MinecraftServer server) {
            if (Finished.getCount() == 0) {
                return;
            }
            try {
                Snapshot current = CaptureSnapshot(server.overworld(), Fixture);
                if (current.GameTime() == PreviousSnapshot.GameTime()) {
                    return;
                }
                TraceQuiescenceTracker.TraceQuiescenceStatus status =
                        Quiescence.Observe(
                                current.GameTime(),
                                current.TraceStates());
                if (status.Settled()) {
                    SettledSnapshot = CaptureDetailedSnapshot(
                            server.overworld(),
                            Fixture,
                            current.GameTime(),
                            current.TraceStates());
                    PreviousSnapshot = SettledSnapshot;
                    Finished.countDown();
                } else if (status.TimedOut()) {
                    PreviousSnapshot = CaptureDetailedSnapshot(
                            server.overworld(),
                            Fixture,
                            current.GameTime(),
                            current.TraceStates());
                    Finished.countDown();
                } else {
                    PreviousSnapshot = current;
                }
            } catch (RuntimeException error) {
                Failure = error;
                Finished.countDown();
            }
        }

        void AwaitCompletion() {
            try {
                Finished.await();
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new Timeout("validation-interrupted");
            }
            if (Failure != null) {
                throw Failure;
            }
        }

        Snapshot PreviousSnapshot() {
            return PreviousSnapshot;
        }

        TraceQuiescenceTracker Quiescence() {
            return Quiescence;
        }

        Snapshot SettledSnapshot() {
            return SettledSnapshot;
        }
    }

    private static final class Mismatch extends RuntimeException {
        private final JsonObject Details;
        private final JsonArray TraceBlocks;

        Mismatch(String value, JsonObject details, JsonArray traceBlocks) {
            super(value);
            Details = details;
            TraceBlocks = traceBlocks;
        }

        JsonObject Details() { return Details; }
        JsonArray TraceBlocks() { return TraceBlocks; }

        Mismatch WithTraceBlocks(JsonArray traceBlocks) {
            return new Mismatch(getMessage(), Details, traceBlocks);
        }
    }
    private static final class Timeout extends RuntimeException {
        private final JsonObject Details;
        private final JsonArray TraceBlocks;

        Timeout(String value) {
            this(value, null, null);
        }

        Timeout(String value, JsonObject details, JsonArray traceBlocks) {
            super(value);
            Details = details;
            TraceBlocks = traceBlocks;
        }

        JsonObject Details() { return Details; }
        JsonArray TraceBlocks() { return TraceBlocks; }

        Timeout WithTraceBlocks(JsonArray traceBlocks) {
            return new Timeout(getMessage(), Details, traceBlocks);
        }
    }
    private record TraceProbe(JsonArray RelativePosition, BlockPos WorldPosition) { }
    private record ValidationFixture(
            Map<String, JsonObject> InputStates,
            Map<String, BlockPos> OutputPositions,
            List<TraceProbe> TraceProbes) { }
    private record Snapshot(
            long GameTime,
            Map<String, Boolean> OutputValues,
            List<BlockState> TraceStates,
            JsonArray TraceBlocks) { }
    private record SettlementEvidence(
            int ElapsedTicks,
            int LastObservedChangeTick,
            int ObservedUnchangedTicks,
            int TraceProbeCount,
            int UnobservedTickGapCount) { }
    private record ValidationSummary(
            int TestedVectors,
            int TraceProbeCount,
            int MaximumSettleTicks,
            int UnobservedTickGapCount) { }
}
