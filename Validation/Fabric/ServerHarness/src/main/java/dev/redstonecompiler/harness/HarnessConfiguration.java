package dev.redstonecompiler.harness;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

record HarnessConfiguration(
        String bindAddress,
        int port,
        String token,
        double requestedTickRate,
        int settleTimeoutTicks) {
    static HarnessConfiguration load() throws IOException {
        Path path = Path.of("config", "redstonecompiler-harness.json");
        JsonObject value = JsonParser.parseString(Files.readString(path)).getAsJsonObject();
        return new HarnessConfiguration(
                value.get("BindAddress").getAsString(),
                value.get("Port").getAsInt(),
                value.get("Token").getAsString(),
                value.get("RequestedTickRate").getAsDouble(),
                value.get("SettleTimeoutTicks").getAsInt());
    }
}
