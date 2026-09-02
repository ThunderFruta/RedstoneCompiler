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
    static final int VALIDATION_TPS_CALIBRATION_TICKS = 40;
    private static final int MAXIMUM_FORCED_FIXTURE_CHUNKS = 1_024;
    private static final int MAXIMUM_TRACE_PROBES = 10_000;
    private static final int MAXIMUM_ACTIVE_TRACE_PROBES = 160_000;
    private static final int VALIDATION_LANE_CLEARANCE_BLOCKS = 16;
    private static final int VALIDATION_STACK_GRID_COLUMNS = 4;
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
            boolean selectValidationLaneCount = "Validate".equals(action);
            int maximumValidationLaneCount = selectValidationLaneCount
                    ? BuildMaximumValidationLaneCount(
                            request.getAsJsonArray("Vectors").size(),
                            configuration.validationLanesPerStack(),
                            configuration.maximumValidationStackCount(),
                            BuildTraceProbes(fixture, origin).size())
                    : 1;
            List<JsonArray> laneOrigins = "Validate".equals(action)
                    ? BuildValidationLaneOriginsForLaneCount(
                            fixture,
                            maximumValidationLaneCount,
                            configuration.validationLanesPerStack())
                    : List.of(origin.deepCopy());
            int initialForcedFixtureChunkCount = OnServer(server, () -> {
                CommandSourceStack source = server.createCommandSourceStack().withPermission(PermissionSet.ALL_PERMISSIONS);
                setTickRate(server, source, configuration.requestedTickRate());
                ServerLevel level = server.overworld();
                int forcedChunkCount = forceFixtureChunks(
                        level,
                        fixture.getAsJsonArray("Blocks"),
                        laneOrigins,
                        fixture.getAsJsonObject("Arena"));
                if (!validateExisting) {
                    for (JsonArray laneOrigin : laneOrigins) {
                        clearFixture(
                                level,
                                fixture.getAsJsonArray("Blocks"),
                                laneOrigin,
                                fixture.getAsJsonObject("Arena"));
                        placeFixture(
                                server,
                                level,
                                fixture.getAsJsonArray("Blocks"),
                                fixture.has("Signs")
                                        ? fixture.getAsJsonArray("Signs")
                                        : new JsonArray(),
                                laneOrigin);
                        forceBlockUpdates(
                                level,
                                fixture.getAsJsonArray("Blocks"),
                                laneOrigin);
                    }
                }
                return forcedChunkCount;
            });
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
            List<ValidationLane> availableValidationLanes = new ArrayList<>();
            for (int laneIndex = 0; laneIndex < laneOrigins.size(); laneIndex++) {
                JsonArray laneOrigin = laneOrigins.get(laneIndex);
                Map<String, JsonObject> laneInputStates = validateExisting
                        ? OnServer(server, () -> inputStatesFromWorld(
                                server.overworld(),
                                fixture.getAsJsonArray("Inputs"),
                                laneOrigin))
                        : inputStates(
                                fixture.getAsJsonArray("Inputs"),
                                fixture.getAsJsonArray("Blocks"),
                                laneOrigin);
                availableValidationLanes.add(new ValidationLane(
                        laneIndex,
                        laneOrigin.deepCopy(),
                        laneInputStates,
                        outputPositions(
                                fixture.getAsJsonArray("Outputs"),
                                laneOrigin),
                        BuildTraceProbes(fixture, laneOrigin)));
            }
            ValidationTpsSelection tpsSelection = selectValidationLaneCount
                    ? SelectMaximumSustainingValidationLanes(
                            server,
                            fixture,
                            request.getAsJsonArray("Vectors"),
                            availableValidationLanes,
                            configuration.requestedTickRate())
                    : new ValidationTpsSelection(
                            1,
                            initialForcedFixtureChunkCount,
                            List.of());
            JsonArray tpsCalibrationSamples = new JsonArray();
            for (ValidationTpsSample sample : tpsSelection.Samples()) {
                JsonObject sampleJson = new JsonObject();
                sampleJson.addProperty("ValidationLaneCount", sample.LaneCount());
                sampleJson.addProperty("ObservedTicks", sample.ObservedTicks());
                sampleJson.addProperty(
                        "AverageTickProcessingNanos",
                        sample.AverageTickProcessingNanos());
                sampleJson.addProperty(
                        "MaximumTickProcessingNanos",
                        sample.MaximumTickProcessingNanos());
                sampleJson.addProperty(
                        "SustainedTickRate",
                        sample.SustainedTickRate());
                sampleJson.addProperty(
                        "MeetsRequestedTickRate",
                        sample.MeetsRequestedTickRate());
                tpsCalibrationSamples.add(sampleJson);
            }
            diagnostics.add("ValidationTpsCalibration", tpsCalibrationSamples);
            diagnostics.addProperty(
                    "ValidationLaneSelectionPolicy",
                    selectValidationLaneCount
                            ? "maximum-measured-at-requested-tps"
                            : "single-existing-fixture");
            if (tpsSelection.LaneCount() < 1) {
                throw new IllegalStateException(
                        "no-validation-lane-count-sustains-requested-tps:"
                                + configuration.requestedTickRate());
            }
            List<ValidationLane> validationLanes = List.copyOf(
                    availableValidationLanes.subList(
                            0,
                            tpsSelection.LaneCount()));
            ValidationLanePlan lanePlan = selectValidationLaneCount
                    ? BuildValidationLanePlan(
                            request.getAsJsonArray("Vectors").size(),
                            validationLanes.size(),
                            configuration.validationLanesPerStack(),
                            configuration.maximumValidationStackCount(),
                            configuration.settleTimeoutTicks(),
                            configuration.requestedTickRate())
                    : ValidationLanePlan.SingleLane();
            ValidationSummary summary;
            try {
                summary = validateVectors(
                        server,
                        request.getAsJsonArray("Vectors"),
                        validationLanes,
                        configuration.settleTimeoutTicks(),
                        output);
            } finally {
                if (validateExisting) {
                    OnServer(server, () -> {
                        restoreInputStates(
                                server,
                                server.overworld(),
                                validationLanes.getFirst().InputStates());
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
            diagnostics.addProperty("ValidationLaneCount", validationLanes.size());
            diagnostics.addProperty("ValidationLanesPerStack", lanePlan.LanesPerStack());
            diagnostics.addProperty("ValidationStackCount", lanePlan.StackCount());
            diagnostics.addProperty(
                    "MaximumValidationStackCount",
                    lanePlan.MaximumStackCount());
            diagnostics.addProperty(
                    "WorstCaseValidationTickBudgetSeconds",
                    lanePlan.TickBudgetSeconds());
            diagnostics.addProperty(
                    "EstimatedValidationBatchCount",
                    lanePlan.BatchCount());
            JsonArray validationLaneOrigins = new JsonArray();
            for (ValidationLane validationLane : validationLanes) {
                validationLaneOrigins.add(validationLane.Origin().deepCopy());
            }
            diagnostics.add("ValidationLaneOrigins", validationLaneOrigins);
            diagnostics.addProperty(
                    "ActiveTraceProbeCount",
                    summary.TraceProbeCount() * validationLanes.size());
            diagnostics.addProperty(
                    "ForcedFixtureChunks",
                    tpsSelection.ForcedFixtureChunkCount());
            diagnostics.addProperty("RequestedTickRate", configuration.requestedTickRate());
            diagnostics.addProperty("ObservedGameTime", OnServer(server, () -> server.overworld().getGameTime()));
            diagnostics.addProperty("WorldStateMode", validateExisting ? "existing" : "fixture-paste");
            diagnostics.addProperty("FixturePasted", !validateExisting);
            diagnostics.addProperty("InputStatesRestored", validateExisting);
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
            int total,
            int laneCount) {
        JsonObject Progress = new JsonObject();
        Progress.addProperty("Status", "progress");
        Progress.addProperty("Completed", completed);
        Progress.addProperty("Total", total);
        Progress.addProperty("ValidationLaneCount", laneCount);
        Progress.addProperty(
                "Stage",
                "authoritative Fabric truth-table validation | lanes="
                        + laneCount);
        return Progress;
    }

    private static void WriteValidationProgress(
            int completed,
            int total,
            int laneCount,
            BufferedWriter output) throws IOException {
        output.write(BuildValidationProgress(completed, total, laneCount) + "\n");
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

    static int BuildMaximumValidationLaneCount(
            int vectorCount,
            int lanesPerStack,
            int maximumStackCount,
            int traceProbeCount) {
        if (vectorCount < 0) {
            throw new IllegalArgumentException("validation-vector-count-is-negative");
        }
        if (lanesPerStack != 4) {
            throw new IllegalArgumentException("validation-lanes-per-stack-must-be-four");
        }
        if (maximumStackCount < 1 || maximumStackCount > 16) {
            throw new IllegalArgumentException("validation-stack-count-out-of-range");
        }
        if (traceProbeCount <= 0 || traceProbeCount > MAXIMUM_TRACE_PROBES) {
            throw new IllegalArgumentException("validation-trace-probe-count-is-invalid");
        }
        int usefulLaneCount = Math.max(1, vectorCount);
        int configuredLaneCount = maximumStackCount * lanesPerStack;
        int traceLimitedLaneCount = MAXIMUM_ACTIVE_TRACE_PROBES / traceProbeCount;
        return Math.min(
                usefulLaneCount,
                Math.min(configuredLaneCount, traceLimitedLaneCount));
    }

    static ValidationLanePlan BuildValidationLanePlan(
            int vectorCount,
            int laneCount,
            int lanesPerStack,
            int maximumStackCount,
            int settleTimeoutTicks,
            double requestedTickRate) {
        if (laneCount < 1 || laneCount > maximumStackCount * lanesPerStack) {
            throw new IllegalArgumentException("validation-lane-count-out-of-range");
        }
        if (!Double.isFinite(requestedTickRate) || requestedTickRate <= 0.0D) {
            throw new IllegalArgumentException("validation-tick-rate-is-invalid");
        }
        if (settleTimeoutTicks <= 0) {
            throw new IllegalArgumentException("validation-settle-timeout-is-invalid");
        }
        int stackCount = (int) Math.ceil(laneCount / (double) lanesPerStack);
        int batchCount = vectorCount == 0
                ? 0
                : (int) Math.ceil(vectorCount / (double) laneCount);
        double tickBudgetSeconds = batchCount
                * settleTimeoutTicks
                / requestedTickRate;
        return new ValidationLanePlan(
                laneCount,
                lanesPerStack,
                stackCount,
                maximumStackCount,
                batchCount,
                tickBudgetSeconds);
    }

    static ValidationTpsSample BuildValidationTpsSample(
            int laneCount,
            int observedTicks,
            long totalTickProcessingNanos,
            long maximumTickProcessingNanos,
            double requestedTickRate) {
        if (laneCount < 1 || observedTicks < 1 || totalTickProcessingNanos < 1L
                || maximumTickProcessingNanos < 1L
                || !Double.isFinite(requestedTickRate)
                || requestedTickRate <= 0.0D) {
            throw new IllegalArgumentException("validation-tps-sample-is-invalid");
        }
        double averageTickProcessingNanos =
                totalTickProcessingNanos / (double) observedTicks;
        double processingCapacityTps = 1_000_000_000.0D
                / averageTickProcessingNanos;
        double sustainedTickRate = Math.min(
                requestedTickRate,
                processingCapacityTps);
        return new ValidationTpsSample(
                laneCount,
                observedTicks,
                averageTickProcessingNanos,
                maximumTickProcessingNanos,
                sustainedTickRate,
                processingCapacityTps >= requestedTickRate);
    }

    static List<JsonArray> BuildValidationLaneOriginsForLaneCount(
            JsonObject fixture,
            int laneCount,
            int lanesPerStack) {
        if (laneCount < 1 || laneCount > 64) {
            throw new IllegalArgumentException("validation-lane-count-out-of-range");
        }
        int stackCount = (int) Math.ceil(laneCount / (double) lanesPerStack);
        List<JsonArray> origins = BuildValidationLaneOrigins(
                fixture,
                stackCount,
                lanesPerStack);
        return new ArrayList<>(origins.subList(0, laneCount));
    }

    static List<JsonArray> BuildValidationLaneOrigins(
            JsonObject fixture,
            int stackCount,
            int lanesPerStack) {
        if (stackCount < 1 || stackCount > 16) {
            throw new IllegalArgumentException("validation-stack-count-out-of-range");
        }
        if (lanesPerStack != 4) {
            throw new IllegalArgumentException("validation-lanes-per-stack-must-be-four");
        }
        JsonArray baseOrigin = fixture.getAsJsonObject("Arena").getAsJsonArray("Origin");
        int minimumX = Integer.MAX_VALUE;
        int minimumY = Integer.MAX_VALUE;
        int minimumZ = Integer.MAX_VALUE;
        int maximumX = Integer.MIN_VALUE;
        int maximumY = Integer.MIN_VALUE;
        int maximumZ = Integer.MIN_VALUE;
        JsonObject arena = fixture.getAsJsonObject("Arena");
        if (arena.has("Bounds")) {
            JsonObject bounds = arena.getAsJsonObject("Bounds");
            JsonArray minimum = bounds.getAsJsonArray("Minimum");
            JsonArray maximum = bounds.getAsJsonArray("Maximum");
            minimumX = minimum.get(0).getAsInt();
            minimumY = minimum.get(1).getAsInt();
            minimumZ = minimum.get(2).getAsInt();
            maximumX = maximum.get(0).getAsInt();
            maximumY = maximum.get(1).getAsInt();
            maximumZ = maximum.get(2).getAsInt();
        }
        for (JsonElement element : fixture.getAsJsonArray("Blocks")) {
            JsonArray position = element.getAsJsonObject().getAsJsonArray("Position");
            minimumX = Math.min(minimumX, position.get(0).getAsInt());
            minimumY = Math.min(minimumY, position.get(1).getAsInt());
            minimumZ = Math.min(minimumZ, position.get(2).getAsInt());
            maximumX = Math.max(maximumX, position.get(0).getAsInt());
            maximumY = Math.max(maximumY, position.get(1).getAsInt());
            maximumZ = Math.max(maximumZ, position.get(2).getAsInt());
        }
        int width = minimumX == Integer.MAX_VALUE ? 1 : maximumX - minimumX + 1;
        int height = minimumY == Integer.MAX_VALUE ? 1 : maximumY - minimumY + 1;
        int depth = minimumZ == Integer.MAX_VALUE ? 1 : maximumZ - minimumZ + 1;
        int pitchX = width + VALIDATION_LANE_CLEARANCE_BLOCKS;
        int pitchY = height + VALIDATION_LANE_CLEARANCE_BLOCKS;
        int pitchZ = depth + VALIDATION_LANE_CLEARANCE_BLOCKS;
        List<JsonArray> origins = new ArrayList<>();
        for (int stackIndex = 0; stackIndex < stackCount; stackIndex++) {
            int stackColumn = stackIndex % VALIDATION_STACK_GRID_COLUMNS;
            int stackRow = stackIndex / VALIDATION_STACK_GRID_COLUMNS;
            for (int verticalIndex = 0; verticalIndex < lanesPerStack; verticalIndex++) {
                JsonArray laneOrigin = new JsonArray();
                laneOrigin.add(
                        baseOrigin.get(0).getAsInt() + stackColumn * pitchX);
                laneOrigin.add(
                        baseOrigin.get(1).getAsInt() + verticalIndex * pitchY);
                laneOrigin.add(
                        baseOrigin.get(2).getAsInt() + stackRow * pitchZ);
                origins.add(laneOrigin);
            }
        }
        return origins;
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

    private static ValidationTpsSelection SelectMaximumSustainingValidationLanes(
            MinecraftServer server,
            JsonObject fixture,
            JsonArray vectors,
            List<ValidationLane> lanes,
            double requestedTickRate) {
        List<ValidationTpsSample> samples = new ArrayList<>();
        int forcedFixtureChunkCount = OnServer(server, () -> forceFixtureChunks(
                server.overworld(),
                fixture.getAsJsonArray("Blocks"),
                lanes.stream().map(ValidationLane::Origin).toList(),
                fixture.getAsJsonObject("Arena")));
        for (int candidateLaneCount = lanes.size();
                candidateLaneCount >= 1;
                candidateLaneCount--) {
            List<ValidationLane> candidateLanes = List.copyOf(
                    lanes.subList(0, candidateLaneCount));
            if (!vectors.isEmpty()) {
                OnServer(server, () -> {
                    ServerLevel level = server.overworld();
                    for (int laneIndex = 0;
                            laneIndex < candidateLanes.size();
                            laneIndex++) {
                        int vectorIndex = (int) Math.floor(
                                laneIndex
                                        * vectors.size()
                                        / (double) candidateLanes.size());
                        ApplyValidationVector(
                                server,
                                level,
                                candidateLanes.get(laneIndex).InputStates(),
                                vectors.get(vectorIndex).getAsJsonObject());
                    }
                    return null;
                });
            }
            waitForTicks(server, 1);
            TickRateCalibrationObserver observer = OnServer(server, () -> {
                TickRateCalibrationObserver installed =
                        new TickRateCalibrationObserver(
                                server,
                                candidateLanes,
                                VALIDATION_TPS_CALIBRATION_TICKS);
                RedstoneCompilerHarness.InstallServerTickObserver(installed);
                return installed;
            });
            try {
                observer.AwaitCompletion();
            } finally {
                RedstoneCompilerHarness.RemoveServerTickObserver(observer);
            }
            ValidationTpsSample sample = observer.BuildSample(
                    candidateLaneCount,
                    requestedTickRate);
            samples.add(sample);
            if (sample.MeetsRequestedTickRate()) {
                return new ValidationTpsSelection(
                        candidateLaneCount,
                        forcedFixtureChunkCount,
                        List.copyOf(samples));
            }

            ValidationLane rejectedLane = lanes.get(candidateLaneCount - 1);
            List<JsonArray> remainingOrigins = lanes.subList(
                            0,
                            candidateLaneCount - 1)
                    .stream()
                    .map(ValidationLane::Origin)
                    .toList();
            forcedFixtureChunkCount = OnServer(server, () -> {
                clearFixture(
                        server.overworld(),
                        fixture.getAsJsonArray("Blocks"),
                        rejectedLane.Origin(),
                        fixture.getAsJsonObject("Arena"));
                return forceFixtureChunks(
                        server.overworld(),
                        fixture.getAsJsonArray("Blocks"),
                        remainingOrigins,
                        fixture.getAsJsonObject("Arena"));
            });
            waitForTicks(server, 2);
        }
        return new ValidationTpsSelection(
                0,
                forcedFixtureChunkCount,
                List.copyOf(samples));
    }

    private static ValidationSummary validateVectors(
            MinecraftServer server,
            JsonArray vectors,
            List<ValidationLane> lanes,
            int timeoutTicks,
            BufferedWriter progressOutput) throws IOException {
        if (lanes.isEmpty() || lanes.getFirst().TraceProbes().isEmpty()) {
            throw new IllegalArgumentException("fixture-has-no-trace-probes");
        }
        int traceProbeCount = lanes.getFirst().TraceProbes().size();
        if ((long) traceProbeCount * lanes.size() > MAXIMUM_ACTIVE_TRACE_PROBES) {
            throw new IllegalArgumentException(
                    "too-many-active-trace-probes:"
                            + ((long) traceProbeCount * lanes.size()));
        }
        int tested = 0;
        int MaximumSettleTicks = 0;
        int TotalUnobservedTickGaps = 0;
        WriteValidationProgress(
                tested,
                vectors.size(),
                lanes.size(),
                progressOutput);
        for (int batchStart = 0; batchStart < vectors.size(); batchStart += lanes.size()) {
            int batchSize = Math.min(lanes.size(), vectors.size() - batchStart);
            List<ValidationLane> activeLanes = lanes.subList(0, batchSize);
            List<JsonObject> activeVectors = new ArrayList<>();
            for (int laneOffset = 0; laneOffset < batchSize; laneOffset++) {
                activeVectors.add(
                        vectors.get(batchStart + laneOffset).getAsJsonObject());
            }
            OnServer(server, () -> {
                ServerLevel level = server.overworld();
                for (int laneOffset = 0; laneOffset < batchSize; laneOffset++) {
                    ApplyValidationVector(
                            server,
                            level,
                            activeLanes.get(laneOffset).InputStates(),
                            activeVectors.get(laneOffset));
                }
                return null;
            });
            waitForTicks(server, 1);
            TickBatchObserver observer = OnServer(server, () -> {
                TickBatchObserver installed = new TickBatchObserver(
                        server,
                        activeLanes,
                        timeoutTicks);
                RedstoneCompilerHarness.InstallServerTickObserver(installed);
                return installed;
            });
            try {
                observer.AwaitCompletion();
            } finally {
                RedstoneCompilerHarness.RemoveServerTickObserver(observer);
            }
            List<Snapshot> previous = observer.PreviousSnapshots();
            List<TraceQuiescenceTracker> quiescence = observer.Quiescence();
            List<Snapshot> settled = observer.SettledSnapshots();
            for (int laneOffset = 0; laneOffset < batchSize; laneOffset++) {
                TraceQuiescenceTracker.TraceQuiescenceStatus status =
                        quiescence.get(laneOffset).Status();
                SettlementEvidence evidence = new SettlementEvidence(
                        status.ElapsedTicks(),
                        status.LastObservedChangeTick(),
                        status.ObservedUnchangedTicks(),
                        traceProbeCount,
                        status.UnobservedTickGapCount());
                ValidationLane lane = activeLanes.get(laneOffset);
                JsonObject vector = activeVectors.get(laneOffset);
                Snapshot laneSettled = settled.get(laneOffset);
                if (laneSettled == null) {
                    Snapshot lastObserved = previous.get(laneOffset);
                    throw BuildTimeout(
                            vector,
                            lastObserved.OutputValues(),
                            tested,
                            evidence,
                            lane)
                            .WithTraceBlocks(lastObserved.TraceBlocks());
                }
                try {
                    compare(
                            vector,
                            laneSettled.OutputValues(),
                            tested,
                            evidence,
                            lane);
                } catch (Mismatch error) {
                    throw error.WithTraceBlocks(laneSettled.TraceBlocks());
                }
                MaximumSettleTicks = Math.max(
                        MaximumSettleTicks,
                        evidence.ElapsedTicks());
                TotalUnobservedTickGaps += status.UnobservedTickGapCount();
                tested++;
                WriteValidationProgress(
                        tested,
                        vectors.size(),
                        lanes.size(),
                        progressOutput);
            }
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

    private static List<Snapshot> CaptureSnapshots(
            ServerLevel level,
            List<ValidationLane> lanes) {
        long gameTime = level.getGameTime();
        List<Snapshot> snapshots = new ArrayList<>();
        for (ValidationLane lane : lanes) {
            snapshots.add(new Snapshot(
                    gameTime,
                    sample(level, lane.OutputPositions()),
                    ReadTraceStates(level, lane.TraceProbes()),
                    null));
        }
        return snapshots;
    }

    private static Snapshot CaptureDetailedSnapshot(
            ServerLevel level,
            ValidationLane lane,
            long gameTime,
            List<BlockState> traceStates) {
        return new Snapshot(
                gameTime,
                sample(level, lane.OutputPositions()),
                traceStates,
                ReadTraceBlocks(level, lane.TraceProbes()));
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
            SettlementEvidence Evidence,
            ValidationLane Lane) {
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
                AddValidationLaneEvidence(
                        details,
                        Lane,
                        testedVectorsBeforeFailure);
                if (vector.has("ExpectedSignals")) {
                    details.add("ExpectedSignals", vector.getAsJsonObject("ExpectedSignals").deepCopy());
                }
                throw new Mismatch(
                        "output-mismatch:" + output.getKey()
                                + ":expected=" + expectedValue
                                + ":actual=" + output.getValue()
                                + ":lane=" + Lane.Index()
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
            SettlementEvidence Evidence,
            ValidationLane Lane) {
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
        AddValidationLaneEvidence(
                details,
                Lane,
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

    private static void AddValidationLaneEvidence(
            JsonObject Details,
            ValidationLane Lane,
            int GlobalVectorIndex) {
        Details.addProperty("ValidationLaneIndex", Lane.Index());
        Details.addProperty("ValidationStackIndex", Lane.Index() / 4);
        Details.addProperty("ValidationVerticalIndex", Lane.Index() % 4);
        Details.add("ValidationLaneOrigin", Lane.Origin().deepCopy());
        Details.addProperty("GlobalVectorIndex", GlobalVectorIndex);
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

    private static final class TickBatchObserver
            implements RedstoneCompilerHarness.ServerTickObserver {
        private final List<ValidationLane> Lanes;
        private final List<Snapshot> PreviousSnapshots;
        private final List<TraceQuiescenceTracker> Quiescence;
        private final List<Snapshot> SettledSnapshots;
        private final CountDownLatch Finished = new CountDownLatch(1);
        private volatile RuntimeException Failure;

        TickBatchObserver(
                MinecraftServer server,
                List<ValidationLane> lanes,
                int timeoutTicks) {
            Lanes = List.copyOf(lanes);
            PreviousSnapshots = CaptureSnapshots(server.overworld(), Lanes);
            Quiescence = new ArrayList<>();
            SettledSnapshots = new ArrayList<>();
            for (Snapshot snapshot : PreviousSnapshots) {
                Quiescence.add(new TraceQuiescenceTracker(
                        REQUIRED_UNCHANGED_TICKS,
                        timeoutTicks,
                        snapshot.GameTime(),
                        snapshot.TraceStates()));
                SettledSnapshots.add(null);
            }
        }

        @Override
        public void OnServerTick(MinecraftServer server) {
            if (Finished.getCount() == 0) {
                return;
            }
            try {
                List<Snapshot> current = CaptureSnapshots(
                        server.overworld(),
                        Lanes);
                boolean complete = true;
                for (int laneOffset = 0; laneOffset < Lanes.size(); laneOffset++) {
                    if (SettledSnapshots.get(laneOffset) != null
                            || Quiescence.get(laneOffset).Status().TimedOut()) {
                        continue;
                    }
                    Snapshot laneCurrent = current.get(laneOffset);
                    Snapshot lanePrevious = PreviousSnapshots.get(laneOffset);
                    if (laneCurrent.GameTime() == lanePrevious.GameTime()) {
                        complete = false;
                        continue;
                    }
                    TraceQuiescenceTracker.TraceQuiescenceStatus status =
                            Quiescence.get(laneOffset).Observe(
                                    laneCurrent.GameTime(),
                                    laneCurrent.TraceStates());
                    if (status.Settled()) {
                        Snapshot detailed = CaptureDetailedSnapshot(
                                server.overworld(),
                                Lanes.get(laneOffset),
                                laneCurrent.GameTime(),
                                laneCurrent.TraceStates());
                        PreviousSnapshots.set(laneOffset, detailed);
                        SettledSnapshots.set(laneOffset, detailed);
                    } else if (status.TimedOut()) {
                        PreviousSnapshots.set(
                                laneOffset,
                                CaptureDetailedSnapshot(
                                        server.overworld(),
                                        Lanes.get(laneOffset),
                                        laneCurrent.GameTime(),
                                        laneCurrent.TraceStates()));
                    } else {
                        PreviousSnapshots.set(laneOffset, laneCurrent);
                        complete = false;
                    }
                }
                if (complete) {
                    Finished.countDown();
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

        List<Snapshot> PreviousSnapshots() {
            return PreviousSnapshots;
        }

        List<TraceQuiescenceTracker> Quiescence() {
            return Quiescence;
        }

        List<Snapshot> SettledSnapshots() {
            return SettledSnapshots;
        }
    }

    private static final class TickRateCalibrationObserver
            implements RedstoneCompilerHarness.ServerTickObserver {
        private final List<ValidationLane> Lanes;
        private final int RequiredTicks;
        private final CountDownLatch Finished = new CountDownLatch(1);
        private List<Snapshot> PreviousSnapshots;
        private int ObservedTicks;
        private long TotalTickProcessingNanos;
        private long MaximumTickProcessingNanos;
        private int TraceChangeCount;
        private boolean CompleteAfterDuration;
        private volatile RuntimeException Failure;

        TickRateCalibrationObserver(
                MinecraftServer server,
                List<ValidationLane> lanes,
                int requiredTicks) {
            if (lanes.isEmpty() || requiredTicks < 1) {
                throw new IllegalArgumentException(
                        "validation-tps-calibration-is-invalid");
            }
            Lanes = List.copyOf(lanes);
            RequiredTicks = requiredTicks;
            PreviousSnapshots = CaptureSnapshots(server.overworld(), Lanes);
        }

        @Override
        public void OnServerTick(MinecraftServer server) {
            if (Finished.getCount() == 0) {
                return;
            }
            try {
                List<Snapshot> current = CaptureSnapshots(
                        server.overworld(),
                        Lanes);
                for (int laneIndex = 0;
                        laneIndex < current.size();
                        laneIndex++) {
                    if (!current.get(laneIndex).TraceStates().equals(
                            PreviousSnapshots.get(laneIndex).TraceStates())) {
                        TraceChangeCount++;
                    }
                }
                PreviousSnapshots = current;
                ObservedTicks++;
                CompleteAfterDuration = ObservedTicks >= RequiredTicks;
            } catch (RuntimeException error) {
                Failure = error;
                CompleteAfterDuration = true;
            }
        }

        @Override
        public void OnServerTickComplete(long TickProcessingNanos) {
            if (Finished.getCount() == 0) {
                return;
            }
            TotalTickProcessingNanos += TickProcessingNanos;
            MaximumTickProcessingNanos = Math.max(
                    MaximumTickProcessingNanos,
                    TickProcessingNanos);
            if (CompleteAfterDuration) {
                Finished.countDown();
            }
        }

        void AwaitCompletion() {
            try {
                Finished.await();
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new Timeout("validation-tps-calibration-interrupted");
            }
            if (Failure != null) {
                throw Failure;
            }
        }

        ValidationTpsSample BuildSample(
                int laneCount,
                double requestedTickRate) {
            return BuildValidationTpsSample(
                    laneCount,
                    ObservedTicks,
                    TotalTickProcessingNanos,
                    MaximumTickProcessingNanos,
                    requestedTickRate);
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
    record ValidationLanePlan(
            int LaneCount,
            int LanesPerStack,
            int StackCount,
            int MaximumStackCount,
            int BatchCount,
            double TickBudgetSeconds) {
        static ValidationLanePlan SingleLane() {
            return new ValidationLanePlan(
                    1,
                    1,
                    1,
                    1,
                    0,
                    0.0D);
        }
    }
    record ValidationTpsSample(
            int LaneCount,
            int ObservedTicks,
            double AverageTickProcessingNanos,
            long MaximumTickProcessingNanos,
            double SustainedTickRate,
            boolean MeetsRequestedTickRate) { }
    private record ValidationTpsSelection(
            int LaneCount,
            int ForcedFixtureChunkCount,
            List<ValidationTpsSample> Samples) { }
    private record ValidationLane(
            int Index,
            JsonArray Origin,
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
