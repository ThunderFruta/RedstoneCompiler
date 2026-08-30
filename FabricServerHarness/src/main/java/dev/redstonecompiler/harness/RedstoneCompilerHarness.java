package dev.redstonecompiler.harness;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.api.DedicatedServerModInitializer;
import net.minecraft.server.MinecraftServer;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Server-only, loopback-only control plane for compiler validation. */
public final class RedstoneCompilerHarness implements DedicatedServerModInitializer {
    private static volatile MinecraftServer server;

    public static void setServer(MinecraftServer value) {
        server = value;
    }

    @Override
    public void onInitializeServer() {
        Thread.ofVirtual().name("redstonecompiler-control").start(() -> {
            try {
                HarnessConfiguration configuration = HarnessConfiguration.load();
                try (ServerSocket listener = new ServerSocket(
                        configuration.port(), 16, InetAddress.getByName(configuration.bindAddress()))) {
                    while (!listener.isClosed()) {
                        Socket socket = listener.accept();
                        Thread.ofVirtual().start(() -> serve(socket, configuration));
                    }
                }
            } catch (IOException error) {
                System.err.println("RedstoneCompiler harness control server failed: " + error);
            }
        });
    }

    private static void serve(Socket socket, HarnessConfiguration configuration) {
        try (socket;
             BufferedReader input = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
             BufferedWriter output = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8))) {
            JsonObject request = JsonParser.parseString(input.readLine()).getAsJsonObject();
            JsonObject response = new JsonObject();
            if (!configuration.token().equals(request.get("Token").getAsString())) {
                response.addProperty("Status", "infrastructure-failure");
                response.addProperty("Error", "unauthenticated-control-request");
                output.write(response + "\n");
                output.flush();
                return;
            }
            MinecraftServer activeServer = server;
            if (activeServer == null) {
                response.addProperty("Status", "infrastructure-failure");
                response.addProperty("Error", "minecraft-server-not-ready");
                output.write(response + "\n");
                output.flush();
                return;
            }
            HarnessValidation.validate(activeServer, request, configuration, output);
        } catch (IOException error) {
            System.err.println("RedstoneCompiler harness request failed: " + error);
        }
    }
}
